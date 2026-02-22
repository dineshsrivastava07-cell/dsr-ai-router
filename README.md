# DSR AI Router

3-Tier AI routing system for Claude CLI on Apple Silicon Mac.

```
T1 → Qwen3-Coder:480B  (haiku  → Ollama, local, free)
T2 → Gemini 2.5-Pro    (sonnet → Google OAuth, free)
T3 → Claude            (opus   → last resort only)
```

## Quick Start

```bash
# Start Claude CLI with routing active
claude-routed

# Standalone routed prompt
cr "your prompt"

# Interactive REPL
cr -i

# Force specific tier
cr1 "prompt"   # T1 Qwen
cr2 "prompt"   # T2 Gemini
cr3 "prompt"   # T3 Claude
```

## Architecture

```
Claude CLI
    │
    ▼
ANTHROPIC_BASE_URL=http://localhost:4000
    │
    ▼
┌─────────────────────────────────┐
│   router_proxy.py (port 4000)   │
│   Routes by model name:         │
│   haiku  → T1 Ollama/Qwen       │
│   sonnet → T2 Gemini Bridge     │
│   opus   → T3 Real Claude       │
└─────────────────────────────────┘
    │           │           │
    ▼           ▼           ▼
 Ollama     Gemini CLI   Anthropic
 :11434      OAuth        API
```

## Files

| File | Purpose |
|------|---------|
| `proxy/router_proxy.py` | Custom routing proxy (port 4000) |
| `proxy/gemini-bridge/gemini_bridge.py` | Gemini CLI bridge (port 4001) |
| `proxy/config.yaml` | LiteLLM config (reference) |
| `scripts/start-claude-routed.sh` | Launch full stack + Claude CLI |
| `scripts/stop-proxy.sh` | Stop proxy + bridge |
| `scripts/claude-wrapper.sh` | Standalone router CLI (`cr`) |
| `scripts/smart_router.py` | Smart router engine |
| `scripts/autostart.sh` | macOS login autostart |
| `scripts/dashboard.py` | Live status dashboard |
| `config.json` | Router configuration |

## Requirements

- macOS Apple Silicon (M1/M2/M4)
- Ollama + qwen3-coder:480b-cloud
- Gemini CLI (`npm install -g @google/gemini-cli`) + Google OAuth
- Claude CLI (`npm install -g @anthropic-ai/claude-code`)
- Python 3.10+

## Shell Aliases

Add to `~/.zshrc`:

```bash
alias claude-routed='~/.claude-router/scripts/start-claude-routed.sh'
alias cr='~/.claude-router/scripts/claude-wrapper.sh'
alias cr1='~/.claude-router/scripts/claude-wrapper.sh --tier1'
alias cr2='~/.claude-router/scripts/claude-wrapper.sh --tier2'
alias cr3='~/.claude-router/scripts/claude-wrapper.sh --tier3'
alias cr-i='~/.claude-router/scripts/claude-wrapper.sh -i'
alias router-status='~/.claude-router/scripts/claude-wrapper.sh --status'
alias proxy-stop='~/.claude-router/scripts/stop-proxy.sh'
```

## Install Location

All runtime files live in `~/.claude-router/` (not this repo).
The LaunchAgent `launchd/com.claude-router.autostart.plist` auto-starts on login.
