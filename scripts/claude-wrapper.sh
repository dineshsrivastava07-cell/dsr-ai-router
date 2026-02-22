#!/usr/bin/env bash
# ╔══════════════════════════════════════════════════════════════╗
# ║    CLAUDE CLI WRAPPER — Enforces Routing Mandate             ║
# ║    Every session → Qwen first, Gemini second, Claude LAST   ║
# ╚══════════════════════════════════════════════════════════════╝
#
# Drop-in Claude CLI wrapper. Intercepts ALL claude invocations.
# Mandates: try Qwen3→Gemini before hitting Claude API.
#
# Install: alias claude="~/.claude-router/claude-wrapper.sh"
#          OR symlink /usr/local/bin/cr → this script

set -euo pipefail

ROUTER_DIR="$HOME/.claude-router"
ROUTER_PY="$ROUTER_DIR/scripts/smart_router.py"
LOG_FILE="$ROUTER_DIR/logs/wrapper_$(date +%Y%m%d).log"
PYTHON="${ROUTER_DIR}/venv/bin/python3"

# Fall back to system python if venv not ready
[[ -f "$PYTHON" ]] || PYTHON=$(which python3)

# ── Banner ────────────────────────────────────────────────────────────────
_banner() {
  cat <<'EOF'
  ╭─────────────────────────────────────────────────────────╮
  │  🤖  CLAUDE AI ROUTER  │  Apple Silicon Optimized        │
  │  T1: Qwen3-Coder-480B (Ollama)  — PRIMARY              │
  │  T2: Gemini (All Models, Account Auth) — FALLBACK       │
  │  T3: Claude (Explicit Auth) — LAST RESORT ONLY          │
  ╰─────────────────────────────────────────────────────────╯
EOF
}

# ── Ensure Ollama is Running ──────────────────────────────────────────────
_ensure_ollama() {
  if ! curl -sf http://localhost:11434/api/tags >/dev/null 2>&1; then
    echo "  ⚡ Starting Ollama server..."
    nohup ollama serve >"$ROUTER_DIR/logs/ollama.log" 2>&1 &
    local retries=0
    while ! curl -sf http://localhost:11434/api/tags >/dev/null 2>&1; do
      sleep 1
      ((retries++))
      [[ $retries -gt 15 ]] && { echo "  ⚠️ Ollama start timeout"; return 1; }
    done
    echo "  ✅ Ollama ready"
  fi

  # Ensure qwen3-coder is available (pull if missing)
  local model_list
  model_list=$(curl -sf http://localhost:11434/api/tags | python3 -c \
    "import sys,json; [print(m['name']) for m in json.load(sys.stdin).get('models',[])]" 2>/dev/null || echo "")

  # 16GB RAM — use qwen2.5-coder:7b (4.7 GB, already installed)
  if ! echo "$model_list" | grep -q "qwen2.5-coder"; then
    echo "  📥 Pulling qwen2.5-coder:7b..."
    ollama pull qwen2.5-coder:7b &
    echo "  ℹ️  Pull started in background (PID: $!)"
  fi
}

# ── Ensure Gemini CLI Auth ────────────────────────────────────────────────
_check_gemini_auth() {
  if ! which gemini >/dev/null 2>&1; then
    echo "  ℹ️  Gemini CLI not found. Install: npm install -g @google/gemini-cli"
    return 1
  fi

  # Quick auth check (non-destructive)
  if ! gemini --version >/dev/null 2>&1; then
    echo "  ⚠️  Gemini CLI auth needed: run 'gemini auth login'"
    return 1
  fi
  return 0
}

# ── Mode: Interactive Router REPL ─────────────────────────────────────────
_interactive() {
  _banner
  _ensure_ollama || true
  _check_gemini_auth || true
  echo ""
  exec "$PYTHON" "$ROUTER_PY" --interactive
}

# ── Mode: Single Prompt ───────────────────────────────────────────────────
_single_prompt() {
  local prompt="$1"
  _ensure_ollama || true
  exec "$PYTHON" "$ROUTER_PY" "$prompt"
}

# ── Mode: Pass-through to actual claude CLI ────────────────────────────────
_use_real_claude() {
  echo "  🟠 [Direct Claude] — Bypassing router (explicit --claude flag)"
  local real_claude
  real_claude=$(which claude 2>/dev/null || echo "")
  if [[ -z "$real_claude" ]]; then
    echo "  ❌ claude CLI not found. Install: npm install -g @anthropic-ai/claude-cli"
    exit 1
  fi
  exec "$real_claude" "$@"
}

# ── Mode: Status Dashboard ────────────────────────────────────────────────
_status() {
  _banner
  echo ""
  "$PYTHON" "$ROUTER_PY" --status

  echo "  System Info:"
  echo "  ─────────────────────────────────────────────"
  echo "  Chip     : $(sysctl -n machdep.cpu.brand_string 2>/dev/null || echo 'Apple Silicon')"
  echo "  Memory   : $(sysctl -n hw.memsize 2>/dev/null | awk '{printf "%.0fGB\n", $1/1073741824}')"
  echo "  Ollama   : $(curl -sf http://localhost:11434/api/tags >/dev/null 2>&1 && echo '✅ Running' || echo '❌ Stopped')"
  echo "  Gemini   : $(which gemini >/dev/null 2>&1 && echo '✅ Installed' || echo '❌ Not found')"
  echo "  Claude   : $(which claude >/dev/null 2>&1 && echo '✅ Installed' || echo '❌ Not found')"
  echo "  API Key  : $([[ -n "${ANTHROPIC_API_KEY:-}" ]] && echo '✅ Set' || echo '⚠️  Not set')"
  echo ""
}

# ── Auto-Startup Check ────────────────────────────────────────────────────
_startup_check() {
  # Called on every session start
  local startup_done="$ROUTER_DIR/.startup_$(date +%Y%m%d)"
  if [[ ! -f "$startup_done" ]]; then
    echo "  🚀 Daily startup check..."
    _ensure_ollama || true
    _check_gemini_auth || true
    touch "$startup_done"
    # Cleanup old startup markers
    find "$ROUTER_DIR" -name ".startup_*" -mtime +3 -delete 2>/dev/null || true
  fi
}

# ── Main Entry ────────────────────────────────────────────────────────────
main() {
  mkdir -p "$ROUTER_DIR/logs"

  # Parse special flags first
  case "${1:-}" in
    --status|-s)
      _status
      exit 0
      ;;
    --interactive|-i)
      _startup_check
      _interactive
      ;;
    --claude|--force-claude)
      shift
      _use_real_claude "$@"
      ;;
    --help|-h)
      _banner
      cat <<'HELP'

  USAGE: cr [OPTIONS] [PROMPT]
         claude [OPTIONS] [PROMPT]   (if aliased)

  OPTIONS:
    (none)              Interactive router mode
    "your prompt"       Route single prompt T1→T2→T3
    --tier1, -1         Force Qwen3 (Ollama)
    --tier2, -2         Force Gemini CLI
    --tier3, -3         Force Claude (last resort)
    --gemini-model M    Use specific Gemini model
    --claude            Bypass router, use real claude CLI
    --status, -s        Show system status dashboard
    --interactive, -i   REPL mode with commands
    --help, -h          This help

  GEMINI MODELS:
    gemini-2.5-pro       gemini-2.5-flash
    gemini-2.0-pro       gemini-2.0-flash
    gemini-3.0-preview   (use --gemini-model gemini-3.0-preview)

  EXAMPLES:
    cr "write a FastAPI endpoint for user auth"
    cr --tier2 "analyze this architecture"
    cr --gemini-model gemini-3.0-preview "complex reasoning task"
    cr -1 "debug this Python code"
    cr --status

HELP
      exit 0
      ;;
    --tier1|-1)
      shift
      _startup_check
      _ensure_ollama || true
      "$PYTHON" "$ROUTER_PY" --tier 1 "$@"
      ;;
    --tier2|-2)
      shift
      _startup_check
      "$PYTHON" "$ROUTER_PY" --tier 2 "$@"
      ;;
    --tier3|-3)
      shift
      _startup_check
      "$PYTHON" "$ROUTER_PY" --tier 3 "$@"
      ;;
    --gemini-model)
      local gmodel="${2:-gemini-2.5-pro}"
      shift 2
      _startup_check
      "$PYTHON" "$ROUTER_PY" --tier 2 --gemini-model "$gmodel" "$@"
      ;;
    "")
      # No args → interactive mode
      _startup_check
      _interactive
      ;;
    *)
      # Treat as prompt
      _startup_check
      _single_prompt "$*"
      ;;
  esac
}

main "$@"
