import os
import logging
import requests
import threading
import http.server
import socketserver
import pytz
import asyncio
import jdatetime
from datetime import datetime, time
from baleio import Bot, Dispatcher, md
from baleio.client.default import DefaultBotProperties
from baleio.enums import ParseMode
from baleio.filters import CommandStart
from baleio.types import Message, ReplyKeyboardMarkup, KeyboardButton
from supabase import create_client, Client

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

BALE_TOKEN = os.environ.get("BALE_TOKEN")
PORT = int(os.environ.get("PORT", 10000))
CHANNEL_ID = "@diginavasan"
ADMIN_ID = 718840144

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
BRS_API_KEY = os.environ.get("BRS_API_KEY")

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

def get_crypto_prices_toman():
    try:
        coins = {
            "BTC": "btc", "ETH": "eth", "USDT": "usdt", 
            "BNB": "bnb", "TRX": "trx", "SOL": "sol", 
            "DOGE": "doge", "TON": "ton"
        }
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
        [KeyboardButton(text="📦 بازار کالا")], # 👈 دکمه جدید بازار کالا
        [KeyboardButton(text="🧮 ماشین‌حساب طلا"), KeyboardButton(text="💸 ماشین‌حساب اقساط")],
        [KeyboardButton(text="💱 مبدل هوشمند ارز"), KeyboardButton(text="🌤 وضعیت آب‌وهوا")],
        [KeyboardButton(text="🚨 آلارم قیمتی"), KeyboardButton(text="🤖 چت با هوش مصنوعی")],
        [KeyboardButton(text="🎁 دعوت از دوستان")]
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
    time_str, _ = get_dates_info()

    if text == "🚀 استارت / منوی اصلی" or text == "/start":
        await command_start_handler(message)
        
    elif text == "💱 قیمت ارزها":
        msg = f"💱 {md.bold('قیمت لحظه‌ای ارزها')}\n⏰ {md.code(time_str)}\n\n"
        msg += f"💵 دلار = {md.code(get_nerkh_price_safe(['dollar', 'usd']))} تومان\n"
        msg += f"💶 یورو = {md.code(get_nerkh_price_safe(['euro', 'eur']))} تومان\n"
        msg += f"💷 پوند = {md.code(get_nerkh_price_safe(['pound', 'gbp']))} تومان\n"
        msg += f"🇦🇪 درهم = {md.code(get_nerkh_price_safe(['aed']))} تومان\n"
        msg += f"🇹🇷 لیر = {md.code(get_nerkh_price_safe(['lira']))} تومان\n"
        msg += f"🇨🇳 یوان = {md.code(get_nerkh_price_safe(['yuan']))} تومان\n"
        msg += f"🇨🇦 کانادا = {md.code(get_nerkh_price_safe(['cad']))} تومان\n"
        msg += f"🇦🇺 استرالیا = {md.code(get_nerkh_price_safe(['aud']))} تومان"
        await message.answer(msg)

    elif text == "🌕 قیمت طلا و سکه":
        msg = f"🌕 {md.bold('قیمت لحظه‌ای طلا و سکه')}\n⏰ {md.code(time_str)}\n\n"
        msg += f"🥇 طلا ۱۸ عیار = {md.code(get_nerkh_price_safe(['gold']))} تومان\n"
        msg += f"🥇 طلا ۲۴ عیار = {md.code(get_nerkh_price_safe(['gold24']))} تومان\n"
        msg += f"🪙 سکه امامی = {md.code(get_nerkh_price_safe(['coin']))} تومان\n"
        msg += f"🪙 سکه بهار = {md.code(get_nerkh_price_safe(['bahar']))} تومان\n"
        msg += f"🪙 نیم سکه = {md.code(get_nerkh_price_safe(['half']))} تومان\n"
        msg += f"🪙 ربع سکه = {md.code(get_nerkh_price_safe(['quarter']))} تومان"
        await message.answer(msg)

    elif text == "🪙 قیمت ارز دیجیتال":
        crypto = get_crypto_prices_toman()
        msg = f"🪙 {md.bold('قیمت لحظه‌ای ارز دیجیتال')}\n⏰ {md.code(time_str)}\n\n"
        if crypto:
            for sym, prc in crypto.items():
                emoji = "🟢" if sym in ["BTC", "ETH"] else "🔹"
                msg += f"تومان {md.code(prc)} = {md.code(sym)} {emoji}\n"
        else:
            msg += "⚠️ خطا در دریافت اطلاعات ارز دیجیتال."
        await message.answer(msg)

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

                msg = f"📈 {md.bold('وضعیت شاخص‌های بورس تهران')}\n⏰ {md.code(time_str)}\n\n"
                msg += f"🔹 {md.bold('شاخص کل:')} {md.code(f'{index_val:,.0f}')} (تغییر: {change_val:,.0f})\n"
                msg += f"⚖️ {md.bold('شاخص هم‌وزن:')} {md.code(f'{equal_weight:,.0f}')} (تغییر: {equal_weight_change:,.0f})\n"
                msg += f"🔴 وضعیت بازار: {md.code(state)}\n\n"
                msg += "_شاخص کل نشان‌دهنده روند کلی بازار و شاخص هم‌وزن نشان‌دهنده عملکرد اکثر شرکت‌هاست._"
                await message.answer(msg)
            else:
                await message.answer(f"❌ خطا در دریافت اطلاعات. کد خطا: {res.status_code}")
        except Exception as e:
            await message.answer(f"❌ خطای شبکه: {str(e)}")

    # 👇 بخش جدید: بازار کالا با دسته‌بندی و توضیحات 👇
    elif text == "📦 بازار کالا":
        await message.answer("⏳ در حال دریافت آمار معاملات کالا...")
        try:
            # گرفتن تاریخ امروز به شمسی برای API
            today_shamsi = jdatetime.date.today().strftime("%Y-%m-%d")
            
            # آدرس API (تاریخ شروع و پایان رو امروز می‌ذاریم)
            url = f"https://Api.BrsApi.ir/IME/Physical.php?key={BRS_API_KEY.strip()}&date_start={today_shamsi}&date_end={today_shamsi}"
            headers = {'User-Agent': 'Mozilla/5.0'}
            
            res = await asyncio.to_thread(requests.get, url, headers=headers, timeout=10)
            
            if res.status_code == 200:
                data = res.json()
                if not data:
                    await message.answer("⚠️ امروز هنوز معامله‌ای در بورس کالا ثبت نشده است.")
                    return

                # پیدا کردن آخرین معامله برای کالاهای مهم
                target_commodities = {
                    "مس کاتد": "پایه سیم و کابل",
                    "آهن اسفنجی": "جایگزین آهن‌قراضه در کوره",
                    "گندله سنگ آهن": "مواد اولیه کوره بلند",
                    "وکیوم باتوم": "پایه قیر و آسفالت",
                    "شمش آلومینیوم": "فلز سبک صنایع",
                    "کنسانتره سنگ آهن": "پودر غلیظ‌شده سنگ آهن"
                }
                
                found_items = {}
                
                # جستجو در لیست معاملات
                for item in data:
                    name = item.get('l30', '')
                    for key in target_commodities.keys():
                        if key in name:
                            # قیمت پایانی به ریال است، تقسیم بر 10 می‌کنیم تا تومان شود
                            price_toman = item.get('pc', 0) / 10
                            volume = item.get('volume_contract', 0)
                            
                            # اگر قبلا پیدا شده بود، جدیدترین رو نگه داریم (بر اساس تاریخ معامله)
                            if key not in found_items or item.get('date_trade') > found_items[key]['date']:
                                found_items[key] = {
                                    "price": price_toman,
                                    "volume": volume,
                                    "date": item.get('date_trade')
                                }

                # ساخت متن پیام
                msg = f"📦 {md.bold('آخرین معاملات بورس کالا')}\n⏰ {md.code(time_str)}\n\n"
                
                # دسته ۱: فلزات اساسی
                msg += f"🥇 {md.bold('فلزات اساسی:')}\n"
                if "مس کاتد" in found_items:
                    i = found_items["مس کاتد"]
                    msg += f"🔹 {md.bold('مس کاتد')} ({target_commodities['مس کاتد']})\n   قیمت: {md.code(f'{i['price']:,.0f}')} تومان | حجم: {md.code(f'{i['volume']:,}')} تن\n"
                if "شمش آلومینیوم" in found_items:
                    i = found_items["شمش آلومینیوم"]
                    msg += f"🔹 {md.bold('شمش آلومینیوم')} ({target_commodities['شمش آلومینیوم']})\n   قیمت: {md.code(f'{i['price']:,.0f}')} تومان | حجم: {md.code(f'{i['volume']:,}')} تن\n"
                
                # دسته ۲: فولاد و مواد معدنی
                msg += f"\n🏗️ {md.bold('فولاد و مواد معدنی:')}\n"
                for key in ["آهن اسفنجی", "گندله سنگ آهن", "کنسانتره سنگ آهن"]:
                    if key in found_items:
                        i = found_items[key]
                        msg += f"🔹 {md.bold(key)} ({target_commodities[key]})\n   قیمت: {md.code(f'{i['price']:,.0f}')} تومان | حجم: {md.code(f'{i['volume']:,}')} تن\n"
                
                # دسته ۳: انرژی و فرآورده‌ها
                msg += f"\n🛢️ {md.bold('انرژی و فرآورده‌ها:')}\n"
                if "وکیوم باتوم" in found_items:
                    i = found_items["وکیوم باتوم"]
                    msg += f"🔹 {md.bold('وکیوم باتوم')} ({target_commodities['وکیوم باتوم']})\n   قیمت: {md.code(f'{i['price']:,.0f}')} تومان | حجم: {md.code(f'{i['volume']:,}')} تن\n"

                msg += "\n_(قیمت‌ها بر اساس آخرین معامله ثبت‌شده در امروز است)_"
                await message.answer(msg)
            else:
                await message.answer(f"❌ خطا در دریافت اطلاعات بازار کالا. کد خطا: {res.status_code}")
        except Exception as e:
            await message.answer(f"❌ خطای شبکه در بازار کالا: {str(e)}")

    elif text == "🤖 چت با هوش مصنوعی":
        await message.answer("🤖 لطفاً سوال خود را بپرسید تا هوش مصنوعی پاسخ دهد:")

    elif text in ["🧮 ماشین‌حساب طلا", "💸 ماشین‌حساب اقساط", "💱 مبدل هوشمند ارز", "🌤 وضعیت آب‌وهوا", "🚨 آلارم قیمتی", "🎁 دعوت از دوستان"]:
        await message.answer(f"⏳ بخش {text} در حال توسعه است و به زودی به ربات اضافه می‌شود.")

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
