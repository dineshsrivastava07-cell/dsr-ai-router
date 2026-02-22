#!/usr/bin/env python3
"""
Gemini CLI Bridge — T2 Tier
Accepts OpenAI format requests → calls Gemini CLI (Google OAuth) → returns SSE streaming response.
Runs on port 4001.
"""

import os
import json
import subprocess
import time
import uuid
from http.server import HTTPServer, BaseHTTPRequestHandler

GEMINI_MODELS = {
    "gemini-2.5-pro":         "gemini-2.5-pro",
    "gemini-2.5-flash":       "gemini-2.5-flash",
    "gemini-2.0-flash":       "gemini-2.0-flash",
    "gemini-3-flash-preview":  "gemini-3-flash-preview",
    "gemini-3-pro-preview":    "gemini-3-pro-preview",
    "gemini-proxy":            "gemini-2.5-pro",
}
DEFAULT_MODEL = "gemini-2.5-pro"


def call_gemini(prompt: str, model: str = DEFAULT_MODEL) -> str:
    env = os.environ.copy()
    env["GOOGLE_GENAI_USE_GCA"] = "true"
    gemini_model = GEMINI_MODELS.get(model, DEFAULT_MODEL)
    for attempt_model in [gemini_model, DEFAULT_MODEL]:
        try:
            result = subprocess.run(
                ["gemini", "--model", attempt_model, "-p", prompt],
                capture_output=True, text=True, timeout=90, env=env
            )
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip()
        except subprocess.TimeoutExpired:
            return "Gemini timeout"
        except Exception as e:
            return f"Gemini error: {e}"
    return "Gemini: no response"


def extract_prompt(body: dict) -> str:
    messages = body.get("messages", [])
    if messages:
        parts = []
        for m in messages:
            role = m.get("role", "user")
            content = m.get("content", "")
            if isinstance(content, list):
                content = " ".join(
                    c.get("text", "") for c in content if isinstance(c, dict)
                )
            if role == "system":
                parts.insert(0, f"[System: {content}]")
            else:
                parts.append(content)
        return "\n".join(parts)
    return body.get("prompt", "")


def stream_openai_response(wfile, text: str, model: str):
    """Write OpenAI-compatible SSE streaming response (LiteLLM converts this to Anthropic format)."""
    chat_id = f"chatcmpl-{uuid.uuid4().hex[:20]}"
    created = int(time.time())

    # role chunk
    chunk = {
        "id": chat_id, "object": "chat.completion.chunk",
        "created": created, "model": model,
        "choices": [{"index": 0, "delta": {"role": "assistant", "content": ""}, "finish_reason": None}]
    }
    wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
    wfile.flush()

    # content chunks
    chunk_size = 20
    for i in range(0, len(text), chunk_size):
        piece = text[i:i + chunk_size]
        chunk = {
            "id": chat_id, "object": "chat.completion.chunk",
            "created": created, "model": model,
            "choices": [{"index": 0, "delta": {"content": piece}, "finish_reason": None}]
        }
        wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
        wfile.flush()

    # final stop chunk
    chunk = {
        "id": chat_id, "object": "chat.completion.chunk",
        "created": created, "model": model,
        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]
    }
    wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
    wfile.write(b"data: [DONE]\n\n")
    wfile.flush()


class BridgeHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def do_GET(self):
        if self.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"status": "ok", "bridge": "gemini"}).encode())
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length) or b"{}")

        model = body.get("model", DEFAULT_MODEL)
        stream = body.get("stream", False)
        prompt = extract_prompt(body)

        print(f"[Gemini Bridge] model={model} stream={stream} len={len(prompt)}", flush=True)
        text = call_gemini(prompt, model)

        if stream:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            stream_openai_response(self.wfile, text, model)
        else:
            resp = {
                "id": f"gemini-{int(time.time())}",
                "object": "chat.completion",
                "created": int(time.time()),
                "model": model,
                "choices": [{
                    "index": 0,
                    "message": {"role": "assistant", "content": text},
                    "finish_reason": "stop"
                }],
                "usage": {"prompt_tokens": 10, "completion_tokens": len(text.split()), "total_tokens": 0}
            }
            payload = json.dumps(resp).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)


if __name__ == "__main__":
    port = int(os.environ.get("GEMINI_BRIDGE_PORT", 4001))
    print(f"[Gemini Bridge] Starting on port {port} (Google OAuth, SSE streaming enabled)", flush=True)
    server = HTTPServer(("127.0.0.1", port), BridgeHandler)
    server.serve_forever()
