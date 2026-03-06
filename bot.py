import os
import re
import json
import time
import math
import requests
from datetime import datetime, timezone

# =========================
# OPTIONAL GOOGLE SHEETS
# =========================
SHEETS_ENABLED = True
try:
    import gspread
    try:
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

# support both old and new sheet variable names
SHEET_ID = os.getenv("SHEET_ID", "").strip() or os.getenv("GOOGLE_SHEET_ID", "").strip()
GOOGLE_SHEET_TAB = os.getenv("GOOGLE_SHEET_TAB", "BETS").strip()

EDGE_PCT = float(os.getenv("EDGE_PCT", "0.04"))
POLL_SECONDS = int(os.getenv("POLL_SECONDS", "30"))
DEFAULT_BANKROLL = float(os.getenv("BANKROLL", "0") or 0)

# =========================
# CONSTANTS
# =========================
TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
ODDS_API_URL = "https://api.the-odds-api.com/v4/sports/basketball_nba/odds"
KALSHI_EVENTS_URL = "https://api.elections.kalshi.com/trade-api/v2/events"

REQUEST_TIMEOUT = 20
MAX_TG_LEN = 3500

LAST_UPDATE_ID = None
LAST_SCAN_TS = 0
SEEN_ALERT_KEYS = set()

# =========================
# SHEET HEADERS
# =========================
SHEET_HEADERS = [
    "bet_id",
    "timestamp",
    "game",
    "market_type",
    "market_key",
    "line",
    "ticker",
    "side",
    "team",
    "edge_pct",
    "pinnacle_price",
    "pinnacle_prob",
    "kalshi_prob",
    "yes_ask",
    "no_ask",
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

def prob_to_pct_str(p: float) -> str:
    return f"{p * 100:.2f}%"

def cents_to_prob(cents):
    if cents is None:
        return None
    try:
        return float(cents) / 100.0
    except Exception:
        return None

def same_line(a, b, tol=0.05):
    if a is None or b is None:
        return False
    return abs(float(a) - float(b)) <= tol

def slugify_market(game: str, market_type: str, side: str, line):
    if line is None:
        line_part = "NA"
    else:
        line_part = str(line).replace(".", "p").replace("-", "m")
    return f"{game}|{market_type}|{side}|{line_part}"

def build_bet_id(market_key: str):
    ts = int(time.time())
    short = abs(hash(market_key)) % 100000
    return f"{short}-{ts}"

def normalize_text(s: str) -> str:
    if not s:
        return ""
    s = s.lower().strip()
    s = s.replace("&", "and")
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s

def suggested_bet_size(bankroll: float, edge: float) -> float:
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

    return round(bankroll * pct_size, 2)

def bet_profit_from_stake(stake: float, entry_prob: float) -> float:
    if stake <= 0 or entry_prob is None or entry_prob <= 0:
        return 0.0
    return round(stake * ((1.0 / entry_prob) - 1.0), 2)

# =========================
# TEAM ALIASES
# =========================
TEAM_ALIASES = {
    "Atlanta Hawks": ["hawks", "atlanta", "atl"],
    "Boston Celtics": ["celtics", "boston", "bos"],
    "Brooklyn Nets": ["nets", "brooklyn", "bkn"],
    "Charlotte Hornets": ["hornets", "charlotte", "cha"],
    "Chicago Bulls": ["bulls", "chicago", "chi"],
    "Cleveland Cavaliers": ["cavaliers", "cavs", "cleveland", "cle"],
    "Dallas Mavericks": ["mavericks", "mavs", "dallas", "dal"],
    "Denver Nuggets": ["nuggets", "denver", "den"],
    "Detroit Pistons": ["pistons", "detroit", "det"],
    "Golden State Warriors": ["warriors", "golden state", "gsw"],
    "Houston Rockets": ["rockets", "houston", "hou"],
    "Indiana Pacers": ["pacers", "indiana", "ind"],
    "Los Angeles Clippers": ["clippers", "la clippers", "los angeles clippers", "lac"],
    "Los Angeles Lakers": ["lakers", "la lakers", "los angeles lakers", "lal"],
    "Memphis Grizzlies": ["grizzlies", "memphis", "mem"],
    "Miami Heat": ["heat", "miami", "mia"],
    "Milwaukee Bucks": ["bucks", "milwaukee", "mil"],
    "Minnesota Timberwolves": ["timberwolves", "wolves", "minnesota", "min"],
    "New Orleans Pelicans": ["pelicans", "new orleans", "nop"],
    "New York Knicks": ["knicks", "new york", "nyk"],
    "Oklahoma City Thunder": ["thunder", "oklahoma city", "okc"],
    "Orlando Magic": ["magic", "orlando", "orl"],
    "Philadelphia 76ers": ["76ers", "sixers", "philadelphia", "phi"],
    "Phoenix Suns": ["suns", "phoenix", "phx"],
    "Portland Trail Blazers": ["trail blazers", "blazers", "portland", "por"],
    "Sacramento Kings": ["kings", "sacramento", "sac"],
    "San Antonio Spurs": ["spurs", "san antonio", "sas"],
    "Toronto Raptors": ["raptors", "toronto", "tor"],
    "Utah Jazz": ["jazz", "utah", "uta"],
    "Washington Wizards": ["wizards", "washington", "was"],
}

def aliases_for_team(team_name: str):
    if team_name in TEAM_ALIASES:
        return TEAM_ALIASES[team_name]
    norm = normalize_text(team_name)
    parts = norm.split()
    out = [norm]
    if parts:
        out.append(parts[-1])
    return list(dict.fromkeys(out))

def contains_team_alias(text: str, team_name: str) -> bool:
    norm = normalize_text(text)
    for alias in aliases_for_team(team_name):
        if alias in norm:
            return True
    return False

# =========================
# TELEGRAM
# =========================
def send_message(text: str):
    url = f"{TELEGRAM_API}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": cut(text)}
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
    return sh.worksheet(GOOGLE_SHEET_TAB)

def ensure_sheet_headers():
    ws = get_ws()
    if ws is None:
        return

    row1 = ws.row_values(1)
    if not row1:
        ws.append_row(SHEET_HEADERS)
        return

    for i, header in enumerate(SHEET_HEADERS, start=1):
        if i > len(row1) or row1[i - 1] != header:
            ws.update_cell(1, i, header)

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
    for i, row in enumerate(rows, start=2):
        if str(row.get("bet_id", "")).strip() == str(bet_id).strip():
            return ws, i
    return ws, None

def update_row_fields(bet_id: str, updates: dict):
    ws, row_index = find_row_index_by_bet_id(bet_id)
    if ws is None or row_index is None:
        return False

    header_map = {h: idx + 1 for idx, h in enumerate(SHEET_HEADERS)}
    for k, v in updates.items():
        if k in header_map:
            ws.update_cell(row_index, header_map[k], v)
    return True

def get_latest_bankroll():
    rows = get_all_rows()
    latest = None

    for row in rows:
        try:
            value = row.get("bankroll_after")
            if value not in ("", None):
                latest = float(value)
        except Exception:
            pass

    if latest is None and DEFAULT_BANKROLL > 0:
        return DEFAULT_BANKROLL
    return latest

def log_bankroll_set(bankroll: float):
    row = {h: "" for h in SHEET_HEADERS}
    row["bet_id"] = f"BANKROLL-{int(time.time())}"
    row["timestamp"] = now_iso()
    row["market_type"] = "SYSTEM"
    row["action"] = "set_bankroll"
    row["bankroll_after"] = bankroll
    row["notes"] = "manual bankroll set"
    append_sheet_row(row)

def get_open_bets():
    rows = get_all_rows()
    out = []
    for row in rows:
        action = str(row.get("action", "")).strip().lower()
        result = str(row.get("result", "")).strip().lower()
        market_type = str(row.get("market_type", "")).strip().upper()
        if market_type == "SYSTEM":
            continue
        if action == "took" and result == "":
            out.append(row)
    return out

def get_stats_summary():
    rows = get_all_rows()

    alerts = 0
    took = 0
    passed = 0
    actual_pnl = 0.0
    paper_pnl = 0.0

    for row in rows:
        market_type = str(row.get("market_type", "")).strip().upper()
        if market_type == "SYSTEM":
            continue

        if str(row.get("bet_id", "")).strip():
            alerts += 1

        action = str(row.get("action", "")).strip().lower()
        if action == "took":
            took += 1
        elif action == "pass":
            passed += 1

        try:
            actual_pnl += float(row.get("profit_loss") or 0)
        except Exception:
            pass

        try:
            paper_pnl += float(row.get("paper_profit") or 0)
        except Exception:
            pass

    return {
        "alerts": alerts,
        "took": took,
        "passed": passed,
        "actual_pnl": round(actual_pnl, 2),
        "paper_pnl": round(paper_pnl, 2),
        "bankroll": get_latest_bankroll(),
    }

# =========================
# ODDS API
# =========================
def get_pinnacle_games():
    params = {
        "apiKey": ODDS_API_KEY,
        "bookmakers": "pinnacle",
        "markets": "h2h,spreads,totals",
        "oddsFormat": "american",
    }

    r = requests.get(ODDS_API_URL, params=params, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    data = r.json()

    games = []

    for event in data:
        home = event.get("home_team")
        away = event.get("away_team")
        bookmakers = event.get("bookmakers", [])
        if not home or not away or not bookmakers:
            continue

        game = {
            "game": f"{away} @ {home}",
            "home": home,
            "away": away,
            "h2h": {},
            "spreads": [],
            "totals": [],
        }

        for bookmaker in bookmakers:
            for market in bookmaker.get("markets", []):
                key = market.get("key")
                outcomes = market.get("outcomes", [])

                if key == "h2h":
                    for o in outcomes:
                        name = o.get("name")
                        price = safe_float(o.get("price"))
                        if name and price is not None:
                            game["h2h"][name] = {
                                "price": price,
                                "prob": american_to_prob(price),
                            }

                elif key == "spreads":
                    for o in outcomes:
                        name = o.get("name")
                        price = safe_float(o.get("price"))
                        point = safe_float(o.get("point"))
                        if name and price is not None and point is not None:
                            game["spreads"].append({
                                "team": name,
                                "point": point,
                                "price": price,
                                "prob": american_to_prob(price),
                            })

                elif key == "totals":
                    for o in outcomes:
                        name = str(o.get("name", "")).strip()
                        price = safe_float(o.get("price"))
                        point = safe_float(o.get("point"))
                        if name and price is not None and point is not None:
                            game["totals"].append({
                                "side": name.lower(),
                                "point": point,
                                "price": price,
                                "prob": american_to_prob(price),
                            })

        games.append(game)

    return games

def odds_command():
    games = get_pinnacle_games()
    if not games:
        return "No NBA Pinnacle odds found."

    lines = [f"🏀 Pinnacle NBA Odds ({len(games)} games)"]

    for g in games[:12]:
        lines.append(
            f"{g['game']}\n"
            f"{g['away']}: {g['h2h'].get(g['away'], {}).get('price', 'N/A')}\n"
            f"{g['home']}: {g['h2h'].get(g['home'], {}).get('price', 'N/A')}"
        )

    return "\n\n".join(lines)

# =========================
# KALSHI EVENTS
# =========================
def get_all_kalshi_open_events():
    events = []
    cursor = None

    for _ in range(20):
        params = {
            "status": "open",
            "with_nested_markets": "true",
            "limit": 200,
        }
        if cursor:
            params["cursor"] = cursor

        r = requests.get(KALSHI_EVENTS_URL, params=params, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        data = r.json()

        batch = data.get("events", [])
        events.extend(batch)

        cursor = data.get("cursor")
        if not cursor:
            break

    return events

def event_text(event: dict) -> str:
    pieces = [
        str(event.get("title", "")),
        str(event.get("sub_title", "")),
        str(event.get("subtitle", "")),
        str(event.get("event_ticker", "")),
        str(event.get("ticker", "")),
    ]
    return normalize_text(" ".join(pieces))

def is_matching_event(event: dict, game: dict) -> bool:
    text = event_text(event)
    return contains_team_alias(text, game["away"]) and contains_team_alias(text, game["home"])

TOTAL_RE = re.compile(r"over\s+([0-9]+(?:\.[0-9]+)?)\s+points\s+scored", re.I)
SPREAD_RE = re.compile(r"(.+?)\s+wins\s+by\s+over\s+([0-9]+(?:\.[0-9]+)?)\s+points", re.I)

def parse_kalshi_market(market: dict, game: dict):
    """
    Returns normalized market info:
    {
      market_type: 'money'|'spread'|'total',
      line: float|None,
      team: str|None,
      over_under: 'over'|None,
      ticker: str,
      yes_ask: ...,
      no_ask: ...,
      yes_prob: ...,
      no_prob: ...,
      title: ...
    }
    """
    title = str(market.get("title", "")).strip()
    subtitle = str(market.get("subtitle", "") or market.get("sub_title", "")).strip()
    combined = normalize_text(f"{title} {subtitle}")
    ticker = market.get("ticker")
    yes_ask = safe_float(market.get("yes_ask"))
    no_ask = safe_float(market.get("no_ask"))
    yes_bid = safe_float(market.get("yes_bid"))
    no_bid = safe_float(market.get("no_bid"))

    yes_prob = cents_to_prob(yes_ask)
    no_prob = cents_to_prob(no_ask)

    if no_prob is None and yes_bid is not None:
        no_prob = (100.0 - yes_bid) / 100.0
    if yes_prob is None and no_bid is not None:
        yes_prob = (100.0 - no_bid) / 100.0

    if not ticker:
        return None

    # ignore obvious props
    if ":" in title:
        return None

    # TOTAL
    m_total = TOTAL_RE.search(title) or TOTAL_RE.search(subtitle) or TOTAL_RE.search(combined)
    if m_total:
        line = safe_float(m_total.group(1))
        return {
            "market_type": "total",
            "line": line,
            "team": None,
            "over_under": "over",
            "ticker": ticker,
            "yes_ask": yes_ask,
            "no_ask": no_ask,
            "yes_prob": yes_prob,
            "no_prob": no_prob,
            "title": title or subtitle,
        }

    # SPREAD
    m_spread = SPREAD_RE.search(title) or SPREAD_RE.search(subtitle)
    if m_spread:
        team_text = m_spread.group(1).strip()
        line = safe_float(m_spread.group(2))

        matched_team = None
        if contains_team_alias(team_text, game["home"]):
            matched_team = game["home"]
        elif contains_team_alias(team_text, game["away"]):
            matched_team = game["away"]

        if matched_team:
            return {
                "market_type": "spread",
                "line": line,
                "team": matched_team,
                "over_under": None,
                "ticker": ticker,
                "yes_ask": yes_ask,
                "no_ask": no_ask,
                "yes_prob": yes_prob,
                "no_prob": no_prob,
                "title": title or subtitle,
            }

    # MONEY
    # Heuristic: if it mentions exactly one team and is not total/spread/prop
    mentions_home = contains_team_alias(combined, game["home"])
    mentions_away = contains_team_alias(combined, game["away"])

    if mentions_home and not mentions_away:
        return {
            "market_type": "money",
            "line": None,
            "team": game["home"],
            "over_under": None,
            "ticker": ticker,
            "yes_ask": yes_ask,
            "no_ask": no_ask,
            "yes_prob": yes_prob,
            "no_prob": no_prob,
            "title": title or subtitle,
        }

    if mentions_away and not mentions_home:
        return {
            "market_type": "money",
            "line": None,
            "team": game["away"],
            "over_under": None,
            "ticker": ticker,
            "yes_ask": yes_ask,
            "no_ask": no_ask,
            "yes_prob": yes_prob,
            "no_prob": no_prob,
            "title": title or subtitle,
        }

    return None

def find_matching_kalshi_event(events, game):
    for event in events:
        if is_matching_event(event, game):
            return event
    return None

# =========================
# EDGE ENGINE
# =========================
def extract_edges():
    bankroll = get_latest_bankroll()
    events = get_all_kalshi_open_events()
    games = get_pinnacle_games()

    edges = []

    for game in games:
        event = find_matching_kalshi_event(events, game)
        if not event:
            continue

        for raw_market in event.get("markets", []):
            km = parse_kalshi_market(raw_market, game)
            if not km:
                continue

            # MONEYLINE
            if km["market_type"] == "money":
                team_yes = km["team"]
                team_no = game["away"] if team_yes == game["home"] else game["home"]

                pin_yes = game["h2h"].get(team_yes)
                pin_no = game["h2h"].get(team_no)
                if not pin_yes or not pin_no:
                    continue

                # YES side
                if km["yes_prob"] is not None:
                    edge = pin_yes["prob"] - km["yes_prob"]
                    if edge >= EDGE_PCT:
                        market_key = slugify_market(game["game"], "money", f"{team_yes}_YES", None)
                        stake = suggested_bet_size(bankroll or 0, edge)
                        edges.append({
                            "bet_id": build_bet_id(market_key),
                            "timestamp": now_iso(),
                            "game": game["game"],
                            "market_type": "MONEY",
                            "market_key": market_key,
                            "line": "",
                            "ticker": km["ticker"],
                            "side": "YES",
                            "team": team_yes,
                            "edge_pct": round(edge, 6),
                            "pinnacle_price": pin_yes["price"],
                            "pinnacle_prob": round(pin_yes["prob"], 6),
                            "kalshi_prob": round(km["yes_prob"], 6),
                            "yes_ask": km["yes_ask"],
                            "no_ask": km["no_ask"],
                            "suggested_bet": stake,
                            "bankroll_before": bankroll or "",
                            "action": "",
                            "result": "",
                            "profit_loss": "",
                            "bankroll_after": "",
                            "paper_result": "",
                            "paper_profit": "",
                            "notes": km["title"],
                        })

                # NO side
                if km["no_prob"] is not None:
                    edge = pin_no["prob"] - km["no_prob"]
                    if edge >= EDGE_PCT:
                        market_key = slugify_market(game["game"], "money", f"{team_no}_NO", None)
                        stake = suggested_bet_size(bankroll or 0, edge)
                        edges.append({
                            "bet_id": build_bet_id(market_key),
                            "timestamp": now_iso(),
                            "game": game["game"],
                            "market_type": "MONEY",
                            "market_key": market_key,
                            "line": "",
                            "ticker": km["ticker"],
                            "side": "NO",
                            "team": team_no,
                            "edge_pct": round(edge, 6),
                            "pinnacle_price": pin_no["price"],
                            "pinnacle_prob": round(pin_no["prob"], 6),
                            "kalshi_prob": round(km["no_prob"], 6),
                            "yes_ask": km["yes_ask"],
                            "no_ask": km["no_ask"],
                            "suggested_bet": stake,
                            "bankroll_before": bankroll or "",
                            "action": "",
                            "result": "",
                            "profit_loss": "",
                            "bankroll_after": "",
                            "paper_result": "",
                            "paper_profit": "",
                            "notes": km["title"],
                        })

            # TOTALS
            elif km["market_type"] == "total":
                line = km["line"]

                pin_over = None
                pin_under = None
                for t in game["totals"]:
                    if same_line(t["point"], line):
                        if t["side"] == "over":
                            pin_over = t
                        elif t["side"] == "under":
                            pin_under = t

                if not pin_over or not pin_under:
                    continue

                if km["yes_prob"] is not None:
                    edge = pin_over["prob"] - km["yes_prob"]
                    if edge >= EDGE_PCT:
                        market_key = slugify_market(game["game"], "total", "OVER_YES", line)
                        stake = suggested_bet_size(bankroll or 0, edge)
                        edges.append({
                            "bet_id": build_bet_id(market_key),
                            "timestamp": now_iso(),
                            "game": game["game"],
                            "market_type": "TOTAL",
                            "market_key": market_key,
                            "line": line,
                            "ticker": km["ticker"],
                            "side": "YES",
                            "team": f"Over {line}",
                            "edge_pct": round(edge, 6),
                            "pinnacle_price": pin_over["price"],
                            "pinnacle_prob": round(pin_over["prob"], 6),
                            "kalshi_prob": round(km["yes_prob"], 6),
                            "yes_ask": km["yes_ask"],
                            "no_ask": km["no_ask"],
                            "suggested_bet": stake,
                            "bankroll_before": bankroll or "",
                            "action": "",
                            "result": "",
                            "profit_loss": "",
                            "bankroll_after": "",
                            "paper_result": "",
                            "paper_profit": "",
                            "notes": km["title"],
                        })

                if km["no_prob"] is not None:
                    edge = pin_under["prob"] - km["no_prob"]
                    if edge >= EDGE_PCT:
                        market_key = slugify_market(game["game"], "total", "UNDER_NO", line)
                        stake = suggested_bet_size(bankroll or 0, edge)
                        edges.append({
                            "bet_id": build_bet_id(market_key),
                            "timestamp": now_iso(),
                            "game": game["game"],
                            "market_type": "TOTAL",
                            "market_key": market_key,
                            "line": line,
                            "ticker": km["ticker"],
                            "side": "NO",
                            "team": f"Under {line}",
                            "edge_pct": round(edge, 6),
                            "pinnacle_price": pin_under["price"],
                            "pinnacle_prob": round(pin_under["prob"], 6),
                            "kalshi_prob": round(km["no_prob"], 6),
                            "yes_ask": km["yes_ask"],
                            "no_ask": km["no_ask"],
                            "suggested_bet": stake,
                            "bankroll_before": bankroll or "",
                            "action": "",
                            "result": "",
                            "profit_loss": "",
                            "bankroll_after": "",
                            "paper_result": "",
                            "paper_profit": "",
                            "notes": km["title"],
                        })

            # SPREADS
            elif km["market_type"] == "spread":
                line = km["line"]
                team_yes = km["team"]
                team_no = game["away"] if team_yes == game["home"] else game["home"]

                pin_yes = None
                pin_no = None

                for s in game["spreads"]:
                    # YES side = team_yes -line
                    if s["team"] == team_yes and same_line(s["point"], -line):
                        pin_yes = s
                    # NO side = opposite team +line
                    if s["team"] == team_no and same_line(s["point"], line):
                        pin_no = s

                if not pin_yes or not pin_no:
                    continue

                if km["yes_prob"] is not None:
                    edge = pin_yes["prob"] - km["yes_prob"]
                    if edge >= EDGE_PCT:
                        market_key = slugify_market(game["game"], "spread", f"{team_yes}_YES", line)
                        stake = suggested_bet_size(bankroll or 0, edge)
                        edges.append({
                            "bet_id": build_bet_id(market_key),
                            "timestamp": now_iso(),
                            "game": game["game"],
                            "market_type": "SPREAD",
                            "market_key": market_key,
                            "line": line,
                            "ticker": km["ticker"],
                            "side": "YES",
                            "team": f"{team_yes} -{line}",
                            "edge_pct": round(edge, 6),
                            "pinnacle_price": pin_yes["price"],
                            "pinnacle_prob": round(pin_yes["prob"], 6),
                            "kalshi_prob": round(km["yes_prob"], 6),
                            "yes_ask": km["yes_ask"],
                            "no_ask": km["no_ask"],
                            "suggested_bet": stake,
                            "bankroll_before": bankroll or "",
                            "action": "",
                            "result": "",
                            "profit_loss": "",
                            "bankroll_after": "",
                            "paper_result": "",
                            "paper_profit": "",
                            "notes": km["title"],
                        })

                if km["no_prob"] is not None:
                    edge = pin_no["prob"] - km["no_prob"]
                    if edge >= EDGE_PCT:
                        market_key = slugify_market(game["game"], "spread", f"{team_no}_NO", line)
                        stake = suggested_bet_size(bankroll or 0, edge)
                        edges.append({
                            "bet_id": build_bet_id(market_key),
                            "timestamp": now_iso(),
                            "game": game["game"],
                            "market_type": "SPREAD",
                            "market_key": market_key,
                            "line": line,
                            "ticker": km["ticker"],
                            "side": "NO",
                            "team": f"{team_no} +{line}",
                            "edge_pct": round(edge, 6),
                            "pinnacle_price": pin_no["price"],
                            "pinnacle_prob": round(pin_no["prob"], 6),
                            "kalshi_prob": round(km["no_prob"], 6),
                            "yes_ask": km["yes_ask"],
                            "no_ask": km["no_ask"],
                            "suggested_bet": stake,
                            "bankroll_before": bankroll or "",
                            "action": "",
                            "result": "",
                            "profit_loss": "",
                            "bankroll_after": "",
                            "paper_result": "",
                            "paper_profit": "",
                            "notes": km["title"],
                        })

    return edges

def alert_key_for_edge(e: dict) -> str:
    return f"{e['market_key']}|{e['ticker']}|{e['yes_ask']}|{e['no_ask']}|{e['pinnacle_price']}"

def edge_command():
    edges = extract_edges()
    if not edges:
        return "No edge found right now."

    lines = [f"📈 Edges Found ({len(edges)})"]
    for e in edges[:12]:
        line_text = f" {e['line']}" if str(e['line']) not in ("", "None") else ""
        lines.append(
            f"{e['bet_id']}\n"
            f"{e['game']}\n"
            f"{e['market_type']} {e['team']}{line_text}\n"
            f"Pinnacle Prob: {e['pinnacle_prob']:.3f}\n"
            f"Kalshi Prob: {e['kalshi_prob']:.3f}\n"
            f"Edge: {prob_to_pct_str(e['edge_pct'])}\n"
            f"Suggested Bet: ${e['suggested_bet']}"
        )
    return "\n\n".join(lines)

def log_and_alert_edges():
    edges = extract_edges()

    for e in edges:
        key = alert_key_for_edge(e)
        if key in SEEN_ALERT_KEYS:
            continue

        append_sheet_row(e)

        line_text = f" @ {e['line']}" if str(e['line']) not in ("", "None") else ""
        msg = (
            f"🚨 LIVE EDGE ALERT\n\n"
            f"Bet ID: {e['bet_id']}\n"
            f"Game: {e['game']}\n"
            f"Market: {e['market_type']}\n"
            f"Play: {e['team']}\n"
            f"Line: {e['line']}\n"
            f"Side: {e['side']}\n"
            f"Pinnacle Prob: {e['pinnacle_prob']:.3f}\n"
            f"Kalshi Prob: {e['kalshi_prob']:.3f}\n"
            f"Edge: {prob_to_pct_str(e['edge_pct'])}\n"
            f"Suggested Bet: ${e['suggested_bet']}\n\n"
            f"Reply:\n"
            f"took {e['bet_id']}\n"
            f"or\n"
            f"pass {e['bet_id']}"
        )

        try:
            send_message(msg)
            SEEN_ALERT_KEYS.add(key)
        except Exception as ex:
            print("Telegram send failed:", ex)

# =========================
# COMMANDS
# =========================
def help_text():
    return (
        "Commands\n\n"
        "/start\n"
        "/help\n"
        "/odds\n"
        "/edge\n"
        "/open\n"
        "/stats\n\n"
        "bankroll 271\n"
        "took <bet_id>\n"
        "pass <bet_id>\n"
        "win <bet_id>\n"
        "loss <bet_id>"
    )

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
        paper_profit = bet_profit_from_stake(suggested_bet, kalshi_prob)
    else:
        paper_profit = round(-suggested_bet, 2)

    updates = {
        "paper_result": settle_to,
        "paper_profit": paper_profit,
    }

    if action == "took":
        updates["result"] = settle_to
        updates["profit_loss"] = paper_profit
        if bankroll_before is not None:
            updates["bankroll_after"] = round(bankroll_before + paper_profit, 2)
    elif action == "pass":
        updates["result"] = "passed"
        updates["profit_loss"] = 0
        if bankroll_before is not None:
            updates["bankroll_after"] = bankroll_before

    ok = update_row_fields(bet_id, updates)
    if not ok:
        return "Could not update the bet."

    return f"Settled {bet_id} as {settle_to.upper()}"

def open_command():
    rows = get_open_bets()
    if not rows:
        return "No open taken bets."

    lines = [f"Open bets ({len(rows)})"]
    for row in rows[:15]:
        lines.append(
            f"{row.get('bet_id')}\n"
            f"{row.get('game')}\n"
            f"{row.get('market_type')} {row.get('team')}\n"
            f"Stake: ${row.get('suggested_bet')}"
        )
    return "\n\n".join(lines)

def stats_command():
    s = get_stats_summary()
    bankroll_text = "N/A" if s["bankroll"] is None else f"${s['bankroll']:.2f}"

    return (
        f"Stats\n\n"
        f"Alerts: {s['alerts']}\n"
        f"Took: {s['took']}\n"
        f"Passed: {s['passed']}\n"
        f"Actual PnL: ${s['actual_pnl']:.2f}\n"
        f"Paper PnL: ${s['paper_pnl']:.2f}\n"
        f"Bankroll: {bankroll_text}"
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
                send_message("Kalshi Edge Bot running.\n\n" + help_text())

            elif cmd == "/help":
                send_message(help_text())

            elif cmd == "/odds":
                send_message(odds_command())

            elif cmd == "/edge":
                send_message(edge_command())

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
# MAIN
# =========================
def main():
    global LAST_SCAN_TS

    must_env()

    try:
        ensure_sheet_headers()
    except Exception as e:
        print("Sheet header setup failed:", e)

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
