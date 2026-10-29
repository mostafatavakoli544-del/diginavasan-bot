import os
import logging
import requests
import threading
import http.server
import socketserver
import pytz
import asyncio
import jdatetime
from datetime import datetime, time, timedelta
from baleio import Bot, Dispatcher, md
from baleio.client.default import DefaultBotProperties
from baleio.enums import ParseMode
from baleio.filters import CommandStart
from baleio.types import Message, ReplyKeyboardMarkup, KeyboardButton

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

BALE_TOKEN = os.environ.get("BALE_TOKEN")
PORT = int(os.environ.get("PORT", 10000))
# فعلاً به ادمین می‌فرستیم چون کانال ساخته نشده. بعداً این رو به کانال تغییر می‌دیم.
TARGET_CHAT_ID = int(os.environ.get("ADMIN_ID", 718840144)) 
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
    # 👇 دکمه بازار کالا حذف شده است 👇
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

dp = Dispatcher()

@dp.message(CommandStart())
async def command_start_handler(message: Message) -> None:
    welcome = "سلام! به ربات دیجی نوسان در بله خوش آمدید. ✅\n\nاز منوی زیر یکی از گزینه‌ها را انتخاب کنید:"
    await message.answer(welcome, reply_markup=get_main_menu())

# 👇 این تابع، وظیفه‌ی گرفتن دیتای کالا و ساختن پیام رو بر عهده داره 👇
async def fetch_and_format_commodities():
    today = jdatetime.date.today()
    headers = {'User-Agent': 'Mozilla/5.0'}
    found_data = None
    date_found_str = ""
    payment_required = False

    for i in range(7):
        check_date = today - jdatetime.timedelta(days=i)
        date_str = check_date.strftime("%Y-%m-%d")
        url = f"https://Api.BrsApi.ir/IME/Physical.php?key={BRS_API_KEY.strip()}&date_start={date_str}&date_end={date_str}"
        
        res = await asyncio.to_thread(requests.get, url, headers=headers, timeout=15)
        
        if res.status_code == 200:
            try:
                data = res.json()
                if isinstance(data, list) and len(data) > 0:
                    found_data = data
                    date_found_str = check_date.strftime("%Y/%m/%d")
                    break
            except:
                pass
        elif res.status_code == 402:
            payment_required = True
            break
        
        await asyncio.sleep(1)

    if payment_required:
        return "⚠️ **اطلاعات موقتاً در دسترس نیست.**\nسهمیه رایگان سرور بورس کالا به پایان رسیده است. (در حال ارتقاء به نسخه پولی هستیم تا این بخش دوباره فعال شود)."

    if not found_data:
        return "⚠️ در ۷ روز گذشته معامله‌ای در بورس کالا ثبت نشده است."

    valid_items = [item for item in found_data if item.get('pc') is not None and item.get('l30')]
    valid_items.sort(key=lambda x: x.get('tval', 0), reverse=True)
    top_items = valid_items[:10]

    if not top_items:
        return "⚠️ معامله‌ای با قیمت ثبت‌شده یافت نشد."

    descriptions = {
        "تختال": "اسلب یا تختال، محصول نیمه‌ساخته فولادی است که مواد اولیه تولید ورق‌های فولادی محسوب می‌شود.",
        "ورق گرم": "ورقی که در دمای بالا نورد می‌شود. در صنایع ساختمانی و خودروسازی کاربرد دارد.",
        "ورق سرد": "ورقی که پس از نورد گرم، در دمای اتاق نورد می‌شود. سطح صاف‌تری دارد و در لوازم خانگی استفاده می‌شود.",
        "میلگرد": "فولاد آجدار که در ساخت‌وساز و بتن‌ریزی برای استحکام بخشیدن به سازه استفاده می‌شود.",
        "اسید سولفوریک": "یک اسید معدنی بسیار قوی که در صنایع شیمیایی، کودسازی و تصفیه فلزات کاربرد دارد.",
        "سود کاستیک": "یا سود سوزآور، ماده شیمیایی قوی که در تولید صابون، کاغذ و نساجی استفاده می‌شود.",
        "پلی اتیلن ترفتالات": "معروف به PET، پلیمری پرکاربرد در تولید الیاف نساجی و بطری‌های پلاستیکی (مثل بطری آب).",
        "ورق قلع اندود": "ورق فولادی که با لایه نازکی از قلع پوشیده شده و در ساخت قوطی کنسرو و نوشابه استفاده می‌شود.",
        "وکیوم باتوم": "ماده اولیه و پایه تولید قیر و آسفالت است که از پالایش نفت به دست می‌آید.",
        "شمش بلوم": "محصول نیمه‌ساخته فولادی با مقطع مربعی که برای تولید میلگرد و تیرآهن استفاده می‌شود."
    }

    msg = f"📦 {md.bold('مهم‌ترین معاملات بورس کالا')}\n"
    msg += f"🗓 آخرین تاریخ معامله: {md.code(date_found_str)}\n"
    msg += f"⏰ {md.code(get_dates_info()[0])}\n\n"
    
    for item in top_items:
        name = item.get('l30', 'نامشخص')
        producer = item.get('producer', 'نامشخص')
        price_toman = item.get('pc', 0) / 10
        volume = item.get('volume_contract') or 0
        
        price_str = f"{price_toman:,.0f}"
        volume_str = f"{volume:,}"
        
        msg += f"🔹 {md.bold(name)}\n"
        msg += f"🏭 کارخانه: {producer}\n"
        msg += f"💰 قیمت: {md.code(price_str)} تومان | 📦 حجم: {md.code(volume_str)} تن\n"
        
        for key, desc in descriptions.items():
            if key in name:
                msg += f"💡 {desc}\n"
                break
        
        msg += "\n"

    msg += "_(بر اساس ۱۰ کالای با بالاترین ارزش معامله)_"
    return msg

# 👇 این تابع، زمان‌بند ارسال روزانه به کانال (فعلا ادمین) است 👇
async def daily_commodity_scheduler(bot: Bot):
    while True:
        try:
            now = datetime.now(pytz.timezone('Asia/Tehran'))
            # تنظیم ساعت روی 12:35 ظهر
            target_time = now.replace(hour=12, minute=35, second=0, microsecond=0)
            if now >= target_time:
                target_time += timedelta(days=1)
            
            wait_seconds = (target_time - now).total_seconds()
            logging.info(f"Next commodity broadcast scheduled for {target_time}")
            await asyncio.sleep(wait_seconds)
            
            # زمان ارسال فرا رسیده
            logging.info("Fetching commodity data for broadcast...")
            msg = await fetch_and_format_commodities()
            
            # ارسال به TARGET_CHAT_ID (که فعلا ادمین است)
            await bot.send_message(chat_id=TARGET_CHAT_ID, text=msg)
            logging.info("Commodity broadcast sent successfully.")
            
            await asyncio.sleep(60) # یک دقیقه صبر کن تا از حلقه خارج نشه
        except Exception as e:
            logging.error(f"Error in daily broadcast: {e}")
            await asyncio.sleep(60)


@dp.message()
async def handle_message(message: Message) -> None:
    text = message.text.strip() if message.text else ""
    time_str, _ = get_dates_info()

    if text == "🚀 استارت / منوی اصلی" or text == "/start":
        await command_start_handler(message)
        
    elif text == "/test_kala": # 👈 کلید مخفی تست
        await message.answer("⏳ در حال دریافت اطلاعات تست کالا...")
        msg = await fetch_and_format_commodities()
        await message.answer(msg)

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
                
                # 👇 استخراج دیتاهای جدید 👇
                mv = data.get('mv', 0)
                tno = data.get('tno', 0)
                tval = data.get('tval', 0)
                tvol = data.get('tvol', 0)

                # فرمت کردن اعداد بزرگ
                # mv (ارزش بازار) بر حسب ریال است. تقسیم بر 10^13 میکنیم تا بشه "هزار میلیارد تومان"
                mv_str = f"{mv / 10000000000000:,.0f}"
                # tval (ارزش معاملات) بر حسب ریال است. تقسیم بر 10^10 میکنیم تا بشه "میلیارد تومان"
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

    elif text == "🧮 ماشین‌حساب طلا":
        await message.answer("⏳ بخش ماشین‌حساب طلا در حال آماده‌سازی است...")
        
    elif text == "💸 ماشین‌حساب اقساط":
        await message.answer("⏳ بخش ماشین‌حساب اقساط در حال آماده‌سازی است...")

    elif text in ["💱 مبدل هوشمند ارز", "🌤 وضعیت آب‌وهوا", "🚨 آلارم قیمتی", "🎁 دعوت از دوستان"]:
        await message.answer(f"⏳ بخش {text} در حال توسعه است و به زودی به ربات اضافه می‌شود.")

    else:
        await message.answer("⏳ در حال پردازش...")
        res = await asyncio.to_thread(get_ai_response, text)
        await message.answer(res)

async def main() -> None:
    bot = Bot(token=BALE_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    
    # روشن کردن زمان‌بند در پس‌زمینه
    asyncio.create_task(daily_commodity_scheduler(bot))
    
    await dp.start_polling(bot)

if __name__ == "__main__":
    threading.Thread(target=run_dummy_server, daemon=True).start()
    print("Starting Bale Bot...")
    asyncio.run(main())
