#!/usr/bin/env bash
# ╔══════════════════════════════════════════════════════════════╗
# ║  Claude CLI — Full 3-Tier Routing                            ║
# ║  T1: Qwen3-Coder:480B (Ollama)  → haiku model names         ║
# ║  T2: Gemini CLI (Google OAuth)   → sonnet model names        ║
# ║  T3: Claude (account auth)       → opus model names          ║
# ╚══════════════════════════════════════════════════════════════╝

ROUTER_DIR="$HOME/.claude-router"
PROXY_PORT=4000
BRIDGE_PORT=4001
PROXY_PID="$ROUTER_DIR/proxy/proxy.pid"
BRIDGE_PID="$ROUTER_DIR/proxy/bridge.pid"
PROXY_LOG="$ROUTER_DIR/logs/proxy.log"
BRIDGE_LOG="$ROUTER_DIR/logs/gemini-bridge.log"
PYTHON="$ROUTER_DIR/venv/bin/python3"

# ── 1. Ensure Ollama is running ───────────────────────────────
if ! curl -sf http://localhost:11434/api/tags >/dev/null 2>&1; then
  echo "  ⚡ Starting Ollama..."
  nohup ollama serve >"$ROUTER_DIR/logs/ollama.log" 2>&1 &
  for i in {1..15}; do
    sleep 1
    curl -sf http://localhost:11434/api/tags >/dev/null 2>&1 && break
  done
  echo "  ✅ Ollama ready"
else
  echo "  ✅ Ollama running"
fi

# ── 2. Start Gemini bridge (T2, port 4001) ────────────────────
if ! curl -sf http://localhost:$BRIDGE_PORT/health >/dev/null 2>&1; then
  echo "  ⚡ Starting Gemini bridge (T2)..."
  GOOGLE_GENAI_USE_GCA=true nohup "$PYTHON" \
    "$ROUTER_DIR/proxy/gemini-bridge/gemini_bridge.py" \
    >"$BRIDGE_LOG" 2>&1 &
  echo $! > "$BRIDGE_PID"
  sleep 2
  curl -sf http://localhost:$BRIDGE_PORT/health >/dev/null 2>&1 \
    && echo "  ✅ Gemini bridge ready" \
    || echo "  ⚠️  Gemini bridge slow — check $BRIDGE_LOG"
else
  echo "  ✅ Gemini bridge running"
fi

# ── 3. Start custom router proxy (port 4000) ─────────────────
if ! curl -sf http://localhost:$PROXY_PORT/health >/dev/null 2>&1; then
  echo "  ⚡ Starting routing proxy (port $PROXY_PORT)..."
  GOOGLE_GENAI_USE_GCA=true nohup "$PYTHON" \
    "$ROUTER_DIR/proxy/router_proxy.py" \
    >"$PROXY_LOG" 2>&1 &
  echo $! > "$PROXY_PID"
  for i in {1..10}; do
    sleep 1
    curl -sf http://localhost:$PROXY_PORT/health >/dev/null 2>&1 && break
  done
  echo "  ✅ Proxy ready (PID: $(cat $PROXY_PID))"
else
  echo "  ✅ Proxy running"
fi

# ── Banner ────────────────────────────────────────────────────
echo ""
echo "  ╭──────────────────────────────────────────────────────╮"
echo "  │  🤖  Claude CLI  +  3-Tier AI Router                 │"
echo "  │                                                       │"
echo "  │  T1 → Qwen3-Coder:480B  (haiku  → Ollama local)     │"
echo "  │  T2 → Gemini 2.5-Pro    (sonnet → Google OAuth)      │"
echo "  │  T3 → Claude            (opus   → last resort)       │"
echo "  ╰──────────────────────────────────────────────────────╯"
echo ""

# ── Launch Claude CLI via proxy ───────────────────────────────
export ANTHROPIC_BASE_URL="http://localhost:$PROXY_PORT"
export GOOGLE_GENAI_USE_GCA=true
unset ANTHROPIC_API_KEY

exec claude "$@"
