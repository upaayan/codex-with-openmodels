use crate::exec_env::SUDHIR_CODEX_GATEWAY_TOKEN_ENV_VAR;
use codex_shell_command::shell_detect::ShellType;
use codex_shell_command::shell_snapshot::{
    SnapshotCaptureOptions, SnapshotStartup, snapshot_capture_script,
};

#[test]
#[cfg(unix)]
fn gateway_token_is_absent_from_model_shell_snapshot() {
    let output = std::process::Command::new("/bin/bash")
        .args([
            "-c",
            &snapshot_capture_script(
                ShellType::Bash,
                SnapshotCaptureOptions {
                    startup: SnapshotStartup::Interactive,
                    declarations: true,
                    environment: false,
                },
            )
            .expect("bash snapshot"),
        ])
        .env("BASH_ENV", "/dev/null")
        .env(SUDHIR_CODEX_GATEWAY_TOKEN_ENV_VAR, "snapshot-secret")
        .output()
        .expect("capture shell snapshot");
    assert!(output.status.success());
    let snapshot = String::from_utf8(output.stdout).expect("utf8 snapshot");
    assert!(!snapshot.contains(SUDHIR_CODEX_GATEWAY_TOKEN_ENV_VAR));
    assert!(!snapshot.contains("snapshot-secret"));
}
