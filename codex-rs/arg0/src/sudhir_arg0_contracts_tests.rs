#[cfg(unix)]
#[test]
fn regular_file_and_hard_link_invocations_preserve_sudhir_identity() -> anyhow::Result<()> {
    use std::os::unix::fs::MetadataExt;

    let fixture = tempfile::tempdir()?;
    let executable = std::env::current_exe()?;
    let alias = fixture.path().join("apply_patch");
    super::create_unix_alias(&executable, &alias)?;

    let executable_metadata = std::fs::metadata(&executable)?;
    let alias_metadata = std::fs::symlink_metadata(&alias)?;
    assert!(alias_metadata.is_file());
    assert!(!alias_metadata.file_type().is_symlink());
    if executable_metadata.dev() == alias_metadata.dev()
        && executable_metadata.ino() == alias_metadata.ino()
    {
        assert!(alias_metadata.nlink() >= 2);
    } else {
        assert_eq!(std::fs::read(&executable)?, std::fs::read(&alias)?);
    }
    assert_ne!(
        alias.file_name().and_then(|name| name.to_str()),
        Some("codex")
    );
    Ok(())
}
