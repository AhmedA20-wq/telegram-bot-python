import os
import json
import time
import requests

# =====================
# ENV
# =====================
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
ODDS_API_KEY = os.getenv("ODDS_API_KEY")
KALSHI_TICKERS_RAW = os.getenv("KALSHI_TICKERS", "{}")

TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
ODDS_API = "https://api.the-odds-api.com/v4/sports/basketball_nba/odds"
KALSHI_MARKET_API_BASE = "https://api.elections.kalshi.com/trade-api/v2/markets"

REQUEST_TIMEOUT = 20
POLL_INTERVAL_SEC = 3
SCAN_INTERVAL_SEC = 60
EDGE_THRESHOLD = 0.04
MAX_TELEGRAM_LEN = 3500

last_update_id = None
last_scan_ts = 0
alerted_keys = set()

try:
    KALSHI_TICKERS = json.loads(KALSHI_TICKERS_RAW)
except Exception:
    KALSHI_TICKERS = {}


# =====================
# BASIC
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
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": cut(text)
    }
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
# ODDS API
# =====================
def get_pinnacle_games():
    params = {
        "apiKey": ODDS_API_KEY,
        "bookmakers": "pinnacle",
        "markets": "h2h",
        "oddsFormat": "american"
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
            "home_price": float(home_price),
            "away_price": float(away_price)
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
            f"{g['away']}: {g['away_price']:.0f}\n"
            f"{g['home']}: {g['home_price']:.0f}"
        )

    return "\n\n".join(lines)


# =====================
# KALSHI
# =====================
def get_kalshi_market(ticker: str):
    url = f"{KALSHI_MARKET_API_BASE}/{ticker}"
    r = requests.get(url, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    data = r.json()

    if isinstance(data, dict) and "market" in data:
        return data["market"]

    return data


def get_kalshi_yes_ask_prob(ticker: str):
    market = get_kalshi_market(ticker)

    yes_ask = market.get("yes_ask")
    if yes_ask is None:
        return None

    return float(yes_ask) / 100.0


# =====================
# MATH
# =====================
def american_to_prob(odds: float) -> float:
    if odds > 0:
        return 100.0 / (odds + 100.0)
    return abs(odds) / (abs(odds) + 100.0)


# =====================
# EDGE LOGIC
# =====================
def find_edges():
    games = get_pinnacle_games()
    edges = []

    for g in games:
        away = g["away"]
        home = g["home"]

        away_ticker = KALSHI_TICKERS.get(away)
        home_ticker = KALSHI_TICKERS.get(home)

        away_prob = american_to_prob(g["away_price"])
        home_prob = american_to_prob(g["home_price"])

        if away_ticker:
            try:
                kalshi_prob = get_kalshi_yes_ask_prob(away_ticker)
                if kalshi_prob is not None:
                    edge = away_prob - kalshi_prob
                    if edge >= EDGE_THRESHOLD:
                        edges.append({
                            "game": f"{away} @ {home}",
                            "team": away,
                            "ticker": away_ticker,
                            "pinnacle_price": g["away_price"],
                            "pinnacle_prob": away_prob,
                            "kalshi_prob": kalshi_prob,
                            "edge": edge
                        })
            except Exception as e:
                print(f"Kalshi error for {away_ticker}: {e}")

        if home_ticker:
            try:
                kalshi_prob = get_kalshi_yes_ask_prob(home_ticker)
                if kalshi_prob is not None:
                    edge = home_prob - kalshi_prob
                    if edge >= EDGE_THRESHOLD:
                        edges.append({
                            "game": f"{away} @ {home}",
                            "team": home,
                            "ticker": home_ticker,
                            "pinnacle_price": g["home_price"],
                            "pinnacle_prob": home_prob,
                            "kalshi_prob": kalshi_prob,
                            "edge": edge
                        })
            except Exception as e:
                print(f"Kalshi error for {home_ticker}: {e}")

    return edges


def edge_command():
    if not KALSHI_TICKERS:
        return "KALSHI_TICKERS is empty."

    edges = find_edges()

    if not edges:
        return "No edge found right now."

    lines = [f"📈 Edges Found ({len(edges)})"]

    for e in edges:
        lines.append(
            f"{e['game']}\n"
            f"Side: {e['team']}\n"
            f"Ticker: {e['ticker']}\n"
            f"Pinnacle ML: {e['pinnacle_price']:.0f}\n"
            f"Pinnacle Prob: {e['pinnacle_prob']:.3f}\n"
            f"Kalshi YES Ask: {e['kalshi_prob']:.3f}\n"
            f"Edge: {e['edge'] * 100:.2f}%"
        )

    return "\n\n".join(lines)


def scan_and_alert():
    if not KALSHI_TICKERS:
        return

    edges = find_edges()

    for e in edges:
        key = f"{e['ticker']}"

        if key in alerted_keys:
            continue

        msg = (
            f"📈 EDGE FOUND\n\n"
            f"{e['game']}\n"
            f"Side: {e['team']}\n"
            f"Ticker: {e['ticker']}\n"
            f"Pinnacle ML: {e['pinnacle_price']:.0f}\n"
            f"Pinnacle Prob: {e['pinnacle_prob']:.3f}\n"
            f"Kalshi YES Ask: {e['kalshi_prob']:.3f}\n"
            f"Edge: {e['edge'] * 100:.2f}%"
        )

        try:
            send_message(msg)
            alerted_keys.add(key)
        except Exception as e2:
            print(f"Telegram alert error: {e2}")


# =====================
# COMMANDS
# =====================
def tickers_command():
    if not KALSHI_TICKERS:
        return "KALSHI_TICKERS is empty."

    lines = ["🎯 Loaded Kalshi tickers"]

    for team, ticker in KALSHI_TICKERS.items():
        lines.append(f"{team}\n{ticker}")

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

        try:
            if cmd == "/start":
                send_message(
                    "Bot running\n\n"
                    "/odds = NBA Pinnacle odds\n"
                    "/tickers = loaded Kalshi tickers\n"
                    "/edge = check current edges"
                )

            elif cmd == "/odds":
                send_message(odds_command())

            elif cmd == "/tickers":
                send_message(tickers_command())

            elif cmd == "/edge":
                send_message(edge_command())

        except Exception as e:
            send_message(f"Command error: {e}")


# =====================
# MAIN
# =====================
def main():
    global last_scan_ts

    must_env()

    try:
        send_message("✅ Kalshi Edge Bot started")
    except Exception as e:
        print("Startup message failed:", e)

    while True:
        try:
            handle_updates()
        except Exception as e:
            print("Update loop error:", e)

        now = time.time()
        if now - last_scan_ts >= SCAN_INTERVAL_SEC:
            try:
                scan_and_alert()
            except Exception as e:
                print("Scan error:", e)
            last_scan_ts = now

        time.sleep(POLL_INTERVAL_SEC)


if __name__ == "__main__":
    main()
