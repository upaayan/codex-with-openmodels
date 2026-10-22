use anyhow::Result;
use app_test_support::TestAppServer;
use codex_app_server_protocol::ConfigBatchWriteParams;
use codex_app_server_protocol::ConfigEdit;
use codex_app_server_protocol::ConfigWriteResponse;
use codex_app_server_protocol::MergeStrategy;
use codex_app_server_protocol::WriteStatus;
use pretty_assertions::assert_eq;
use serde_json::json;
use tempfile::TempDir;
use tokio::time::timeout;

const READ_TIMEOUT: std::time::Duration = std::time::Duration::from_secs(60);

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn new_task_selection_updates_new_task_defaults() -> Result<()> {
    let temp = TempDir::new()?;
    let codex_home = temp.path().canonicalize()?;
    std::fs::write(
        codex_home.join("config.toml"),
        r#"
model = "gpt-default"
model_reasoning_effort = "high"
"#,
    )?;
    let mut app = TestAppServer::builder()
        .with_codex_home(&codex_home)
        .without_auto_env()
        .build_initialized_with_timeout(READ_TIMEOUT)
        .await?;

    let picker_request_id = app
        .send_config_batch_write_request(ConfigBatchWriteParams {
            file_path: None,
            edits: vec![
                ConfigEdit {
                    key_path: "model".to_string(),
                    value: json!("pi-deepseek/v4-flash"),
                    merge_strategy: MergeStrategy::Replace,
                },
                ConfigEdit {
                    key_path: "model_reasoning_effort".to_string(),
                    value: json!("ultra"),
                    merge_strategy: MergeStrategy::Replace,
                },
            ],
            expected_version: None,
            reload_user_config: false,
        })
        .await?;
    let picker_response: ConfigWriteResponse =
        timeout(READ_TIMEOUT, app.read_response(picker_request_id)).await??;
    assert_eq!(picker_response.status, WriteStatus::Ok);

    let mixed_request_id = app
        .send_config_batch_write_request(ConfigBatchWriteParams {
            file_path: None,
            edits: vec![
                ConfigEdit {
                    key_path: "model".to_string(),
                    value: json!("pi-deepseek/v4-flash"),
                    merge_strategy: MergeStrategy::Replace,
                },
                ConfigEdit {
                    key_path: "model_reasoning_effort".to_string(),
                    value: json!("ultra"),
                    merge_strategy: MergeStrategy::Replace,
                },
                ConfigEdit {
                    key_path: "personality".to_string(),
                    value: json!("pragmatic"),
                    merge_strategy: MergeStrategy::Replace,
                },
            ],
            expected_version: None,
            reload_user_config: false,
        })
        .await?;
    let mixed_response: ConfigWriteResponse =
        timeout(READ_TIMEOUT, app.read_response(mixed_request_id)).await??;
    assert_eq!(mixed_response.status, WriteStatus::Ok);

    let config: toml::Value =
        toml::from_str(&std::fs::read_to_string(codex_home.join("config.toml"))?)?;
    assert_eq!(config["model"].as_str(), Some("pi-deepseek/v4-flash"));
    assert_eq!(config["model_reasoning_effort"].as_str(), Some("ultra"));
    assert_eq!(config["personality"].as_str(), Some("pragmatic"));
    Ok(())
}
