import os
import logging
import requests
import threading
import http.server
import socketserver
import pytz
import asyncio
import jdatetime
import json
from datetime import datetime
from io import BytesIO
from PIL import Image, ImageDraw, ImageFont
import arabic_reshaper
from bidi.algorithm import get_display

from baleio import Bot, Dispatcher, md
from baleio.client.default import DefaultBotProperties
from baleio.enums import ChatType
from baleio.filters import CommandStart
from baleio.types import Message, ReplyKeyboardMarkup, KeyboardButton

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

# ==========================================
# تنظیمات اصلی
# ==========================================
BALE_TOKEN = os.environ.get("BALE_TOKEN")
PORT = int(os.environ.get("PORT", 10000))
CHANNEL_ID = "@diginavasan"
ADMIN_ID = 718840144
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
BRS_API_KEY = os.environ.get("BRS_API_KEY")

FONT_PATH = "fonts/Vazirmatn-Bold.ttf"
CHANNEL_FOOTER = "\n\n────────────\n🤖 [ورود به ربات دیجی نوسان](https://ble.ir/diginavasan_bot) | 📢 [کانال دیجی نوسان](https://ble.ir/diginavasan)"

# ==========================================
# کش سراسری
# ==========================================
PRICE_CACHE = {
    "fiat": "", "gold": "", "crypto": "", "usdt": "",
    "gold_18_num": 0, "gold_24_num": 0, "last_update": "در حال دریافت..."
}
INFO_CACHE = {}
user_states = {}

# ==========================================
# سرور تستی برای زنده نگه داشتن رندر
# ==========================================
def run_dummy_server():
    Handler = http.server.SimpleHTTPRequestHandler
    with socketserver.TCPServer(("", PORT), Handler) as httpd: httpd.serve_forever()

# ==========================================
# توابع کمکی اینفوگرافیک
# ==========================================
def fa_text(text):
    if not text: return ""
    reshaped = arabic_reshaper.reshape(str(text))
    return get_display(reshaped)

def format_num(num):
    try: return f"{int(float(num)):,}"
    except: return "0"

def draw_rtl_text(draw, xy, text, font, fill, align="right"):
    bbox = draw.textbbox((0, 0), text, font=font)
    text_width = bbox[2] - bbox[0]
    if align == "right":
        draw.text((xy[0] - text_width, xy[1]), text, font=font, fill=fill)
    else:
        draw.text(xy, text, font=font, fill=fill)

def generate_infographic(data):
    width, height = 1000, 1500
    bg_color = (15, 25, 45)
    card_bg = (28, 45, 75)
    text_white = (255, 255, 255)
    text_gold = (255, 215, 0)
    
    img = Image.new('RGB', (width, height), bg_color)
    draw = ImageDraw.Draw(img)
    
    try:
        font_large = ImageFont.truetype(FONT_PATH, 50)
        font_medium = ImageFont.truetype(FONT_PATH, 35)
        font_small = ImageFont.truetype(FONT_PATH, 28)
        font_tiny = ImageFont.truetype(FONT_PATH, 22)
    except:
        logging.error("Font not found!")
        font_large = font_medium = font_small = font_tiny = ImageFont.load_default()

    draw_rtl_text(draw, (width - 50, 50), fa_text("خلاصه وضعیت بازار"), font_large, text_white)
    now_str = datetime.now(pytz.timezone('Asia/Tehran')).strftime("%Y/%m/%d - %H:%M")
    draw_rtl_text(draw, (width - 50, 110), fa_text(now_str), font_small, (150, 150, 150))
    draw.line((50, 160, width-50, 160), fill=(100, 100, 100), width=2)

    y = 200
    draw.rounded_rectangle((50, y, width-50, y+220), radius=20, fill=card_bg)
    draw_rtl_text(draw, (width-80, y+20), fa_text("طلا و سکه"), font_medium, text_gold)
    draw_rtl_text(draw, (width-80, y+80), fa_text(f"طلای ۱۸ عیار: {format_num(data.get('gold_18', 0))} تومان"), font_small, text_white)
    draw_rtl_text(draw, (width-80, y+130), fa_text(f"سکه امامی: {format_num(data.get('coin', 0))} تومان"), font_small, text_white)

    y = 450
    draw.rounded_rectangle((50, y, width-50, y+220), radius=20, fill=card_bg)
    draw_rtl_text(draw, (width-80, y+20), fa_text("ارزهای خارجی"), font_medium, text_gold)
    draw_rtl_text(draw, (width-80, y+80), fa_text(f"دلار: {format_num(data.get('dollar', 0))} تومان"), font_small, text_white)
    draw_rtl_text(draw, (width-80, y+130), fa_text(f"یورو: {format_num(data.get('euro', 0))} تومان"), font_small, text_white)

    y = 700
    draw.rounded_rectangle((50, y, width-50, y+220), radius=20, fill=card_bg)
    draw_rtl_text(draw, (width-80, y+20), fa_text("رمزارزها"), font_medium, text_gold)
    draw_rtl_text(draw, (width-80, y+80), fa_text(f"بیت‌کوین: {format_num(data.get('btc', 0))} تومان"), font_small, text_white)
    draw_rtl_text(draw, (width-80, y+130), fa_text(f"اتریوم: {format_num(data.get('eth', 0))} تومان"), font_small, text_white)

    y = 950
    draw.rounded_rectangle((50, y, width-50, y+180), radius=20, fill=card_bg)
    draw_rtl_text(draw, (width-80, y+20), fa_text("تتر (USDT)"), font_medium, text_gold)
    draw_rtl_text(draw, (width-80, y+80), fa_text(f"بهترین قیمت: {format_num(data.get('usdt_best', 0))} تومان"), font_small, text_white)

    y = 1160
    draw.rounded_rectangle((50, y, width-50, y+180), radius=20, fill=card_bg)
    draw_rtl_text(draw, (width-80, y+20), fa_text("نفت و نقره (دلاری)"), font_medium, text_gold)
    draw_rtl_text(draw, (width-80, y+80), fa_text(f"نفت برنت: {data.get('oil_usd', 'N/A')} دلار"), font_small, text_white)
    draw_rtl_text(draw, (width-80, y+130), fa_text(f"نقره: {data.get('silver_usd', 'N/A')} دلار"), font_small, text_white)

    draw.line((50, height-150, width-50, height-150), fill=(100, 100, 100), width=1)
    draw_rtl_text(draw, (width-50, height-120), fa_text("امکانات: قیمت لحظه‌ای | ماشین‌حساب | هوش مصنوعی"), font_tiny, (150, 150, 150))
    draw_rtl_text(draw, (width-50, height-80), fa_text("Digi Navasan"), font_small, text_gold)

    bio = BytesIO()
    img.save(bio, format="PNG")
    bio.seek(0)
    return bio.getvalue()

# ==========================================
# دریافت اطلاعات قیمت‌ها
# ==========================================
def get_nerkh_price(key):
    try:
        res = requests.get("https://nerkh.jahankhahan.shop/data/live.json", timeout=15)
        if res.status_code == 200: return int(res.json()['rates'].get(key, 0))
    except: pass
    return 0

def get_nerkh_price_safe(keys_list):
    for key in keys_list:
        p = get_nerkh_price(key)
        if p > 0: return f"{p:,.0f}"
    return "ناموجود"

def format_price(price_str):
    if price_str == "ناموجود": return price_str
    return f"`{price_str}` تومان"

def get_nobitex_token_price(symbol):
    try:
        url = f"https://apiv2.nobitex.ir/market/stats?srcCurrency={symbol}&dstCurrency=usdt"
        res = requests.get(url, timeout=15)
        if res.status_code == 200 and f"{symbol}-usdt" in res.json().get('stats', {}):
            return float(res.json()['stats'][f"{symbol}-usdt"]['latest'])
    except: pass
    return None

def get_multi_exchange_usdt_prices():
    results = {}
    headers = {'User-Agent': 'Mozilla/5.0'}
    try:
        res = requests.get("https://apiv2.nobitex.ir/market/stats?srcCurrency=usdt&dstCurrency=rls", headers=headers, timeout=15)
        if res.status_code == 200 and 'usdt-rls' in res.json().get('stats', {}):
            results['نوبیتکس'] = int(float(res.json()['stats']['usdt-rls']['latest']) / 10)
    except: pass
    try:
        res = requests.get("https://api.wallex.ir/v1/markets", headers=headers, timeout=15)
        if res.status_code == 200 and 'USDTTMN' in res.json().get('result', {}).get('symbols', {}):
            results['والکس'] = int(float(res.json()['result']['symbols']['USDTTMN']['stats']['lastPrice']))
    except: pass
    try:
        res = requests.get("https://publicapi.ramzinex.com/exchange/api/v1.0/exchange/pairs", headers=headers, timeout=15)
        if res.status_code == 200:
            for item in res.json().get('data', []):
                if item.get('name', {}).get('en') == 'tether/rial' and float(item.get('sell', 0)) > 0:
                    results['رمزینکس'] = int(float(item['sell']) / 10)
                    break
    except: pass
    try:
        res = requests.get("https://api.bitpin.ir/api/v1/mkt/tickers/", headers=headers, timeout=15)
        if res.status_code == 200:
            for item in res.json():
                if item.get('symbol') == 'USDT_IRT':
                    results['بیت‌پین'] = int(float(item.get('price')))
                    break
    except: pass
    try:
        res = requests.get("https://api.tabdeal.org/api/v1/market/statistics", headers=headers, timeout=15)
        if res.status_code == 200 and 'USDTIRT' in res.json():
            results['تبدیل'] = int(float(res.json()['USDTIRT'].get('lastPrice', 0)))
    except: pass
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
    except: return {}

def get_ai_response(prompt):
    try:
        url = "https://api.groq.com/openai/v1/chat/completions"
        headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
        data = {"model": "openai/gpt-oss-120b", "messages": [{"role": "system", "content": "You are a helpful assistant. Answer in Persian."}, {"role": "user", "content": prompt}], "temperature": 0.7, "max_tokens": 1024}
        response = requests.post(url, headers=headers, json=data, timeout=30)
        if response.status_code == 200: return response.json()['choices'][0]['message']['content']
        else: return "خطایی در ارتباط با هوش مصنوعی رخ داد."
    except: return "خطای اتصال به هوش مصنوعی."

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
    except Exception: return "❌ خطا در دریافت اطلاعات آب‌وهوا."

# ==========================================
# ساخت پیام‌های کش ربات
# ==========================================
def gen_fiat_msg(): return PRICE_CACHE["fiat"]
def gen_gold_msg(): return PRICE_CACHE["gold"]
def gen_crypto_msg(): return PRICE_CACHE["crypto"]
def gen_usdt_msg(): return PRICE_CACHE["usdt"]

async def update_cache_task():
    global PRICE_CACHE, INFO_CACHE
    while True:
        try:
            now = datetime.now(pytz.timezone('Asia/Tehran'))
            time_str = now.strftime('%H:%M')
            
            PRICE_CACHE["fiat"] = await asyncio.to_thread(lambda: (
                f"💱 **قیمت لحظه‌ای ارزها**\n⏰ `{time_str}`\n\n"
                f"💵 دلار = {format_price(get_nerkh_price_safe(['dollar', 'usd']))}\n"
                f"💶 یورو = {format_price(get_nerkh_price_safe(['euro', 'eur']))}\n"
                f"💷 پوند = {format_price(get_nerkh_price_safe(['pound', 'gbp']))}\n"
                f"🇦🇪 درهم = {format_price(get_nerkh_price_safe(['aed']))}\n"
                f"🇹🇷 لیر = {format_price(get_nerkh_price_safe(['lira', 'try']))}\n"
                f"🇨🇳 یوان = {format_price(get_nerkh_price_safe(['yuan', 'cny']))}\n"
                f"🇨🇦 کانادا = {format_price(get_nerkh_price_safe(['cad']))}\n"
                f"🇦🇺 استرالیا = {format_price(get_nerkh_price_safe(['aud']))}\n"
                f"🇨🇭 فرانک سوئیس = {format_price(get_nerkh_price_safe(['chf']))}\n"
                f"🇸🇦 ریال عربستان = {format_price(get_nerkh_price_safe(['sar']))}\n"
                f"🇶🇦 ریال قطر = {format_price(get_nerkh_price_safe(['qar']))}\n"
                f"🇰🇼 دینار کویت = {format_price(get_nerkh_price_safe(['kwd']))}\n"
                f"🇮🇶 دینار عراق = {format_price(get_nerkh_price_safe(['iqd']))}\n"
                f"🇦🇫 افغانی = {format_price(get_nerkh_price_safe(['afn']))}\n"
                f"🇷🇺 روبل = {format_price(get_nerkh_price_safe(['rub']))}"
            ))

            gold_18_str = get_nerkh_price_safe(['gold'])
            gold_24_str = get_nerkh_price_safe(['gold24'])
            PRICE_CACHE["gold_18_num"] = int(gold_18_str.replace(',', '')) if gold_18_str != "ناموجود" else 0
            PRICE_CACHE["gold_24_num"] = int(gold_24_str.replace(',', '')) if gold_24_str != "ناموجود" else 0

            PRICE_CACHE["gold"] = await asyncio.to_thread(lambda: (
                f"🌕 **قیمت لحظه‌ای طلا و سکه**\n⏰ `{time_str}`\n\n"
                f"🥇 طلا ۱۸ عیار = {format_price(gold_18_str)}\n"
                f"🥇 طلا ۲۴ عیار = {format_price(gold_24_str)}\n"
                f"🪙 سکه امامی = {format_price(get_nerkh_price_safe(['coin']))}\n"
                f"🪙 سکه بهار = {format_price(get_nerkh_price_safe(['bahar']))}\n"
                f"🪙 نیم سکه = {format_price(get_nerkh_price_safe(['half']))}\n"
                f"🪙 ربع سکه = {format_price(get_nerkh_price_safe(['quarter']))}"
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

            # آپدیت INFO_CACHE برای اینفوگرافیک
            INFO_CACHE = {
                "dollar": get_nerkh_price('dollar'), "euro": get_nerkh_price('euro'),
                "gold_18": PRICE_CACHE["gold_18_num"], "coin": get_nerkh_price('coin'),
                "btc": int(crypto.get('BTC', '0').replace(',','')), "eth": int(crypto.get('ETH', '0').replace(',','')),
                "usdt_best": min(usdt_prices.values()) if usdt_prices else 0,
                "oil_usd": get_nobitex_token_price('usoon'), "silver_usd": get_nobitex_token_price('slvon')
            }

            PRICE_CACHE["last_update"] = time_str
        except Exception as e:
            logging.error(f"Cache update error: {e}")
        await asyncio.sleep(30)

# ==========================================
# زمان‌بند کانال
# ==========================================
async def fetch_bourse_msg():
    try:
        url = f"https://Api.Brsapi.ir/Tsetmc/Index.php?key={BRS_API_KEY.strip()}&type=1"
        res = await asyncio.to_thread(requests.get, url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=30)
        if res.status_code == 200:
            data = res.json()
            return (
                f"📈 **وضعیت شاخص‌های بورس تهران**\n\n"
                f"🔹 **شاخص کل:** `{data.get('index', 0):,.0f}`\n"
                f"⚖️ **شاخص هم‌وزن:** `{data.get('index_equalWeight', 0):,.0f}`\n"
                f"🔴 وضعیت بازار: `{data.get('state', 'نامشخص')}`\n"
            )
        return "❌ خطا در دریافت بورس."
    except Exception: return "❌ خطا در دریافت بورس."

async def fetch_kala_msg():
    today = jdatetime.date.today()
    headers = {'User-Agent': 'Mozilla/5.0'}
    for i in range(7):
        check_date = today - jdatetime.timedelta(days=i)
        date_str = check_date.strftime("%Y-%m-%d")
        url = f"https://Api.BrsApi.ir/IME/Physical.php?key={BRS_API_KEY.strip()}&date_start={date_str}&date_end={date_str}"
        res = await asyncio.to_thread(requests.get, url, headers=headers, timeout=30)
        if res.status_code == 200:
            try:
                data = res.json()
                if isinstance(data, list) and len(data) > 0:
                    valid_items = [item for item in data if item.get('pc') is not None and item.get('l30')]
                    valid_items.sort(key=lambda x: x.get('tval', 0), reverse=True)
                    top_items = valid_items[:10]
                    msg = f"📦 **مهم‌ترین معاملات بورس کالا**\n🗓 تاریخ: `{check_date.strftime('%Y/%m/%d')}`\n\n"
                    for item in top_items:
                        msg += f"🔹 **{item.get('l30', 'نامشخص')}**\n🏭 {item.get('producer', 'نامشخص')}\n💰 `{item.get('pc', 0) / 10:,.0f}` تومان | 📦 `{item.get('volume_contract') or 0:,}` تن\n\n"
                    return msg + "_(بر اساس ۱۰ کالای برتر)_"
            except: pass
        elif res.status_code == 402: return "⚠️ سهمیه API کالا تمام شده است."
        await asyncio.sleep(1)
    return "⚠️ در ۷ روز گذشته معامله‌ای ثبت نشده است."

async def channel_scheduler(bot: Bot):
    last_sent_minute = -1
    while True:
        try:
            now = datetime.now(pytz.timezone('Asia/Tehran'))
            hour, minute = now.hour, now.minute
            
            if minute != last_sent_minute and 8 <= hour < 20:
                current_time = f"{hour:02d}:{minute:02d}"
                msg_to_send = None
                send_infographic = False

                if current_time == "08:00":
                    msg_to_send = "🌅 **بسم الله الرحمن الرحیم**\n\nامیدوارم روز خوب و پر از موفقیت رو آغاز کنید. ☀️\nفرصت‌ها منتظر نمی‌مانند؛ آغاز نرخ‌دهی بازار! 🚀💰"
                elif current_time == "20:00":
                    msg_to_send = "🌙 **شب بخیر**\n\nامیدوارم روز پرباری را سپری کرده باشید. شبی آرام و فردایی پر از موفقیت پیش رو داشته باشید. 🌌"
                elif current_time == "12:45":
                    msg_to_send = await fetch_bourse_msg() + CHANNEL_FOOTER
                elif current_time == "13:00":
                    msg_to_send = await fetch_kala_msg() + CHANNEL_FOOTER
                elif minute == 15:
                    msg_to_send = gen_fiat_msg() + CHANNEL_FOOTER
                elif minute == 30:
                    msg_to_send = gen_gold_msg() + CHANNEL_FOOTER
                elif minute == 45:
                    msg_to_send = gen_usdt_msg() + CHANNEL_FOOTER
                elif minute == 0 and hour != 8 and hour != 20:
                    msg_to_send = gen_crypto_msg() + CHANNEL_FOOTER
                    send_infographic = True

                if msg_to_send:
                    await bot.send_message(chat_id=CHANNEL_ID, text=msg_to_send)
                    logging.info(f"Channel message sent at {current_time}")
                    last_sent_minute = minute

                if send_infographic:
                    await asyncio.sleep(3)
                    try:
                        img_bytes = generate_infographic(INFO_CACHE)
                        await bot.send_photo(chat_id=CHANNEL_ID, photo=img_bytes, caption="📊 خلاصه وضعیت بازار")
                    except Exception as e: logging.error(f"Infographic Send Error: {e}")

        except Exception as e:
            logging.error(f"Scheduler Error: {e}")
        
        await asyncio.sleep(15)

# ==========================================
# منو و ربات
# ==========================================
def get_main_menu():
    keyboard = [
        [KeyboardButton(text="🚀 استارت / منوی اصلی")],
        [KeyboardButton(text="💱 قیمت ارزها"), KeyboardButton(text="🌕 قیمت طلا و سکه")],
        [KeyboardButton(text="🪙 قیمت ارز دیجیتال"), KeyboardButton(text="💵 مقایسه قیمت تتر")],
        [KeyboardButton(text="🧮 ماشین‌حساب طلا"), KeyboardButton(text="💸 ماشین‌حساب اقساط")],
        [KeyboardButton(text="🌤 وضعیت آب‌وهوا"), KeyboardButton(text="🤖 چت با هوش مصنوعی")],
        [KeyboardButton(text="🚨 آلارم قیمتی")]
    ]
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)

def get_back_menu():
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="🔙 بازگشت به منوی اصلی")]], resize_keyboard=True)

dp = Dispatcher()

@dp.message(CommandStart())
async def command_start_handler(message: Message) -> None:
    if message.chat.type != ChatType.PRIVATE: return
    user_states.pop(message.from_user.id, None)
    await message.answer("سلام! به ربات دیجی نوسان در بله خوش آمدید. ✅\n\nاز منوی زیر یکی از گزینه‌ها را انتخاب کنید:", reply_markup=get_main_menu())

@dp.message()
async def handle_message(message: Message) -> None:
    if message.chat.type != ChatType.PRIVATE: return

    text = message.text.strip() if message.text else ""
    user_id = message.from_user.id
    current_state = user_states.get(user_id)

    if text == "🔙 بازگشت به منوی اصلی":
        user_states.pop(user_id, None)
        await message.answer("به منوی اصلی بازگشتید.", reply_markup=get_main_menu())
        return

    if text == "/test_info":
        await message.answer("⏳ در حال ساخت اینفوگرافیک تستی...")
        try:
            img_bytes = generate_infographic(INFO_CACHE)
            await message.answer_photo(photo=img_bytes, caption="📊 تست اینفوگرافیک")
        except Exception as e:
            await message.answer(f"❌ خطا: {str(e)}")
        return

    # ------------------ ماشین‌حساب طلا ------------------
    if current_state == "gold_waiting_price":
        try:
            gold_price = float(text.replace(',', ''))
            if gold_price <= 0: raise ValueError
            user_states[user_id] = {"state": "gold_waiting_weight", "price": gold_price}
            await message.answer("⚖️ حالا وزن طلا را به **گرم** وارد کنید (مثلاً: 5):", reply_markup=get_back_menu())
        except ValueError: await message.answer("❌ لطفاً یک عدد معتبر برای قیمت وارد کنید.", reply_markup=get_back_menu())
            
    elif isinstance(current_state, dict) and current_state.get("state") == "gold_waiting_weight":
        try:
            weight = float(text.replace(',', ''))
            if weight <= 0: raise ValueError
            current_state["weight"] = weight
            current_state["state"] = "gold_waiting_wage"
            await message.answer("🛠 درصد اجرت ساخت را وارد کنید (اگر ندارد، 0 بزنید):", reply_markup=get_back_menu())
        except ValueError: await message.answer("❌ لطفاً یک عدد معتبر برای وزن وارد کنید.", reply_markup=get_back_menu())

    elif isinstance(current_state, dict) and current_state.get("state") == "gold_waiting_wage":
        try:
            wage_pct = float(text)
            if wage_pct < 0: raise ValueError
            current_state["wage_pct"] = wage_pct
            current_state["state"] = "gold_waiting_profit"
            await message.answer("🏪 درصد سود فروشنده را وارد کنید (اگر ندارد، 0 بزنید):", reply_markup=get_back_menu())
        except ValueError: await message.answer("❌ لطفاً یک عدد معتبر برای درصد وارد کنید.", reply_markup=get_back_menu())

    elif isinstance(current_state, dict) and current_state.get("state") == "gold_waiting_profit":
        try:
            profit_pct = float(text)
            if profit_pct < 0: raise ValueError
            current_state["profit_pct"] = profit_pct
            current_state["state"] = "gold_waiting_tax"
            await message.answer("📄 درصد مالیات بر ارزش افزوده را وارد کنید (معمولاً 9):", reply_markup=get_back_menu())
        except ValueError: await message.answer("❌ لطفاً یک عدد معتبر برای درصد وارد کنید.", reply_markup=get_back_menu())

    elif isinstance(current_state, dict) and current_state.get("state") == "gold_waiting_tax":
        try:
            tax_pct = float(text)
            if tax_pct < 0: raise ValueError
            price = current_state["price"]
            weight = current_state["weight"]
            wage = (weight * price) * (current_state["wage_pct"] / 100)
            profit = (weight * price) * (current_state["profit_pct"] / 100)
            tax = ((weight * price) + wage + profit) * (tax_pct / 100)
            total = (weight * price) + wage + profit + tax
            
            msg = f"🧮 **نتیجه محاسبه طلا**\n\n💰 قیمت هر گرم: `{price:,.0f}`\n⚖️ وزن: `{weight}` گرم\n📦 قیمت خام: `{weight * price:,.0f}`\n🛠 اجرت ({current_state['wage_pct']}٪): `{wage:,.0f}`\n🏪 سود ({current_state['profit_pct']}٪): `{profit:,.0f}`\n📄 مالیات ({tax_pct}٪): `{tax:,.0f}`\n\n✅ **مبلغ نهایی: `{total:,.0f}` تومان**"
            await message.answer(msg, reply_markup=get_main_menu())
            user_states.pop(user_id, None) 
        except ValueError: await message.answer("❌ لطفاً یک عدد معتبر وارد کنید.", reply_markup=get_back_menu())

    # ------------------ ماشین‌حساب اقساط (وام) ------------------
    elif current_state == "loan_waiting_amount":
        try:
            amount = float(text.replace(',', ''))
            if amount <= 0: raise ValueError
            user_states[user_id] = {"state": "loan_waiting_interest", "amount": amount}
            await message.answer("📈 نرخ سود سالانه را وارد کنید (بدون سود=0):", reply_markup=get_back_menu())
        except ValueError: await message.answer("❌ لطفاً یک مبلغ معتبر وارد کنید.", reply_markup=get_back_menu())

    elif isinstance(current_state, dict) and current_state.get("state") == "loan_waiting_interest":
        try:
            interest = float(text)
            if interest < 0: raise ValueError
            current_state["interest"] = interest
            current_state["state"] = "loan_waiting_months"
            await message.answer("⏳ تعداد ماه‌های بازپرداخت:", reply_markup=get_back_menu())
        except ValueError: await message.answer("❌ لطفاً یک عدد معتبر وارد کنید.", reply_markup=get_back_menu())

    elif isinstance(current_state, dict) and current_state.get("state") == "loan_waiting_months":
        try:
            months = int(text)
            if months <= 0: raise ValueError
            amount = current_state["amount"]
            interest = current_state["interest"]
            
            if interest == 0:
                monthly = amount / months; total = amount; t_int = 0
            else:
                r = (interest / 100) / 12
                monthly = amount * (r * (1 + r)**months) / ((1 + r)**months - 1)
                total = monthly * months; t_int = total - amount

            msg = f"💸 **نتیجه محاسبه وام**\n\n💵 مبلغ: `{amount:,.0f}`\n📈 سود: `{interest}٪`\n⏳ مدت: `{months}` ماه\n\n💳 **قسط ماهانه: `{monthly:,.0f}` تومان**\n💰 مجموع: `{total:,.0f}`\n📉 سود کل: `{t_int:,.0f}`"
            await message.answer(msg, reply_markup=get_main_menu())
            user_states.pop(user_id, None)
        except ValueError: await message.answer("❌ لطفاً یک عدد صحیح وارد کنید.", reply_markup=get_back_menu())

    # ------------------ آب‌وهوا ------------------
    elif current_state == "waiting_for_city":
        await message.answer(f"⏳ در حال دریافت آب‌وهوای {text}...")
        await message.answer(await asyncio.to_thread(get_weather, text), reply_markup=get_main_menu())
        user_states.pop(user_id, None)
        
    # ------------------ منوی اصلی ------------------
    elif text == "🚀 استارت / منوی اصلی" or text == "/start":
        await command_start_handler(message)
    elif text == "💱 قیمت ارزها": await message.answer(gen_fiat_msg())
    elif text == "🌕 قیمت طلا و سکه": await message.answer(gen_gold_msg())
    elif text == "🪙 قیمت ارز دیجیتال": await message.answer(gen_crypto_msg())
    elif text == "💵 مقایسه قیمت تتر": await message.answer(gen_usdt_msg())
    elif text == "🧮 ماشین‌حساب طلا":
        g18 = f"{PRICE_CACHE['gold_18_num']:,}" if PRICE_CACHE['gold_18_num'] > 0 else "ناموجود"
        g24 = f"{PRICE_CACHE['gold_24_num']:,}" if PRICE_CACHE['gold_24_num'] > 0 else "ناموجود"
        msg = f"🧮 **ماشین‌حساب طلا**\n\n💡 قیمت روز:\n🥇 ۱۸ عیار: `{g18}`\n🥇 ۲۴ عیار: `{g24}`\n\n👇 **قیمت هر گرم** مد نظر خود را وارد کنید:"
        await message.answer(msg, reply_markup=get_back_menu())
        user_states[user_id] = "gold_waiting_price"
    elif text == "💸 ماشین‌حساب اقساط":
        await message.answer("💸 **مبلغ کل وام** را وارد کنید:", reply_markup=get_back_menu())
        user_states[user_id] = "loan_waiting_amount"
    elif text == "🌤 وضعیت آب‌وهوا":
        await message.answer("🌤 نام شهر خود را وارد کنید:", reply_markup=get_back_menu())
        user_states[user_id] = "waiting_for_city"
    elif text == "🤖 چت با هوش مصنوعی":
        await message.answer("🤖 سوال خود را بپرسید:")
    elif text == "🚨 آلارم قیمتی":
        await message.answer(f"⏳ بخش {text} در حال توسعه است.")
    else:
        await message.answer("⏳ در حال پردازش...")
        await message.answer(await asyncio.to_thread(get_ai_response, text))

async def main() -> None:
    bot = Bot(token=BALE_TOKEN, default=DefaultBotProperties())
    asyncio.create_task(update_cache_task())
    asyncio.create_task(channel_scheduler(bot))
    await dp.start_polling(bot)

if __name__ == "__main__":
    threading.Thread(target=run_dummy_server, daemon=True).start()
    print("Starting Bale Bot...")
    asyncio.run(main())
