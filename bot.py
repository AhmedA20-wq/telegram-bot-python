import os
import requests
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ODDS_API_KEY = os.getenv("ODDS_API_KEY")

KALSHI_BASE = "https://api.elections.kalshi.com/trade-api/v2"


# --------------------
# START COMMAND
# --------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Bot online")


# --------------------
# PINNACLE ODDS
# --------------------
async def odds(update: Update, context: ContextTypes.DEFAULT_TYPE):

    url = f"https://api.the-odds-api.com/v4/sports/basketball_nba/odds/?apiKey={ODDS_API_KEY}&regions=us&markets=h2h,spreads,totals&bookmakers=pinnacle"

    r = requests.get(url)
    games = r.json()

    if len(games) == 0:
        await update.message.reply_text("No games found")
        return

    game = games[0]

    home = game["home_team"]
    away = game["away_team"]

    bookmakers = game["bookmakers"][0]
    markets = bookmakers["markets"]

    message = f"{away} @ {home}\n\n"

    for market in markets:

        if market["key"] == "h2h":
            for o in market["outcomes"]:
                message += f"{o['name']} ML: {o['price']}\n"

        if market["key"] == "spreads":
            for o in market["outcomes"]:
                message += f"{o['name']} {o['point']} ({o['price']})\n"

        if market["key"] == "totals":
            for o in market["outcomes"]:
                message += f"{o['name']} {o['point']} ({o['price']})\n"

    await update.message.reply_text(message)


# --------------------
# KALSHI MARKET
# --------------------
async def kalshi(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if not context.args:
        await update.message.reply_text("Usage: /kalshi MARKET_TICKER")
        return

    ticker = context.args[0]

    url = f"{KALSHI_BASE}/markets/{ticker}/orderbook"

    r = requests.get(url)
    data = r.json()

    yes_orders = data["orderbook"]["yes"]
    no_orders = data["orderbook"]["no"]

    best_yes = yes_orders[0][0] if yes_orders else None
    best_no = no_orders[0][0] if no_orders else None

    message = f"Kalshi Market: {ticker}\n\n"

    if best_yes:
        message += f"Best YES bid: {best_yes}¢\n"

    if best_no:
        message += f"Best NO bid: {best_no}¢\n"

    await update.message.reply_text(message)


# --------------------
# RUN BOT
# --------------------
def main():

    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("odds", odds))
    app.add_handler(CommandHandler("kalshi", kalshi))

    print("Bot running...")

    app.run_polling()


if __name__ == "__main__":
    main()
