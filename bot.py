import os
import json
import time
import requests
from datetime import datetime

import gspread
from google.oauth2.service_account import Credentials


# TELEGRAM
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# GOOGLE SHEETS
SHEET_ID = os.getenv("GOOGLE_SHEET_ID")
SHEET_TAB = os.getenv("GOOGLE_SHEET_TAB", "BETS")
SERVICE_ACCOUNT_JSON = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")


def send_telegram(message):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": message}
    requests.post(url, json=payload)


def get_sheet():
    creds_dict = json.loads(SERVICE_ACCOUNT_JSON)

    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]

    creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
    client = gspread.authorize(creds)

    sheet = client.open_by_key(SHEET_ID)
    return sheet.worksheet(SHEET_TAB)


def write_test_row():
    sheet = get_sheet()
    row = [
        f"test_{int(time.time())}",
        datetime.utcnow().isoformat(),
        "TEST_TICKER",
        0,
        "",
        "",
        0,
        "",
        "TEST",
        "",
        "",
        "",
    ]
    sheet.append_row(row)


def main():
    send_telegram("✅ Kalshi Edge Bot is ONLINE")

    try:
        write_test_row()
        send_telegram("✅ Google Sheets connection SUCCESS")
    except Exception as e:
        send_telegram(f"❌ Sheets error: {str(e)}")

    while True:
        time.sleep(60)


if __name__ == "__main__":
    main()
