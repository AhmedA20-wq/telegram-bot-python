import os
import json
import requests

ODDS_API_KEY = os.getenv("ODDS_API_KEY")

def get_nba_pinnacle_games():
    url = "https://api.the-odds-api.com/v4/sports/basketball_nba/odds"
    params = {
        "apiKey": ODDS_API_KEY,
        "bookmakers": "pinnacle",
        "markets": "h2h",
        "oddsFormat": "american",
    }

    r = requests.get(url, params=params, timeout=15)
    r.raise_for_status()
    data = r.json()

    games = []

    for event in data:
        home_team = event.get("home_team")
        away_team = event.get("away_team")
        bookmakers = event.get("bookmakers", [])

        if not bookmakers:
            continue

        outcomes = bookmakers[0].get("markets", [])[0].get("outcomes", [])
        if len(outcomes) < 2:
            continue

        prices = {}
        for outcome in outcomes:
            prices[outcome["name"]] = outcome["price"]

        home_price = prices.get(home_team)
        away_price = prices.get(away_team)

        if home_price is None or away_price is None:
            continue

        games.append({
            "home_team": home_team,
            "away_team": away_team,
            "home_price": home_price,
            "away_price": away_price,
        })

    return games
