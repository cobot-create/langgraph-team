"""
LangGraph AI Team — Shared State Definition
すべてのノード間で共有されるステートの型定義。
"""
from __future__ import annotations

import operator
from dataclasses import dataclass, field
from typing import Annotated, Any, Literal, TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph import add_messages


class TeamState(TypedDict):
    """Hierarchical Supervisor の共有ステート。"""

    # --- メッセージ履歴（LangGraph add_messages reducer で自動マージ）---
    messages: Annotated[list[BaseMessage], add_messages]

    # --- Supervisor の内部ルーティング ---
    next_node: str  # 次に実行するノード名
    task_plan: str  # Supervisor が立てた作業計画

    # --- Grok Consultant ---
    grok_query: str  # Grok に送る問い合わせ
    grok_response: str  # Grok からの回答

    # --- Mem0 ---
    mem0_query: str  # Mem0 検索クエリ
    mem0_results: list[dict[str, Any]]  # Mem0 検索結果
    mem0_write_content: str  # Mem0 に書き込む内容
    mem0_write_metadata: dict[str, Any]  # Mem0 メタデータ

    # --- Evolution Loop ---
    evolution_review: dict[str, str]  # Team Evolution Review 4視点
    template_candidates: list[str]  # テンプレート化候補
    knowledge_to_promote: list[str]  # 昇格対象の知見

    # --- セッション管理 ---
    session_id: str  # 現在のセッションID (e.g. "SL-128")
    task_status: Literal["planning", "executing", "reflecting", "completed", "error"]
    error_log: Annotated[list[str], operator.add]  # エラーログ（追記型）

    # --- Slack ---
    slack_channel: str  # 応答先チャネル
    slack_thread_ts: str  # スレッドタイムスタンプ

    # --- Agent Identity (SL-148) ---
    agent_name: str  # 呼び出し元エージェント名 ("claude" | "grok" | "")

    # --- Mem0 Cross-Scope (SL-156) ---
    mem0_cross_scope: bool  # True のとき mem0_search_node がクロススコープ検索を行う
