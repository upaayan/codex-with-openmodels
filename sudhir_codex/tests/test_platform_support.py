import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from sudhir_codex_gateway import platform_support


class PlatformSupportTests(unittest.TestCase):
    @unittest.skipIf(os.name == "nt", "covered by native Windows process test")
    def test_start_lock_serializes_two_callers(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            lock_path = Path(temporary) / "gateway-start.lock"
            first_acquired = threading.Event()
            release_first = threading.Event()
            second_acquired = threading.Event()
            errors: list[Exception] = []

            def first() -> None:
                try:
                    with platform_support.gateway_start_lock(lock_path):
                        first_acquired.set()
                        release_first.wait(timeout=5)
                except Exception as exc:
                    errors.append(exc)

            def second() -> None:
                try:
                    first_acquired.wait(timeout=5)
                    with platform_support.gateway_start_lock(lock_path):
                        second_acquired.set()
                except Exception as exc:
                    errors.append(exc)

            first_thread = threading.Thread(target=first)
            second_thread = threading.Thread(target=second)
            first_thread.start()
            second_thread.start()
            self.assertTrue(first_acquired.wait(timeout=5))
            self.assertFalse(second_acquired.wait(timeout=0.2))
            release_first.set()
            self.assertTrue(second_acquired.wait(timeout=5))
            first_thread.join(timeout=5)
            second_thread.join(timeout=5)

            self.assertEqual(errors, [])
            self.assertFalse(first_thread.is_alive())
            self.assertFalse(second_thread.is_alive())

    @unittest.skipUnless(os.name == "nt", "native Windows lock test")
    def test_windows_start_lock_serializes_processes(self) -> None:
        script = (
            "import pathlib,sys,time\n"
            "from sudhir_codex_gateway.platform_support import gateway_start_lock\n"
            "mode,lock,ready,release,acquired=sys.argv[1:]\n"
            "lock=pathlib.Path(lock); ready=pathlib.Path(ready)\n"
            "release=pathlib.Path(release); acquired=pathlib.Path(acquired)\n"
            "with gateway_start_lock(lock):\n"
            "    if mode == 'first':\n"
            "        ready.write_text('ready')\n"
            "        while not release.exists(): time.sleep(0.05)\n"
            "    else:\n"
            "        acquired.write_text('acquired')\n"
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            lock = root / "gateway-start.lock"
            ready = root / "ready"
            release = root / "release"
            acquired = root / "acquired"
            environment = os.environ.copy()
            source = Path(__file__).resolve().parents[1] / "src"
            existing = environment.get("PYTHONPATH")
            environment["PYTHONPATH"] = (
                f"{source}{os.pathsep}{existing}" if existing else str(source)
            )
            arguments = [
                str(lock),
                str(ready),
                str(release),
                str(acquired),
            ]
            first = subprocess.Popen(
                [sys.executable, "-c", script, "first", *arguments],
                env=environment,
            )
            second: subprocess.Popen[bytes] | None = None
            try:
                deadline = time.monotonic() + 20
                while not ready.exists() and time.monotonic() < deadline:
                    if first.poll() is not None:
                        self.fail(f"First lock process exited with {first.returncode}")
                    time.sleep(0.05)
                self.assertTrue(ready.exists())

                second = subprocess.Popen(
                    [sys.executable, "-c", script, "second", *arguments],
                    env=environment,
                )
                time.sleep(0.5)
                self.assertFalse(acquired.exists())
                release.write_text("release", encoding="ascii")
                first.wait(timeout=20)
                second.wait(timeout=20)

                self.assertEqual(first.returncode, 0)
                self.assertEqual(second.returncode, 0)
                self.assertTrue(acquired.exists())
            finally:
                for process in (first, second):
                    if process is not None and process.poll() is None:
                        process.kill()
                        process.wait(timeout=10)

    def test_windows_acl_uses_current_identity_and_removes_inheritance(self) -> None:
        identity_result = subprocess.CompletedProcess(
            args=["whoami"],
            returncode=0,
            stdout="example\\owner\n",
            stderr="",
        )
        icacls_result = subprocess.CompletedProcess(
            args=["icacls"],
            returncode=0,
            stdout="",
            stderr="",
        )
        with (
            mock.patch.object(platform_support, "WINDOWS", True),
            mock.patch.object(
                platform_support.subprocess,
                "run",
                side_effect=[identity_result, icacls_result],
            ) as run,
        ):
            platform_support.ensure_private_file(Path("private-token"))

        self.assertEqual(
            run.call_args_list[1].args[0],
            [
                "icacls",
                "private-token",
                "/inheritance:r",
                "/grant:r",
                "example\\owner:F",
            ],
        )

    def test_windows_detached_process_starts_without_a_console(self) -> None:
        with (
            mock.patch.object(platform_support, "WINDOWS", True),
            mock.patch(
                "sudhir_codex_gateway.platform_support.subprocess."
                "CREATE_NEW_PROCESS_GROUP",
                0x00000200,
                create=True,
            ),
            mock.patch(
                "sudhir_codex_gateway.platform_support.subprocess.DETACHED_PROCESS",
                0x00000008,
                create=True,
            ),
            mock.patch(
                "sudhir_codex_gateway.platform_support.subprocess.CREATE_NO_WINDOW",
                0x08000000,
                create=True,
            ),
        ):
            flags = platform_support.detached_process_kwargs()

        self.assertEqual(
            flags,
            {"creationflags": 0x08000200},
        )

    @unittest.skipUnless(os.name == "nt", "native Windows process test")
    def test_windows_process_liveness_and_termination(self) -> None:
        process = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)"],
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
        )
        try:
            self.assertTrue(platform_support.process_alive(process.pid))
            platform_support.terminate_process(process.pid)
            process.wait(timeout=10)
            deadline = time.monotonic() + 5
            while (
                platform_support.process_alive(process.pid)
                and time.monotonic() < deadline
            ):
                time.sleep(0.05)
            self.assertFalse(platform_support.process_alive(process.pid))
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=10)

    @unittest.skipUnless(os.name == "nt", "native Windows ACL test")
    def test_windows_private_file_acl_is_accepted_by_icacls(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "private-token"
            path.write_text("secret", encoding="utf-8")

            platform_support.ensure_private_file(path)
            completed = subprocess.run(
                ["icacls", str(path), "/verify"],
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)


if __name__ == "__main__":
    unittest.main()
