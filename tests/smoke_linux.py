#!/usr/bin/env python3
"""Run the real Linux viewer / TeX / Neovim integration in a simulated terminal.

Requires LuaLaTeX, synctex, Neovim >= 0.10, pynvim, nvr, and a built tdf with
this repository's socket patch and the synctex feature. No Windows commands
are simulated: Windows Terminal pane launching is outside this test's scope.
"""

import argparse
import errno
import fcntl
import hashlib
import os
from pathlib import Path
import pty
import re
import select
import shutil
import signal
import struct
import subprocess
import sys
import tempfile
import termios
import threading
import time


REPO = Path(__file__).resolve().parents[1]


class SmokeFailure(RuntimeError):
    pass


def check(condition, message):
    if not condition:
        raise SmokeFailure(message)


def run(argv, cwd=None, timeout=30):
    result = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    check(result.returncode == 0, f"Command failed: {argv!r}\n{result.stdout[-4000:]}\n{result.stderr[-4000:]}")
    return result.stdout


def wait_for(predicate, label, timeout=20, process=None):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        if process is not None and process.poll() is not None:
            raise SmokeFailure(f"{label}: process exited with {process.returncode}")
        time.sleep(0.05)
    raise SmokeFailure(f"Timed out waiting for {label} ({timeout:g}s)")


def terminate_group(process):
    if process is None or process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=3)


def claim_controlling_terminal():
    # Popen performs setsid() before preexec_fn. The inherited PTY must also
    # become this session's controlling terminal for crossterm's /dev/tty use.
    fcntl.ioctl(0, termios.TIOCSCTTY, 0)


class SixelTerminal:
    """Small capture harness; records text status and completed SIXEL frames.

    It answers graphics/cell-size probes, but does not display or decode raster
    images. Its text screen supports the cursor moves used by ratatui updates.
    """

    def __init__(self, master, rows=32, columns=100):
        self.master = master
        self.rows = rows
        self.columns = columns
        self.screen = [[" "] * columns for _ in range(rows)]
        self.row = self.column = 0
        self.saved = (0, 0)
        self.state = "text"
        self.control = bytearray()
        self.dcs_header = bytearray()
        self.dcs_sixel = False
        self.dcs_escape = False
        self.sixels = 0
        self.probes = 0
        self.raw_tail = bytearray()
        self.error = None
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self.read, name="sixel-capture", daemon=True)

    def start(self):
        self.thread.start()

    def write(self, data):
        os.write(self.master, data)

    def snapshot(self):
        with self.lock:
            return self.sixels, "\n".join("".join(row) for row in self.screen), self.error

    def read(self):
        try:
            while not self.stop.is_set():
                ready, _, _ = select.select([self.master], [], [], 0.1)
                if ready:
                    try:
                        chunk = os.read(self.master, 65536)
                    except OSError as exc:
                        if exc.errno == errno.EIO:
                            break
                        raise
                    if not chunk:
                        break
                    with self.lock:
                        self.raw_tail.extend(chunk)
                        del self.raw_tail[:-12000]
                        self.feed(chunk)
        except Exception as exc:
            with self.lock:
                self.error = repr(exc)

    def feed(self, data):
        for byte in data:
            if self.state == "dcs":
                if self.dcs_escape and byte == ord("\\"):
                    if self.dcs_sixel:
                        self.sixels += 1
                    self.state = "text"
                    self.dcs_escape = False
                else:
                    self.dcs_escape = byte == 27
                    if not self.dcs_sixel and len(self.dcs_header) < 64:
                        self.dcs_header.append(byte)
                        if re.fullmatch(rb"[0-9;?]*q", self.dcs_header):
                            self.dcs_sixel = True
                continue
            if self.state == "osc":
                if byte == 7 or (self.dcs_escape and byte == ord("\\")):
                    self.state = "text"
                self.dcs_escape = byte == 27
                continue
            if self.state == "escape":
                if byte == ord("["):
                    self.state = "csi"
                    self.control.clear()
                elif byte == ord("P"):
                    self.state = "dcs"
                    self.dcs_header.clear()
                    self.dcs_sixel = self.dcs_escape = False
                elif byte in (ord("]"), ord("_")):
                    self.state = "osc"
                    self.dcs_escape = False
                else:
                    self.state = "text"
                continue
            if self.state == "csi":
                if 0x40 <= byte <= 0x7E:
                    self.csi(self.control.decode("ascii", "ignore"), chr(byte))
                    self.state = "text"
                else:
                    self.control.append(byte)
                continue
            if byte == 27:
                self.state = "escape"
            elif byte == 13:
                self.column = 0
            elif byte == 10:
                self.row = min(self.rows - 1, self.row + 1)
            elif byte == 8:
                self.column = max(0, self.column - 1)
            elif 32 <= byte <= 126:
                if self.column < self.columns:
                    self.screen[self.row][self.column] = chr(byte)
                self.column = min(self.columns, self.column + 1)

    def csi(self, values, final):
        # Capability replies precede status: the picker stops at the latter.
        if final == "n" and values == "5":
            self.write(b"\x1b[?62;4c\x1b[6;16;8t\x1b[0n")
            self.probes += 1
            return
        if final == "n" and values == "6":
            self.write(f"\x1b[{self.row + 1};{self.column + 1}R".encode())
            return
        if final == "t" and values == "16":
            # Answered together with [5n to preserve response ordering.
            return
        if values.startswith("?"):
            return
        parts = values.split(";")
        args = [int(part) if part.isdigit() else 0 for part in parts]
        first = args[0] or 1
        if final in ("H", "f"):
            self.row = min(self.rows - 1, max(0, first - 1))
            self.column = min(self.columns - 1, max(0, (args[1] or 1) - 1)) if len(args) > 1 else 0
        elif final == "G":
            self.column = min(self.columns - 1, first - 1)
        elif final == "d":
            self.row = min(self.rows - 1, first - 1)
        elif final == "A":
            self.row = max(0, self.row - first)
        elif final == "B":
            self.row = min(self.rows - 1, self.row + first)
        elif final == "C":
            self.column = min(self.columns - 1, self.column + first)
        elif final == "D":
            self.column = max(0, self.column - first)
        elif final == "s":
            self.saved = (self.row, self.column)
        elif final == "u":
            self.row, self.column = self.saved
        elif final == "J" and args[0] in (2, 3):
            self.screen = [[" "] * self.columns for _ in range(self.rows)]
        elif final == "K":
            if args[0] == 2:
                self.screen[self.row] = [" "] * self.columns
            elif args[0] == 1:
                self.screen[self.row][:self.column + 1] = [" "] * (self.column + 1)
            else:
                self.screen[self.row][self.column:] = [" "] * (self.columns - self.column)

    def close(self):
        self.stop.set()
        self.thread.join(timeout=2)
        os.close(self.master)


def fixture(root):
    project = root / "paper 日本語 with spaces"
    chapters = project / "chapters"
    chapters.mkdir(parents=True)
    build = project / "build"
    build.mkdir()
    main = project / "main.tex"
    child = chapters / "second chapter.tex"
    main.write_text(
        "\\documentclass[letterpaper]{article}\n"
        "\\begin{document}\n"
        "\\noindent FIRST PAGE: the viewer must start here.\n"
        "\\newpage\n"
        "\\input{chapters/second chapter.tex}\n"
        "\\end{document}\n", encoding="utf-8")
    child.write_text(
        "% !TeX root = ../main.tex\n"
        "\\vspace*{230pt}\n"
        "\\noindent SECOND PAGE: SyncTeX forward target.\\\\\n"
        "The inverse search must return to this included source.\\\\\n"
        "A second text line near the center of the page.\n", encoding="utf-8")
    run(["lualatex", "-interaction=nonstopmode", "-halt-on-error", "-synctex=1",
         "-output-directory=build", "main.tex"], cwd=project)
    pdf = build / "main.pdf"
    check(pdf.is_file() and (build / "main.synctex.gz").is_file(), "TeX did not generate PDF and SyncTeX data")
    output = run(["synctex", "view", "-i", f"3:1:{child}", "-o", str(pdf)], cwd=project)
    pages = re.findall(r"^Page:(\d+)$", output, re.MULTILINE)
    check(pages and set(pages) == {"2"}, f"Child forward query should select page 2:\n{output}")
    # letterpaper is exactly 612 x 792 PDF points. tdf's 's' uses its center.
    inverse = run(["synctex", "edit", "-o", f"2:306:396:{pdf}"], cwd=project)
    input_match = re.search(r"^Input:(.+)$", inverse, re.MULTILINE)
    line_match = re.search(r"^Line:(\d+)$", inverse, re.MULTILINE)
    check(input_match and line_match, f"No inverse result for page 2 center:\n{inverse}")
    expected_source = Path(input_match[1])
    if not expected_source.is_absolute():
        expected_source = project / expected_source
    check(expected_source.resolve() == child.resolve(), f"Page 2 center should resolve to included child:\n{inverse}")
    expected_line = int(line_match[1])
    check(1 <= expected_line <= 5, f"Invalid inverse line: {expected_line}")
    return project, main, child, pdf, expected_line


def smoke(options):
    try:
        import pynvim
    except ImportError as exc:
        raise SmokeFailure("pynvim is required; install neovim-remote (provides nvr and pynvim)") from exc
    for executable in ("lualatex", "synctex", "nvim", "nvr"):
        check(shutil.which(executable), f"Required command not found: {executable}")
    viewer = Path(options.viewer).expanduser().resolve()
    check(viewer.is_file() and os.access(viewer, os.X_OK), f"Viewer is not executable: {viewer}")
    check("--synctex-forward" in run([str(viewer), "--help"]), "Viewer lacks SyncTeX CLI")

    editor = preview = terminal = nvim = None
    viewer_socket = None
    with tempfile.TemporaryDirectory(prefix="tpdf-smoke-") as temporary:
        root = Path(temporary)
        project, main, child, pdf, expected_line = fixture(root)
        print("PASS: actual LuaLaTeX compilation; child source / page 2 SyncTeX mapping; build output and Unicode/space paths", flush=True)
        server = root / "nvim.sock"
        digest = hashlib.sha256(f"{server}\n{pdf}".encode()).hexdigest()[:32]
        viewer_socket = Path(f"/tmp/tpdf-{os.getuid()}-{digest}.sock")
        nvim_log = root / "nvim.log"
        try:
            env = os.environ.copy()
            env.pop("NVIM", None)
            env.pop("NVIM_LISTEN_ADDRESS", None)
            with nvim_log.open("wb") as log:
                editor = subprocess.Popen(["nvim", "--headless", "--clean", "--listen", str(server)],
                                          stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                          cwd=project, env=env, start_new_session=True)
            wait_for(server.exists, "Neovim RPC server", timeout=10, process=editor)
            nvim = pynvim.attach("socket", path=str(server))
            version = nvim.exec_lua("return vim.version()")
            check((version["major"], version["minor"]) >= (0, 10), f"Neovim >= 0.10 required, found {version}")
            nvim.exec_lua("""
                local plugin_dir, launcher, viewer = ...
                vim.opt.runtimepath:prepend(plugin_dir)
                vim.g.tpdf_smoke_notifications = {}
                vim.notify = function(message, level)
                  local messages = vim.g.tpdf_smoke_notifications
                  table.insert(messages, { message = tostring(message), level = level })
                  vim.g.tpdf_smoke_notifications = messages
                end
                require('terminal_pdf').setup({
                  launcher = launcher, viewer = viewer, keymaps = false,
                  resolve_pdf = function() return 'build/main.pdf' end,
                })
            """, str(REPO / "neovim"), str(REPO / "bin" / "term-pdf"), str(viewer))
            nvim.command("edit " + nvim.funcs.fnameescape(str(child)))
            nvim.current.window.cursor = (3, 0)
            check(nvim.eval("v:servername") == str(server), "Unexpected Neovim RPC server pathname")

            master, slave = pty.openpty()
            fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 32, 100, 800, 512))
            env = os.environ.copy()
            env["TERM"] = "xterm-256color"
            env.pop("TERM_PROGRAM", None)
            env.pop("TMUX", None)
            env["NVIM"] = str(root / "wrong-editor.sock")
            terminal = SixelTerminal(master)
            try:
                preview = subprocess.Popen(
                    [sys.executable, str(REPO / "bin" / "term-pdf"), "view",
                     "--pdf", str(pdf), "--server", str(server), "--socket", str(viewer_socket),
                     "--cwd", str(project), "--viewer", str(viewer)],
                    stdin=slave, stdout=slave, stderr=slave, cwd=project, env=env,
                    start_new_session=True, preexec_fn=claim_controlling_terminal)
            finally:
                os.close(slave)
            terminal.start()
            wait_for(lambda: terminal.snapshot()[0] > 0 and "1 / 2" in terminal.snapshot()[1],
                     "initial page 1 SIXEL output", timeout=options.timeout, process=preview)
            check(terminal.probes > 0, "Viewer did not query terminal capabilities")
            check(viewer_socket.exists(), "Patched viewer did not create the specified Unix socket")
            print("PASS: actual patched tdf starts through term-pdf; completes SIXEL output in a simulated terminal", flush=True)

            previous_frames = terminal.snapshot()[0]
            nvim.command("TermPdfForward")
            wait_for(lambda: terminal.snapshot()[0] > previous_frames and "2 / 2" in terminal.snapshot()[1],
                     "Lua forward command / page 2 SIXEL output", timeout=options.timeout, process=preview)
            notifications = nvim.vars.get("tpdf_smoke_notifications", [])
            errors = [entry for entry in notifications if entry.get("level", 0) >= 4]
            check(not errors, f"Neovim plugin reported errors: {errors}")
            print("PASS: actual :TermPdfForward resolves child root and output; viewer changes to page 2 via Unix IPC", flush=True)

            # Put the editor elsewhere so staying at the forward source cannot
            # produce a false positive for the actual 's' inverse operation.
            nvim.command("edit " + nvim.funcs.fnameescape(str(main)))
            nvim.current.window.cursor = (1, 0)
            terminal.write(b"s")

            def inverse_done():
                return (Path(nvim.current.buffer.name).resolve() == child.resolve()
                        and nvim.current.window.cursor[0] == expected_line)

            wait_for(inverse_done, "viewer 's' -> nvr -> correct Neovim source and line",
                     timeout=options.timeout, process=preview)
            print(f"PASS: actual inverse key uses nvr/RPC and returns to child line {expected_line}, matching synctex edit", flush=True)

            terminal.write(b"q")
            preview.wait(timeout=5)
            check(preview.returncode == 0, f"Viewer/launcher exit code: {preview.returncode}")
            check(terminal.snapshot()[2] is None, f"Terminal reader failed: {terminal.snapshot()[2]}")
            print("PASS: viewer and launcher exit cleanly after q", flush=True)
        except Exception:
            if terminal is not None:
                frames, screen, error = terminal.snapshot()
                print(f"Terminal diagnostics: completed SIXEL frames={frames}, reader_error={error}\n{screen}", file=sys.stderr)
                with terminal.lock:
                    print(f"Last terminal bytes: {bytes(terminal.raw_tail[-1500:])!r}", file=sys.stderr)
            if nvim_log.exists():
                print("Neovim diagnostics:\n" + nvim_log.read_text(errors="replace")[-4000:], file=sys.stderr)
            raise
        finally:
            terminate_group(preview)
            terminate_group(editor)
            if terminal is not None:
                terminal.close()
            if nvim is not None:
                nvim.close()
            if viewer_socket is not None:
                viewer_socket.unlink(missing_ok=True)
    print("Boundary: Linux processes and SIXEL emission were tested; Windows/WSL pane launch, raster appearance, resize behavior and performance were not tested.", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--viewer", required=True, help="Absolute path to the built, patched SyncTeX-enabled tdf")
    parser.add_argument("--timeout", type=float, default=20, help="Timeout for each viewer/RPC stage in seconds")
    options = parser.parse_args()
    check(0 < options.timeout <= 120, "--timeout must be in (0, 120]")
    smoke(options)


if __name__ == "__main__":
    try:
        main()
    except (SmokeFailure, subprocess.TimeoutExpired, OSError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        sys.exit(1)
