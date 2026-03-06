import os
import time
import requests

# =====================
# ENV VARIABLES
# =====================

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
ODDS_API_KEY = os.getenv("ODDS_API_KEY")

TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
ODDS_API = "https://api.the-odds-api.com/v4/sports/basketball_nba/odds"
KALSHI_API = "https://api.elections.kalshi.com/trade-api/v2/markets"

REQUEST_TIMEOUT = 20
last_update_id = None


# =====================
# TELEGRAM
# =====================

def send_message(text):

    url = f"{TELEGRAM_API}/sendMessage"

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text
    }

    r = requests.post(url, json=payload, timeout=REQUEST_TIMEOUT)

    return r.json()


def get_updates():

    global last_update_id

    url = f"{TELEGRAM_API}/getUpdates"

    params = {}

    if last_update_id:
        params["offset"] = last_update_id + 1

    r = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)

    data = r.json()

    if not data["ok"]:
        return []

    return data["result"]


# =====================
# ODDS API
# =====================

def get_pinnacle_odds():

    params = {
        "apiKey": ODDS_API_KEY,
        "bookmakers": "pinnacle",
        "markets": "h2h",
        "oddsFormat": "american"
    }

    r = requests.get(ODDS_API, params=params)

    data = r.json()

    games = []

    for event in data:

        home = event["home_team"]
        away = event["away_team"]

        bookmaker = event["bookmakers"][0]

        outcomes = bookmaker["markets"][0]["outcomes"]

        prices = {}

        for o in outcomes:

            prices[o["name"]] = o["price"]

        games.append(
            {
                "home": home,
                "away": away,
                "home_price": prices.get(home),
                "away_price": prices.get(away)
            }
        )

    return games


# =====================
# KALSHI MARKETS
# =====================

def get_kalshi_markets():

    params = {
        "status": "open"
    }

    r = requests.get(KALSHI_API, params=params)

    data = r.json()

    return data.get("markets", [])


def is_real_game_market(market):

    ticker = str(market.get("ticker","")).lower()

    # remove combo markets
    if "crosscategory" in ticker:
        return False

    if "multigame" in ticker:
        return False

    title = str(market.get("title","")).lower()

    # remove player props
    if ":" in title:
        return False

    # allow spreads / totals / wins
    keywords = [
        "wins",
        "points scored",
        "over",
        "under"
    ]

    for k in keywords:
        if k in title:
            return True

    return False


# =====================
# COMMANDS
# =====================

def odds_command():

    games = get_pinnacle_odds()

    lines = ["NBA Pinnacle Odds"]

    for g in games:

        lines.append(
            f"{g['away']} @ {g['home']}\n"
            f"{g['away']}: {g['away_price']}\n"
            f"{g['home']}: {g['home_price']}"
        )

    return "\n\n".join(lines)


def kalshi_command():

    markets = get_kalshi_markets()

    filtered = []

    for m in markets:

        if is_real_game_market(m):

            filtered.append(m)

    lines = ["Kalshi NBA Markets"]

    count = 0

    for m in filtered:

        title = m.get("title")
        ticker = m.get("ticker")
        price = m.get("yes_ask")

        lines.append(
            f"{title}\n{ticker}\nyes_ask={price}"
        )

        count += 1

        if count >= 20:
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

        message = update.get("message", {})

        chat = message.get("chat", {})

        text = message.get("text", "")

        if str(chat.get("id")) != str(TELEGRAM_CHAT_ID):
            continue

        text = text.lower().strip()

        if text == "/start":

            send_message(
                "Bot running\n\n"
                "/odds = NBA odds\n"
                "/kalshi = Kalshi markets"
            )

        if text == "/odds":

            send_message(odds_command())

        if text == "/kalshi":

            send_message(kalshi_command())


# =====================
# MAIN LOOP
# =====================

def main():

    send_message("Bot started")

    while True:

        try:

            handle_updates()

        except Exception as e:

            print(e)

        time.sleep(3)


if __name__ == "__main__":

    main()
