"""
LangGraph AI Team — Mem0 Tool Integration
Mem0 セルフホスト (http://localhost:8888) への検索・書き込みツール。
Graph Memory 対応。
"""
from __future__ import annotations

import os
from typing import Any

import httpx
from langchain_core.tools import tool

MEM0_API_URL = os.getenv("MEM0_API_URL", "http://localhost:8888")
MEM0_API_KEY = os.getenv("MEM0_API_KEY", "mem0-admin-cobot-2026")
MEM0_USER_ID = os.getenv("MEM0_USER_ID", "ai-team")

_headers = {
    "Content-Type": "application/json",
    "Authorization": f"Bearer {MEM0_API_KEY}",
}


@tool
def mem0_search(query: str, limit: int = 10, user_id: str = "") -> list[dict[str, Any]]:
    """Mem0から知見・引き継ぎ・方針を検索する。

    Args:
        query: 検索クエリ（日本語OK）
        limit: 最大件数（デフォルト10）
        user_id: 検索対象ユーザーID（省略時は環境変数 MEM0_USER_ID を使用）

    Returns:
        検索結果のリスト。各要素は memory, metadata, score を含む。
    """
    resolved_user_id = user_id if user_id else MEM0_USER_ID
    with httpx.Client(timeout=30) as client:
        resp = client.post(
            f"{MEM0_API_URL}/search",
            headers=_headers,
            json={
                "query": query,
                "user_id": resolved_user_id,
                "limit": limit,
            },
        )
        resp.raise_for_status()
        data = resp.json()

    results = data.get("results", [])
    return [
        {
            "memory": r.get("memory", ""),
            "metadata": r.get("metadata", {}),
            "score": r.get("score", 0),
            "id": r.get("id", ""),
            "created_at": r.get("created_at", ""),
        }
        for r in results
    ]


@tool
def mem0_write(
    content: str,
    metadata_type: str = "knowledge",
    session_id: str = "",
    date: str = "",
    user_id: str = "",
) -> dict[str, Any]:
    """Mem0に新しい知見・引き継ぎ・方針を書き込む。

    Args:
        content: 書き込む内容
        metadata_type: knowledge / decision / session_handoff / team_evolution / template_candidate
        session_id: セッションID (e.g. "SL-128")
        date: 日付 (e.g. "2026-04-08")
        user_id: 書き込み先ユーザーID（省略時は環境変数 MEM0_USER_ID を使用）

    Returns:
        書き込み結果。
    """
    resolved_user_id = user_id if user_id else MEM0_USER_ID
    metadata = {"type": metadata_type}
    if session_id:
        metadata["session_id"] = session_id
    if date:
        metadata["date"] = date

    with httpx.Client(timeout=30) as client:
        resp = client.post(
            f"{MEM0_API_URL}/memories",
            headers=_headers,
            json={
                "messages": [{"role": "assistant", "content": content}],
                "user_id": resolved_user_id,
                "metadata": metadata,
            },
        )
        resp.raise_for_status()
        return resp.json()


@tool
def mem0_get_all(limit: int = 50) -> list[dict[str, Any]]:
    """Mem0から全メモリを取得する（最新順）。

    Args:
        limit: 最大件数

    Returns:
        メモリのリスト。
    """
    with httpx.Client(timeout=30) as client:
        resp = client.get(
            f"{MEM0_API_URL}/memories",
            headers=_headers,
            params={"user_id": MEM0_USER_ID, "limit": limit},
        )
        resp.raise_for_status()
        data = resp.json()

    results = data.get("results", [])
    return [
        {
            "memory": r.get("memory", ""),
            "metadata": r.get("metadata", {}),
            "id": r.get("id", ""),
            "created_at": r.get("created_at", ""),
        }
        for r in results
    ]


@tool
def mem0_health_check() -> dict[str, Any]:
    """Mem0サーバーのヘルスチェック。"""
    with httpx.Client(timeout=10) as client:
        resp = client.get(f"{MEM0_API_URL}/health", headers=_headers)
        resp.raise_for_status()
        return resp.json()


# ============================================================
# SL-156: Cross-Scope Search (Grok ↔ Claude 相互参照)
# ============================================================

def _search_one_scope(
    client: httpx.Client, query: str, user_id: str, limit: int
) -> list[dict[str, Any]]:
    """単一スコープのMem0検索（内部ヘルパー）。"""
    try:
        resp = client.post(
            f"{MEM0_API_URL}/search",
            headers=_headers,
            json={"query": query, "user_id": user_id, "limit": limit},
        )
        resp.raise_for_status()
        return [
            {
                "memory": r.get("memory", ""),
                "metadata": r.get("metadata", {}),
                "score": r.get("score", 0),
                "id": r.get("id", ""),
                "created_at": r.get("created_at", ""),
                "scope": user_id,
            }
            for r in resp.json().get("results", [])
        ]
    except Exception as e:
        return [{
            "memory": f"[ERROR scope={user_id}] {e}",
            "metadata": {},
            "score": 0,
            "id": "",
            "created_at": "",
            "scope": user_id,
        }]


@tool
def mem0_search_cross_scope(
    query: str,
    limit: int = 10,
) -> "list[dict[str, Any]]": 
    """GrokとClaudeのMem0スコープを横断して知見を検索する（クロスリファレンス）。

    ai-team（共有）・ai-team-claude・ai-team-grok の全スコープを検索し、
    スコア降順でマージした結果を返す。重複IDは除去。

    Args:
        query: 検索クエリ（日本語OK）
        limit: 各スコープごとの最大件数（デフォルト10）

    Returns:
        統合検索結果のリスト。各要素に scope フィールドを付加。
    """
    scopes = ["ai-team", "ai-team-claude", "ai-team-grok"]
    seen_ids: set = set()
    combined: list = []

    with httpx.Client(timeout=30) as client:
        for scope in scopes:
            for item in _search_one_scope(client, query, scope, limit):
                rid = item.get("id", "")
                if rid and rid in seen_ids:
                    continue
                if rid:
                    seen_ids.add(rid)
                combined.append(item)

    combined.sort(key=lambda x: x["score"], reverse=True)
    return combined
