import os
import logging
import requests
import threading
import http.server
import socketserver
import pytz
import asyncio
import json
from datetime import datetime
from baleio import Bot, Dispatcher, md
from baleio.client.default import DefaultBotProperties
from baleio.filters import CommandStart
from baleio.types import Message, ReplyKeyboardMarkup, KeyboardButton

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

BALE_TOKEN = os.environ.get("BALE_TOKEN")
PORT = int(os.environ.get("PORT", 10000))
CHANNEL_ID = "@diginavasan"
ADMIN_ID = 718840144
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
BRS_API_KEY = os.environ.get("BRS_API_KEY")

UPSTASH_URL = os.environ.get("UPSTASH_REDIS_REST_URL")
UPSTASH_TOKEN = os.environ.get("UPSTASH_REDIS_REST_TOKEN")

# 👇 کش سراسری برای جلوگیری از قفل شدن ربات و افزایش سرعت 👇
PRICE_CACHE = {
    "fiat": "",
    "gold": "",
    "crypto": "",
    "usdt": "",
    "bourse": "",
    "last_update": "در حال دریافت..."
}

def run_dummy_server():
    Handler = http.server.SimpleHTTPRequestHandler
    with socketserver.TCPServer(("", PORT), Handler) as httpd: httpd.serve_forever()

def get_dates_info():
    now = datetime.now(pytz.timezone('Asia/Tehran'))
    return now.strftime('%H:%M'), now.hour

# ==========================================
# 👇 توابع دیتابیس Upstash (با REST API) 👇
# ==========================================
def upstash_get(key):
    if not UPSTASH_URL or not UPSTASH_TOKEN: return None
    try:
        res = requests.get(f"{UPSTASH_URL}/get/{key}", headers={"Authorization": f"Bearer {UPSTASH_TOKEN}"}, timeout=5)
        if res.status_code == 200:
            return res.json().get("result")
    except Exception: pass
    return None

def upstash_incr(key):
    if not UPSTASH_URL or not UPSTASH_TOKEN: return 0
    try:
        res = requests.get(f"{UPSTASH_URL}/incr/{key}", headers={"Authorization": f"Bearer {UPSTASH_TOKEN}"}, timeout=5)
        if res.status_code == 200:
            return res.json().get("result")
    except Exception: pass
    return 0

def check_quota(user_id, feature):
    # اگه دیتابیس وصل نبود، برای اینکه ربات از کار نیفته، اجازه بده
    if not UPSTASH_URL or not UPSTASH_TOKEN: return True 
    
    key = f"quota:{user_id}:{feature}"
    count = upstash_get(key)
    if count is None: count = 0
    
    if int(count) < 3:
        upstash_incr(key)
        return True
    return False

# ==========================================
# 👇 توابع دریافت اطلاعات 👇
# ==========================================
def get_nerkh_price(key):
    try:
        response = requests.get("https://nerkh.jahankhahan.shop/data/live.json", timeout=15)
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

def get_multi_exchange_usdt_prices():
    results = {}
    headers = {'User-Agent': 'Mozilla/5.0'}
    try:
        res = requests.get("https://apiv2.nobitex.ir/market/stats?srcCurrency=usdt&dstCurrency=rls", headers=headers, timeout=15)
        if res.status_code == 200 and 'usdt-rls' in res.json().get('stats', {}):
            results['نوبیتکس'] = int(float(res.json()['stats']['usdt-rls']['latest']) / 10)
    except Exception: pass
    try:
        res = requests.get("https://api.wallex.ir/v1/markets", headers=headers, timeout=15)
        if res.status_code == 200 and 'USDTTMN' in res.json().get('result', {}).get('symbols', {}):
            results['والکس'] = int(float(res.json()['result']['symbols']['USDTTMN']['stats']['lastPrice']))
    except Exception: pass
    try:
        res = requests.get("https://publicapi.ramzinex.com/exchange/api/v1.0/exchange/pairs", headers=headers, timeout=15)
        if res.status_code == 200:
            for item in res.json().get('data', []):
                if item.get('name', {}).get('en') == 'tether/rial' and float(item.get('sell', 0)) > 0:
                    results['رمزینکس'] = int(float(item['sell']) / 10)
                    break
    except Exception: pass
    try:
        res = requests.get("https://api.bitpin.ir/api/v1/mkt/tickers/", headers=headers, timeout=15)
        if res.status_code == 200:
            for item in res.json():
                if item.get('symbol') == 'USDT_IRT':
                    results['بیت‌پین'] = int(float(item.get('price')))
                    break
    except Exception: pass
    try:
        res = requests.get("https://api.tabdeal.org/api/v1/market/statistics", headers=headers, timeout=15)
        if res.status_code == 200 and 'USDTIRT' in res.json():
            results['تبدیل'] = int(float(res.json()['USDTIRT'].get('lastPrice', 0)))
    except Exception: pass
    return results

def get_crypto_prices_toman():
    try:
        coins = {"BTC": "btc", "ETH": "eth", "BNB": "bnb", "TRX": "trx", "SOL": "sol", "DOGE": "doge", "TON": "ton"}
        prices = {}
        for symbol, coin_id in coins.items():
            url = f"https://apiv2.nobitex.ir/market/stats?srcCurrency={coin_id}&dstCurrency=rls"
            res = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=15)
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
        else: return "خطایی در ارتباط با هوش مصنوعی رخ داد."
    except Exception: return "خطای اتصال به هوش مصنوعی."

# ==========================================
# 👇 ساخت پیام‌ها (با استفاده از کش) 👇
# ==========================================
def gen_fiat_msg():
    return PRICE_CACHE["fiat"]

def gen_gold_msg():
    return PRICE_CACHE["gold"]

def gen_crypto_msg():
    return PRICE_CACHE["crypto"]

def gen_usdt_msg():
    return PRICE_CACHE["usdt"]

def gen_bourse_msg():
    return PRICE_CACHE["bourse"]

# ==========================================
# 👇 تسک پس‌زمینه برای آپدیت کش هر ۳۰ ثانیه 👇
# ==========================================
async def update_cache_task():
    global PRICE_CACHE
    while True:
        try:
            now = datetime.now(pytz.timezone('Asia/Tehran'))
            time_str = now.strftime('%H:%M')
            
            # --- 1. آپدیت ارزها ---
            fiat_msg = f"💱 {md.bold('قیمت لحظه‌ای ارزها')}\n⏰ {md.code(time_str)}\n\n"
            fiat_msg += f"💵 دلار = {md.code(get_nerkh_price_safe(['dollar', 'usd']))} تومان\n"
            fiat_msg += f"💶 یورو = {md.code(get_nerkh_price_safe(['euro', 'eur']))} تومان\n"
            fiat_msg += f"💷 پوند = {md.code(get_nerkh_price_safe(['pound', 'gbp']))} تومان\n"
            fiat_msg += f"🇦🇪 درهم = {md.code(get_nerkh_price_safe(['aed']))} تومان\n"
            fiat_msg += f"🇹🇷 لیر = {md.code(get_nerkh_price_safe(['lira']))} تومان"
            PRICE_CACHE["fiat"] = fiat_msg

            # --- 2. آپدیت طلا ---
            gold_msg = f"🌕 {md.bold('قیمت لحظه‌ای طلا و سکه')}\n⏰ {md.code(time_str)}\n\n"
            gold_msg += f"🥇 طلا ۱۸ عیار = {md.code(get_nerkh_price_safe(['gold']))} تومان\n"
            gold_msg += f"🥇 طلا ۲۴ عیار = {md.code(get_nerkh_price_safe(['gold24']))} تومان\n"
            gold_msg += f"🪙 سکه امامی = {md.code(get_nerkh_price_safe(['coin']))} تومان\n"
            gold_msg += f"🪙 سکه بهار = {md.code(get_nerkh_price_safe(['bahar']))} تومان\n"
            gold_msg += f"🪙 نیم سکه = {md.code(get_nerkh_price_safe(['half']))} تومان\n"
            gold_msg += f"🪙 ربع سکه = {md.code(get_nerkh_price_safe(['quarter']))} تومان"
            PRICE_CACHE["gold"] = gold_msg

            # --- 3. آپدیت کریپتو ---
            crypto = get_crypto_prices_toman()
            crypto_names = {"BTC": "بیت‌کوین", "ETH": "اتریوم", "BNB": "بایننس کوین", "TRX": "ترون", "SOL": "سولانا", "DOGE": "دوج کوین", "TON": "تون کوین"}
            crypto_msg = f"🪙 {md.bold('قیمت لحظه‌ای ارز دیجیتال')}\n⏰ {md.code(time_str)}\n\n"
            if crypto:
                for sym, prc in crypto.items():
                    fa_name = crypto_names.get(sym, sym)
                    emoji = "🟢" if sym in ["BTC", "ETH"] else "🔹"
                    crypto_msg += f"{emoji} {md.bold(fa_name)} ({sym}): {md.code(prc)} تومان\n"
            PRICE_CACHE["crypto"] = crypto_msg

            # --- 4. آپدیت تتر ---
            usdt_prices = get_multi_exchange_usdt_prices()
            usdt_msg = f"💵 {md.bold('مقایسه قیمت تتر (USDT)')}\n⏰ {md.code(time_str)}\n\n"
            if usdt_prices:
                sorted_prices = sorted(usdt_prices.items(), key=lambda item: item[1])
                for i, (exchange, price) in enumerate(sorted_prices):
                    if i == 0: usdt_msg += f"🥇 {md.bold(exchange)}: {md.code(f'{price:,}')} تومان (ارزان‌ترین)\n"
                    else: usdt_msg += f"🔸 {exchange}: {md.code(f'{price:,}')} تومان\n"
                if len(sorted_prices) > 1:
                    diff = sorted_prices[-1][1] - sorted_prices[0][1]
                    usdt_msg += f"\n📊 اختلاف: {md.code(f'{diff:,}')} تومان"
            PRICE_CACHE["usdt"] = usdt_msg

            # --- 5. آپدیت بورس ---
            try:
                url = f"https://Api.Brsapi.ir/Tsetmc/Index.php?key={BRS_API_KEY.strip()}&type=1"
                res = await asyncio.to_thread(requests.get, url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=30)
                if res.status_code == 200:
                    data = res.json()
                    bourse_msg = f"📈 {md.bold('وضعیت شاخص‌های بورس تهران')}\n⏰ {md.code(time_str)}\n\n"
                    bourse_msg += f"🔹 {md.bold('شاخص کل:')} {md.code(f'{data.get('index', 0):,.0f}')}\n"
                    bourse_msg += f"⚖️ {md.bold('شاخص هم‌وزن:')} {md.code(f'{data.get('index_equalWeight', 0):,.0f}')}\n"
                    bourse_msg += f"🔴 وضعیت بازار: {md.code(data.get('state', 'نامشخص'))}\n"
                    PRICE_CACHE["bourse"] = bourse_msg
            except Exception: pass

            PRICE_CACHE["last_update"] = time_str
            logging.info(f"Cache updated at {time_str}")

        except Exception as e:
            logging.error(f"Cache update error: {e}")
        
        await asyncio.sleep(30) # هر ۳۰ ثانیه یکبار آپدیت میکنه

# ==========================================
# 👇 منو و ربات 👇
# ==========================================
def get_main_menu():
    keyboard = [
        [KeyboardButton(text="🚀 استارت / منوی اصلی")],
        [KeyboardButton(text="💱 قیمت ارزها"), KeyboardButton(text="🌕 قیمت طلا و سکه")],
        [KeyboardButton(text="🪙 قیمت ارز دیجیتال"), KeyboardButton(text="📈 شاخص بورس")],
        [KeyboardButton(text="🧮 ماشین‌حساب طلا"), KeyboardButton(text="💸 ماشین‌حساب اقساط")],
        [KeyboardButton(text="💱 مبدل هوشمند ارز"), KeyboardButton(text="🌤 وضعیت آب‌وهوا")],
        [KeyboardButton(text="🚨 آلارم قیمتی"), KeyboardButton(text="🤖 چت با هوش مصنوعی")]
    ]
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)

dp = Dispatcher()

@dp.message(CommandStart())
async def command_start_handler(message: Message) -> None:
    welcome = "سلام! به ربات دیجی نوسان در بله خوش آمدید. ✅\n\nاز منوی زیر یکی از گزینه‌ها را انتخاب کنید:"
    await message.answer(welcome, reply_markup=get_main_menu())

@dp.message()
async def handle_message(message: Message) -> None:
    text = message.text.strip() if message.text else ""
    user_id = message.from_user.id

    if text == "🚀 استارت / منوی اصلی" or text == "/start":
        await command_start_handler(message)
        
    elif text == "💱 قیمت ارزها":
        if check_quota(user_id, "fiat"):
            await message.answer(gen_fiat_msg())
        else:
            await message.answer("⚠️ سهمیه رایگان شما برای بخش «قیمت ارزها» به پایان رسیده است.")

    elif text == "🌕 قیمت طلا و سکه":
        if check_quota(user_id, "gold"):
            await message.answer(gen_gold_msg())
        else:
            await message.answer("⚠️ سهمیه رایگان شما برای بخش «طلا و سکه» به پایان رسیده است.")

    elif text == "🪙 قیمت ارز دیجیتال":
        if check_quota(user_id, "crypto"):
            await message.answer(gen_crypto_msg())
            await asyncio.sleep(1)
            await message.answer(gen_usdt_msg())
        else:
            await message.answer("⚠️ سهمیه رایگان شما برای بخش «ارز دیجیتال» به پایان رسیده است.")

    elif text == "📈 شاخص بورس":
        if check_quota(user_id, "bourse"):
            await message.answer(gen_bourse_msg())
        else:
            await message.answer("⚠️ سهمیه رایگان شما برای بخش «شاخص بورس» به پایان رسیده است.")

    elif text == "🤖 چت با هوش مصنوعی":
        if check_quota(user_id, "ai"):
            await message.answer("🤖 لطفاً سوال خود را بپرسید تا هوش مصنوعی پاسخ دهد:")
        else:
            await message.answer("⚠️ سهمیه رایگان شما برای بخش «هوش مصنوعی» به پایان رسیده است.")

    elif text in ["🧮 ماشین‌حساب طلا", "💸 ماشین‌حساب اقساط", "💱 مبدل هوشمند ارز", "🌤 وضعیت آب‌وهوا", "🚨 آلارم قیمتی"]:
        await message.answer(f"⏳ بخش {text} در حال توسعه است و به زودی به ربات اضافه می‌شود.")

    else:
        # اینجا برای هوش مصنوعی چک میکنیم که سهمیه داشته باشه
        if check_quota(user_id, "ai"):
            await message.answer("⏳ در حال پردازش...")
            res = await asyncio.to_thread(get_ai_response, text)
            await message.answer(res)
        else:
            await message.answer("⚠️ سهمیه رایگان شما برای بخش «هوش مصنوعی» به پایان رسیده است. برای استفاده نامحدود به کانال ما بپیوندید.")

async def main() -> None:
    bot = Bot(token=BALE_TOKEN, default=DefaultBotProperties())
    # 👇 روشن کردن تسک پس‌زمینه کش 👇
    asyncio.create_task(update_cache_task())
    await dp.start_polling(bot)

if __name__ == "__main__":
    threading.Thread(target=run_dummy_server, daemon=True).start()
    print("Starting Bale Bot...")
    asyncio.run(main())
