import os
import logging
import requests
import pytz
from datetime import datetime
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

# تنظیمات
TOKEN = os.environ.get("BOT_TOKEN")
PROXY_URL = "https://square-wood-1983.mostafatavakoli544.workers.dev"

# تابع جدید برای گرفتن قیمت از priceto.day
def get_price_from_api(symbol):
    try:
        url = f"https://api.priceto.day/v1/latest/irr/{symbol}"
        response = requests.get(url, timeout=10)
        if response.status_code == 200:
            data = response.json()
            price = data.get('price')
            if price:
                return f"{price:,.0f}"
        return "نامشخص"
    except Exception as e:
        print(f"Error fetching {symbol}: {e}")
        return "خطا"

# تابع گرفتن قیمت تتر از نوبیتکس
def get_usdt_nobitex():
    try:
        url = "https://apiv2.nobitex.ir/market/stats?srcCurrency=usdt&dstCurrency=rls"
        response = requests.get(url, timeout=10)
        if response.status_code == 200:
            data = response.json()
            price_rial = float(data['stats']['usdt-rls']['latest'])
            price_toman = int(price_rial / 10)
            return f"{price_toman:,.0f}"
        return "نامشخص"
    except Exception as e:
        print(f"Error fetching Nobitex USDT: {e}")
        return "خطا"

# تابع گرفتن انس جهانی
def get_global_ounce():
    try:
        res = requests.get("https://api.gold-api.com/price/XAU", timeout=10)
        gold_ounce = round(res.json()['price'], 2)
        res2 = requests.get("https://api.gold-api.com/price/XAG", timeout=10)
        silver_ounce = round(res2.json()['price'], 2)
        return f"{gold_ounce:,.2f}", f"{silver_ounce:,.2f}"
    except Exception:
        return "خطا", "خطا"

# تابع ارسال پیام قیمت‌ها
async def send_prices_to_chat(context: ContextTypes.DEFAULT_TYPE, chat_id):
    try:
        usd = get_price_from_api("usd")
        eur = get_price_from_api("eur")
        gbp = get_price_from_api("gbp")
        usdt = get_usdt_nobitex()
        ounce_gold, ounce_silver = get_global_ounce()

        msg = f"""💰 **قیمت لحظه‌ای (تست نهایی)**

💵 قیمت دلار = `{usd}` تومان
💵 قیمت یورو = `{eur}` تومان
💵 قیمت پوند = `{gbp}` تومان

🟢 قیمت تتر (نوبیتکس) = `{usdt}` تومان

🌕 اونس جهانی طلا = `{ounce_gold}` دلار
🪙 اونس جهانی نقره = `{ounce_silver}` دلار

*💰 قیمت لحظه‌ای دیجی نوسان 👇*
@diginavasan"""
        await context.bot.send_message(chat_id=chat_id, text=msg, parse_mode='Markdown')
    except Exception as e:
        print(f"❌ خطا در ساخت گزارش: {e}")

async def start(update, context):
    await context.bot.send_message(chat_id=update.effective_chat.id, text="سلام! ربات دیجی نوسان با موفقیت فعال شد. ✅\nبرای دریافت قیمت‌ها، دستور /price را بزنید.")
    
async def price(update, context):
    await send_prices_to_chat(context, update.effective_chat.id)

if __name__ == '__main__':
    app = (
        ApplicationBuilder()
        .token(TOKEN)
        .base_url(f"{PROXY_URL}/bot")
        .base_file_url(f"{PROXY_URL}/file/bot")
        .build()
    )
    
    app.add_handler(CommandHandler('start', start))
    app.add_handler(CommandHandler('price', price))
    
    print("ربات روشن شد...")
    app.run_polling()
