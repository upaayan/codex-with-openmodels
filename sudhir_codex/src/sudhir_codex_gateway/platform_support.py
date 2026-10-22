"""Small operating-system boundary for the private gateway runtime."""

import contextlib
import ctypes
import os
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any

WINDOWS = os.name == "nt"


def is_windows() -> bool:
    return WINDOWS


def core_binary_name() -> str:
    return "sudhir-codex-core.exe" if WINDOWS else "sudhir-codex-core"


def venv_python_path(repo_root: Path) -> Path:
    if WINDOWS:
        return repo_root / ".venv" / "Scripts" / "python.exe"
    return repo_root / ".venv" / "bin" / "python"


def installed_launcher_path(repo_root: Path) -> Path:
    if WINDOWS:
        return repo_root / "bin" / "sudhir-codex.cmd"
    return Path.home() / ".local" / "bin" / "sudhir-codex"


def platform_scope() -> str:
    return "Windows" if WINDOWS else "macOS/Unix"


def ensure_private_directory(path: Path) -> None:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if WINDOWS:
        _restrict_windows_acl(path, directory=True)
    else:
        os.chmod(path, 0o700)


def ensure_private_file(path: Path, *, mode: int = 0o600) -> None:
    if WINDOWS:
        _restrict_windows_acl(path, directory=False)
    else:
        os.chmod(path, mode)


def private_access_label(path: Path) -> str | None:
    try:
        if WINDOWS:
            return "windows-acl"
        return oct(path.stat(follow_symlinks=False).st_mode & 0o777)
    except OSError:
        return None


@contextlib.contextmanager
def gateway_start_lock(path: Path) -> Iterator[None]:
    ensure_private_directory(path.parent)
    file_descriptor = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        ensure_private_file(path)
        if WINDOWS:
            import msvcrt

            if os.fstat(file_descriptor).st_size == 0:
                os.write(file_descriptor, b"\0")
            os.lseek(file_descriptor, 0, os.SEEK_SET)
            msvcrt.locking(file_descriptor, msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(file_descriptor, fcntl.LOCK_EX)
        yield
    finally:
        if WINDOWS:
            import msvcrt

            os.lseek(file_descriptor, 0, os.SEEK_SET)
            msvcrt.locking(file_descriptor, msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(file_descriptor, fcntl.LOCK_UN)
        os.close(file_descriptor)


def detached_process_kwargs() -> dict[str, Any]:
    if WINDOWS:
        return {
            "creationflags": (
                subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
            )
        }
    return {"start_new_session": True}


def process_alive(pid: int) -> bool:
    if not WINDOWS:
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True

    kernel32, handle = _open_windows_process(
        0x1000,  # PROCESS_QUERY_LIMITED_INFORMATION
        pid,
    )
    if not handle:
        error = ctypes.get_last_error()
        if error == 5:  # ERROR_ACCESS_DENIED: the process exists.
            return True
        return False
    try:
        from ctypes import wintypes

        exit_code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            raise ctypes.WinError(ctypes.get_last_error())
        return exit_code.value == 259  # STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


def terminate_process(pid: int) -> None:
    if not WINDOWS:
        import signal

        os.kill(pid, signal.SIGTERM)
        return

    kernel32, handle = _open_windows_process(
        0x0001 | 0x00100000,  # PROCESS_TERMINATE | SYNCHRONIZE
        pid,
    )
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        if not kernel32.TerminateProcess(handle, 0):
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        kernel32.CloseHandle(handle)


def _open_windows_process(access: int, pid: int) -> tuple[Any, Any]:
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [
        wintypes.DWORD,
        wintypes.BOOL,
        wintypes.DWORD,
    ]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.GetExitCodeProcess.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel32.TerminateProcess.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    return kernel32, kernel32.OpenProcess(access, False, pid)


def _restrict_windows_acl(path: Path, *, directory: bool) -> None:
    identity = subprocess.run(
        ["whoami"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if not identity:
        raise OSError("Could not determine the current Windows identity")
    permission = "(OI)(CI)F" if directory else "F"
    completed = subprocess.run(
        [
            "icacls",
            str(path),
            "/inheritance:r",
            "/grant:r",
            f"{identity}:{permission}",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise OSError(f"Could not restrict private Windows path {path}")
