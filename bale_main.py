import os
import logging
import requests
import threading
import http.server
import socketserver
import pytz
import asyncio
from io import BytesIO
from datetime import datetime, time
from baleio import Bot, Dispatcher, md
from baleio.client.default import DefaultBotProperties
from baleio.enums import ParseMode
from baleio.filters import CommandStart
from baleio.types import Message
from supabase import create_client, Client

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

BALE_TOKEN = os.environ.get("BALE_TOKEN")
PORT = int(os.environ.get("PORT", 10000))
CHANNEL_ID = "@diginavasan"
ADMIN_ID = 718840144

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

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

def get_ai_response(prompt):
    try:
        url = "https://api.groq.com/openai/v1/chat/completions"
        headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
        data = {"model": "openai/gpt-oss-120b", "messages": [{"role": "system", "content": "You are a helpful assistant. Answer in Persian."}, {"role": "user", "content": prompt}], "temperature": 0.7, "max_tokens": 1024}
        response = requests.post(url, headers=headers, json=data, timeout=30)
        if response.status_code == 200: return response.json()['choices'][0]['message']['content']
        else: return "خطایی در ارتباط با هوش مصنوعی رخ داد."
    except Exception: return "خطای اتصال به هوش مصنوعی."

dp = Dispatcher()

@dp.message(CommandStart())
async def command_start_handler(message: Message) -> None:
    welcome = "سلام! به ربات دیجی نوسان در بله خوش آمدید. ✅\n\nبرای دریافت قیمت‌ها، کلمه «قیمت» یا دستور /price را بفرستید."
    await message.answer(welcome)

@dp.message()
async def handle_message(message: Message) -> None:
    text = message.text.strip() if message.text else ""
    
    if "price" in text.lower() or "قیمت" in text:
        try:
            dollar = await asyncio.to_thread(get_nerkh_price_safe, ['dollar'])
            euro = await asyncio.to_thread(get_nerkh_price_safe, ['euro'])
            gold = await asyncio.to_thread(get_nerkh_price_safe, ['gold'])
            coin = await asyncio.to_thread(get_nerkh_price_safe, ['coin'])
            time_str, _ = get_dates_info()
            
            # 👇 استفاده از md.bold و md.code برای فرمت‌بندی بله 👇
            msg = f"💱 {md.bold('قیمت لحظه‌ای')}\n⏰ {md.code(time_str)}\n\n"
            msg += f"💵 دلار = {md.code(dollar)} تومان\n"
            msg += f"💶 یورو = {md.code(euro)} تومان\n"
            msg += f"🌕 طلا ۱۸ = {md.code(gold)} تومان\n"
            msg += f"🪙 سکه امامی = {md.code(coin)} تومان"
            
            await message.answer(msg)
        except Exception as e:
            await message.answer(f"❌ خطا در دریافت قیمت: {str(e)}")
    else:
        await message.answer("⏳ در حال پردازش...")
        res = await asyncio.to_thread(get_ai_response, text)
        await message.answer(res)

async def main() -> None:
    bot = Bot(token=BALE_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    await dp.start_polling(bot)

if __name__ == "__main__":
    threading.Thread(target=run_dummy_server, daemon=True).start()
    print("Starting Bale Bot...")
    asyncio.run(main())
