"""
LangGraph AI Team — Hierarchical Supervisor Graph
メインのグラフ定義。Supervisor → 各ノードへのルーティング。
"""
from __future__ import annotations

from typing import Literal

from langgraph.graph import END, StateGraph

from nodes import (
    evolution_node,
    grok_node,
    mem0_search_node,
    mem0_write_node,
    reflection_node,
    respond_node,
    supervisor_node,
)
from state import TeamState
import os
import sqlite3
from langgraph.checkpoint.sqlite import SqliteSaver


def route_from_supervisor(state: TeamState) -> str:
    """Supervisor の next_node に基づいてルーティング。"""
    next_node = state.get("next_node", "respond_node")
    if next_node == "__end__" or next_node not in {
        "grok_node",
        "mem0_search_node",
        "mem0_write_node",
        "reflection_node",
        "evolution_node",
        "respond_node",
    }:
        return "respond_node"
    return next_node


def route_back_to_supervisor(state: TeamState) -> str:
    """各ノードからの帰還ルーティング。"""
    next_node = state.get("next_node", "supervisor")
    if next_node == "__end__":
        return END
    if next_node == "respond_node":
        return "respond_node"
    # Mem0 write の後は supervisor に戻す
    if next_node == "mem0_write_node":
        return "mem0_write_node"
    return "supervisor"


def build_graph() -> StateGraph:
    """Hierarchical Supervisor グラフを構築。"""
    graph = StateGraph(TeamState)

    # --- ノード登録 ---
    graph.add_node("supervisor", supervisor_node)
    graph.add_node("grok_node", grok_node)
    graph.add_node("mem0_search_node", mem0_search_node)
    graph.add_node("mem0_write_node", mem0_write_node)
    graph.add_node("reflection_node", reflection_node)
    graph.add_node("evolution_node", evolution_node)
    graph.add_node("respond_node", respond_node)

    # --- エントリーポイント ---
    graph.set_entry_point("supervisor")

    # --- Supervisor → 各ノードへの条件分岐 ---
    graph.add_conditional_edges(
        "supervisor",
        route_from_supervisor,
        {
            "grok_node": "grok_node",
            "mem0_search_node": "mem0_search_node",
            "mem0_write_node": "mem0_write_node",
            "reflection_node": "reflection_node",
            "evolution_node": "evolution_node",
            "respond_node": "respond_node",
        },
    )

    # --- 各ノード → Supervisor / respond / END ---
    for node_name in [
        "grok_node",
        "mem0_search_node",
        "mem0_write_node",
        "reflection_node",
    ]:
        graph.add_conditional_edges(
            node_name,
            route_back_to_supervisor,
            {
                "supervisor": "supervisor",
                "respond_node": "respond_node",
                "mem0_write_node": "mem0_write_node",
                END: END,
            },
        )

    # Evolution → mem0_write → supervisor
    graph.add_conditional_edges(
        "evolution_node",
        route_back_to_supervisor,
        {
            "supervisor": "supervisor",
            "respond_node": "respond_node",
            "mem0_write_node": "mem0_write_node",
            END: END,
        },
    )

    # respond_node → END
    graph.add_edge("respond_node", END)

    return graph


def compile_graph():
    """グラフをコンパイルして返す。"""
    graph = build_graph()
    db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "checkpoints.db")
    conn = sqlite3.connect(db_path, check_same_thread=False)
    checkpointer = SqliteSaver(conn)
    return graph.compile(checkpointer=checkpointer)


# シングルトン
app = compile_graph()
