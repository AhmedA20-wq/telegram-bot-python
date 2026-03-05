import os
import time
import requests

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

SCAN_SECONDS = int(os.getenv("SCAN_SECONDS", "30"))
EDGE_MIN_CENTS = int(os.getenv("EDGE_MIN_CENTS", "3"))   # alert if (100 - (yes_ask + no_ask)) >= this
MIN_VOLUME = int(os.getenv("MIN_VOLUME", "5000"))

KALSHI_MARKETS_URL = "https://api.elections.kalshi.com/trade-api/v2/markets"


def tg_send(text: str):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        raise RuntimeError("Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID")

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text}
    r = requests.post(url, data=payload, timeout=20)
    if r.status_code != 200:
        raise RuntimeError(f"Telegram send failed {r.status_code}: {r.text}")


def fetch_markets(limit=200, cursor=None):
    params = {"limit": limit}
    if cursor:
        params["cursor"] = cursor
    r = requests.get(KALSHI_MARKETS_URL, params=params, timeout=20)
    r.raise_for_status()
    return r.json()


def is_good_market(m: dict) -> bool:
    # Filter out dead markets
    vol = m.get("volume") or 0
    if vol < MIN_VOLUME:
        return False

    status = (m.get("status") or "").lower()
    # keep if open/active-like; if empty, we still allow
    if status and status not in {"open", "active", "trading"}:
        # if Kalshi uses different statuses, this avoids closed/settled ones
        return False

    # Need both asks in cents
    yes_ask = m.get("yes_ask")
    no_ask = m.get("no_ask")
    if not isinstance(yes_ask, int) or not isinstance(no_ask, int):
        return False

    # Asks should be realistic
    if yes_ask <= 0 or no_ask <= 0 or yes_ask >= 100 or no_ask >= 100:
        return False

    return True


def compute_edge_cents(m: dict) -> int:
    yes_ask = m["yes_ask"]
    no_ask = m["no_ask"]
    return 100 - (yes_ask + no_ask)  # positive = “cheap” combined asks


def main():
    tg_send("✅ Kalshi Alert Bot ONLINE (scanning public markets)")

    # prevent spam: remember alerts we've already sent recently
    alerted = {}  # ticker -> last_sent_epoch
    cooldown = 20 * 60  # 20 minutes

    while True:
        try:
            found = []
            cursor = None
            pages = 0

            # scan a few pages each loop so it’s fast + not too spammy
            # (increase pages if you want later)
            while pages < 3:
                data = fetch_markets(limit=200, cursor=cursor)
                markets = data.get("markets", [])
                cursor = data.get("cursor")
                pages += 1

                for m in markets:
                    if not is_good_market(m):
                        continue

                    edge = compute_edge_cents(m)
                    if edge < EDGE_MIN_CENTS:
                        continue

                    ticker = m.get("ticker") or ""
                    title = m.get("title") or ticker
                    vol = m.get("volume") or 0

                    now = int(time.time())
                    last = alerted.get(ticker, 0)
                    if now - last < cooldown:
                        continue

                    alerted[ticker] = now
                    found.append((edge, title, ticker, m["yes_ask"], m["no_ask"], vol))

                if not cursor:
                    break

            # send top 3 best edges
            if found:
                found.sort(reverse=True, key=lambda x: x[0])
                for edge, title, ticker, yes_ask, no_ask, vol in found[:3]:
                    tg_send(
                        f"🚨 KALSHI EDGE\n"
                        f"{title}\n"
                        f"Ticker: {ticker}\n"
                        f"YES ask: {yes_ask}c | NO ask: {no_ask}c\n"
                        f"Edge: {edge}c | Vol: {vol}"
                    )

        except Exception as e:
            tg_send(f"❌ Scanner error: {type(e).__name__}: {e}")

        time.sleep(SCAN_SECONDS)


if __name__ == "__main__":
    main()
