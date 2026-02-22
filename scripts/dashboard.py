#!/usr/bin/env python3
"""
Claude Router — Live Status Dashboard
Real-time monitoring of T1/T2/T3 tiers + routing history
"""

import os
import sys
import json
import time
import subprocess
from pathlib import Path
from datetime import datetime, timedelta
from collections import Counter

ROUTER_DIR = Path.home() / ".claude-router"
LOG_DIR = ROUTER_DIR / "logs"

# ── ANSI Colors ─────────────────────────────────────────────────────────────
G = "\033[32m"; R = "\033[31m"; Y = "\033[33m"; B = "\033[34m"
C = "\033[36m"; W = "\033[37m"; BOLD = "\033[1m"; DIM = "\033[2m"; RESET = "\033[0m"
BG_DARK = "\033[40m"

def clr():
    print("\033[2J\033[H", end="", flush=True)

def check_ollama() -> dict:
    try:
        r = subprocess.run(["curl", "-sf", "http://localhost:11434/api/tags"],
                           capture_output=True, timeout=3)
        if r.returncode != 0:
            return {"status": "down", "models": []}
        data = json.loads(r.stdout)
        models = [m["name"] for m in data.get("models", [])]
        qwen_models = [m for m in models if "qwen" in m.lower()]
        return {
            "status": "up",
            "models": models,
            "qwen_models": qwen_models,
            "has_480b": any("480b" in m for m in models),
            "has_qwen": len(qwen_models) > 0,
        }
    except Exception as e:
        return {"status": "error", "models": [], "error": str(e)}

def check_gemini() -> dict:
    try:
        r = subprocess.run(["which", "gemini"], capture_output=True, timeout=3)
        installed = r.returncode == 0
        if not installed:
            return {"status": "not_installed"}
        # Check if auth works
        r2 = subprocess.run(["gemini", "--version"], capture_output=True, timeout=5)
        return {
            "status": "ready" if r2.returncode == 0 else "auth_required",
            "version": r2.stdout.decode().strip() or "unknown",
        }
    except:
        return {"status": "error"}

def check_claude() -> dict:
    try:
        r = subprocess.run(["which", "claude"], capture_output=True, timeout=3)
        installed = r.returncode == 0
        api_key = bool(os.environ.get("ANTHROPIC_API_KEY", ""))
        return {
            "status": "ready" if installed else "not_installed",
            "has_api_key": api_key,
        }
    except:
        return {"status": "error"}

def get_routing_stats() -> dict:
    history_file = LOG_DIR / "routing_history.jsonl"
    if not history_file.exists():
        return {}
    try:
        entries = []
        with open(history_file) as f:
            for line in f:
                try:
                    entries.append(json.loads(line))
                except:
                    pass
        # Last 24h
        cutoff = datetime.now() - timedelta(hours=24)
        recent = [e for e in entries
                  if datetime.fromisoformat(e.get("ts", "2000-01-01")) > cutoff]
        tier_counts = Counter(e.get("tier", "?") for e in recent)
        return {
            "total_24h": len(recent),
            "by_tier": dict(tier_counts),
            "total_all_time": len(entries),
        }
    except:
        return {}

def get_ollama_running_models() -> list:
    try:
        r = subprocess.run(["ollama", "ps"], capture_output=True, text=True, timeout=5)
        lines = r.stdout.strip().split("\n")
        if len(lines) <= 1:
            return []
        return [l.split()[0] for l in lines[1:] if l.strip()]
    except:
        return []

def status_icon(status: str) -> str:
    icons = {
        "up": f"{G}●{RESET}",
        "ready": f"{G}●{RESET}",
        "down": f"{R}●{RESET}",
        "not_installed": f"{Y}○{RESET}",
        "auth_required": f"{Y}◐{RESET}",
        "error": f"{R}✕{RESET}",
    }
    return icons.get(status, f"{W}?{RESET}")

def draw_dashboard():
    clr()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    print(f"{BOLD}{C}╔══════════════════════════════════════════════════════════╗{RESET}")
    print(f"{BOLD}{C}║  🤖  CLAUDE AI ROUTER — Live Dashboard                  ║{RESET}")
    print(f"{BOLD}{C}║  Routing: T1(Qwen) → T2(Gemini) → T3(Claude) LAST       ║{RESET}")
    print(f"{BOLD}{C}╚══════════════════════════════════════════════════════════╝{RESET}")
    print(f"  {DIM}Updated: {now}{RESET}")
    print()

    # ── Tier Status ──────────────────────────────────────────────────────
    print(f"  {BOLD}TIER STATUS{RESET}")
    print(f"  {'─'*54}")

    ollama = check_ollama()
    gemini = check_gemini()
    claude_s = check_claude()
    running = get_ollama_running_models()

    # T1
    icon = status_icon("up" if ollama["status"] == "up" and ollama.get("has_qwen") else
                       "down" if ollama["status"] == "down" else "not_installed")
    qwen_info = ""
    if ollama.get("qwen_models"):
        best = next((m for m in ollama["qwen_models"] if "480b" in m), ollama["qwen_models"][0])
        qwen_info = f"{G}{best}{RESET}"
        if best in running:
            qwen_info += f" {G}[ACTIVE]{RESET}"
    else:
        qwen_info = f"{Y}(pulling... or: ollama pull qwen3-coder:480b){RESET}"

    print(f"  {icon} {BOLD}T1 PRIMARY  {RESET}│ Ollama │ {qwen_info}")

    # T2
    icon2 = status_icon(gemini.get("status", "error"))
    gem_info = {
        "ready": f"{G}Account auth OK │ gemini-2.5-pro/flash/gemini-3.0-preview{RESET}",
        "not_installed": f"{Y}npm install -g @google/gemini-cli{RESET}",
        "auth_required": f"{Y}Run: gemini auth login{RESET}",
    }.get(gemini.get("status", ""), f"{R}Error{RESET}")
    print(f"  {icon2} {BOLD}T2 FALLBACK {RESET}│ Gemini │ {gem_info}")

    # T3
    icon3 = status_icon(claude_s.get("status", "error"))
    key_status = f"{G}API key set{RESET}" if claude_s.get("has_api_key") else f"{Y}set ANTHROPIC_API_KEY{RESET}"
    claude_info = f"{key_status} │ {Y}⚠ LAST RESORT ONLY{RESET}"
    print(f"  {icon3} {BOLD}T3 LAST     {RESET}│ Claude │ {claude_info}")

    # ── Routing Stats ─────────────────────────────────────────────────────
    print()
    print(f"  {BOLD}ROUTING STATS (last 24h){RESET}")
    print(f"  {'─'*54}")
    stats = get_routing_stats()
    if stats:
        total = stats.get("total_24h", 0)
        by_tier = stats.get("by_tier", {})
        t1_n = by_tier.get("T1_OLLAMA_QWEN", 0)
        t2_n = by_tier.get("T2_GEMINI", 0)
        t3_n = by_tier.get("T3_CLAUDE_LAST_RESORT", 0)

        def bar(n, total, width=20):
            if total == 0:
                return "─" * width
            filled = int((n / total) * width)
            return f"{G}{'█' * filled}{DIM}{'░' * (width - filled)}{RESET}"

        print(f"  T1 Qwen3  {bar(t1_n, total)} {t1_n:4d} calls")
        print(f"  T2 Gemini {bar(t2_n, total)} {t2_n:4d} calls")
        print(f"  T3 Claude {bar(t3_n, total)} {t3_n:4d} calls")
        print(f"             {'─'*20} {total:4d} total")
        print(f"  All-time: {stats.get('total_all_time', 0)} routed tasks")
    else:
        print(f"  {DIM}No routing history yet — run: cr \"hello world\"{RESET}")

    # ── Available Gemini Models ───────────────────────────────────────────
    print()
    print(f"  {BOLD}GEMINI MODELS (via account auth){RESET}")
    print(f"  {'─'*54}")
    gemini_models = [
        "gemini-2.5-pro     (best reasoning)",
        "gemini-2.5-flash   (fast + efficient)",
        "gemini-2.0-pro     (stable)",
        "gemini-2.0-flash   (ultra fast)",
        "gemini-3.0-preview (latest preview ⭐)",
    ]
    for m in gemini_models:
        print(f"  {G}•{RESET} {m}")

    # ── Quick Commands ────────────────────────────────────────────────────
    print()
    print(f"  {BOLD}QUICK COMMANDS{RESET}")
    print(f"  {'─'*54}")
    print(f"  {C}cr \"prompt\"{RESET}           → auto-route T1→T2→T3")
    print(f"  {C}cr -i{RESET}                → interactive REPL")
    print(f"  {C}cr1 \"prompt\"{RESET}         → force Qwen3-480B")
    print(f"  {C}cr2 \"prompt\"{RESET}         → force Gemini")
    print(f"  {C}cr-gemini3 \"prompt\"{RESET}  → use Gemini 3 Preview")
    print(f"  {C}ollama ps{RESET}             → running models")

    print()
    print(f"  {DIM}Press Ctrl+C to exit │ Refreshes every 5s{RESET}")

def main():
    if "--once" in sys.argv:
        draw_dashboard()
        return

    try:
        while True:
            draw_dashboard()
            time.sleep(5)
    except KeyboardInterrupt:
        clr()
        print("  Dashboard closed.")

if __name__ == "__main__":
    main()
