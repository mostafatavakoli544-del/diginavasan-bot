import os
import logging
import requests
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

TOKEN = os.environ.get("BOT_TOKEN")

def get_price_from_api(symbol):
    try:
        url = f"https://api.priceto.day/v1/latest/irr/{symbol}"
        response = requests.get(url, timeout=10)
        if response.status_code == 200:
            data = response.json()
            return f"{data.get('price', 0):,.0f}"
        return "نامشخص"
    except:
        return "خطا"

def get_usdt_nobitex():
    try:
        url = "https://apiv2.nobitex.ir/market/stats?srcCurrency=usdt&dstCurrency=rls"
        response = requests.get(url, timeout=10)
        if response.status_code == 200:
            data = response.json()
            price_toman = int(float(data['stats']['usdt-rls']['latest']) / 10)
            return f"{price_toman:,.0f}"
        return "نامشخص"
    except:
        return "خطا"

async def send_prices_to_chat(context: ContextTypes.DEFAULT_TYPE, chat_id):
    usd = get_price_from_api("usd")
    usdt = get_usdt_nobitex()
    msg = f"💵 دلار: `{usd}` تومان\n🟢 تتر: `{usdt}` تومان\n\n@diginavasan"
    await context.bot.send_message(chat_id=chat_id, text=msg, parse_mode='Markdown')

async def start(update, context):
    await context.bot.send_message(chat_id=update.effective_chat.id, text="سلام! ربات فعال شد. ✅")

async def price(update, context):
    await send_prices_to_chat(context, update.effective_chat.id)

if __name__ == '__main__':
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler('start', start))
    app.add_handler(CommandHandler('price', price))
    
    print("ربات روشن شد...")
    app.run_polling()
