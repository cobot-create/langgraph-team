#!/usr/bin/env python3
"""SL-157: weekly_cost_report.py — 週次APIコストレポート自動化"""
from __future__ import annotations
import argparse, os, sys
from collections import defaultdict
from datetime import datetime, timedelta
import httpx
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

MEM0_API_URL = os.getenv("MEM0_API_URL", "http://localhost:8888")
MEM0_API_KEY = os.getenv("MEM0_API_KEY", "mem0-admin-cobot-2026")
SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN", "")
SLACK_CHANNEL = os.getenv("SLACK_REPORT_CHANNEL", "C0AL5DRAY15")
XAI_API_KEY = os.getenv("XAI_API_KEY", "")
GROK_MODEL = os.getenv("GROK_MODEL", "grok-3-fast")
MEM0_HEADERS = {"Content-Type": "application/json", "Authorization": f"Bearer {MEM0_API_KEY}"}
SCOPES = ["ai-team", "ai-team-claude", "ai-team-grok"]


def fetch_cost_logs(dry_run=False):
    all_logs = []
    seen_ids = set()
    with httpx.Client(timeout=30) as client:
        for scope in SCOPES:
            try:
                resp = client.post(f"{MEM0_API_URL}/search", headers=MEM0_HEADERS,
                    json={"query": "api cost log", "user_id": scope, "limit": 100})
                resp.raise_for_status()
                results = resp.json().get("results", [])
                cost_count = 0
                for r in results:
                    rid = r.get("id", "")
                    if r.get("metadata", {}).get("type") != "api_cost_log":
                        continue
                    if rid and rid in seen_ids:
                        continue
                    if rid:
                        seen_ids.add(rid)
                    all_logs.append({**r, "scope": scope})
                    cost_count += 1
                print(f"  {scope}: {cost_count}件")
            except Exception as e:
                print(f"  [{scope}] error: {e}")
    if dry_run and not all_logs:
        print("  [dry-run] ダミーデータ使用")
        today = datetime.now()
        for i in range(7):
            d = today - timedelta(days=i)
            all_logs.append({
                "memory": f"claude: $0.{10+i*3:02d}",
                "metadata": {"type": "api_cost_log", "date": d.strftime("%Y-%m-%d"),
                             "claude_usd": round(0.10+i*0.03, 3), "grok_usd": round(0.05+i*0.02, 3)},
                "scope": "ai-team", "id": f"dummy-{i}"
            })
    return all_logs


def aggregate_by_date(logs):
    daily = defaultdict(lambda: {"claude": 0.0, "grok": 0.0, "total": 0.0})
    for log in logs:
        meta = log.get("metadata", {})
        date = meta.get("date", "") or log.get("created_at", "")[:10]
        if not date:
            continue
        c = float(meta.get("claude_usd", 0))
        g = float(meta.get("grok_usd", 0))
        daily[date]["claude"] += c
        daily[date]["grok"] += g
        daily[date]["total"] += c + g
    return dict(sorted(daily.items()))


def generate_graph(daily, output_path):
    if not daily:
        fig, ax = plt.subplots(figsize=(10, 5))
        ax.text(0.5, 0.5, "No cost data available", ha="center", va="center", fontsize=14)
        fig.savefig(output_path, dpi=150, bbox_inches="tight")
        plt.close(fig)
        return
    dates = [datetime.strptime(d, "%Y-%m-%d") for d in daily.keys()]
    claude_costs = [v["claude"] for v in daily.values()]
    grok_costs = [v["grok"] for v in daily.values()]
    totals = [v["total"] for v in daily.values()]
    fig, ax = plt.subplots(figsize=(12, 6))
    ax.bar(dates, claude_costs, label="Claude API", color="#4A90D9", alpha=0.8)
    ax.bar(dates, grok_costs, bottom=claude_costs, label="Grok API", color="#E8734A", alpha=0.8)
    ax.plot(dates, totals, "ko-", linewidth=2, markersize=5, label="Total", zorder=5)
    ax.set_title(f"Weekly API Cost ({dates[0].strftime('%m/%d')}-{dates[-1].strftime('%m/%d/%Y')})",
                 fontsize=14, fontweight="bold")
    ax.set_xlabel("Date")
    ax.set_ylabel("Cost (USD)")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%m/%d"))
    ax.xaxis.set_major_locator(mdates.DayLocator())
    plt.xticks(rotation=45)
    ax.legend()
    ax.grid(axis="y", alpha=0.3)
    ax.text(0.02, 0.97, f"Weekly Total: ${sum(totals):.3f}", transform=ax.transAxes,
            fontsize=12, va="top", bbox=dict(boxstyle="round", facecolor="wheat", alpha=0.5))
    plt.tight_layout()
    fig.savefig(output_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  グラフ生成: {output_path}")


def grok_comment(daily):
    if not XAI_API_KEY:
        return "(Grok APIキー未設定)"
    total = sum(v["total"] for v in daily.values())
    try:
        from langchain_xai import ChatXAI
        model = ChatXAI(model=GROK_MODEL, api_key=XAI_API_KEY, max_tokens=60)
        resp = model.invoke([
            {"role": "system", "content": "AIコストアナリスト。1行30文字以内で日本語コメント。"},
            {"role": "user", "content": f"今週AIチームAPIコスト合計: ${total:.3f} USD"},
        ])
        return resp.content.strip()
    except Exception as e:
        return f"(Grok失敗: {e})"


def post_to_slack(graph_path, summary_text, dry_run=False):
    if dry_run:
        print(f"  [dry-run] Slack投稿スキップ")
        print(f"  内容: {summary_text[:120]}")
        return True
    if not SLACK_BOT_TOKEN:
        print("  警告: SLACK_BOT_TOKEN未設定")
        return False
    with httpx.Client(timeout=30) as client:
        r = client.post("https://slack.com/api/chat.postMessage",
            headers={"Authorization": f"Bearer {SLACK_BOT_TOKEN}"},
            json={"channel": SLACK_CHANNEL, "text": summary_text, "mrkdwn": True})
        if not r.json().get("ok"):
            print(f"  Slack失敗: {r.json().get('error')}")
            return False
        with open(graph_path, "rb") as f:
            r2 = client.post("https://slack.com/api/files.upload",
                headers={"Authorization": f"Bearer {SLACK_BOT_TOKEN}"},
                data={"channels": SLACK_CHANNEL, "filename": "weekly_cost.png"},
                files={"file": f})
        print(f"  画像投稿: {'OK' if r2.json().get('ok') else r2.json().get('error')}")
    return True


def write_completion_to_mem0(summary, dry_run=False):
    if dry_run:
        print("  [dry-run] Mem0書き込みスキップ")
        return
    content = f"SL-157 weekly_cost_report 実行完了: {summary}"
    meta = {"type": "api_cost_log", "date": datetime.now().strftime("%Y-%m-%d"), "report": "weekly"}
    with httpx.Client(timeout=30) as client:
        for scope in SCOPES:
            try:
                r = client.post(f"{MEM0_API_URL}/memories", headers=MEM0_HEADERS,
                    json={"messages": [{"role": "assistant", "content": content}],
                          "user_id": scope, "metadata": meta})
                r.raise_for_status()
                print(f"  Mem0[{scope}]: OK")
            except Exception as e:
                print(f"  Mem0[{scope}]: エラー {e}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    dry_run = args.dry_run
    label = "[DRY-RUN] " if dry_run else ""
    print(f"=== Weekly Cost Report {label}=== {datetime.now().strftime('%Y-%m-%d %H:%M')}")

    print("\n[1] Mem0コストログ取得 (3スコープ横断)...")
    logs = fetch_cost_logs(dry_run=dry_run)
    print(f"  合計: {len(logs)}件")

    print("\n[2] 日別集計...")
    daily = aggregate_by_date(logs)
    total = sum(v["total"] for v in daily.values())
    keys = list(daily.keys())
    print(f"  期間: {keys[0] if keys else 'N/A'} - {keys[-1] if keys else 'N/A'}")
    print(f"  週間合計: ${total:.3f} USD")

    print("\n[3] グラフ生成...")
    graph_path = "/tmp/weekly_cost_report.png"
    generate_graph(daily, graph_path)

    print("\n[4] Grokコメント...")
    comment = grok_comment(daily)
    print(f"  {comment}")

    summary = (
        f"*週次APIコストレポート* ({datetime.now().strftime('%Y-%m-%d')})\n"
        f"週間合計: *${total:.3f} USD*\n"
        f"Grok分析: _{comment}_\n"
        f"スコープ: {', '.join(SCOPES)}"
    )

    print("\n[5] Slack投稿...")
    post_to_slack(graph_path, summary, dry_run=dry_run)

    print("\n[6] Mem0完了記録 (cross-scope)...")
    write_completion_to_mem0(f"週間合計 ${total:.3f} USD", dry_run=dry_run)

    print("\n✅ 完了")
    return 0


if __name__ == "__main__":
    sys.exit(main())
