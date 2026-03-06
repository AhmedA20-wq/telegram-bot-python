import os
import time
import requests

# =====================
# ENV
# =====================
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
ODDS_API_KEY = os.getenv("ODDS_API_KEY")

TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
ODDS_API = "https://api.the-odds-api.com/v4/sports/basketball_nba/odds"
KALSHI_MARKETS_API = "https://api.elections.kalshi.com/trade-api/v2/markets"

REQUEST_TIMEOUT = 20
POLL_INTERVAL_SEC = 3
MAX_TELEGRAM_LEN = 3500

last_update_id = None


# =====================
# HELPERS
# =====================
def must_env():
    missing = []
    if not TELEGRAM_BOT_TOKEN:
        missing.append("TELEGRAM_BOT_TOKEN")
    if not TELEGRAM_CHAT_ID:
        missing.append("TELEGRAM_CHAT_ID")
    if not ODDS_API_KEY:
        missing.append("ODDS_API_KEY")
    if missing:
        raise RuntimeError("Missing env vars: " + ", ".join(missing))


def cut(text: str) -> str:
    if len(text) <= MAX_TELEGRAM_LEN:
        return text
    return text[:MAX_TELEGRAM_LEN] + "\n\n...message cut off"


# =====================
# TELEGRAM
# =====================
def send_message(text: str):
    url = f"{TELEGRAM_API}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": cut(text)}
    r = requests.post(url, json=payload, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    return r.json()


def get_updates():
    global last_update_id
    url = f"{TELEGRAM_API}/getUpdates"
    params = {"timeout": 1}
    if last_update_id is not None:
        params["offset"] = last_update_id + 1

    r = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    data = r.json()
    if not data.get("ok"):
        return []
    return data.get("result", [])


# =====================
# ODDS API (PINNACLE)
# =====================
def get_pinnacle_odds():
    params = {
        "apiKey": ODDS_API_KEY,
        "bookmakers": "pinnacle",
        "markets": "h2h",
        "oddsFormat": "american",
    }

    r = requests.get(ODDS_API, params=params, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    data = r.json()

    games = []
    for event in data:
        home = event.get("home_team")
        away = event.get("away_team")
        bookmakers = event.get("bookmakers", [])
        if not home or not away or not bookmakers:
            continue

        markets = bookmakers[0].get("markets", [])
        if not markets:
            continue

        outcomes = markets[0].get("outcomes", [])
        if len(outcomes) < 2:
            continue

        prices = {}
        for o in outcomes:
            name = o.get("name")
            price = o.get("price")
            if name is not None and price is not None:
                prices[name] = price

        home_price = prices.get(home)
        away_price = prices.get(away)
        if home_price is None or away_price is None:
            continue

        games.append(
            {
                "home": home,
                "away": away,
                "home_price": home_price,
                "away_price": away_price,
            }
        )

    return games


def odds_command():
    games = get_pinnacle_odds()
    if not games:
        return "No NBA Pinnacle odds found."

    lines = [f"🏀 Pinnacle NBA Odds (games={len(games)})"]
    for g in games:
        lines.append(
            f"{g['away']} @ {g['home']}\n"
            f"{g['away']}: {g['away_price']}\n"
            f"{g['home']}: {g['home_price']}"
        )

    return "\n\n".join(lines)


# =====================
# KALSHI
# =====================
def get_kalshi_markets(status="open", limit=200):
    # Kalshi supports market data endpoints; we’ll just fetch and print what we get.
    params = {
        "status": status,
        "limit": limit,
    }
    r = requests.get(KALSHI_MARKETS_API, params=params, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    data = r.json()
    return data.get("markets", [])


def kalshi_command():
    markets = get_kalshi_markets(status="open", limit=200)

    if not markets:
        return "Kalshi returned 0 open markets (unexpected)."

    lines = [f"🎯 Kalshi Markets (open={len(markets)})", "Showing first 25:"]

    shown = 0
    for m in markets:
        title = m.get("title") or "NO TITLE"
        ticker = m.get("ticker") or "NO TICKER"

        # Kalshi often uses cents; sometimes fields differ by endpoint/version.
        yes_ask = m.get("yes_ask")
        yes_bid = m.get("yes_bid")
        last_price = m.get("last_price")

        price_bits = []
        if yes_bid is not None:
            price_bits.append(f"yes_bid={yes_bid}")
        if yes_ask is not None:
            price_bits.append(f"yes_ask={yes_ask}")
        if last_price is not None and not price_bits:
            price_bits.append(f"last={last_price}")
        if not price_bits:
            price_bits.append("no price fields")

        lines.append(f"{title}\n{ticker}\n{', '.join(price_bits)}")

        shown += 1
        if shown >= 25:
            break

    return "\n\n".join(lines)


# =====================
# TELEGRAM HANDLER
# =====================
def handle_updates():
    global last_update_id

    updates = get_updates()
    for update in updates:
        last_update_id = update["update_id"]

        msg = update.get("message", {})
        chat = msg.get("chat", {})
        text = (msg.get("text") or "").strip()

        # only respond in your configured chat
        if str(chat.get("id")) != str(TELEGRAM_CHAT_ID):
            continue

        cmd = text.lower()

        if cmd == "/start":
            send_message("Bot running\n\n/odds = NBA odds (Pinnacle)\n/kalshi = show first 25 Kalshi markets")

        elif cmd == "/odds":
            send_message(odds_command())

        elif cmd == "/kalshi":
            send_message(kalshi_command())


# =====================
# MAIN
# =====================
def main():
    must_env()
    try:
        send_message("✅ Bot started")
    except Exception as e:
        print("Startup message failed:", e)

    while True:
        try:
            handle_updates()
        except Exception as e:
            print("Loop error:", e)

        time.sleep(POLL_INTERVAL_SEC)


if __name__ == "__main__":
    main()
