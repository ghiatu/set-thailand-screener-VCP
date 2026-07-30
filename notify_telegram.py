"""
notify_telegram.py
===================
Reads the screener's output CSV and sends a summary to a Telegram chat
via a Telegram bot.

Setup (one-time):
1. Open Telegram, message @BotFather, send /newbot, follow the prompts.
   BotFather gives you a token like "123456789:AAExxxxxxxxxxxxxxxxxxxxxxxxxxxx"
2. Start a chat with your new bot (search its @username, send it any message).
3. Get your chat_id by visiting this URL in a browser (replace TOKEN):
       https://api.telegram.org/botTOKEN/getUpdates
   Send your bot a message first, then reload that URL — look for "chat":{"id": ...}
4. Add both values as GitHub repo secrets (Settings -> Secrets and variables -> Actions):
       TELEGRAM_BOT_TOKEN
       TELEGRAM_CHAT_ID

Usage:
    python notify_telegram.py --results thailand_screen_results.csv
"""
from __future__ import annotations

import argparse
import os
import sys

import pandas as pd
import requests


def build_message(results_path: str, top_n: int = 15) -> str:
    df = pd.read_csv(results_path)

    passed = df[df["pass_trend_template"] == True]  # noqa: E712
    vcp = df[df["vcp_detected"] == True]  # noqa: E712

    lines = [f"📊 <b>Thailand Stock Screener</b> — {len(df)} stocks scanned\n"]

    lines.append(f"✅ <b>Trend Template + RS pass ({len(passed)}):</b>")
    if passed.empty:
        lines.append("(none today)")
    else:
        for _, r in passed.head(top_n).iterrows():
            lines.append(f"• {r['symbol']}  close={r['close']}  RS={r['rs_rating']:.0f}  VCP={r['vcp_score']:.0f}")
        if len(passed) > top_n:
            lines.append(f"...and {len(passed) - top_n} more (see the full CSV)")

    lines.append(f"\n🌀 <b>VCP pattern detected ({len(vcp)}):</b>")
    if vcp.empty:
        lines.append("(none today)")
    else:
        for _, r in vcp.head(top_n).iterrows():
            pivot = r.get("pivot")
            dist = r.get("distance_to_pivot_pct")
            pivot_txt = f"  pivot={pivot}  dist={dist}%" if pd.notna(pivot) else ""
            lines.append(f"• {r['symbol']}  close={r['close']}  score={r['vcp_score']:.0f}{pivot_txt}")
        if len(vcp) > top_n:
            lines.append(f"...and {len(vcp) - top_n} more (see the full CSV)")

    return "\n".join(lines)


def send_telegram_message(token: str, chat_id: str, text: str) -> None:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    # Telegram messages are capped at 4096 chars — split into chunks if needed
    max_len = 4000
    chunks = [text[i:i + max_len] for i in range(0, len(text), max_len)] or [text]

    for chunk in chunks:
        resp = requests.post(url, data={
            "chat_id": chat_id,
            "text": chunk,
            "parse_mode": "HTML",
        })
        if resp.status_code != 200:
            print(f"Telegram API error {resp.status_code}: {resp.text}", file=sys.stderr)
            resp.raise_for_status()


def main():
    parser = argparse.ArgumentParser(description="Send screener results to Telegram")
    parser.add_argument("--results", default="thailand_screen_results.csv", help="Path to the results CSV")
    parser.add_argument("--top-n", type=int, default=15, help="Max stocks to list per section")
    args = parser.parse_args()

    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        print("ERROR: set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID environment variables "
              "(as GitHub Actions secrets, or locally with `export` before running).", file=sys.stderr)
        sys.exit(1)

    message = build_message(args.results, args.top_n)
    send_telegram_message(token, chat_id, message)
    print("Sent results to Telegram.")


if __name__ == "__main__":
    main()
