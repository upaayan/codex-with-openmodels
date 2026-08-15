"""Isolated legacy Computer Use runtime for the standard ChatGPT frontend."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

CONTROL_ROOT_NAME = "frontend-control-runtime"
LEGACY_RUNTIME_NAME = "legacy-cua"
PLUGIN_ASSETS_NAME = "plugin-assets"
COMPUTER_USE_MARKETPLACE = "openai-bundled"
COMPUTER_USE_PLUGIN = "computer-use"
PLUGIN_FILES = (
    Path("scripts/computer-use-client.mjs"),
    Path(".codex-plugin/computer-use-node-repl.md"),
    Path("skills/computer-use/SKILL.md"),
)
_UPSTREAM_SERVICE_PATH_KEY = "SKY_CUA_SERVICE_PATH"
_UPSTREAM_SOCKET_PATH_KEY = "SKY_CUA_SERVICE_NATIVE_PIPE_PATH"
_TRANSITION_SERVICE_PATH_KEY = "SUDHIR_CUA_SERVICE_PATH"
_TRANSITION_SOCKET_PATH_KEY = "SUDHIR_CUA_SERVICE_NATIVE_PIPE_PATH"
_BOOTSTRAP = re.compile(r"(?ms)^## Bootstrap\n.*?(?=^## )")


class ControlRuntimeError(RuntimeError):
    """The isolated Computer Use runtime is missing or incompatible."""


@dataclass(frozen=True)
class ControlRuntimePaths:
    transition_state: Path
    primary_state: Path
    official_app: Path
    legacy_source: Path

    @property
    def root(self) -> Path:
        return self.transition_state / CONTROL_ROOT_NAME

    @property
    def runtime(self) -> Path:
        return self.root / LEGACY_RUNTIME_NAME

    @property
    def node_repl(self) -> Path:
        return self.runtime / "bin" / "node_repl"

    @property
    def node(self) -> Path:
        return self.runtime / "bin" / "node"

    @property
    def legacy_sky(self) -> Path:
        return self.runtime / "lib" / "node_modules" / "@oai" / "sky"

    @property
    def legacy_create_client(self) -> Path:
        return (
            self.legacy_sky
            / "dist"
            / "project"
            / "cua"
            / "sky_js"
            / "src"
            / "targets"
            / "mac"
            / "create_client.js"
        )

    @property
    def legacy_native_pipe(self) -> Path:
        return self.legacy_create_client.with_name("native-pipe.js")

    @property
    def official_node_modules(self) -> Path:
        return (
            self.official_app
            / "Contents"
            / "Resources"
            / "cua_node"
            / "lib"
            / "node_modules"
        )

    @property
    def assets(self) -> Path:
        return self.root / PLUGIN_ASSETS_NAME

    @property
    def helper(self) -> Path:
        return self.primary_state / "control" / "Sudhir Computer Use.app"

    @property
    def helper_executable(self) -> Path:
        return self.helper / "Contents" / "MacOS" / "Sudhir Computer Use"

    @property
    def socket(self) -> Path:
        return self.primary_state / "control" / "run" / "computer-use.sock"

    @property
    def broker(self) -> Path:
        return self.primary_state / "control" / "bin" / "sudhir-chrome-broker"

    @property
    def source_node_repl(self) -> Path:
        return self.legacy_source / "bin" / "node_repl"

    @property
    def source_node(self) -> Path:
        return self.legacy_source / "bin" / "node"

    @property
    def source_sky(self) -> Path:
        return self.legacy_source / "lib" / "node_modules" / "@oai" / "sky"

    @property
    def source_create_client(self) -> Path:
        return (
            self.source_sky
            / "dist"
            / "project"
            / "cua"
            / "sky_js"
            / "src"
            / "targets"
            / "mac"
            / "create_client.js"
        )


@dataclass(frozen=True)
class PluginAssets:
    wrapper: Path
    node_repl_instructions: Path
    skill: Path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_atomic(path: Path, data: bytes, *, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _codesign_value(path: Path, key: str) -> str:
    completed = subprocess.run(
        ["/usr/bin/codesign", "-d", "--verbose=4", str(path)],
        check=False,
        capture_output=True,
        text=True,
    )
    output = completed.stderr + completed.stdout
    prefix = f"{key}="
    for line in output.splitlines():
        if line.startswith(prefix):
            return line[len(prefix) :]
    return ""


def _verify_openai_node_repl(path: Path) -> None:
    if not path.is_file() or not os.access(path, os.X_OK):
        raise ControlRuntimeError(f"OpenAI node_repl is missing: {path}")
    subprocess.run(
        ["/usr/bin/codesign", "--verify", "--strict", str(path)],
        check=True,
        capture_output=True,
        text=True,
    )
    if _codesign_value(path, "Identifier") != "node_repl":
        raise ControlRuntimeError(f"Unexpected node_repl signing identifier: {path}")
    if _codesign_value(path, "TeamIdentifier") != "2DC432GLL2":
        raise ControlRuntimeError(f"Unexpected node_repl signing team: {path}")


def _verify_control_components(paths: ControlRuntimePaths) -> dict[str, str]:
    if not paths.helper.is_dir() or not paths.helper_executable.is_file():
        raise ControlRuntimeError(
            f"Sudhir Computer Use helper is missing: {paths.helper}"
        )
    subprocess.run(
        [
            "/usr/bin/codesign",
            "--verify",
            "--deep",
            "--strict",
            str(paths.helper),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    if _codesign_value(paths.helper, "Identifier") != "com.sudhir.codex.computer-use":
        raise ControlRuntimeError("Unexpected Sudhir Computer Use helper identity")
    expected_path = str(paths.node_repl).encode("utf-8")
    if expected_path not in paths.helper_executable.read_bytes():
        raise ControlRuntimeError(
            "Installed Sudhir Computer Use helper does not authorize "
            "the transition node_repl"
        )
    if not paths.broker.is_file() or not os.access(paths.broker, os.X_OK):
        raise ControlRuntimeError(f"Sudhir Chrome broker is missing: {paths.broker}")
    subprocess.run(
        ["/usr/bin/codesign", "--verify", "--strict", str(paths.broker)],
        check=True,
        capture_output=True,
        text=True,
    )
    if _codesign_value(paths.broker, "Identifier") != "com.sudhir.codex.chrome-broker":
        raise ControlRuntimeError("Unexpected Sudhir Chrome broker identity")
    if expected_path not in paths.broker.read_bytes():
        raise ControlRuntimeError(
            "Installed Sudhir Chrome broker does not authorize "
            "the transition node_repl"
        )
    return {
        "controlHelperSha256": _sha256(paths.helper_executable),
        "controlBrokerSha256": _sha256(paths.broker),
    }


def _verify_source(paths: ControlRuntimePaths) -> dict[str, str]:
    _verify_openai_node_repl(paths.source_node_repl)
    if not paths.source_node.is_file() or not os.access(paths.source_node, os.X_OK):
        raise ControlRuntimeError(f"Legacy Node runtime is missing: {paths.source_node}")
    subprocess.run(
        ["/usr/bin/codesign", "--verify", "--strict", str(paths.source_node)],
        check=True,
        capture_output=True,
        text=True,
    )
    if not paths.source_create_client.is_file():
        raise ControlRuntimeError(
            f"Legacy Computer Use client is missing: {paths.source_create_client}"
        )
    return {
        "controlSourceNodeReplSha256": _sha256(paths.source_node_repl),
        "controlSourceNodeSha256": _sha256(paths.source_node),
        "controlSourceCreateClientSha256": _sha256(paths.source_create_client),
    }


def _rewrite_native_pipe_source(source: str) -> str:
    replacements = (
        (_UPSTREAM_SERVICE_PATH_KEY, _TRANSITION_SERVICE_PATH_KEY),
        (_UPSTREAM_SOCKET_PATH_KEY, _TRANSITION_SOCKET_PATH_KEY),
    )
    updated = source
    for upstream, transition in replacements:
        upstream_count = updated.count(upstream)
        transition_count = updated.count(transition)
        if upstream_count == 1 and transition_count == 0:
            updated = updated.replace(upstream, transition, 1)
            continue
        if upstream_count == 0 and transition_count == 1:
            continue
        raise ControlRuntimeError(
            f"Unexpected Computer Use environment-key counts for {upstream}: "
            f"upstream={upstream_count}, transition={transition_count}"
        )
    return updated


def _patch_runtime(paths: ControlRuntimePaths, working_state: Path) -> None:
    runtime = ControlRuntimePaths(
        transition_state=working_state,
        primary_state=paths.primary_state,
        official_app=paths.official_app,
        legacy_source=paths.legacy_source,
    )
    native_pipe = runtime.legacy_native_pipe
    if not native_pipe.is_file():
        raise ControlRuntimeError(
            f"Copied Computer Use native-pipe client is missing: {native_pipe}"
        )
    source = native_pipe.read_text(encoding="utf-8")
    updated = _rewrite_native_pipe_source(source)
    if updated != source:
        _write_atomic(native_pipe, updated.encode("utf-8"), mode=0o644)


def _copy_runtime(paths: ControlRuntimePaths, working_state: Path) -> None:
    destination = working_state / CONTROL_ROOT_NAME / LEGACY_RUNTIME_NAME
    if destination.exists() or destination.is_symlink():
        return
    source_metadata = _verify_source(paths)
    temporary = destination.with_name(f".{destination.name}.copy.{os.getpid()}")
    if temporary.exists():
        raise ControlRuntimeError(f"Temporary control-runtime path exists: {temporary}")
    try:
        (temporary / "bin").mkdir(parents=True, mode=0o700)
        (temporary / "lib" / "node_modules" / "@oai").mkdir(
            parents=True,
            mode=0o700,
        )
        shutil.copy2(paths.source_node_repl, temporary / "bin" / "node_repl")
        shutil.copy2(paths.source_node, temporary / "bin" / "node")
        shutil.copytree(
            paths.source_sky,
            temporary / "lib" / "node_modules" / "@oai" / "sky",
            symlinks=True,
        )
        for name in ("manifest.json", "LICENSE"):
            source = paths.legacy_source / name
            if source.is_file():
                shutil.copy2(source, temporary / name)
        _write_atomic(
            temporary / "source-metadata.json",
            (json.dumps(source_metadata, indent=2, sort_keys=True) + "\n").encode(),
            mode=0o600,
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


def _plugin_roots(state: Path) -> list[Path]:
    root = (
        state
        / "plugins"
        / "cache"
        / COMPUTER_USE_MARKETPLACE
        / COMPUTER_USE_PLUGIN
    )
    if not root.is_dir():
        return []
    return sorted(path for path in root.iterdir() if path.is_dir())


def _asset_paths(assets_root: Path) -> PluginAssets:
    return PluginAssets(
        wrapper=assets_root / PLUGIN_FILES[0],
        node_repl_instructions=assets_root / PLUGIN_FILES[1],
        skill=assets_root / PLUGIN_FILES[2],
    )


def _wrapper_text(paths: ControlRuntimePaths) -> str:
    return f'''import path from "node:path";
import {{ pathToFileURL }} from "node:url";
import {{ spawn }} from "node:child_process";

const COMPUTER_USE_RUNTIME_KEY = Symbol.for("openai.computer-use.runtime");
const CHROME_COMPUTER_USE_META_KEY = "codex/computerUseChrome";
const MAC_CHROME_APP_PATH_PATTERN = /(?:^|[\\\\/])Google Chrome\\.app(?:[\\\\/]|$)/i;
const LEGACY_CREATE_CLIENT = {json.dumps(str(paths.legacy_create_client))};
const HELPER_PATH = {json.dumps(str(paths.helper))};

export async function setupComputerUseRuntime({{ globals = globalThis }} = {{}}) {{
  startConfiguredSudhirComputerUseService();
  const installedRuntime = Reflect.get(globalThis, COMPUTER_USE_RUNTIME_KEY);
  if (installedRuntime != null) {{
    Reflect.set(globalThis, "sky", installedRuntime);
    Reflect.set(globals, "sky", installedRuntime);
    return installedRuntime;
  }}
  const module = await import(pathToFileURL(LEGACY_CREATE_CLIENT).href);
  if (typeof module.create_client !== "function") {{
    throw new Error("Legacy @oai/sky is missing create_client");
  }}
  const sky = module.create_client({{ target: "mac" }});
  installChromeComputerUseMetadata(sky);
  Object.freeze(sky);
  Reflect.set(globalThis, COMPUTER_USE_RUNTIME_KEY, sky);
  Reflect.set(globalThis, "sky", sky);
  Reflect.set(globals, "sky", sky);
  return sky;
}}

function startConfiguredSudhirComputerUseService() {{
  const executablePath = path.join(
    HELPER_PATH,
    "Contents",
    "MacOS",
    "Sudhir Computer Use",
  );
  const child = spawn(executablePath, [], {{ stdio: "ignore" }});
  child.on("error", () => {{}});
  child.unref();
}}

function installChromeComputerUseMetadata(sky) {{
  for (const [property, value] of Object.entries(sky)) {{
    if (property === "list_apps" || typeof value !== "function") continue;
    const instrumentedAction = (...args) => {{
      if (isChromeComputerUseInput(args[0])) {{
        Reflect.get(globalThis, "nodeRepl")?.setResponseMeta?.({{
          [CHROME_COMPUTER_USE_META_KEY]: true,
        }});
      }}
      return Reflect.apply(value, sky, args);
    }};
    Reflect.set(sky, property, instrumentedAction);
  }}
}}

function isChromeComputerUseInput(input) {{
  if (typeof input !== "object" || input == null) return false;
  const descriptor = Object.getOwnPropertyDescriptor(input, "app");
  if (
    descriptor == null ||
    !("value" in descriptor) ||
    typeof descriptor.value !== "string"
  ) {{
    return false;
  }}
  const app = descriptor.value.trim();
  return (
    ["chrome", "google chrome", "com.google.chrome"].includes(
      app.toLowerCase(),
    ) || MAC_CHROME_APP_PATH_PATTERN.test(app)
  );
}}
'''


def _primary_wrapper_text(paths: ControlRuntimePaths) -> str:
    return _wrapper_text(paths)


def _rewrite_instructions(source: str) -> str:
    replacement = '''## Bootstrap

Load Computer Use through the plugin-owned wrapper. Do not import `@oai/sky`
directly from the JavaScript session.

The absolute path shown for this skill ends in `/skills/computer-use/SKILL.md`.
Remove that suffix to determine `<plugin root>`, then run this once per fresh
`node_repl` session:

```js
if (!globalThis.sky) {
  const { setupComputerUseRuntime } = await import(
    "<plugin root>/scripts/computer-use-client.mjs"
  );
  await setupComputerUseRuntime({ globals: globalThis });
}
```

'''
    updated, count = _BOOTSTRAP.subn(replacement, source, count=1)
    if count != 1:
        raise ControlRuntimeError("Computer Use plugin has no recognised Bootstrap section")
    if 'import("@oai/sky")' in updated or "import('@oai/sky')" in updated:
        raise ControlRuntimeError("Computer Use instructions still import @oai/sky directly")
    return updated


def _instruction_template(
    working_state: Path,
    relative: Path,
) -> Path:
    control_root = working_state / CONTROL_ROOT_NAME
    candidates: list[Path] = []
    candidates.extend(root / relative for root in reversed(_plugin_roots(working_state)))
    original_plugins = control_root / "original-plugins"
    if original_plugins.is_dir():
        candidates.extend(
            root / relative
            for root in sorted(original_plugins.iterdir(), reverse=True)
            if root.is_dir()
        )
    candidates.extend(
        root / relative
        for root in sorted(control_root.glob("original-plugin-*"), reverse=True)
        if root.is_dir()
    )
    for candidate in candidates:
        if not candidate.is_file():
            continue
        source = candidate.read_text(encoding="utf-8")
        if _BOOTSTRAP.search(source):
            return candidate
    raise ControlRuntimeError(
        f"Computer Use plugin has no recoverable instruction template for {relative}"
    )


def _prepare_plugin_assets(
    paths: ControlRuntimePaths,
    working_state: Path,
) -> PluginAssets:
    if not _plugin_roots(working_state):
        raise ControlRuntimeError("Transition state has no installed Computer Use plugin")
    assets = _asset_paths(working_state / CONTROL_ROOT_NAME / PLUGIN_ASSETS_NAME)
    _write_atomic(
        assets.wrapper,
        _wrapper_text(paths).encode("utf-8"),
        mode=0o600,
    )
    for relative, destination in (
        (PLUGIN_FILES[1], assets.node_repl_instructions),
        (PLUGIN_FILES[2], assets.skill),
    ):
        source = _instruction_template(working_state, relative)
        _write_atomic(
            destination,
            _rewrite_instructions(source.read_text(encoding="utf-8")).encode("utf-8"),
            mode=0o600,
        )
    return assets


def _install_plugin_assets(
    working_state: Path,
    assets: PluginAssets,
    *,
    backups: Path | None = None,
) -> list[str]:
    roots = _plugin_roots(working_state)
    if not roots:
        raise ControlRuntimeError("Transition state has no installed Computer Use plugin")
    if backups is None:
        backups = working_state / CONTROL_ROOT_NAME / "original-plugins"
    versions: list[str] = []
    sources = {
        PLUGIN_FILES[0]: assets.wrapper,
        PLUGIN_FILES[1]: assets.node_repl_instructions,
        PLUGIN_FILES[2]: assets.skill,
    }
    for root in roots:
        versions.append(root.name)
        for relative, source in sources.items():
            destination = root / relative
            backup = backups / root.name / relative
            if destination.is_file() and not backup.exists():
                backup.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(destination, backup)
                os.chmod(backup, 0o600)
            destination.parent.mkdir(parents=True, exist_ok=True)
            _write_atomic(destination, source.read_bytes(), mode=0o600)
    return versions


def _prepare_primary_plugin_assets(paths: ControlRuntimePaths) -> PluginAssets:
    state = paths.primary_state
    if not _plugin_roots(state):
        raise ControlRuntimeError("Primary state has no installed Computer Use plugin")
    assets = _asset_paths(state / "control" / PLUGIN_ASSETS_NAME)
    _write_atomic(
        assets.wrapper,
        _primary_wrapper_text(paths).encode("utf-8"),
        mode=0o600,
    )
    for relative, destination in (
        (PLUGIN_FILES[1], assets.node_repl_instructions),
        (PLUGIN_FILES[2], assets.skill),
    ):
        source = _instruction_template(state, relative)
        _write_atomic(
            destination,
            _rewrite_instructions(source.read_text(encoding="utf-8")).encode("utf-8"),
            mode=0o600,
        )
    return assets


def provision_primary_control_runtime(paths: ControlRuntimePaths) -> dict[str, Any]:
    """Verify shared control components and patch primary-home plugin assets."""

    component_metadata = _verify_control_components(paths)
    _verify_openai_node_repl(paths.node_repl)
    if not paths.node.is_file() or not os.access(paths.node, os.X_OK):
        raise ControlRuntimeError(f"Legacy Node runtime is missing: {paths.node}")
    if not paths.official_node_modules.is_dir():
        raise ControlRuntimeError(
            f"Official Computer Use modules are missing: {paths.official_node_modules}"
        )
    assets = _prepare_primary_plugin_assets(paths)
    plugin_versions = _install_plugin_assets(
        paths.primary_state,
        assets,
        backups=paths.primary_state / "control" / "original-plugins",
    )
    return {
        **component_metadata,
        "controlNodeRepl": str(paths.node_repl),
        "controlNode": str(paths.node),
        "controlHelper": str(paths.helper),
        "controlSocket": str(paths.socket),
        "controlPluginVersions": plugin_versions,
        "controlPluginWrapperSha256": _sha256(assets.wrapper),
    }


def _verify_runtime(
    paths: ControlRuntimePaths,
    working_state: Path,
) -> dict[str, str]:
    runtime = ControlRuntimePaths(
        transition_state=working_state,
        primary_state=paths.primary_state,
        official_app=paths.official_app,
        legacy_source=paths.legacy_source,
    )
    _verify_openai_node_repl(runtime.node_repl)
    if not runtime.node.is_file() or not os.access(runtime.node, os.X_OK):
        raise ControlRuntimeError(f"Copied Node runtime is missing: {runtime.node}")
    if not runtime.legacy_create_client.is_file():
        raise ControlRuntimeError(
            f"Copied Computer Use client is missing: {runtime.legacy_create_client}"
        )
    native_pipe_source = runtime.legacy_native_pipe.read_text(encoding="utf-8")
    if native_pipe_source.count(_TRANSITION_SERVICE_PATH_KEY) != 1:
        raise ControlRuntimeError("Copied Computer Use client lacks transition service key")
    if native_pipe_source.count(_TRANSITION_SOCKET_PATH_KEY) != 1:
        raise ControlRuntimeError("Copied Computer Use client lacks transition socket key")
    return {
        "controlNodeReplSha256": _sha256(runtime.node_repl),
        "controlNodeSha256": _sha256(runtime.node),
        "controlCreateClientSha256": _sha256(runtime.legacy_create_client),
        "controlNativePipeSha256": _sha256(runtime.legacy_native_pipe),
    }


def provision_control_runtime(
    paths: ControlRuntimePaths,
    working_state: Path,
) -> dict[str, Any]:
    """Copy/verify the runtime and patch every installed Computer Use plugin."""

    component_metadata = _verify_control_components(paths)
    _copy_runtime(paths, working_state)
    _patch_runtime(paths, working_state)
    assets = _prepare_plugin_assets(paths, working_state)
    plugin_versions = _install_plugin_assets(working_state, assets)
    runtime_metadata = _verify_runtime(paths, working_state)
    return {
        **component_metadata,
        **runtime_metadata,
        "controlRuntime": str(paths.runtime),
        "controlNodeRepl": str(paths.node_repl),
        "controlNode": str(paths.node),
        "controlHelper": str(paths.helper),
        "controlSocket": str(paths.socket),
        "controlPluginVersions": plugin_versions,
        "controlPluginWrapperSha256": _sha256(assets.wrapper),
    }


def _set_table_string(
    source: str,
    table: str,
    key: str,
    value: str,
    *,
    insert_after: str | None = None,
) -> str:
    lines = source.splitlines(keepends=True)
    current = ""
    matches: list[int] = []
    table_lines: list[int] = []
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            current = stripped[1:-1]
            continue
        if current != table:
            continue
        table_lines.append(index)
        if line.lstrip().startswith(f"{key} ") or line.lstrip().startswith(
            f"{key}="
        ):
            matches.append(index)
    replacement = f"{key} = {json.dumps(value)}\n"
    if len(matches) == 1:
        lines[matches[0]] = replacement
        return "".join(lines)
    if matches:
        raise ControlRuntimeError(
            f"Expected one {table}.{key}, found {len(matches)}"
        )
    if insert_after is None:
        raise ControlRuntimeError(f"Missing required setting {table}.{key}")
    insert_index = None
    for index in table_lines:
        line = lines[index]
        if line.lstrip().startswith(f"{insert_after} ") or line.lstrip().startswith(
            f"{insert_after}="
        ):
            insert_index = index + 1
            break
    if insert_index is None:
        raise ControlRuntimeError(
            f"Cannot insert {table}.{key}; {insert_after} is missing"
        )
    lines.insert(insert_index, replacement)
    return "".join(lines)


def apply_control_config(
    source: str,
    paths: ControlRuntimePaths,
    *,
    official_version: str,
    browser_client_hash: str,
) -> str:
    """Pin the transition config to the isolated runtime and shared helper."""

    trusted_paths = f"{paths.transition_state}:{paths.official_node_modules}"
    values = (
        ("mcp_servers.node_repl", "command", str(paths.node_repl), None),
        (
            "mcp_servers.node_repl.env",
            "NODE_REPL_NODE_MODULE_DIRS",
            str(paths.official_node_modules),
            None,
        ),
        (
            "mcp_servers.node_repl.env",
            "NODE_REPL_NODE_PATH",
            str(paths.node),
            None,
        ),
        (
            "mcp_servers.node_repl.env",
            "NODE_REPL_TRUSTED_CODE_PATHS",
            trusted_paths,
            None,
        ),
        (
            "mcp_servers.node_repl.env",
            "NODE_REPL_TRUSTED_BROWSER_CLIENT_SHA256S",
            browser_client_hash,
            None,
        ),
        (
            "mcp_servers.node_repl.env",
            "BROWSER_USE_CODEX_APP_VERSION",
            official_version,
            None,
        ),
        (
            "mcp_servers.node_repl.env",
            _TRANSITION_SERVICE_PATH_KEY,
            str(paths.helper),
            _UPSTREAM_SERVICE_PATH_KEY,
        ),
        (
            "mcp_servers.node_repl.env",
            _TRANSITION_SOCKET_PATH_KEY,
            str(paths.socket),
            _TRANSITION_SERVICE_PATH_KEY,
        ),
        (
            "shell_environment_policy.set",
            "NODE_REPL_TRUSTED_CODE_PATHS",
            trusted_paths,
            None,
        ),
        (
            "shell_environment_policy.set",
            "NODE_REPL_TRUSTED_BROWSER_CLIENT_SHA256S",
            browser_client_hash,
            None,
        ),
    )
    updated = source
    for table, key, value, insert_after in values:
        updated = _set_table_string(
            updated,
            table,
            key,
            value,
            insert_after=insert_after,
        )
    tomllib.loads(updated)
    return updated


def apply_primary_control_config(
    source: str,
    paths: ControlRuntimePaths,
    *,
    official_version: str,
    browser_client_hash: str,
) -> str:
    """Restore primary-home Computer Use settings without changing CODEX_HOME."""

    trusted_paths = f"{paths.primary_state}:{paths.official_node_modules}"
    values = (
        ("mcp_servers.node_repl", "command", str(paths.node_repl), None),
        (
            "mcp_servers.node_repl.env",
            "NODE_REPL_NODE_MODULE_DIRS",
            str(paths.official_node_modules),
            None,
        ),
        (
            "mcp_servers.node_repl.env",
            "NODE_REPL_NODE_PATH",
            str(paths.node),
            None,
        ),
        (
            "mcp_servers.node_repl.env",
            "CODEX_HOME",
            str(paths.primary_state),
            "NODE_REPL_NODE_PATH",
        ),
        (
            "mcp_servers.node_repl.env",
            "NODE_REPL_TRUSTED_CODE_PATHS",
            trusted_paths,
            None,
        ),
        (
            "mcp_servers.node_repl.env",
            "NODE_REPL_TRUSTED_BROWSER_CLIENT_SHA256S",
            browser_client_hash,
            None,
        ),
        (
            "mcp_servers.node_repl.env",
            "BROWSER_USE_CODEX_APP_VERSION",
            official_version,
            None,
        ),
        (
            "mcp_servers.node_repl.env",
            _UPSTREAM_SERVICE_PATH_KEY,
            str(paths.helper),
            "BROWSER_USE_CODEX_APP_VERSION",
        ),
        (
            "mcp_servers.node_repl.env",
            _UPSTREAM_SOCKET_PATH_KEY,
            str(paths.socket),
            _UPSTREAM_SERVICE_PATH_KEY,
        ),
        (
            "mcp_servers.node_repl.env",
            _TRANSITION_SERVICE_PATH_KEY,
            str(paths.helper),
            _UPSTREAM_SOCKET_PATH_KEY,
        ),
        (
            "mcp_servers.node_repl.env",
            _TRANSITION_SOCKET_PATH_KEY,
            str(paths.socket),
            _TRANSITION_SERVICE_PATH_KEY,
        ),
        (
            "shell_environment_policy.set",
            "NODE_REPL_TRUSTED_CODE_PATHS",
            trusted_paths,
            None,
        ),
        (
            "shell_environment_policy.set",
            "NODE_REPL_TRUSTED_BROWSER_CLIENT_SHA256S",
            browser_client_hash,
            None,
        ),
    )
    updated = source
    for table, key, value, insert_after in values:
        updated = _set_table_string(
            updated,
            table,
            key,
            value,
            insert_after=insert_after,
        )
    tomllib.loads(updated)
    return updated


def runtime_status(paths: ControlRuntimePaths) -> dict[str, Any]:
    files = {
        "nodeRepl": paths.node_repl,
        "node": paths.node,
        "legacySky": paths.legacy_sky,
        "helper": paths.helper,
        "broker": paths.broker,
    }
    return {
        "ready": all(path.exists() for path in files.values()),
        "paths": {name: str(path) for name, path in files.items()},
        "pluginVersions": [
            path.name for path in _plugin_roots(paths.transition_state)
        ],
    }
