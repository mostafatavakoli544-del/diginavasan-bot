import os
import logging
import requests
import threading
import http.server
import socketserver
import pytz
import asyncio
from datetime import datetime
from baleio import Bot, Dispatcher, md
from baleio.client.default import DefaultBotProperties
from baleio.enums import ParseMode
from baleio.filters import CommandStart
from baleio.types import Message, ReplyKeyboardMarkup, KeyboardButton

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

BALE_TOKEN = os.environ.get("BALE_TOKEN")
PORT = int(os.environ.get("PORT", 10000))
CHANNEL_ID = "@diginavasan"  # 👈 آیدی کانال شما
ADMIN_ID = 718840144
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
BRS_API_KEY = os.environ.get("BRS_API_KEY")

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

def get_multi_exchange_usdt_prices():
    results = {}
    headers = {'User-Agent': 'Mozilla/5.0'}
    try:
        url = "https://apiv2.nobitex.ir/market/stats?srcCurrency=usdt&dstCurrency=rls"
        res = requests.get(url, headers=headers, timeout=10)
        if res.status_code == 200:
            key = "usdt-rls"
            if key in res.json().get('stats', {}):
                results['نوبیتکس'] = int(float(res.json()['stats'][key]['latest']) / 10)
    except Exception: pass
    try:
        url = "https://api.wallex.ir/v1/markets"
        res = requests.get(url, headers=headers, timeout=10)
        if res.status_code == 200:
            data = res.json()
            if 'result' in data and 'symbols' in data['result'] and 'USDTTMN' in data['result']['symbols']:
                results['والکس'] = int(float(data['result']['symbols']['USDTTMN']['stats']['lastPrice']))
    except Exception: pass
    try:
        url = "https://publicapi.ramzinex.com/exchange/api/v1.0/exchange/pairs"
        res = requests.get(url, headers=headers, timeout=15)
        if res.status_code == 200:
            data = res.json()
            if 'data' in data:
                for item in data['data']:
                    if item.get('name', {}).get('en') == 'tether/rial':
                        price_rls = float(item.get('sell', 0))
                        if price_rls > 0: results['رمزینکس'] = int(price_rls / 10)
                        break
    except Exception: pass
    return results

def get_crypto_prices_toman():
    try:
        coins = {"BTC": "btc", "ETH": "eth", "USDT": "usdt", "BNB": "bnb", "TRX": "trx", "SOL": "sol", "DOGE": "doge", "TON": "ton"}
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
        else: return "خطایی در ارتباط با هوش مصنوعی رخ داد."
    except Exception: return "خطای اتصال به هوش مصنوعی."

def get_main_menu():
    keyboard = [
        [KeyboardButton(text="🚀 استارت / منوی اصلی")],
        [KeyboardButton(text="💱 قیمت ارزها"), KeyboardButton(text="🌕 قیمت طلا و سکه")],
        [KeyboardButton(text="🪙 قیمت ارز دیجیتال"), KeyboardButton(text="📈 شاخص بورس")],
        [KeyboardButton(text="🧮 ماشین‌حساب طلا"), KeyboardButton(text="💸 ماشین‌حساب اقساط")],
        [KeyboardButton(text="💱 مبدل هوشمند ارز"), KeyboardButton(text="🌤 وضعیت آب‌وهوا")],
        [KeyboardButton(text="🚨 آلارم قیمتی"), KeyboardButton(text="🤖 چت با هوش مصنوعی")],
        [KeyboardButton(text="🎁 دعوت از دوستان")]
    ]
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)

def gen_morning_msg():
    return "🌅 **بسم الله الرحمن الرحیم**\n\nصبحتون بخیر و پر از انرژی ☀️\nروزتون پر از سود و موفقیت باشه. امروز رو با توکل به خدا و یه برنامه عالی شروع کنیم. 🚀"

def gen_fiat_msg(time_str):
    msg = f"💱 {md.bold('قیمت لحظه‌ای ارزها')}\n⏰ {md.code(time_str)}\n\n"
    msg += f"💵 دلار = {md.code(get_nerkh_price_safe(['dollar', 'usd']))} تومان\n"
    msg += f"💶 یورو = {md.code(get_nerkh_price_safe(['euro', 'eur']))} تومان\n"
    msg += f"💷 پوند = {md.code(get_nerkh_price_safe(['pound', 'gbp']))} تومان\n"
    msg += f"🇦🇪 درهم = {md.code(get_nerkh_price_safe(['aed']))} تومان\n"
    msg += f"🇹🇷 لیر = {md.code(get_nerkh_price_safe(['lira']))} تومان"
    return msg

def gen_gold_msg(time_str):
    msg = f"🌕 {md.bold('قیمت لحظه‌ای طلا و سکه')}\n⏰ {md.code(time_str)}\n\n"
    msg += f"🥇 طلا ۱۸ عیار = {md.code(get_nerkh_price_safe(['gold']))} تومان\n"
    msg += f"🥇 طلا ۲۴ عیار = {md.code(get_nerkh_price_safe(['gold24']))} تومان\n"
    msg += f"🪙 سکه امامی = {md.code(get_nerkh_price_safe(['coin']))} تومان\n"
    msg += f"🪙 سکه بهار = {md.code(get_nerkh_price_safe(['bahar']))} تومان\n"
    msg += f"🪙 نیم سکه = {md.code(get_nerkh_price_safe(['half']))} تومان\n"
    msg += f"🪙 ربع سکه = {md.code(get_nerkh_price_safe(['quarter']))} تومان"
    return msg

def gen_crypto_msg(time_str):
    crypto = get_crypto_prices_toman()
    msg = f"🪙 {md.bold('قیمت لحظه‌ای ارز دیجیتال')}\n⏰ {md.code(time_str)}\n\n"
    if crypto:
        for sym, prc in crypto.items():
            if sym != "USDT":
                emoji = "🟢" if sym in ["BTC", "ETH"] else "🔹"
                msg += f"تومان {md.code(prc)} = {md.code(sym)} {emoji}\n"
    else:
        msg += "⚠️ خطا در دریافت اطلاعات."
    return msg

def gen_usdt_msg(time_str):
    usdt_prices = get_multi_exchange_usdt_prices()
    msg = f"💵 {md.bold('مقایسه قیمت تتر (USDT)')}\n⏰ {md.code(time_str)}\n\n"
    if usdt_prices:
        sorted_prices = sorted(usdt_prices.items(), key=lambda item: item[1])
        for i, (exchange, price) in enumerate(sorted_prices):
            if i == 0:
                msg += f"🥇 {md.bold(exchange)}: {md.code(f'{price:,}')} تومان (ارزان‌ترین)\n"
            else:
                msg += f"🔸 {exchange}: {md.code(f'{price:,}')} تومان\n"
        if len(sorted_prices) > 1:
            diff = sorted_prices[-1][1] - sorted_prices[0][1]
            msg += f"\n📊 اختلاف ارزان‌ترین و گران‌ترین: {md.code(f'{diff:,}')} تومان"
    else:
        msg += "⚠️ خطا در دریافت اطلاعات."
    return msg

def gen_infographic_msg(time_str):
    msg = f"📊 {md.bold('خلاصه وضعیت بازار')}\n⏰ {md.code(time_str)}\n\n"
    msg += "📈 وضعیت کلی بازار در حال بررسی است...\n_(به زودی اینفوگرافیک اختصاصی این بخش فعال می‌شود)_"
    return msg

# 👇 زمان‌بند کانال (در حالت تست، محدودیت زمانی برداشته شده) 👇
async def channel_scheduler(bot: Bot):
    last_sent_minute = -1
    while True:
        try:
            now = datetime.now(pytz.timezone('Asia/Tehran'))
            hour, minute = now.hour, now.minute
            time_str = now.strftime('%H:%M')

            # 👇 برای تست، به جای if 8 <= hour < 20: از if True: استفاده شده است 👇
            if True:
                if minute != last_sent_minute:
                    total_minutes = (hour * 60) + minute # محاسبه ساده برای تست
                    msg_to_send = None

                    # در حالت تست، هر ۱ دقیقه یک نوع پیام را پشت سر هم می‌فرستد
                    if minute % 5 == 0: msg_to_send = gen_morning_msg()
                    elif minute % 5 == 1: msg_to_send = gen_fiat_msg(time_str)
                    elif minute % 5 == 2: msg_to_send = gen_gold_msg(time_str)
                    elif minute % 5 == 3: msg_to_send = gen_crypto_msg(time_str)
                    elif minute % 5 == 4: msg_to_send = gen_usdt_msg(time_str)

                    if msg_to_send:
                        await bot.send_message(chat_id=CHANNEL_ID, text=msg_to_send)
                        logging.info(f"Channel message sent at {time_str}")
                        last_sent_minute = minute
        except Exception as e:
            logging.error(f"Scheduler Error: {e}")
        await asyncio.sleep(10) # هر ۱۰ ثانیه چک می‌کند

dp = Dispatcher()

@dp.message(CommandStart())
async def command_start_handler(message: Message) -> None:
    welcome = "سلام! به ربات دیجی نوسان در بله خوش آمدید. ✅\n\nاز منوی زیر یکی از گزینه‌ها را انتخاب کنید:"
    await message.answer(welcome, reply_markup=get_main_menu())

@dp.message()
async def handle_message(message: Message) -> None:
    text = message.text.strip() if message.text else ""
    time_str, _ = get_dates_info()

    if text == "🚀 استارت / منوی اصلی" or text == "/start":
        await command_start_handler(message)
    elif text == "🪙 قیمت ارز دیجیتال":
        await message.answer("⏳ در حال دریافت اطلاعات...")
        msg = gen_crypto_msg(time_str) + "\n\n" + gen_usdt_msg(time_str)
        await message.answer(msg)
    elif text == "💱 قیمت ارزها":
        await message.answer(gen_fiat_msg(time_str))
    elif text == "🌕 قیمت طلا و سکه":
        await message.answer(gen_gold_msg(time_str))
    elif text == "📈 شاخص بورس":
        await message.answer("⏳ در حال دریافت اطلاعات بورس...")
        try:
            url = f"https://Api.Brsapi.ir/Tsetmc/Index.php?key={BRS_API_KEY.strip()}&type=1"
            headers = {'User-Agent': 'Mozilla/5.0'}
            res = await asyncio.to_thread(requests.get, url, headers=headers, timeout=10)
            if res.status_code == 200:
                data = res.json()
                index_val = data.get('index', 0)
                change_val = data.get('index_change', 0)
                equal_weight = data.get('index_equalWeight', 0)
                equal_weight_change = data.get('index_equalWeight_change', 0)
                state = data.get('state', 'نامشخص')
                mv = data.get('mv', 0)
                tno = data.get('tno', 0)
                tval = data.get('tval', 0)
                tvol = data.get('tvol', 0)
                mv_str = f"{mv / 10000000000000:,.0f}"
                tval_str = f"{tval / 10000000000:,.0f}"
                tno_str = f"{tno:,}"
                tvol_str = f"{tvol:,}"
                msg = f"📈 {md.bold('وضعیت شاخص‌های بورس تهران')}\n⏰ {md.code(time_str)}\n\n"
                msg += f"🔹 {md.bold('شاخص کل:')} {md.code(f'{index_val:,.0f}')} (تغییر: {change_val:,.0f})\n"
                msg += f"⚖️ {md.bold('شاخص هم‌وزن:')} {md.code(f'{equal_weight:,.0f}')} (تغییر: {equal_weight_change:,.0f})\n"
                msg += f"🔴 وضعیت بازار: {md.code(state)}\n\n"
                msg += f"📊 {md.bold('آمار کلی بازار:')}\n"
                msg += f"💰 ارزش کل بازار: {md.code(mv_str)} هزار میلیارد تومان\n"
                msg += f"💵 ارزش معاملات: {md.code(tval_str)} میلیارد تومان\n"
                msg += f"🧾 تعداد معاملات: {md.code(tno_str)} معامله\n"
                msg += f"📦 حجم معاملات: {md.code(tvol_str)} سهم\n\n"
                msg += "_توجه: در پلن رایگان، آمار مربوط به آخرین روز معاملاتی است._"
                await message.answer(msg)
            else:
                await message.answer(f"❌ خطا در دریافت اطلاعات. کد خطا: {res.status_code}")
        except Exception as e:
            await message.answer(f"❌ خطای شبکه: {str(e)}")
    elif text in ["🧮 ماشین‌حساب طلا", "💸 ماشین‌حساب اقساط", "💱 مبدل هوشمند ارز", "🌤 وضعیت آب‌وهوا", "🚨 آلارم قیمتی", "🎁 دعوت از دوستان"]:
        await message.answer(f"⏳ بخش {text} در حال توسعه است و به زودی به ربات اضافه می‌شود.")
    else:
        await message.answer("⏳ در حال پردازش...")
        res = await asyncio.to_thread(get_ai_response, text)
        await message.answer(res)

async def main() -> None:
    bot = Bot(token=BALE_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    asyncio.create_task(channel_scheduler(bot))
    await dp.start_polling(bot)

if __name__ == "__main__":
    threading.Thread(target=run_dummy_server, daemon=True).start()
    print("Starting Bale Bot...")
    asyncio.run(main())
