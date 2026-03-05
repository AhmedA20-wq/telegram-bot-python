import os
import requests
import time
from telegram import Bot

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

bot = Bot(token=TOKEN)

def get_markets():
    url = "https://api.elections.kalshi.com/trade-api/v2/markets"
    r = requests.get(url)
    return r.json()["markets"]

def find_edges():
    markets = get_markets()

    edges = []

    for m in markets:
        yes_ask = m.get("yes_ask")
        no_ask = m.get("no_ask")

        if yes_ask and no_ask:
            total = yes_ask + no_ask

            if total < 99:  # inefficiency
                edges.append({
                    "title": m["title"],
                    "yes": yes_ask,
                    "no": no_ask,
                    "edge": 100 - total
                })

    return edges

def send(msg):
    bot.send_message(chat_id=CHAT_ID, text=msg)

send("✅ Kalshi Edge Bot Running")

while True:
    try:
        edges = find_edges()

        for e in edges[:3]:
            send(
                f"🚨 Edge Found\n\n"
                f"{e['title']}\n"
                f"YES ask: {e['yes']}\n"
                f"NO ask: {e['no']}\n"
                f"Edge: {e['edge']}%"
            )

    except Exception as e:
        send(f"Error: {e}")

    time.sleep(120)
