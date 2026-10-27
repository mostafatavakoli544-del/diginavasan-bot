import os
import logging
import requests
import threading
import http.server
import socketserver
import pytz
import urllib.parse
import arabic_reshaper
import asyncio
import json
from io import BytesIO
from datetime import datetime, time
from baleio import Bot, Message, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from PIL import Image, ImageDraw, ImageFont, features
from supabase import create_client, Client

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

BALE_TOKEN = os.environ.get("BALE_TOKEN")
PORT = int(os.environ.get("PORT", 10000))
CHANNEL_ID = "@diginavasan"
ADMIN_ID = 718840144

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
UPSTASH_REDIS_REST_URL = os.environ.get("UPSTASH_REDIS_REST_URL")
UPSTASH_REDIS_REST_TOKEN = os.environ.get("UPSTASH_REDIS_REST_TOKEN")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
HAS_RAQM = features.check("raqm")
user_alerts = {}

def run_dummy_server():
    Handler = http.server.SimpleHTTPRequestHandler
    with socketserver.TCPServer(("", PORT), Handler) as httpd: httpd.serve_forever()

def get_dates_info():
    now = datetime.now(pytz.timezone('Asia/Tehran'))
    return now.strftime('%H:%M'), now.hour

def get_nerkh_price(key):
    try:
        response = requests.get("https://nerkh.jahankhahan.shop/data/live.json", timeout=10)
        if response.status_code == 200:
            price = response.json()['rates'].get(key)
            return int(price) if price else 0
        return 0
    except Exception: return 0

def get_nerkh_price_safe(keys_list):
    for key in keys_list:
        price = get_nerkh_price(key)
        if price > 0: return f"{price:,.0f}"
    return "0"

def get_crypto_prices_toman():
    try:
        coins = {"BTC": "btc", "ETH": "eth", "USDT": "usdt", "BNB": "bnb"}
        prices = {}
        for symbol, coin_id in coins.items():
            url = f"https://apiv2.nobitex.ir/market/stats?srcCurrency={coin_id}&dstCurrency=rls"
            res = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=5)
            if res.status_code == 200:
                key = f"{coin_id}-rls"
                if key in res.json().get('stats', {}):
                    prices[symbol] = f"{int(float(res.json()['stats'][key]['latest']) / 10):,.0f}"
        return prices
    except Exception: return {}

def get_ai_response(prompt):
    try:
        url = "https://api.groq.com/openai/v1/chat/completions"
        headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
        data = {"model": "openai/gpt-oss-120b", "messages": [{"role": "system", "content": "You are a helpful assistant. Answer in Persian."}, {"role": "user", "content": prompt}], "temperature": 0.7, "max_tokens": 1024}
        response = requests.post(url, headers=headers, json=data, timeout=30)
        if response.status_code == 200: return response.json()['choices'][0]['message']['content']
        else: return "خطایی رخ داد."
    except Exception: return "خطای اتصال."

def get_main_menu():
    keyboard = [
        [KeyboardButton("💱 قیمت ارزها"), KeyboardButton("🌕 قیمت طلا و سکه")],
        [KeyboardButton("🪙 قیمت ارز دیجیتال"), KeyboardButton("🧮 ماشین‌حساب طلا")],
        [KeyboardButton("🤖 چت با هوش مصنوعی")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

bot = Bot(token=BALE_TOKEN)

@bot.on_event("ready")
async def on_ready():
    print("Bale bot is running...")

@bot.handle("message")
async def handle_message(message: Message):
    text = message.text.strip() if message.text else ""
    user_id = message.from_user.id

    if text == "/start":
        welcome = "سلام! به ربات دیجی نوسان خوش آمدید. ✅\n\nاز منوی زیر انتخاب کنید:"
        await message.reply(welcome, reply_markup=get_main_menu())
    elif "قیمت ارزها" in text:
        time_str, _ = get_dates_info()
        msg = f"💱 <b>قیمت لحظه‌ای ارزها</b>\n⏰ <code>{time_str}</code>\n\n💵 دلار = <code>{get_nerkh_price_safe(['dollar'])}</code> تومان\n💶 یورو = <code>{get_nerkh_price_safe(['euro'])}</code> تومان\n💷 پوند = <code>{get_nerkh_price_safe(['pound'])}</code> تومان"
        await message.reply(msg, parse_mode='HTML')
    elif "قیمت طلا و سکه" in text:
        time_str, _ = get_dates_info()
        msg = f"🌕 <b>قیمت طلا و سکه</b>\n⏰ <code>{time_str}</code>\n\nطلا ۱۸ = <code>{get_nerkh_price_safe(['gold'])}</code> تومان\nسکه امامی = <code>{get_nerkh_price_safe(['coin'])}</code> تومان"
        await message.reply(msg, parse_mode='HTML')
    elif "قیمت ارز دیجیتال" in text:
        time_str, _ = get_dates_info()
        crypto = get_crypto_prices_toman()
        msg = f"🪙 <b>قیمت ارز دیجیتال</b>\n⏰ <code>{time_str}</code>\n\n"
        for sym, prc in crypto.items(): msg += f"🔸 {sym} = <code>{prc}</code> تومان\n"
        await message.reply(msg, parse_mode='HTML')
    elif "چت با هوش مصنوعی" in text:
        await message.reply("سوال خود را بپرسید:")
    else:
        # اگر ربات در حالت عادی بود، به عنوان سوال هوش مصنوعی در نظر می‌گیرد
        await message.reply("⏳ در حال پردازش...")
        res = await asyncio.to_thread(get_ai_response, text)
        await message.reply(res)

if __name__ == "__main__":
    threading.Thread(target=run_dummy_server, daemon=True).start()
    print("Starting Bale Bot...")
    bot.run()
