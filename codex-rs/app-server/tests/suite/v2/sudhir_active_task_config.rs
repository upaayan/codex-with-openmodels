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
async fn active_selection_never_rewrites_new_task_defaults() -> Result<()> {
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

    let request_id = app
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
    let response: ConfigWriteResponse =
        timeout(READ_TIMEOUT, app.read_response(request_id)).await??;
    assert_eq!(response.status, WriteStatus::Ok);

    let config: toml::Value =
        toml::from_str(&std::fs::read_to_string(codex_home.join("config.toml"))?)?;
    assert_eq!(config["model"].as_str(), Some("gpt-default"));
    assert_eq!(config["model_reasoning_effort"].as_str(), Some("high"));
    assert_eq!(config["personality"].as_str(), Some("pragmatic"));
    Ok(())
}
