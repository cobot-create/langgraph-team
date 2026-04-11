"""
LangGraph AI Team — Slack Bot + FastAPI Server
Events API (HTTP Webhook) 対応版 v4
SL-151/152/153: get_session_id() によるスレッド記憶継続対応
"""
from __future__ import annotations
import json, logging, os, threading, uuid
from contextlib import asynccontextmanager
from datetime import datetime
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
load_dotenv()
from langchain_core.messages import HumanMessage
from graph import app as langgraph_app
from state import TeamState

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("langgraph-team")

SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN", "")
AI_OPS_CHANNEL = os.getenv("SLACK_CHANNEL_AI_OPS", "ai-ops")
slack_client = None

if SLACK_BOT_TOKEN:
    from slack_sdk import WebClient
    slack_client = WebClient(token=SLACK_BOT_TOKEN)
    logger.info("Slack WebClient initialized")

SESSION_ID = os.getenv("SESSION_ID", f"SL-{datetime.now().strftime('%Y%m%d%H%M')}")


# ============================================================
# SL-151: session_id generation (thread_ts → session continuity)
# ============================================================

def get_session_id(event: dict) -> str:
    """
    Slack event から session_id を生成。
    - スレッド返信 (thread_ts あり) → slack-thread-{thread_ts} でスレッド内記憶を共有
    - 新規メッセージ (thread_ts なし) → slack-{uuid4} で新規セッション
    """
    thread_ts = event.get("thread_ts")
    if thread_ts:
        return f"slack-thread-{thread_ts}"
    else:
        return f"slack-{uuid.uuid4()}"


def run_langgraph(query, slack_channel="", slack_thread_ts="", session_id=None, agent_name=""):
    resolved_session_id = session_id if session_id else SESSION_ID
    initial_state: TeamState = {
        "messages": [HumanMessage(content=query)],
        "next_node": "", "task_plan": "", "grok_query": "", "grok_response": "",
        "mem0_query": "", "mem0_results": [], "mem0_write_content": "",
        "mem0_write_metadata": {}, "evolution_review": {}, "template_candidates": [],
        "knowledge_to_promote": [], "session_id": resolved_session_id,
        "task_status": "planning", "error_log": [],
        "slack_channel": slack_channel, "slack_thread_ts": slack_thread_ts,
        "agent_name": agent_name,
    }
    logger.info(f"Starting LangGraph: session_id={resolved_session_id}, agent_name={agent_name}, query={query[:80]}")
    final_state = None
    for step in langgraph_app.stream(initial_state, {"recursion_limit": 15}):
        for node_name, node_output in step.items():
            for msg in node_output.get("messages", []):
                logger.info(f"[{node_name}] {(msg.content if hasattr(msg, 'content') else str(msg))[:200]}")
            final_state = node_output
    return final_state

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("LangGraph AI Team starting (Events API mode v4 - session_id support)")
    yield

fastapi_app = FastAPI(title="LangGraph AI Team", version="4.0.0", lifespan=lifespan)

@fastapi_app.post("/slack/events")
async def slack_events(request: Request):
    raw_body = await request.body()
    try:
        body = json.loads(raw_body)
    except Exception:
        return JSONResponse(status_code=400, content={"error": "invalid json"})

    # URL verification (Slack App設定時)
    if body.get("type") == "url_verification":
        logger.info("URL verification challenge received")
        return JSONResponse(content={"challenge": body["challenge"]})

    # リトライは即座にOKを返す
    if request.headers.get("X-Slack-Retry-Num"):
        return JSONResponse(content={"ok": True})

    # イベント処理
    event = body.get("event", {})
    event_type = event.get("type", "")
    logger.info(f"Received event type: {event_type}")

    if event_type == "app_mention" and slack_client:
        text = event.get("text", "")
        channel = event.get("channel", "")
        thread_ts = event.get("ts", "")
        user = event.get("user", "")

        # SL-151: session_id を thread_ts から自動生成
        session_id = get_session_id(event)

        # メンション部分を除去
        query = text.split(">", 1)[-1].strip() if ">" in text else text
        # Slack MCP経由の付加テキストを除去
        if "*使用して" in query:
            query = query.split("*使用して")[0].strip()
        logger.info(f"app_mention from {user}: session_id={session_id}, query={query[:100]}")

        # 即座にエコー返信
        try:
            slack_client.chat_postMessage(
                channel=channel,
                thread_ts=thread_ts,
                text=f"🔮 Events API v4 動作中\nsession_id: `{session_id}`\nクエリ受信: {query[:100]}"
            )
        except Exception as e:
            logger.error(f"Echo reply error: {e}")

        # LangGraph をバックグラウンド実行
        def _run():
            try:
                result = run_langgraph(query, channel, thread_ts, session_id=session_id)
                final_msg = ""
                if result and "messages" in result:
                    for msg in result["messages"]:
                        content = msg.content if hasattr(msg, "content") else str(msg)
                        final_msg += content + "\n"
                if final_msg:
                    slack_client.chat_postMessage(channel=channel, thread_ts=thread_ts, text=f"✅ 実行完了\n\n{final_msg[:3000]}")
            except Exception as e:
                logger.error(f"LangGraph error: {e}")
                slack_client.chat_postMessage(channel=channel, thread_ts=thread_ts, text=f"❌ エラー: {e}")
        threading.Thread(target=_run, daemon=True).start()

    return JSONResponse(content={"ok": True})

@fastapi_app.get("/health")
async def health():
    return {"status": "ok", "session_id": SESSION_ID, "mode": "events_api_v4_session_id_support"}

@fastapi_app.post("/run")
async def run_api(request: Request):
    body = await request.json()
    query = body.get("query", "")
    if not query:
        return JSONResponse(status_code=400, content={"error": "query is required"})
    session_id = body.get("session_id", SESSION_ID)
    agent_name = body.get("agent_name", "")
    result = run_langgraph(query, session_id=session_id, agent_name=agent_name)
    final_messages = []
    if result and "messages" in result:
        for msg in result["messages"]:
            final_messages.append(msg.content if hasattr(msg, "content") else str(msg))
    return {"status": "completed", "session_id": session_id, "messages": final_messages,
            "task_status": result.get("task_status", "unknown") if result else "error"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(fastapi_app, host="0.0.0.0", port=8080)
