import os
import logging
import requests
import threading
import http.server
import socketserver
import pytz
import asyncio
import jdatetime
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

def run_dummy_server():
    Handler = http.server.SimpleHTTPRequestHandler
    with socketserver.TCPServer(("", PORT), Handler) as httpd: httpd.serve_forever()

def get_dates_info():
    now = datetime.now(pytz.timezone('Asia/Tehran'))
    return now.strftime('%H:%M'), now.hour

def get_nerkh_price(key):
    try:
        response = requests.get("https://nerkh.jahankhahan.shop/data/live.json", timeout=30)
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

    # ۱. نوبیتکس
    try:
        res = requests.get("https://apiv2.nobitex.ir/market/stats?srcCurrency=usdt&dstCurrency=rls", headers=headers, timeout=15)
        if res.status_code == 200 and 'usdt-rls' in res.json().get('stats', {}):
            results['نوبیتکس'] = int(float(res.json()['stats']['usdt-rls']['latest']) / 10)
    except Exception: pass

    # ۲. والکس
    try:
        res = requests.get("https://api.wallex.ir/v1/markets", headers=headers, timeout=15)
        if res.status_code == 200:
            data = res.json()
            if 'USDTTMN' in data.get('result', {}).get('symbols', {}):
                results['والکس'] = int(float(data['result']['symbols']['USDTTMN']['stats']['lastPrice']))
    except Exception: pass

    # ۳. رمزینکس
    try:
        res = requests.get("https://publicapi.ramzinex.com/exchange/api/v1.0/exchange/pairs", headers=headers, timeout=15)
        if res.status_code == 200:
            for item in res.json().get('data', []):
                if item.get('name', {}).get('en') == 'tether/rial' and float(item.get('sell', 0)) > 0:
                    results['رمزینکس'] = int(float(item['sell']) / 10)
                    break
    except Exception: pass

    # ۴. بیت‌پین
    try:
        res = requests.get("https://api.bitpin.ir/api/v1/mkt/tickers/", headers=headers, timeout=15)
        if res.status_code == 200:
            for item in res.json():
                if item.get('symbol') == 'USDT_IRT':
                    results['بیت‌پین'] = int(float(item.get('price')))
                    break
    except Exception: pass

    # ۵. تبدیل
    try:
        res = requests.get("https://api.tabdeal.org/api/v1/market/statistics", headers=headers, timeout=15)
        if res.status_code == 200:
            data = res.json()
            if 'USDTIRT' in data:
                results['تبدیل'] = int(float(data['USDTIRT'].get('lastPrice', 0)))
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

# 👇 تابع مخفی برای دریافت دیتای بازار کالا 👇
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
        
        res = await asyncio.to_thread(requests.get, url, headers=headers, timeout=30)
        
        if res.status_code == 200:
            try:
                data = res.json()
                if isinstance(data, list) and len(data) > 0:
                    found_data = data
                    date_found_str = check_date.strftime("%Y/%m/%d")
                    break
            except: pass
        elif res.status_code == 402:
            payment_required = True
            break
        await asyncio.sleep(1)

    if payment_required:
        return "⚠️ **اطلاعات موقتاً در دسترس نیست.**\nسهمیه رایگان سرور بورس کالا به پایان رسیده است."
    if not found_data:
        return "⚠️ در ۷ روز گذشته معامله‌ای در بورس کالا ثبت نشده است."

    valid_items = [item for item in found_data if item.get('pc') is not None and item.get('l30')]
    valid_items.sort(key=lambda x: x.get('tval', 0), reverse=True)
    top_items = valid_items[:10]

    if not top_items:
        return "⚠️ معامله‌ای با قیمت ثبت‌شده یافت نشد."

    descriptions = {
        "تختال": "اسلب یا تختال، محصول نیمه‌ساخته فولادی است.",
        "ورق گرم": "ورقی که در دمای بالا نورد می‌شود.",
        "ورق سرد": "ورقی که در دمای اتاق نورد می‌شود.",
        "میلگرد": "فولاد آجدار برای بتن‌ریزی.",
        "اسید سولفوریک": "اسید معدنی قوی در صنایع شیمیایی.",
        "سود کاستیک": "ماده شیمیایی قوی در تولید صابون و کاغذ.",
        "پلی اتیلن ترفتالات": "پلیمر پرکاربرد در بطری‌های پلاستیکی.",
        "ورق قلع اندود": "ورق فولادی برای ساخت قوطی کنسرو.",
        "وکیوم باتوم": "ماده اولیه تولید قیر و آسفالت.",
        "شمش بلوم": "محصول نیمه‌ساخته فولادی برای میلگرد."
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

    elif text == "/test_kala":
        await message.answer("⏳ در حال دریافت اطلاعات تست کالا...")
        msg = await fetch_and_format_commodities()
        await message.answer(msg)

    elif text == "🪙 قیمت ارز دیجیتال":
        await message.answer("⏳ در حال دریافت اطلاعات...")
        
        # ۱. قیمت ارزهای دیجیتال
        crypto = get_crypto_prices_toman()
        crypto_names = {"BTC": "بیت‌کوین", "ETH": "اتریوم", "BNB": "بایننس کوین", "TRX": "ترون", "SOL": "سولانا", "DOGE": "دوج کوین", "TON": "تون کوین"}
        
        msg_crypto = f"🪙 {md.bold('قیمت لحظه‌ای ارز دیجیتال')}\n⏰ {md.code(time_str)}\n\n"
        if crypto:
            for sym, prc in crypto.items():
                fa_name = crypto_names.get(sym, sym)
                emoji = "🟢" if sym in ["BTC", "ETH"] else "🔹"
                msg_crypto += f"{emoji} {md.bold(fa_name)} ({sym}): {md.code(prc)} تومان\n"
        else:
            msg_crypto += "⚠️ خطا در دریافت اطلاعات."
        
        await message.answer(msg_crypto)
        await asyncio.sleep(1)

        # ۲. مقایسه تتر
        usdt_prices = get_multi_exchange_usdt_prices()
        msg_usdt = f"💵 {md.bold('مقایسه قیمت تتر (USDT)')}\n⏰ {md.code(time_str)}\n\n"
        if usdt_prices:
            sorted_prices = sorted(usdt_prices.items(), key=lambda item: item[1])
            for i, (exchange, price) in enumerate(sorted_prices):
                if i == 0:
                    msg_usdt += f"🥇 {md.bold(exchange)}: {md.code(f'{price:,}')} تومان (ارزان‌ترین)\n"
                else:
                    msg_usdt += f"🔸 {exchange}: {md.code(f'{price:,}')} تومان\n"
            if len(sorted_prices) > 1:
                diff = sorted_prices[-1][1] - sorted_prices[0][1]
                msg_usdt += f"\n📊 اختلاف ارزان‌ترین و گران‌ترین: {md.code(f'{diff:,}')} تومان"
        else:
            msg_usdt += "⚠️ خطا در دریافت اطلاعات."
        
        await message.answer(msg_usdt)

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

    elif text == "📈 شاخص بورس":
        await message.answer("⏳ در حال دریافت اطلاعات بورس...")
        try:
            url = f"https://Api.Brsapi.ir/Tsetmc/Index.php?key={BRS_API_KEY.strip()}&type=1"
            headers = {'User-Agent': 'Mozilla/5.0'}
            res = await asyncio.to_thread(requests.get, url, headers=headers, timeout=30)
            
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
    bot = Bot(token=BALE_TOKEN, default=DefaultBotProperties())
    await dp.start_polling(bot)

if __name__ == "__main__":
    threading.Thread(target=run_dummy_server, daemon=True).start()
    print("Starting Bale Bot...")
    asyncio.run(main())
