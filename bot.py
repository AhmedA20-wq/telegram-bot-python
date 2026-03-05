import os
import time
import requests

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# Optional for later (keep it for now)
KALSHI_TICKER = os.getenv("KALSHI_TICKER", "")

def tg_send(text: str):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        raise Exception("Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID")

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text}
    r = requests.post(url, data=payload, timeout=20)
    if r.status_code != 200:
        raise Exception(f"Telegram error {r.status_code}: {r.text}")

def main():
    tg_send("✅ Kalshi Edge Bot is ONLINE")

    # keepalive loop (we’ll replace this with Kalshi scanning next)
    while True:
        time.sleep(60)

if __name__ == "__main__":
    main()
