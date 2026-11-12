"""Exercise compiled native help/auth boundaries and real local tools against a loopback-only fixture."""
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import secrets
import subprocess
import sys
import tempfile
import threading
import time

CONTROL = Path(__file__).resolve().parent
PLAN = json.loads((CONTROL / "plan.json").read_text())
ROOT = Path(os.environ.get("GITHUB_WORKSPACE", Path.cwd())).resolve()
SPEC = next(p for p in PLAN["platforms"] if p["id"] == sys.argv[1])
BINARY = ROOT / "codex-rs/target" / SPEC["target"] / "release" / ("codex.exe" if os.name == "nt" else "codex")
if os.environ.get("MANAGED_SMOKE_BINARY"):
    # Local preflight harness only; the workflow never supplies this override.
    BINARY = Path(os.environ["MANAGED_SMOKE_BINARY"]).resolve()


def run(args, env, *, cwd, timeout=90):
    return subprocess.run([str(BINARY), *args], cwd=cwd, env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)


def main():
    records = []
    with tempfile.TemporaryDirectory(prefix="managed-native-") as directory:
        home = Path(directory)
        native_home = home / ".codex"
        native_home.mkdir()
        env = {**os.environ, "HOME": str(home), "USERPROFILE": str(home), "CODEX_HOME": str(native_home), "TERM": "dumb", "CODEXCLAUDE_MANAGED": "1", "CODEX_DISABLE_UPDATE_CHECK": "1"}
        version = run(["--version"], env, cwd=home)
        assert version.returncode == 0 and "0.167.2" in version.stdout
        for args in [["--help"], ["exec", "--help"]]:
            result = run(args, env, cwd=home)
            assert result.returncode == 0
            assert "Ask Codex" not in result.stdout and "OpenAI's command-line" not in result.stdout
            records.append({"check": "help", "args": args, "passed": True})
        for action, args in [("login", ["login", "status"]), ("logout", ["logout"]), ("update", ["update"])]:
            result = run(args, env, cwd=home)
            assert result.returncode == 0
            expected = "codexclaude@latest" if action == "update" else "codexclaude " + action
            assert expected in result.stdout + result.stderr, "Managed auth/update escaped into an upstream flow"
            assert not (native_home / "auth.json").exists()
            records.append({"check": "managed-" + action, "passed": True})
        # Direct, explicitly upstream behavior stays upstream and does not initiate OAuth.
        direct_env = {k: v for k, v in env.items() if k != "CODEXCLAUDE_MANAGED"}
        upstream = run(["login", "status"], direct_env, cwd=home)
        assert upstream.returncode != 0 and "Not logged in" in upstream.stdout + upstream.stderr
        assert "codexclaude login" not in upstream.stdout + upstream.stderr
        records.append({"check": "unmanaged-status-stays-upstream", "passed": True})

        catalog = json.loads((CONTROL / "catalog.json").read_text())
        (home / "catalog.json").write_text(json.dumps(catalog))
        state = {"call": 0, "verified": False, "marker": "", "tool": "", "requests": []}

        class Provider(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                data = b'{"object":"list","data":[]}'
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_POST(self):
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    assert 0 < length < 5_000_000
                    body = json.loads(self.rfile.read(length))
                    assert self.headers.get("Authorization") == "Bearer synthetic-ci-token"
                    state["requests"].append(body.get("model"))
                    state["call"] += 1
                    first = state["call"] == 1
                    response_id = "resp_native_" + str(state["call"])
                    if first:
                        tools = {t.get("name"): t for t in body.get("tools", [])}
                        name = next((n for n in ["shell_command", "exec_command", "shell"] if n in tools), None)
                        assert name, "Native client did not provide a local shell tool"
                        state["tool"] = name
                        if name == "exec_command":
                            arguments = {"cmd": "cat native-verify.txt", "workdir": str(home), "yield_time_ms": 1000, "max_output_tokens": 1000}
                        elif name == "shell":
                            arguments = {"command": ["cmd", "/c", "type native-verify.txt"] if os.name == "nt" else ["sh", "-c", "cat native-verify.txt"], "workdir": str(home), "timeout_ms": 10000}
                        else:
                            arguments = {"command": "cat native-verify.txt", "workdir": str(home), "timeout_ms": 10000}
                        item = {"type": "function_call", "id": "fc_native_verify", "call_id": "call_native_verify", "name": name, "arguments": json.dumps(arguments), "status": "completed"}
                    else:
                        assert state["marker"] in json.dumps(body.get("input", [])), "The local tool result was not returned intact"
                        state["verified"] = True
                        item = {"type": "message", "id": "msg_native_verify", "role": "assistant", "status": "completed", "content": [{"type": "output_text", "text": state["marker"], "annotations": []}]}
                    response = {"id": response_id, "object": "response", "status": "completed", "model": body.get("model"), "output": [item], "error": None, "incomplete_details": None, "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2, "input_tokens_details": {"cached_tokens": 0}, "output_tokens_details": {"reasoning_tokens": 0}}}
                    events = [{"type": "response.created", "response": {**response, "status": "in_progress", "output": []}}, {"type": "response.output_item.added", "output_index": 0, "item": {**item, "arguments": ""} if first else {**item, "content": []}}]
                    if first:
                        events += [{"type": "response.function_call_arguments.delta", "item_id": item["id"], "output_index": 0, "delta": item["arguments"]}, {"type": "response.function_call_arguments.done", "item_id": item["id"], "output_index": 0, "arguments": item["arguments"]}]
                    else:
                        events += [{"type": "response.content_part.added", "item_id": item["id"], "output_index": 0, "content_index": 0, "part": {"type": "output_text", "text": "", "annotations": []}}, {"type": "response.output_text.delta", "item_id": item["id"], "output_index": 0, "content_index": 0, "delta": state["marker"]}, {"type": "response.output_text.done", "item_id": item["id"], "output_index": 0, "content_index": 0, "text": state["marker"]}, {"type": "response.content_part.done", "item_id": item["id"], "output_index": 0, "content_index": 0, "part": item["content"][0]}]
                    events += [{"type": "response.output_item.done", "output_index": 0, "item": item}, {"type": "response.completed", "response": response}]
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Connection", "close")
                    self.end_headers()
                    for event in events:
                        self.wfile.write(("event: " + event["type"] + "\ndata: " + json.dumps(event) + "\n\n").encode())
                    self.wfile.flush()
                    self.close_connection = True
                except Exception as exc:
                    state["fixtureError"] = str(exc)
                    self.send_error(500, "Loopback fixture rejected the native request")

        server = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            config_lines = [
                'model = "gpt-6.1-sol"', 'model_provider = "managed_fixture"',
                'model_catalog_json = ' + json.dumps(str(home / "catalog.json")),
                'approval_policy = "never"', 'sandbox_mode = "danger-full-access"',
                'check_for_update_on_startup = false', 'analytics_enabled = false', 'feedback_enabled = false',
                '[model_providers.managed_fixture]', 'name = "MagnetAPI.org"',
                'base_url = "http://127.0.0.1:' + str(server.server_port) + '/v1"',
                'wire_api = "responses"', 'supports_websockets = false',
                'experimental_bearer_token = "synthetic-ci-token"',
                '[projects.' + json.dumps(str(home)) + ']', 'trust_level = "trusted"', '',
            ]
            (native_home / "config.toml").write_text("\n".join(config_lines))
            for model in catalog["models"]:
                if model["visibility"] != "list":
                    continue
                state.update(call=0, verified=False, marker="NATIVE_TOOL_" + secrets.token_hex(8), tool="", requests=[])
                (home / "native-verify.txt").write_text(state["marker"] + "\n")
                result = run(["exec", "--skip-git-repo-check", "--json", "--model", model["slug"], "Read native-verify.txt with the local shell, then return its exact contents only. Do not modify files."], env, cwd=home)
                events = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
                answered = any(event.get("type") == "item.completed" and event.get("item", {}).get("type") == "agent_message" and state["marker"] in event["item"].get("text", "") for event in events)
                assert result.returncode == 0 and answered and state["verified"], json.dumps({"model": model["slug"], "exit": result.returncode, "error": state.get("fixtureError"), "stderr": result.stderr[-1500:]})
                assert state["requests"] == [model["slug"], model["slug"]], "Selected wire model changed in a tool continuation"
                records.append({"check": "local-tool-continuation", "model": model["slug"], "tool": state["tool"], "wireModelPreserved": True, "cleanJson": True, "passed": True})
            if os.name != "nt":
                # Real terminal, cursor-position responses, no pipe/output rewriting.
                import fcntl
                import pty
                import select
                import signal
                import struct
                import termios
                master, slave = pty.openpty()
                fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 45, 115, 0, 0))
                tty_env = {**env, "TERM": "xterm-256color"}
                child = subprocess.Popen([str(BINARY), "--no-alt-screen"], cwd=home, env=tty_env, stdin=slave, stdout=slave, stderr=slave, start_new_session=True)
                os.close(slave)
                transcript = bytearray()
                start = time.monotonic()
                try:
                    while time.monotonic() - start < 35 and child.poll() is None:
                        if select.select([master], [], [], .2)[0]:
                            try:
                                data = os.read(master, 65536)
                            except OSError:
                                break
                            transcript.extend(data)
                            if b"\x1b[6n" in data:
                                os.write(master, b"\x1b[1;1R")
                            if b"\x1b]11;?" in data:
                                os.write(master, b"\x1b]11;rgb:1010/1010/1010\x1b\\")
                            if b"Ask CoderX" in transcript and b"MagnetAPI.org Codex CLI" in transcript:
                                break
                finally:
                    if child.poll() is None:
                        os.killpg(child.pid, signal.SIGTERM)
                    try:
                        child.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        os.killpg(child.pid, signal.SIGKILL)
                        child.wait(timeout=5)
                    os.close(master)
                text = transcript.decode(errors="replace")
                assert "Ask CoderX" in text and "MagnetAPI.org Codex CLI" in text
                assert "OpenAI Codex" not in text
                records.append({"check": "native-pty-branding", "passed": True})
            else:
                from windows_conpty import capture_idle
                text = capture_idle(BINARY, home, {**env, "TERM": "xterm-256color"})
                assert "Ask CoderX" in text and "MagnetAPI.org Codex CLI" in text
                assert "OpenAI Codex" not in text
                records.append({"check": "native-windows-conpty-branding", "passed": True})
        finally:
            server.shutdown()
        (ROOT / "native-smoke.json").write_text(json.dumps({"platform": SPEC["id"], "records": records, "network": "loopback only; no client credentials, live purchases or provider calls"}, indent=2) + "\n")
        print(json.dumps({"platform": SPEC["id"], "checks": len(records), "allPassed": True}))


if __name__ == "__main__":
    main()
