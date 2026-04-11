"""
LangGraph AI Team — Grok Consultant Agent Tool
xAI API 経由で Grok にコンサルティング問い合わせを行うツール。
"""
from __future__ import annotations

import os
from typing import Any

from langchain_core.tools import tool
from langchain_xai import ChatXAI
from cost_logger import log_xai_response


def _get_grok_model() -> ChatXAI:
    """Grok モデルインスタンスを取得。"""
    return ChatXAI(
        model=os.getenv("GROK_MODEL", "grok-3-fast"),
        api_key=os.getenv("XAI_API_KEY", ""),
        temperature=0.7,
        max_tokens=4096,
    )


@tool
def grok_consult(
    query: str,
    context: str = "",
    role: str = "ビジネスコンサルタント兼マーケティング専門家",
) -> str:
    """Grok Consultant Agent にコンサルティング問い合わせを行う。

    Args:
        query: Grok に聞きたい質問・相談内容
        context: 追加のコンテキスト情報（任意）
        role: Grok に期待する役割（デフォルト: ビジネスコンサルタント）

    Returns:
        Grok からの回答テキスト。
    """
    model = _get_grok_model()

    system_prompt = f"""あなたは{role}です。
以下のルールに従って回答してください：
- 具体的で実行可能なアドバイスを提供する
- データや根拠に基づいた提案をする
- リスクや注意点も明記する
- 日本語で回答する
"""
    messages = [
        {"role": "system", "content": system_prompt},
    ]

    if context:
        messages.append(
            {"role": "user", "content": f"【コンテキスト】\n{context}"}
        )

    messages.append({"role": "user", "content": query})

    response = model.invoke(messages)
    log_xai_response(model.model_name, response)
    return response.content


@tool
def grok_cross_check(
    claim: str,
    source_context: str = "",
) -> str:
    """Grok でクロスチェック（ファクトチェック）を行う。

    Claude の出力や外部情報の正確性を Grok に検証させる。

    Args:
        claim: 検証したい主張・情報
        source_context: 元の情報源やコンテキスト（任意）

    Returns:
        Grok からのクロスチェック結果。
    """
    model = _get_grok_model()

    system_prompt = """あなたはファクトチェック専門家です。
与えられた主張を客観的に検証し、以下の形式で回答してください：
1. 【判定】正確 / 一部不正確 / 不正確 / 検証不可
2. 【根拠】判定の理由
3. 【補足】追加で知っておくべき情報
"""
    user_content = f"【検証対象の主張】\n{claim}"
    if source_context:
        user_content += f"\n\n【情報源コンテキスト】\n{source_context}"

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content},
    ]

    response = model.invoke(messages)
    log_xai_response(model.model_name, response)
    return response.content


@tool
def grok_trend_analysis(topic: str) -> str:
    """Grok でトレンド分析を行う。

    Args:
        topic: 分析したいトピック

    Returns:
        Grok からのトレンド分析結果。
    """
    model = _get_grok_model()

    system_prompt = """あなたはトレンドアナリストです。
与えられたトピックについて、最新のトレンドと今後の見通しを分析してください。
以下の観点を含めて回答：
1. 現在の市場動向
2. 注目すべき変化やシグナル
3. 短期（1-3ヶ月）の予測
4. 具体的なアクション提案
"""
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"トピック: {topic}"},
    ]

    response = model.invoke(messages)
    log_xai_response(model.model_name, response)
    return response.content
