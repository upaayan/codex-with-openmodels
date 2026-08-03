use super::compact::assert_compaction_uses_turn_lifecycle_id;
use super::compact::assert_pre_sampling_switch_compaction_requests;
use super::compact::disabled_permission_user_turn;
use super::compact::model_info_with_context_window;
use super::compact::model_info_with_optional_comp_hash;
use super::compact::non_openai_model_provider;
use super::compact::set_test_compact_prompt;
use codex_login::CodexAuth;
use codex_protocol::openai_models::ModelsResponse;
use codex_protocol::protocol::EventMsg;
use codex_protocol::protocol::Op;
use codex_protocol::protocol::RolloutItem;
use codex_protocol::protocol::RolloutLine;
use core_test_support::responses::ev_assistant_message;
use core_test_support::responses::ev_completed_with_tokens;
use core_test_support::responses::mount_models_once;
use core_test_support::responses::mount_sse_sequence;
use core_test_support::responses::sse;
use core_test_support::responses::start_mock_server;
use core_test_support::skip_if_no_network;
use core_test_support::test_codex::test_codex;
use core_test_support::wait_for_event;
use pretty_assertions::assert_eq;
use std::fs;

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn pressure_compaction_uses_selected_model() {
    skip_if_no_network!();

    let server = wiremock::MockServer::start().await;
    let previous_model = "gpt-5.4";
    let selected_model = "gpt-5.2";
    let models = mount_models_once(
        &server,
        ModelsResponse {
            models: vec![
                model_info_with_context_window(previous_model, /*context_window*/ 273_000),
                model_info_with_context_window(selected_model, /*context_window*/ 125_000),
            ],
        },
    )
    .await;
    let requests = mount_sse_sequence(
        &server,
        vec![
            sse(vec![
                ev_assistant_message("sudhir-m1", "before switch"),
                ev_completed_with_tokens("sudhir-r1", /*total_tokens*/ 120_000),
            ]),
            sse(vec![
                ev_assistant_message("sudhir-m2", "selected-model-summary"),
                ev_completed_with_tokens("sudhir-r2", /*total_tokens*/ 10),
            ]),
            sse(vec![
                ev_assistant_message("sudhir-m3", "after switch"),
                ev_completed_with_tokens("sudhir-r3", /*total_tokens*/ 100),
            ]),
        ],
    )
    .await;
    let provider = non_openai_model_provider(&server);
    let mut builder = test_codex()
        .with_auth(CodexAuth::create_dummy_chatgpt_auth_for_testing())
        .with_model(previous_model)
        .with_config(move |config| {
            config.model_provider = provider;
            set_test_compact_prompt(config);
        });
    let test = builder.build(&server).await.expect("build test codex");

    test.codex
        .submit(disabled_permission_user_turn(
            "before switch",
            test.cwd.path().to_path_buf(),
            previous_model.to_string(),
        ))
        .await
        .expect("submit first turn");
    wait_for_event(&test.codex, |event| {
        matches!(event, EventMsg::TurnComplete(_))
    })
    .await;
    test.codex
        .submit(disabled_permission_user_turn(
            "after switch",
            test.cwd.path().to_path_buf(),
            selected_model.to_string(),
        ))
        .await
        .expect("submit selected-model turn");
    assert_compaction_uses_turn_lifecycle_id(&test.codex).await;

    let captured = requests.requests();
    assert_eq!(models.requests().len(), 1);
    assert_eq!(captured.len(), 3);
    assert_pre_sampling_switch_compaction_requests(
        &captured[0].body_json(),
        &captured[1].body_json(),
        &captured[2].body_json(),
        previous_model,
        selected_model,
    );
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn model_provider_and_hash_change_do_not_compact_without_pressure() {
    skip_if_no_network!();

    let server = start_mock_server().await;
    let previous_model = "custom/gpt-5.5";
    let previous_family = "gpt-5.5";
    let selected_model = "gpt-5.6";
    let mut previous_info =
        model_info_with_optional_comp_hash("gpt-5.4", Some("old-provider-hash"));
    previous_info.slug = previous_family.to_string();
    let mut selected_info =
        model_info_with_optional_comp_hash("gpt-5.4", Some("new-provider-hash"));
    selected_info.slug = selected_model.to_string();
    let catalog = ModelsResponse {
        models: vec![previous_info, selected_info],
    };
    let requests = mount_sse_sequence(
        &server,
        vec![
            sse(vec![
                ev_assistant_message("sudhir-hash-m1", "before switch"),
                ev_completed_with_tokens("sudhir-hash-r1", /*total_tokens*/ 100),
            ]),
            sse(vec![
                ev_assistant_message("sudhir-hash-m2", "after switch"),
                ev_completed_with_tokens("sudhir-hash-r2", /*total_tokens*/ 100),
            ]),
        ],
    )
    .await;
    let provider = non_openai_model_provider(&server);
    let mut builder = test_codex()
        .with_auth(CodexAuth::from_api_key("test-api-key"))
        .with_model(previous_model)
        .with_config(move |config| {
            config.model_provider = provider;
            config.model_catalog = Some(catalog);
            set_test_compact_prompt(config);
        });
    let test = builder.build(&server).await.expect("build test codex");

    for (prompt, model) in [
        ("before switch", previous_model),
        ("after switch", selected_model),
    ] {
        test.codex
            .submit(disabled_permission_user_turn(
                prompt,
                test.cwd.path().to_path_buf(),
                model.to_string(),
            ))
            .await
            .expect("submit model turn");
        wait_for_event(&test.codex, |event| {
            matches!(event, EventMsg::TurnComplete(_))
        })
        .await;
    }

    let captured = requests.requests();
    assert_eq!(
        captured.len(),
        2,
        "model, provider, or hash changes are not pressure"
    );
    assert_eq!(captured[0].body_json()["model"], previous_model);
    assert_eq!(captured[1].body_json()["model"], selected_model);
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn legacy_hash_resume_uses_selected_model_without_spurious_compaction() {
    skip_if_no_network!();

    let server = wiremock::MockServer::start().await;
    let previous_model = "gpt-5.4";
    let selected_model = "gpt-5.2";
    let models = mount_models_once(
        &server,
        ModelsResponse {
            models: vec![
                model_info_with_optional_comp_hash(previous_model, Some("legacy-hash")),
                model_info_with_optional_comp_hash(selected_model, Some("selected-hash")),
            ],
        },
    )
    .await;
    let requests = mount_sse_sequence(
        &server,
        vec![
            sse(vec![
                ev_assistant_message("sudhir-resume-m1", "before resume"),
                ev_completed_with_tokens("sudhir-resume-r1", /*total_tokens*/ 100),
            ]),
            sse(vec![
                ev_assistant_message("sudhir-resume-m2", "after resume"),
                ev_completed_with_tokens("sudhir-resume-r2", /*total_tokens*/ 100),
            ]),
        ],
    )
    .await;
    let provider = non_openai_model_provider(&server);
    let mut initial_builder = test_codex()
        .with_auth(CodexAuth::create_dummy_chatgpt_auth_for_testing())
        .with_model(previous_model)
        .with_config(move |config| {
            config.model_provider = provider;
            set_test_compact_prompt(config);
        });
    let initial = initial_builder
        .build(&server)
        .await
        .expect("build initial codex");
    let home = initial.home.clone();
    let rollout_path = initial
        .session_configured
        .rollout_path
        .clone()
        .expect("rollout path");
    initial
        .codex
        .submit(disabled_permission_user_turn(
            "before resume",
            initial.cwd.path().to_path_buf(),
            previous_model.to_string(),
        ))
        .await
        .expect("submit pre-resume turn");
    wait_for_event(&initial.codex, |event| {
        matches!(event, EventMsg::TurnComplete(_))
    })
    .await;
    initial
        .codex
        .submit(Op::Shutdown)
        .await
        .expect("shutdown initial session");
    wait_for_event(&initial.codex, |event| {
        matches!(event, EventMsg::ShutdownComplete)
    })
    .await;

    let rollout = fs::read_to_string(&rollout_path).expect("read rollout");
    let persisted_hash = rollout
        .lines()
        .filter_map(|line| serde_json::from_str::<RolloutLine>(line).ok())
        .find_map(|line| match line.item {
            RolloutItem::TurnContext(context) => context.comp_hash,
            _ => None,
        });
    assert_eq!(persisted_hash.as_deref(), Some("legacy-hash"));

    let provider = non_openai_model_provider(&server);
    let mut resumed_builder = test_codex()
        .with_auth(CodexAuth::create_dummy_chatgpt_auth_for_testing())
        .with_model(previous_model)
        .with_config(move |config| {
            config.model_provider = provider;
            set_test_compact_prompt(config);
        });
    let resumed = resumed_builder
        .resume(&server, home, rollout_path)
        .await
        .expect("resume codex");
    resumed
        .codex
        .submit(disabled_permission_user_turn(
            "after resume",
            resumed.cwd.path().to_path_buf(),
            selected_model.to_string(),
        ))
        .await
        .expect("submit resumed turn");
    wait_for_event(&resumed.codex, |event| {
        matches!(event, EventMsg::TurnComplete(_))
    })
    .await;

    let captured = requests.requests();
    assert_eq!(models.requests().len(), 1);
    assert_eq!(captured.len(), 2, "legacy hash must not add compaction");
    assert_eq!(captured[0].body_json()["model"], previous_model);
    assert_eq!(captured[1].body_json()["model"], selected_model);
}
