"""
LangGraph AI Team — SQLite Checkpoint Cleanup Script
SL-172: thread_id 自動クリーンアップ

実行: python3 cleanup_threads.py
cron: 0 3 * * 1 cd /home/ubuntu/langgraph-team && python3 cleanup_threads.py >> cleanup.log 2>&1

削除ルール:
- slack-ai-ops-* thread_id → 7日以上経過で削除
- project-* thread_id → 30日以上経過で削除
- Mem0で project_status=completed のプロジェクト → 即削除
"""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timedelta, timezone

import httpx

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "checkpoints.db")
MEM0_API_URL = os.getenv("MEM0_API_URL", "http://localhost:8888")
MEM0_API_KEY = os.getenv("MEM0_API_KEY", "mem0-admin-cobot-2026")
MEM0_USER_ID = os.getenv("MEM0_USER_ID", "ai-team")

_headers = {
    "Content-Type": "application/json",
    "Authorization": f"Bearer {MEM0_API_KEY}",
}

NOW = datetime.now(timezone.utc)
THRESHOLD_SLACK = NOW - timedelta(days=7)
THRESHOLD_PROJECT = NOW - timedelta(days=30)


def log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}")


def get_completed_projects() -> set[str]:
    """Mem0から project_status=completed のプロジェクト名一覧を取得。"""
    try:
        with httpx.Client(timeout=15) as client:
            resp = client.post(
                f"{MEM0_API_URL}/search",
                headers=_headers,
                json={
                    "query": "project_status completed",
                    "user_id": MEM0_USER_ID,
                    "limit": 50,
                },
            )
            resp.raise_for_status()
            results = resp.json().get("results", [])
            completed = set()
            for r in results:
                meta = r.get("metadata", {})
                if meta.get("project_status") == "completed":
                    project = meta.get("project", "")
                    if project:
                        completed.add(project)
            log(f"Mem0 completed projects: {completed}")
            return completed
    except Exception as e:
        log(f"Mem0 search error: {e}")
        return set()


def cleanup_checkpoints(conn: sqlite3.Connection, completed_projects: set[str]) -> dict:
    """checkpoints テーブルから対象 thread_id を削除。"""
    stats = {"slack_deleted": 0, "project_deleted": 0, "completed_deleted": 0, "errors": 0}

    try:
        # LangGraph SQLite スキーマ確認
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {row[0] for row in cur.fetchall()}
        log(f"Tables in DB: {tables}")

        if "checkpoints" not in tables:
            log("No 'checkpoints' table found. DB may be empty or use different schema.")
            return stats

        # thread_id 一覧取得
        cur.execute("SELECT DISTINCT thread_id FROM checkpoints")
        thread_ids = [row[0] for row in cur.fetchall()]
        log(f"Total thread_ids: {len(thread_ids)}")

        for thread_id in thread_ids:
            # 最新チェックポイントのタイムスタンプを取得 (thread_ts は ISO 文字列 or 数値)
            cur.execute(
                "SELECT MAX(thread_ts) FROM checkpoints WHERE thread_id = ?",
                (thread_id,),
            )
            row = cur.fetchone()
            last_ts_raw = row[0] if row else None

            # タイムスタンプ解析
            last_ts = None
            if last_ts_raw is not None:
                try:
                    # Slack ts 形式 (e.g., "1776170973.491919") → Unix timestamp
                    last_ts = datetime.fromtimestamp(float(last_ts_raw), tz=timezone.utc)
                except (ValueError, TypeError):
                    try:
                        # ISO 形式
                        last_ts = datetime.fromisoformat(str(last_ts_raw).replace("Z", "+00:00"))
                    except Exception:
                        last_ts = None

            should_delete = False
            reason = ""

            if thread_id.startswith("slack-ai-ops-"):
                if last_ts is None or last_ts < THRESHOLD_SLACK:
                    should_delete = True
                    reason = f"slack-ai-ops older than 7 days (last: {last_ts})"
                    stats["slack_deleted"] += 1

            elif thread_id.startswith("project-"):
                # completed プロジェクトは即削除
                for cp in completed_projects:
                    if cp in thread_id:
                        should_delete = True
                        reason = f"project completed ({cp})"
                        stats["completed_deleted"] += 1
                        break
                # 30日経過で削除
                if not should_delete and (last_ts is None or last_ts < THRESHOLD_PROJECT):
                    should_delete = True
                    reason = f"project older than 30 days (last: {last_ts})"
                    stats["project_deleted"] += 1

            if should_delete:
                try:
                    cur.execute("DELETE FROM checkpoints WHERE thread_id = ?", (thread_id,))
                    # writes テーブルがあれば同様に削除
                    if "writes" in tables:
                        cur.execute("DELETE FROM writes WHERE thread_id = ?", (thread_id,))
                    log(f"Deleted thread_id={thread_id}: {reason}")
                except Exception as e:
                    log(f"Error deleting {thread_id}: {e}")
                    stats["errors"] += 1

        conn.commit()

    except Exception as e:
        log(f"Cleanup error: {e}")
        stats["errors"] += 1

    return stats


def main() -> None:
    log("=== cleanup_threads.py start ===")
    log(f"DB: {DB_PATH}")

    if not os.path.exists(DB_PATH):
        log("checkpoints.db not found — nothing to clean.")
        return

    completed_projects = get_completed_projects()

    conn = sqlite3.connect(DB_PATH)
    try:
        stats = cleanup_checkpoints(conn, completed_projects)
    finally:
        conn.close()

    log(f"Done: slack_deleted={stats['slack_deleted']}, "
        f"project_deleted={stats['project_deleted']}, "
        f"completed_deleted={stats['completed_deleted']}, "
        f"errors={stats['errors']}")
    log("=== cleanup_threads.py end ===")


if __name__ == "__main__":
    main()
