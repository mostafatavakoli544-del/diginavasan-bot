# -*- coding: utf-8 -*-
"""
Digi Navasan — Bale Bot (Final v3)
==================================
تغییرات نسبت به v2:
- اصلاح endpoint رمزینکس (ساختار جدید data[])
- برگشت والکس به v1/markets (با آخرین بررسی)
- اینفوگرافیک با ۶ سکه (۱۸، ۲۴، امامی، بهار، نیم، ربع)
- هماهنگی معیار قیمت (lastPrice) در همه صرافی‌ها
"""

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

try:
    from bidi.algorithm import get_display
except ImportError:
    from bidi import get_display

from baleio import Bot, Dispatcher, md
from baleio.client.default import DefaultBotProperties
from baleio.enums import ChatType, ParseMode
from baleio.filters import CommandStart
from baleio.types import Message, ReplyKeyboardMarkup, KeyboardButton

logging.basicConfig(
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    level=logging.INFO,
    stream=sys.stdout,
)
log = logging.getLogger("diginavasan.bale")

# ---------------------------------------------------------------------------
# تنظیمات
# ---------------------------------------------------------------------------
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

TEHRAN = pytz.timezone("Asia/Tehran")

CHANNEL_FOOTER = (
    "\n\n────────────\n"
    "🤖 [ورود به ربات دیجی نوسان](https://ble.ir/diginavasan_bot) | "
    "📢 [کانال دیجی نوسان](https://ble.ir/diginavasan)"
)

FONT_PATHS = [
    os.path.join(BASE_DIR, "fonts", "Vazirmatn-Bold.ttf"),
    os.path.join(BASE_DIR, "font.ttf"),
]

# ---------------------------------------------------------------------------
# PriceRecord
# ---------------------------------------------------------------------------
class PriceRecord:
    __slots__ = ("value", "source", "fetched_at", "status", "error", "metric")

    def __init__(self, value=None, source="", fetched_at=None, status="ok",
                 error=None, metric="lastPrice"):
        self.value: Optional[float] = value
        self.source: str = source
        self.fetched_at: Optional[datetime] = fetched_at
        self.status: str = status
        self.error: Optional[str] = error
        self.metric: str = metric

    def age_sec(self) -> Optional[float]:
        if self.fetched_at is None:
            return None
        return (datetime.now(TEHRAN) - self.fetched_at).total_seconds()


class PriceStore:
    def __init__(self):
        self._lock = threading.Lock()
        self.fiat = {}; self.gold = {}; self.crypto = {}; self.usdt = {}; self.aux = {}
        self._messages = {}
        self._last_cycle_ok = None

    def set_bucket(self, name, records):
        with self._lock:
            setattr(self, name, dict(records))

    def get_bucket(self, name):
        with self._lock:
            return dict(getattr(self, name))

    def set_message(self, key, text):
        with self._lock:
            self._messages[key] = text

    def get_message(self, key):
        with self._lock:
            return self._messages.get(key)

    def touch_cycle(self):
        with self._lock:
            self._last_cycle_ok = datetime.now(TEHRAN)

    @property
    def last_cycle(self):
        with self._lock:
            return self._last_cycle_ok


STORE = PriceStore()

# ---------------------------------------------------------------------------
# HTTP client
# ---------------------------------------------------------------------------
import aiohttp

_HTTP_TIMEOUT = aiohttp.ClientTimeout(total=20, connect=8)
_HTTP_HEADERS = {"User-Agent": "TraderBot/DigiNavasan-3.0"}

@asynccontextmanager
async def http_session():
    connector = aiohttp.TCPConnector(limit=20, ttl_dns_cache=300, ssl=True)
    async with aiohttp.ClientSession(
        connector=connector, timeout=_HTTP_TIMEOUT, headers=_HTTP_HEADERS
    ) as session:
        yield session


async def _get_json(session, url, params=None, retries=2):
    last_err = None
    for attempt in range(retries + 1):
        try:
            async with session.get(url, params=params) as resp:
                if resp.status != 200:
                    last_err = f"HTTP {resp.status}"
                    log.warning("GET %s → %s (attempt %d)", url, last_err, attempt + 1)
                    if resp.status in (401, 403, 404):
                        return None
                else:
                    try:
                        return await resp.json(content_type=None)
                    except Exception as e:
                        last_err = f"JSON: {type(e).__name__}"
                        log.warning("GET %s → invalid JSON: %s", url, e)
        except asyncio.TimeoutError:
            last_err = "timeout"
            log.warning("GET %s → timeout (attempt %d)", url, attempt + 1)
        except aiohttp.ClientError as e:
            last_err = f"{type(e).__name__}: {e}"
            log.warning("GET %s → %s (attempt %d)", url, last_err, attempt + 1)
        if attempt < retries:
            await asyncio.sleep(0.6 * (2 ** attempt))
    log.error("GET %s → giving up after %d attempts: %s", url, retries + 1, last_err)
    return None


def _is_positive_number(x):
    try:
        v = float(x)
        return math.isfinite(v) and v > 0
    except (TypeError, ValueError):
        return False


def _to_toman(value, source_publishes_rial):
    return value / 10.0 if source_publishes_rial else value


# ===========================================================================
# USDT — endpointها و ساختارهای بررسی‌شده
# ===========================================================================
async def fetch_usdt(session):
    now = datetime.now(TEHRAN)
    out = {}
    sem = asyncio.Semaphore(5)

    async def nobitex():
        async with sem:
            data = await _get_json(
                session, "https://apiv2.nobitex.ir/market/stats",
                params={"srcCurrency": "usdt", "dstCurrency": "rls"},
            )
            v = (data or {}).get("stats", {}).get("usdt-rls", {}).get("latest")
            if _is_positive_number(v):
                out["نوبیتکس"] = PriceRecord(_to_toman(float(v), True), "Nobitex", now, "ok", metric="latest(rial)")
            else:
                out["نوبیتکس"] = PriceRecord(status="error", source="Nobitex", error="missing usdt-rls.latest", fetched_at=now)

    async def wallex():
        async with sem:
            data = await _get_json(session, "https://api.wallex.ir/v1/markets")
            stats = ((data or {}).get("result") or {}).get("symbols", {}).get("USDTTMN", {}).get("stats", {})
            v = stats.get("lastPrice")
            if _is_positive_number(v):
                out["والکس"] = PriceRecord(float(v), "Wallex", now, "ok", metric="lastPrice(toman)")
            else:
                out["والکس"] = PriceRecord(status="error", source="Wallex", error="missing USDTTMN.lastPrice", fetched_at=now)

    async def ramzinex():
        async with sem:
            data = await _get_json(session, "https://publicapi.ramzinex.com/exchange/api/v1.0/exchange/pairs")
            for item in (data or {}).get("data", []) or []:
                if not isinstance(item, dict):
                    continue
                name_en = ((item.get("name") or {}).get("en") or "").lower()
                quote = ((item.get("quote_currency_symbol") or {}).get("en") or "").lower()
                if quote != "irr":
                    continue
                if "tether" not in name_en and "usdt" not in name_en:
                    continue
                # استفاده از close (آخرین معامله) — هم‌معیار با بقیه صرافی‌ها
                close = (((item.get("financial") or {}).get("last24h") or {}).get("close"))
                if _is_positive_number(close):
                    out["رمزینکس"] = PriceRecord(_to_toman(float(close), True), "Ramzinex", now, "ok", metric="close(rial)")
                    return
                sell = item.get("sell")
                if _is_positive_number(sell):
                    out["رمزینکس"] = PriceRecord(_to_toman(float(sell), True), "Ramzinex", now, "ok", metric="sell(rial)")
                    return
            out["رمزینکس"] = PriceRecord(status="error", source="Ramzinex", error="tether/rial pair not found", fetched_at=now)

    async def bitpin():
        async with sem:
            data = await _get_json(session, "https://api.bitpin.ir/api/v1/mkt/tickers/")
            for item in data or []:
                if isinstance(item, dict) and item.get("symbol") == "USDT_IRT":
                    v = item.get("price")
                    if _is_positive_number(v):
                        out["بیت‌پین"] = PriceRecord(float(v), "Bitpin", now, "ok", metric="price(toman)")
                        return
            out["بیت‌پین"] = PriceRecord(status="error", source="Bitpin", error="USDT_IRT not found", fetched_at=now)

    async def tabdeal():
        async with sem:
            data = await _get_json(session, "https://api.tabdeal.org/api/v1/market/statistics")
            node = (data or {}).get("USDTIRT") or {}
            v = node.get("lastPrice") if isinstance(node, dict) else None
            if _is_positive_number(v):
                out["تبدیل"] = PriceRecord(float(v), "Tabdeal", now, "ok", metric="lastPrice(toman)")
            else:
                out["تبدیل"] = PriceRecord(status="error", source="Tabdeal", error="missing USDTIRT.lastPrice", fetched_at=now)

    await asyncio.gather(nobitex(), wallex(), ramzinex(), bitpin(), tabdeal(), return_exceptions=True)

    # حفظ داده قبلی در صورت خرابی
    old = STORE.get_bucket("usdt")
    for k, rec in list(out.items()):
        if rec.status != "ok" and k in old and old[k].value is not None:
            prev = old[k]
            out[k] = PriceRecord(prev.value, prev.source, prev.fetched_at, "stale", rec.error, prev.metric)
    return out


def build_usdt_message(records):
    valid = [(k, r) for k, r in records.items() if r.value and r.status == "ok"]
    stale = [(k, r) for k, r in records.items() if r.value and r.status == "stale"]
    if not valid and not stale:
        return None

    ts = datetime.now(TEHRAN).strftime("%H:%M")
    lines = [f"💵 **مقایسه قیمت تتر (USDT)**\n⏰ `{ts}`\n"]
    priced = sorted(valid + stale, key=lambda x: x[1].value)
    for i, (name, r) in enumerate(priced):
        tag = "🥇 (ارزان‌ترین)" if i == 0 and r.status == "ok" else ""
        if r.status == "stale":
            tag = f"⚠️ داده‌ی {int(r.age_sec() or 0)//60} دقیقه پیش"
        lines.append(f"{'🥇' if i==0 and r.status=='ok' else '🔸'} **{name}**: `{r.value:,.0f}` تومان {tag}")
    if len(valid) >= 2:
        diff = max(r.value for _, r in valid) - min(r.value for _, r in valid)
        lines.append(f"\n📊 اختلاف بین معتبرترین‌ها: `{diff:,.0f}` تومان")
    failed = [k for k, r in records.items() if r.status == "error"]
    if failed:
        lines.append(f"\n⚠️ ناموفق: {', '.join(failed)}")
    return "\n".join(lines)


# ===========================================================================
# Crypto (7 coins)
# ===========================================================================
CRYPTO_META = {
    "BTC": ("btc", "بیت‌کوین"),
    "ETH": ("eth", "اتریوم"),
    "BNB": ("bnb", "بایننس کوین"),
    "TRX": ("trx", "ترون"),
    "SOL": ("sol", "سولانا"),
    "DOGE": ("doge", "دوج کوین"),
    "TON": ("ton", "تون کوین"),
}


async def fetch_crypto(session):
    now = datetime.now(TEHRAN)
    out = {}
    sem = asyncio.Semaphore(7)

    async def one(sym, coin_id):
        async with sem:
            data = await _get_json(session, "https://apiv2.nobitex.ir/market/stats",
                                   params={"srcCurrency": coin_id, "dstCurrency": "rls"})
            node = ((data or {}).get("stats") or {}).get(f"{coin_id}-rls", {})
            v = node.get("latest")
            if _is_positive_number(v):
                out[sym] = PriceRecord(_to_toman(float(v), True), "Nobitex", now, "ok", metric="latest(rial)")
            else:
                out[sym] = PriceRecord(status="error", source="Nobitex", error=f"{coin_id}-rls.latest missing", fetched_at=now)

    await asyncio.gather(*(one(s, c) for s, (c, _) in CRYPTO_META.items()), return_exceptions=True)
    old = STORE.get_bucket("crypto")
    for k, rec in list(out.items()):
        if rec.status != "ok" and k in old and old[k].value is not None:
            prev = old[k]
            out[k] = PriceRecord(prev.value, prev.source, prev.fetched_at, "stale", rec.error, prev.metric)
    return out


def build_crypto_message(records):
    good = {k: r for k, r in records.items() if r.value is not None and r.status in ("ok", "stale")}
    if not good:
        return None
    ts = datetime.now(TEHRAN).strftime("%H:%M")
    lines = [f"🪙 **قیمت لحظه‌ای ارز دیجیتال**\n⏰ `{ts}`\n"]
    for sym, (_, fa) in CRYPTO_META.items():
        r = good.get(sym)
        if not r:
            lines.append(f"🔹 **{fa} ({sym})**: ناموجود")
            continue
        flag = "" if r.status == "ok" else " ⚠️"
        lines.append(f"🟢 **{fa} ({sym})**: `{r.value:,.0f}` تومان{flag}")
    return "\n".join(lines)


# ===========================================================================
# Fiat & Gold (منبع jahankhahan — همه تومانی)
# ===========================================================================
_NERKH_URL = "https://nerkh.jahankhahan.shop/data/live.json"

FIAT_KEYS = [
    ("dollar", "💵 دلار", ["dollar", "usd"]),
    ("euro",   "💶 یورو", ["euro", "eur"]),
    ("pound",  "💷 پوند", ["pound", "gbp"]),
    ("aed",    "🇦🇪 درهم", ["aed"]),
    ("lira",   "🇹🇷 لیر", ["lira", "try"]),
    ("yuan",   "🇨🇳 یوان", ["yuan", "cny"]),
    ("cad",    "🇨🇦 کانادا", ["cad"]),
    ("aud",    "🇦🇺 استرالیا", ["aud"]),
    ("chf",    "🇨🇭 فرانک سوئیس", ["chf"]),
    ("sar",    "🇸🇦 ریال عربستان", ["sar"]),
    ("qar",    "🇶🇦 ریال قطر", ["qar"]),
    ("kwd",    "🇰🇼 دینار کویت", ["kwd"]),
    ("iqd",    "🇮🇶 دینار عراق", ["iqd"]),
    ("afn",    "🇦🇫 افغانی", ["afn"]),
    ("rub",    "🇷🇺 روبل", ["rub"]),
]

GOLD_KEYS = [
    ("gold_18", "🥇 طلا ۱۸ عیار", ["gold"]),
    ("gold_24", "🥇 طلا ۲۴ عیار", ["gold24"]),
    ("coin",    "🪙 سکه امامی", ["coin"]),
    ("bahar",   "🪙 سکه بهار آزادی", ["bahar"]),
    ("half",    "🪙 نیم سکه", ["half"]),
    ("quarter", "🪙 ربع سکه", ["quarter"]),
]


async def fetch_nerkh(session):
    data = await _get_json(session, _NERKH_URL)
    if not isinstance(data, dict):
        return None
    rates = data.get("rates")
    return rates if isinstance(rates, dict) else None


def _pick_rate(rates, aliases):
    if not rates:
        return None
    for a in aliases:
        v = rates.get(a)
        if _is_positive_number(v):
            return float(v)
    return None


def _build_bucket(keys, rates, bucket_name):
    now = datetime.now(TEHRAN)
    out = {}
    old = STORE.get_bucket(bucket_name)
    for key, _, aliases in keys:
        v = _pick_rate(rates, aliases)
        if v:
            out[key] = PriceRecord(v, "jahankhahan", now, "ok")
        elif key in old and old[key].value:
            p = old[key]
            out[key] = PriceRecord(p.value, p.source, p.fetched_at, "stale", "rate missing in latest fetch")
        else:
            out[key] = PriceRecord(status="error", source="jahankhahan", error="rate missing", fetched_at=now)
    return out


def _fmt_toman(v):
    return f"{v:,.0f}" if v is not None else "ناموجود"


def build_fiat_message(records):
    if not any(r.value for r in records.values()):
        return None
    ts = datetime.now(TEHRAN).strftime("%H:%M")
    lines = [f"💱 **قیمت لحظه‌ای ارزها**\n⏰ `{ts}`\n"]
    for key, label, _ in FIAT_KEYS:
        r = records.get(key)
        if r and r.value is not None:
            flag = "" if r.status == "ok" else " ⚠️"
            lines.append(f"{label} = `{_fmt_toman(r.value)}` تومان{flag}")
        else:
            lines.append(f"{label} = ناموجود")
    return "\n".join(lines)


def build_gold_message(records):
    if not any(r.value for r in records.values()):
        return None
    ts = datetime.now(TEHRAN).strftime("%H:%M")
    lines = [f"🌕 **قیمت لحظه‌ای طلا و سکه**\n⏰ `{ts}`\n"]
    for key, label, _ in GOLD_KEYS:
        r = records.get(key)
        if r and r.value is not None:
            flag = "" if r.status == "ok" else " ⚠️"
            lines.append(f"{label} = `{_fmt_toman(r.value)}` تومان{flag}")
        else:
            lines.append(f"{label} = ناموجود")
    return "\n".join(lines)


async def fetch_aux(session):
    """نفت و نقره — از Nobitex (توکن‌های USOON/SLVON به USDT)"""
    now = datetime.now(TEHRAN)
    out = {}
    async def one(key, sym):
        data = await _get_json(session, "https://apiv2.nobitex.ir/market/stats",
                               params={"srcCurrency": sym, "dstCurrency": "usdt"})
        v = ((data or {}).get("stats") or {}).get(f"{sym}-usdt", {}).get("latest")
        if _is_positive_number(v):
            out[key] = PriceRecord(float(v), "Nobitex", now, "ok", metric="latest")
        else:
            out[key] = PriceRecord(status="error", source="Nobitex", error=f"{sym}-usdt missing", fetched_at=now)
    await asyncio.gather(one("oil", "usoon"), one("silver", "slvon"), return_exceptions=True)
    old = STORE.get_bucket("aux")
    for k, rec in list(out.items()):
        if rec.status != "ok" and k in old and old[k].value is not None:
            p = old[k]
            out[k] = PriceRecord(p.value, p.source, p.fetched_at, "stale", rec.error, p.metric)
    return out


# ===========================================================================
# Cache Cycle
# ===========================================================================
CACHE_INTERVAL = 30

async def refresh_cycle():
    async with http_session() as session:
        rates, usdt, crypto, aux = await asyncio.gather(
            fetch_nerkh(session), fetch_usdt(session), fetch_crypto(session), fetch_aux(session),
            return_exceptions=True,
        )
    if isinstance(rates, Exception):
        log.exception("rates fetch failed", exc_info=rates); rates = None
    if isinstance(usdt, Exception):
        log.exception("usdt fetch failed", exc_info=usdt); usdt = {}
    if isinstance(crypto, Exception):
        log.exception("crypto fetch failed", exc_info=crypto); crypto = {}
    if isinstance(aux, Exception):
        log.exception("aux fetch failed", exc_info=aux); aux = {}

    fiat_rec = _build_bucket(FIAT_KEYS, rates, "fiat")
    gold_rec = _build_bucket(GOLD_KEYS, rates, "gold")

    STORE.set_bucket("fiat", fiat_rec)
    STORE.set_bucket("gold", gold_rec)
    STORE.set_bucket("usdt", usdt)
    STORE.set_bucket("crypto", crypto)
    STORE.set_bucket("aux", aux)

    for key, msg in [("fiat", build_fiat_message(fiat_rec)),
                     ("gold", build_gold_message(gold_rec)),
                     ("crypto", build_crypto_message(crypto)),
                     ("usdt", build_usdt_message(usdt))]:
        if msg:
            STORE.set_message(key, msg)
    STORE.touch_cycle()
    log.info("Cache cycle done | fiat=%d gold=%d usdt_ok=%d crypto_ok=%d",
             sum(1 for r in fiat_rec.values() if r.status == "ok"),
             sum(1 for r in gold_rec.values() if r.status == "ok"),
             sum(1 for r in usdt.values() if r.status == "ok"),
             sum(1 for r in crypto.values() if r.status == "ok"))


async def cache_loop():
    while True:
        try:
            await refresh_cycle()
        except Exception:
            log.exception("Cache refresh cycle crashed (continuing)")
        await asyncio.sleep(CACHE_INTERVAL)


# ===========================================================================
# Fonts
# ===========================================================================
HAS_RAQM = features.check("raqm")
log.info("Pillow raqm available: %s", HAS_RAQM)


def _fa(text):
    if not text:
        return ""
    if HAS_RAQM:
        return str(text)
    return get_display(arabic_reshaper.reshape(str(text)))


def _font_supports_persian(path):
    try:
        if not os.path.exists(path) or os.path.getsize(path) < 20000:
            return False
        f = ImageFont.truetype(path, 40)
        img = Image.new("L", (90, 90), 0)
        ImageDraw.Draw(img).text((10, 10), "ی", font=f, fill=255)
        sig_y = img.tobytes()
        img2 = Image.new("L", (90, 90), 0)
        ImageDraw.Draw(img2).text((10, 10), "\uffff", font=f, fill=255)
        missing = img2.tobytes()
        blank = Image.new("L", (90, 90), 0).tobytes()
        return sig_y not in (missing, blank)
    except Exception:
        return False


_FONT_PATH_CACHE = None


def resolve_font_path():
    global _FONT_PATH_CACHE
    if _FONT_PATH_CACHE and _font_supports_persian(_FONT_PATH_CACHE):
        return _FONT_PATH_CACHE
    for p in FONT_PATHS:
        if _font_supports_persian(p):
            _FONT_PATH_CACHE = p
            log.info("Font loaded: %s", p)
            return p
    log.error("No usable Persian font found. Place Vazirmatn-Bold.ttf in ./fonts/")
    return None


def load_fonts():
    path = resolve_font_path()
    if path:
        try:
            return tuple(ImageFont.truetype(path, s) for s in (56, 34, 28, 22))
        except Exception as e:
            log.exception("Font load failed: %s", e)
    d = ImageFont.load_default()
    return d, d, d, d


def _rtl(draw, xy, text, font, fill):
    bbox = draw.textbbox((0, 0), text, font=font)
    w = bbox[2] - bbox[0]
    draw.text((xy[0] - w, xy[1]), text, font=font, fill=fill)


def _fmt_num(v):
    try:
        return f"{int(float(v)):,}"
    except Exception:
        return "—"


def _fmt_usd(v):
    try:
        return f"{float(v):,.2f}"
    except Exception:
        return "—"


def _gradient_bg(width, height):
    img = Image.new("RGB", (width, height), (10, 18, 32))
    d = ImageDraw.Draw(img)
    for y in range(height):
        r = int(10 + (22 - 10) * y / height)
        g = int(18 + (32 - 18) * y / height)
        b = int(32 + (56 - 32) * y / height)
        d.line([(0, y), (width, y)], fill=(r, g, b))
    return img


# ===========================================================================
# Infographic — 6 coins in gold card + 5 fiat + 7 crypto
# ===========================================================================
def generate_infographic():
    font_path = resolve_font_path()
    if not font_path:
        return None
    f_title, f_head, f_body, f_small = load_fonts()

    W, H = 1080, 1920
    img = _gradient_bg(W, H)
    d = ImageDraw.Draw(img)

    GOLD = (255, 210, 80)
    WHITE = (255, 255, 255)
    GRAY = (180, 190, 210)
    CARD = (20, 32, 54)
    OUT = (70, 95, 140)
    LINE = (45, 65, 100)

    # Header
    d.rounded_rectangle((50, 40, W - 50, 190), radius=24, fill=(24, 38, 64),
                        outline=(218, 165, 32), width=3)
    _rtl(d, (W - 80, 48), _fa("دیجی نوسان | خلاصه بازار"), f_title, GOLD)
    cycle = STORE.last_cycle
    ts_txt = cycle.strftime("%Y/%m/%d - %H:%M") if cycle else "در حال دریافت..."
    _rtl(d, (W - 80, 135), _fa(ts_txt), f_small, GRAY)

    def card(y, h, title):
        d.rounded_rectangle((50, y, W - 50, y + h), radius=22, fill=CARD, outline=OUT, width=2)
        _rtl(d, (W - 80, y + 20), _fa(title), f_head, GOLD)
        d.line((80, y + 78, W - 80, y + 78), fill=LINE, width=2)

    def row(y, label, value, font=f_body):
        _rtl(d, (W - 80, y), _fa(label), font, GRAY)
        _rtl(d, (W - 80, y + 32), _fa(value), font, WHITE)

    fiat = STORE.get_bucket("fiat")
    gold = STORE.get_bucket("gold")
    crypto = STORE.get_bucket("crypto")
    aux = STORE.get_bucket("aux")
    usdt = STORE.get_bucket("usdt")

    # ===== Card 1: Gold (6 coins) y=220 to y=820 (600px) =====
    card(220, 600, "طلا و سکه")
    gold_items = [
        ("gold_18", "طلای ۱۸ عیار:"),
        ("gold_24", "طلای ۲۴ عیار:"),
        ("coin",    "سکه امامی:"),
        ("bahar",   "سکه بهار آزادی:"),
        ("half",    "نیم سکه:"),
        ("quarter", "ربع سکه:"),
    ]
    y = 320
    for key, label in gold_items:
        r = gold.get(key)
        row(y, label, f"{_fmt_num(r.value if r else 0)} تومان")
        y += 82

    # ===== Card 2: Fiat (5 items) y=840 to y=1310 (470px) =====
    card(840, 470, "ارزهای خارجی")
    dolar = fiat.get("dollar"); euro = fiat.get("euro")
    usdt_min = min((r.value for r in usdt.values() if r.value and r.status == "ok"), default=None)
    oil = aux.get("oil"); silver = aux.get("silver")
    fiat_items = [
        ("دلار:", f"{_fmt_num(dolar.value if dolar else 0)} تومان"),
        ("یورو:", f"{_fmt_num(euro.value if euro else 0)} تومان"),
        ("تتر (بهترین):", f"{_fmt_num(usdt_min)} تومان"),
        ("نفت برنت:", f"{_fmt_usd(oil.value if oil else None)} دلار"),
        ("نقره:", f"{_fmt_usd(silver.value if silver else None)} دلار"),
    ]
    y = 940
    for label, value in fiat_items:
        row(y, label, value)
        y += 72

    # ===== Card 3: Crypto (7 items) y=1330 to y=1830 (500px) =====
    card(1330, 500, "رمزارزهای برتر")
    y = 1430
    for sym, (_, fa) in CRYPTO_META.items():
        r = crypto.get(sym)
        val = r.value if r else None
        _rtl(d, (W - 80, y), _fa(f"{fa} ({sym}):"), f_small, GRAY)
        _rtl(d, (W - 80, y + 26), _fa(f"{_fmt_num(val)} تومان"), f_small, WHITE)
        y += 56

    # Footer
    d.line((80, H - 80, W - 80, H - 80), fill=OUT, width=1)
    _rtl(d, (W - 80, H - 60), _fa("قیمت لحظه‌ای | ماشین‌حساب | تحلیل هوشمند"), f_small, (160, 175, 200))

    bio = BytesIO()
    img.save(bio, format="PNG", optimize=True)
    return bio.getvalue()


# ===========================================================================
# Weather
# ===========================================================================
_DIGITS_MAP = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")


def _norm_fa(t):
    t = (t or "").translate(_DIGITS_MAP)
    t = t.replace("ي", "ی").replace("ك", "ک").replace("\u200c", " ").replace("ـ", "")
    return " ".join(t.split())


CITY_EN = {
    "تهران": "Tehran", "مشهد": "Mashhad", "اصفهان": "Isfahan", "کرج": "Karaj",
    "شیراز": "Shiraz", "تبریز": "Tabriz", "قم": "Qom", "اهواز": "Ahvaz",
    "کرمانشاه": "Kermanshah", "ارومیه": "Urmia", "رشت": "Rasht", "زاهدان": "Zahedan",
    "کرمان": "Kerman", "همدان": "Hamadan", "یزد": "Yazd", "اردبیل": "Ardabil",
    "بندرعباس": "Bandar Abbas", "اراک": "Arak", "زنجان": "Zanjan", "سنندج": "Sanandaj",
    "قزوین": "Qazvin", "خرم آباد": "Khorramabad", "گرگان": "Gorgan", "ساری": "Sari",
    "بجنورد": "Bojnord", "بوشهر": "Bushehr", "بیرجند": "Birjand", "ایلام": "Ilam",
    "شهرکرد": "Shahr-e Kord", "سمنان": "Semnan", "یاسوج": "Yasuj", "کیش": "Kish",
    "قشم": "Qeshm", "آبادان": "Abadan", "دزفول": "Dezful",
}

WMO_FA = {
    0: "آسمان صاف ☀️", 1: "عمدتاً صاف 🌤", 2: "نیمه‌ابری ⛅", 3: "ابری ☁️",
    45: "مه 🌫", 48: "مه یخ‌زده 🌫", 51: "نم‌نم باران خفیف 🌦", 53: "نم‌نم باران 🌦",
    55: "نم‌نم باران شدید 🌧", 61: "باران خفیف 🌧", 63: "باران 🌧", 65: "باران شدید 🌧",
    71: "برف خفیف 🌨", 73: "برف 🌨", 75: "برف شدید ❄️", 80: "رگبار خفیف 🌦",
    81: "رگبار 🌧", 82: "رگبار شدید ⛈", 95: "رعد و برق ⛈", 96: "رعد و برق و تگرگ ⛈",
    99: "رعد و برق و تگرگ شدید ⛈",
}


async def get_weather(city):
    city = _norm_fa(city)
    if not city:
        return "❌ نام شهر را وارد کنید."
    if len(city) > 60:
        return "❌ نام شهر خیلی طولانی است."

    query = CITY_EN.get(city, city)
    try:
        async with http_session() as s:
            geo = await _get_json(s, "https://geocoding-api.open-meteo.com/v1/search",
                                  params={"name": query, "count": 10, "language": "fa", "format": "json"})
            results = (geo or {}).get("results") or []
            if results:
                place = next((r for r in results if r.get("country_code") == "IR"), results[0])
                fc = await _get_json(s, "https://api.open-meteo.com/v1/forecast", params={
                    "latitude": place["latitude"], "longitude": place["longitude"],
                    "current": "temperature_2m,relative_humidity_2m,apparent_temperature,weather_code,wind_speed_10m",
                    "daily": "temperature_2m_max,temperature_2m_min",
                    "timezone": "auto", "forecast_days": 1,
                })
                if fc and (fc.get("current") or {}).get("temperature_2m") is not None:
                    cur = fc["current"]; daily = fc.get("daily") or {}
                    tmax = (daily.get("temperature_2m_max") or [None])[0]
                    tmin = (daily.get("temperature_2m_min") or [None])[0]
                    name = place.get("name") or city
                    country = place.get("country")
                    title = f"{name}، {country}" if country else name
                    now = datetime.now(TEHRAN).strftime("%H:%M")
                    return (
                        f"🌤 **وضعیت آب‌وهوای {title}**\n\n"
                        f"🌡 دما: {cur['temperature_2m']:.0f} درجه (احساس {cur.get('apparent_temperature', 0):.0f})\n"
                        f"☁️ {WMO_FA.get(int(cur.get('weather_code', -1)), 'نامشخص')}\n"
                        f"🔼 بیشینه: {tmax if tmax is not None else '؟'} | 🔽 کمینه: {tmin if tmin is not None else '؟'}\n"
                        f"💧 رطوبت: {cur.get('relative_humidity_2m', '؟')}٪\n"
                        f"💨 باد: {cur.get('wind_speed_10m', '؟')} km/h\n"
                        f"⏰ `{now}`"
                    )
    except Exception:
        log.exception("open-meteo weather failed for %s", city)

    try:
        async with http_session() as s:
            data = await _get_json(s, f"https://wttr.in/{quote(city)}", params={"format": "j1"})
            cur = ((data or {}).get("current_condition") or [{}])[0]
            if cur.get("temp_C"):
                desc = (cur.get("lang_fa") or [{}])[0].get("value") or \
                       (cur.get("weatherDesc") or [{}])[0].get("value", "—")
                return (
                    f"🌤 **آب‌وهوای {city}** (wttr)\n\n"
                    f"🌡 دما: {cur['temp_C']}°C\n☁️ {desc}\n"
                    f"💧 رطوبت: {cur.get('humidity', '؟')}٪"
                )
    except Exception:
        log.exception("wttr weather failed for %s", city)
    return "❌ شهر پیدا نشد یا سرویس آب‌وهوا در دسترس نیست."


# ===========================================================================
# Groq AI
# ===========================================================================
_AI_URL = "https://api.groq.com/openai/v1/chat/completions"
_AI_MODEL = "openai/gpt-oss-120b"
_AI_MAX_INPUT = 2000
_AI_RATE_WINDOW = 60
_AI_RATE_MAX = 6
_ai_hits = {}


def _ai_allowed(uid):
    now = time.time()
    hits = [t for t in _ai_hits.get(uid, []) if now - t < _AI_RATE_WINDOW]
    _ai_hits[uid] = hits
    if len(hits) >= _AI_RATE_MAX:
        return False
    hits.append(now)
    return True


async def get_ai_response(prompt, uid):
    if not GROQ_API_KEY:
        return "⚠️ کلید هوش مصنوعی روی سرور تنظیم نشده است."
    if len(prompt) > _AI_MAX_INPUT:
        return f"❌ متن شما بیش از حد طولانی است (حداکثر {_AI_MAX_INPUT} کاراکتر)."
    if not _ai_allowed(uid):
        return "⏳ تعداد درخواست‌های شما زیاد است؛ یک دقیقه بعد دوباره تلاش کنید."

    payload = {
        "model": _AI_MODEL,
        "messages": [
            {"role": "system", "content": "You are a helpful assistant. Answer in Persian, concisely."},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.7, "max_tokens": 1024,
    }
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
    try:
        async with http_session() as s:
            async with s.post(_AI_URL, json=payload, headers=headers) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    txt = (data["choices"][0]["message"].get("content") or "").strip()
                    return txt or "پاسخی دریافت نشد."
                if resp.status == 429:
                    return "⏳ سهمیه هوش مصنوعی پر شده؛ بعداً تلاش کنید."
                if resp.status in (401, 403):
                    return "⚠️ کلید هوش مصنوعی نامعتبر است."
                log.warning("Groq HTTP %s", resp.status)
                return "خطا در ارتباط با هوش مصنوعی."
    except Exception:
        log.exception("Groq request failed")
        return "خطای اتصال به هوش مصنوعی."


# ===========================================================================
# Scheduler (Upstash + fallback local)
# ===========================================================================
STATE_FILE = os.path.join(DATA_DIR, "scheduler_state.json")
_STATE_LOCK = asyncio.Lock()


async def _load_state():
    state = {}
    if UPSTASH_URL and UPSTASH_TOKEN:
        try:
            async with http_session() as s:
                headers = {"Authorization": f"Bearer {UPSTASH_TOKEN}"}
                async with s.post(f"{UPSTASH_URL}/", json=["GET", REDIS_KEY], headers=headers) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        val = data.get("result")
                        if val:
                            parsed = json.loads(val)
                            if isinstance(parsed, dict):
                                state = parsed
                                log.info("State loaded from Upstash")
        except Exception:
            log.exception("Upstash load failed")
    if not state:
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    state = data
                    log.info("State loaded from local file")
        except FileNotFoundError:
            pass
        except Exception:
            log.exception("Local state load failed")
    return state


async def _save_state(state):
    today = datetime.now(TEHRAN).strftime("%Y-%m-%d")
    for k in list(state.keys()):
        if not k.startswith(today) and not k.startswith(f"img:{today}"):
            state.pop(k, None)
    val_str = json.dumps(state, ensure_ascii=False)
    if UPSTASH_URL and UPSTASH_TOKEN:
        try:
            async with http_session() as s:
                headers = {"Authorization": f"Bearer {UPSTASH_TOKEN}"}
                async with s.post(f"{UPSTASH_URL}/",
                                  json=["SET", REDIS_KEY, val_str, "EX", 259200],
                                  headers=headers) as resp:
                    if resp.status == 200:
                        log.info("State saved to Upstash")
                        return
                    log.error("Upstash save failed: HTTP %s", resp.status)
        except Exception:
            log.exception("Upstash save failed")
    try:
        os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
        tmp = STATE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(val_str); f.flush(); os.fsync(f.fileno())
        os.replace(tmp, STATE_FILE)
    except Exception:
        log.exception("Local state save failed")


def _slot_for(now):
    d = now.strftime("%Y-%m-%d")
    hm = f"{now.hour:02d}:{now.minute:02d}"
    if now.hour == 8 and now.minute < 10:  return (f"{d}-morning", "morning", False)
    if now.hour == 20 and now.minute < 10: return (f"{d}-night", "night", False)
    if not (8 <= now.hour < 20):           return None
    if hm == "12:45":                      return (f"{d}-{hm}", "bourse", False)
    if hm == "13:00":                      return (f"{d}-{hm}", "kala", False)
    if now.minute == 15:                   return (f"{d}-{hm}", "fiat", False)
    if now.minute == 30:                   return (f"{d}-{hm}", "gold", False)
    if now.minute == 45:                   return (f"{d}-{hm}", "usdt", False)
    if now.minute == 0 and now.hour != 8:  return (f"{d}-{hm}", "crypto", True)
    return None


MORNING_MSG = ("🌅 **بسم الله الرحمن الرحیم**\n\n"
               "امیدوارم روز خوب و پر از موفقیت رو آغاز کنید. ☀️\n"
               "فرصت‌ها منتظر نمی‌مانند؛ آغاز نرخ‌دهی بازار! 🚀💰")
NIGHT_MSG = ("🌙 **شب بخیر**\n\n"
             "امیدوارم روز پرباری را سپری کرده باشید. شبی آرام و فردایی پر از موفقیت پیش رو داشته باشید. 🌌")


async def fetch_bourse():
    if not BRS_API_KEY:
        return None
    try:
        async with http_session() as s:
            data = await _get_json(s, "https://Api.Brsapi.ir/Tsetmc/Index.php",
                                   params={"key": BRS_API_KEY.strip(), "type": "1"})
            if isinstance(data, dict) and "index" in data:
                return (
                    f"📈 **وضعیت شاخص‌های بورس تهران**\n\n"
                    f"🔹 **شاخص کل:** `{data.get('index', 0):,.0f}`\n"
                    f"⚖️ **شاخص هم‌وزن:** `{data.get('index_equalWeight', 0):,.0f}`\n"
                    f"🔴 وضعیت بازار: `{data.get('state', 'نامشخص')}`\n"
                )
    except Exception:
        log.exception("bourse fetch failed")
    return None


async def fetch_kala():
    if not BRS_API_KEY:
        return None
    today = jdatetime.date.today()
    async with http_session() as s:
        for i in range(7):
            day = today - jdatetime.timedelta(days=i)
            ds = day.strftime("%Y-%m-%d")
            try:
                data = await _get_json(s, "https://Api.Brsapi.ir/IME/Physical.php",
                                       params={"key": BRS_API_KEY.strip(), "date_start": ds, "date_end": ds})
            except Exception:
                log.exception("kala fetch failed"); return None
            if isinstance(data, list) and data:
                valid = [x for x in data if x.get("pc") is not None and x.get("l30")]
                valid.sort(key=lambda x: x.get("tval", 0), reverse=True)
                if not valid:
                    continue
                lines = [f"📦 **مهم‌ترین معاملات بورس کالا**\n🗓 `{day.strftime('%Y/%m/%d')}`\n"]
                for it in valid[:10]:
                    lines.append(
                        f"🔹 **{it.get('l30', '—')}**\n"
                        f"🏭 {it.get('producer', '—')}\n"
                        f"💰 `{it.get('pc', 0)/10:,.0f}` تومان | "
                        f"📦 `{it.get('volume_contract') or 0:,}` تن"
                    )
                return "\n\n".join(lines) + "\n\n_(۱۰ کالای برتر)_"
            await asyncio.sleep(0.5)
    return None


async def build_slot_text(kind):
    if kind == "morning": return MORNING_MSG
    if kind == "night":   return NIGHT_MSG
    if kind == "bourse":  body = await fetch_bourse()
    elif kind == "kala":  body = await fetch_kala()
    else:
        body = STORE.get_message(kind)
        if not body:
            try:
                await refresh_cycle()
            except Exception:
                log.exception("on-demand refresh failed")
            body = STORE.get_message(kind)
    return (body + CHANNEL_FOOTER) if body else None


async def try_send_text(bot, kind):
    text = await build_slot_text(kind)
    if not text:
        log.warning("slot %s: no data → NOT marking as sent", kind)
        return False
    for attempt in range(2):
        try:
            await bot.send_message(chat_id=CHANNEL_ID, text=text)
            log.info("sent text: %s", kind)
            return True
        except Exception:
            log.exception("send_message failed (%s attempt %d)", kind, attempt + 1)
            await asyncio.sleep(3 * (attempt + 1))
    return False


async def try_send_infographic(bot):
    try:
        img = await asyncio.to_thread(generate_infographic)
        if not img:
            log.error("infographic skipped: font missing")
            return False
        if hasattr(bot, "send_photo"):
            await bot.send_photo(chat_id=CHANNEL_ID, photo=img, caption="📊 خلاصه وضعیت بازار")
        log.info("sent infographic")
        return True
    except Exception:
        log.exception("send_photo failed")
        return False


async def scheduler_loop(bot):
    state = await _load_state()
    while True:
        try:
            now = datetime.now(TEHRAN)
            slot = _slot_for(now)
            if slot:
                key, kind, with_info = slot
                async with _STATE_LOCK:
                    if not state.get(key):
                        ok = await try_send_text(bot, kind)
                        if ok:
                            state[key] = True
                            await _save_state(state)
                    img_key = f"img:{key}"
                    if with_info and state.get(key) and not state.get(img_key):
                        ok = await try_send_infographic(bot)
                        if ok:
                            state[img_key] = True
                            await _save_state(state)
        except Exception:
            log.exception("scheduler iteration crashed")
        await asyncio.sleep(15)


# ===========================================================================
# Health check
# ===========================================================================
_BOT_STATE = {"polling": False, "started_at": datetime.now(TEHRAN)}


class HealthHandler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="text/plain; charset=utf-8"):
        b = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        if self.path == "/healthz":
            self._send(200, "alive")
        elif self.path == "/ready":
            ready = bool(_BOT_STATE["polling"]) and STORE.last_cycle is not None
            self._send(200 if ready else 503,
                       f"polling={_BOT_STATE['polling']} last_cycle={STORE.last_cycle.isoformat() if STORE.last_cycle else 'none'}")
        else:
            self._send(200, "OK - Digi Navasan Bale Bot")

    def log_message(self, fmt, *args):
        pass


def run_health_server():
    try:
        srv = ThreadingHTTPServer(("0.0.0.0", PORT), HealthHandler)
        log.info("health server on :%d", PORT)
        srv.serve_forever()
    except Exception:
        log.exception("health server crashed")


# ===========================================================================
# User state (in-memory, TTL)
# ===========================================================================
USER_STATE_TTL = 30 * 60
_user_states = {}
_user_lock = threading.Lock()


def ustate_get(uid):
    with _user_lock:
        st = _user_states.get(uid)
        if not st:
            return None
        if time.time() - st.get("_touched", 0) > USER_STATE_TTL:
            _user_states.pop(uid, None)
            return None
        return st


def ustate_set(uid, **kw):
    with _user_lock:
        st = _user_states.setdefault(uid, {})
        st.update(kw)
        st["_touched"] = time.time()


def ustate_clear(uid):
    with _user_lock:
        _user_states.pop(uid, None)


def _gc_states():
    cutoff = time.time() - USER_STATE_TTL
    with _user_lock:
        for uid in [u for u, s in _user_states.items() if s.get("_touched", 0) < cutoff]:
            _user_states.pop(uid, None)


# ===========================================================================
# Calculations
# ===========================================================================
def parse_number(text, lo=None, hi=None):
    t = str(text or "").translate(_DIGITS_MAP)
    for ch in (",", "٬", "،", " ", "\u200c", "_", "٪", "%"):
        t = t.replace(ch, "")
    t = t.replace("٫", ".")
    v = float(t)
    if not math.isfinite(v):
        raise ValueError("not finite")
    if lo is not None and v < lo:
        raise ValueError("too small")
    if hi is not None and v > hi:
        raise ValueError("too large")
    return v


def calc_gold(price_per_gram, weight, wage_pct, profit_pct, tax_pct):
    """
    مبنای مالیات (مطابق رفتار قبلی): ماخذ = قیمت خام + اجرت + سود
    مبلغ مالیات = ماخذ × درصد مالیات
    """
    raw = price_per_gram * weight
    wage = raw * (wage_pct / 100.0)
    profit = raw * (profit_pct / 100.0)
    base = raw + wage + profit
    tax = base * (tax_pct / 100.0)
    return {"raw": raw, "wage": wage, "profit": profit, "tax": tax, "total": base + tax,
            "price_per_gram": price_per_gram, "weight": weight,
            "wage_pct": wage_pct, "profit_pct": profit_pct, "tax_pct": tax_pct}


def calc_loan(amount, annual_rate_pct, months):
    if months <= 0: raise ValueError("months must be positive")
    if annual_rate_pct < 0: raise ValueError("rate must be non-negative")
    r = (annual_rate_pct / 100.0) / 12.0
    if r == 0:
        monthly = amount / months
    else:
        f = (1 + r) ** months
        monthly = amount * r * f / (f - 1)
    total = monthly * months
    return {"amount": amount, "rate_pct": annual_rate_pct, "months": months,
            "monthly": monthly, "total": total, "interest_total": total - amount}


# ===========================================================================
# Menu & handlers
# ===========================================================================
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
        if len(cur) + len(line) + 1 > limit:
            chunks.append(cur); cur = line
        else:
            cur = f"{cur}\n{line}" if cur else line
    if cur: chunks.append(cur)
    return chunks


async def safe_reply(message, text, **kw):
    parts = _split(text)
    for i, part in enumerate(parts):
        await message.answer(part, **(kw if i == len(parts) - 1 else {}))


dp = Dispatcher()


@dp.message(CommandStart())
async def cmd_start(message):
    if message.chat.type != ChatType.PRIVATE:
        return
    ustate_clear(message.from_user.id)
    name = (message.from_user.first_name or "دوست عزیز").strip()
    await safe_reply(message,
                     f"سلام {name} عزیز! به ربات دیجی نوسان خوش آمدید. ✅\n\n"
                     f"از منوی زیر یکی از گزینه‌ها را انتخاب کنید:",
                     reply_markup=main_menu())


STATE_ERRORS = {
    "gold_price":    "❌ لطفاً یک عدد معتبر برای قیمت وارد کنید.",
    "gold_weight":   "❌ لطفاً یک عدد معتبر برای وزن وارد کنید.",
    "gold_wage":     "❌ لطفاً درصد اجرت را وارد کنید (۰ تا ۱۰۰۰).",
    "gold_profit":   "❌ لطفاً درصد سود را وارد کنید (۰ تا ۱۰۰۰).",
    "gold_tax":      "❌ لطفاً درصد مالیات را وارد کنید (۰ تا ۱۰۰۰).",
    "loan_amount":   "❌ لطفاً مبلغ معتبر وارد کنید.",
    "loan_interest": "❌ لطفاً نرخ سود (۰ تا ۲۰۰) وارد کنید.",
    "loan_months":   "❌ لطفاً تعداد ماه صحیح (۱ تا ۶۰۰) وارد کنید.",
}


async def menu_click(message, text, uid):
    if text == BTN_FIAT:
        await safe_reply(message, STORE.get_message("fiat") or "⏳ در حال دریافت...")
    elif text == BTN_GOLD:
        await safe_reply(message, STORE.get_message("gold") or "⏳ در حال دریافت...")
    elif text == BTN_CRYPTO:
        await safe_reply(message, STORE.get_message("crypto") or "⏳ در حال دریافت...")
    elif text == BTN_USDT:
        await safe_reply(message, STORE.get_message("usdt") or "⏳ در حال دریافت...")
    elif text == BTN_GCALC:
        g = STORE.get_bucket("gold")
        g18 = g.get("gold_18"); g24 = g.get("gold_24")
        msg = (
            f"🧮 **ماشین‌حساب طلا**\n\n💡 قیمت روز:\n"
            f"🥇 ۱۸ عیار: `{_fmt_toman(g18.value if g18 else None)}`\n"
            f"🥇 ۲۴ عیار: `{_fmt_toman(g24.value if g24 else None)}`\n\n"
            f"👇 **قیمت هر گرم** را وارد کنید:"
        )
        ustate_set(uid, state="gold_price")
        await safe_reply(message, msg, reply_markup=back_menu())
    elif text == BTN_LCALC:
        ustate_set(uid, state="loan_amount")
        await safe_reply(message, "💸 **مبلغ کل وام** را وارد کنید:", reply_markup=back_menu())
    elif text == BTN_WEATHER:
        ustate_set(uid, state="weather_city")
        await safe_reply(message, "🌤 نام شهر خود را وارد کنید (مثلاً: تهران):", reply_markup=back_menu())
    elif text == BTN_AI:
        ustate_set(uid, state="ai_chat")
        await safe_reply(message, "🤖 چت با هوش مصنوعی فعال شد. سوال خود را بپرسید:", reply_markup=back_menu())


async def handle_state(message, text, st, uid):
    state = st.get("state")

    if state == "weather_city":
        if not text:
            await safe_reply(message, "❌ لطفاً نام شهر را بنویسید.", reply_markup=back_menu()); return
        await safe_reply(message, f"⏳ در حال دریافت آب‌وهوای {text}...")
        res = await get_weather(text)
        ustate_clear(uid)
        await safe_reply(message, res, reply_markup=main_menu())
        return

    if state == "ai_chat":
        if not text:
            await safe_reply(message, "لطفاً سوال خود را بنویسید.", reply_markup=back_menu()); return
        await safe_reply(message, "⏳ در حال پردازش...")
        ans = await get_ai_response(text, uid)
        await safe_reply(message, ans, reply_markup=back_menu())
        return

    try:
        if state == "gold_price":
            ustate_set(uid, state="gold_weight", price=parse_number(text, lo=1, hi=1e13))
            await safe_reply(message, "⚖️ وزن طلا را به **گرم** وارد کنید:", reply_markup=back_menu())
        elif state == "gold_weight":
            st["weight"] = parse_number(text, lo=0.001, hi=1e6)
            ustate_set(uid, state="gold_wage")
            await safe_reply(message, "🛠 درصد اجرت ساخت (بدون اجرت: 0):", reply_markup=back_menu())
        elif state == "gold_wage":
            st["wage_pct"] = parse_number(text, lo=0, hi=1000)
            ustate_set(uid, state="gold_profit")
            await safe_reply(message, "🏪 درصد سود فروشنده (بدون سود: 0):", reply_markup=back_menu())
        elif state == "gold_profit":
            st["profit_pct"] = parse_number(text, lo=0, hi=1000)
            ustate_set(uid, state="gold_tax")
            await safe_reply(message, "📄 درصد مالیات بر ارزش افزوده (معمولاً 9):", reply_markup=back_menu())
        elif state == "gold_tax":
            tax = parse_number(text, lo=0, hi=1000)
            r = calc_gold(st["price"], st["weight"], st["wage_pct"], st["profit_pct"], tax)
            msg = (
                f"🧮 **نتیجه محاسبه طلا**\n\n"
                f"💰 قیمت هر گرم: `{r['price_per_gram']:,.0f}`\n"
                f"⚖️ وزن: `{r['weight']:g}` گرم\n"
                f"📦 قیمت خام: `{r['raw']:,.0f}`\n"
                f"🛠 اجرت ({r['wage_pct']:g}٪): `{r['wage']:,.0f}`\n"
                f"🏪 سود ({r['profit_pct']:g}٪): `{r['profit']:,.0f}`\n"
                f"📄 مالیات ({r['tax_pct']:g}٪): `{r['tax']:,.0f}`\n\n"
                f"✅ **مبلغ نهایی: `{r['total']:,.0f}` تومان**"
            )
            ustate_clear(uid)
            await safe_reply(message, msg, reply_markup=main_menu())
        elif state == "loan_amount":
            ustate_set(uid, state="loan_interest", amount=parse_number(text, lo=1, hi=1e15))
            await safe_reply(message, "📈 نرخ سود سالانه (بدون سود: 0):", reply_markup=back_menu())
        elif state == "loan_interest":
            st["interest"] = parse_number(text, lo=0, hi=200)
            ustate_set(uid, state="loan_months")
            await safe_reply(message, "⏳ تعداد ماه‌های بازپرداخت:", reply_markup=back_menu())
        elif state == "loan_months":
            m = parse_number(text, lo=1, hi=600)
            if m != int(m):
                raise ValueError("months must be integer")
            r = calc_loan(st["amount"], st["interest"], int(m))
            msg = (
                f"💸 **نتیجه محاسبه وام**\n\n"
                f"💵 مبلغ: `{r['amount']:,.0f}`\n"
                f"📈 سود: `{r['rate_pct']:g}٪`\n"
                f"⏳ مدت: `{r['months']}` ماه\n\n"
                f"💳 **قسط ماهانه: `{r['monthly']:,.0f}` تومان**\n"
                f"💰 مجموع: `{r['total']:,.0f}`\n"
                f"📉 سود کل: `{r['interest_total']:,.0f}`"
            )
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
        if uid != ADMIN_ID:
            await safe_reply(message, "⛔ این دستور فقط برای مدیر مجاز است.", reply_markup=main_menu())
            return
        await safe_reply(message, "⏳ در حال ساخت اینفوگرافیک تستی...")
        try:
            img = await asyncio.to_thread(generate_infographic)
            if not img:
                await safe_reply(message, "❌ فونت فارسی در سرور یافت نشد.", reply_markup=main_menu()); return
            await message.answer_photo(photo=img, caption="📊 تست اینفوگرافیک")
        except Exception:
            log.exception("test_info failed")
            await safe_reply(message, "⚠️ خطا در ساخت تصویر.", reply_markup=main_menu())
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
async def handle_any(message):
    if message.chat.type != ChatType.PRIVATE:
        return
    try:
        await process_private(message)
    except Exception:
        log.exception("handler failed for user %s", getattr(message.from_user, "id", "?"))
        try:
            await message.answer("⚠️ خطایی رخ داد؛ لطفاً دوباره تلاش کنید.", reply_markup=main_menu())
        except Exception:
            pass


# ===========================================================================
# Bootstrap
# ===========================================================================
_BACKGROUND = []


async def main():
    log.info("ENV: BALE=%s GROQ=%s BRS=%s UPSTASH=%s PORT=%s",
             bool(BALE_TOKEN), bool(GROQ_API_KEY), bool(BRS_API_KEY),
             bool(UPSTASH_URL and UPSTASH_TOKEN), PORT)
    if not BALE_TOKEN:
        raise SystemExit("BALE_TOKEN is not set")
    if not resolve_font_path():
        log.error("!! Vazirmatn font missing — infographic will be disabled !!")

    bot = Bot(token=BALE_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN))

    _BACKGROUND.append(asyncio.create_task(cache_loop(), name="cache_loop"))
    _BACKGROUND.append(asyncio.create_task(scheduler_loop(bot), name="scheduler"))
    _BACKGROUND.append(asyncio.create_task(_gc_loop(), name="state_gc"))

    _BOT_STATE["polling"] = True
    try:
        await dp.start_polling(bot)
    finally:
        _BOT_STATE["polling"] = False
        for t in _BACKGROUND:
            t.cancel()
        await asyncio.gather(*_BACKGROUND, return_exceptions=True)
        log.info("shutdown complete")


async def _gc_loop():
    while True:
        await asyncio.sleep(300)
        try:
            _gc_states()
        except Exception:
            log.exception("user-state GC failed")


if __name__ == "__main__":
    threading.Thread(target=run_health_server, daemon=True, name="health").start()
    print("Starting Bale Bot (v3)...")
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("interrupted")
