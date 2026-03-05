import os
import requests
import json
from datetime import datetime
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

ODDS_API_KEY = os.getenv("ODDS_API_KEY")
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Kalshi Edge Bot Online")

async def odds(update: Update, context: ContextTypes.DEFAULT_TYPE):

    url = f"https://api.the-odds-api.com/v4/sports/basketball_nba/odds/?apiKey={ODDS_API_KEY}&regions=us&markets=h2h,spreads,totals&bookmakers=pinnacle"

    response = requests.get(url)
    data = response.json()

    if len(data) == 0:
        await update.message.reply_text("No games found")
        return

    game = data[0]

    home = game["home_team"]
    away = game["away_team"]

    bookmakers = game["bookmakers"][0]
    markets = bookmakers["markets"]

    message = f"🏀 Pinnacle Odds\n{away} @ {home}\n\n"

    for market in markets:

        if market["key"] == "h2h":
            for outcome in market["outcomes"]:
                message += f"{outcome['name']} ML: {outcome['price']}\n"

        if market["key"] == "spreads":
            for outcome in market["outcomes"]:
                message += f"{outcome['name']} {outcome['point']} ({outcome['price']})\n"

        if market["key"] == "totals":
            for outcome in market["outcomes"]:
                message += f"{outcome['name']} {outcome['point']} ({outcome['price']})\n"

    await update.message.reply_text(message)

def main():

    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("odds", odds))

    print("Bot running...")

    app.run_polling()

if __name__ == "__main__":
    main()
