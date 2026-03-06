import os
import time
import requests

# =========================
# ENV
# =========================
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
ODDS_API_KEY = os.getenv("ODDS_API_KEY")

TELEGRAM_API_BASE = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
ODDS_API_URL = "https://api.the-odds-api.com/v4/sports/basketball_nba/odds"
KALSHI_API_BASE = "https://api.elections.kalshi.com/trade-api/v2"

REQUEST_TIMEOUT = 20
COMMAND_POLL_INTERVAL = 5

last_update_id = None


# =========================
# BASIC CHECK
# =========================
def validate_env():
    missing = []
    if not TELEGRAM_BOT_TOKEN:
        missing.append("TELEGRAM_BOT_TOKEN")
    if not TELEGRAM_CHAT_ID:
        missing.append("TELEGRAM_CHAT_ID")
    if not ODDS_API_KEY:
        missing.append("ODDS_API_KEY")

    if missing:
        raise RuntimeError("Missing env vars: " + ", ".join(missing))


# =========================
# TELEGRAM
# =========================
def send_telegram_message(text):
    url = f"{TELEGRAM_API_BASE}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text
    }
    r = requests.post(url, json=payload, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    return r.json()


def get_telegram_updates():
    global last_update_id

    url = f"{TELEGRAM_API_BASE}/getUpdates"
    params = {"timeout": 1}

    if last_update_id is not None:
        params["offset"] = last_update_id + 1

    r = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    data = r.json()

    if not data.get("ok"):
        return []

    return data.get("result", [])


# =========================
# ODDS API
# =========================
def get_nba_pinnacle_games():
    params = {
        "apiKey": ODDS_API_KEY,
        "bookmakers": "pinnacle",
        "markets": "h2h",
        "oddsFormat": "american"
    }

    r = requests.get(ODDS_API_URL, params=params, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    data = r.json()

    games = []

    for event in data:
        home_team = event.get("home_team")
        away_team = event.get("away_team")
        bookmakers = event.get("bookmakers", [])

        if not home_team or not away_team or not bookmakers:
            continue

        bookmaker = bookmakers[0]
        markets = bookmaker.get("markets", [])
        if not markets:
            continue

        outcomes = markets[0].get("outcomes", [])
        if len(outcomes) < 2:
            continue

        prices = {}
        for outcome in outcomes:
            name = outcome.get("name")
            price = outcome.get("price")
            if name is not None and price is not None:
                prices[name] = price

        home_price = prices.get(home_team)
        away_price = prices.get(away_team)

        if home_price is None or away_price is None:
            continue

        games.append({
            "home_team": home_team,
            "away_team": away_team,
            "home_price": home_price,
            "away_price": away_price
        })

    return games


def odds_command():
    games = get_nba_pinnacle_games()

    if not games:
        return "No NBA Pinnacle odds found."

    lines = ["🏀 Pinnacle NBA Odds"]

    for g in games:
        lines.append(
            f"{g['away_team']} @ {g['home_team']}\n"
            f"{g['away_team']}: {g['away_price']}\n"
            f"{g['home_team']}: {g['home_price']}"
        )

    msg = "\n\n".join(lines)

    if len(msg) > 3500:
        msg = msg[:3500] + "\n\n...message cut off"

    return msg


# =========================
# KALSHI
# =========================
def get_kalshi_markets():
    url = f"{KALSHI_API_BASE}/markets"
    params = {
        "status": "open"
    }

    r = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    data = r.json()

    return data.get("markets", [])


def is_basketball_market(market):

    title = str(market.get("title","")).lower()

    # ignore combo or multi markets
    if "combo" in title:
        return False

    if "multi" in title:
        return False

    if "crosscategory" in title:
        return False

    # ignore player props
    if ":" in title:
        return False

    # include real betting markets
    keywords = [
        "wins",
        "points scored",
        "over",
        "under",
        "spread"
    ]

    for k in keywords:
        if k in title:
            return True

    return False
    text = " ".join([
        str(market.get("title", "")),
        str(market.get("subtitle", "")),
        str(market.get("category", "")),
        str(market.get("event_ticker", "")),
        str(market.get("ticker", ""))
    ]).lower()

    basketball_words = [
        "basketball", "nba", "pro basketball", "points scored",
        "money", "spread", "total"
    ]

    return any(word in text for word in basketball_words)


def market_price_text(market):
    yes_ask = market.get("yes_ask")
    yes_price = market.get("yes_price")
    last_price = market.get("last_price")

    if yes_ask is not None:
        return f"yes_ask={yes_ask}"
    if yes_price is not None:
        return f"yes_price={yes_price}"
    if last_price is not None:
        return f"last_price={last_price}"
    return "no price"


def kalshi_command():
    markets = get_kalshi_markets()
    basketball_markets = [m for m in markets if is_basketball_market(m)]

    if not basketball_markets:
        return "No open basketball Kalshi markets found."

    lines = ["🎯 Kalshi basketball markets"]

    count = 0
    for m in basketball_markets:
        title = m.get("title", "No title")
        ticker = m.get("ticker", "No ticker")
        price_text = market_price_text(m)

        lines.append(f"{title}\n{ticker}\n{price_text}")
        count += 1

        if count >= 25:
            break

    msg = "\n\n".join(lines)

    if len(msg) > 3500:
        msg = msg[:3500] + "\n\n...message cut off"

    return msg


# =========================
# COMMAND HANDLER
# =========================
def handle_updates():
    global last_update_id

    updates = get_telegram_updates()

    for update in updates:
        last_update_id = update["update_id"]

        message = update.get("message", {})
        chat = message.get("chat", {})
        text = message.get("text", "")

        if str(chat.get("id")) != str(TELEGRAM_CHAT_ID):
            continue

        text = text.strip().lower()

        try:
            if text == "/start":
                send_telegram_message(
                    "Bot is running.\n\n"
                    "/odds = all NBA Pinnacle odds\n"
                    "/kalshi = Kalshi basketball titles + tickers"
                )

            elif text == "/odds":
                send_telegram_message(odds_command())

            elif text == "/kalshi":
                send_telegram_message(kalshi_command())

        except Exception as e:
            send_telegram_message(f"Error: {e}")


# =========================
# MAIN
# =========================
def main():
    validate_env()
    print("Bot starting...")

    try:
        send_telegram_message("Ticker bot is live.")
    except Exception as e:
        print(f"Startup Telegram failed: {e}")

    while True:
        try:
            handle_updates()
        except Exception as e:
            print(f"Loop error: {e}")

        time.sleep(COMMAND_POLL_INTERVAL)


if __name__ == "__main__":
    main()
