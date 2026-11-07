# -*- coding: utf-8 -*-
import os
import sys
import time
import math
import json
import asyncio
import logging
import re
import tempfile
import threading
import requests
import urllib3
from io import BytesIO
from urllib.parse import quote
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from contextlib import asynccontextmanager
from typing import Optional, Dict, Any, List, Tuple

import pytz
import jdatetime
from PIL import Image, ImageDraw, ImageFont, features
import arabic_reshaper

# خاموش کردن هشدارهای SSL (برای رد شدن از فیلتر سرورهای خارجی)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

try:
    from bidi.algorithm import get_display
except ImportError:
    from bidi import get_display

from baleio import Bot, Dispatcher, md
from baleio.client.default import DefaultBotProperties
from baleio.enums import ChatType, ParseMode
from baleio.filters import CommandStart
from baleio.types import Message, ReplyKeyboardMarkup, KeyboardButton

logging.basicConfig(format="%(asctime)s | %(levelname)-7s | %(message)s", level=logging.INFO, stream=sys.stdout)
log = logging.getLogger("diginavasan")

BALE_TOKEN   = os.environ.get("BALE_TOKEN")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
BRS_API_KEY  = os.environ.get("BRS_API_KEY")
PORT         = int(os.environ.get("PORT", "10000"))
CHANNEL_ID   = "@diginavasan"
ADMIN_ID     = int(os.environ.get("ADMIN_ID", "718840144"))

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.environ.get("DATA_DIR", os.path.join(BASE_DIR, "data"))
os.makedirs(DATA_DIR, exist_ok=True)

UPSTASH_URL   = os.environ.get("UPSTASH_REDIS_REST_URL")
UPSTASH_TOKEN = os.environ.get("UPSTASH_REDIS_REST_TOKEN")
REDIS_KEY     = "diginavasan:bale:scheduler_state"
TEHRAN        = pytz.timezone("Asia/Tehran")

CHANNEL_FOOTER = "\n\n────────────\n🤖 [ورود به ربات دیجی نوسان](https://ble.ir/diginavasan_bot) | 📢 [کانال دیجی نوسان](https://ble.ir/diginavasan)"

FONT_PATHS = [os.path.join(BASE_DIR, "Vazirmatn-Bold.ttf"), os.path.join(BASE_DIR, "fonts", "Vazirmatn-Bold.ttf"), os.path.join(BASE_DIR, "font.ttf")]

class PriceRecord:
    __slots__ = ("value", "source", "fetched_at", "status", "error", "metric")
    def __init__(self, value=None, source="", fetched_at=None, status="ok", error=None):
        self.value, self.source, self.fetched_at, self.status, self.error = value, source, fetched_at, status, error
    def age_sec(self):
        return (datetime.now(TEHRAN) - self.fetched_at).total_seconds() if self.fetched_at else None

class PriceStore:
    def __init__(self):
        self._lock = threading.Lock()
        self.fiat = {}; self.gold = {}; self.crypto = {}; self.usdt = {}; self.aux = {}; self._messages = {}; self._last_cycle_ok = None
    def set_bucket(self, name, records):
        with self._lock: setattr(self, name, dict(records))
    def get_bucket(self, name):
        with self._lock: return dict(getattr(self, name))
    def set_message(self, key, text):
        with self._lock: self._messages[key] = text
    def get_message(self, key):
        with self._lock: return self._messages.get(key)
    def touch_cycle(self):
        with self._lock: self._last_cycle_ok = datetime.now(TEHRAN)
    @property
    def last_cycle(self):
        with self._lock: return self._last_cycle_ok

STORE = PriceStore()

def _is_positive_number(x):
    try:
        v = float(x)
        return math.isfinite(v) and v > 0
    except: return False

def _to_toman(value, source_publishes_rial):
    return value / 10.0 if source_publishes_rial else value

# درخواست امن با غیرفعال کردن SSL و هدر مرورگر
def _req_json(url, params=None):
    try:
        r = requests.get(url, params=params, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}, timeout=20, verify=False)
        if r.status_code == 200: 
            return r.json()
        log.error(f"Req {url} status: {r.status_code}")
    except Exception as e: 
        log.error(f"Req failed {url}: {e}")
    return None

async def fetch_usdt(session):
    now = datetime.now(TEHRAN)
    out = {}
    
    async def nobitex():
        data = await asyncio.to_thread(_req_json, "https://apiv2.nobitex.ir/market/stats", {"srcCurrency": "usdt", "dstCurrency": "rls"})
        v = (data or {}).get("stats", {}).get("usdt-rls", {}).get("latest")
        if _is_positive_number(v): out["نوبیتکس"] = PriceRecord(_to_toman(float(v), True), "Nobitex", now)
        else: out["نوبیتکس"] = PriceRecord(status="error", source="Nobitex", fetched_at=now)

    async def wallex():
        data = await asyncio.to_thread(_req_json, "https://api.wallex.ir/v1/markets")
        v = ((data or {}).get("result") or {}).get("symbols", {}).get("USDTTMN", {}).get("stats", {}).get("lastPrice")
        if _is_positive_number(v): out["والکس"] = PriceRecord(float(v), "Wallex", now)
        else: out["والکس"] = PriceRecord(status="error", source="Wallex", fetched_at=now)

    async def ramzinex():
        data = await asyncio.to_thread(_req_json, "https://publicapi.ramzinex.com/exchange/api/v1.0/exchange/pairs")
        for item in (data or {}).get("data", []) or []:
            name_en = ((item.get("name") or {}).get("en") or "").lower()
            if "tether" in name_en or "usdt" in name_en:
                v = (((item.get("financial") or {}).get("last24h") or {}).get("close")) or item.get("sell")
                if _is_positive_number(v): out["رمزینکس"] = PriceRecord(_to_toman(float(v), True), "Ramzinex", now); return
        out["رمزینکس"] = PriceRecord(status="error", source="Ramzinex", fetched_at=now)

    async def bitpin():
        data = await asyncio.to_thread(_req_json, "https://api.bitpin.ir/api/v1/mkt/tickers/")
        for item in data or []:
            if item.get("symbol") == "USDT_IRT":
                v = item.get("price")
                if _is_positive_number(v): out["بیت‌پین"] = PriceRecord(float(v), "Bitpin", now); return
        out["بیت‌پین"] = PriceRecord(status="error", source="Bitpin", fetched_at=now)

    async def tabdeal():
        data = await asyncio.to_thread(_req_json, "https://api.tabdeal.org/api/v1/market/statistics")
        v = ((data or {}).get("USDTIRT") or {}).get("lastPrice")
        if _is_positive_number(v): out["تبدیل"] = PriceRecord(float(v), "Tabdeal", now)
        else: out["تبدیل"] = PriceRecord(status="error", source="Tabdeal", fetched_at=now)

    await asyncio.gather(nobitex(), wallex(), ramzinex(), bitpin(), tabdeal(), return_exceptions=True)
    
    old = STORE.get_bucket("usdt")
    for k, rec in list(out.items()):
        if rec.status != "ok" and k in old and old[k].value is not None:
            out[k] = PriceRecord(old[k].value, old[k].source, old[k].fetched_at, "stale", rec.error)
    return out

async def fetch_crypto(session):
    now = datetime.now(TEHRAN)
    out = {}
    meta = {"BTC": "btc", "ETH": "eth", "BNB": "bnb", "TRX": "trx", "SOL": "sol", "DOGE": "doge", "TON": "ton"}
    async def one(sym, coin_id):
        data = await asyncio.to_thread(_req_json, "https://apiv2.nobitex.ir/market/stats", {"srcCurrency": coin_id, "dstCurrency": "rls"})
        v = ((data or {}).get("stats") or {}).get(f"{coin_id}-rls", {}).get("latest")
        if _is_positive_number(v): out[sym] = PriceRecord(_to_toman(float(v), True), "Nobitex", now)
        else: out[sym] = PriceRecord(status="error", source="Nobitex", fetched_at=now)
    await asyncio.gather(*(one(s, c) for s, c in meta.items()), return_exceptions=True)
    old = STORE.get_bucket("crypto")
    for k, rec in list(out.items()):
        if rec.status != "ok" and k in old and old[k].value is not None:
            out[k] = PriceRecord(old[k].value, old[k].source, old[k].fetched_at, "stale", rec.error)
    return out

async def fetch_aux(session):
    now = datetime.now(TEHRAN)
    out = {}
    async def one(key, sym):
        data = await asyncio.to_thread(_req_json, "https://apiv2.nobitex.ir/market/stats", {"srcCurrency": sym, "dstCurrency": "usdt"})
        v = ((data or {}).get("stats") or {}).get(f"{sym}-usdt", {}).get("latest")
        if _is_positive_number(v): out[key] = PriceRecord(float(v), "Nobitex", now)
        else: out[key] = PriceRecord(status="error", source="Nobitex", fetched_at=now)
    await asyncio.gather(one("oil", "usoon"), one("silver", "slvon"), return_exceptions=True)
    old = STORE.get_bucket("aux")
    for k, rec in list(out.items()):
        if rec.status != "ok" and k in old and old[k].value is not None:
            out[k] = PriceRecord(old[k].value, old[k].source, old[k].fetched_at, "stale", rec.error)
    return out

async def fetch_nerkh(session):
    data = await asyncio.to_thread(_req_json, "https://nerkh.jahankhahan.shop/data/live.json")
    return data.get("rates") if isinstance(data, dict) else None
def build_usdt_message(records):
    valid = [(k, r) for k, r in records.items() if r.value and r.status == "ok"]
    stale = [(k, r) for k, r in records.items() if r.value and r.status == "stale"]
    if not valid and not stale: return None
    lines = [f"💵 **مقایسه قیمت تتر (USDT)**\n⏰ `{datetime.now(TEHRAN).strftime('%H:%M')}`\n"]
    for i, (name, r) in enumerate(sorted(valid + stale, key=lambda x: x[1].value)):
        tag = "🥇 (ارزان‌ترین)" if i == 0 and r.status == "ok" else (f"⚠️ {int(r.age_sec() or 0)//60}m" if r.status=="stale" else "")
        lines.append(f"{'🥇' if i==0 and r.status=='ok' else '🔸'} **{name}**: `{r.value:,.0f}` تومان {tag}")
    if len(valid) >= 2:
        diff = max(r.value for _, r in valid) - min(r.value for _, r in valid)
        lines.append(f"\n📊 اختلاف: `{diff:,.0f}` تومان")
    return "\n".join(lines)

def build_crypto_message(records):
    good = {k: r for k, r in records.items() if r.value is not None and r.status in ("ok", "stale")}
    if not good: return None
    meta = {"BTC":"بیت‌کوین", "ETH":"اتریوم", "BNB":"بایننس کوین", "TRX":"ترون", "SOL":"سولانا", "DOGE":"دوج کوین", "TON":"تون کوین"}
    lines = [f"🪙 **قیمت لحظه‌ای ارز دیجیتال**\n⏰ `{datetime.now(TEHRAN).strftime('%H:%M')}`\n"]
    for sym, fa in meta.items():
        r = good.get(sym)
        lines.append(f"🟢 **{fa} ({sym})**: `{r.value:,.0f}` تومان" if r else f"🔹 **{fa} ({sym})**: ناموجود")
    return "\n".join(lines)

FIAT_KEYS = [("dollar","💵 دلار",["dollar","usd"]),("euro","💶 یورو",["euro","eur"]),("pound","💷 پوند",["pound","gbp"]),("aed","🇦🇪 درهم",["aed"]),("lira","🇹🇷 لیر",["lira","try"]),("yuan","🇨🇳 یوان",["yuan","cny"]),("cad","🇨🇦 کانادا",["cad"]),("aud","🇦🇺 استرالیا",["aud"]),("chf","🇨🇭 فرانک سوئیس",["chf"]),("sar","🇸🇦 ریال عربستان",["sar"]),("qar","🇶🇦 ریال قطر",["qar"]),("kwd","🇰🇼 دینار کویت",["kwd"]),("iqd","🇮🇶 دینار عراق",["iqd"]),("afn","🇦🇫 افغانی",["afn"]),("rub","🇷🇺 روبل",["rub"])]
GOLD_KEYS = [("gold_18","🥇 طلا ۱۸ عیار",["gold"]),("gold_24","🥇 طلا ۲۴ عیار",["gold24"]),("coin","🪙 سکه امامی",["coin"]),("bahar","🪙 سکه بهار",["bahar"]),("half","🪙 نیم سکه",["half"]),("quarter","🪙 ربع سکه",["quarter"])]

def _build_bucket(keys, rates, bucket_name):
    now = datetime.now(TEHRAN)
    out = {}
    old = STORE.get_bucket(bucket_name)
    for key, _, aliases in keys:
        v = next((float(rates[a]) for a in aliases if rates and _is_positive_number(rates.get(a))), None)
        if v: out[key] = PriceRecord(v, "jahankhahan", now, "ok")
        elif key in old and old[key].value: out[key] = PriceRecord(old[key].value, "jahankhahan", old[key].fetched_at, "stale", "missing")
        else: out[key] = PriceRecord(status="error", source="jahankhahan", fetched_at=now)
    return out

def build_fiat_message(records):
    if not any(r.value for r in records.values()): return None
    lines = [f"💱 **قیمت لحظه‌ای ارزها**\n⏰ `{datetime.now(TEHRAN).strftime('%H:%M')}`\n"]
    for k, label, _ in FIAT_KEYS:
        r = records.get(k)
        lines.append(f"{label} = `{r.value:,.0f}` تومان" if r and r.value else f"{label} = ناموجود")
    return "\n".join(lines)

def build_gold_message(records):
    if not any(r.value for r in records.values()): return None
    lines = [f"🌕 **قیمت لحظه‌ای طلا و سکه**\n⏰ `{datetime.now(TEHRAN).strftime('%H:%M')}`\n"]
    for k, label, _ in GOLD_KEYS:
        r = records.get(k)
        lines.append(f"{label} = `{r.value:,.0f}` تومان" if r and r.value else f"{label} = ناموجود")
    return "\n".join(lines)

async def refresh_cycle():
    async with http_session() as session:
        rates, usdt, crypto, aux = await asyncio.gather(fetch_nerkh(session), fetch_usdt(session), fetch_crypto(session), fetch_aux(session), return_exceptions=True)
    
    if isinstance(rates, Exception): rates = None
    if isinstance(usdt, Exception): usdt = {}
    if isinstance(crypto, Exception): crypto = {}
    if isinstance(aux, Exception): aux = {}

    fiat_rec = _build_bucket(FIAT_KEYS, rates, "fiat")
    gold_rec = _build_bucket(GOLD_KEYS, rates, "gold")

    STORE.set_bucket("fiat", fiat_rec)
    STORE.set_bucket("gold", gold_rec)
    STORE.set_bucket("usdt", usdt)
    STORE.set_bucket("crypto", crypto)
    STORE.set_bucket("aux", aux)

    for k, m in [("fiat", build_fiat_message(fiat_rec)), ("gold", build_gold_message(gold_rec)), ("crypto", build_crypto_message(crypto)), ("usdt", build_usdt_message(usdt))]:
        if m: STORE.set_message(k, m)
    STORE.touch_cycle()

async def cache_loop():
    while True:
        try: await refresh_cycle()
        except Exception: log.exception("Cache loop error")
        await asyncio.sleep(30)

HAS_RAQM = features.check("raqm")
def _fa(text): return str(text) if HAS_RAQM else get_display(arabic_reshaper.reshape(str(text)))
def _font_supports_persian(path):
    try:
        if not os.path.exists(path) or os.path.getsize(path) < 20000: return False
        f = ImageFont.truetype(path, 40)
        img = Image.new("L", (90, 90), 0); ImageDraw.Draw(img).text((10, 10), "ی", font=f, fill=255); sig_y = img.tobytes()
        img2 = Image.new("L", (90, 90), 0); ImageDraw.Draw(img2).text((10, 10), "\uffff", font=f, fill=255); missing = img2.tobytes()
        return sig_y not in (missing, Image.new("L", (90, 90), 0).tobytes())
    except Exception: return False

_FONT_PATH_CACHE = None
def resolve_font_path():
    global _FONT_PATH_CACHE
    if _FONT_PATH_CACHE and _font_supports_persian(_FONT_PATH_CACHE): return _FONT_PATH_CACHE
    for p in FONT_PATHS:
        if _font_supports_persian(p): _FONT_PATH_CACHE = p; log.info("Font loaded: %s", p); return p
    log.error("No usable Persian font found.")
    return None

def generate_infographic():
    path = resolve_font_path()
    if not path: return None
    f_title, f_head, f_body, f_small = tuple(ImageFont.truetype(path, s) for s in (56, 34, 28, 22))
    W, H = 1080, 1920
    img = Image.new("RGB", (W, H), (10, 18, 32)); d = ImageDraw.Draw(img)
    for y in range(H):
        d.line([(0, y), (W, y)], fill=(int(10+(22-10)*y/H), int(18+(32-18)*y/H), int(32+(56-32)*y/H)))
    
    def _rtl(xy, text, font, fill):
        bbox = d.textbbox((0, 0), text, font=font); w = bbox[2] - bbox[0]
        d.text((xy[0] - w, xy[1]), text, font=font, fill=fill)

    GOLD, WHITE, GRAY, CARD, OUT, LINE = (255,210,80), (255,255,255), (180,190,210), (20,32,54), (70,95,140), (45,65,100)
    d.rounded_rectangle((50, 40, W - 50, 190), radius=24, fill=(24, 38, 64), outline=(218, 165, 32), width=3)
    _rtl((W - 80, 48), _fa("دیجی نوسان | خلاصه بازار"), f_title, GOLD)
    _rtl((W - 80, 135), _fa(STORE.last_cycle.strftime("%Y/%m/%d - %H:%M") if STORE.last_cycle else "..."), f_small, GRAY)

    def card(y, h, title):
        d.rounded_rectangle((50, y, W - 50, y + h), radius=22, fill=CARD, outline=OUT, width=2)
        _rtl((W - 80, y + 20), _fa(title), f_head, GOLD); d.line((80, y + 78, W - 80, y + 78), fill=LINE, width=2)

    def row(y, label, value, font=f_body):
        _rtl((W - 80, y), _fa(label), font, GRAY); _rtl((W - 80, y + 32), _fa(value), font, WHITE)

    gold, fiat, crypto, aux, usdt = STORE.get_bucket("gold"), STORE.get_bucket("fiat"), STORE.get_bucket("crypto"), STORE.get_bucket("aux"), STORE.get_bucket("usdt")
    
    card(220, 600, "طلا و سکه")
    y = 320
    for k, label in [("gold_18", "طلای ۱۸ عیار:"),("gold_24", "طلای ۲۴ عیار:"),("coin", "سکه امامی:"),("bahar", "سکه بهار آزادی:"),("half", "نیم سکه:"),("quarter", "ربع سکه:")]:
        r = gold.get(k); row(y, label, f"{r.value:,.0f} تومان" if r and r.value else "—"); y += 82

    card(840, 470, "ارزهای خارجی")
    usdt_min = min((r.value for r in usdt.values() if r.value and r.status == "ok"), default=None)
    y = 940
    for label, val in [("دلار:", fiat.get("dollar")), ("یورو:", fiat.get("euro")), ("تتر (بهترین):", type('V', (), {'value': usdt_min})), ("نفت برنت:", aux.get("oil")), ("نقره:", aux.get("silver"))]:
        if label in ("نفت برنت:", "نقره:"): row(y, label, f"{val.value:,.2f} دلار" if val and val.value else "—")
        else: row(y, label, f"{val.value:,.0f} تومان" if val and getattr(val, 'value', None) else "—")
        y += 72

    card(1330, 500, "رمزارزهای برتر")
    y = 1430
    for sym, fa in [("BTC","بیت‌کوین"),("ETH","اتریوم"),("BNB","بایننس"),("TRX","ترون"),("SOL","سولانا"),("DOGE","دوج"),("TON","تون")]:
        r = crypto.get(sym); _rtl((W - 80, y), _fa(f"{fa} ({sym}):"), f_small, GRAY); _rtl((W - 80, y + 26), _fa(f"{r.value:,.0f} تومان" if r and r.value else "—"), f_small, WHITE); y += 56

    bio = BytesIO(); img.save(bio, format="PNG", optimize=True); return bio.getvalue()

_ai_hits = {}
async def get_ai_response(prompt, uid):
    if not GROQ_API_KEY: return "⚠️ کلید AI تنظیم نشده."
    if len(prompt) > 2000: return "❌ متن طولانی است."
    now = time.time(); hits = [t for t in _ai_hits.get(uid, []) if now - t < 60]
    if len(hits) >= 6: return "⏳ درخواست زیاد است."
    _ai_hits[uid] = hits + [now]
    try:
        async with http_session() as s:
            async with s.post("https://api.groq.com/openai/v1/chat/completions", headers={"Authorization": f"Bearer {GROQ_API_KEY}"}, json={"model": "openai/gpt-oss-120b", "messages": [{"role": "user", "content": prompt}], "temperature": 0.7, "max_tokens": 1024}) as resp:
                if resp.status == 200: return (await resp.json())["choices"][0]["message"]["content"].strip()
                return "خطا در پاسخ AI."
    except Exception: return "خطای اتصال."

_DIGITS_MAP = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
def _norm_fa(t):
    t = (t or "").translate(_DIGITS_MAP)
    t = t.replace("ي", "ی").replace("ك", "ک").replace("\u200c", " ").replace("ـ", "")
    return " ".join(t.split())

CITY_EN = {"تهران": "Tehran", "مشهد": "Mashhad", "اصفهان": "Isfahan", "کرج": "Karaj", "شیراز": "Shiraz", "تبریز": "Tabriz", "قم": "Qom", "اهواز": "Ahvaz"}
WMO_FA = {0: "آسمان صاف ☀️", 1: "عمدتاً صاف 🌤", 2: "نیمه‌ابری ⛅", 3: "ابری ☁️", 45: "مه 🌫", 48: "مه یخ‌زده 🌫", 51: "نم‌نم باران خفیف 🌦", 53: "نم‌نم باران 🌦", 55: "نم‌نم باران شدید 🌧", 61: "باران خفیف 🌧", 63: "باران 🌧", 65: "باران شدید 🌧", 71: "برف خفیف 🌨", 73: "برف 🌨", 75: "برف شدید ❄️", 80: "رگبار خفیف 🌦", 81: "رگبار 🌧", 82: "رگبار شدید ⛈", 95: "رعد و برق ⛈", 96: "رعد و برق و تگرگ ⛈", 99: "رعد و برق و تگرگ شدید ⛈"}

async def get_weather(city):
    city = _norm_fa(city)
    if not city: return "❌ نام شهر را وارد کنید."
    query = CITY_EN.get(city, city)
    try:
        geo = await asyncio.to_thread(_req_json, "https://geocoding-api.open-meteo.com/v1/search", {"name": query, "count": 10, "language": "fa", "format": "json"})
        results = (geo or {}).get("results") or []
        if results:
            place = next((r for r in results if r.get("country_code") == "IR"), results[0])
            fc = await asyncio.to_thread(_req_json, "https://api.open-meteo.com/v1/forecast", {
                "latitude": place["latitude"], "longitude": place["longitude"],
                "current": "temperature_2m,relative_humidity_2m,apparent_temperature,weather_code,wind_speed_10m",
                "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max,weather_code",
                "timezone": "auto", "forecast_days": 2
            })
            if fc and (fc.get("current") or {}).get("temperature_2m") is not None:
                cur = fc["current"]; daily = fc.get("daily") or {}
                tmax = (daily.get("temperature_2m_max") or [None])[0]
                tmin = (daily.get("temperature_2m_min") or [None])[0]
                rain_prob = (daily.get("precipitation_probability_max") or [0])[0]
                tomorrow_code = (daily.get("weather_code") or [0])[1] if len(daily.get("weather_code") or []) > 1 else 0
                tomorrow_tmax = (daily.get("temperature_2m_max") or [None])[1] if len(daily.get("temperature_2m_max") or []) > 1 else None
                tomorrow_tmin = (daily.get("temperature_2m_min") or [None])[1] if len(daily.get("temperature_2m_min") or []) > 1 else None
                
                name = place.get("name") or city
                w_desc = WMO_FA.get(int(cur.get('weather_code', -1)), 'نامشخص')
                rain_text = f" (احتمال بارش: {rain_prob}٪)" if rain_prob > 10 else ""
                tomorrow_text = f"\n\n📅 **فردا:** {WMO_FA.get(int(tomorrow_code), 'نامشخص')}\n🔽 {tomorrow_tmin} | 🔼 {tomorrow_tmax} درجه" if tomorrow_tmax else ""

                return (
                    f"🌤 **وضعیت آب‌وهوای {name}**\n\n"
                    f"🌡 دما: {cur['temperature_2m']:.0f} درجه (احساس {cur.get('apparent_temperature', 0):.0f})\n"
                    f"☁️ وضعیت: {w_desc}{rain_text}\n"
                    f"🔼 بیشینه: {tmax if tmax is not None else '؟'} | 🔽 کمینه: {tmin if tmin is not None else '؟'}\n"
                    f"💧 رطوبت: {cur.get('relative_humidity_2m', '؟')}٪\n"
                    f"💨 باد: {cur.get('wind_speed_10m', '؟')} km/h"
                    f"{tomorrow_text}\n"
                    f"⏰ `{datetime.now(TEHRAN).strftime('%H:%M')}`"
                )
    except Exception: log.exception("open-meteo error")
    return "❌ شهر پیدا نشد یا سرویس در دسترس نیست."

def parse_number(text, lo=None, hi=None):
    t = str(text or "").translate(_DIGITS_MAP)
    for ch in (",", "٬", "،", " ", "\u200c", "_", "٪", "%"): t = t.replace(ch, "")
    t = t.replace("٫", ".")
    v = float(t)
    if not math.isfinite(v): raise ValueError("not finite")
    if lo is not None and v < lo: raise ValueError("too small")
    if hi is not None and v > hi: raise ValueError("too large")
    return v

def calc_gold(price_per_gram, weight, wage_pct, profit_pct, tax_pct):
    raw = price_per_gram * weight
    wage = raw * (wage_pct / 100.0)
    profit = raw * (profit_pct / 100.0)
    base = raw + wage + profit
    tax = base * (tax_pct / 100.0)
    return {"raw": raw, "wage": wage, "profit": profit, "tax": tax, "total": base + tax, "price_per_gram": price_per_gram, "weight": weight, "wage_pct": wage_pct, "profit_pct": profit_pct, "tax_pct": tax_pct}

def calc_loan(amount, annual_rate_pct, months):
    r = (annual_rate_pct / 100.0) / 12.0
    if r == 0: monthly = amount / months
    else:
        f = (1 + r) ** months
        monthly = amount * r * f / (f - 1)
    total = monthly * months
    return {"amount": amount, "rate_pct": annual_rate_pct, "months": months, "monthly": monthly, "total": total, "interest_total": total - amount}
BTN_START   = "🚀 استارت / منوی اصلی"
BTN_BACK    = "🔙 بازگشت به منوی اصلی"
BTN_FIAT    = "💱 قیمت ارزها"
BTN_GOLD    = "🌕 قیمت طلا و سکه"
BTN_CRYPTO  = "🪙 قیمت ارز دیجیتال"
BTN_USDT    = "💵 مقایسه قیمت تتر"
BTN_GCALC   = "🧮 ماشین‌حساب طلا"
BTN_LCALC   = "💸 ماشین‌حساب اقساط"
BTN_WEATHER = "🌤 وضعیت آب‌وهوا"
BTN_AI      = "🤖 چت با هوش مصنوعی"
MENU_BUTTONS = {BTN_FIAT, BTN_GOLD, BTN_CRYPTO, BTN_USDT, BTN_GCALC, BTN_LCALC, BTN_WEATHER, BTN_AI}

_user_states = {}
_user_lock = threading.Lock()

def ustate_get(uid):
    with _user_lock:
        st = _user_states.get(uid)
        if not st: return None
        if time.time() - st.get("_touched", 0) > 1800: _user_states.pop(uid, None); return None
        return st

def ustate_set(uid, **kw):
    with _user_lock:
        st = _user_states.setdefault(uid, {})
        st.update(kw); st["_touched"] = time.time()

def ustate_clear(uid):
    with _user_lock: _user_states.pop(uid, None)

def main_menu():
    kb = [
        [KeyboardButton(text=BTN_START)],
        [KeyboardButton(text=BTN_FIAT), KeyboardButton(text=BTN_GOLD)],
        [KeyboardButton(text=BTN_CRYPTO), KeyboardButton(text=BTN_USDT)],
        [KeyboardButton(text=BTN_GCALC), KeyboardButton(text=BTN_LCALC)],
        [KeyboardButton(text=BTN_WEATHER), KeyboardButton(text=BTN_AI)],
    ]
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

def back_menu():
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text=BTN_BACK)]], resize_keyboard=True)

def _split(text, limit=3800):
    text = str(text or "")
    if len(text) <= limit: return [text]
    chunks, cur = [], ""
    for line in text.split("\n"):
        if len(cur) + len(line) + 1 > limit: chunks.append(cur); cur = line
        else: cur = f"{cur}\n{line}" if cur else line
    if cur: chunks.append(cur)
    return chunks

async def safe_reply(message, text, **kw):
    parts = _split(text)
    for i, part in enumerate(parts):
        await message.answer(part, **(kw if i == len(parts) - 1 else {}))

dp = Dispatcher()

@dp.message(CommandStart())
async def cmd_start(message: Message):
    if message.chat.type != ChatType.PRIVATE: return
    ustate_clear(message.from_user.id)
    name = (message.from_user.first_name or "دوست عزیز").strip()
    await safe_reply(message, f"سلام {name} عزیز! به ربات دیجی نوسان خوش آمدید. ✅\n\nاز منوی زیر یکی از گزینه‌ها را انتخاب کنید:", reply_markup=main_menu())

STATE_ERRORS = {
    "gold_waiting_price": "❌ لطفاً یک عدد معتبر وارد کنید.",
    "gold_waiting_weight": "❌ لطفاً یک عدد معتبر وارد کنید.",
    "gold_waiting_wage": "❌ لطفاً درصد اجرت را وارد کنید.",
    "gold_waiting_profit": "❌ لطفاً درصد سود را وارد کنید.",
    "gold_waiting_tax": "❌ لطفاً درصد مالیات را وارد کنید.",
    "loan_waiting_amount": "❌ لطفاً مبلغ معتبر وارد کنید.",
    "loan_waiting_interest": "❌ لطفاً نرخ سود وارد کنید.",
    "loan_waiting_months": "❌ لطفاً تعداد ماه صحیح وارد کنید."
}

async def menu_click(message, text, uid):
    if text == BTN_FIAT: await safe_reply(message, STORE.get_message("fiat") or "⏳ در حال دریافت...")
    elif text == BTN_GOLD: await safe_reply(message, STORE.get_message("gold") or "⏳ در حال دریافت...")
    elif text == BTN_CRYPTO: await safe_reply(message, STORE.get_message("crypto") or "⏳ در حال دریافت...")
    elif text == BTN_USDT: await safe_reply(message, STORE.get_message("usdt") or "⏳ در حال دریافت...")
    elif text == BTN_GCALC:
        g = STORE.get_bucket("gold")
        g18, g24 = g.get("gold_18"), g.get("gold_24")
        msg = f"🧮 **ماشین‌حساب طلا**\n\n💡 قیمت روز:\n🥇 ۱۸ عیار: `{g18.value:,.0f}`\n🥇 ۲۴ عیار: `{g24.value:,.0f}`\n\n👇 **قیمت هر گرم** را وارد کنید:" if g18 and g24 else "👇 **قیمت هر گرم** را وارد کنید:"
        ustate_set(uid, state="gold_waiting_price")
        await safe_reply(message, msg, reply_markup=back_menu())
    elif text == BTN_LCALC:
        ustate_set(uid, state="loan_waiting_amount")
        await safe_reply(message, "💸 **مبلغ کل وام** را وارد کنید:", reply_markup=back_menu())
    elif text == BTN_WEATHER:
        ustate_set(uid, state="weather_waiting_city")
        await safe_reply(message, "🌤 نام شهر خود را وارد کنید (مثلاً: تهران):", reply_markup=back_menu())
    elif text == BTN_AI:
        ustate_set(uid, state="ai_chat_mode")
        await safe_reply(message, "🤖 چت با هوش مصنوعی فعال شد. سوال خود را بپرسید:", reply_markup=back_menu())

async def handle_state(message, text, st, uid):
    state = st.get("state")
    
    if state == "weather_waiting_city":
        await safe_reply(message, f"⏳ در حال دریافت آب‌وهوای {text}...")
        res = await get_weather(text)
        ustate_clear(uid)
        await safe_reply(message, res, reply_markup=main_menu()); return

    if state == "ai_chat_mode":
        if not text: await safe_reply(message, "لطفاً متن بفرستید.", reply_markup=back_menu()); return
        await safe_reply(message, "⏳ در حال پردازش...")
        ans = await get_ai_response(text, uid)
        await safe_reply(message, ans, reply_markup=back_menu()); return

    try:
        if state == "gold_waiting_price":
            ustate_set(uid, state="gold_waiting_weight", price=parse_number(text, lo=1, hi=1e13))
            await safe_reply(message, "⚖️ وزن طلا را به **گرم** وارد کنید:", reply_markup=back_menu())
        elif state == "gold_waiting_weight":
            st["weight"] = parse_number(text, lo=0.001, hi=1e6)
            ustate_set(uid, state="gold_waiting_wage")
            await safe_reply(message, "🛠 درصد اجرت ساخت (بدون اجرت: 0):", reply_markup=back_menu())
        elif state == "gold_waiting_wage":
            st["wage_pct"] = parse_number(text, lo=0, hi=1000)
            ustate_set(uid, state="gold_waiting_profit")
            await safe_reply(message, "🏪 درصد سود فروشنده (بدون سود: 0):", reply_markup=back_menu())
        elif state == "gold_waiting_profit":
            st["profit_pct"] = parse_number(text, lo=0, hi=1000)
            ustate_set(uid, state="gold_waiting_tax")
            await safe_reply(message, "📄 درصد مالیات بر ارزش افزوده (معمولاً 9):", reply_markup=back_menu())
        elif state == "gold_waiting_tax":
            tax = parse_number(text, lo=0, hi=1000)
            r = calc_gold(st["price"], st["weight"], st["wage_pct"], st["profit_pct"], tax)
            msg = f"🧮 **نتیجه محاسبه طلا**\n\n💰 قیمت هر گرم: `{r['price_per_gram']:,.0f}`\n⚖️ وزن: `{r['weight']:g}` گرم\n📦 قیمت خام: `{r['raw']:,.0f}`\n🛠 اجرت: `{r['wage']:,.0f}`\n🏪 سود: `{r['profit']:,.0f}`\n📄 مالیات: `{r['tax']:,.0f}`\n\n✅ **مبلغ نهایی: `{r['total']:,.0f}` تومان**"
            ustate_clear(uid)
            await safe_reply(message, msg, reply_markup=main_menu())
        elif state == "loan_waiting_amount":
            ustate_set(uid, state="loan_waiting_interest", amount=parse_number(text, lo=1, hi=1e15))
            await safe_reply(message, "📈 نرخ سود سالانه (بدون سود: 0):", reply_markup=back_menu())
        elif state == "loan_waiting_interest":
            st["interest"] = parse_number(text, lo=0, hi=200)
            ustate_set(uid, state="loan_waiting_months")
            await safe_reply(message, "⏳ تعداد ماه‌های بازپرداخت:", reply_markup=back_menu())
        elif state == "loan_waiting_months":
            m = parse_number(text, lo=1, hi=600)
            if m != int(m): raise ValueError("not integer")
            r = calc_loan(st["amount"], st["interest"], int(m))
            msg = f"💸 **نتیجه محاسبه وام**\n\n💵 مبلغ: `{r['amount']:,.0f}`\n📈 سود: `{r['rate_pct']:g}٪`\n⏳ مدت: `{r['months']}` ماه\n\n💳 **قسط ماهانه: `{r['monthly']:,.0f}` تومان**\n💰 مجموع: `{r['total']:,.0f}`\n📉 سود کل: `{r['interest_total']:,.0f}`"
            ustate_clear(uid)
            await safe_reply(message, msg, reply_markup=main_menu())
        else:
            ustate_clear(uid)
            await safe_reply(message, "به منوی اصلی بازگشتید.", reply_markup=main_menu())
    except ValueError:
        await safe_reply(message, STATE_ERRORS.get(state, "❌ عدد معتبر وارد کنید."), reply_markup=back_menu())

async def process_private(message):
    text = (message.text or "").strip()
    uid = message.from_user.id
    text_norm = _norm_fa(text)

    if text in (BTN_BACK, BTN_START, "/start") or text_norm == "/start":
        ustate_clear(uid)
        await safe_reply(message, "به منوی اصلی بازگشتید.", reply_markup=main_menu())
        return

    if text_norm == "/test_info":
        if str(uid) != str(ADMIN_ID):
            await safe_reply(message, f"⛔ این دستور فقط برای مدیر مجاز است.\n\n(آیدی عددی بله شما: {uid})\nاین آیدی را در تنظیمات Render به عنوان ADMIN_ID وارد کنید.", reply_markup=main_menu()); return
        await safe_reply(message, "⏳ در حال ساخت اینفوگرافیک تستی...")
        img = await asyncio.to_thread(generate_infographic)
        if not img: await safe_reply(message, "❌ فونت پیدا نشد.", reply_markup=main_menu()); return
        await message.answer_photo(photo=img, caption="📊 تست اینفوگرافیک")
        return

    if text in MENU_BUTTONS:
        ustate_clear(uid)
        await menu_click(message, text, uid)
        return

    st = ustate_get(uid)
    if st and st.get("state"):
        await handle_state(message, text, st, uid)
        return

    await safe_reply(message, "لطفاً یکی از گزینه‌های منو را انتخاب کنید.", reply_markup=main_menu())

@dp.message()
async def handle_any(message: Message):
    if message.chat.type != ChatType.PRIVATE: return
    try: await process_private(message)
    except Exception:
        log.exception("handler failed")
        try: await message.answer("⚠️ خطایی رخ داد؛ لطفاً دوباره تلاش کنید.", reply_markup=main_menu())
        except Exception: pass

MORNING_MSG = "🌅 **بسم الله الرحمن الرحیم**\n\nامیدوارم روز خوب و پر از موفقیت رو آغاز کنید. ☀️\nفرصت‌ها منتظر نمی‌مانند؛ آغاز نرخ‌دهی بازار! 🚀💰"
NIGHT_MSG = "🌙 **شب بخیر**\n\nامیدوارم روز پرباری را سپری کرده باشید. شبی آرام و فردایی پر از موفقیت پیش رو داشته باشید. 🌌"

STATE_FILE = os.path.join(DATA_DIR, "scheduler_state.json")
async def _save_state(state):
    state = {k: v for k, v in state.items() if k.startswith(datetime.now(TEHRAN).strftime("%Y-%m-%d")) or k.startswith(f"img:{datetime.now(TEHRAN).strftime('%Y-%m-%d')}")}
    val = json.dumps(state, ensure_ascii=False)
    if UPSTASH_URL and UPSTASH_TOKEN:
        try:
            async with http_session() as s:
                async with s.post(f"{UPSTASH_URL}/", json=["SET", REDIS_KEY, val, "EX", 259200], headers={"Authorization": f"Bearer {UPSTASH_TOKEN}"}) as resp:
                    if resp.status == 200: return
        except Exception: pass
    try:
        with open(STATE_FILE, "w") as f: f.write(val)
    except Exception: pass

def _slot_for(now):
    d, hm = now.strftime("%Y-%m-%d"), f"{now.hour:02d}:{now.minute:02d}"
    if now.hour == 8 and now.minute < 10: return (f"{d}-morning", "morning", False)
    if now.hour == 20 and now.minute < 10: return (f"{d}-night", "night", False)
    if not (8 <= now.hour < 20): return None
    if hm == "12:45": return (f"{d}-{hm}", "bourse", False)
    if hm == "13:00": return (f"{d}-{hm}", "kala", False)
    if now.minute == 15: return (f"{d}-{hm}", "fiat", False)
    if now.minute == 30: return (f"{d}-{hm}", "gold", False)
    if now.minute == 45: return (f"{d}-{hm}", "usdt", False)
    if now.minute == 0 and now.hour != 8: return (f"{d}-{hm}", "crypto", True)
    return None

async def scheduler_loop(bot):
    state = {}
    if UPSTASH_URL and UPSTASH_TOKEN:
        try:
            async with http_session() as s:
                async with s.post(f"{UPSTASH_URL}/", json=["GET", REDIS_KEY], headers={"Authorization": f"Bearer {UPSTASH_TOKEN}"}) as resp:
                    if resp.status == 200:
                        val = (await resp.json()).get("result")
                        if val: state = json.loads(val)
        except Exception: pass
    if not state:
        try:
            with open(STATE_FILE) as f: state = json.load(f)
        except Exception: pass

    while True:
        try:
            slot = _slot_for(datetime.now(TEHRAN))
            if slot:
                key, kind, with_info = slot
                if not state.get(key):
                    msg = STORE.get_message(kind)
                    if not msg and kind in ("morning", "night"): msg = MORNING_MSG if kind=="morning" else NIGHT_MSG
                    if msg:
                        await bot.send_message(chat_id=CHANNEL_ID, text=msg + CHANNEL_FOOTER)
                        state[key] = True; await _save_state(state)
                if with_info and state.get(key) and not state.get(f"img:{key}"):
                    img = await asyncio.to_thread(generate_infographic)
                    if img:
                        await bot.send_photo(chat_id=CHANNEL_ID, photo=img, caption="📊 خلاصه بازار")
                        state[f"img:{key}"] = True; await _save_state(state)
        except Exception as e: log.error(f"Scheduler: {e}")
        await asyncio.sleep(15)

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")

async def main():
    bot = Bot(token=BALE_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN))
    asyncio.create_task(cache_loop())
    asyncio.create_task(scheduler_loop(bot))
    await dp.start_polling(bot)

if __name__ == "__main__":
    threading.Thread(target=lambda: ThreadingHTTPServer(("0.0.0.0", PORT), HealthHandler).serve_forever(), daemon=True).start()
    asyncio.run(main())
