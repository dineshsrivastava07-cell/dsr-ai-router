#!/usr/bin/env bash
# ╔══════════════════════════════════════════════════════════════╗
# ║  CLAUDE ROUTER AUTOSTART — Runs Every Mac Login             ║
# ║  Mandate: Qwen3 + Gemini ALWAYS ready before Claude         ║
# ╚══════════════════════════════════════════════════════════════╝

set -euo pipefail

ROUTER_DIR="$HOME/.claude-router"
LOG="$ROUTER_DIR/logs/autostart_$(date +%Y%m%d_%H%M%S).log"

mkdir -p "$ROUTER_DIR/logs"

exec >> "$LOG" 2>&1
echo "═══════════════════════════════════════"
echo "  Claude Router Autostart — $(date)"
echo "═══════════════════════════════════════"

# ── Load environment ──────────────────────────────────────────────────────
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"

# Source shell rc files for env vars (API keys etc)
[[ -f "$HOME/.zshrc" ]]  && source "$HOME/.zshrc"  2>/dev/null || true
[[ -f "$HOME/.bashrc" ]] && source "$HOME/.bashrc" 2>/dev/null || true
[[ -f "$HOME/.profile" ]] && source "$HOME/.profile" 2>/dev/null || true
[[ -f "$HOME/.claude-router/.env" ]] && source "$HOME/.claude-router/.env" || true

# ── Step 1: Start Ollama Server ───────────────────────────────────────────
echo ""
echo "  [1/4] Ollama Server..."

if command -v ollama >/dev/null 2>&1; then
  if ! curl -sf http://localhost:11434/api/tags >/dev/null 2>&1; then
    echo "  → Starting Ollama..."
    nohup ollama serve > "$ROUTER_DIR/logs/ollama_server.log" 2>&1 &
    OLLAMA_PID=$!
    echo "  → Ollama PID: $OLLAMA_PID"

    # Wait for ready
    for i in {1..30}; do
      sleep 1
      curl -sf http://localhost:11434/api/tags >/dev/null 2>&1 && break
    done
    echo "  ✅ Ollama started"
  else
    echo "  ✅ Ollama already running"
  fi
else
  echo "  ⚠️  Ollama not installed — run: brew install ollama"
fi

# ── Step 2: Ensure Qwen3-Coder-480B Available ────────────────────────────
echo ""
echo "  [2/4] Qwen2.5-Coder-7B model (16GB RAM optimised)..."

if curl -sf http://localhost:11434/api/tags >/dev/null 2>&1; then
  MODELS=$(curl -sf http://localhost:11434/api/tags | \
    python3 -c "import sys,json; [print(m['name']) for m in json.load(sys.stdin).get('models',[])]" 2>/dev/null || echo "")

  if echo "$MODELS" | grep -q "qwen2.5-coder"; then
    QWEN_MODEL=$(echo "$MODELS" | grep "qwen2.5-coder" | head -1)
    echo "  ✅ $QWEN_MODEL available"
  else
    echo "  → qwen2.5-coder:7b not found, pulling..."
    nohup ollama pull qwen2.5-coder:7b > "$ROUTER_DIR/logs/pull_qwen7b.log" 2>&1 &
    echo "  ℹ️  Pulling in background — check: tail -f $ROUTER_DIR/logs/pull_qwen7b.log"
  fi
fi

# ── Step 3: Check Gemini CLI ──────────────────────────────────────────────
echo ""
echo "  [3/4] Gemini CLI status..."

if command -v gemini >/dev/null 2>&1; then
  GEMINI_VER=$(gemini --version 2>/dev/null || echo "unknown")
  echo "  ✅ Gemini CLI installed (v$GEMINI_VER)"
  echo "     Auth method: Google Account (OAuth)"
  echo "     Available models: gemini-2.5-pro, gemini-2.5-flash, gemini-3.0-preview"
else
  echo "  ⚠️  Gemini CLI not installed"
  echo "     Install: npm install -g @google/gemini-cli"
  echo "     Auth:    gemini auth login"

  # Try npm install if npm available
  if command -v npm >/dev/null 2>&1; then
    echo "  → Auto-installing Gemini CLI..."
    npm install -g @google/gemini-cli 2>/dev/null && echo "  ✅ Installed" || true
  fi
fi

# ── Step 4: Claude CLI Status (Last Resort) ───────────────────────────────
echo ""
echo "  [4/4] Claude CLI status (last resort tier)..."

if command -v claude >/dev/null 2>&1; then
  CLAUDE_VER=$(claude --version 2>/dev/null || echo "installed")
  echo "  ✅ Claude CLI: $CLAUDE_VER"
  echo "     API Key: $([[ -n "${ANTHROPIC_API_KEY:-}" ]] && echo 'SET ✅' || echo 'NOT SET ⚠️')"
  echo "     MANDATE: Used ONLY when T1+T2 both fail"
else
  echo "  ⚠️  Claude CLI not installed"
  echo "     Install: npm install -g @anthropic-ai/claude-cli"
fi

# ── Summary ───────────────────────────────────────────────────────────────
echo ""
echo "═══════════════════════════════════════"
echo "  Routing Priority Status:"
echo "  T1 Qwen3-480B  : $(curl -sf http://localhost:11434/api/tags >/dev/null 2>&1 && echo '✅ READY' || echo '❌ UNAVAILABLE')"
echo "  T2 Gemini CLI  : $(command -v gemini >/dev/null 2>&1 && echo '✅ READY' || echo '❌ UNAVAILABLE')"
echo "  T3 Claude      : $(command -v claude >/dev/null 2>&1 && echo '✅ STANDBY (last resort)' || echo '⚠️ NOT INSTALLED')"
echo "═══════════════════════════════════════"
echo ""
echo "  Autostart complete — $(date)"
echo ""

# ── Update shell aliases (idempotent) ─────────────────────────────────────
ALIAS_LINE="alias cr='$ROUTER_DIR/scripts/claude-wrapper.sh'"
ZSHRC="$HOME/.zshrc"

if ! grep -qF "claude-wrapper" "$ZSHRC" 2>/dev/null; then
  echo "" >> "$ZSHRC"
  echo "# Claude AI Router aliases" >> "$ZSHRC"
  echo "$ALIAS_LINE" >> "$ZSHRC"
  echo "alias router-status='$ROUTER_DIR/scripts/claude-wrapper.sh --status'" >> "$ZSHRC"
  echo "  → Added aliases to ~/.zshrc"
fi

echo "  Done! Use: cr \"your prompt\" or cr -i (interactive)"
