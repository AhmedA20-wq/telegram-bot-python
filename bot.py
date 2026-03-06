import os
import json
import time
import math
import requests
from datetime import datetime, timezone

# =========================
# OPTIONAL GOOGLE SHEETS IMPORTS
# =========================
SHEETS_ENABLED = True
try:
    import gspread
    try:
        # gspread >= 5.7
        from gspread import service_account_from_dict
    except Exception:
        service_account_from_dict = None
except Exception:
    SHEETS_ENABLED = False
    gspread = None
    service_account_from_dict = None

# =========================
# ENV
# =========================
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
ODDS_API_KEY = os.getenv("ODDS_API_KEY", "").strip()
GOOGLE_SERVICE_ACCOUNT_JSON = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
SHEET_ID = os.getenv("SHEET_ID", "").strip()
GOOGLE_SHEET_TAB = os.getenv("GOOGLE_SHEET_TAB", "BETS").strip()
KALSHI_TICKERS_RAW = os.getenv("KALSHI_TICKERS", "{}").strip()
EDGE_PCT = float(os.getenv("EDGE_PCT", "0.04"))
POLL_SECONDS = int(os.getenv("POLL_SECONDS", "30"))

# =========================
# CONSTANTS
# =========================
TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
ODDS_API_URL = "https://api.the-odds-api.com/v4/sports/basketball_nba/odds"
KALSHI_MARKET_URL = "https://api.elections.kalshi.com/trade-api/v2/markets"
REQUEST_TIMEOUT = 20
MAX_TG_LEN = 3500

# Prevent duplicate alert spam in memory
SEEN_ALERT_KEYS = set()
LAST_SCAN_TS = 0
LAST_UPDATE_ID = None

# =========================
# PARSE KALSHI TICKERS
# =========================
try:
    KALSHI_TICKERS = json.loads(KALSHI_TICKERS_RAW) if KALSHI_TICKERS_RAW else {}
except Exception:
    KALSHI_TICKERS = {}

# Expected format:
# {
#   "Brooklyn Nets @ Miami Heat": {
#       "ticker": "KALSHI_TICKER",
#       "yes_team": "Miami Heat"
#   }
# }

# =========================
# HELPERS
# =========================
def now_iso():
    return datetime.now(timezone.utc).isoformat()

def cut(text: str) -> str:
    if len(text) <= MAX_TG_LEN:
        return text
    return text[:MAX_TG_LEN] + "\n\n...message cut off"

def safe_float(value, default=None):
    try:
        return float(value)
    except Exception:
        return default

def must_env():
    missing = []
    for name, value in [
        ("TELEGRAM_BOT_TOKEN", TELEGRAM_BOT_TOKEN),
        ("TELEGRAM_CHAT_ID", TELEGRAM_CHAT_ID),
        ("ODDS_API_KEY", ODDS_API_KEY),
    ]:
        if not value:
            missing.append(name)

    if missing:
        raise RuntimeError("Missing env vars: " + ", ".join(missing))

def american_to_prob(odds: float) -> float:
    odds = float(odds)
    if odds > 0:
        return 100.0 / (odds + 100.0)
    return abs(odds) / (abs(odds) + 100.0)

def pct(x: float) -> str:
    return f"{x * 100:.2f}%"

def cents_to_prob(cents):
    if cents is None:
        return None
    return float(cents) / 100.0

def suggested_bet_size(bankroll: float, edge: float) -> float:
    """
    Simple tiered bankroll sizing.
    Conservative by design.
    """
    if bankroll is None or bankroll <= 0:
        return 0.0

    if edge >= 0.10:
        pct_size = 0.03
    elif edge >= 0.07:
        pct_size = 0.02
    elif edge >= 0.04:
        pct_size = 0.01
    else:
        pct_size = 0.005

    stake = bankroll * pct_size
    return round(stake, 2)

def yes_profit_from_stake(stake: float, price_prob: float) -> float:
    """
    If you spend 'stake' dollars buying YES at price p,
    contracts = stake / p, settlement payout = contracts * 1.
    Profit = stake * (1/p - 1)
    Ignoring fees.
    """
    if stake <= 0 or price_prob <= 0:
        return 0.0
    return round(stake * ((1.0 / price_prob) - 1.0), 2)

def build_bet_id(ticker: str, side: str) -> str:
    ts = int(time.time())
    short = ticker[-8:] if ticker else "UNKNOWN"
    return f"{short}-{side}-{ts}"

# =========================
# TELEGRAM
# =========================
def send_message(text: str):
    url = f"{TELEGRAM_API}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": cut(text),
    }
    r = requests.post(url, json=payload, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    return r.json()

def get_updates():
    global LAST_UPDATE_ID

    url = f"{TELEGRAM_API}/getUpdates"
    params = {"timeout": 1}
    if LAST_UPDATE_ID is not None:
        params["offset"] = LAST_UPDATE_ID + 1

    r = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    data = r.json()

    if not data.get("ok"):
        return []

    return data.get("result", [])

# =========================
# GOOGLE SHEETS
# =========================
SHEET_HEADERS = [
    "bet_id",
    "timestamp",
    "market",
    "ticker",
    "game",
    "team",
    "side",
    "edge_pct",
    "yes_ask",
    "no_ask",
    "pinnacle_price",
    "pinnacle_prob",
    "kalshi_prob",
    "suggested_bet",
    "bankroll_before",
    "action",
    "result",
    "profit_loss",
    "bankroll_after",
    "paper_result",
    "paper_profit",
    "notes",
]

def get_ws():
    if not SHEETS_ENABLED:
        return None
    if not GOOGLE_SERVICE_ACCOUNT_JSON or not SHEET_ID:
        return None

    info = json.loads(GOOGLE_SERVICE_ACCOUNT_JSON)

    if service_account_from_dict is not None:
        gc = service_account_from_dict(info)
    else:
        from google.oauth2.service_account import Credentials
        scopes = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
        ]
        creds = Credentials.from_service_account_info(info, scopes=scopes)
        gc = gspread.authorize(creds)

    sh = gc.open_by_key(SHEET_ID)
    ws = sh.worksheet(GOOGLE_SHEET_TAB)
    return ws

def ensure_sheet_headers():
    ws = get_ws()
    if ws is None:
        return

    existing = ws.row_values(1)
    if existing != SHEET_HEADERS:
        if not existing:
            ws.append_row(SHEET_HEADERS)
        else:
            # only rewrite if headers are missing badly
            for i, val in enumerate(SHEET_HEADERS, start=1):
                ws.update_cell(1, i, val)

def append_sheet_row(row_dict: dict):
    ws = get_ws()
    if ws is None:
        return

    row = [row_dict.get(h, "") for h in SHEET_HEADERS]
    ws.append_row(row, value_input_option="USER_ENTERED")

def get_all_rows():
    ws = get_ws()
    if ws is None:
        return []
    return ws.get_all_records()

def find_row_index_by_bet_id(bet_id: str):
    ws = get_ws()
    if ws is None:
        return None, None

    rows = ws.get_all_records()
    for i, row in enumerate(rows, start=2):  # row 1 is header
        if str(row.get("bet_id", "")).strip() == str(bet_id).strip():
            return ws, i
    return ws, None

def update_row_fields(bet_id: str, updates: dict):
    ws, row_index = find_row_index_by_bet_id(bet_id)
    if ws is None or row_index is None:
        return False

    header_map = {h: idx + 1 for idx, h in enumerate(SHEET_HEADERS)}

    for key, value in updates.items():
        if key in header_map:
            ws.update_cell(row_index, header_map[key], value)

    return True

def get_latest_bankroll():
    rows = get_all_rows()
    latest = None

    for row in rows:
        ba = row.get("bankroll_after")
        if ba not in ("", None):
            try:
                latest = float(ba)
            except Exception:
                pass

    return latest

def log_bankroll_set(bankroll: float):
    row = {h: "" for h in SHEET_HEADERS}
    row["bet_id"] = f"BANKROLL-{int(time.time())}"
    row["timestamp"] = now_iso()
    row["market"] = "SYSTEM"
    row["notes"] = "manual bankroll set"
    row["action"] = "set_bankroll"
    row["bankroll_after"] = bankroll
    append_sheet_row(row)

def get_open_bets():
    rows = get_all_rows()
    open_rows = []
    for row in rows:
        market = str(row.get("market", "")).strip().upper()
        bet_id = str(row.get("bet_id", "")).strip()
        action = str(row.get("action", "")).strip().lower()
        result = str(row.get("result", "")).strip().lower()

        if not bet_id or market == "SYSTEM":
            continue

        if action == "took" and result == "":
            open_rows.append(row)
    return open_rows

def get_stats_summary():
    rows = get_all_rows()

    alert_count = 0
    took_count = 0
    pass_count = 0
    actual_pnl = 0.0
    paper_pnl = 0.0

    for row in rows:
        market = str(row.get("market", "")).strip().upper()
        if market == "SYSTEM":
            continue

        if str(row.get("bet_id", "")).strip():
            alert_count += 1

        action = str(row.get("action", "")).strip().lower()
        if action == "took":
            took_count += 1
        elif action == "pass":
            pass_count += 1

        try:
            actual_pnl += float(row.get("profit_loss") or 0)
        except Exception:
            pass

        try:
            paper_pnl += float(row.get("paper_profit") or 0)
        except Exception:
            pass

    bankroll = get_latest_bankroll()

    return {
        "alerts": alert_count,
        "took": took_count,
        "passed": pass_count,
        "actual_pnl": round(actual_pnl, 2),
        "paper_pnl": round(paper_pnl, 2),
        "bankroll": bankroll,
    }

# =========================
# ODDS API / PINNACLE
# =========================
def get_pinnacle_games():
    params = {
        "apiKey": ODDS_API_KEY,
        "bookmakers": "pinnacle",
        "markets": "h2h",
        "oddsFormat": "american",
    }

    r = requests.get(ODDS_API_URL, params=params, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    data = r.json()

    games = []

    for event in data:
        home = event.get("home_team")
        away = event.get("away_team")
        commence_time = event.get("commence_time")
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
                prices[name] = float(price)

        if home not in prices or away not in prices:
            continue

        games.append({
            "game": f"{away} @ {home}",
            "away": away,
            "home": home,
            "away_price": prices[away],
            "home_price": prices[home],
            "away_prob": american_to_prob(prices[away]),
            "home_prob": american_to_prob(prices[home]),
            "commence_time": commence_time,
        })

    return games

def odds_command():
    games = get_pinnacle_games()
    if not games:
        return "No NBA Pinnacle odds found."

    lines = [f"🏀 Pinnacle NBA Odds ({len(games)} games)"]
    for g in games:
        lines.append(
            f"{g['game']}\n"
            f"{g['away']}: {g['away_price']:.0f}\n"
            f"{g['home']}: {g['home_price']:.0f}"
        )
    return "\n\n".join(lines)

# =========================
# KALSHI
# =========================
def get_kalshi_market(ticker: str):
    url = f"{KALSHI_MARKET_URL}/{ticker}"
    r = requests.get(url, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    data = r.json()

    if isinstance(data, dict) and "market" in data:
        return data["market"]
    return data

def extract_game_edges():
    """
    Returns a list of edge opportunities for games present
    in both Pinnacle odds and KALSHI_TICKERS mapping.
    """
    games = get_pinnacle_games()
    bankroll = get_latest_bankroll()
    edges = []

    for g in games:
        game_key = g["game"]
        mapping = KALSHI_TICKERS.get(game_key)

        if not mapping:
            continue

        ticker = str(mapping.get("ticker", "")).strip()
        yes_team = str(mapping.get("yes_team", "")).strip()

        if not ticker or not yes_team:
            continue

        if yes_team not in [g["away"], g["home"]]:
            continue

        try:
            market = get_kalshi_market(ticker)
        except Exception as e:
            print(f"Kalshi fetch failed for {ticker}: {e}")
            continue

        yes_ask = safe_float(market.get("yes_ask"))
        no_ask = safe_float(market.get("no_ask"))
        yes_bid = safe_float(market.get("yes_bid"))
        no_bid = safe_float(market.get("no_bid"))

        yes_prob = cents_to_prob(yes_ask)

        if no_ask is not None:
            no_prob = cents_to_prob(no_ask)
        elif yes_bid is not None:
            # Reciprocal fallback
            no_prob = (100.0 - yes_bid) / 100.0
        else:
            no_prob = None

        if yes_team == g["away"]:
            yes_team_prob = g["away_prob"]
            no_team = g["home"]
            no_team_prob = g["home_prob"]
            yes_team_price = g["away_price"]
            no_team_price = g["home_price"]
        else:
            yes_team_prob = g["home_prob"]
            no_team = g["away"]
            no_team_prob = g["away_prob"]
            yes_team_price = g["home_price"]
            no_team_price = g["away_price"]

        # YES side
        if yes_prob is not None:
            edge_yes = yes_team_prob - yes_prob
            if edge_yes >= EDGE_PCT:
                stake = suggested_bet_size(bankroll or 0, edge_yes)
                bet_id = build_bet_id(ticker, "YES")
                edges.append({
                    "bet_id": bet_id,
                    "timestamp": now_iso(),
                    "market": "MONEYLINE",
                    "ticker": ticker,
                    "game": game_key,
                    "team": yes_team,
                    "side": "YES",
                    "edge_pct": round(edge_yes, 6),
                    "yes_ask": yes_ask,
                    "no_ask": no_ask,
                    "pinnacle_price": yes_team_price,
                    "pinnacle_prob": round(yes_team_prob, 6),
                    "kalshi_prob": round(yes_prob, 6),
                    "suggested_bet": stake,
                    "bankroll_before": bankroll or "",
                    "action": "",
                    "result": "",
                    "profit_loss": "",
                    "bankroll_after": "",
                    "paper_result": "",
                    "paper_profit": "",
                    "notes": "",
                })

        # NO side
        if no_prob is not None:
            edge_no = no_team_prob - no_prob
            if edge_no >= EDGE_PCT:
                stake = suggested_bet_size(bankroll or 0, edge_no)
                bet_id = build_bet_id(ticker, "NO")
                edges.append({
                    "bet_id": bet_id,
                    "timestamp": now_iso(),
                    "market": "MONEYLINE",
                    "ticker": ticker,
                    "game": game_key,
                    "team": no_team,
                    "side": "NO",
                    "edge_pct": round(edge_no, 6),
                    "yes_ask": yes_ask,
                    "no_ask": no_ask,
                    "pinnacle_price": no_team_price,
                    "pinnacle_prob": round(no_team_prob, 6),
                    "kalshi_prob": round(no_prob, 6),
                    "suggested_bet": stake,
                    "bankroll_before": bankroll or "",
                    "action": "",
                    "result": "",
                    "profit_loss": "",
                    "bankroll_after": "",
                    "paper_result": "",
                    "paper_profit": "",
                    "notes": "",
                })

    return edges

def edge_command():
    edges = extract_game_edges()
    if not edges:
        return "No edge found right now."

    lines = [f"📈 Edges Found ({len(edges)})"]
    for e in edges[:10]:
        lines.append(
            f"{e['bet_id']}\n"
            f"{e['game']}\n"
            f"{e['team']} {e['side']}\n"
            f"Pinnacle Prob: {e['pinnacle_prob']:.3f}\n"
            f"Kalshi Prob: {e['kalshi_prob']:.3f}\n"
            f"Edge: {pct(e['edge_pct'])}\n"
            f"Suggested Bet: ${e['suggested_bet']}"
        )

    return "\n\n".join(lines)

def alert_key_for_edge(e: dict) -> str:
    # dedupe on ticker+side+prices snapshot
    return f"{e['ticker']}|{e['side']}|{e['yes_ask']}|{e['no_ask']}|{e['pinnacle_price']}"

def log_and_alert_edges():
    edges = extract_game_edges()

    for e in edges:
        alert_key = alert_key_for_edge(e)
        if alert_key in SEEN_ALERT_KEYS:
            continue

        # log to sheet first
        append_sheet_row(e)

        msg = (
            f"🚨 LIVE EDGE ALERT\n\n"
            f"Bet ID: {e['bet_id']}\n"
            f"Game: {e['game']}\n"
            f"Market: {e['market']}\n"
            f"Play: {e['team']} {e['side']}\n"
            f"Ticker: {e['ticker']}\n"
            f"Pinnacle Prob: {e['pinnacle_prob']:.3f}\n"
            f"Kalshi Prob: {e['kalshi_prob']:.3f}\n"
            f"Edge: {pct(e['edge_pct'])}\n"
            f"Suggested Bet: ${e['suggested_bet']}\n\n"
            f"Reply with:\n"
            f"took {e['bet_id']}\n"
            f"or\n"
            f"pass {e['bet_id']}"
        )

        try:
            send_message(msg)
            SEEN_ALERT_KEYS.add(alert_key)
        except Exception as ex:
            print(f"Telegram send failed: {ex}")

# =========================
# ACTION COMMANDS
# =========================
def set_bankroll_command(text: str):
    parts = text.strip().split()
    if len(parts) != 2:
        return "Use: bankroll 271"

    bankroll = safe_float(parts[1])
    if bankroll is None or bankroll < 0:
        return "Invalid bankroll amount."

    log_bankroll_set(bankroll)
    return f"Bankroll set to ${bankroll:.2f}"

def took_command(text: str):
    parts = text.strip().split(maxsplit=1)
    if len(parts) != 2:
        return "Use: took <bet_id>"

    bet_id = parts[1].strip()
    ok = update_row_fields(bet_id, {"action": "took"})
    if not ok:
        return "Bet ID not found."

    return f"Marked TOOK for {bet_id}"

def pass_command(text: str):
    parts = text.strip().split(maxsplit=1)
    if len(parts) != 2:
        return "Use: pass <bet_id>"

    bet_id = parts[1].strip()
    ok = update_row_fields(bet_id, {"action": "pass"})
    if not ok:
        return "Bet ID not found."

    return f"Marked PASS for {bet_id}"

def settle_bet(text: str, settle_to: str):
    """
    settle_to = 'win' or 'loss'
    """
    parts = text.strip().split(maxsplit=1)
    if len(parts) != 2:
        return f"Use: {settle_to} <bet_id>"

    bet_id = parts[1].strip()
    rows = get_all_rows()
    target = None

    for row in rows:
        if str(row.get("bet_id", "")).strip() == bet_id:
            target = row
            break

    if target is None:
        return "Bet ID not found."

    action = str(target.get("action", "")).strip().lower()
    suggested_bet = safe_float(target.get("suggested_bet"), 0.0)
    kalshi_prob = safe_float(target.get("kalshi_prob"), None)
    bankroll_before = safe_float(target.get("bankroll_before"), None)

    if kalshi_prob is None or kalshi_prob <= 0:
        return "Missing Kalshi price on this row."

    if settle_to == "win":
        paper_profit = yes_profit_from_stake(suggested_bet, kalshi_prob)
        paper_result = "win"
    else:
        paper_profit = round(-suggested_bet, 2)
        paper_result = "loss"

    updates = {
        "paper_result": paper_result,
        "paper_profit": paper_profit,
    }

    if action == "took":
        actual_profit = paper_profit
        updates["result"] = settle_to
        updates["profit_loss"] = actual_profit

        if bankroll_before is not None:
            updates["bankroll_after"] = round(bankroll_before + actual_profit, 2)
    else:
        # If passed, actual result stays blank or passed
        if action == "pass":
            updates["result"] = "passed"
            updates["profit_loss"] = 0
            if bankroll_before is not None:
                updates["bankroll_after"] = bankroll_before

    ok = update_row_fields(bet_id, updates)
    if not ok:
        return "Could not update the bet."

    return (
        f"Settled {bet_id} as {settle_to.upper()}\n"
        f"Paper PnL: ${paper_profit:.2f}"
    )

def open_command():
    rows = get_open_bets()
    if not rows:
        return "No open taken bets."

    lines = [f"Open bets ({len(rows)})"]
    for row in rows[:15]:
        lines.append(
            f"{row.get('bet_id')}\n"
            f"{row.get('game')}\n"
            f"{row.get('team')} {row.get('side')}\n"
            f"Stake: ${row.get('suggested_bet')}"
        )
    return "\n\n".join(lines)

def stats_command():
    s = get_stats_summary()
    bankroll_txt = "N/A" if s["bankroll"] is None else f"${s['bankroll']:.2f}"

    return (
        f"Stats\n\n"
        f"Alerts: {s['alerts']}\n"
        f"Took: {s['took']}\n"
        f"Passed: {s['passed']}\n"
        f"Actual PnL: ${s['actual_pnl']:.2f}\n"
        f"Paper PnL: ${s['paper_pnl']:.2f}\n"
        f"Bankroll: {bankroll_txt}"
    )

def tickers_command():
    if not KALSHI_TICKERS:
        return "KALSHI_TICKERS is empty."

    lines = ["Loaded Kalshi game mappings"]
    for game, info in KALSHI_TICKERS.items():
        lines.append(
            f"{game}\n"
            f"ticker: {info.get('ticker','')}\n"
            f"yes_team: {info.get('yes_team','')}"
        )
    return "\n\n".join(lines)

def help_text():
    return (
        "Commands\n\n"
        "/start\n"
        "/odds\n"
        "/edge\n"
        "/tickers\n"
        "/open\n"
        "/stats\n\n"
        "bankroll 271\n"
        "took <bet_id>\n"
        "pass <bet_id>\n"
        "win <bet_id>\n"
        "loss <bet_id>"
    )

# =========================
# TELEGRAM HANDLER
# =========================
def handle_updates():
    global LAST_UPDATE_ID

    updates = get_updates()

    for update in updates:
        LAST_UPDATE_ID = update["update_id"]

        message = update.get("message", {})
        chat = message.get("chat", {})
        text = (message.get("text") or "").strip()

        if str(chat.get("id")) != str(TELEGRAM_CHAT_ID):
            continue

        cmd = text.lower()

        try:
            if cmd == "/start":
                send_message(
                    "Kalshi Edge Bot running.\n\n"
                    + help_text()
                )

            elif cmd == "/help":
                send_message(help_text())

            elif cmd == "/odds":
                send_message(odds_command())

            elif cmd == "/edge":
                send_message(edge_command())

            elif cmd == "/tickers":
                send_message(tickers_command())

            elif cmd == "/open":
                send_message(open_command())

            elif cmd == "/stats":
                send_message(stats_command())

            elif cmd.startswith("bankroll "):
                send_message(set_bankroll_command(text))

            elif cmd.startswith("took "):
                send_message(took_command(text))

            elif cmd.startswith("pass "):
                send_message(pass_command(text))

            elif cmd.startswith("win "):
                send_message(settle_bet(text, "win"))

            elif cmd.startswith("loss "):
                send_message(settle_bet(text, "loss"))

        except Exception as e:
            send_message(f"Command error: {e}")

# =========================
# MAIN LOOP
# =========================
def main():
    global LAST_SCAN_TS

    must_env()
    ensure_sheet_headers()

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
        if now - LAST_SCAN_TS >= POLL_SECONDS:
            try:
                log_and_alert_edges()
            except Exception as e:
                print("Scan error:", e)
            LAST_SCAN_TS = now

        time.sleep(3)

if __name__ == "__main__":
    main()
