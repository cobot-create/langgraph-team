"""
LangGraph AI Team — Main Entry Point
CLI でのテスト実行用。
"""
from __future__ import annotations

import os
import sys

from dotenv import load_dotenv
from langchain_core.messages import HumanMessage

load_dotenv()

from graph import app
from state import TeamState


def run_query(query: str, session_id: str = "SL-128") -> dict:
    """クエリを実行してグラフを走らせる。"""
    initial_state: TeamState = {
        "messages": [HumanMessage(content=query)],
        "next_node": "",
        "task_plan": "",
        "grok_query": "",
        "grok_response": "",
        "mem0_query": "",
        "mem0_results": [],
        "mem0_write_content": "",
        "mem0_write_metadata": {},
        "evolution_review": {},
        "template_candidates": [],
        "knowledge_to_promote": [],
        "session_id": session_id,
        "task_status": "planning",
        "error_log": [],
        "slack_channel": "",
        "slack_thread_ts": "",
    }

    # グラフ実行（ストリーム出力）
    print(f"\n{'='*60}")
    print(f"🔮 LangGraph AI Team — Query: {query[:80]}")
    print(f"{'='*60}\n")

    final_state = None
    for step in app.stream(initial_state, {"recursion_limit": 15}):
        for node_name, node_output in step.items():
            msgs = node_output.get("messages", [])
            for msg in msgs:
                content = msg.content if hasattr(msg, "content") else str(msg)
                print(f"  [{node_name}] {content[:200]}")
            final_state = node_output

    print(f"\n{'='*60}")
    print("✅ 実行完了")
    print(f"{'='*60}\n")

    return final_state


def main():
    if len(sys.argv) > 1:
        query = " ".join(sys.argv[1:])
    else:
        query = input("🔮 クエリを入力: ")

    result = run_query(query)

    # 最終メッセージを表示
    if result and "messages" in result:
        for msg in result["messages"]:
            content = msg.content if hasattr(msg, "content") else str(msg)
            print(f"\n📋 最終回答:\n{content}")


if __name__ == "__main__":
    main()
