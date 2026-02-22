#!/usr/bin/env python3
"""
Claude AI Router Proxy — Lightweight custom proxy
Replaces LiteLLM. Routes by model name, handles Anthropic SSE natively.

  haiku  → Ollama/Qwen3-Coder:480B  (T1, local)
  sonnet → Gemini CLI bridge         (T2, Google OAuth)
  opus   → Real Anthropic API        (T3, last resort)
"""

import os, json, time, uuid, subprocess, urllib.request, urllib.error
from http.server import HTTPServer, BaseHTTPRequestHandler

OLLAMA_HOST  = "http://localhost:11434"
GEMINI_PORT  = 4001
T1_MODEL     = "qwen3-coder:480b-cloud"
T2_MODEL     = "gemini-2.5-pro"
T3_MODEL     = "claude-sonnet-4-6"

HAIKU_MODELS  = {"claude-haiku-4-5-20251001", "claude-3-haiku-20240307"}
SONNET_MODELS = {"claude-sonnet-4-6", "claude-3-5-sonnet-20241022"}
# anything else (opus) → T3 real Claude


# ── SSE helpers ─────────────────────────────────────────────────────────────
def sse(event, data):
    return f"event: {event}\ndata: {json.dumps(data)}\n\n".encode()

def start_stream(wfile, model):
    msg_id = f"msg_{uuid.uuid4().hex[:24]}"
    wfile.write(sse("message_start", {
        "type": "message_start",
        "message": {"id": msg_id, "type": "message", "role": "assistant",
                    "content": [], "model": model, "stop_reason": None,
                    "usage": {"input_tokens": 10, "output_tokens": 0,
                              "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}}
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


# ── Extract messages ──────────────────────────────────────────────────────────
def extract_messages(body):
    messages = body.get("messages", [])
    system   = body.get("system", "")
    return messages, system

def messages_to_prompt(messages, system=""):
    parts = []
    if system:
        parts.append(f"[System: {system}]")
    for m in messages:
        role    = m.get("role", "user")
        content = m.get("content", "")
        if isinstance(content, list):
            content = " ".join(c.get("text","") for c in content if isinstance(c,dict))
        parts.append(f"{role}: {content}" if role != "user" else content)
    return "\n".join(parts)


# ── T1: Ollama streaming ──────────────────────────────────────────────────────
def route_ollama(wfile, body, stream, model_label):
    messages, system = extract_messages(body)
    prompt = messages_to_prompt(messages, system)

    payload = json.dumps({
        "model": T1_MODEL,
        "prompt": prompt,
        "system": system or "You are a helpful AI assistant.",
        "stream": True,
        "options": {"temperature": 0.1, "num_ctx": 32768}
    }).encode()

    req = urllib.request.Request(
        f"{OLLAMA_HOST}/api/generate",
        data=payload,
        headers={"Content-Type": "application/json"}
    )

    if stream:
        start_stream(wfile, model_label)
        token_count = 0
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                for line in resp:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                        chunk = data.get("response", "")
                        if chunk:
                            send_chunk(wfile, chunk)
                            token_count += 1
                        if data.get("done"):
                            break
                    except json.JSONDecodeError:
                        continue
        except Exception as e:
            send_chunk(wfile, f"\n[Qwen error: {e}]")
        end_stream(wfile, token_count)
    else:
        full_payload = json.loads(payload)
        full_payload["stream"] = False
        req2 = urllib.request.Request(
            f"{OLLAMA_HOST}/api/generate",
            data=json.dumps(full_payload).encode(),
            headers={"Content-Type": "application/json"}
        )
        try:
            with urllib.request.urlopen(req2, timeout=120) as resp:
                data = json.loads(resp.read())
                return data.get("response", "").strip()
        except Exception as e:
            return f"Qwen error: {e}"


# ── T2: Gemini CLI ─────────────────────────────────────────────────────────────
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
    messages, system = extract_messages(body)
    prompt = messages_to_prompt(messages, system)

    text = call_gemini_cli(prompt)

    if stream:
        start_stream(wfile, model_label)
        # send in chunks for natural streaming feel
        chunk_size = 30
        for i in range(0, len(text), chunk_size):
            send_chunk(wfile, text[i:i+chunk_size])
        end_stream(wfile, len(text.split()))
    else:
        return text


# ── T3: Real Claude passthrough ───────────────────────────────────────────────
def route_claude_real(wfile, body, stream, headers_in):
    api_key = os.environ.get("ANTHROPIC_API_KEY_REAL", "")
    if not api_key:
        # Fall back to no key — let Claude CLI handle auth via its own token
        # This path shouldn't normally be hit since Claude CLI has account auth
        text = "[T3 Claude: Set ANTHROPIC_API_KEY_REAL for direct API fallback]"
        if stream:
            start_stream(wfile, T3_MODEL)
            send_chunk(wfile, text)
            end_stream(wfile, 10)
        return text

    body["model"] = T3_MODEL
    payload = json.dumps(body).encode()
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "anthropic-beta": "messages-2023-12-15"
        }
    )
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            data = resp.read()
            wfile.write(data)
            wfile.flush()
    except Exception as e:
        text = f"[Claude API error: {e}]"
        if stream:
            start_stream(wfile, T3_MODEL)
            send_chunk(wfile, text)
            end_stream(wfile, 5)


# ── HTTP Handler ──────────────────────────────────────────────────────────────
class ProxyHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        model = getattr(self, "_model", "?")
        tier  = getattr(self, "_tier", "?")
        print(f"[Router] {tier} | {model} | {self.command} {self.path} | {args[1]}", flush=True)

    def do_GET(self):
        if self.path in ("/health", "/"):
            body = json.dumps({
                "status": "ok",
                "tiers": {
                    "T1": f"Qwen3-Coder:480B (haiku→{OLLAMA_HOST})",
                    "T2": f"Gemini 2.5-Pro (sonnet→port {GEMINI_PORT})",
                    "T3": "Claude (opus→Anthropic)"
                }
            }).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def _send_stream_headers(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()

    def _send_json(self, text, model):
        payload = non_stream_response(model, text)
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body   = json.loads(self.rfile.read(length) or b"{}")

        model  = body.get("model", "claude-sonnet-4-6")
        stream = body.get("stream", False)
        has_tools = bool(body.get("tools"))

        self._model = model

        # ── Route by model name ──────────────────────────────────────────────
        if model in HAIKU_MODELS and not has_tools:
            self._tier = "T1-Qwen"
            if stream:
                self._send_stream_headers()
                route_ollama(self.wfile, body, True, model)
            else:
                text = route_ollama(self.wfile, body, False, model) or ""
                self._send_json(text, model)

        elif model in SONNET_MODELS and not has_tools:
            self._tier = "T2-Gemini"
            if stream:
                self._send_stream_headers()
                route_gemini(self.wfile, body, True, model)
            else:
                text = route_gemini(self.wfile, body, False, model) or ""
                self._send_json(text, model)

        else:
            # opus / tool-use → T3 real Claude
            self._tier = "T3-Claude"
            if stream:
                self._send_stream_headers()
                route_claude_real(self.wfile, body, True, dict(self.headers))
            else:
                text = route_claude_real(self.wfile, body, False, dict(self.headers)) or ""
                self._send_json(text, model)


if __name__ == "__main__":
    port = int(os.environ.get("ROUTER_PORT", 4000))
    print(f"[Claude Router Proxy] Listening on port {port}", flush=True)
    print(f"  T1 haiku  → Qwen3-Coder:480B (Ollama)", flush=True)
    print(f"  T2 sonnet → Gemini 2.5-Pro (CLI OAuth)", flush=True)
    print(f"  T3 opus   → Real Claude (Anthropic)", flush=True)
    server = HTTPServer(("127.0.0.1", port), ProxyHandler)
    server.serve_forever()
