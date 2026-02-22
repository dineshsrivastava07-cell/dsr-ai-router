#!/usr/bin/env python3
"""
Claude AI Router Proxy — 3-Tier routing with native Ollama Anthropic API
─────────────────────────────────────────────────────────────────────────
  T1 (default) → Ollama /v1/messages (native Anthropic API, no conversion)
                  Model: qwen3-coder:480b-cloud
  T2 (gemini-*) → Gemini CLI bridge (port 4001, Google OAuth)
  T3 (claude-real/opus) → Claude CLI account login (last resort)

Ollama v0.14+ implements the Anthropic Messages API natively at /v1/messages
— no format conversion needed, just passthrough with Authorization: Bearer ollama
"""

import os, json, time, uuid, subprocess, urllib.request, urllib.error
from http.server import HTTPServer, BaseHTTPRequestHandler

CLAUDE_BIN   = os.environ.get("CLAUDE_BIN", "claude")
OLLAMA_HOST  = "http://127.0.0.1:11434"
GEMINI_PORT  = 4001
T1_MODEL     = "qwen3-coder:480b-cloud"
T1_LABEL     = "qwen3-coder-480b"
T2_MODEL     = "gemini-2.5-flash"
T3_MODEL     = "claude-sonnet-4-6"

GEMINI_MODELS = {
    "gemini-2.5-pro", "gemini-2.5-flash", "gemini-2.0-flash",
    "gemini-3-flash-preview", "gemini-3-pro-preview", "gemini-proxy",
}
CLAUDE_REAL_MODELS = {
    "claude-real", "claude-account", "claude-opus-4-6", "claude-opus",
}


# ── SSE helpers (used by T2/T3 synthetic streaming, and loop guard) ──────────
def sse(event, data):
    return f"event: {event}\ndata: {json.dumps(data)}\n\n".encode()

def start_stream(wfile, model):
    msg_id = f"msg_{uuid.uuid4().hex[:24]}"
    wfile.write(sse("message_start", {
        "type": "message_start",
        "message": {"id": msg_id, "type": "message", "role": "assistant",
                    "content": [], "model": model, "stop_reason": None,
                    "usage": {"input_tokens": 10, "output_tokens": 0,
                              "cache_creation_input_tokens": 0,
                              "cache_read_input_tokens": 0}}
    }))
    wfile.write(sse("content_block_start", {
        "type": "content_block_start", "index": 0,
        "content_block": {"type": "text", "text": ""}
    }))
    wfile.flush()
    return msg_id

def send_chunk(wfile, text):
    wfile.write(sse("content_block_delta", {
        "type": "content_block_delta", "index": 0,
        "delta": {"type": "text_delta", "text": text}
    }))
    wfile.flush()

def end_stream(wfile, output_tokens=0):
    wfile.write(sse("content_block_stop", {"type": "content_block_stop", "index": 0}))
    wfile.write(sse("message_delta", {
        "type": "message_delta",
        "delta": {"stop_reason": "end_turn"},
        "usage": {"output_tokens": output_tokens}
    }))
    wfile.write(sse("message_stop", {"type": "message_stop"}))
    wfile.flush()

def non_stream_response(model, text):
    return json.dumps({
        "id": f"msg_{uuid.uuid4().hex[:24]}",
        "type": "message", "role": "assistant", "model": model,
        "content": [{"type": "text", "text": text}],
        "stop_reason": "end_turn", "stop_sequence": None,
        "usage": {"input_tokens": 10, "output_tokens": len(text.split())}
    }).encode()


# ── Content helpers (used by T2/T3 only) ────────────────────────────────────
def _flatten(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if not isinstance(block, dict):
                parts.append(str(block))
                continue
            btype = block.get("type", "")
            if btype == "text":
                parts.append(block.get("text", ""))
            elif btype == "tool_result":
                parts.append(_flatten(block.get("content", "")))
            elif btype == "tool_use":
                parts.append(f"[tool_use: {block.get('name','tool')}({json.dumps(block.get('input',{}))})]")
        return "\n".join(p for p in parts if p)
    return str(content)

def messages_to_prompt(messages, system=""):
    parts = []
    if system:
        parts.append(f"[System: {_flatten(system)}]")
    for m in messages:
        role    = m.get("role", "user")
        content = _flatten(m.get("content", ""))
        parts.append(f"{role}: {content}" if role != "user" else content)
    return "\n".join(parts)


# ── T1: Native Ollama Anthropic API passthrough ──────────────────────────────
def route_ollama_native(wfile, body, stream):
    """
    Passthrough to Ollama's native Anthropic Messages API (/v1/messages).
    Ollama v0.14+ speaks Anthropic wire format natively — no conversion needed.
    We only swap the model name and forward Authorization: Bearer ollama.
    """
    body["model"]  = T1_MODEL
    body["stream"] = stream
    payload = json.dumps(body).encode()

    req = urllib.request.Request(
        f"{OLLAMA_HOST}/v1/messages",
        data=payload,
        headers={
            "Content-Type":      "application/json",
            "Authorization":     "Bearer ollama",
            "anthropic-version": "2023-06-01",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            if stream:
                # Pipe native Anthropic SSE chunks directly — no conversion
                while True:
                    chunk = resp.read(4096)
                    if not chunk:
                        break
                    wfile.write(chunk)
                    wfile.flush()
            else:
                return resp.read()   # raw Anthropic JSON bytes
    except urllib.error.HTTPError as e:
        err_body = e.read().decode(errors="replace")
        print(f"[T1] Ollama HTTP {e.code}: {err_body}", flush=True)
        if stream:
            start_stream(wfile, T1_LABEL)
            send_chunk(wfile, f"[Qwen3 error {e.code}: {err_body}]")
            end_stream(wfile, 1)
        else:
            return non_stream_response(T1_LABEL, f"[Qwen3 error {e.code}: {err_body}]")
    except Exception as e:
        print(f"[T1] Ollama error: {e}", flush=True)
        if stream:
            start_stream(wfile, T1_LABEL)
            send_chunk(wfile, f"[Qwen3 error: {e}]")
            end_stream(wfile, 1)
        else:
            return non_stream_response(T1_LABEL, f"[Qwen3 error: {e}]")


# ── T2: Gemini CLI ────────────────────────────────────────────────────────────
def call_gemini_cli(prompt):
    env = os.environ.copy()
    env["GOOGLE_GENAI_USE_GCA"] = "true"
    for model in [T2_MODEL, "gemini-2.5-flash"]:
        try:
            result = subprocess.run(
                ["gemini", "--model", model, "-p", prompt],
                capture_output=True, text=True, timeout=90, env=env
            )
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip()
        except subprocess.TimeoutExpired:
            return "Gemini timeout"
        except Exception as e:
            return f"Gemini error: {e}"
    return "Gemini: no response"

def route_gemini(wfile, body, stream, model_label):
    messages = body.get("messages", [])
    system   = body.get("system", "")
    prompt   = messages_to_prompt(messages, system)
    text     = call_gemini_cli(prompt)

    if stream:
        start_stream(wfile, model_label)
        chunk_size = 30
        for i in range(0, len(text), chunk_size):
            send_chunk(wfile, text[i:i+chunk_size])
        end_stream(wfile, len(text.split()))
    else:
        return text


# ── T3: Claude CLI account login (last resort) ───────────────────────────────
def route_claude_real(wfile, body, stream, headers_in):
    messages = body.get("messages", [])
    system   = body.get("system", "")
    prompt   = messages_to_prompt(messages, system)

    env = os.environ.copy()
    env.pop("CLAUDECODE", None)
    env.pop("CLAUDE_CODE_SESSION_ID", None)

    cmd = [CLAUDE_BIN, "-p", prompt, "--output-format", "text"]

    if stream:
        start_stream(wfile, T3_MODEL)
        tokens = 0
        try:
            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, env=env,
            )
            for line in proc.stdout:
                if line:
                    send_chunk(wfile, line)
                    tokens += len(line.split())
            proc.wait()
            if proc.returncode != 0:
                err = proc.stderr.read().strip()
                send_chunk(wfile, f"\n[Claude error: {err}]")
        except Exception as e:
            send_chunk(wfile, f"\n[Claude launch error: {e}]")
        end_stream(wfile, tokens)
    else:
        try:
            result = subprocess.run(cmd, capture_output=True, text=True,
                                    env=env, timeout=180)
            if result.returncode == 0:
                return result.stdout.strip()
            return f"[Claude error: {result.stderr.strip()}]"
        except Exception as e:
            return f"[Claude error: {e}]"


# ── HTTP Handler ──────────────────────────────────────────────────────────────
class ProxyHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        tier  = getattr(self, "_tier",  "?")
        model = getattr(self, "_model", "?")
        print(f"[Router] {tier} | {model} | {self.command} {self.path} | {args[1]}",
              flush=True)

    def do_GET(self):
        if self.path in ("/health", "/"):
            body = json.dumps({
                "status": "ok",
                "routing": {
                    "default (claude-*)":  f"T1 → Qwen3-Coder:480B native Ollama API",
                    "gemini-* models":     f"T2 → Gemini {T2_MODEL} (port {GEMINI_PORT})",
                    "claude-real/opus":    "T3 → Claude account login",
                }
            }).encode()
        elif self.path.startswith("/v1/models"):
            body = json.dumps({
                "data": [
                    {"type": "model", "id": T1_LABEL,
                     "display_name": "T1 · Qwen3-Coder 480B (Ollama cloud — default)",
                     "created_at": "2025-01-01T00:00:00Z"},
                    {"type": "model", "id": "gemini-2.5-flash",
                     "display_name": "T2 · Gemini 2.5 Flash (Google OAuth)",
                     "created_at": "2025-01-01T00:00:00Z"},
                    {"type": "model", "id": "claude-real",
                     "display_name": "T3 · Claude Pro (Account login — last resort)",
                     "created_at": "2025-01-01T00:00:00Z"},
                ],
                "has_more": False,
                "first_id": T1_LABEL,
                "last_id":  "claude-real",
            }).encode()
        else:
            self.send_response(404)
            self.end_headers()
            return

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_stream_headers(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()

    def _send_json_bytes(self, raw_bytes):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw_bytes)))
        self.end_headers()
        self.wfile.write(raw_bytes)

    def _send_json(self, text, model):
        self._send_json_bytes(non_stream_response(model, text))

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body   = json.loads(self.rfile.read(length) or b"{}")

        model  = body.get("model", "claude-sonnet-4-6")
        stream = body.get("stream", False)

        self._model = model
        self._tier  = "?"

        # ── Agentic loop guard ────────────────────────────────────────────────
        # Claude Code v2.x makes "should I continue?" follow-up calls after
        # every response. Last message role == "assistant" → immediate end_turn.
        messages = body.get("messages", [])
        if messages and messages[-1].get("role") == "assistant":
            self._tier = "LoopGuard"
            if stream:
                self._send_stream_headers()
                start_stream(self.wfile, model)
                end_stream(self.wfile, 0)
            else:
                self._send_json("", model)
            return

        # ── Route by model name ───────────────────────────────────────────────
        if model in CLAUDE_REAL_MODELS:
            self._tier = "T3-Claude"
            if stream:
                self._send_stream_headers()
                route_claude_real(self.wfile, body, True, dict(self.headers))
            else:
                text = route_claude_real(self.wfile, body, False, dict(self.headers)) or ""
                self._send_json(text, model)

        elif model in GEMINI_MODELS:
            self._tier = "T2-Gemini"
            if stream:
                self._send_stream_headers()
                route_gemini(self.wfile, body, True, model)
            else:
                text = route_gemini(self.wfile, body, False, model) or ""
                self._send_json(text, model)

        else:
            # Default: ALL claude-* and anything else → T1 Qwen3 480B
            # Native Ollama Anthropic passthrough — no format conversion
            self._tier = "T1-Qwen"
            if stream:
                self._send_stream_headers()
                route_ollama_native(self.wfile, body, True)
            else:
                raw = route_ollama_native(None, body, False)
                if isinstance(raw, bytes):
                    self._send_json_bytes(raw)
                else:
                    self._send_json("", T1_LABEL)


if __name__ == "__main__":
    port = int(os.environ.get("ROUTER_PORT", 4000))
    print(f"[Claude Router Proxy] Listening on port {port}", flush=True)
    print(f"  T1 (default) → Qwen3-Coder:480B via Ollama native /v1/messages", flush=True)
    print(f"  T2 (gemini-*) → Gemini 2.5-Flash (CLI OAuth, port {GEMINI_PORT})", flush=True)
    print(f"  T3 (claude-real) → Claude CLI account login", flush=True)
    server = HTTPServer(("127.0.0.1", port), ProxyHandler)
    server.serve_forever()
