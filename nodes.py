"""
LangGraph AI Team — Node Definitions
Supervisor・Grok・Mem0・Reflection・Evolution の各ノード。
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from state import TeamState
from tools_grok import grok_consult, grok_cross_check, grok_trend_analysis
from tools_mem0 import mem0_get_all, mem0_health_check, mem0_search, mem0_search_cross_scope, mem0_write

# ============================================================
# Constants
# ============================================================
SUPERVISOR_MODEL = os.getenv("SUPERVISOR_MODEL", "claude-sonnet-4-20250514")
SESSION_ID = os.getenv("SESSION_ID", "SL-128")
TODAY = datetime.now().strftime("%Y-%m-%d")

# SL-148: agent_name → Mem0 user_id マッピング
AGENT_USER_ID: dict[str, str] = {
    "claude": "ai-team-claude",
    "grok":   "ai-team-grok",
}


def get_user_id(agent_name: str) -> str:
    """agent_name に応じた Mem0 user_id を返す。未指定時は共有プール "ai-team"。"""
    return AGENT_USER_ID.get(agent_name, "ai-team")

# ============================================================
# Supervisor Node — 最上位ノード（計画・ルーティング）
# ============================================================

SUPERVISOR_SYSTEM_PROMPT = """あなたはClaude COO — Cobo AIチームの最高オーケストレーション責任者です。

## あなたの役割
- ユーザー（CEO Cobo）からの指示を受け取り、作業計画を立てる
- 適切なサブエージェント/ツールにタスクを振り分ける
- 結果を統合し、最終回答を返す

## 利用可能なノード
- **grok_node**: Grok Consultant Agent（コンサル・クロスチェック・トレンド分析）
- **mem0_search_node**: Mem0から知見・引き継ぎを検索
- **mem0_write_node**: Mem0に新しい知見を書き込み
- **reflection_node**: 作業結果の振り返り・品質チェック
- **evolution_node**: Team Evolution Review（4視点振り返り）
- **respond_node**: 最終回答をまとめてユーザーに返す

## ルーティングルール
1. まずタスクを分析し、task_plan を設定
2. next_node に次に実行するノード名を設定
3. 情報収集が必要 → mem0_search_node
4. 外部コンサルが必要 → grok_node
5. 結果をMem0に保存 → mem0_write_node
6. 品質チェック → reflection_node
7. セッション終了時 → evolution_node
8. 最終回答 → respond_node

必ず next_node を以下のいずれかに設定してください:
grok_node, mem0_search_node, mem0_write_node, reflection_node, evolution_node, respond_node
"""

VALID_NODES = {
    "grok_node",
    "mem0_search_node",
    "mem0_write_node",
    "reflection_node",
    "evolution_node",
    "respond_node",
}


def supervisor_node(state: TeamState) -> dict[str, Any]:
    """Supervisor: 計画策定とルーティング。"""
    model = ChatAnthropic(
        model=SUPERVISOR_MODEL,
        max_tokens=2048,
        temperature=0.3,
    )

    messages = [SystemMessage(content=SUPERVISOR_SYSTEM_PROMPT)] + state["messages"]

    # Supervisorに現在のstateサマリーを追加
    state_summary = f"""
【現在のステート】
- session_id: {state.get('session_id', SESSION_ID)}
- task_status: {state.get('task_status', 'planning')}
- grok_response: {'あり' if state.get('grok_response') else 'なし'}
- mem0_results: {len(state.get('mem0_results', []))}件
- error_log: {len(state.get('error_log', []))}件

次に実行するノードを決定してください。
回答は以下のJSON形式で返してください:
{{"next_node": "ノード名", "task_plan": "計画の説明", "grok_query": "Grokへのクエリ（必要な場合）", "mem0_query": "Mem0検索クエリ（必要な場合）", "mem0_write_content": "Mem0書き込み内容（必要な場合）"}}
"""
    messages.append(HumanMessage(content=state_summary))

    response = model.invoke(messages)
    content = response.content

    # JSONパース試行
    try:
        # JSON部分を抽出
        json_start = content.find("{")
        json_end = content.rfind("}") + 1
        if json_start >= 0 and json_end > json_start:
            parsed = json.loads(content[json_start:json_end])
        else:
            parsed = {}
    except json.JSONDecodeError:
        parsed = {}

    next_node = parsed.get("next_node", "respond_node")
    if next_node not in VALID_NODES:
        next_node = "respond_node"

    return {
        "messages": [AIMessage(content=f"[Supervisor] 計画: {parsed.get('task_plan', content[:200])}")],
        "next_node": next_node,
        "task_plan": parsed.get("task_plan", ""),
        "task_status": "executing",
        "grok_query": parsed.get("grok_query", state.get("grok_query", "")),
        "mem0_query": parsed.get("mem0_query", state.get("mem0_query", "")),
        "mem0_write_content": parsed.get("mem0_write_content", state.get("mem0_write_content", "")),
    }


# ============================================================
# Grok Node — Grok Consultant Agent 呼び出し
# ============================================================

def grok_node(state: TeamState) -> dict[str, Any]:
    """Grok Consultant Agent にクエリを送信。"""
    query = state.get("grok_query", "")
    if not query:
        return {
            "messages": [AIMessage(content="[Grok] クエリが空のためスキップ")],
            "grok_response": "",
            "next_node": "supervisor",
        }

    try:
        # コンテキストとしてMem0の結果があれば付与
        context = ""
        mem0_results = state.get("mem0_results", [])
        if mem0_results:
            context = "【過去の知見】\n" + "\n".join(
                f"- {r['memory']}" for r in mem0_results[:5]
            )

        response = grok_consult.invoke({"query": query, "context": context})
        return {
            "messages": [AIMessage(content=f"[Grok Consultant] {response[:500]}")],
            "grok_response": response,
            "next_node": "supervisor",
        }
    except Exception as e:
        return {
            "messages": [AIMessage(content=f"[Grok] エラー: {e}")],
            "grok_response": "",
            "error_log": [f"[Grok] {e}"],
            "next_node": "supervisor",
        }


# ============================================================
# Mem0 Search Node — 知見検索
# ============================================================

def mem0_search_node(state: TeamState) -> dict[str, Any]:
    """Mem0 から知見・引き継ぎを検索。"""
    query = state.get("mem0_query", "")
    if not query:
        return {
            "messages": [AIMessage(content="[Mem0 Search] クエリが空のためスキップ")],
            "mem0_results": [],
            "next_node": "supervisor",
        }

    try:
        if state.get("mem0_cross_scope", False):
            results = mem0_search_cross_scope.invoke({"query": query, "limit": 10})
            scope_label = "cross-scope (ai-team / ai-team-claude / ai-team-grok)"
        else:
            user_id = get_user_id(state.get("agent_name", ""))
            results = mem0_search.invoke({"query": query, "limit": 10, "user_id": user_id})
            scope_label = user_id
        summary = f"[Mem0 Search] {len(results)}件の結果 (scope={scope_label}):"
        for r in results[:5]:
            summary += f"\n  - {r['memory'][:100]} (score: {r.get('score', 0):.2f})"

        return {
            "messages": [AIMessage(content=summary)],
            "mem0_results": results,
            "next_node": "supervisor",
        }
    except Exception as e:
        return {
            "messages": [AIMessage(content=f"[Mem0 Search] エラー: {e}")],
            "mem0_results": [],
            "error_log": [f"[Mem0 Search] {e}"],
            "next_node": "supervisor",
        }


# ============================================================
# Mem0 Write Node — 知見書き込み
# ============================================================

def mem0_write_node(state: TeamState) -> dict[str, Any]:
    """Mem0 に新しい知見を書き込み。"""
    content = state.get("mem0_write_content", "")
    if not content:
        return {
            "messages": [AIMessage(content="[Mem0 Write] 書き込み内容が空のためスキップ")],
            "next_node": "supervisor",
        }

    metadata = state.get("mem0_write_metadata", {})
    metadata_type = metadata.get("type", "knowledge")
    user_id = get_user_id(state.get("agent_name", ""))

    try:
        result = mem0_write.invoke({
            "content": content,
            "metadata_type": metadata_type,
            "session_id": state.get("session_id", SESSION_ID),
            "date": TODAY,
            "user_id": user_id,
        })
        return {
            "messages": [AIMessage(content=f"[Mem0 Write] 書き込み完了: {content[:100]}")],
            "mem0_write_content": "",  # 書き込み後クリア
            "next_node": "supervisor",
        }
    except Exception as e:
        return {
            "messages": [AIMessage(content=f"[Mem0 Write] エラー: {e}")],
            "error_log": [f"[Mem0 Write] {e}"],
            "next_node": "supervisor",
        }


# ============================================================
# Reflection Node — 品質チェック・振り返り
# ============================================================

def reflection_node(state: TeamState) -> dict[str, Any]:
    """作業結果の品質チェック・振り返り。"""
    model = ChatAnthropic(
        model=SUPERVISOR_MODEL,
        max_tokens=1024,
        temperature=0.2,
    )

    reflection_prompt = f"""作業結果を振り返り、品質チェックを行ってください。

【作業計画】
{state.get('task_plan', '不明')}

【Grokの回答】
{state.get('grok_response', 'なし')[:1000]}

【Mem0検索結果】
{json.dumps(state.get('mem0_results', [])[:3], ensure_ascii=False, indent=2)[:1000]}

【エラーログ】
{state.get('error_log', [])}

以下の観点でチェックしてください:
1. 回答の正確性・完全性
2. 追加で必要な情報はないか
3. Mem0に保存すべき知見はあるか
4. 改善点はあるか

JSON形式で回答: {{"quality": "high/medium/low", "needs_more_work": true/false, "knowledge_to_save": "保存すべき知見", "improvement": "改善点"}}
"""

    response = model.invoke([HumanMessage(content=reflection_prompt)])
    content = response.content

    try:
        json_start = content.find("{")
        json_end = content.rfind("}") + 1
        if json_start >= 0 and json_end > json_start:
            parsed = json.loads(content[json_start:json_end])
        else:
            parsed = {}
    except json.JSONDecodeError:
        parsed = {}

    # 知見保存が必要な場合
    mem0_write_content = parsed.get("knowledge_to_save", "")
    next_node = "supervisor" if parsed.get("needs_more_work") else "respond_node"

    return {
        "messages": [AIMessage(content=f"[Reflection] 品質: {parsed.get('quality', '不明')} — {parsed.get('improvement', '')[:200]}")],
        "mem0_write_content": mem0_write_content if mem0_write_content else state.get("mem0_write_content", ""),
        "next_node": next_node,
    }


# ============================================================
# Evolution Node — Team Evolution Review (4視点振り返り)
# ============================================================

def evolution_node(state: TeamState) -> dict[str, Any]:
    """Team Evolution Review — 4視点で振り返り、改善提案を生成。"""
    model = ChatAnthropic(
        model=SUPERVISOR_MODEL,
        max_tokens=1500,
        temperature=0.3,
    )

    evolution_prompt = f"""Team Evolution Review を実施してください。

【今セッション（{state.get('session_id', SESSION_ID)}）の作業内容】
{state.get('task_plan', '不明')}

【エラーログ】
{state.get('error_log', [])}

以下の4視点で振り返り、JSON形式で回答してください:

{{
  "repeat_patterns": "🔁 繰り返しパターン: また同じことをやった作業は？テンプレート化できるか？",
  "efficiency_chances": "⚡ 効率化チャンス: もっと速くできたはずの作業・ボトルネックは？",
  "tool_improvements": "🛠️ ツール・スキル改善: CLAUDE.md / スキル / Mem0への具体的な改善提案は？",
  "team_collaboration": "🤝 チーム連携改善: 他エージェントとの分業・連携でもっとうまくできることは？",
  "template_candidates": ["テンプレート化候補1", "テンプレート化候補2"]
}}
"""

    response = model.invoke([HumanMessage(content=evolution_prompt)])
    content = response.content

    try:
        json_start = content.find("{")
        json_end = content.rfind("}") + 1
        if json_start >= 0 and json_end > json_start:
            parsed = json.loads(content[json_start:json_end])
        else:
            parsed = {}
    except json.JSONDecodeError:
        parsed = {}

    evolution_review = {
        "repeat_patterns": parsed.get("repeat_patterns", ""),
        "efficiency_chances": parsed.get("efficiency_chances", ""),
        "tool_improvements": parsed.get("tool_improvements", ""),
        "team_collaboration": parsed.get("team_collaboration", ""),
    }
    template_candidates = parsed.get("template_candidates", [])

    # Mem0に保存する内容を準備
    evolution_content = f"Team Evolution Review ({state.get('session_id', SESSION_ID)}): "
    evolution_content += " | ".join(f"{k}: {v[:100]}" for k, v in evolution_review.items() if v)

    return {
        "messages": [AIMessage(content=f"[Evolution] Review完了 — テンプレート候補: {len(template_candidates)}件")],
        "evolution_review": evolution_review,
        "template_candidates": template_candidates,
        "mem0_write_content": evolution_content,
        "mem0_write_metadata": {"type": "team_evolution"},
        "next_node": "mem0_write_node",  # Evolution結果をMem0に書き込み
    }


# ============================================================
# Respond Node — 最終回答を組み立て
# ============================================================

def respond_node(state: TeamState) -> dict[str, Any]:
    """最終回答をまとめてユーザーに返す。"""
    model = ChatAnthropic(
        model=SUPERVISOR_MODEL,
        max_tokens=2048,
        temperature=0.3,
    )

    respond_prompt = f"""CEOへの最終報告をまとめてください。

【作業計画】
{state.get('task_plan', '')}

【Grokの回答】
{state.get('grok_response', 'なし')[:1500]}

【Mem0検索結果】
{json.dumps([r['memory'] for r in state.get('mem0_results', [])[:5]], ensure_ascii=False)[:500]}

簡潔かつ具体的に報告してください。マークダウン形式で。
"""

    response = model.invoke([HumanMessage(content=respond_prompt)])

    return {
        "messages": [AIMessage(content=response.content)],
        "task_status": "completed",
        "next_node": "__end__",
    }
