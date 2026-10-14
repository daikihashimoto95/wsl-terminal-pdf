import contextlib
import base64
import importlib.machinery
import importlib.util
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import threading
import unittest
from unittest import mock


LAUNCHER = Path(__file__).resolve().parents[1] / "bin" / "term-pdf"
loader = importlib.machinery.SourceFileLoader("term_pdf_launcher", str(LAUNCHER))
spec = importlib.util.spec_from_loader(loader.name, loader)
launcher = importlib.util.module_from_spec(spec)
loader.exec_module(launcher)


class LauncherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="term-pdf-test-")
        self.root = Path(self.temp.name)
        self.pdf = self.root / "論文 $(touch SHOULD_NOT_EXIST); [a].pdf"
        self.pdf.write_bytes(b"%PDF-1.7\n")
        self.tex = self.root / "章:テスト `echo unsafe`; &.tex"
        self.tex.write_text("document", encoding="utf-8")
        self.socket_path = self.root / "tdf.sock"
        self.server = self.root / "editor.sock"
        self.server.touch()
        self.viewer = self.root / "tdf"
        self.viewer.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        self.viewer.chmod(0o755)
        self.wt = self.root / "wt.exe"
        self.wt.write_text("fake", encoding="utf-8")
        self.wt.chmod(0o755)

    def tearDown(self):
        self.temp.cleanup()

    def args(self, command="open", forward=True):
        result = [command, "--pdf", str(self.pdf), "--socket", str(self.socket_path)]
        if command != "forward":
            result += ["--server", str(self.server), "--viewer", str(self.viewer)]
        if forward:
            result += ["--line", "31", "--column", "4", "--tex", str(self.tex)]
        return result

    def capture_error(self, args):
        error = io.StringIO()
        with contextlib.redirect_stderr(error):
            code = launcher.main(args)
        return code, error.getvalue()

    def test_open_uses_private_manifest_and_direct_argv(self):
        captured = {}

        def run(argv, **kwargs):
            captured["argv"] = argv
            captured["kwargs"] = kwargs
            path = Path(argv[-1])
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            captured["request"] = json.loads(path.read_text(encoding="utf-8"))
            captured["path"] = path
            return subprocess.CompletedProcess(argv, 0)

        try:
            with mock.patch.dict(os.environ, {"WSL_DISTRO_NAME": "Ubuntu-24.04"}), mock.patch.object(
                launcher.subprocess, "run", side_effect=run
            ):
                code = launcher.main(self.args() + ["--wt-path", str(self.wt), "--cwd", str(self.root)])
            self.assertEqual(code, 0)
            argv = captured["argv"]
            self.assertEqual(argv[:8], [str(self.wt), "-w", "0", "split-pane", "-V", "-s", "0.45", "wsl.exe"])
            self.assertIn("--exec", argv)
            self.assertNotIn("shell", captured["kwargs"])
            self.assertFalse(any(";" in arg for arg in argv))
            request = captured["request"]
            self.assertEqual(request["pdf"], str(self.pdf))
            self.assertEqual(request["tex"], str(self.tex))
            self.assertEqual(request["cwd"], str(self.root))
            options = launcher.parser().parse_args(["view", "--request", str(captured["path"])])
            self.assertEqual(launcher.load_request(options).tex, str(self.tex))
            self.assertFalse(captured["path"].exists())
        finally:
            if "path" in captured:
                captured["path"].unlink(missing_ok=True)

    def test_forward_delivers_real_unix_socket_protocol(self):
        received = []
        try:
            listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        except PermissionError as exc:
            self.skipTest(f"This runtime forbids Unix sockets: {exc}")
        with listener:
            listener.bind(str(self.socket_path))
            listener.listen(1)

            def receive():
                with listener.accept()[0] as conn:
                    chunks = []
                    while True:
                        data = conn.recv(4096)
                        if not data:
                            break
                        chunks.append(data)
                    received.append(b"".join(chunks))

            thread = threading.Thread(target=receive)
            thread.start()
            code = launcher.main(self.args("forward"))
            thread.join(timeout=2)
            self.assertFalse(thread.is_alive())
        self.assertEqual(code, 0)
        self.assertEqual(received, [f"31:4:{self.tex}\n".encode("utf-8")])

    def test_open_reuses_running_viewer_and_sends_forward(self):
        messages = []
        try:
            listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        except PermissionError as exc:
            self.skipTest(f"This runtime forbids Unix sockets: {exc}")
        with listener:
            listener.bind(str(self.socket_path))
            listener.listen(2)

            def receive():
                for _ in range(2):
                    with listener.accept()[0] as conn:
                        messages.append(conn.recv(4096))

            thread = threading.Thread(target=receive)
            thread.start()
            with mock.patch.object(launcher.subprocess, "run") as run:
                self.assertEqual(launcher.main(self.args()), 0)
                run.assert_not_called()
            thread.join(timeout=2)
            self.assertFalse(thread.is_alive())
        self.assertEqual(messages, [b"", f"31:4:{self.tex}\n".encode("utf-8")])

    def test_view_sets_socket_editor_and_limits_prerender(self):
        with mock.patch.dict(os.environ, {"NVIM": "unsafe-old-socket"}), mock.patch.object(
            launcher.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)
        ) as run:
            self.assertEqual(launcher.main(self.args("view") + ["--cwd", str(self.root)]), 0)
        argv = run.call_args.args[0]
        self.assertEqual(argv[:7], [str(self.viewer), "--prerender", "2", "--max-wide", "1", "--reload-delay", "150"])
        self.assertEqual(argv[-1], str(self.pdf))
        env = run.call_args.kwargs["env"]
        self.assertNotIn("NVIM", env)
        self.assertEqual(env["NVIM_LISTEN_ADDRESS"], str(self.server))
        self.assertEqual(env["TDF_SOCKET_PATH"], str(self.socket_path))
        self.assertTrue(env["PATH"].startswith(str(Path.home() / ".local" / "bin") + os.pathsep))
        self.assertEqual(run.call_args.kwargs["cwd"], str(self.root))

    def test_viewer_failure_is_reported(self):
        with mock.patch.object(launcher.subprocess, "run", return_value=subprocess.CompletedProcess([], 7)):
            code, error = self.capture_error(self.args("view"))
        self.assertEqual(code, 1)
        self.assertIn("viewer failed with exit code 7", error)

    def test_missing_viewer_socket(self):
        code, error = self.capture_error(self.args("forward"))
        self.assertEqual(code, 1)
        self.assertIn("viewer is not running", error)
    def test_stale_viewer_socket(self):
        self.socket_path.touch()
        with mock.patch.object(launcher.socket, "socket") as socket_factory:
            socket_factory.return_value.__enter__.return_value.connect.side_effect = ConnectionRefusedError()
            code, error = self.capture_error(self.args("forward"))
        self.assertEqual(code, 1)
        self.assertIn("viewer is not running", error)
        self.assertTrue(self.socket_path.exists(), "Launcher must not unlink another process's socket")

    def test_socket_timeout_is_reported(self):
        self.socket_path.touch()
        with mock.patch.object(launcher.socket, "socket") as socket_factory:
            socket_factory.return_value.__enter__.return_value.connect.side_effect = socket.timeout()
            code, error = self.capture_error(self.args("forward") + ["--timeout", "0.1"])
        self.assertEqual(code, 1)
        self.assertIn("timed out after 0.1s", error)

    def test_forward_payload_is_delivered_without_shell(self):
        self.socket_path.touch()
        with mock.patch.object(launcher.socket, "socket") as socket_factory:
            client = socket_factory.return_value.__enter__.return_value
            self.assertEqual(launcher.main(self.args("forward")), 0)
        client.connect.assert_called_once_with(str(self.socket_path))
        client.sendall.assert_called_once_with(f"31:4:{self.tex}\n".encode("utf-8"))

    def test_encoded_powershell_passes_data_argv(self):
        captured = {}
        fake_powershell = str(self.root / "powershell.exe")

        def which(name):
            return fake_powershell if name == "powershell.exe" else None

        def run(argv, **kwargs):
            self.assertEqual(argv[:5], [fake_powershell, "-NoLogo", "-NoProfile", "-NonInteractive", "-EncodedCommand"])
            program = base64.b64decode(argv[5]).decode("utf-16-le")
            encoded = program.split("FromBase64String('", 1)[1].split("'", 1)[0]
            wt_argv = json.loads(base64.b64decode(encoded).decode("utf-8"))
            self.assertEqual(wt_argv[:4], ["wt.exe", "-w", "0", "split-pane"])
            self.assertNotIn(";", " ".join(wt_argv))
            captured["path"] = Path(wt_argv[-1])
            self.assertEqual(json.loads(captured["path"].read_text())["tex"], str(self.tex))
            return subprocess.CompletedProcess(argv, 0)

        try:
            with mock.patch.dict(os.environ, {"WSL_DISTRO_NAME": "Ubuntu"}), mock.patch.object(
                launcher.shutil, "which", side_effect=which
            ), mock.patch.object(launcher.subprocess, "run", side_effect=run):
                self.assertEqual(launcher.main(self.args()), 0)
        finally:
            if "path" in captured:
                captured["path"].unlink(missing_ok=True)

    def test_newline_in_source_is_rejected_before_ipc(self):
        args = self.args("forward")
        args[-1] += "\n1:2:/other.tex"
        code, error = self.capture_error(args)
        self.assertEqual(code, 1)
        self.assertIn("cannot contain NUL or newlines", error)

    def test_windowsapps_alias_need_not_be_linux_executable(self):
        alias = "/mnt/c/Users/user/AppData/Local/Microsoft/WindowsApps/wt.exe"
        def which(name):
            return {"wt.exe": alias, "powershell.exe": "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"}.get(name)
        with mock.patch.object(launcher.shutil, "which", side_effect=which):
            executable, mode = launcher.terminal_path(launcher.parser().parse_args(["open"]))
        self.assertEqual(mode, "powershell")
        self.assertTrue(executable.endswith("powershell.exe"))

    def test_missing_wt_has_actionable_error_without_shell_fallback(self):
        with mock.patch.object(launcher.shutil, "which", return_value=None), mock.patch.object(
            launcher.subprocess, "run"
        ) as run:
            code, error = self.capture_error(self.args())
        self.assertEqual(code, 1)
        self.assertIn("--wt-path", error)
        run.assert_not_called()

    def test_terminal_failure_removes_manifest(self):
        paths = []

        def failed(argv, **kwargs):
            paths.append(Path(argv[-1]))
            return subprocess.CompletedProcess(argv, 8)

        with mock.patch.dict(os.environ, {"WSL_DISTRO_NAME": "Ubuntu"}), mock.patch.object(
            launcher.subprocess, "run", side_effect=failed
        ):
            code, error = self.capture_error(self.args() + ["--wt-path", str(self.wt)])
        self.assertEqual(code, 1)
        self.assertIn("Windows Terminal failed", error)
        self.assertTrue(paths)
        self.assertFalse(paths[0].exists())

    def test_window_name_is_safe_data(self):
        captured = []

        def run(argv, **kwargs):
            captured.append(argv)
            Path(argv[-1]).unlink()
            return subprocess.CompletedProcess(argv, 0)

        with mock.patch.dict(os.environ, {"WSL_DISTRO_NAME": "Ubuntu"}), mock.patch.object(
            launcher.subprocess, "run", side_effect=run
        ):
            self.assertEqual(launcher.main(self.args() + ["--wt-path", str(self.wt), "--window", "LaTeX research"]), 0)
            code, error = self.capture_error(self.args() + ["--wt-path", str(self.wt), "--window", "0; new-tab"])
        self.assertEqual(captured[0][2], "LaTeX research")
        self.assertEqual(code, 1)
        self.assertIn("--window must be", error)


if __name__ == "__main__":
    unittest.main()
