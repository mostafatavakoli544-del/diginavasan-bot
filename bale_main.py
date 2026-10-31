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

# 👇 آدرس فایل آنلاین کدهای پلاک خودت 👇
PLATE_DATA_URL = "https://raw.githubusercontent.com/mostafatavakoli544-del/diginavasan-bot/refs/heads/main/plates.json"

# دیتای پیش‌فرض کامل (در صورت قطعی اینترنت)
DEFAULT_PLATE_DATA = {
    "11": "تهران", "22": "تهران", "33": "تهران", "44": "تهران", "55": "تهران", "66": "تهران", "77": "تهران", "88": "تهران", "99": "تهران",
    "10": "تهران", "20": "تهران", "30": "تهران", "40": "تهران", "50": "تهران", "60": "تهران", "70": "تهران", "80": "تهران", "90": "تهران",
    "21": "البرز (کرج) / تهران", "38": "البرز (کرج) / تهران", "68": "البرز (کرج) / تهران", "78": "البرز (کرج) / تهران",
    "12": "خراسان رضوی", "32": "خراسان رضوی", "36": "خراسان رضوی", "42": "خراسان رضوی", "74": "خراسان رضوی",
    "13": "اصفهان", "23": "اصفهان", "43": "اصفهان", "53": "اصفهان", "67": "اصفهان",
    "63": "فارس", "73": "فارس", "83": "فارس", "93": "فارس",
    "14": "خوزستان", "24": "خوزستان", "34": "خوزستان",
    "15": "آذربایجان شرقی", "25": "آذربایجان شرقی", "35": "آذربایجان شرقی",
    "17": "آذربایجان غربی", "27": "آذربایجان غربی", "37": "آذربایجان غربی",
    "62": "مازندران", "72": "مازندران", "82": "مازندران", "92": "مازندران",
    "46": "گیلان", "56": "گیلان", "76": "گیلان",
    "45": "کرمان", "65": "کرمان", "75": "کرمان",
    "59": "گلستان", "69": "گلستان", "79": "قزوین", "89": "قزوین",
    "16": "قم", "18": "همدان", "28": "همدان", "19": "کرمانشاه", "29": "کرمانشاه",
    "26": "خراسان شمالی", "31": "لرستان", "41": "لرستان", "47": "مرکزی", "57": "مرکزی",
    "48": "بوشهر", "58": "بوشهر", "49": "کهگیلویه و بویراحمد", "51": "کردستان", "61": "کردستان",
    "52": "خراسان جنوبی", "54": "یزد", "64": "یزد", "71": "چهارمحال و بختیاری", "81": "چهارمحال و بختیاری",
    "84": "هرمزگان", "94": "هرمزگان", "85": "سیستان و بلوچستان", "95": "سیستان و بلوچستان",
    "86": "سمنان", "96": "سمنان", "87": "زنجان", "97": "زنجان", "91": "اردبیل", "98": "ایلام"
}
CURRENT_PLATE_DATA = DEFAULT_PLATE_DATA.copy()

# کش سراسری
PRICE_CACHE = {
    "fiat": "", "gold": "", "crypto": "", "usdt": "",
    "gold_18_num": 0, "gold_24_num": 0, "last_update": "در حال دریافت..."
}
user_states = {}

def run_dummy_server():
    Handler = http.server.SimpleHTTPRequestHandler
    with socketserver.TCPServer(("", PORT), Handler) as httpd: httpd.serve_forever()

def get_dates_info():
    now = datetime.now(pytz.timezone('Asia/Tehran'))
    return now.strftime('%H:%M'), now.hour

def fetch_plate_data_from_url():
    global CURRENT_PLATE_DATA
    try:
        res = requests.get(PLATE_DATA_URL, timeout=10)
        if res.status_code == 200:
            new_data = res.json()
            CURRENT_PLATE_DATA.update(new_data)
            logging.info("Plate data updated successfully from URL.")
    except Exception as e:
        logging.error(f"Failed to fetch plate data: {e}")

async def update_plate_data_task():
    while True:
        await asyncio.to_thread(fetch_plate_data_from_url)
        await asyncio.sleep(86400) # هر ۲۴ ساعت یکبار

# ==========================================
# توابع قیمت
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

def get_weather(city):
    try:
        url = f"https://wttr.in/{city}?format=j1"
        res = requests.get(url, timeout=15)
        if res.status_code == 200:
            data = res.json()
            current = data['current_condition'][0]
            temp = current['temp_C']
            desc = current['lang_fa'][0]['value'] if 'lang_fa' in current else current['weatherDesc'][0]['value']
            humidity = current['humidity']
            now = datetime.now(pytz.timezone('Asia/Tehran')).strftime('%H:%M')
            return f"🌤 **وضعیت آب‌وهوای {city}**\n\n🌡 دما: {temp} درجه سانتی‌گراد\n☁️ وضعیت: {desc}\n💧 رطوبت: {humidity}%\n⏰ آخرین به‌روزرسانی: `{now}`"
        return "❌ شهر مورد نظر پیدا نشد."
    except Exception:
        return "❌ خطا در دریافت اطلاعات آب‌وهوا."

# ==========================================
# ساخت پیام‌ها
# ==========================================
def gen_fiat_msg(): return PRICE_CACHE["fiat"]
def gen_gold_msg(): return PRICE_CACHE["gold"]
def gen_crypto_msg(): return PRICE_CACHE["crypto"]
def gen_usdt_msg(): return PRICE_CACHE["usdt"]

async def update_cache_task():
    global PRICE_CACHE
    while True:
        try:
            now = datetime.now(pytz.timezone('Asia/Tehran'))
            time_str = now.strftime('%H:%M')
            
            PRICE_CACHE["fiat"] = await asyncio.to_thread(lambda: (
                f"💱 **قیمت لحظه‌ای ارزها**\n⏰ `{time_str}`\n\n"
                f"💵 دلار = `{get_nerkh_price_safe(['dollar', 'usd'])}` تومان\n"
                f"💶 یورو = `{get_nerkh_price_safe(['euro', 'eur'])}` تومان\n"
                f"💷 پوند = `{get_nerkh_price_safe(['pound', 'gbp'])}` تومان\n"
                f"🇦🇪 درهم = `{get_nerkh_price_safe(['aed'])}` تومان\n"
                f"🇹🇷 لیر = `{get_nerkh_price_safe(['lira'])}` تومان\n"
                f"🇨🇳 یوان = `{get_nerkh_price_safe(['yuan', 'cny'])}` تومان\n"
                f"🇨🇦 کانادا = `{get_nerkh_price_safe(['cad'])}` تومان\n"
                f"🇦🇺 استرالیا = `{get_nerkh_price_safe(['aud'])}` تومان\n"
                f"🇯🇵 ین ژاپن = `{get_nerkh_price_safe(['jpy'])}` تومان\n"
                f"🇷🇺 روبل = `{get_nerkh_price_safe(['rub'])}` تومان"
            ))

            gold_18_str = get_nerkh_price_safe(['gold'])
            gold_24_str = get_nerkh_price_safe(['gold24'])
            PRICE_CACHE["gold_18_num"] = int(gold_18_str.replace(',', '')) if gold_18_str != "0" else 0
            PRICE_CACHE["gold_24_num"] = int(gold_24_str.replace(',', '')) if gold_24_str != "0" else 0

            PRICE_CACHE["gold"] = await asyncio.to_thread(lambda: (
                f"🌕 **قیمت لحظه‌ای طلا و سکه**\n⏰ `{time_str}`\n\n"
                f"🥇 طلا ۱۸ عیار = `{gold_18_str}` تومان\n"
                f"🥇 طلا ۲۴ عیار = `{gold_24_str}` تومان\n"
                f"🪙 سکه امامی = `{get_nerkh_price_safe(['coin'])}` تومان\n"
                f"🪙 سکه بهار = `{get_nerkh_price_safe(['bahar'])}` تومان\n"
                f"🪙 نیم سکه = `{get_nerkh_price_safe(['half'])}` تومان\n"
                f"🪙 ربع سکه = `{get_nerkh_price_safe(['quarter'])}` تومان"
            ))

            crypto = await asyncio.to_thread(get_crypto_prices_toman)
            crypto_names = {"BTC": "بیت‌کوین", "ETH": "اتریوم", "BNB": "بایننس کوین", "TRX": "ترون", "SOL": "سولانا", "DOGE": "دوج کوین", "TON": "تون کوین"}
            crypto_lines = []
            if crypto:
                for sym, prc in crypto.items():
                    fa_name = crypto_names.get(sym, sym)
                    emoji = "🟢" if sym in ["BTC", "ETH"] else "🔹"
                    crypto_lines.append(f"{emoji} **{fa_name} ({sym})**: `{prc}` تومان")
            PRICE_CACHE["crypto"] = f"🪙 **قیمت لحظه‌ای ارز دیجیتال**\n⏰ `{time_str}`\n\n" + "\n".join(crypto_lines)

            usdt_prices = await asyncio.to_thread(get_multi_exchange_usdt_prices)
            usdt_lines = []
            if usdt_prices:
                sorted_prices = sorted(usdt_prices.items(), key=lambda item: item[1])
                for i, (exchange, price) in enumerate(sorted_prices):
                    if i == 0: usdt_lines.append(f"🥇 **{exchange}**: `{price:,}` تومان (ارزان‌ترین)")
                    else: usdt_lines.append(f"🔸 {exchange}: `{price:,}` تومان")
                if len(sorted_prices) > 1:
                    diff = sorted_prices[-1][1] - sorted_prices[0][1]
                    usdt_lines.append(f"\n📊 اختلاف: `{diff:,}` تومان")
            PRICE_CACHE["usdt"] = f"💵 **مقایسه قیمت تتر (USDT)**\n⏰ `{time_str}`\n\n" + "\n".join(usdt_lines)

            PRICE_CACHE["last_update"] = time_str
        except Exception as e:
            logging.error(f"Cache update error: {e}")
        await asyncio.sleep(30)

# ==========================================
# پلاک‌خوان (نسخه نهایی و بدون باگ)
# ==========================================
async def check_plate(plate_code):
    if plate_code in CURRENT_PLATE_DATA:
        province = CURRENT_PLATE_DATA[plate_code]
        if "/" in province:
            return f"🔍 **نتیجه:**\n\nکد `{plate_code}` مشترک بین **{province}** است."
        return f"🔍 **نتیجه:**\n\nکد `{plate_code}` مربوط به **{province}** است."
    
    return f"❌ کد پلاک `{plate_code}` معتبر نیست."

# ==========================================
# کیبوردها
# ==========================================
def get_main_menu():
    keyboard = [
        [KeyboardButton(text="🚀 استارت / منوی اصلی")],
        [KeyboardButton(text="💱 قیمت ارزها"), KeyboardButton(text="🌕 قیمت طلا و سکه")],
        [KeyboardButton(text="🪙 قیمت ارز دیجیتال"), KeyboardButton(text="💵 مقایسه قیمت تتر")],
        [KeyboardButton(text="🧮 ماشین‌حساب طلا"), KeyboardButton(text="💸 ماشین‌حساب اقساط")],
        [KeyboardButton(text="🔍 پلاک‌خوان"), KeyboardButton(text="🌤 وضعیت آب‌وهوا")],
        [KeyboardButton(text="💱 مبدل هوشمند ارز"), KeyboardButton(text="🤖 چت با هوش مصنوعی")],
        [KeyboardButton(text="🚨 آلارم قیمتی")]
    ]
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)

def get_back_menu():
    keyboard = [[KeyboardButton(text="🔙 بازگشت به منوی اصلی")]]
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)

dp = Dispatcher()

@dp.message(CommandStart())
async def command_start_handler(message: Message) -> None:
    user_states.pop(message.from_user.id, None)
    welcome = "سلام! به ربات دیجی نوسان در بله خوش آمدید. ✅\n\nاز منوی زیر یکی از گزینه‌ها را انتخاب کنید:"
    await message.answer(welcome, reply_markup=get_main_menu())

@dp.message()
async def handle_message(message: Message) -> None:
    text = message.text.strip() if message.text else ""
    user_id = message.from_user.id
    current_state = user_states.get(user_id)

    if text == "🔙 بازگشت به منوی اصلی":
        user_states.pop(user_id, None)
        await message.answer("به منوی اصلی بازگشتید.", reply_markup=get_main_menu())
        return

    # ------------------ پلاک‌خوان ------------------
    if current_state == "waiting_for_plate_code":
        plate_code = text.strip()
        if len(plate_code) == 2 and plate_code.isdigit():
            result = await check_plate(plate_code)
            await message.answer(result, reply_markup=get_back_menu()) # دکمه بازگشت حفظ می‌شود
            # وضعیت را پاک نمی‌کنیم تا کاربر بتواند عدد دیگری را امتحان کند
        else:
            await message.answer("❌ لطفاً فقط **دو رقم** سمت راست پلاک را وارد کنید (مثلاً 33):", reply_markup=get_back_menu())

    # ------------------ ماشین‌حساب طلا ------------------
    elif current_state == "gold_waiting_price":
        try:
            gold_price = float(text.replace(',', ''))
            if gold_price <= 0: raise ValueError
            user_states[user_id] = {"state": "gold_waiting_weight", "price": gold_price}
            await message.answer("⚖️ حالا وزن طلا را به **گرم** وارد کنید (مثلاً: 5):", reply_markup=get_back_menu())
        except ValueError:
            await message.answer("❌ لطفاً یک عدد معتبر برای قیمت وارد کنید.", reply_markup=get_back_menu())
            
    elif isinstance(current_state, dict) and current_state.get("state") == "gold_waiting_weight":
        try:
            weight = float(text.replace(',', ''))
            if weight <= 0: raise ValueError
            current_state["weight"] = weight
            current_state["state"] = "gold_waiting_wage"
            await message.answer("🛠 درصد اجرت ساخت را وارد کنید (اگر ندارد، 0 بزنید):", reply_markup=get_back_menu())
        except ValueError:
            await message.answer("❌ لطفاً یک عدد معتبر برای وزن وارد کنید.", reply_markup=get_back_menu())

    elif isinstance(current_state, dict) and current_state.get("state") == "gold_waiting_wage":
        try:
            wage_pct = float(text)
            if wage_pct < 0: raise ValueError
            current_state["wage_pct"] = wage_pct
            current_state["state"] = "gold_waiting_profit"
            await message.answer("🏪 درصد سود فروشنده را وارد کنید (اگر ندارد، 0 بزنید):", reply_markup=get_back_menu())
        except ValueError:
            await message.answer("❌ لطفاً یک عدد معتبر برای درصد وارد کنید.", reply_markup=get_back_menu())

    elif isinstance(current_state, dict) and current_state.get("state") == "gold_waiting_profit":
        try:
            profit_pct = float(text)
            if profit_pct < 0: raise ValueError
            current_state["profit_pct"] = profit_pct
            current_state["state"] = "gold_waiting_tax"
            await message.answer("📄 درصد مالیات بر ارزش افزوده را وارد کنید (معمولاً 9):", reply_markup=get_back_menu())
        except ValueError:
            await message.answer("❌ لطفاً یک عدد معتبر برای درصد وارد کنید.", reply_markup=get_back_menu())

    elif isinstance(current_state, dict) and current_state.get("state") == "gold_waiting_tax":
        try:
            tax_pct = float(text)
            if tax_pct < 0: raise ValueError
            
            price = current_state["price"]
            weight = current_state["weight"]
            wage_pct = current_state["wage_pct"]
            profit_pct = current_state["profit_pct"]
            
            raw_price = weight * price
            wage = raw_price * (wage_pct / 100)
            profit = raw_price * (profit_pct / 100)
            tax = (raw_price + wage + profit) * (tax_pct / 100)
            total_price = raw_price + wage + profit + tax

            msg = f"🧮 **نتیجه محاسبه طلا**\n\n"
            msg += f"💰 قیمت هر گرم: `{price:,.0f}` تومان\n"
            msg += f"⚖️ وزن: `{weight}` گرم\n"
            msg += f"📦 قیمت خام: `{raw_price:,.0f}` تومان\n"
            msg += f"🛠 اجرت ساخت ({wage_pct}٪): `{wage:,.0f}` تومان\n"
            msg += f"🏪 سود فروشنده ({profit_pct}٪): `{profit:,.0f}` تومان\n"
            msg += f"📄 مالیات ({tax_pct}٪): `{tax:,.0f}` تومان\n\n"
            msg += f"✅ **مبلغ نهایی خرید: `{total_price:,.0f}` تومان**"
            
            await message.answer(msg, reply_markup=get_main_menu())
            user_states.pop(user_id, None) 
        except ValueError:
            await message.answer("❌ لطفاً یک عدد معتبر برای درصد وارد کنید.", reply_markup=get_back_menu())

    # ------------------ ماشین‌حساب اقساط (وام) ------------------
    elif current_state == "loan_waiting_amount":
        try:
            amount = float(text.replace(',', ''))
            if amount <= 0: raise ValueError
            user_states[user_id] = {"state": "loan_waiting_interest", "amount": amount}
            await message.answer("📈 نرخ سود سالانه را وارد کنید (اگر بدون سود است، عدد 0 را بفرستید):", reply_markup=get_back_menu())
        except ValueError:
            await message.answer("❌ لطفاً یک مبلغ معتبر وارد کنید.", reply_markup=get_back_menu())

    elif isinstance(current_state, dict) and current_state.get("state") == "loan_waiting_interest":
        try:
            interest = float(text)
            if interest < 0: raise ValueError
            current_state["interest"] = interest
            current_state["state"] = "loan_waiting_months"
            await message.answer("⏳ تعداد ماه‌های بازپرداخت را وارد کنید (مثلاً 12):", reply_markup=get_back_menu())
        except ValueError:
            await message.answer("❌ لطفاً یک عدد معتبر برای نرخ سود وارد کنید.", reply_markup=get_back_menu())

    elif isinstance(current_state, dict) and current_state.get("state") == "loan_waiting_months":
        try:
            months = int(text)
            if months <= 0: raise ValueError
            
            amount = current_state["amount"]
            annual_interest = current_state["interest"]
            
            if annual_interest == 0:
                monthly_payment = amount / months
                total_repayment = amount
                total_interest = 0
            else:
                monthly_rate = (annual_interest / 100) / 12
                monthly_payment = amount * (monthly_rate * (1 + monthly_rate)**months) / ((1 + monthly_rate)**months - 1)
                total_repayment = monthly_payment * months
                total_interest = total_repayment - amount

            msg = f"💸 **نتیجه محاسبه وام**\n\n"
            msg += f"💵 مبلغ وام: `{amount:,.0f}` تومان\n"
            msg += f"📈 نرخ سود سالانه: `{annual_interest}٪`\n"
            msg += f"⏳ مدت بازپرداخت: `{months}` ماه\n\n"
            msg += f"💳 **قسط ماهانه: `{monthly_payment:,.0f}` تومان**\n"
            msg += f"💰 مجموع بازپرداخت: `{total_repayment:,.0f}` تومان\n"
            msg += f"📉 مجموع سود پرداختی: `{total_interest:,.0f}` تومان"

            await message.answer(msg, reply_markup=get_main_menu())
            user_states.pop(user_id, None)
        except ValueError:
            await message.answer("❌ لطفاً یک عدد صحیح برای تعداد ماه‌ها وارد کنید.", reply_markup=get_back_menu())

    # ------------------ آب‌وهوا ------------------
    elif current_state == "waiting_for_city":
        await message.answer(f"⏳ در حال دریافت آب‌وهوای {text}...")
        weather_info = await asyncio.to_thread(get_weather, text)
        await message.answer(weather_info, reply_markup=get_main_menu())
        user_states.pop(user_id, None)
        
    # ------------------ منوی اصلی ------------------
    elif text == "🚀 استارت / منوی اصلی" or text == "/start":
        await command_start_handler(message)
        
    elif text == "💱 قیمت ارزها":
        await message.answer(gen_fiat_msg())
        
    elif text == "🌕 قیمت طلا و سکه":
        await message.answer(gen_gold_msg())
        
    elif text == "🪙 قیمت ارز دیجیتال":
        await message.answer(gen_crypto_msg())
        
    elif text == "💵 مقایسه قیمت تتر":
        await message.answer(gen_usdt_msg())

    elif text == "🔍 پلاک‌خوان":
        await message.answer("🔍 **پلاک‌خوان**\n\nلطفاً **دو رقم سمت راست** پلاک را وارد کنید (مثلاً: 33):", reply_markup=get_back_menu())
        user_states[user_id] = "waiting_for_plate_code"

    elif text == "🧮 ماشین‌حساب طلا":
        g18 = f"{PRICE_CACHE['gold_18_num']:,}" if PRICE_CACHE['gold_18_num'] > 0 else "در دسترس نیست"
        g24 = f"{PRICE_CACHE['gold_24_num']:,}" if PRICE_CACHE['gold_24_num'] > 0 else "در دسترس نیست"
        
        msg = f"🧮 **ماشین‌حساب طلا**\n\n"
        msg += f"💡 برای اطلاع شما، قیمت روز:\n"
        msg += f"🥇 ۱۸ عیار: `{g18}` تومان\n"
        msg += f"🥇 ۲۴ عیار: `{g24}` تومان\n\n"
        msg += f"👇 حالا **قیمت هر گرم** طلایی که می‌خواهید بخرید را به تومان وارد کنید (مثلاً: 25300000):"
        
        await message.answer(msg, reply_markup=get_back_menu())
        user_states[user_id] = "gold_waiting_price"
        
    elif text == "💸 ماشین‌حساب اقساط":
        await message.answer("💸 لطفاً **مبلغ کل وام** را به تومان وارد کنید:", reply_markup=get_back_menu())
        user_states[user_id] = "loan_waiting_amount"

    elif text == "🌤 وضعیت آب‌وهوا":
        await message.answer("🌤 لطفاً نام شهر خود را وارد کنید (مثلاً: تهران):", reply_markup=get_back_menu())
        user_states[user_id] = "waiting_for_city"

    elif text == "🤖 چت با هوش مصنوعی":
        await message.answer("🤖 لطفاً سوال خود را بپرسید تا هوش مصنوعی پاسخ دهد:")

    elif text in ["💱 مبدل هوشمند ارز", "🚨 آلارم قیمتی"]:
        await message.answer(f"⏳ بخش {text} در حال توسعه است و به زودی به ربات اضافه می‌شود.")

    else:
        await message.answer("⏳ در حال پردازش...")
        res = await asyncio.to_thread(get_ai_response, text)
        await message.answer(res)

async def main() -> None:
    bot = Bot(token=BALE_TOKEN, default=DefaultBotProperties())
    
    asyncio.create_task(update_cache_task())
    asyncio.create_task(update_plate_data_task())
    
    await dp.start_polling(bot)

if __name__ == "__main__":
    threading.Thread(target=run_dummy_server, daemon=True).start()
    print("Starting Bale Bot...")
    asyncio.run(main())
