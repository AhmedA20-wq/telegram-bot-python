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
KALSHI_EVENTS_API = "https://api.elections.kalshi.com/trade-api/v2/events"

REQUEST_TIMEOUT = 20
POLL_INTERVAL_SEC = 3
MAX_TELEGRAM_LEN = 3500

last_update_id = None


# =====================
# HELPERS
# =====================
def cut(text: str) -> str:
    if len(text) <= MAX_TELEGRAM_LEN:
        return text
    return text[:MAX_TELEGRAM_LEN] + "\n\n...message cut off"


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


def norm_team(name: str) -> str:
    if not name:
        return ""
    x = name.lower().strip()
    replacements = {
        "trail blazers": "blazers",
        "76ers": "sixers",
        "la clippers": "clippers",
        "los angeles clippers": "clippers",
        "la lakers": "lakers",
        "los angeles lakers": "lakers",
        "new york knicks": "knicks",
        "golden state warriors": "warriors",
        "minnesota timberwolves": "timberwolves",
        "san antonio spurs": "spurs",
        "phoenix suns": "suns",
        "miami heat": "heat",
        "brooklyn nets": "nets",
        "orlando magic": "magic",
        "houston rockets": "rockets",
        "new orleans pelicans": "pelicans",
        "sacramento kings": "kings",
        "toronto raptors": "raptors",
        "detroit pistons": "pistons",
        "chicago bulls": "bulls",
        "utah jazz": "jazz",
        "washington wizards": "wizards",
        "dallas mavericks": "mavericks",
        "boston celtics": "celtics",
        "denver nuggets": "nuggets",
        "charlotte hornets": "hornets",
        "atlanta hawks": "hawks",
        "milwaukee bucks": "bucks",
        "cleveland cavaliers": "cavaliers",
        "memphis grizzlies": "grizzlies",
        "oklahoma city thunder": "thunder",
        "indiana pacers": "pacers",
        "philadelphia 76ers": "sixers",
    }
    for k, v in replacements.items():
        if k in x:
            return v

    parts = x.split()
    return parts[-1] if parts else x


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
# PINNACLE ODDS
# =====================
def get_pinnacle_games():
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

        games.append({
            "home": home,
            "away": away,
            "home_key": norm_team(home),
            "away_key": norm_team(away),
            "home_price": home_price,
            "away_price": away_price,
        })

    return games


def odds_command():
    games = get_pinnacle_games()
    if not games:
        return "No NBA Pinnacle odds found."

    lines = [f"🏀 Pinnacle NBA Odds ({len(games)} games)"]
    for g in games:
        lines.append(
            f"{g['away']} @ {g['home']}\n"
            f"{g['away']}: {g['away_price']}\n"
            f"{g['home']}: {g['home_price']}"
        )

    return "\n\n".join(lines)


# =====================
# KALSHI EVENTS
# =====================
def get_kalshi_open_events():
    params = {
        "status": "open",
        "with_nested_markets": "true",
        "limit": 200,
    }

    all_events = []
    cursor = None

    for _ in range(10):  # enough pages for tonight
        p = dict(params)
        if cursor:
            p["cursor"] = cursor

        r = requests.get(KALSHI_EVENTS_API, params=p, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        data = r.json()

        events = data.get("events", [])
        all_events.extend(events)

        cursor = data.get("cursor")
        if not cursor:
            break

    return all_events


def event_matches_game(event, game):
    text = " ".join([
        str(event.get("title", "")),
        str(event.get("sub_title", "")),
        str(event.get("event_ticker", "")),
    ]).lower()

    return game["home_key"] in text and game["away_key"] in text


def market_kind(title: str):
    t = (title or "").lower()

    if ":" in t:
        return None  # skip player props

    if "points scored" in t or "over " in t or "under " in t:
        return "total"

    if "wins by over" in t:
        return "spread"

    if t.startswith("yes ") or t.startswith("no "):
        if " wins by over" not in t and " points scored" not in t and ":" not in t:
            return "money"

    if " wins" in t and "wins by over" not in t:
        return "money"

    return None


def get_tonight_kalshi_markets():
    games = get_pinnacle_games()
    if not games:
        return []

    events = get_kalshi_open_events()
    found = []

    seen = set()

    for game in games:
        for event in events:
            if not event_matches_game(event, game):
                continue

            event_title = event.get("title", "No event title")
            for m in event.get("markets", []):
                title = m.get("title", "")
                kind = market_kind(title)
                if not kind:
                    continue

                ticker = m.get("ticker")
                yes_ask = m.get("yes_ask")
                yes_bid = m.get("yes_bid")

                if not ticker:
                    continue

                key = (game["away"], game["home"], ticker)
                if key in seen:
                    continue
                seen.add(key)

                found.append({
                    "game": f"{game['away']} @ {game['home']}",
                    "event_title": event_title,
                    "market_title": title,
                    "kind": kind,
                    "ticker": ticker,
                    "yes_bid": yes_bid,
                    "yes_ask": yes_ask,
                })

    return found


def kalshi_command():
    markets = get_tonight_kalshi_markets()

    if not markets:
        return "No matching Kalshi NBA markets found for tonight."

    lines = [f"🎯 Tonight Kalshi NBA Markets ({len(markets)})"]

    count = 0
    for m in markets:
        lines.append(
            f"{m['game']}\n"
            f"{m['kind'].upper()} | {m['market_title']}\n"
            f"{m['ticker']}\n"
            f"yes_bid={m['yes_bid']} yes_ask={m['yes_ask']}"
        )
        count += 1
        if count >= 30:
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

        if str(chat.get("id")) != str(TELEGRAM_CHAT_ID):
            continue

        cmd = text.lower()

        if cmd == "/start":
            send_message(
                "Bot running\n\n"
                "/odds = NBA Pinnacle odds\n"
                "/kalshi = tonight Kalshi NBA markets"
            )

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
