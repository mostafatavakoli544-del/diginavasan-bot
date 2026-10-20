import os
import logging
import requests
import pytz
from datetime import datetime
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

TOKEN = os.environ.get("BOT_TOKEN")
PORT = int(os.environ.get("PORT", 10000))
RENDER_URL = os.environ.get("RENDER_EXTERNAL_URL")
PROXY_URL = "https://square-wood-1983.mostafatavakoli544.workers.dev"

def get_tgju_price(item_name, to_toman=False):
    try:
        url = "https://call1.tgju.org/ajax.json"
        headers = {'User-Agent': 'Mozilla/5.0'}
        response = requests.get(url, headers=headers, timeout=10)
        data = response.json()
        if item_name in data['current']:
            price_str = data['current'][item_name]['p']
            price_val = int(price_str.replace(',', ''))
            if to_toman:
                price_val = price_val // 10
            return f"{price_val:,.0f}"
        return "نامشخص"
    except Exception:
        return "خطا"

def get_global_ounce():
    try:
        res = requests.get("https://api.gold-api.com/price/XAU", timeout=10)
        gold_ounce = round(res.json()['price'], 2)
        res2 = requests.get("https://api.gold-api.com/price/XAG", timeout=10)
        silver_ounce = round(res2.json()['price'], 2)
        return f"{gold_ounce:,.2f}", f"{silver_ounce:,.2f}"
    except Exception:
        return "خطا", "خطا"

def is_working_hours():
    tehran_tz = pytz.timezone('Asia/Tehran')
    now = datetime.now(tehran_tz)
    return 8 <= now.hour < 20

async def send_prices_to_chat(context: ContextTypes.DEFAULT_TYPE, chat_id):
    try:
        usd = get_tgju_price("price_dollar_rl", to_toman=True)
        eur = get_tgju_price("price_eur", to_toman=True)
        gbp = get_tgju_price("price_gbp", to_toman=True)
        aed = get_tgju_price("price_aed", to_toman=True)
        try_price = get_tgju_price("price_try", to_toman=True)
        gold18 = get_tgju_price("geram18", to_toman=True)
        gold24 = get_tgju_price("geram24", to_toman=True)
        coin_emami = get_tgju_price("sekee")
        coin_bahar = get_tgju_price("sekeb")
        coin_nim = get_tgju_price("nim")
        coin_rob = get_tgju_price("rob")
        ounce_gold, ounce_silver = get_global_ounce()

        msg = f"""💰 **قیمت لحظه‌ای دلار، طلا و سکه**

💵 قیمت دلار = `{usd}` تومان
💵 قیمت یورو = `{eur}` تومان
💵 قیمت پوند انگلستان = `{gbp}` تومان
💵 قیمت لیر ترکیه = `{try_price}` تومان
💵 قیمت درهم امارات = `{aed}` تومان

🌕 اونس جهانی طلا = `{ounce_gold}` دلار
🪙 اونس جهانی نقره = `{ounce_silver}` دلار
🌕 قیمت طلا ۱۸ عیار = `{gold18}` تومان
🌕 قیمت طلا ۲۴ عیار = `{gold24}` تومان
🌕 سکه امامی = `{coin_emami}` تومان
🌕 سکه تمام بهار آزادی = `{coin_bahar}` تومان
🌕 نیم سکه = `{coin_nim}` تومان
🌕 ربع سکه = `{coin_rob}` تومان

*💰 قیمت لحظه‌ای دیجی نوسان 👇*
@diginavasan"""
        await context.bot.send_message(chat_id=chat_id, text=msg, parse_mode='Markdown')
    except Exception as e:
        print(f"❌ خطا: {e}")

async def send_crypto_to_chat(context: ContextTypes.DEFAULT_TYPE, chat_id):
    try:
        usd_price_str = get_tgju_price("price_dollar_rl", to_toman=True)
        usd_price = int(usd_price_str.replace(',', '')) if usd_price_str not in ["خطا", "نامشخص"] else 0
        url = "https://api.coingecko.com/api/v3/simple/price?ids=bitcoin,tether,ethereum,binancecoin&vs_currencies=usd"
        res = requests.get(url, timeout=10)
        data = res.json()
        btc_toman = int(data['bitcoin']['usd'] * usd_price)
        usdt_toman = int(data['tether']['usd'] * usd_price)
        eth_toman = int(data['ethereum']['usd'] * usd_price)
        bnb_toman = int(data['binancecoin']['usd'] * usd_price)

        msg = f"""🪙 **قیمت لحظه‌ای ارز دیجیتال**

💵 دلار پایه = `{usd_price:,}` تومان

🟠 بیت‌کوین (BTC) = `{btc_toman:,}` تومان
🟢 تتر (USDT) = `{usdt_toman:,}` تومان
🔷 اتریوم (ETH) = `{eth_toman:,}` تومان
🟡 بایننس (BNB) = `{bnb_toman:,}` تومان

*💰 قیمت لحظه‌ای دیجی نوسان 👇*
@diginavasan"""
        await context.bot.send_message(chat_id=chat_id, text=msg, parse_mode='Markdown')
    except Exception as e:
        print(f"❌ خطا: {e}")

async def auto_report(context: ContextTypes.DEFAULT_TYPE):
    if not is_working_hours(): return
    await send_prices_to_chat(context, "@diginavasan")

async def auto_crypto(context: ContextTypes.DEFAULT_TYPE):
    if not is_working_hours(): return
    await send_crypto_to_chat(context, "@diginavasan")

_rotation_index = 0

async def rotate_jobs(context: ContextTypes.DEFAULT_TYPE):
    global _rotation_index
    if not is_working_hours(): return
    jobs = [auto_report, auto_crypto]
    current = jobs[_rotation_index % len(jobs)]
    _rotation_index += 1
    await current(context)

async def start(update, context):
    await context.bot.send_message(chat_id=update.effective_chat.id, text="سلام! ربات دیجی نوسان با موفقیت فعال شد. ✅")

if __name__ == '__main__':
    app = (
        ApplicationBuilder()
        .token(TOKEN)
        .base_url(f"{PROXY_URL}/bot")
        .base_file_url(f"{PROXY_URL}/file/bot")
        .build()
    )
    app.add_handler(CommandHandler('start', start))
    app.job_queue.run_repeating(rotate_jobs, interval=300, first=10)
    app.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path=TOKEN,
        webhook_url=f"{RENDER_URL}/{TOKEN}"
    )
