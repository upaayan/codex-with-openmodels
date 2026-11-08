"""Core-derived catalog version, with an explicit private compatibility override."""

import os
import re
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path

from .errors import GatewayError
from .platform_support import core_binary_name, ensure_private_directory

_VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?")


@dataclass(frozen=True)
class CatalogVersion:
    actual: str | None
    effective: str | None
    override: str | None
    detection_error: str | None

    def document(self) -> dict:
        return asdict(self)


def resolve_catalog_version(repo_root: Path, state_dir: Path) -> CatalogVersion:
    actual = None
    error = None
    try:
        # Invoke the core directly: the launcher starts the gateway and would recurse.
        result = subprocess.run(
            [str(repo_root / "dist" / core_binary_name()), "--version"],
            env={**os.environ, "CODEX_HOME": str(state_dir)},
            capture_output=True, text=True, check=True, timeout=5,
        )
        match = re.fullmatch(r"codex-cli (" + _VERSION.pattern + r")", result.stdout.strip())
        if not match:
            raise ValueError("unrecognized core --version output")
        actual = match[1]
    except (OSError, subprocess.SubprocessError, ValueError):
        error = "Core version detection failed; check the core path and private CODEX_HOME"
    override = None
    try:
        override = (state_dir / "gateway" / "catalog-version-override").read_text().strip()
        if not _VERSION.fullmatch(override):
            override = None
            error = (error + "; " if error else "") + "Invalid catalog version override"
    except FileNotFoundError:
        pass
    except OSError:
        error = (error + "; " if error else "") + "Cannot read catalog version override"
    return CatalogVersion(actual, override or actual, override, error)


def set_catalog_version_override(state_dir: Path, version: str | None) -> None:
    path = state_dir / "gateway" / "catalog-version-override"
    if version is None:
        path.unlink(missing_ok=True)
        return
    if not _VERSION.fullmatch(version):
        raise GatewayError(64, "invalid_catalog_version", "Use a version such as 0.153.2, or --clear")
    ensure_private_directory(path.parent)
    temporary = path.with_suffix(".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as output:
        output.write(version + "\n")
    temporary.replace(path)
