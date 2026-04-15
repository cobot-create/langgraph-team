from __future__ import annotations
import json, logging, os, re, subprocess
from datetime import datetime
import requests

logger = logging.getLogger("executor")

MEM0_API_URL = os.getenv("MEM0_API_URL", "https://mem0.element-laboratory.com")
MEM0_API_KEY = os.getenv("MEM0_API_KEY", "mem0-admin-cobot-2026")
MEM0_USER_ID = os.getenv("MEM0_USER_ID", "ai-team")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

BLOCKED_PATTERNS = [
    r"rm\s+-rf\s+[/~]",
    r"DROP\s+TABLE",
    r"mkfs\.",
    r"dd\s+if=",
    r"chmod\s+777\s+/",
]

DEFAULT_ALLOWED = [
    "systemctl", "pip", "pip3", "python3", "python",
    "curl", "git", "cat", "ls", "ps", "echo", "grep",
    "mkdir", "cp", "mv", "head", "tail", "sleep",
    "crontab", "chmod", "chown", "env", "which", "sudo",
]


def get_project_config(project_id: str) -> dict:
    default = {
        "project_id": project_id or "default",
        "langgraph": True,
        "auto_execute": False,
        "safe_mode": True,
        "allowed_commands": DEFAULT_ALLOWED,
    }
    if not project_id:
        return default
    try:
        # 改善: project_id完全一致 + メモリテキスト含む検索
        for query in [f"project_config {project_id}", f"{project_id} langgraph auto_execute"]:
            resp = requests.post(
                f"{MEM0_API_URL}/search",
                headers={"Content-Type": "application/json",
                         "Authorization": f"Bearer {MEM0_API_KEY}"},
                json={"query": query,
                      "user_id": MEM0_USER_ID, "limit": 8},
                timeout=10,
            )
            for r in resp.json().get("results", []):
                meta = r.get("metadata", {})
                mem_text = r.get("memory", "")
                # metadata.project_idによるマッチング
                if meta.get("type") == "project_config":
                    pid = meta.get("project_id", "")
                    if pid == project_id or pid == "default":
                        logger.info(f"project_config found: pid={pid} query={query}")
                        return {
                            "project_id": pid,
                            "langgraph": meta.get("langgraph", True),
                            "auto_execute": meta.get("auto_execute", False),
                            "safe_mode": meta.get("safe_mode", True),
                            "allowed_commands": meta.get("allowed_commands", DEFAULT_ALLOWED),
                        }
                    # フォールバック: メモリテキストにproject_idが含まれる場合
                    if project_id in mem_text and meta.get("type") == "project_config":
                        logger.info(f"project_config found by text: {mem_text[:60]}")
                        return {
                            "project_id": project_id,
                            "langgraph": meta.get("langgraph", True),
                            "auto_execute": meta.get("auto_execute", False),
                            "safe_mode": meta.get("safe_mode", True),
                            "allowed_commands": meta.get("allowed_commands", DEFAULT_ALLOWED),
                        }
    except Exception as e:
        logger.warning(f"get_project_config error: {e}")
    logger.info(f"project_config not found for {project_id}, using default")
    return default


def resolve_execution_mode(config: dict) -> str:
    lg = config.get("langgraph", True)
    ae = config.get("auto_execute", False)
    if lg and not ae:   return "A"
    if lg and ae:       return "B"
    if not lg and ae:   return "C"
    return "D"


def _is_safe(command: str, config: dict) -> bool:
    for pattern in BLOCKED_PATTERNS:
        if re.search(pattern, command, re.IGNORECASE):
            logger.warning(f"BLOCKED: {command}")
            return False
    if not config.get("safe_mode", True):
        return True
    first = os.path.basename(command.strip().split()[0]) if command.strip() else ""
    return first in config.get("allowed_commands", DEFAULT_ALLOWED)


def execute_instruction(instruction: str, config: dict) -> str:
    if not ANTHROPIC_API_KEY:
        return "ANTHROPIC_API_KEY が未設定です"
    import anthropic
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    system = (
        "You are Claude Code on Ubuntu 24.04. "
        "Given an instruction, respond ONLY with JSON: "
        '{"commands": ["cmd1", "cmd2"], "description": "brief summary"}. '
        "Use minimal safe commands. No markdown, just JSON."
    )
    try:
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=512,
            system=system,
            messages=[{"role": "user", "content": instruction}],
        )
        raw = resp.content[0].text.strip()
        # markdownコードフェンス除去
        fence = chr(96) * 3
        if raw.startswith(fence):
            parts = raw.split(chr(10))
            raw = chr(10).join(parts[1:-1]) if parts[-1].strip() == fence else chr(10).join(parts[1:])
        parsed = json.loads(raw.strip())
        commands = parsed.get("commands", [])
        description = parsed.get("description", "")
    except Exception as e:
        logger.error(f"Anthropic API error: {e}")
        return f"コマンド生成エラー: {e}"
    if not commands:
        return "実行コマンドなし"
    lines = [f"[{description}]"] if description else []
    blocked = []
    for cmd in commands:
        if not _is_safe(cmd, config):
            blocked.append(cmd)
            continue
        try:
            r = subprocess.run(
                cmd, shell=True, capture_output=True, text=True,
                timeout=30, cwd=os.path.expanduser("~/langgraph-team"),
            )
            out = (r.stdout.strip() or r.stderr.strip() or "(no output)")[:500]
            status = "OK" if r.returncode == 0 else "WARN"
            lines.append(f"[{status}] `{cmd}`\n{out}")
        except subprocess.TimeoutExpired:
            lines.append(f"[TIMEOUT] `{cmd}`")
        except Exception as e:
            lines.append(f"[ERROR] `{cmd}`: {e}")
    if blocked:
        lines.append(f"[BLOCKED by safe_mode]: {', '.join(blocked)}")
    return "\n\n".join(lines)
