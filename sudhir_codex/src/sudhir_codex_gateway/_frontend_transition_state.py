"""Reversible standard-ChatGPT frontend trial for the Sudhir-Codex backend."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import plistlib
import shutil
import subprocess
import tempfile
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ._frontend_transition_control import ControlRuntimePaths
from ._frontend_transition_control import apply_control_config
from ._frontend_transition_control import provision_control_runtime

TRANSITION_METADATA = "gpt-pro-frontend-transition.json"
BASELINE_CONFIG = "config.toml.sudhir-baseline"
TRANSITION_WRAPPER = "sudhir-codex-chatgpt"
CHROME_MANIFEST_NAME = "com.openai.codexextension.json"


class TransitionError(RuntimeError):
    """A reversible transition precondition or operation failed."""


@dataclass(frozen=True)
class TransitionPaths:
    repo_root: Path
    primary_state: Path
    state: Path
    profile: Path
    official_app: Path
    chrome_manifest: Path
    pi_agent_dir: Path
    legacy_cua_source: Path

    @classmethod
    def from_env(cls) -> TransitionPaths:
        home = Path.home()
        return cls(
            repo_root=Path(
                os.environ.get(
                    "SUDHIR_CODEX_ROOT",
                    str(home / ".playground" / "sudhir-codex"),
                )
            ).expanduser(),
            primary_state=Path(
                os.environ.get(
                    "SUDHIR_CODEX_PRIMARY_STATE",
                    str(home / ".sudhir-codex"),
                )
            ).expanduser(),
            state=Path(
                os.environ.get(
                    "SUDHIR_CODEX_CHATGPT_STATE",
                    str(home / ".sudhir-codex-chatgpt"),
                )
            ).expanduser(),
            profile=Path(
                os.environ.get(
                    "SUDHIR_CODEX_CHATGPT_PROFILE",
                    str(
                        home
                        / "Library"
                        / "Application Support"
                        / "Sudhir-Codex-ChatGPT"
                    ),
                )
            ).expanduser(),
            official_app=Path(
                os.environ.get(
                    "SUDHIR_CODEX_CHATGPT_APP",
                    "/Applications/ChatGPT.app",
                )
            ).expanduser(),
            chrome_manifest=Path(
                os.environ.get(
                    "SUDHIR_CODEX_CHROME_NATIVE_HOST",
                    str(
                        home
                        / "Library"
                        / "Application Support"
                        / "Google"
                        / "Chrome"
                        / "NativeMessagingHosts"
                        / CHROME_MANIFEST_NAME
                    ),
                )
            ).expanduser(),
            pi_agent_dir=Path(
                os.environ.get(
                    "SUDHIR_CODEX_PI_AGENT_DIR",
                    str(home / ".pi" / "agent"),
                )
            ).expanduser(),
            legacy_cua_source=Path(
                os.environ.get(
                    "SUDHIR_CODEX_LEGACY_CUA_SOURCE",
                    "/Applications/Sudhir-Codex.app/Contents/Resources/cua_node",
                )
            ).expanduser(),
        )

    @property
    def config(self) -> Path:
        return self.state / "config.toml"

    @property
    def baseline_config(self) -> Path:
        return self.state / BASELINE_CONFIG

    @property
    def metadata(self) -> Path:
        return self.state / TRANSITION_METADATA

    @property
    def wrapper(self) -> Path:
        return self.state / "bin" / TRANSITION_WRAPPER

    @property
    def backups(self) -> Path:
        return self.state / "frontend-transition-backups"

    @property
    def chrome_backup(self) -> Path:
        return self.backups / CHROME_MANIFEST_NAME

    @property
    def deployed_core(self) -> Path:
        return self.repo_root / "dist" / "sudhir-codex-core"

    @property
    def installed_launcher(self) -> Path:
        return Path.home() / ".local" / "bin" / "sudhir-codex"

    @property
    def rollback_root(self) -> Path:
        return self.repo_root / "dist" / "backups"


    @property
    def control_runtime(self) -> ControlRuntimePaths:
        return ControlRuntimePaths(
            transition_state=self.state,
            primary_state=self.primary_state,
            official_app=self.official_app,
            legacy_source=self.legacy_cua_source,
        )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _config_semantic_sha256(path: Path) -> str:
    config = tomllib.loads(path.read_text(encoding="utf-8"))
    marketplaces = config.get("marketplaces")
    if isinstance(marketplaces, dict):
        for marketplace in marketplaces.values():
            if isinstance(marketplace, dict):
                marketplace.pop("last_updated", None)
    payload = json.dumps(
        config,
        default=str,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def primary_config_matches(
    paths: TransitionPaths,
    metadata: dict[str, Any],
) -> bool:
    primary_config = Path(str(metadata["primaryState"])) / "config.toml"
    expected_semantic = metadata.get("primaryConfigSemanticSha256")
    if not isinstance(expected_semantic, str) and paths.baseline_config.is_file():
        expected_semantic = _config_semantic_sha256(paths.baseline_config)
    if isinstance(expected_semantic, str):
        return _config_semantic_sha256(primary_config) == expected_semantic
    return _sha256(primary_config) == metadata.get("primaryConfigSha256")


def _read_app_identity(app: Path) -> tuple[str, str, str]:
    plist_path = app / "Contents" / "Info.plist"
    if not plist_path.is_file():
        raise TransitionError(f"ChatGPT metadata is missing: {plist_path}")
    with plist_path.open("rb") as handle:
        plist = plistlib.load(handle)
    identifier = str(plist.get("CFBundleIdentifier", ""))
    version = str(plist.get("CFBundleShortVersionString", ""))
    build = str(plist.get("CFBundleVersion", ""))
    if identifier != "com.openai.codex" or not version or not build:
        raise TransitionError(
            "The selected ChatGPT application has unexpected identity metadata"
        )
    return identifier, version, build


def _browser_client(app: Path) -> Path:
    path = (
        app
        / "Contents"
        / "Resources"
        / "plugins"
        / "openai-bundled"
        / "plugins"
        / "chrome"
        / "scripts"
        / "browser-client.mjs"
    )
    if not path.is_file():
        raise TransitionError(f"ChatGPT browser client is missing: {path}")
    return path


def _verify_official_resources(paths: TransitionPaths) -> tuple[str, str, str, str]:
    if not paths.official_app.is_dir():
        raise TransitionError(f"ChatGPT is missing: {paths.official_app}")
    subprocess.run(
        [
            "/usr/bin/codesign",
            "--verify",
            "--deep",
            "--strict",
            str(paths.official_app),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    identifier, version, build = _read_app_identity(paths.official_app)
    resources = paths.official_app / "Contents" / "Resources" / "cua_node"
    required = (
        resources / "bin" / "node_repl",
        resources / "bin" / "node",
        resources / "lib" / "node_modules",
    )
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise TransitionError(f"ChatGPT Computer Use resources are missing: {missing}")
    browser_hash = _sha256(_browser_client(paths.official_app))
    return identifier, version, build, browser_hash


def rewrite_transition_config(
    source: str,
    *,
    primary_state: Path,
    transition_state: Path,
    official_app: Path,
    official_version: str,
    browser_client_hash: str,
    legacy_cua_source: Path,
) -> str:
    """Convert cloned Sudhir frontend config to standard ChatGPT resources."""

    text = source.replace(str(primary_state), str(transition_state))
    text = text.replace("/Applications/Sudhir-Codex.app", str(official_app))
    text = apply_control_config(
        text,
        ControlRuntimePaths(
            transition_state=transition_state,
            primary_state=primary_state,
            official_app=official_app,
            legacy_source=legacy_cua_source,
        ),
        official_version=official_version,
        browser_client_hash=browser_client_hash,
    )
    tomllib.loads(text)
    return text


def _write_atomic(path: Path, data: bytes, *, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _clone_state(source: Path, destination: Path) -> None:
    completed = subprocess.run(
        ["/bin/cp", "-cR", str(source), str(destination)],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise TransitionError(
            f"Copy-on-write state clone failed: {completed.stderr.strip()}"
        )


def _wrapper_text(paths: TransitionPaths) -> str:
    return f"""#!/bin/sh
set -eu
export SUDHIR_CODEX_ROOT={json.dumps(str(paths.repo_root))}
export SUDHIR_CODEX_STATE={json.dumps(str(paths.state))}
export SUDHIR_CODEX_GATEWAY_STATE={json.dumps(str(paths.primary_state))}
export SUDHIR_CODEX_PI_AGENT_DIR={json.dumps(str(paths.pi_agent_dir))}
exec {json.dumps(str(paths.installed_launcher))} "$@"
"""


def _git_head(repo_root: Path) -> str:
    completed = subprocess.run(
        ["/usr/bin/git", "rev-parse", "HEAD"],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def prepare(paths: TransitionPaths) -> dict[str, Any]:
    primary_state = paths.primary_state.resolve()
    state = paths.state.expanduser()
    profile = paths.profile.expanduser()
    if not primary_state.is_dir() or primary_state.is_symlink():
        raise TransitionError(
            f"Primary Sudhir-Codex state is not a regular directory: {primary_state}"
        )
    primary_config = primary_state / "config.toml"
    if not primary_config.is_file() or primary_config.is_symlink():
        raise TransitionError(
            f"Primary Sudhir-Codex config is invalid: {primary_config}"
        )
    if state.exists() or state.is_symlink():
        raise TransitionError(f"Transition state already exists: {state}")
    if profile.exists() or profile.is_symlink():
        raise TransitionError(f"Transition Electron profile already exists: {profile}")
    if state.resolve(strict=False) == primary_state:
        raise TransitionError("Transition state must differ from primary state")
    if not paths.installed_launcher.is_file():
        raise TransitionError(
            f"Sudhir-Codex launcher is missing: {paths.installed_launcher}"
        )
    if not paths.deployed_core.is_file():
        raise TransitionError(f"Sudhir-Codex core is missing: {paths.deployed_core}")

    identifier, version, build, browser_hash = _verify_official_resources(paths)
    primary_helper = primary_state / "computer-use" / "Codex Computer Use.app"
    if not primary_helper.is_dir():
        raise TransitionError(
            f"Official Computer Use helper is missing: {primary_helper}"
        )

    primary_config_hash = _sha256(primary_config)
    core_hash = _sha256(paths.deployed_core)
    chrome_bytes = (
        paths.chrome_manifest.read_bytes() if paths.chrome_manifest.is_file() else None
    )
    temporary = state.with_name(f".{state.name}.prepare.{os.getpid()}")
    if temporary.exists():
        raise TransitionError(f"Temporary transition path already exists: {temporary}")

    try:
        _clone_state(primary_state, temporary)
        os.chmod(temporary, 0o700)
        for transient in (
            temporary / "ipc" / "ipc.sock",
            temporary / "gateway" / "gateway.pid",
            temporary / "gateway" / "gateway-start.lock",
        ):
            transient.unlink(missing_ok=True)

        cloned_config = temporary / "config.toml"
        baseline_config = temporary / BASELINE_CONFIG
        shutil.copy2(cloned_config, baseline_config, follow_symlinks=False)
        os.chmod(baseline_config, 0o600)
        rewritten = rewrite_transition_config(
            cloned_config.read_text(encoding="utf-8"),
            primary_state=primary_state,
            transition_state=state,
            official_app=paths.official_app,
            official_version=version,
            browser_client_hash=browser_hash,
            legacy_cua_source=paths.legacy_cua_source,
        )
        control_metadata = provision_control_runtime(
            paths.control_runtime,
            temporary,
        )
        _write_atomic(cloned_config, rewritten.encode("utf-8"), mode=0o600)

        backups = temporary / "frontend-transition-backups"
        backups.mkdir(mode=0o700, exist_ok=False)
        chrome_backup = backups / CHROME_MANIFEST_NAME
        if chrome_bytes is not None:
            _write_atomic(chrome_backup, chrome_bytes, mode=0o600)

        wrapper = temporary / "bin" / TRANSITION_WRAPPER
        _write_atomic(wrapper, _wrapper_text(paths).encode("utf-8"), mode=0o755)
        metadata = {
            "createdAt": dt.datetime.now(dt.UTC).isoformat(),
            "sourceCommit": _git_head(paths.repo_root),
            "primaryState": str(primary_state),
            "transitionState": str(state),
            "transitionProfile": str(profile),
            "officialApp": str(paths.official_app),
            "officialIdentifier": identifier,
            "officialVersion": version,
            "officialBuild": build,
            "browserClientSha256": browser_hash,
            "primaryConfigSha256": primary_config_hash,
            "primaryConfigSemanticSha256": _config_semantic_sha256(primary_config),
            "deployedCoreSha256": core_hash,
            **control_metadata,
            "legacyCuaSource": str(paths.legacy_cua_source),
            "chromeManifest": str(paths.chrome_manifest),
            "chromeManifestPresent": chrome_bytes is not None,
            "chromeManifestSha256": (
                hashlib.sha256(chrome_bytes).hexdigest()
                if chrome_bytes is not None
                else None
            ),
        }
        _write_atomic(
            temporary / TRANSITION_METADATA,
            (json.dumps(metadata, indent=2, sort_keys=True) + "\n").encode("utf-8"),
            mode=0o600,
        )
        if _sha256(primary_config) != primary_config_hash:
            raise TransitionError(
                "Primary Sudhir-Codex config changed during transition preparation"
            )
        profile.mkdir(parents=True, mode=0o700)
        os.replace(temporary, state)
    except Exception:
        if temporary.exists():
            shutil.rmtree(temporary)
        if state.exists() and not (state / TRANSITION_METADATA).exists():
            shutil.rmtree(state)
        if profile.exists() and not any(profile.iterdir()):
            profile.rmdir()
        raise

    return metadata


def _load_metadata(paths: TransitionPaths) -> dict[str, Any]:
    if not paths.metadata.is_file():
        raise TransitionError(f"Transition is not prepared: {paths.metadata}")
    value = json.loads(paths.metadata.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TransitionError("Transition metadata is invalid")
    return value


def restore_chrome(paths: TransitionPaths) -> None:
    metadata = _load_metadata(paths)
    if metadata.get("chromeManifestPresent"):
        if not paths.chrome_backup.is_file():
            raise TransitionError(
                f"Chrome manifest backup is missing: {paths.chrome_backup}"
            )
        _write_atomic(
            paths.chrome_manifest, paths.chrome_backup.read_bytes(), mode=0o600
        )
    else:
        paths.chrome_manifest.unlink(missing_ok=True)
