import os
import asyncio
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

async def send_startup_message(app):
    await asyncio.sleep(5)
    await app.bot.send_message(chat_id=CHAT_ID, text="✅ Kalshi Edge Bot is ONLINE")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Bot is running.")

async def hello(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Hello from Kalshi Edge Bot.")

async def on_startup(app):
    asyncio.create_task(send_startup_message(app))

def main():
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("hello", hello))

    app.post_init = on_startup
    app.run_polling()

if __name__ == "__main__":
    main()
