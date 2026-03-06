import os
import json
import time
import requests

# =========================
# ENV VARS
# =========================
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
ODDS_API_KEY = os.getenv("ODDS_API_KEY")
KALSHI_TICKERS_RAW = os.getenv("KALSHI_TICKERS", "{}")

# Example KALSHI_TICKERS env var:
# {
#   "Los Angeles Lakers": "YOUR_LAKERS_TICKER",
#   "Boston Celtics": "YOUR_CELTICS_TICKER",
#   "Miami Heat": "YOUR_HEAT_TICKER",
#   "New York Knicks": "YOUR_KNICKS_TICKER"
# }

try:
    KALSHI_TICKERS = json.loads(KALSHI_TICKERS_RAW)
except Exception:
    KALSHI_TICKERS = {}

# =========================
# SETTINGS
# =========================
ODDS_API_URL = "https://api.the-odds-api.com/v4/sports/basketball_nba/odds"
KALSHI_API_BASE = "https://api.elections.kalshi.com/trade-api/v2"
TELEGRAM_API_BASE = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

EDGE_THRESHOLD = 0.03       # 3%
SCAN_INTERVAL = 120         # seconds
COMMAND_POLL_INTERVAL = 5   # seconds
REQUEST_TIMEOUT = 20

# Prevent duplicate alert spam
already_alerted = set()

# Telegram getUpdates offset tracking
last_update_id = None


# =========================
# BASIC VALIDATION
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
        raise RuntimeError(f"Missing env vars: {', '.join(missing)}")


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
    params = {
        "timeout": 1
    }

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

        market = markets[0]
        outcomes = market.get("outcomes", [])
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
            "id": event.get("id"),
            "home_team": home_team,
            "away_team": away_team,
            "commence_time": event.get("commence_time"),
            "home_price": home_price,
            "away_price": away_price
        })

    return games


# =========================
# KALSHI
# =========================
def get_kalshi_market(ticker):
    url = f"{KALSHI_API_BASE}/markets/{ticker}"
    r = requests.get(url, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    data = r.json()

    # Handle either {"market": {...}} or direct object
    if isinstance(data, dict) and "market" in data:
        return data["market"]

    return data


def get_kalshi_yes_ask_prob(ticker):
    market = get_kalshi_market(ticker)
    yes_ask = market.get("yes_ask")

    if yes_ask is None:
        return None

    return float(yes_ask) / 100.0


# =========================
# MATH
# =========================
def american_to_prob(odds):
    odds = float(odds)
    if odds > 0:
        return 100 / (odds + 100)
    return abs(odds) / (abs(odds) + 100)


# =========================
# COMMAND OUTPUTS
# =========================
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

    return "\n\n".join(lines)


def find_edges():
    games = get_nba_pinnacle_games()
    edges = []

    for g in games:
        home_team = g["home_team"]
        away_team = g["away_team"]

        home_prob = american_to_prob(g["home_price"])
        away_prob = american_to_prob(g["away_price"])

        home_ticker = KALSHI_TICKERS.get(home_team)
        away_ticker = KALSHI_TICKERS.get(away_team)

        # Home side
        if home_ticker:
            try:
                kalshi_prob = get_kalshi_yes_ask_prob(home_ticker)
                if kalshi_prob is not None:
                    edge = home_prob - kalshi_prob
                    if edge >= EDGE_THRESHOLD:
                        edges.append({
                            "game": f"{away_team} @ {home_team}",
                            "team": home_team,
                            "ticker": home_ticker,
                            "pinnacle_price": g["home_price"],
                            "pinnacle_prob": home_prob,
                            "kalshi_prob": kalshi_prob,
                            "edge": edge
                        })
            except Exception as e:
                print(f"Kalshi lookup failed for {home_ticker}: {e}")

        # Away side
        if away_ticker:
            try:
                kalshi_prob = get_kalshi_yes_ask_prob(away_ticker)
                if kalshi_prob is not None:
                    edge = away_prob - kalshi_prob
                    if edge >= EDGE_THRESHOLD:
                        edges.append({
                            "game": f"{away_team} @ {home_team}",
                            "team": away_team,
                            "ticker": away_ticker,
                            "pinnacle_price": g["away_price"],
                            "pinnacle_prob": away_prob,
                            "kalshi_prob": kalshi_prob,
                            "edge": edge
                        })
            except Exception as e:
                print(f"Kalshi lookup failed for {away_ticker}: {e}")

    return edges


def edge_command():
    edges = find_edges()

    if not edges:
        return "No edge found right now."

    lines = ["📈 Kalshi Edges Found"]

    for e in edges:
        lines.append(
            f"{e['game']}\n"
            f"Side: {e['team']}\n"
            f"Ticker: {e['ticker']}\n"
            f"Pinnacle ML: {e['pinnacle_price']}\n"
            f"Pinnacle Prob: {e['pinnacle_prob']:.3f}\n"
            f"Kalshi YES Ask: {e['kalshi_prob']:.3f}\n"
            f"Edge: {e['edge'] * 100:.2f}%"
        )

    return "\n\n".join(lines)


# =========================
# ALERT SCAN
# =========================
def scan_and_alert():
    edges = find_edges()

    for e in edges:
        key = f"{e['ticker']}"

        if key in already_alerted:
            continue

        msg = (
            f"📈 EDGE FOUND\n\n"
            f"{e['game']}\n"
            f"Side: {e['team']}\n"
            f"Ticker: {e['ticker']}\n"
            f"Pinnacle ML: {e['pinnacle_price']}\n"
            f"Pinnacle Prob: {e['pinnacle_prob']:.3f}\n"
            f"Kalshi YES Ask: {e['kalshi_prob']:.3f}\n"
            f"Edge: {e['edge'] * 100:.2f}%"
        )

        try:
            send_telegram_message(msg)
            already_alerted.add(key)
            print(f"Alert sent for {key}")
        except Exception as e2:
            print(f"Failed to send alert for {key}: {e2}")


# =========================
# TELEGRAM COMMAND HANDLER
# =========================
def handle_updates():
    global last_update_id

    updates = get_telegram_updates()

    for update in updates:
        last_update_id = update["update_id"]

        message = update.get("message", {})
        chat = message.get("chat", {})
        text = message.get("text", "")

        # Only respond to your configured chat id
        if str(chat.get("id")) != str(TELEGRAM_CHAT_ID):
            continue

        text = text.strip().lower()

        try:
            if text == "/start":
                send_telegram_message(
                    "Bot is running.\n\n"
                    "Commands:\n"
                    "/odds - show all NBA Pinnacle odds\n"
                    "/edge - check current Kalshi edges"
                )

            elif text == "/odds":
                send_telegram_message(odds_command())

            elif text == "/edge":
                send_telegram_message(edge_command())

        except Exception as e:
            send_telegram_message(f"Command error: {e}")


# =========================
# MAIN LOOP
# =========================
def main():
    validate_env()
    print("Bot starting...")

    try:
        send_telegram_message("Kalshi Edge Bot is live.")
    except Exception as e:
        print(f"Startup Telegram send failed: {e}")

    last_scan_time = 0

    while True:
        try:
            handle_updates()
        except Exception as e:
            print(f"Update handler error: {e}")

        now = time.time()

        if now - last_scan_time >= SCAN_INTERVAL:
            try:
                scan_and_alert()
            except Exception as e:
                print(f"Scan error: {e}")
            last_scan_time = now

        time.sleep(COMMAND_POLL_INTERVAL)


if __name__ == "__main__":
    main()
