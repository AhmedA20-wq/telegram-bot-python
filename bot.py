import os
import json
import asyncio
from datetime import datetime

import gspread
from google.oauth2.service_account import Credentials

from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

SHEET_ID = os.getenv("GOOGLE_SHEET_ID")
SHEET_TAB = os.getenv("GOOGLE_SHEET_TAB", "BETS")
SA_JSON = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")


def get_sheet():
    if not SA_JSON:
        raise RuntimeError("Missing GOOGLE_SERVICE_ACCOUNT_JSON")
    if not SHEET_ID:
        raise RuntimeError("Missing GOOGLE_SHEET_ID")

    info = json.loads(SA_JSON)
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_info(info, scopes=scopes)
    client = gspread.authorize(creds)
    sh = client.open_by_key(SHEET_ID)
    ws = sh.worksheet(SHEET_TAB)
    return ws


async def send_telegram(app, text: str):
    # Sends a message even if you didn't /start recently (because we use CHAT_ID)
    if not CHAT_ID:
        return
    await app.bot.send_message(chat_id=int(CHAT_ID), text=text)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("✅ Bot is running.")


async def test_sheet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ws = get_sheet()

    # Add a test row to your BETS tab
    now = datetime.utcnow().isoformat()
    row = [
        f"test_{int(datetime.utcnow().timestamp())}",  # bet_id
        now,                                          # timestamp
        "TEST_TICKER",                                # ticker
        0,                                            # edge_cents
        "",                                           # yes_ask
        "",                                           # no_ask
        0,                                            # suggested_bet
        "",                                           # bankroll_before
        "TEST",                                       # action
        "",                                           # result
        "",                                           # profit_loss
        "",                                           # bankroll_after
    ]
    ws.append_row(row, value_input_option="USER_ENTERED")

    await update.message.reply_text("✅ Wrote a test row to Google Sheets.")
    await send_telegram(context.application, "✅ Kalshi Edge Bot: Google Sheets connected (test row written).")


async def on_startup(app):
    # Sends you a message on deploy/restart (this is the “automatic notification” part)
    await send_telegram(app, "✅ Kalshi Edge Bot is ONLINE")


def main():
    if not TOKEN:
        raise RuntimeError("Missing TELEGRAM_BOT_TOKEN")

    app = ApplicationBuilder().token(TOKEN).post_init(on_startup).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("testsheet", test_sheet))
    app.run_polling()


if __name__ == "__main__":
    main()
