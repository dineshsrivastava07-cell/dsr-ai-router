#!/usr/bin/env bash
# ╔══════════════════════════════════════════════════════════════╗
# ║  Claude CLI — Full 3-Tier Routing                            ║
# ║  T1: Qwen3-Coder:480B (Ollama native Anthropic API)         ║
# ║  T2: Gemini CLI (Google OAuth)   → gemini-* model names     ║
# ║  T3: Claude (account auth)       → claude-real/opus names   ║
# ╚══════════════════════════════════════════════════════════════╝

ROUTER_DIR="$HOME/.claude-router"
PROXY_PORT=4000
BRIDGE_PORT=4001

# ── Services are managed by launchd (KeepAlive) — just verify ────────────────
_is_up() {
  curl -sf --max-time 3 "http://127.0.0.1:$1/health"   >/dev/null 2>&1 && return 0
  curl -sf --max-time 3 "http://127.0.0.1:$1/api/tags" >/dev/null 2>&1 && return 0
  return 1
}

_ensure() {
  local port=$1 label=$2 name=$3
  if _is_up $port; then
    echo "  ✅ $name running (port $port)"
    return 0
  fi
  echo "  ⚡ $name not responding — kicking launchd..."
  if [[ -n "$label" ]]; then
    launchctl kickstart -k "gui/$(id -u)/$label" 2>/dev/null || \
    launchctl start "$label" 2>/dev/null || true
    sleep 4
  fi
  _is_up $port \
    && echo "  ✅ $name ready" \
    || echo "  ⚠️  $name still starting (check: ai-services logs)"
}

# ── Ollama: always on 127.0.0.1:11434 ────────────────────────────────────────
if ! _is_up 11434; then
  echo "  ⚡ Starting Ollama server..."
  nohup ollama serve >"$ROUTER_DIR/logs/ollama.log" 2>&1 &
  for i in {1..20}; do
    sleep 1
    _is_up 11434 && break
  done
  _is_up 11434 && echo "  ✅ Ollama ready" || echo "  ⚠️  Ollama slow to start"
else
  echo "  ✅ Ollama running"
fi

_ensure $BRIDGE_PORT "com.claude-router.gemini-bridge" "Gemini bridge"
_ensure $PROXY_PORT  "com.claude-router.proxy"         "Router proxy"

# ── Banner ────────────────────────────────────────────────────────────────────
echo ""
echo "  ╭──────────────────────────────────────────────────────╮"
echo "  │  🤖  Claude CLI  +  3-Tier AI Router                 │"
echo "  │                                                       │"
echo "  │  T1 → Qwen3-Coder:480B  (default — Ollama native)   │"
echo "  │  T2 → Gemini 2.5-Flash  (gemini-* → Google OAuth)   │"
echo "  │  T3 → Claude Pro        (claude-real → last resort)  │"
echo "  ╰──────────────────────────────────────────────────────╯"
echo ""

# ── Launch Claude CLI via proxy ───────────────────────────────────────────────
# ANTHROPIC_BASE_URL → router proxy (port 4000), which:
#   • Routes all claude-* requests natively through Ollama /v1/messages (T1)
#   • Routes gemini-* to Gemini bridge (T2)
#   • Routes claude-real/opus to Claude CLI account (T3)
#
# CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1 prevents Claude Code from making
# extra startup calls to api.anthropic.com (model list fetch, telemetry, etc.)
# which would hang or fail when ANTHROPIC_BASE_URL points to a local proxy.
#
# No ANTHROPIC_API_KEY — use OAuth session (Claude Pro subscription).
export ANTHROPIC_BASE_URL="http://127.0.0.1:$PROXY_PORT"
export GOOGLE_GENAI_USE_GCA=true
export CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1
unset ANTHROPIC_API_KEY

exec claude "$@"
