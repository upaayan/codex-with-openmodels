use anyhow::Result;
use codex_features::Feature;
use codex_models_manager::bundled_models_response;
use codex_protocol::config_types::WebSearchMode;
use codex_protocol::models::PermissionProfile;
use codex_protocol::openai_models::ModelVisibility;
use codex_protocol::openai_models::ModelsResponse;
use core_test_support::responses::ev_assistant_message;
use core_test_support::responses::ev_completed;
use core_test_support::responses::ev_function_call_with_namespace;
use core_test_support::responses::ev_response_created;
use core_test_support::responses::mount_sse_once_match;
use core_test_support::responses::sse;
use core_test_support::responses::start_mock_server;
use core_test_support::skip_if_no_network;
use core_test_support::test_codex::test_codex;
use pretty_assertions::assert_eq;
use serde_json::Value;
use serde_json::json;
use std::io::Cursor;
use std::time::Duration;
use tokio::time::Instant;
use tokio::time::sleep;

const SUDHIR_AGENTS_NAMESPACE: &str = "sudhir_agents";
const PARENT_PROMPT: &str = "spawn the requested Sudhir test child";
const CHILD_MESSAGE: &str = "return the Sudhir plaintext nonce";
const SPAWN_CALL_ID: &str = "sudhir-spawn-call";
const ROUTE_MODELS: [&str; 3] = [
    "gpt-5.6-sol",
    "pi-deepseek/deepseek-v4-flash",
    "cursor/composer-2.5",
];

fn decoded_body(request: &wiremock::Request) -> Option<Vec<u8>> {
    let uses_zstd = request
        .headers
        .get("content-encoding")
        .and_then(|value| value.to_str().ok())
        .is_some_and(|value| {
            value
                .split(',')
                .any(|entry| entry.trim().eq_ignore_ascii_case("zstd"))
        });
    if uses_zstd {
        zstd::stream::decode_all(Cursor::new(&request.body)).ok()
    } else {
        Some(request.body.clone())
    }
}

fn request_body(request: &wiremock::Request) -> Option<Value> {
    decoded_body(request).and_then(|body| serde_json::from_slice(&body).ok())
}

fn body_contains(request: &wiremock::Request, text: &str) -> bool {
    decoded_body(request)
        .and_then(|body| String::from_utf8(body).ok())
        .is_some_and(|body| body.contains(text))
}

fn has_agent_message(request: &wiremock::Request) -> bool {
    request_body(request)
        .and_then(|body| body.get("input").and_then(Value::as_array).cloned())
        .is_some_and(|items| {
            items
                .iter()
                .any(|item| item.get("type").and_then(Value::as_str) == Some("agent_message"))
        })
}

async fn assert_plaintext_route_pair(parent_model: &str, child_model: &str) -> Result<()> {
    let server = start_mock_server().await;
    let spawn_args = serde_json::to_string(&json!({
        "message": CHILD_MESSAGE,
        "task_name": "worker",
    }))?;
    mount_sse_once_match(
        &server,
        |request: &wiremock::Request| body_contains(request, PARENT_PROMPT),
        sse(vec![
            ev_response_created("sudhir-parent-response-1"),
            ev_function_call_with_namespace(
                SPAWN_CALL_ID,
                SUDHIR_AGENTS_NAMESPACE,
                "spawn_agent",
                &spawn_args,
            ),
            ev_completed("sudhir-parent-response-1"),
        ]),
    )
    .await;
    let child_requests = mount_sse_once_match(
        &server,
        has_agent_message,
        sse(vec![
            ev_response_created("sudhir-child-response"),
            ev_assistant_message("sudhir-child-message", "sudhir-child-done"),
            ev_completed("sudhir-child-response"),
        ]),
    )
    .await;
    mount_sse_once_match(
        &server,
        |request: &wiremock::Request| {
            body_contains(request, SPAWN_CALL_ID) && !has_agent_message(request)
        },
        sse(vec![
            ev_response_created("sudhir-parent-response-2"),
            ev_assistant_message("sudhir-parent-message", "sudhir-parent-done"),
            ev_completed("sudhir-parent-response-2"),
        ]),
    )
    .await;

    let parent_model = parent_model.to_string();
    let child_model = child_model.to_string();
    let configured_parent = parent_model.clone();
    let configured_child = child_model.clone();
    let mut builder = test_codex()
        .with_model(&parent_model)
        .with_config(move |config| {
            config
                .features
                .enable(Feature::Collab)
                .expect("test config should allow collaboration");
            config
                .features
                .enable(Feature::MultiAgentV2)
                .expect("test config should allow multi-agent v2");
            config.multi_agent_v2.tool_namespace = Some(SUDHIR_AGENTS_NAMESPACE.to_string());
            config.agent_default_subagent_model = Some(configured_child.clone());

            let bundled = bundled_models_response().expect("bundled models should parse");
            let template = bundled
                .models
                .first()
                .expect("bundled models should not be empty");
            let models = ROUTE_MODELS
                .iter()
                .map(|slug| {
                    let mut model = template.clone();
                    model.slug = (*slug).to_string();
                    model.display_name = (*slug).to_string();
                    model.visibility = ModelVisibility::List;
                    model
                })
                .collect();
            config.model_catalog = Some(ModelsResponse { models });
            config.model = Some(configured_parent.clone());
        });
    let test = builder.build_with_auto_env(&server).await?;
    test.submit_turn(PARENT_PROMPT).await?;

    let deadline = Instant::now() + Duration::from_secs(3);
    let child_request = loop {
        if let Some(request) = child_requests
            .requests()
            .into_iter()
            .find(|request| !request.inputs_of_type("agent_message").is_empty())
        {
            break request;
        }
        if Instant::now() >= deadline {
            anyhow::bail!("timed out waiting for {parent_model} -> {child_model} agent message");
        }
        sleep(Duration::from_millis(10)).await;
    };
    let body = child_request.body_json();
    assert_eq!(body["model"], json!(child_model));
    let messages = body["input"]
        .as_array()
        .expect("child input should be an array")
        .iter()
        .filter(|item| item["type"] == "agent_message")
        .collect::<Vec<_>>();
    assert_eq!(messages.len(), 1);
    assert_eq!(messages[0]["content"][0]["text"], json!(CHILD_MESSAGE));
    assert_eq!(messages[0].get("encrypted_content"), None);
    Ok(())
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn all_parent_and_child_routes_use_plaintext_sudhir_agent_messages() -> Result<()> {
    skip_if_no_network!(Ok(()));

    for parent_model in ROUTE_MODELS {
        for child_model in ROUTE_MODELS {
            assert_plaintext_route_pair(parent_model, child_model).await?;
        }
    }
    Ok(())
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn pi_routes_suppress_provider_hosted_tools() -> Result<()> {
    skip_if_no_network!(Ok(()));

    let server = start_mock_server().await;
    let response = core_test_support::responses::mount_sse_once(
        &server,
        sse(vec![
            ev_response_created("sudhir-pi-tools-response"),
            ev_completed("sudhir-pi-tools-response"),
        ]),
    )
    .await;
    let mut builder = test_codex()
        .with_model("pi-deepseek/deepseek-v4-flash")
        .with_config(|config| {
            config
                .web_search_mode
                .set(WebSearchMode::Live)
                .expect("live search mode should be valid");
        });
    let test = builder.build_with_auto_env(&server).await?;
    test.submit_turn_with_permission_profile(
        "do not advertise provider-hosted tools",
        PermissionProfile::read_only(),
    )
    .await?;

    let body = response.single_request().body_json();
    let tools = body["tools"].as_array().expect("tools should be an array");
    assert!(tools.iter().all(|tool| {
        !matches!(
            tool.get("type").and_then(Value::as_str),
            Some("web_search" | "image_generation")
        )
    }));
    Ok(())
}
