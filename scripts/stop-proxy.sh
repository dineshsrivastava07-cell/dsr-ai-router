#!/usr/bin/env bash
ROUTER_DIR="$HOME/.claude-router"

# Stop LiteLLM proxy
if [[ -f "$ROUTER_DIR/proxy/proxy.pid" ]]; then
  kill "$(cat $ROUTER_DIR/proxy/proxy.pid)" 2>/dev/null && echo "  ✅ Proxy stopped"
  rm -f "$ROUTER_DIR/proxy/proxy.pid"
fi

# Stop Gemini bridge
if [[ -f "$ROUTER_DIR/proxy/bridge.pid" ]]; then
  kill "$(cat $ROUTER_DIR/proxy/bridge.pid)" 2>/dev/null && echo "  ✅ Gemini bridge stopped"
  rm -f "$ROUTER_DIR/proxy/bridge.pid"
fi

pkill -f "litellm.*claude-router" 2>/dev/null || true
pkill -f "gemini_bridge.py" 2>/dev/null || true
echo "  Done"
