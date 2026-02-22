#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════╗
║         CLAUDE AI SMART ROUTER - M1/M4 Mac Suite        ║
║  T1→Qwen3-Coder-480B │ T2→Gemini │ T3→Claude (Last)     ║
╚══════════════════════════════════════════════════════════╝

Routing Priority:
  Tier 1 (PRIMARY)  : Ollama → qwen3-coder:480b  [LOCAL, zero cost]
  Tier 2 (FALLBACK) : Gemini CLI → account auth   [All models incl. Gemini 3]
  Tier 3 (LAST)     : Claude CLI → explicit API   [Last resort only]
"""

import os
import sys
import json
import time
import subprocess
import argparse
import logging
import re
from pathlib import Path
from datetime import datetime
from typing import Optional, Tuple

# ── Config ─────────────────────────────────────────────────────────────────
BASE_DIR     = Path.home() / ".claude-router"
LOG_DIR      = BASE_DIR / "logs"
CONFIG_FILE  = BASE_DIR / "config.json"
SESSION_FILE = BASE_DIR / "session.json"

LOG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / f"router_{datetime.now():%Y%m%d}.log"),
        logging.StreamHandler(sys.stderr),
    ],
)
log = logging.getLogger("router")

# ── Default Config ──────────────────────────────────────────────────────────
DEFAULT_CONFIG = {
    "routing": {
        "always_try_t1_first": True,
        "t1_timeout_seconds": 120,
        "t2_timeout_seconds": 90,
        "t3_timeout_seconds": 180,
        "max_retries_per_tier": 2,
        "escalate_on_failure": True,
        "claude_is_last_resort": True,
    },
    "tier1_ollama": {
        "model": "qwen3-coder:480b",
        "host": "http://localhost:11434",
        "fallback_models": ["qwen3-coder:32b", "qwen2.5-coder:32b", "qwen3:30b"],
        "context_length": 32768,
        "temperature": 0.1,
    },
    "tier2_gemini": {
        "default_model": "gemini-2.5-pro",
        "models": [
            "gemini-2.5-pro",
            "gemini-2.5-flash",
            "gemini-2.0-flash",
            "gemini-2.0-pro",
            "gemini-3.0-preview",
        ],
        "auth_method": "account",  # NOT api key — uses gemini CLI OAuth
        "cli_command": "gemini",
    },
    "tier3_claude": {
        "use_only_as_last_resort": True,
        "require_explicit_flag": False,  # set True to require --use-claude
        "models": [
            "claude-sonnet-4-5",
            "claude-opus-4",
            "claude-haiku-4-5",
        ],
        "default_model": "claude-sonnet-4-5",
        "api_key_env": "ANTHROPIC_API_KEY",
    },
    "task_routing": {
        "coding_keywords": [
            "code", "function", "debug", "fix", "implement", "script",
            "python", "javascript", "typescript", "bash", "rust", "go",
            "class", "algorithm", "api", "refactor", "test", "unittest",
        ],
        "analysis_keywords": [
            "analyze", "explain", "review", "compare", "evaluate",
            "architecture", "design", "plan", "strategy",
        ],
        "creative_keywords": [
            "write", "draft", "creative", "story", "blog", "essay",
            "poem", "content", "generate",
        ],
        "complex_keywords": [
            "complex", "advanced", "difficult", "sophisticated",
            "enterprise", "production", "optimize",
        ],
    },
}


def load_config() -> dict:
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE) as f:
                saved = json.load(f)
            # Deep merge with defaults
            merged = DEFAULT_CONFIG.copy()
            for k, v in saved.items():
                if k in merged and isinstance(merged[k], dict):
                    merged[k].update(v)
                else:
                    merged[k] = v
            return merged
        except Exception as e:
            log.warning(f"Config load error: {e}, using defaults")
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_FILE, "w") as f:
        json.dump(DEFAULT_CONFIG, f, indent=2)
    return DEFAULT_CONFIG


CFG = load_config()


# ── Tier 1: Ollama / Qwen3-Coder-480B ──────────────────────────────────────
class OllamaTier:
    def __init__(self):
        self.cfg = CFG["tier1_ollama"]
        self.host = self.cfg["host"]
        self.model = self.cfg["model"]

    def is_available(self) -> bool:
        try:
            result = subprocess.run(
                ["curl", "-sf", f"{self.host}/api/tags"],
                capture_output=True, timeout=5
            )
            if result.returncode != 0:
                return False
            data = json.loads(result.stdout)
            models = [m["name"] for m in data.get("models", [])]
            # Check primary or any fallback
            primary = self.model.split(":")[0]
            return any(primary in m for m in models)
        except Exception as e:
            log.debug(f"Ollama check failed: {e}")
            return False

    def get_available_model(self) -> Optional[str]:
        try:
            result = subprocess.run(
                ["curl", "-sf", f"{self.host}/api/tags"],
                capture_output=True, timeout=5
            )
            data = json.loads(result.stdout)
            models = [m["name"] for m in data.get("models", [])]
            # Try primary first
            for candidate in [self.model] + self.cfg["fallback_models"]:
                base = candidate.split(":")[0]
                matches = [m for m in models if base in m]
                if matches:
                    return matches[0]
            return None
        except:
            return None

    def run(self, prompt: str, system: str = "", stream: bool = True) -> Tuple[bool, str]:
        model = self.get_available_model()
        if not model:
            # Try to pull qwen3-coder
            log.info(f"Pulling {self.model} from Ollama registry...")
            subprocess.Popen(
                ["ollama", "pull", self.model],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
            return False, "Model not available, pull initiated"

        log.info(f"[T1 OLLAMA] Using model: {model}")

        payload = {
            "model": model,
            "prompt": prompt,
            "system": system or "You are an expert AI assistant. Be precise, thorough, and helpful.",
            "stream": stream,
            "options": {
                "temperature": self.cfg.get("temperature", 0.1),
                "num_ctx": self.cfg.get("context_length", 32768),
            },
        }

        try:
            if stream:
                # Stream via subprocess for real-time output
                cmd = [
                    "curl", "-sf", "-X", "POST",
                    f"{self.host}/api/generate",
                    "-H", "Content-Type: application/json",
                    "-d", json.dumps(payload),
                ]
                process = subprocess.Popen(
                    cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
                )

                output_parts = []
                print(f"\n🟢 [Qwen3-Coder-480B via Ollama]\n{'─'*50}", flush=True)

                for line in process.stdout:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                        chunk = data.get("response", "")
                        if chunk:
                            print(chunk, end="", flush=True)
                            output_parts.append(chunk)
                        if data.get("done"):
                            break
                    except json.JSONDecodeError:
                        continue

                print(f"\n{'─'*50}", flush=True)
                process.wait(timeout=CFG["routing"]["t1_timeout_seconds"])

                full_output = "".join(output_parts)
                if process.returncode == 0 and full_output.strip():
                    return True, full_output
                return False, "Empty or failed response"

            else:
                payload["stream"] = False
                result = subprocess.run(
                    ["curl", "-sf", "-X", "POST",
                     f"{self.host}/api/generate",
                     "-H", "Content-Type: application/json",
                     "-d", json.dumps(payload)],
                    capture_output=True, text=True,
                    timeout=CFG["routing"]["t1_timeout_seconds"]
                )
                data = json.loads(result.stdout)
                output = data.get("response", "").strip()
                if output:
                    return True, output
                return False, "Empty response"

        except subprocess.TimeoutExpired:
            log.warning("[T1] Timeout — escalating to T2")
            return False, "Timeout"
        except Exception as e:
            log.error(f"[T1] Error: {e}")
            return False, str(e)


# ── Tier 2: Gemini CLI (Account Auth, No API Key) ──────────────────────────
class GeminiTier:
    def __init__(self):
        self.cfg = CFG["tier2_gemini"]
        self.cli = self.cfg["cli_command"]

    def is_available(self) -> bool:
        try:
            # Check gemini CLI is installed
            result = subprocess.run(
                ["which", self.cli], capture_output=True, timeout=5
            )
            if result.returncode != 0:
                # Try npx gemini or node-based
                result2 = subprocess.run(
                    ["which", "npx"], capture_output=True, timeout=5
                )
                return result2.returncode == 0
            return True
        except:
            return False

    def _build_cmd(self, prompt: str, model: str) -> list:
        """Build gemini CLI command using account auth (OAuth, not API key)"""
        base_cmd = self.cli

        # Google's official Gemini CLI uses account-based auth
        # gemini --model gemini-2.5-pro "prompt"
        cmd = [base_cmd, "--model", model, prompt]

        # Alternative if using npx
        if subprocess.run(["which", self.cli], capture_output=True).returncode != 0:
            cmd = ["npx", "-y", "@google/gemini-cli", "--model", model, prompt]

        return cmd

    def run(self, prompt: str, model: Optional[str] = None,
            stream: bool = True) -> Tuple[bool, str]:
        model = model or self.cfg["default_model"]
        log.info(f"[T2 GEMINI] Using model: {model}")

        print(f"\n🔵 [Gemini {model} — Account Auth]\n{'─'*50}", flush=True)

        for attempt_model in [model] + [m for m in self.cfg["models"] if m != model]:
            try:
                cmd = self._build_cmd(prompt, attempt_model)
                log.debug(f"Gemini cmd: {' '.join(cmd[:3])}...")

                process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )

                output, errors = process.communicate(
                    timeout=CFG["routing"]["t2_timeout_seconds"]
                )

                if process.returncode == 0 and output.strip():
                    print(output, flush=True)
                    print(f"{'─'*50}", flush=True)
                    return True, output.strip()

                if "auth" in errors.lower() or "login" in errors.lower():
                    log.error("[T2] Auth error — run: gemini auth login")
                    print("⚠️  Gemini auth required: run `gemini auth login`")
                    return False, "Auth required"

                log.warning(f"[T2] Model {attempt_model} failed, trying next...")

            except subprocess.TimeoutExpired:
                log.warning(f"[T2] {attempt_model} timed out")
                continue
            except Exception as e:
                log.error(f"[T2] Error with {attempt_model}: {e}")
                continue

        return False, "All Gemini models failed"


# ── Tier 3: Claude CLI (Explicit Auth — LAST RESORT) ───────────────────────
class ClaudeTier:
    def __init__(self):
        self.cfg = CFG["tier3_claude"]
        self.api_key = os.environ.get(self.cfg["api_key_env"], "")

    def is_available(self) -> bool:
        # Uses Claude CLI account auth (OAuth) — no API key required
        try:
            result = subprocess.run(["which", "claude"], capture_output=True, timeout=5)
            return result.returncode == 0
        except:
            return False

    def run(self, prompt: str, model: Optional[str] = None,
            stream: bool = True) -> Tuple[bool, str]:
        if self.cfg["use_only_as_last_resort"]:
            log.warning("[T3 CLAUDE] ⚠️  Using Claude as LAST RESORT tier")
            print(f"\n🟠 [CLAUDE — Last Resort Tier]\n{'─'*50}", flush=True)
            print("⚠️  T1 (Qwen) and T2 (Gemini) unavailable — escalating to Claude", flush=True)

        model = model or self.cfg["default_model"]

        try:
            cmd = [
                "claude",
                "--model", model,
                "--print",
            ]

            if stream:
                cmd.append("--output-format")
                cmd.append("text")

            env = os.environ.copy()
            if self.api_key:
                env["ANTHROPIC_API_KEY"] = self.api_key

            process = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=env,
            )

            output, errors = process.communicate(
                input=prompt,
                timeout=CFG["routing"]["t3_timeout_seconds"]
            )

            if process.returncode == 0 and output.strip():
                print(output, flush=True)
                print(f"{'─'*50}", flush=True)
                return True, output.strip()

            log.error(f"[T3] Claude failed: {errors[:200]}")
            return False, errors

        except subprocess.TimeoutExpired:
            return False, "Claude timeout"
        except Exception as e:
            log.error(f"[T3] Error: {e}")
            return False, str(e)


# ── Task Analyzer ───────────────────────────────────────────────────────────
def analyze_task(prompt: str) -> dict:
    """Analyze task to determine routing hints."""
    prompt_lower = prompt.lower()
    routing_cfg = CFG["task_routing"]

    scores = {
        "coding": sum(1 for k in routing_cfg["coding_keywords"] if k in prompt_lower),
        "analysis": sum(1 for k in routing_cfg["analysis_keywords"] if k in prompt_lower),
        "creative": sum(1 for k in routing_cfg["creative_keywords"] if k in prompt_lower),
        "complex": sum(1 for k in routing_cfg["complex_keywords"] if k in prompt_lower),
    }

    dominant = max(scores, key=scores.get) if max(scores.values()) > 0 else "general"
    word_count = len(prompt.split())

    return {
        "type": dominant,
        "scores": scores,
        "word_count": word_count,
        "is_complex": scores["complex"] > 0 or word_count > 500,
        "prefers_local": scores["coding"] > 0,  # Coding → prefer Qwen first
    }


# ── Main Router ─────────────────────────────────────────────────────────────
class SmartRouter:
    def __init__(self):
        self.t1 = OllamaTier()
        self.t2 = GeminiTier()
        self.t3 = ClaudeTier()
        self._log_session_start()

    def _log_session_start(self):
        session = {
            "started": datetime.now().isoformat(),
            "t1_available": self.t1.is_available(),
            "t2_available": self.t2.is_available(),
            "t3_available": self.t3.is_available(),
        }
        with open(SESSION_FILE, "w") as f:
            json.dump(session, f, indent=2)
        log.info(f"Session: T1={'✓' if session['t1_available'] else '✗'} "
                 f"T2={'✓' if session['t2_available'] else '✗'} "
                 f"T3={'✓' if session['t3_available'] else '✗'}")

    def route(self, prompt: str, force_tier: Optional[int] = None,
              gemini_model: Optional[str] = None) -> str:
        task = analyze_task(prompt)
        log.info(f"Task type: {task['type']}, words: {task['word_count']}")

        routing_cfg = CFG["routing"]

        # Force tier override
        if force_tier == 1:
            ok, out = self.t1.run(prompt)
            return out if ok else "T1 failed"
        elif force_tier == 2:
            ok, out = self.t2.run(prompt, model=gemini_model)
            return out if ok else "T2 failed"
        elif force_tier == 3:
            ok, out = self.t3.run(prompt)
            return out if ok else "T3 failed"

        # ── Smart routing: ALWAYS try T1 first ──────────────────────────────
        print(f"\n{'═'*55}", flush=True)
        print(f"  🧠 CLAUDE ROUTER  │  Task: {task['type'].upper()}", flush=True)
        print(f"  Priority: T1(Qwen) → T2(Gemini) → T3(Claude)", flush=True)
        print(f"{'═'*55}\n", flush=True)

        # TIER 1: Ollama / Qwen3-Coder-480B
        if routing_cfg["always_try_t1_first"] and self.t1.is_available():
            log.info("Routing to T1: Ollama/Qwen3-Coder-480B")
            for attempt in range(routing_cfg["max_retries_per_tier"]):
                ok, output = self.t1.run(prompt)
                if ok and output.strip():
                    self._log_routing("T1_OLLAMA_QWEN", task, True)
                    return output
                log.warning(f"T1 attempt {attempt+1} failed")

        # TIER 2: Gemini CLI (account auth)
        if self.t2.is_available():
            log.info("Escalating to T2: Gemini CLI")
            for attempt in range(routing_cfg["max_retries_per_tier"]):
                ok, output = self.t2.run(prompt, model=gemini_model)
                if ok and output.strip():
                    self._log_routing("T2_GEMINI", task, True)
                    return output
                log.warning(f"T2 attempt {attempt+1} failed")

        # TIER 3: Claude (LAST RESORT — explicit API auth)
        if routing_cfg["escalate_on_failure"]:
            log.warning("🚨 Escalating to T3: Claude (LAST RESORT)")
            ok, output = self.t3.run(prompt)
            if ok:
                self._log_routing("T3_CLAUDE_LAST_RESORT", task, True)
                return output

        return "❌ All tiers failed. Check: ollama serve | gemini auth login | ANTHROPIC_API_KEY"

    def _log_routing(self, tier: str, task: dict, success: bool):
        entry = {
            "ts": datetime.now().isoformat(),
            "tier": tier,
            "task_type": task["type"],
            "success": success,
        }
        log_file = LOG_DIR / "routing_history.jsonl"
        with open(log_file, "a") as f:
            f.write(json.dumps(entry) + "\n")

    def status(self):
        print(f"\n{'═'*55}")
        print("  CLAUDE AI ROUTER — System Status")
        print(f"{'─'*55}")
        t1_model = self.t1.get_available_model()
        t1_ok = self.t1.is_available()
        t2_ok = self.t2.is_available()
        t3_ok = self.t3.is_available()

        print(f"  T1 Ollama/Qwen  : {'✅' if t1_ok else '❌'} "
              f"{'→ ' + t1_model if t1_model else '(run: ollama serve)'}")
        print(f"  T2 Gemini CLI   : {'✅' if t2_ok else '❌'} "
              f"{'→ account auth' if t2_ok else '(run: gemini auth login)'}")
        print(f"  T3 Claude (last): {'✅' if t3_ok else '❌'} "
              f"{'→ ' + self.t3.cfg['default_model'] + ' (account auth)' if t3_ok else '(claude CLI not found)'}")
        print(f"{'═'*55}\n")


# ── CLI Entry Point ─────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="Claude AI Smart Router — T1:Qwen → T2:Gemini → T3:Claude"
    )
    parser.add_argument("prompt", nargs="?", help="Task/prompt to route")
    parser.add_argument("--tier", type=int, choices=[1, 2, 3],
                        help="Force specific tier (1=Qwen, 2=Gemini, 3=Claude)")
    parser.add_argument("--gemini-model", default=None,
                        help="Specific Gemini model (e.g. gemini-3.0-preview)")
    parser.add_argument("--status", action="store_true", help="Show tier status")
    parser.add_argument("--interactive", "-i", action="store_true",
                        help="Interactive REPL mode")
    args = parser.parse_args()

    router = SmartRouter()

    if args.status:
        router.status()
        return

    if args.interactive:
        print("\n🤖 Claude AI Router — Interactive Mode")
        print("   T1: Qwen3-Coder-480B | T2: Gemini | T3: Claude (last)")
        print("   Commands: /status /tier1 /tier2 /tier3 /gemini-model <m> /quit\n")
        current_tier = None
        gemini_model = None

        while True:
            try:
                prompt = input("You> ").strip()
                if not prompt:
                    continue
                if prompt in ("/quit", "/exit", "quit", "exit"):
                    break
                elif prompt == "/status":
                    router.status()
                    continue
                elif prompt.startswith("/tier"):
                    try:
                        current_tier = int(prompt[5:])
                        print(f"  → Forcing T{current_tier}")
                    except:
                        current_tier = None
                    continue
                elif prompt.startswith("/gemini-model "):
                    gemini_model = prompt.split(" ", 1)[1]
                    print(f"  → Gemini model set: {gemini_model}")
                    continue
                elif prompt == "/reset":
                    current_tier = None
                    gemini_model = None
                    print("  → Routing reset to auto")
                    continue

                router.route(prompt, force_tier=current_tier,
                             gemini_model=gemini_model)

            except (KeyboardInterrupt, EOFError):
                print("\n  Goodbye!")
                break
        return

    if args.prompt:
        router.route(args.prompt, force_tier=args.tier,
                     gemini_model=args.gemini_model)
    else:
        # Read from stdin (pipe mode)
        if not sys.stdin.isatty():
            prompt = sys.stdin.read().strip()
            if prompt:
                router.route(prompt, force_tier=args.tier,
                             gemini_model=args.gemini_model)
        else:
            parser.print_help()


if __name__ == "__main__":
    main()
