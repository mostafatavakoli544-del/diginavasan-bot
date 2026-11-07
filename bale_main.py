import os
import time
import logging
import requests
import re
import json
import math
import tempfile
import threading
from urllib.parse import quote
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import pytz
import asyncio
import jdatetime
from datetime import datetime
from io import BytesIO

from PIL import Image, ImageDraw, ImageFont, features
import arabic_reshaper
try:
    from bidi.algorithm import get_display
except ImportError:
    from bidi import get_display

from baleio import Bot, Dispatcher, md
from baleio.client.default import DefaultBotProperties
from baleio.enums import ChatType, ParseMode
from baleio.filters import CommandStart
from baleio.types import Message, ReplyKeyboardMarkup, KeyboardButton

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

BALE_TOKEN = os.environ.get("BALE_TOKEN")
PORT = int(os.environ.get("PORT", 10000))
CHANNEL_ID = "@diginavasan"
ADMIN_ID = int(os.environ.get("ADMIN_ID", "718840144"))
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
BRS_API_KEY = os.environ.get("BRS_API_KEY")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEHRAN = pytz.timezone('Asia/Tehran')
FONT_PATHS = [os.path.join(BASE_DIR, "Vazirmatn-Bold.ttf"), os.path.join(BASE_DIR, "fonts", "Vazirmatn-Bold.ttf"), os.path.join(BASE_DIR, "font.ttf")]

CHANNEL_FOOTER = "\n\n────────────\n🤖 [ورود به ربات دیجی نوسان](https://ble.ir/diginavasan_bot) | 📢 [کانال دیجی نوسان](https://ble.ir/diginavasan)"

PRICE_CACHE = {"fiat": "", "gold": "", "crypto": "", "usdt": "", "gold_18_num": 0, "gold_24_num": 0, "last_update": "در حال دریافت..."}
INFO_CACHE = {}
user_states = {}

class HealthHandler(BaseHTTPRequestHandler):
    def _respond(self, send_body=True):
        body = "OK - Digi Navasan bot is running".encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if send_body: self.wfile.write(body)
    def do_GET(self): self._respond(True)
    def do_HEAD(self): self._respond(False)
    def log_message(self, format, *args): pass

def run_dummy_server():
    server = ThreadingHTTPServer(("", PORT), HealthHandler)
    server.serve_forever()

def as_photo(img_bytes, filename="infographic.png"):
    return img_bytes

HAS_RAQM = features.check("raqm")
def fa_text(text):
    if not text: return ""
    if HAS_RAQM: return str(text)
    reshaped = arabic_reshaper.reshape(str(text))
    return get_display(reshaped)

def format_num(num):
    try: return f"{int(float(num)):,}"
    except Exception: return "0"

def format_usd(value):
    try: return f"{float(value):,.2f}"
    except (TypeError, ValueError): return "N/A"

def draw_rtl_text(draw, xy, text, font, fill, align="right"):
    bbox = draw.textbbox((0, 0), text, font=font)
    text_width = bbox[2] - bbox[0]
    if align == "right": draw.text((xy[0] - text_width, xy[1]), text, font=font, fill=fill)
    else: draw.text(xy, text, font=font, fill=fill)

def create_luxury_gradient_bg(width, height):
    base = Image.new('RGB', (width, height), (12, 20, 36))
    draw = ImageDraw.Draw(base)
    for y in range(height):
        r = int(12 + (22 - 12) * y / height)
        g = int(20 + (34 - 20) * y / height)
        b = int(36 + (58 - 36) * y / height)
        draw.line([(0, y), (width, y)], fill=(r, g, b))
    return base

def _font_supports_persian(path):
    try:
        if not os.path.exists(path) or os.path.getsize(path) < 20000: return False
        font = ImageFont.truetype(path, 40)
        missing = Image.new("L", (90, 90), 0).tobytes()
        img = Image.new("L", (90, 90), 0); ImageDraw.Draw(img).text((10, 10), "ی", font=font, fill=255)
        return img.tobytes() != missing
    except: return False

_FONT_PATH_CACHE = None
def resolve_font_path():
    global _FONT_PATH_CACHE
    if _FONT_PATH_CACHE and _font_supports_persian(_FONT_PATH_CACHE): return _FONT_PATH_CACHE
    for p in FONT_PATHS:
        if _font_supports_persian(p): _FONT_PATH_CACHE = p; return p
    return None

def load_fonts():
    path = resolve_font_path()
    if path:
        try: return tuple(ImageFont.truetype(path, size) for size in (55, 36, 30, 24))
        except: pass
    default = ImageFont.load_default()
    return default, default, default, default

def generate_infographic(data):
    width, height = 1080, 1920
    img = create_luxury_gradient_bg(width, height)
    draw = ImageDraw.Draw(img)
    font_title, font_header, font_body, font_small = load_fonts()
    gold_color, white, gray, card_fill, card_outline, line_color = (255, 215, 0), (255, 255, 255), (180, 190, 210), (20, 32, 54), (70, 95, 140), (45, 65, 100)

    draw.rounded_rectangle((50, 40, width - 50, 190), radius=24, fill=(24, 38, 64), outline=(218, 165, 32), width=3)
    draw_rtl_text(draw, (width - 80, 48), fa_text("دیجی نوسان | خلاصه بازار"), font_title, gold_color)
    now_str = datetime.now(pytz.timezone('Asia/Tehran')).strftime("%Y/%m/%d - %H:%M")
    draw_rtl_text(draw, (width - 80, 135), fa_text(now_str), font_small, gray)

    y1 = 220
    draw.rounded_rectangle((50, y1, width - 50, y1 + 520), radius=22, fill=card_fill, outline=card_outline, width=2)
    draw_rtl_text(draw, (width - 80, y1 + 25), fa_text("طلا و سکه"), font_header, gold_color)
    draw.line((80, y1 + 80, width - 80, y1 + 80), fill=line_color, width=2)
    
    gold_items = [("طلای ۱۸ عیار:", data.get('gold_18', 0)), ("طلای ۲۴ عیار:", data.get('gold_24', 0)), ("سکه امامی:", data.get('coin', 0)), ("سکه بهار آزادی:", data.get('bahar', 0)), ("نیم سکه:", data.get('half', 0)), ("ربع سکه:", data.get('quarter', 0))]
    y_offset = y1 + 105
    for label, val in gold_items:
        draw_rtl_text(draw, (width - 80, y_offset), fa_text(f"{label} {format_num(val)} تومان"), font_body, white)
        y_offset += 68

    y2 = 770
    draw.rounded_rectangle((50, y2, width - 50, y2 + 480), radius=22, fill=card_fill, outline=card_outline, width=2)
    draw_rtl_text(draw, (width - 80, y2 + 25), fa_text("ارزهای خارجی"), font_header, gold_color)
    draw.line((80, y2 + 80, width - 80, y2 + 80), fill=line_color, width=2)
    draw_rtl_text(draw, (width - 80, y2 + 105), fa_text(f"دلار: {format_num(data.get('dollar', 0))} تومان"), font_body, white)
    draw_rtl_text(draw, (width - 80, y2 + 175), fa_text(f"یورو: {format_num(data.get('euro', 0))} تومان"), font_body, white)
    draw_rtl_text(draw, (width - 80, y2 + 245), fa_text(f"تتر (بهترین): {format_num(data.get('usdt_best', 0))} تومان"), font_body, white)
    draw_rtl_text(draw, (width - 80, y2 + 315), fa_text(f"نفت برنت: {format_usd(data.get('oil_usd'))} دلار"), font_body, white)
    draw_rtl_text(draw, (width - 80, y2 + 385), fa_text(f"نقره: {format_usd(data.get('silver_usd'))} دلار"), font_body, white)

    y3 = 1280
    draw.rounded_rectangle((50, y3, width - 50, y3 + 500), radius=22, fill=card_fill, outline=card_outline, width=2)
    draw_rtl_text(draw, (width - 80, y3 + 25), fa_text("رمزارزهای برتر"), font_header, gold_color)
    draw.line((80, y3 + 80, width - 80, y3 + 80), fill=line_color, width=2)
    
    crypto_items = [("بیت‌کوین (BTC):", data.get('btc', 0)), ("اتریوم (ETH):", data.get('eth', 0)), ("بایننس (BNB):", data.get('bnb', 0)), ("ترون (TRX):", data.get('trx', 0)), ("سولانا (SOL):", data.get('sol', 0)), ("دوج (DOGE):", data.get('doge', 0)), ("تون (TON):", data.get('ton', 0))]
    y_offset = y3 + 105
    for label, val in crypto_items:
        draw_rtl_text(draw, (width - 80, y_offset), fa_text(f"{label} {format_num(val)} تومان"), font_small, white)
        y_offset += 50

    draw.line((80, height - 150, width - 80, height - 150), fill=card_outline, width=1)
    draw_rtl_text(draw, (width - 50, height - 110), fa_text("قیمت لحظه‌ای | ماشین‌حساب | تحلیل هوشمند"), font_small, (160, 175, 200))
    draw_rtl_text(draw, (width - 50, height - 70), "Digi Navasan Channel & Bot", font_small, gold_color)

    bio = BytesIO()
    img.save(bio, format="PNG")
    return bio.getvalue()
_NERKH_CACHE = {"time": 0, "rates": {}}
def get_nerkh_rates():
    now = time.time()
    if _NERKH_CACHE["rates"] and now - _NERKH_CACHE["time"] < 20: return _NERKH_CACHE["rates"]
    try:
        res = requests.get("https://nerkh.jahankhahan.shop/data/live.json", headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
        if res.status_code == 200:
            rates = res.json().get('rates', {})
            if rates:
                _NERKH_CACHE["rates"] = rates
                _NERKH_CACHE["time"] = now
                return rates
    except Exception: pass
    return _NERKH_CACHE["rates"]

def get_nerkh_price(key):
    try: return int(float(get_nerkh_rates().get(key, 0)))
    except Exception: return 0

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
        res = requests.get(f"https://apiv2.nobitex.ir/market/stats?srcCurrency={symbol}&dstCurrency=usdt", headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
        if res.status_code == 200 and f"{symbol}-usdt" in res.json().get('stats', {}):
            return float(res.json()['stats'][f"{symbol}-usdt"]['latest'])
    except Exception: pass
    return None

def get_multi_exchange_usdt_prices():
    results = {}
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
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
                if item.get('name', {}).get('en') == 'tether/rial':
                    v = (((item.get("financial") or {}).get("last24h") or {}).get("close")) or item.get('sell', 0)
                    if float(v) > 0:
                        results['رمزینکس'] = int(float(v) / 10)
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
    if not GROQ_API_KEY: return "⚠️ کلید هوش مصنوعی (GROQ_API_KEY) روی سرور تنظیم نشده است."
    try:
        url = "https://api.groq.com/openai/v1/chat/completions"
        headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
        data = {"model": "openai/gpt-oss-120b", "messages": [{"role": "system", "content": "You are a helpful assistant. Answer in Persian."}, {"role": "user", "content": prompt}], "temperature": 0.7, "max_tokens": 2048}
        response = requests.post(url, headers=headers, json=data, timeout=60)
        if response.status_code == 200: return (response.json()['choices'][0]['message'].get('content') or '').strip() or "پاسخی دریافت نشد."
        if response.status_code == 429: return "⏳ تعداد درخواست‌ها زیاد است."
        if response.status_code in (401, 403): return "⚠️ کلید هوش مصنوعی نامعتبر است."
        return "خطایی در ارتباط با هوش مصنوعی رخ داد."
    except Exception: return "خطای اتصال به هوش مصنوعی."

def _normalize_fa(text):
    t = str(text or "").replace("ي", "ی").replace("ك", "ک").replace("\u200c", " ").replace("ـ", "")
    return " ".join(t.split())

def _has_persian(text): return bool(re.search(r"[\u0600-\u06FF]", text))

_CITY_EN_RAW = {"تهران": "Tehran", "مشهد": "Mashhad", "اصفهان": "Isfahan", "کرج": "Karaj", "شیراز": "Shiraz", "تبریز": "Tabriz", "قم": "Qom", "اهواز": "Ahvaz"}
CITY_EN = {_normalize_fa(k): v for k, v in _CITY_EN_RAW.items()}

WMO_FA = {0: "آسمان صاف ☀️", 1: "عمدتاً صاف 🌤", 2: "نیمه‌ابری ⛅", 3: "ابری ☁️", 45: "مه 🌫", 48: "مه یخ‌زده 🌫", 51: "نم‌نم باران خفیف 🌦", 53: "نم‌نم باران 🌦", 55: "نم‌نم باران شدید 🌧", 61: "باران خفیف 🌧", 63: "باران 🌧", 65: "باران شدید 🌧", 71: "برف خفیف 🌨", 73: "برف 🌨", 75: "برف شدید ❄️", 80: "رگبار خفیف 🌦", 81: "رگبار 🌧", 82: "رگبار شدید ⛈", 95: "رعد و برق ⛈", 96: "رعد و برق و تگرگ ⛈", 99: "رعد و برق و تگرگ شدید ⛈"}

def get_weather(city):
    city_in = _normalize_fa(city)
    if not city_in: return "❌ نام شهر را وارد کنید."
    query = CITY_EN.get(city_in, city_in)
    try:
        geo = requests.get("https://geocoding-api.open-meteo.com/v1/search", params={"name": query, "count": 10, "language": "fa", "format": "json"}, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
        results = (geo.json() if geo.status_code == 200 else {}).get("results") or []
        if results:
            place = next((r for r in results if r.get("country_code") == "IR"), results[0])
            fc = requests.get("https://api.open-meteo.com/v1/forecast", params={"latitude": place["latitude"], "longitude": place["longitude"], "current": "temperature_2m,relative_humidity_2m,apparent_temperature,weather_code,wind_speed_10m", "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max,weather_code", "timezone": "auto", "forecast_days": 2}, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
            if fc.status_code == 200:
                data = fc.json(); cur = data.get("current") or {}; daily = data.get("daily") or {}
                if cur.get("temperature_2m") is not None:
                    tmax = (daily.get("temperature_2m_max") or [None])[0]; tmin = (daily.get("temperature_2m_min") or [None])[0]
                    rain_prob = (daily.get("precipitation_probability_max") or [0])[0]
                    tomorrow_code = (daily.get("weather_code") or [0])[1] if len(daily.get("weather_code") or []) > 1 else 0
                    tomorrow_tmax = (daily.get("temperature_2m_max") or [None])[1] if len(daily.get("temperature_2m_max") or []) > 1 else None
                    tomorrow_tmin = (daily.get("temperature_2m_min") or [None])[1] if len(daily.get("temperature_2m_min") or []) > 1 else None
                    name = place.get("name") or city_in
                    w_desc = WMO_FA.get(int(cur.get('weather_code', -1)), 'نامشخص')
                    rain_text = f" (احتمال بارش: {rain_prob}٪)" if rain_prob > 10 else ""
                    tomorrow_text = f"\n\n📅 **فردا:** {WMO_FA.get(int(tomorrow_code), 'نامشخص')}\n🔽 {tomorrow_tmin} | 🔼 {tomorrow_tmax} درجه" if tomorrow_tmax else ""
                    return f"🌤 **وضعیت آب‌وهوای {name}**\n\n🌡 دما: {cur['temperature_2m']:.0f} درجه (احساس {cur.get('apparent_temperature', 0):.0f})\n☁️ وضعیت: {w_desc}{rain_text}\n🔼 بیشینه: {tmax if tmax is not None else '؟'} | 🔽 کمینه: {tmin if tmin is not None else '؟'}\n💧 رطوبت: {cur.get('relative_humidity_2m', '؟')}٪\n💨 باد: {cur.get('wind_speed_10m', '؟')} km/h{tomorrow_text}\n⏰ `{datetime.now(TEHRAN).strftime('%H:%M')}`"
    except Exception: pass
    return "❌ شهر پیدا نشد یا سرویس در دسترس نیست."
def build_info_cache(gold_18_num, crypto, usdt_prices):
    def to_int(value):
        try: return int(str(value).replace(',', ''))
        except Exception: return 0
    return {
        "dollar": get_nerkh_price('dollar'), "euro": get_nerkh_price('euro'),
        "gold_18": gold_18_num, "gold_24": PRICE_CACHE.get("gold_24_num", 0), "coin": get_nerkh_price('coin'),
        "bahar": get_nerkh_price('bahar'), "half": get_nerkh_price('half'), "quarter": get_nerkh_price('quarter'),
        "btc": to_int(crypto.get('BTC', '0')), "eth": to_int(crypto.get('ETH', '0')), "bnb": to_int(crypto.get('BNB', '0')),
        "trx": to_int(crypto.get('TRX', '0')), "sol": to_int(crypto.get('SOL', '0')), "doge": to_int(crypto.get('DOGE', '0')), "ton": to_int(crypto.get('TON', '0')),
        "usdt_best": min(usdt_prices.values()) if usdt_prices else 0,
        "oil_usd": get_nobitex_token_price('usoon'), "silver_usd": get_nobitex_token_price('slvon')
    }

def build_fiat_msg(time_str):
    if get_nerkh_price_safe(['dollar', 'usd']) == "ناموجود": return None
    return f"💱 **قیمت لحظه‌ای ارزها**\n⏰ `{time_str}`\n\n💵 دلار = {format_price(get_nerkh_price_safe(['dollar', 'usd']))}\n💶 یورو = {format_price(get_nerkh_price_safe(['euro', 'eur']))}\n💷 پوند = {format_price(get_nerkh_price_safe(['pound', 'gbp']))}\n🇦🇪 درهم = {format_price(get_nerkh_price_safe(['aed']))}\n🇹🇷 لیر = {format_price(get_nerkh_price_safe(['lira', 'try']))}\n🇨🇳 یوان = {format_price(get_nerkh_price_safe(['yuan', 'cny']))}\n🇨🇦 کانادا = {format_price(get_nerkh_price_safe(['cad']))}\n🇦🇺 استرالیا = {format_price(get_nerkh_price_safe(['aud']))}\n🇨🇭 فرانک سوئیس = {format_price(get_nerkh_price_safe(['chf']))}\n🇸🇦 ریال عربستان = {format_price(get_nerkh_price_safe(['sar']))}\n🇶🇦 ریال قطر = {format_price(get_nerkh_price_safe(['qar']))}\n🇰🇼 دینار کویت = {format_price(get_nerkh_price_safe(['kwd']))}\n🇮🇶 دینار عراق = {format_price(get_nerkh_price_safe(['iqd']))}\n🇦🇫 افغانی = {format_price(get_nerkh_price_safe(['afn']))}\n🇷🇺 روبل = {format_price(get_nerkh_price_safe(['rub']))}"

def build_gold_msg(time_str, gold_18_str, gold_24_str):
    if gold_18_str == "ناموجود" and gold_24_str == "ناموجود": return None
    return f"🌕 **قیمت لحظه‌ای طلا و سکه**\n⏰ `{time_str}`\n\n🥇 طلا ۱۸ عیار = {format_price(gold_18_str)}\n🥇 طلا ۲۴ عیار = {format_price(gold_24_str)}\n🪙 سکه امامی = {format_price(get_nerkh_price_safe(['coin']))}\n🪙 سکه بهار = {format_price(get_nerkh_price_safe(['bahar']))}\n🪙 نیم سکه = {format_price(get_nerkh_price_safe(['half']))}\n🪙 ربع سکه = {format_price(get_nerkh_price_safe(['quarter']))}"

CRYPTO_NAMES = {"BTC": "بیت‌کوین", "ETH": "اتریوم", "BNB": "بایننس کوین", "TRX": "ترون", "SOL": "سولانا", "DOGE": "دوج کوین", "TON": "تون کوین"}
def build_crypto_msg(time_str, crypto):
    if not crypto: return None
    lines = [f"🪙 **قیمت لحظه‌ای ارز دیجیتال**\n⏰ `{time_str}`\n"]
    for sym, prc in crypto.items():
        emoji = "🟢" if sym in ["BTC", "ETH"] else "🔹"
        lines.append(f"{emoji} **{CRYPTO_NAMES.get(sym, sym)} ({sym})**: `{prc}` تومان")
    return "\n".join(lines)

def build_usdt_msg(time_str, usdt_prices):
    if not usdt_prices: return None
    lines = []
    sorted_prices = sorted(usdt_prices.items(), key=lambda item: item[1])
    for i, (exchange, price) in enumerate(sorted_prices):
        if i == 0: lines.append(f"🥇 **{exchange}**: `{price:,}` تومان (ارزان‌ترین)")
        else: lines.append(f"🔸 {exchange}: `{price:,}` تومان")
    if len(sorted_prices) > 1:
        diff = sorted_prices[-1][1] - sorted_prices[0][1]
        lines.append(f"\n📊 اختلاف: `{diff:,}` تومان")
    return f"💵 **مقایسه قیمت تتر (USDT)**\n⏰ `{time_str}`\n\n" + "\n".join(lines)

async def update_cache_task():
    global INFO_CACHE
    while True:
        time_str = datetime.now(TEHRAN).strftime('%H:%M')
        crypto, usdt_prices, gold_18_str = {}, {}, "ناموجود"
        try:
            msg = await asyncio.to_thread(build_fiat_msg, time_str)
            if msg: PRICE_CACHE["fiat"] = msg
        except Exception: pass
        try:
            gold_18_str = await asyncio.to_thread(get_nerkh_price_safe, ['gold'])
            gold_24_str = await asyncio.to_thread(get_nerkh_price_safe, ['gold24'])
            PRICE_CACHE["gold_18_num"] = int(gold_18_str.replace(',', '')) if gold_18_str != "ناموجود" else 0
            PRICE_CACHE["gold_24_num"] = int(gold_24_str.replace(',', '')) if gold_24_str != "ناموجود" else 0
            msg = await asyncio.to_thread(build_gold_msg, time_str, gold_18_str, gold_24_str)
            if msg: PRICE_CACHE["gold"] = msg
        except Exception: pass
        try:
            crypto = await asyncio.to_thread(get_crypto_prices_toman)
            msg = build_crypto_msg(time_str, crypto)
            if msg: PRICE_CACHE["crypto"] = msg
        except Exception: pass
        try:
            usdt_prices = await asyncio.to_thread(get_multi_exchange_usdt_prices)
            msg = build_usdt_msg(time_str, usdt_prices)
            if msg: PRICE_CACHE["usdt"] = msg
        except Exception: pass
        try:
            INFO_CACHE = await asyncio.to_thread(build_info_cache, PRICE_CACHE["gold_18_num"], crypto or {}, usdt_prices or {})
            PRICE_CACHE["last_update"] = time_str
        except Exception: pass
        await asyncio.sleep(30)

MORNING_MSG = "🌅 **بسم الله الرحمن الرحیم**\n\nامیدوارم روز خوب و پر از موفقیت رو آغاز کنید. ☀️\nفرصت‌ها منتظر نمی‌مانند؛ آغاز نرخ‌دهی بازار! 🚀💰"
NIGHT_MSG = "🌙 **شب بخیر**\n\nامیدوارم روز پرباری را سپری کرده باشید. شبی آرام و فردایی پر از موفقیت پیش رو داشته باشید. 🌌"

async def fetch_bourse_msg():
    if not BRS_API_KEY: return None
    try:
        url = f"https://Api.Brsapi.ir/Tsetmc/Index.php?key={BRS_API_KEY.strip()}&type=1"
        res = await asyncio.to_thread(requests.get, url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=30)
        if res.status_code == 200:
            data = res.json()
            return f"📈 **وضعیت شاخص‌های بورس تهران**\n\n🔹 **شاخص کل:** `{data.get('index', 0):,.0f}`\n⚖️ **شاخص هم‌وزن:** `{data.get('index_equalWeight', 0):,.0f}`\n🔴 وضعیت بازار: `{data.get('state', 'نامشخص')}`\n"
    except Exception: pass
    return None

async def fetch_kala_msg():
    if not BRS_API_KEY: return None
    today = jdatetime.date.today()
    for i in range(7):
        check_date = today - jdatetime.timedelta(days=i)
        date_str = check_date.strftime("%Y-%m-%d")
        url = f"https://Api.BrsApi.ir/IME/Physical.php?key={BRS_API_KEY.strip()}&date_start={date_str}&date_end={date_str}"
        try:
            res = await asyncio.to_thread(requests.get, url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=30)
            if res.status_code == 200:
                data = res.json()
                if isinstance(data, list) and len(data) > 0:
                    valid_items = [item for item in data if item.get('pc') is not None and item.get('l30')]
                    valid_items.sort(key=lambda x: x.get('tval', 0), reverse=True)
                    msg = f"📦 **مهم‌ترین معاملات بورس کالا**\n🗓 تاریخ: `{check_date.strftime('%Y/%m/%d')}`\n\n"
                    for item in valid_items[:10]:
                        msg += f"🔹 **{item.get('l30', 'نامشخص')}**\n🏭 {item.get('producer', 'نامشخص')}\n💰 `{item.get('pc', 0) / 10:,.0f}` تومان | 📦 `{item.get('volume_contract') or 0:,}` تن\n\n"
                    return msg + "_(بر اساس ۱۰ کالای برتر)_"
        except Exception: pass
    return None

SENT_FILE = os.path.join(tempfile.gettempdir(), "digi_sent_slots.json")
def load_sent_slots():
    try:
        with open(SENT_FILE, encoding="utf-8") as f: return set(json.load(f))
    except Exception: return set()

def save_sent_slots(sent):
    try:
        today = datetime.now(TEHRAN).strftime("%Y-%m-%d")
        sent.intersection_update({k for k in sent if k.startswith(today)})
        with open(SENT_FILE, "w", encoding="utf-8") as f: json.dump(sorted(sent), f)
    except Exception: pass

def get_schedule_slot(now):
    hour, minute = now.hour, now.minute
    day = now.strftime("%Y-%m-%d")
    hm = f"{hour:02d}:{minute:02d}"
    if hour == 8 and minute < 10: return (f"{day}-morning", "morning", False)
    if hour == 20 and minute < 10: return (f"{day}-night", "night", False)
    if not (8 <= hour < 20): return None
    if hm == "12:45": return (f"{day}-{hm}", "bourse", False)
    if hm == "13:00": return (f"{day}-{hm}", "kala", False)
    if minute == 15: return (f"{day}-{hm}", "fiat", False)
    if minute == 30: return (f"{day}-{hm}", "gold", False)
    if minute == 45: return (f"{day}-{hm}", "usdt", False)
    if minute == 0 and hour != 8: return (f"{day}-{hm}", "crypto", True)
    return None

async def build_slot_message(kind):
    if kind == "morning": return MORNING_MSG
    if kind == "night": return NIGHT_MSG
    if kind == "bourse": body = await fetch_bourse_msg()
    elif kind == "kala": body = await fetch_kala_msg()
    else: body = PRICE_CACHE.get(kind)
    return (body + CHANNEL_FOOTER) if body else None

async def channel_scheduler(bot: Bot):
    sent = load_sent_slots()
    while True:
        try:
            now = datetime.now(TEHRAN)
            slot = get_schedule_slot(now)
            if slot and slot[0] not in sent:
                key, kind, with_info = slot
                text = await build_slot_message(kind)
                if text:
                    await bot.send_message(chat_id=CHANNEL_ID, text=text)
                sent.add(key)
                save_sent_slots(sent)
                if text and with_info:
                    await asyncio.sleep(3)
                    try:
                        img_bytes = await asyncio.to_thread(generate_infographic, INFO_CACHE)
                        await bot.send_photo(chat_id=CHANNEL_ID, photo=as_photo(img_bytes), caption="📊 خلاصه وضعیت بازار")
                    except Exception: pass
        except Exception: pass
        await asyncio.sleep(15)

BTN_START = "🚀 استارت / منوی اصلی"
BTN_BACK = "🔙 بازگشت به منوی اصلی"
BTN_FIAT = "💱 قیمت ارزها"
BTN_GOLD = "🌕 قیمت طلا و سکه"
BTN_CRYPTO = "🪙 قیمت ارز دیجیتال"
BTN_USDT = "💵 مقایسه قیمت تتر"
BTN_GOLD_CALC = "🧮 ماشین‌حساب طلا"
BTN_LOAN_CALC = "💸 ماشین‌حساب اقساط"
BTN_WEATHER = "🌤 وضعیت آب‌وهوا"
BTN_AI = "🤖 چت با هوش مصنوعی"
MAIN_MENU_BUTTONS = {BTN_FIAT, BTN_GOLD, BTN_CRYPTO, BTN_USDT, BTN_GOLD_CALC, BTN_LOAN_CALC, BTN_WEATHER, BTN_AI}

def get_main_menu():
    keyboard = [[KeyboardButton(text=BTN_START)], [KeyboardButton(text=BTN_FIAT), KeyboardButton(text=BTN_GOLD)], [KeyboardButton(text=BTN_CRYPTO), KeyboardButton(text=BTN_USDT)], [KeyboardButton(text=BTN_GOLD_CALC), KeyboardButton(text=BTN_LOAN_CALC)], [KeyboardButton(text=BTN_WEATHER), KeyboardButton(text=BTN_AI)]]
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)

def get_back_menu(): return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text=BTN_BACK)]], resize_keyboard=True)

_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
def parse_number(text, lo=None, hi=None):
    t = str(text or "").translate(_DIGITS)
    for ch in (",", "٬", "،", " ", "\u200c", "_", "٪", "%"): t = t.replace(ch, "")
    t = t.replace("٫", ".")
    value = float(t)
    if not math.isfinite(value): raise ValueError("not finite")
    if lo is not None and value < lo: raise ValueError("too small")
    if hi is not None and value > hi: raise ValueError("too large")
    return value

def split_text(text, limit=3800):
    text = str(text or "")
    if len(text) <= limit: return [text]
    chunks, current = [], ""
    for line in text.split("\n"):
        while len(line) > limit:
            if current: chunks.append(current); current = ""
            chunks.append(line[:limit]); line = line[limit:]
        if len(current) + len(line) + 1 > limit: chunks.append(current); current = line
        else: current = f"{current}\n{line}" if current else line
    if current: chunks.append(current)
    return chunks

async def reply(message, text, **kwargs):
    chunks = split_text(text)
    for i, chunk in enumerate(chunks): await message.answer(chunk, **(kwargs if i == len(chunks) - 1 else {}))

def user_first_name(message):
    try: name = (message.from_user.first_name or "").strip()
    except Exception: name = ""
    return name or "دوست عزیز"

dp = Dispatcher()

@dp.message(CommandStart())
async def command_start_handler(message: Message) -> None:
    if message.chat.type != ChatType.PRIVATE: return
    user_states.pop(message.from_user.id, None)
    await reply(message, f"سلام {user_first_name(message)} عزیز! به ربات دیجی نوسان خوش آمدید. ✅\n\nاز منوی زیر یکی از گزینه‌ها را انتخاب کنید:", reply_markup=get_main_menu())

STATE_ERRORS = {"gold_waiting_price": "❌ لطفاً یک عدد معتبر برای قیمت وارد کنید.", "gold_waiting_weight": "❌ لطفاً یک عدد معتبر برای وزن وارد کنید.", "gold_waiting_wage": "❌ لطفاً یک عدد معتبر برای درصد وارد کنید.", "gold_waiting_profit": "❌ لطفاً یک عدد معتبر برای درصد وارد کنید.", "gold_waiting_tax": "❌ لطفاً یک عدد معتبر وارد کنید.", "loan_waiting_amount": "❌ لطفاً یک مبلغ معتبر وارد کنید.", "loan_waiting_interest": "❌ لطفاً یک عدد معتبر (۰ تا ۲۰۰) وارد کنید.", "loan_waiting_months": "❌ لطفاً تعداد ماه را به‌صورت عدد صحیح (۱ تا ۶۰۰) وارد کنید."}

async def handle_menu_button(message, text, user_id):
    if text == BTN_FIAT: await reply(message, PRICE_CACHE.get("fiat") or "⏳ اطلاعات در حال دریافت است...")
    elif text == BTN_GOLD: await reply(message, PRICE_CACHE.get("gold") or "⏳ اطلاعات در حال دریافت است...")
    elif text == BTN_CRYPTO: await reply(message, PRICE_CACHE.get("crypto") or "⏳ اطلاعات در حال دریافت است...")
    elif text == BTN_USDT: await reply(message, PRICE_CACHE.get("usdt") or "⏳ اطلاعات در حال دریافت است...")
    elif text == BTN_GOLD_CALC:
        g18 = f"{PRICE_CACHE['gold_18_num']:,}" if PRICE_CACHE['gold_18_num'] > 0 else "ناموجود"
        g24 = f"{PRICE_CACHE['gold_24_num']:,}" if PRICE_CACHE['gold_24_num'] > 0 else "ناموجود"
        user_states[user_id] = {"state": "gold_waiting_price"}
        await reply(message, f"🧮 **ماشین‌حساب طلا**\n\n💡 قیمت روز:\n🥇 ۱۸ عیار: `{g18}`\n🥇 ۲۴ عیار: `{g24}`\n\n👇 **قیمت هر گرم** مد نظر خود را وارد کنید:", reply_markup=get_back_menu())
    elif text == BTN_LOAN_CALC:
        user_states[user_id] = {"state": "loan_waiting_amount"}
        await reply(message, "💸 **مبلغ کل وام** را وارد کنید:", reply_markup=get_back_menu())
    elif text == BTN_WEATHER:
        user_states[user_id] = {"state": "waiting_for_city"}
        await reply(message, "🌤 نام شهر خود را وارد کنید (مثلاً: تهران):", reply_markup=get_back_menu())
    elif text == BTN_AI:
        user_states[user_id] = {"state": "ai_chat_mode"}
        await reply(message, "🤖 حالت چت با هوش مصنوعی فعال شد. سوال خود را بپرسید:", reply_markup=get_back_menu())

async def handle_state(message, user_id, text, state):
    st = state.get("state")
    if st == "waiting_for_city":
        await reply(message, f"⏳ در حال دریافت آب‌وهوای {text}...")
        result = await asyncio.to_thread(get_weather, text)
        await reply(message, result, reply_markup=get_main_menu())
        user_states.pop(user_id, None); return
    if st == "ai_chat_mode":
        await reply(message, "⏳ در حال پردازش...")
        answer = await asyncio.to_thread(get_ai_response, text)
        await reply(message, answer, reply_markup=get_back_menu()); return
    try:
        if st == "gold_waiting_price":
            price = parse_number(text, lo=1, hi=1e13); user_states[user_id] = {"state": "gold_waiting_weight", "price": price}
            await reply(message, "⚖️ حالا وزن طلا را به **گرم** وارد کنید (مثلاً: 5):", reply_markup=get_back_menu())
        elif st == "gold_waiting_weight":
            state["weight"] = parse_number(text, lo=0.001, hi=1e6); state["state"] = "gold_waiting_wage"
            await reply(message, "🛠 درصد اجرت ساخت را وارد کنید (اگر ندارد، 0 بزنید):", reply_markup=get_back_menu())
        elif st == "gold_waiting_wage":
            state["wage_pct"] = parse_number(text, lo=0, hi=1000); state["state"] = "gold_waiting_profit"
            await reply(message, "🏪 درصد سود فروشنده را وارد کنید (اگر ندارد، 0 بزنید):", reply_markup=get_back_menu())
        elif st == "gold_waiting_profit":
            state["profit_pct"] = parse_number(text, lo=0, hi=1000); state["state"] = "gold_waiting_tax"
            await reply(message, "📄 درصد مالیات بر ارزش افزوده را وارد کنید (معمولاً 9):", reply_markup=get_back_menu())
        elif st == "gold_waiting_tax":
            tax_pct = parse_number(text, lo=0, hi=1000)
            price, weight = state["price"], state["weight"]
            wage = (weight * price) * (state["wage_pct"] / 100); profit = (weight * price) * (state["profit_pct"] / 100)
            tax = ((weight * price) + wage + profit) * (tax_pct / 100); total = (weight * price) + wage + profit + tax
            msg = f"🧮 **نتیجه محاسبه طلا**\n\n💰 قیمت هر گرم: `{price:,.0f}`\n⚖️ وزن: `{weight:g}` گرم\n📦 قیمت خام: `{weight * price:,.0f}`\n🛠 اجرت ({state['wage_pct']:g}٪): `{wage:,.0f}`\n🏪 سود ({state['profit_pct']:g}٪): `{profit:,.0f}`\n📄 مالیات ({tax_pct:g}٪): `{tax:,.0f}`\n\n✅ **مبلغ نهایی: `{total:,.0f}` تومان**"
            await reply(message, msg, reply_markup=get_main_menu()); user_states.pop(user_id, None)
        elif st == "loan_waiting_amount":
            user_states[user_id] = {"state": "loan_waiting_interest", "amount": parse_number(text, lo=1, hi=1e15)}
            await reply(message, "📈 نرخ سود سالانه را وارد کنید (بدون سود=0):", reply_markup=get_back_menu())
        elif st == "loan_waiting_interest":
            state["interest"] = parse_number(text, lo=0, hi=200); state["state"] = "loan_waiting_months"
            await reply(message, "⏳ تعداد ماه‌های بازپرداخت:", reply_markup=get_back_menu())
        elif st == "loan_waiting_months":
            months_f = parse_number(text, lo=1, hi=600)
            if months_f != int(months_f): raise ValueError("integer only")
            months = int(months_f); amount, interest = state["amount"], state["interest"]
            if interest < 0.0001: monthly = amount / months; total = amount; t_int = 0
            else:
                r = (interest / 100) / 12; monthly = amount * (r * (1 + r) ** months) / ((1 + r) ** months - 1)
                total = monthly * months; t_int = total - amount
            msg = f"💸 **نتیجه محاسبه وام**\n\n💵 مبلغ: `{amount:,.0f}`\n📈 سود: `{interest:g}٪`\n⏳ مدت: `{months}` ماه\n\n💳 **قسط ماهانه: `{monthly:,.0f}` تومان**\n💰 مجموع: `{total:,.0f}`\n📉 سود کل: `{t_int:,.0f}`"
            await reply(message, msg, reply_markup=get_main_menu()); user_states.pop(user_id, None)
        else:
            user_states.pop(user_id, None); await reply(message, "به منوی اصلی بازگشتید.", reply_markup=get_main_menu())
    except ValueError:
        await reply(message, STATE_ERRORS.get(st, "❌ لطفاً یک عدد معتبر وارد کنید."), reply_markup=get_back_menu())

async def process_private_message(message: Message) -> None:
    text = message.text.strip() if message.text else ""
    user_id = message.from_user.id
    if text in (BTN_BACK, BTN_START, "/start"):
        user_states.pop(user_id, None); await reply(message, "به منوی اصلی بازگشتید.", reply_markup=get_main_menu()); return
    if text == "/test_info":
        if user_id != ADMIN_ID:
            await reply(message, f"⛔ این دستور فقط برای مدیر مجاز است.\n\n(آیدی عددی بله شما: {user_id})\nاین آیدی را در تنظیمات Render به عنوان ADMIN_ID وارد کنید.", reply_markup=get_main_menu()); return
        await reply(message, "⏳ در حال ساخت اینفوگرافیک تستی...")
        try:
            img_bytes = await asyncio.to_thread(generate_infographic, INFO_CACHE)
            await message.answer_photo(photo=as_photo(img_bytes), caption="📊 تست اینفوگرافیک")
        except Exception as e: await reply(message, f"❌ خطا: {type(e).__name__}: {str(e)}")
        return
    if text in MAIN_MENU_BUTTONS:
        user_states.pop(user_id, None); await handle_menu_button(message, text, user_id); return
    state = user_states.get(user_id)
    if isinstance(state, dict) and state.get("state"):
        await handle_state(message, user_id, text, state); return
    await reply(message, "لطفاً یکی از گزینه‌های منو را انتخاب کنید یا برای چت با هوش مصنوعی، دکمه مربوطه را بزنید.", reply_markup=get_main_menu())

@dp.message()
async def handle_message(message: Message) -> None:
    if message.chat.type != ChatType.PRIVATE: return
    try: await process_private_message(message)
    except Exception:
        try: await message.answer("⚠️ خطایی رخ داد؛ لطفاً دوباره تلاش کنید.", reply_markup=get_main_menu())
        except Exception: pass

BACKGROUND_TASKS = []
async def main() -> None:
    if not BALE_TOKEN: raise SystemExit("BALE_TOKEN is not set")
    bot = Bot(token=BALE_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN))
    BACKGROUND_TASKS.extend([asyncio.create_task(update_cache_task()), asyncio.create_task(channel_scheduler(bot))])
    await dp.start_polling(bot)

if __name__ == "__main__":
    threading.Thread(target=run_dummy_server, daemon=True).start()
    print("Starting Bale Bot...")
    asyncio.run(main())
