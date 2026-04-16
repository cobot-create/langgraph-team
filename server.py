"""
LangGraph AI Team — Slack Bot + FastAPI Server
Events API (HTTP Webhook) 対応版 v5
SL-151/152/153: get_session_id() によるスレッド記憶継続対応
SL-161: #ai-ops 即時自動トリガー強化（message イベント対応）
"""
from __future__ import annotations
import json, logging, os, re, subprocess, threading, uuid
from contextlib import asynccontextmanager
from datetime import datetime
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
load_dotenv()
from langchain_core.messages import HumanMessage
from graph import app as langgraph_app
from state import TeamState
import mission_engine

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("langgraph-team")

SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN", "")
AI_OPS_CHANNEL_ID = os.getenv("SLACK_CHANNEL_AI_OPS_ID", "C0AL5DRAY15")
AI_OPS_CHANNEL = os.getenv("SLACK_CHANNEL_AI_OPS", "ai-ops")

# SL-161: 自動トリガーフィルタキーワード
AI_OPS_TRIGGER_KEYWORDS = ["🎯", ":dart:", "【指令】", "【instruction】", "mission-add", "mission-list", "mission-retry", "mission-skip"]

def extract_full_text_from_blocks(event: dict) -> str:
    blocks = event.get("blocks", [])
    if not blocks:
        return event.get("text", "")
    parts = []
    for block in blocks:
        if block.get("type") != "rich_text":
            continue
        for element in block.get("elements", []):
            etype = element.get("type", "")
            if etype in ("rich_text_section", "rich_text_preformatted", "rich_text_list"):
                for item in element.get("elements", []):
                    if item.get("type") == "text":
                        parts.append(item.get("text", ""))
                    elif item.get("type") == "link":
                        parts.append(item.get("url", ""))
                if etype == "rich_text_preformatted" and parts and not parts[-1].endswith(chr(10)):
                    parts.append(chr(10))
    full_text = "".join(parts).strip()
    return full_text if full_text else event.get("text", "")

# Bot自身のユーザーIDを環境変数で管理（自己応答ループ防止）
BOT_USER_ID = os.getenv("SLACK_BOT_USER_ID", "")

slack_client = None

if SLACK_BOT_TOKEN:
    from slack_sdk import WebClient
    slack_client = WebClient(token=SLACK_BOT_TOKEN)
    logger.info("Slack WebClient initialized")
_ALF="/home/ubuntu/langgraph-team/autoloop.flag"
_alr=False
_alt=None

SESSION_ID = os.getenv("SESSION_ID", f"SL-{datetime.now().strftime('%Y%m%d%H%M')}")


# ============================================================
# SL-151: session_id generation (thread_ts → session continuity)
# ============================================================

def _alon(sc,ch,ts):
    global _alr
    if _alr:
        sc and sc.chat_postMessage(channel=ch,thread_ts=ts,text="already ON")
        return
    import json as _j
    _j.dump({"s":"on"},open(_ALF,"w"))
    _alr=True
    sc and sc.chat_postMessage(channel=ch,thread_ts=ts,text=":white_check_mark: autoloop ON")
    mission_engine.on_autoloop_on(sc, ch)
def _aloff(sc,ch,ts):
    global _alr
    _alr=False
    import os as _o
    _o.path.exists(_ALF) and _o.remove(_ALF)
    sc and sc.chat_postMessage(channel=ch,thread_ts=ts,text=":octagonal_sign: autoloop OFF")
def _alstatus(sc,ch,ts):
    msg = mission_engine.mission_status()
    sc and sc.chat_postMessage(channel=ch,thread_ts=ts,text=msg)



def _extract_text_from_blocks(blocks):
    parts = []
    for block in blocks:
        btype = block.get("type", "")
        if btype == "rich_text":
            for elem in block.get("elements", []):
                etype = elem.get("type", "")
                if etype in ("rich_text_section", "rich_text_preformatted", "rich_text_list"):
                    line = []
                    for sub in elem.get("elements", []):
                        stype = sub.get("type", "")
                        if stype == "text":
                            line.append(sub.get("text", ""))
                        elif stype == "link":
                            line.append(sub.get("text") or sub.get("url", ""))
                        elif stype == "user":
                            line.append("<@" + sub.get("user_id", "") + ">")
                    parts.append("".join(line))
        elif btype == "section":
            txt = block.get("text", {})
            if txt.get("text"):
                parts.append(txt["text"])
    return chr(10).join(p for p in parts if p).strip()

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


# ============================================================
# SL-172: thread_id自動管理 (project-xxx パターン検出 + Mem0登録)
# ============================================================

MEM0_API_URL = os.getenv("MEM0_API_URL", "http://localhost:8888")
MEM0_API_KEY = os.getenv("MEM0_API_KEY", "mem0-admin-cobot-2026")
MEM0_USER_ID = os.getenv("MEM0_USER_ID", "ai-team")
_mem0_headers = {
    "Content-Type": "application/json",
    "Authorization": f"Bearer {MEM0_API_KEY}",
}

def resolve_thread_id(text: str, ts: str) -> str:
    """
    Slackメッセージからthread_idを解決する。
    - テキストに project-xxx パターンがあれば Mem0 で既存 thread_id を検索・再利用
    - なければ新規登録して返す
    - デフォルト: slack-ai-ops-{ts}
    """
    import httpx
    match = re.search(r'project-[a-z0-9][a-z0-9-]*', text)
    if not match:
        return f"slack-ai-ops-{ts}"

    project_name = match.group(0)
    default_thread_id = f"slack-ai-ops-{ts}"

    try:
        with httpx.Client(timeout=10) as client:
            # Mem0で既存thread_idを検索
            resp = client.post(
                f"{MEM0_API_URL}/search",
                headers=_mem0_headers,
                json={
                    "query": f"thread_id {project_name}",
                    "user_id": MEM0_USER_ID,
                    "limit": 5,
                },
            )
            resp.raise_for_status()
            results = resp.json().get("results", [])

            # type=thread_id かつ project_name が一致するエントリを探す
            for r in results:
                meta = r.get("metadata", {})
                if meta.get("type") == "thread_id" and meta.get("project") == project_name:
                    existing = meta.get("thread_id", "")
                    if existing:
                        logger.info(f"SL-172 resolve_thread_id: reuse {existing} for {project_name}")
                        return existing

            # 新規登録
            new_thread_id = f"project-{project_name}-{ts}"
            client.post(
                f"{MEM0_API_URL}/memories",
                headers=_mem0_headers,
                json={
                    "messages": [{"role": "assistant", "content": f"thread_id for {project_name}: {new_thread_id}"}],
                    "user_id": MEM0_USER_ID,
                    "metadata": {
                        "type": "thread_id",
                        "project": project_name,
                        "thread_id": new_thread_id,
                        "created_at": datetime.now().strftime("%Y-%m-%d"),
                    },
                },
            )
            logger.info(f"SL-172 resolve_thread_id: new {new_thread_id} for {project_name}")
            return new_thread_id

    except Exception as e:
        logger.warning(f"SL-172 resolve_thread_id error: {e} — fallback to default")
        return default_thread_id


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
    for step in langgraph_app.stream(
        initial_state,
        {
            "recursion_limit": 15,
            "configurable": {"thread_id": resolved_session_id},
        }
    ):
        for node_name, node_output in step.items():
            for msg in node_output.get("messages", []):
                logger.info(f"[{node_name}] {(msg.content if hasattr(msg, 'content') else str(msg))[:200]}")
            final_state = node_output
    return final_state

@asynccontextmanager
async def lifespan(app: FastAPI):
    if os.path.exists(_ALF):
        global _alr,_alt
        import threading,time
        _alr=True
        def _rwk():
            while _alr: time.sleep(30)
        _alt=threading.Thread(target=_rwk,daemon=True)
        _alt.start()
        logger.info("[autoloop] restored ON")
        mission_engine.on_autoloop_on()
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

    # SL-161: #ai-ops message イベント → 自動トリガー
    if event_type == "message" and slack_client:
        channel = event.get("channel", "")
        text_for_trigger = event.get("text", "")
        text = extract_full_text_from_blocks(event)
        _blk = event.get("blocks", [])
        if _blk:
            _bt = _extract_text_from_blocks(_blk)
            if _bt and len(_bt) > len(text):
                text = _bt
        user = event.get("user", "")
        ts = event.get("ts", "")
        subtype = event.get("subtype", "")

        # #ai-ops チャンネルへの新規投稿のみ対象（Bot自身・編集・削除は除外）
        is_ai_ops = channel == AI_OPS_CHANNEL_ID
        is_new_msg = not subtype  # subtypeなし = 新規投稿
        is_not_bot = user != BOT_USER_ID and not event.get("bot_id")
        has_trigger = any(kw in text for kw in AI_OPS_TRIGGER_KEYWORDS) or any(kw in text_for_trigger for kw in AI_OPS_TRIGGER_KEYWORDS) or any(kw in text_for_trigger for kw in AI_OPS_TRIGGER_KEYWORDS)

        # waiting_user ミッション再開チェック（スレッド返信）
        _tts = event.get("thread_ts")
        if is_ai_ops and is_not_bot and _tts and _tts != ts:
            if mission_engine.resume_from_thread(_tts, text):
                return JSONResponse(content={"ok": True})

        # DEBUG: 条件診断ログ (SL-172)
        logger.info(
            f"DEBUG trigger check: channel={channel}({is_ai_ops}) "
            f"subtype={subtype!r}({is_new_msg}) "
            f"user={user} bot_id={event.get('bot_id')}({is_not_bot}) "
            f"has_trigger={has_trigger} text_start={text[:40]!r}"
        )

        if is_ai_ops and is_new_msg and is_not_bot and has_trigger:
            session_id = resolve_thread_id(text, ts)
            logger.info(f"SL-161/172 ai-ops trigger: session_id={session_id}, text={text[:100]}")

            # 受信確認を即座に返信
            try:
                slack_client.chat_postMessage(
                    channel=channel,
                    text=f"⚡ #ai-ops 自動トリガー起動\nsession_id: `{session_id}`\n処理中..."
                )
            except Exception as e:
                logger.error(f"Trigger ack error: {e}")

            def _run_ai_ops_trigger():
                try:
                    if "langgraph-on" in text:
                        _alon(slack_client,channel,ts); return
                    if "langgraph-off" in text:
                        _aloff(slack_client,channel,ts); return
                    if "langgraph-status" in text:
                        _alstatus(slack_client,channel,ts); return
                    if "mission-add" in text:
                        _m = re.search(r'mission-add\s+(.*)', text, re.DOTALL)
                        _mt = _m.group(1).strip() if _m else text
                        _mt = _mt.split("使用して送信されました")[0].strip() if "使用して送信されました" in _mt else _mt
                        mission_engine.mission_add(_mt, slack_client, channel, ts); return
                    if "mission-list" in text:
                        _m = re.search(r'mission-list\s*(\w+)?', text)
                        _fs = _m.group(1) if _m and _m.group(1) else None
                        mission_engine.mission_list(slack_client, channel, ts, filter_status=_fs); return
                    if "mission-retry" in text:
                        _m = re.search(r'mission-retry\s+(m-\S+)', text)
                        _mid = _m.group(1) if _m else ""
                        mission_engine.mission_retry(_mid, slack_client, channel, ts); return
                    if "mission-skip" in text:
                        _m = re.search(r'mission-skip\s+(m-\S+)', text)
                        _mid = _m.group(1) if _m else ""
                        mission_engine.mission_skip(_mid, slack_client, channel, ts); return
                    # SL-172 v6: Mode A/B/C/D 実行制御
                    import importlib, executor as _em; importlib.reload(_em); from executor import get_project_config, resolve_execution_mode, execute_instruction
                    proj_match = re.search(r'project-[a-z0-9][a-z0-9-]*', text)
                    project_id = proj_match.group(0) if proj_match else "default"
                    config = get_project_config(project_id)
                    mode = resolve_execution_mode(config)
                    logger.info(f"Execution mode={mode}, project={project_id}")

                    lg_msg = ""
                    exec_result = ""

                    if mode in ("A", "B"):
                        result = run_langgraph(text, channel, ts, session_id=session_id, agent_name="ai-ops-trigger")
                        if result and "messages" in result:
                            for msg in result["messages"]:
                                content = msg.content if hasattr(msg, "content") else str(msg)
                                lg_msg += content + "\n"

                    if mode in ("B", "C"):
                        exec_result = execute_instruction(text, config)

                    if mode == "D":
                        exec_result = "受信しました（半自動モード: 手動実行待ち）"
                    if mode == "E":
                        # Mode E: coro委譲 VPS→Tailscale SSH→Claude Code@coro
                        import subprocess as _sp
                        # SL-185: 「使用して送信されました」サフィックスを除去してからclaude -pに渡す
                        _clean = text.split("使用して送信されました")[0].strip() if "使用して送信されました" in text else text
                        _safe = _clean.replace(chr(39), chr(39)+chr(92)+chr(39)+chr(39))
                        _r = _sp.run(
                            ["ssh","-o","StrictHostKeyChecking=no","-o","ConnectTimeout=30",
                             "-o","IdentitiesOnly=yes","-i",os.path.expanduser("~/.ssh/id_ed25519"),
                             "manag@100.116.84.60",
                             f"claude -p '{_safe}'"],
                            capture_output=True, timeout=300
                        )
                        exec_result = (_r.stdout.decode("utf-8",errors="replace").strip() or _r.stderr.decode("utf-8",errors="replace").strip() or "(no output from coro)")

                    parts = []
                    if lg_msg: parts.append(f"LangGraph:\n{lg_msg.strip()}")
                    if exec_result: parts.append(f"実行結果:\n{exec_result}")
                    final_msg = "\n\n".join(parts) or "(no output)"

                    # a. Mem0に記録
                    try:
                        from tools_mem0 import mem0_write
                        mem0_write(
                            content=f"ai-ops auto-trigger: {text[:200]} → {final_msg[:200]}",
                            metadata_type="ai_ops_trigger",
                            session_id=session_id,
                            date=datetime.now().strftime("%Y-%m-%d"),
                            user_id="ai-team",
                        )
                    except Exception as e:
                        logger.error(f"Mem0 write error: {e}")

                    # b. progress.md 更新 & Git push
                    try:
                        progress_path = os.path.join(os.path.dirname(__file__), "progress.md")
                        entry = (f"\n### auto-trigger {datetime.now().strftime('%Y-%m-%d %H:%M')} JST\n"
                                 f"- session_id: {session_id}\n"
                                 f"- mode: {mode}, project: {project_id}\n"
                                 f"- query: {text[:100]}\n"
                                 f"- result: {final_msg[:150]}\n")
                        with open(progress_path, "a") as f:
                            f.write(entry)
                        repo_dir = os.path.dirname(__file__)
                        subprocess.run(["git", "add", "progress.md"], cwd=repo_dir, check=True)
                        subprocess.run(["git", "commit", "-m", f"auto-trigger: {session_id}"],
                                       cwd=repo_dir, check=True)
                        subprocess.run(["git", "push", "origin", "main"], cwd=repo_dir, check=True)
                    except Exception as e:
                        logger.error(f"Git push error: {e}")

                    # c. #ai-ops に結果報告
                    report = f"✅ ai-ops auto-trigger 完了\n`{session_id}`\n\n{final_msg[:2000]}" if final_msg else f"✅ ai-ops auto-trigger 完了\n`{session_id}`"
                    slack_client.chat_postMessage(channel=channel, text=report)

                except Exception as e:
                    logger.error(f"ai-ops trigger error: {e}")
                    slack_client.chat_postMessage(channel=channel, text=f"❌ auto-trigger エラー: {e}")

            threading.Thread(target=_run_ai_ops_trigger, daemon=True).start()

    if event_type == "app_mention" and slack_client:
        text_for_trigger = event.get("text", "")
        text = extract_full_text_from_blocks(event)
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
    return {"status": "ok", "session_id": SESSION_ID, "mode": "events_api_v5_ai_ops_trigger"}

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


@fastapi_app.get("/threads/{thread_id}")
async def get_thread_state(thread_id: str):
    """SL-187: LangGraph thread state API"""
    try:
        config={"configurable":{"thread_id":thread_id}}
        state=langgraph_app.get_state(config)
        if not state or not state.values:
            return JSONResponse(status_code=404,content={"error":"Thread not found","thread_id":thread_id})
        msgs=[{"role":type(m).__name__,"content":getattr(m,"content",str(m))} for m in state.values.get("messages",[])]
        return {"thread_id":thread_id,"status":"ok","messages":msgs,"next":list(state.next) if state.next else []}
    except Exception as e:
        logger.error(f"/threads error: {e}")
        return JSONResponse(status_code=404,content={"error":str(e),"thread_id":thread_id})

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(fastapi_app, host="0.0.0.0", port=8080)
