"""
LangGraph AI Team — API Cost Logger
API呼び出し後にMem0へコストログを記録するユーティリティ。
SL-132: Anthropic API + xAI API 両方に対応。
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

import httpx

MEM0_API_URL = os.getenv("MEM0_API_URL", "http://localhost:8888")
MEM0_API_KEY = os.getenv("MEM0_API_KEY", "mem0-admin-cobot-2026")
MEM0_USER_ID = os.getenv("MEM0_USER_ID", "ai-team")

# 料金表 (USD per 1M tokens)
PRICING = {
    # Anthropic
    "claude-sonnet-4-20250514": {"input": 3.0, "output": 15.0},
    "claude-haiku-4-5-20251001": {"input": 0.80, "output": 4.0},
    "claude-opus-4-20250514": {"input": 15.0, "output": 75.0},
    # xAI
    "grok-4-1-fast-reasoning": {"input": 0.20, "output": 0.50},
    "grok-4.20-multi-agent-0309": {"input": 2.0, "output": 6.0},
}


def _calc_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """トークン数からUSDコストを計算。"""
    rates = PRICING.get(model, {"input": 3.0, "output": 15.0})
    return (input_tokens * rates["input"] + output_tokens * rates["output"]) / 1_000_000


def log_api_cost(
    service: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
) -> None:
    """Mem0にAPIコストログを非同期的に記録。失敗してもエラーを握りつぶす。"""
    try:
        now = datetime.now(timezone.utc)
        date_str = now.strftime("%Y-%m-%d")
        cost_usd = _calc_cost(model, input_tokens, output_tokens)

        content = (
            f"api_cost_log {date_str}: service={service} model={model} "
            f"input={input_tokens}tok output={output_tokens}tok "
            f"cost=${cost_usd:.6f} source=langgraph"
        )

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {MEM0_API_KEY}",
        }

        payload = {
            "messages": [{"role": "user", "content": content}],
            "user_id": MEM0_USER_ID,
            "metadata": {
                "type": "api_cost_log",
                "service": service,
                "model": model,
                "date": date_str,
                "timestamp": now.isoformat(),
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cost_usd": round(cost_usd, 6),
                "source": "langgraph",
            },
        }

        with httpx.Client(timeout=10) as client:
            client.post(
                f"{MEM0_API_URL}/memories",
                headers=headers,
                json=payload,
            )
    except Exception:
        pass  # コストログ失敗でメイン処理を止めない


def log_anthropic_response(model: str, response) -> None:
    """LangChain ChatAnthropic のレスポンスからコストログを記録。"""
    usage = getattr(response, "usage_metadata", None)
    if usage:
        log_api_cost(
            service="anthropic",
            model=model,
            input_tokens=usage.get("input_tokens", 0),
            output_tokens=usage.get("output_tokens", 0),
        )


def log_xai_response(model: str, response) -> None:
    """LangChain ChatXAI のレスポンスからコストログを記録。"""
    usage = getattr(response, "usage_metadata", None)
    if usage:
        log_api_cost(
            service="xai",
            model=model,
            input_tokens=usage.get("input_tokens", 0),
            output_tokens=usage.get("output_tokens", 0),
        )
