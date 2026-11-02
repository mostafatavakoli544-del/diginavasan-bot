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
from baleio.enums import ChatType
from baleio.filters import CommandStart
from baleio.types import Message, ReplyKeyboardMarkup, KeyboardButton

try:
    from baleio.types import BufferedInputFile
except Exception:
    BufferedInputFile = None

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

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEHRAN = pytz.timezone('Asia/Tehran')
FONT_CANDIDATES = [
    os.path.join(BASE_DIR, "font.ttf"),
    os.path.join(BASE_DIR, "fonts", "Vazirmatn-Bold.ttf"),
]

CHANNEL_FOOTER = "\n\n────────────\n🤖 [ورود به ربات دیجی نوسان](https://ble.ir/diginavasan_bot) | 📢 [کانال دیجی نوسان](https://ble.ir/diginavasan)"

PRICE_CACHE = {
    "fiat": "", "gold": "", "crypto": "", "usdt": "",
    "gold_18_num": 0, "gold_24_num": 0, "last_update": "در حال دریافت..."
}
INFO_CACHE = {}
user_states = {}

# ==========================================
# سرور تستی
# ==========================================
class HealthHandler(BaseHTTPRequestHandler):
    def _respond(self, send_body=True):
        body = "OK - Digi Navasan bot is running".encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if send_body:
            self.wfile.write(body)

    def do_GET(self): self._respond(True)
    def do_HEAD(self): self._respond(False)
    def log_message(self, format, *args): pass

def run_dummy_server():
    server = ThreadingHTTPServer(("", PORT), HealthHandler)
    server.serve_forever()

async def keep_alive_task():
    url = os.environ.get("RENDER_EXTERNAL_URL") or os.environ.get("SELF_URL")
    if not url: return
    while True:
        await asyncio.sleep(240)
        try: await asyncio.to_thread(requests.get, url, timeout=20)
        except Exception: pass

# ==========================================
# توابع اینفوگرافیک
# ==========================================
def as_photo(img_bytes, filename="infographic.png"):
    if BufferedInputFile is not None:
        return BufferedInputFile(img_bytes, filename=filename)
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
    if align == "right":
        draw.text((xy[0] - text_width, xy[1]), text, font=font, fill=fill)
    else:
        draw.text(xy, text, font=font, fill=fill)

def create_luxury_gradient_bg(width, height):
    base = Image.new('RGB', (width, height), (12, 20, 36))
    draw = ImageDraw.Draw(base)
    for y in range(height):
        r = int(12 + (22 - 12) * y / height)
        g = int(20 + (34 - 20) * y / height)
        b = int(36 + (58 - 36) * y / height)
        draw.line([(0, y), (width, y)], fill=(r, g, b))
    return base

FONT_URLS = [
    "https://cdn.jsdelivr.net/gh/rastikerdar/vazirmatn@v33.003/fonts/ttf/Vazirmatn-Bold.ttf",
    "https://raw.githubusercontent.com/rastikerdar/vazirmatn/master/fonts/ttf/Vazirmatn-Bold.ttf"
]
FONT_SAVE_PATHS = [
    os.path.join(BASE_DIR, "fonts", "Vazirmatn-Bold.ttf"),
    os.path.join(tempfile.gettempdir(), "Vazirmatn-Bold.ttf"),
]
_FONT_STATE = {"path": None}
_FONT_LOCK = threading.Lock()

def _glyph_signature(font, ch):
    img = Image.new("L", (90, 90), 0)
    ImageDraw.Draw(img).text((10, 10), ch, font=font, fill=255)
    return img.tobytes()

def _font_supports_persian(path):
    try:
        if not os.path.exists(path) or os.path.getsize(path) < 20000: return False
        font = ImageFont.truetype(path, 40)
        try: missing = _glyph_signature(font, "\uffff")
        except Exception: missing = b"__none__"
        blank = Image.new("L", (90, 90), 0).tobytes()
        for ch in ("ی", "ک", "ش"):
            sig = _glyph_signature(font, ch)
            if sig == missing or sig == blank: return False
        return True
    except Exception: return False

def ensure_font():
    with _FONT_LOCK:
        cached = _FONT_STATE["path"]
        if cached and _font_supports_persian(cached): return cached
        for path in FONT_CANDIDATES + FONT_SAVE_PATHS:
            if _font_supports_persian(path):
                _FONT_STATE["path"] = path
                return path
        for url in FONT_URLS:
            try:
                res = requests.get(url, timeout=60)
                if res.status_code != 200 or len(res.content) < 20000: continue
                for dest in FONT_SAVE_PATHS:
                    try:
                        os.makedirs(os.path.dirname(dest), exist_ok=True)
                        with open(dest, "wb") as f: f.write(res.content)
                    except Exception: continue
                    if _font_supports_persian(dest):
                        _FONT_STATE["path"] = dest
                        return dest
            except Exception: pass
        return None

def load_fonts():
    path = ensure_font()
    if path:
        try: return tuple(ImageFont.truetype(path, size) for size in (55, 36, 30, 24))
        except Exception: pass
    default = ImageFont.load_default()
    return default, default, default, default

def generate_infographic(data):
    width, height = 1080, 1920
    img = create_luxury_gradient_bg(width, height)
    draw = ImageDraw.Draw(img)
    font_title, font_header, font_body, font_small = load_fonts()

    gold_color = (255, 215, 0)
    white = (255, 255, 255)
    gray = (180, 190, 210)
    card_fill = (20, 32, 54)
    card_outline = (70, 95, 140)
    line_color = (45, 65, 100)

    header_box = (50, 40, width - 50, 190)
    draw.rounded_rectangle(header_box, radius=24, fill=(24, 38, 64), outline=(218, 165, 32), width=3)
    draw_rtl_text(draw, (width - 80, 48), fa_text("دیجی نوسان | خلاصه بازار"), font_title, gold_color)
    now_str = datetime.now(pytz.timezone('Asia/Tehran')).strftime("%Y/%m/%d - %H:%M")
    draw_rtl_text(draw, (width - 80, 135), fa_text(now_str), font_small, gray)

    y1 = 220
    draw.rounded_rectangle((50, y1, width - 50, y1 + 380), radius=22, fill=card_fill, outline=card_outline, width=2)
    draw_rtl_text(draw, (width - 80, y1 + 25), fa_text("طلا و سکه"), font_header, gold_color)
    draw.line((80, y1 + 80, width - 80, y1 + 80), fill=line_color, width=2)
    draw_rtl_text(draw, (width - 80, y1 + 105), fa_text("طلای ۱۸ عیار:"), font_body, gray)
    gold_18_val = format_num(data.get('gold_18', 0))
    draw_rtl_text(draw, (width - 80, y1 + 155), fa_text(f"{gold_18_val} تومان"), font_body, white)
    draw_rtl_text(draw, (width - 80, y1 + 225), fa_text("سکه امامی:"), font_body, gray)
    coin_val = format_num(data.get('coin', 0))
    draw_rtl_text(draw, (width - 80, y1 + 275), fa_text(f"{coin_val} تومان"), font_body, white)

    y2 = 630
    draw.rounded_rectangle((50, y2, width - 50, y2 + 480), radius=22, fill=card_fill, outline=card_outline, width=2)
    draw_rtl_text(draw, (width - 80, y2 + 25), fa_text("ارزهای خارجی"), font_header, gold_color)
    draw.line((80, y2 + 80, width - 80, y2 + 80), fill=line_color, width=2)
    dollar_val = format_num(data.get('dollar', 0))
    draw_rtl_text(draw, (width - 80, y2 + 105), fa_text(f"دلار: {dollar_val} تومان"), font_body, white)
    euro_val = format_num(data.get('euro', 0))
    draw_rtl_text(draw, (width - 80, y2 + 175), fa_text(f"یورو: {euro_val} تومان"), font_body, white)
    usdt_val = format_num(data.get('usdt_best', 0))
    draw_rtl_text(draw, (width - 80, y2 + 245), fa_text(f"تتر (بهترین): {usdt_val} تومان"), font_body, white)
    oil_val = format_usd(data.get('oil_usd'))
    draw_rtl_text(draw, (width - 80, y2 + 315), fa_text(f"نفت برنت: {oil_val} دلار"), font_body, white)
    silver_val = format_usd(data.get('silver_usd'))
    draw_rtl_text(draw, (width - 80, y2 + 385), fa_text(f"نقره: {silver_val} دلار"), font_body, white)

    y3 = 1140
    draw.rounded_rectangle((50, y3, width - 50, y3 + 420), radius=22, fill=card_fill, outline=card_outline, width=2)
    draw_rtl_text(draw, (width - 80, y3 + 25), fa_text("رمزارزهای برتر"), font_header, gold_color)
    draw.line((80, y3 + 80, width - 80, y3 + 80), fill=line_color, width=2)
    btc_val = format_num(data.get('btc', 0))
    draw_rtl_text(draw, (width - 80, y3 + 115), fa_text(f"بیت‌کوین: {btc_val} تومان"), font_body, white)
    eth_val = format_num(data.get('eth', 0))
    draw_rtl_text(draw, (width - 80, y3 + 205), fa_text(f"اتریوم: {eth_val} تومان"), font_body, white)

    draw.line((80, height - 150, width - 80, height - 150), fill=card_outline, width=1)
    draw_rtl_text(draw, (width - 50, height - 110), fa_text("قیمت لحظه‌ای | ماشین‌حساب | تحلیل هوشمند"), font_small, (160, 175, 200))
    draw_rtl_text(draw, (width - 50, height - 70), "Digi Navasan Channel & Bot", font_small, gold_color)

    bio = BytesIO()
    img.save(bio, format="PNG")
    return bio.getvalue()

# ==========================================
# دریافت اطلاعات قیمت‌ها
# ==========================================
_NERKH_CACHE = {"time": 0, "rates": {}}

def get_nerkh_rates():
    now = time.time()
    if _NERKH_CACHE["rates"] and now - _NERKH_CACHE["time"] < 20:
        return _NERKH_CACHE["rates"]
    try:
        res = requests.get("https://nerkh.jahankhahan.shop/data/live.json", timeout=15)
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
        url = f"https://apiv2.nobitex.ir/market/stats?srcCurrency={symbol}&dstCurrency=usdt"
        res = requests.get(url, timeout=15)
        if res.status_code == 200 and f"{symbol}-usdt" in res.json().get('stats', {}):
            return float(res.json()['stats'][f"{symbol}-usdt"]['latest'])
    except Exception: pass
    return None

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
    if not GROQ_API_KEY: return "⚠️ کلید هوش مصنوعی تنظیم نشده است."
    try:
        url = "https://api.groq.com/openai/v1/chat/completions"
        headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
        data = {
            "model": "openai/gpt-oss-120b",
            "messages": [{"role": "system", "content": "You are a helpful assistant. Answer in Persian."}, {"role": "user", "content": prompt}],
            "temperature": 0.7, "max_tokens": 2048
        }
        response = requests.post(url, headers=headers, json=data, timeout=60)
        if response.status_code == 200:
            return (response.json()['choices'][0]['message'].get('content') or '').strip() or "پاسخی دریافت نشد."
        if response.status_code == 429: return "⏳ تعداد درخواست‌ها زیاد است؛ چند لحظه بعد تلاش کنید."
        if response.status_code in (401, 403): return "⚠️ کلید هوش مصنوعی نامعتبر است."
        return "خطایی در ارتباط با هوش مصنوعی رخ داد."
    except Exception: return "خطای اتصال به هوش مصنوعی."

# ---------------- آب‌وهوا ----------------
def _normalize_fa(text):
    t = str(text or "")
    t = t.replace("ي", "ی").replace("ك", "ک").replace("\u200c", " ").replace("ـ", "")
    return " ".join(t.split())

def _has_persian(text): return bool(re.search(r"[\u0600-\u06FF]", text))

_CITY_EN_RAW = {
    "تهران": "Tehran", "مشهد": "Mashhad", "اصفهان": "Isfahan", "کرج": "Karaj", "شیراز": "Shiraz",
    "تبریز": "Tabriz", "قم": "Qom", "اهواز": "Ahvaz", "کرمانشاه": "Kermanshah", "ارومیه": "Urmia",
    "رشت": "Rasht", "زاهدان": "Zahedan", "کرمان": "Kerman", "همدان": "Hamadan", "یزد": "Yazd",
    "اردبیل": "Ardabil", "بندرعباس": "Bandar Abbas", "اراک": "Arak", "زنجان": "Zanjan",
    "سنندج": "Sanandaj", "قزوین": "Qazvin", "خرم آباد": "Khorramabad", "گرگان": "Gorgan",
    "ساری": "Sari", "بجنورد": "Bojnord", "بوشهر": "Bushehr", "بیرجند": "Birjand", "ایلام": "Ilam",
    "شهرکرد": "Shahr-e Kord", "سمنان": "Semnan", "یاسوج": "Yasuj", "کیش": "Kish", "قشم": "Qeshm",
    "آبادان": "Abadan", "دزفول": "Dezful", "نیشابور": "Nishapur", "سبزوار": "Sabzevar",
    "کاشان": "Kashan", "ایرانشهر": "Iranshahr", "چابهار": "Chabahar", "ساوه": "Saveh", "خوی": "Khoy",
    "مراغه": "Maragheh", "مهاباد": "Mahabad", "بابل": "Babol", "آمل": "Amol", "قائم شهر": "Qaem Shahr",
    "ورامین": "Varamin", "شهریار": "Shahriar", "اسلامشهر": "Eslamshahr", "ملارد": "Malard",
    "جهرم": "Jahrom", "مرودشت": "Marvdasht", "سیرجان": "Sirjan", "رفسنجان": "Rafsanjan", "بم": "Bam",
    "زابل": "Zabol", "نجف آباد": "Najafabad", "شاهرود": "Shahroud", "دامغان": "Damghan",
    "بهبهان": "Behbahan", "ماهشهر": "Mahshahr", "خرمشهر": "Khorramshahr", "سقز": "Saqqez",
    "بوکان": "Bukan", "ملایر": "Malayer", "نهاوند": "Nahavand", "بروجرد": "Borujerd",
    "اندیمشک": "Andimeshk", "گنبد کاووس": "Gonbad-e Kavus", "بندر انزلی": "Bandar-e Anzali",
    "انزلی": "Bandar-e Anzali", "لاهیجان": "Lahijan", "آستارا": "Astara", "چالوس": "Chalus",
    "رامسر": "Ramsar", "نوشهر": "Nowshahr", "تنکابن": "Tonekabon"
}
CITY_EN = {_normalize_fa(k): v for k, v in _CITY_EN_RAW.items()}

WMO_FA = {
    0: "آسمان صاف ☀️", 1: "عمدتاً صاف 🌤", 2: "نیمه‌ابری ⛅", 3: "ابری ☁️",
    45: "مه 🌫", 48: "مه یخ‌زده 🌫",
    51: "نم‌نم باران خفیف 🌦", 53: "نم‌نم باران 🌦", 55: "نم‌نم باران شدید 🌧",
    56: "نم‌نم یخ‌زده 🌧", 57: "نم‌نم یخ‌زده شدید 🌧",
    61: "باران خفیف 🌧", 63: "باران 🌧", 65: "باران شدید 🌧",
    66: "باران یخ‌زده 🌧", 67: "باران یخ‌زده شدید 🌧",
    71: "برف خفیف 🌨", 73: "برف 🌨", 75: "برف شدید ❄️", 77: "دانه‌های برف ❄️",
    80: "رگبار خفیف 🌦", 81: "رگبار 🌧", 82: "رگبار شدید ⛈",
    85: "رگبار برف 🌨", 86: "رگبار برف شدید 🌨",
    95: "رعد و برق ⛈", 96: "رعد و برق و تگرگ ⛈", 99: "رعد و برق و تگرگ شدید ⛈"
}

def _wmo_text(code):
    try: return WMO_FA.get(int(code), "نامشخص")
    except Exception: return "نامشخص"

def _num(value, digits=0):
    try: return f"{float(value):.{digits}f}"
    except (TypeError, ValueError): return "؟"

def _get_weather_openmeteo(city_in):
    query = CITY_EN.get(city_in, city_in)
    geo = requests.get("https://geocoding-api.open-meteo.com/v1/search", params={"name": query, "count": 10, "language": "fa", "format": "json"}, timeout=15)
    if geo.status_code != 200: return None
    results = geo.json().get("results") or []
    if not results: return None
    place = results[0]
    if _has_persian(city_in) or city_in in CITY_EN:
        for r in results:
            if r.get("country_code") == "IR":
                place = r
                break
    res = requests.get("https://api.open-meteo.com/v1/forecast", params={
        "latitude": place["latitude"], "longitude": place["longitude"],
        "current": "temperature_2m,relative_humidity_2m,apparent_temperature,weather_code,wind_speed_10m",
        "daily": "temperature_2m_max,temperature_2m_min", "timezone": "auto", "forecast_days": 1
    }, timeout=15)
    if res.status_code != 200: return None
    data = res.json()
    cur = data.get("current") or {}
    if cur.get("temperature_2m") is None: return None
    daily = data.get("daily") or {}
    t_max = (daily.get("temperature_2m_max") or [None])[0]
    t_min = (daily.get("temperature_2m_min") or [None])[0]
    name = place.get("name") or city_in
    country = place.get("country")
    title = f"{name}، {country}" if country else name
    now = datetime.now(TEHRAN).strftime('%H:%M')
    
    temp = _num(cur.get('temperature_2m'))
    feels = _num(cur.get('apparent_temperature'))
    wmo = _wmo_text(cur.get('weather_code'))
    max_t = _num(t_max)
    min_t = _num(t_min)
    hum = _num(cur.get('relative_humidity_2m'))
    wind = _num(cur.get('wind_speed_10m'))
    
    return (
        f"🌤 **وضعیت آب‌وهوای {title}**\n\n"
        f"🌡 دما: {temp} درجه 
