use super::EXCLUDED_EXPORT_VARS;
use crate::exec_env::SUDHIR_CODEX_GATEWAY_TOKEN_ENV_VAR;

#[test]
fn gateway_token_is_absent_from_model_shell_snapshot() {
    assert!(EXCLUDED_EXPORT_VARS.contains(&SUDHIR_CODEX_GATEWAY_TOKEN_ENV_VAR));
    assert_ne!(SUDHIR_CODEX_GATEWAY_TOKEN_ENV_VAR, "PWD");
    assert_ne!(SUDHIR_CODEX_GATEWAY_TOKEN_ENV_VAR, "OLDPWD");
}
