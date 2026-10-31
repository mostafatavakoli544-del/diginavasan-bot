import os
import logging
import asyncio
import threading
import time
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from http.server import BaseHTTPRequestHandler, HTTPServer

import requests
import pytz
import jdatetime

from baleio import Bot, Dispatcher, md
from baleio.client.default import DefaultBotProperties
from baleio.filters import CommandStart
from baleio.types import Message, ReplyKeyboardMarkup, KeyboardButton


# ---------------------------------------------------------
# Configuration
# ---------------------------------------------------------

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("diginavasan-bale")

BALE_TOKEN = os.environ.get("BALE_TOKEN")
PORT = int(os.environ.get("PORT", 10000))
CHANNEL_ID = "@diginavasan"
ADMIN_ID = 718840144
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
BRS_API_KEY = os.environ.get("BRS_API_KEY")

TEHRAN_TZ = pytz.timezone("Asia/Tehran")
CACHE_TTL_SECONDS = int(os.environ.get("PRICE_CACHE_TTL", "20"))
HTTP_TIMEOUT_SECONDS = 8
HTTP_HEADERS = {"User-Agent": "Mozilla/5.0 DigiNavasan/1.0"}


# ---------------------------------------------------------
# Render health endpoint (does not expose project files)
# ---------------------------------------------------------

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path in ("/", "/healthz"):
            body = b"OK"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_error(404)

    def log_message(self, format, *args):
        logger.info("Health check: " + format, *args)


def run_health_server():
    server = HTTPServer(("0.0.0.0", PORT), HealthHandler)
    logger.info("Health server listening on port %s", PORT)
    server.serve_forever()


# ---------------------------------------------------------
# Common helpers and cache
# ---------------------------------------------------------

_price_cache = {}
_price_locks = {}


def get_dates_info():
    now = datetime.now(TEHRAN_TZ)
    return now.strftime("%H:%M:%S"), now.hour


def _timestamp():
    return datetime.now(TEHRAN_TZ).strftime("%H:%M:%S")


async def _cached(group, loader):
    """TTL cache with single-flight refresh for each price group."""
    now = time.monotonic()
    entry = _price_cache.get(group)
    if entry and now - entry["stored_at"] < CACHE_TTL_SECONDS:
        return entry["data"], entry["updated_at"], False

    lock = _price_locks.setdefault(group, asyncio.Lock())
    async with lock:
        now = time.monotonic()
        entry = _price_cache.get(group)
        if entry and now - entry["stored_at"] < CACHE_TTL_SECONDS:
            return entry["data"], entry["updated_at"], False

        try:
            data = await asyncio.to_thread(loader)
            if not data:
                raise ValueError("Provider returned no valid data")

            updated_at = _timestamp()
            _price_cache[group] = {
                "data": data,
                "stored_at": time.monotonic(),
                "updated_at": updated_at,
            }
            return data, updated_at, False
        except Exception:
            logger.exception("Price refresh failed: %s", group)
            if entry and entry.get("data"):
                return entry["data"], entry["updated_at"], True
            return {}, None, False


def _fetch_nerkh_rates():
    url = "https://nerkh.jahankhahan.shop/data/live.json"
    response = requests.get(url, headers=HTTP_HEADERS, timeout=HTTP_TIMEOUT_SECONDS)
    response.raise_for_status()

    payload = response.json()
    rates = payload.get("rates")
    if not isinstance(rates, dict):
        raise ValueError("Invalid currency/gold response")

    valid = {}
    for key, raw_value in rates.items():
        try:
            value = int(float(raw_value))
            if value > 0:
                valid[str(key).lower()] = value
        except (TypeError, ValueError):
            continue

    if not valid:
        raise ValueError("No valid prices in response")
    return valid


def _first_price(rates, aliases):
    for alias in aliases:
        value = rates.get(alias.lower())
        if isinstance(value, (int, float)) and value > 0:
            return int(value)
    return None


async def get_currency_prices():
    rates, updated_at, stale = await _cached("nerkh_rates", _fetch_nerkh_rates)
    aliases = {
        "dollar": ["dollar", "usd"],
        "euro": ["euro", "eur"],
        "pound": ["pound", "gbp"],
        "aed": ["aed"],
        "lira": ["lira"],
        "yuan": ["yuan"],
        "cad": ["cad"],
        "aud": ["aud"],
    }
    return {
        name: _first_price(rates, keys)
        for name, keys in aliases.items()
    }, updated_at, stale


async def get_gold_prices():
    rates, updated_at, stale = await _cached("nerkh_rates", _fetch_nerkh_rates)
    aliases = {
        "gold": ["gold"],
        "gold24": ["gold24"],
        "coin": ["coin"],
        "bahar": ["bahar"],
        "half": ["half"],
        "quarter": ["quarter"],
    }
    return {
        name: _first_price(rates, keys)
        for name, keys in aliases.items()
    }, updated_at, stale


# ---------------------------------------------------------
# Cryptocurrency prices
# ---------------------------------------------------------

def _fetch_one_crypto(symbol, coin_id):
    url = (
        "https://apiv2.nobitex.ir/market/stats"
        f"?srcCurrency={coin_id}&dstCurrency=rls"
    )
    response = requests.get(url, headers=HTTP_HEADERS, timeout=HTTP_TIMEOUT_SECONDS)
    response.raise_for_status()

    stats = response.json().get("stats", {})
    raw_value = stats.get(f"{coin_id}-rls", {}).get("latest")
    value = int(float(raw_value) / 10)
    if value <= 0:
        raise ValueError(f"Invalid price for {symbol}")
    return symbol, value


def _fetch_crypto_all():
    coins = {
        "BTC": "btc",
        "ETH": "eth",
        "BNB": "bnb",
        "TRX": "trx",
        "SOL": "sol",
        "DOGE": "doge",
        "TON": "ton",
    }
    results = {}

    # محدودیت هم‌زمانی برای جلوگیری از فشار بیش از حد به API
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {
            pool.submit(_fetch_one_crypto, symbol, coin_id): symbol
            for symbol, coin_id in coins.items()
        }
        for future in as_completed(futures):
            try:
                symbol, price = future.result()
                results[symbol] = price
            except Exception:
                logger.warning(
                    "Crypto provider failed for %s",
                    futures[future],
                    exc_info=True,
                )

    return results


async def get_crypto_prices():
    prices, updated_at, stale = await _cached("crypto", _fetch_crypto_all)
    return {
        symbol: f"{price:,.0f}"
        for symbol, price in prices.items()
    }, updated_at, stale


# ---------------------------------------------------------
# USDT prices from exchanges
# ---------------------------------------------------------

def _fetch_one_usdt_provider(name):
    if name == "نوبیتکس":
        url = (
            "https://apiv2.nobitex.ir/market/stats"
            "?srcCurrency=usdt&dstCurrency=rls"
        )
        response = requests.get(url, headers=HTTP_HEADERS, timeout=HTTP_TIMEOUT_SECONDS)
        response.raise_for_status()
        value = float(response.json()["stats"]["usdt-rls"]["latest"]) / 10

    elif name == "والکس":
        response = requests.get(
            "https://api.wallex.ir/v1/markets",
            headers=HTTP_HEADERS,
            timeout=HTTP_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        value = float(
            response.json()["result"]["symbols"]["USDTTMN"]["stats"]["lastPrice"]
        )

    elif name == "رمزینکس":
        response = requests.get(
            "https://publicapi.ramzinex.com/exchange/api/v1.0/exchange/pairs",
            headers=HTTP_HEADERS,
            timeout=HTTP_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        item = next(
            item for item in response.json().get("data", [])
            if item.get("name", {}).get("en") == "tether/rial"
        )
        value = float(item.get("sell", 0)) / 10

    elif name == "بیت‌پین":
        response = requests.get(
            "https://api.bitpin.ir/api/v1/mkt/tickers/",
            headers=HTTP_HEADERS,
            timeout=HTTP_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        item = next(
            item for item in response.json()
            if item.get("symbol") == "USDT_IRT"
        )
        value = float(item.get("price", 0))

    elif name == "تبدیل":
        response = requests.get(
            "https://api.tabdeal.org/api/v1/market/statistics",
            headers=HTTP_HEADERS,
            timeout=HTTP_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        value = float(response.json()["USDTIRT"]["lastPrice"])

    else:
        raise ValueError("Unknown USDT provider")

    value = int(value)
    if value <= 0:
        raise ValueError(f"Invalid USDT price from {name}")
    return name, value


def _fetch_usdt_all():
    providers = ["نوبیتکس", "والکس", "رمزینکس", "بیت‌پین", "تبدیل"]
    results = {}

    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {
            pool.submit(_fetch_one_usdt_provider, name): name
            for name in providers
        }
        for future in as_completed(futures):
            try:
                name, price = future.result()
                results[name] = price
            except Exception:
                logger.warning(
                    "USDT provider failed: %s",
                    futures[future],
                    exc_info=True,
                )

    return results


async def get_usdt_prices():
    return await _cached("usdt", _fetch_usdt_all)


# ---------------------------------------------------------
# AI
# ---------------------------------------------------------

def get_ai_response(prompt):
    if not GROQ_API_KEY:
        return "سرویس هوش مصنوعی پیکربندی نشده است."

    try:
        url = "https://api.groq.com/openai/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {GROQ_API_KEY}",
            "Content-Type": "application/json",
        }
        data = {
            "model": "openai/gpt-oss-120b",
            "messages": [
                {
                    "role": "system",
                    "content": "You are a helpful assistant. Answer in Persian.",
                },
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.7,
            "max_tokens": 1024,
        }
        response = requests.post(
            url, headers=headers, json=data, timeout=30
        )
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"]
    except Exception:
        logger.exception("Groq request failed")
        return "خطای اتصال به هوش مصنوعی."


# ---------------------------------------------------------
# Menus
# ---------------------------------------------------------

def get_main_menu():
    keyboard = [
        [KeyboardButton(text="🚀 استارت / منوی اصلی")],
        [KeyboardButton(text="💱 قیمت ارزها"), KeyboardButton(text="🌕 قیمت طلا و سکه")],
        [KeyboardButton(text="🪙 قیمت ارز دیجیتال"), KeyboardButton(text="📈 شاخص بورس")],
        [KeyboardButton(text="🧮 ماشین‌حساب طلا"), KeyboardButton(text="💸 ماشین‌حساب اقساط")],
        [KeyboardButton(text="💱 مبدل هوشمند ارز"), KeyboardButton(text="🌤 وضعیت آب‌وهوا")],
        [KeyboardButton(text="🚨 آلارم قیمتی"), KeyboardButton(text="🤖 چت با هوش مصنوعی")],
        [KeyboardButton(text="🎁 دعوت از دوستان")],
    ]
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)


# ---------------------------------------------------------
# Commodities - unchanged in this stage
# ---------------------------------------------------------

async def fetch_and_format_commodities():
    if not BRS_API_KEY:
        return "⚠️ کلید سرویس بورس کالا تنظیم نشده است."

    today = jdatetime.date.today()
    headers = {"User-Agent": "Mozilla/5.0"}
    found_data = None
    date_found_str = ""
    payment_required = False

    for i in range(7):
        check_date = today - jdatetime.timedelta(days=i)
        date_str = check_date.strftime("%Y-%m-%d")
        url = (
            "https://Api.BrsApi.ir/IME/Physical.php"
            f"?key={BRS_API_KEY.strip()}&date_start={date_str}&date_end={date_str}"
        )

        try:
            res = await asyncio.to_thread(
                requests.get, url, headers=headers, timeout=30
            )
        except requests.RequestException:
            logger.exception("BRS commodities request failed")
            await asyncio.sleep(1)
            continue

        if res.status_code == 200:
            try:
                data = res.json()
                if isinstance(data, list) and data:
                    found_data = data
                    date_found_str = check_date.strftime("%Y/%m/%d")
                    break
            except ValueError:
                logger.warning("Invalid BRS commodities JSON")
        elif res.status_code == 402:
            payment_required = True
            break

        await asyncio.sleep(1)

    if payment_required:
        return (
            "⚠️ اطلاعات موقتاً در دسترس نیست.\n"
            "سهمیه رایگان سرور بورس کالا به پایان رسیده است."
        )
    if not found_data:
        return "⚠️ در ۷ روز گذشته معامله‌ای در بورس کالا ثبت نشده است."

    valid_items = [
        item for item in found_data
        if item.get("pc") is not None and item.get("l30")
    ]
    valid_items.sort(key=lambda item: item.get("tval", 0), reverse=True)
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
        "شمش بلوم": "محصول نیمه‌ساخته فولادی برای میلگرد.",
    }

    msg = f"📦 {md.bold('مهم‌ترین معاملات بورس کالا')}\n"
    msg += f"🗓 آخرین تاریخ معامله: {md.code(date_found_str)}\n"
    msg += f"⏰ {md.code(get_dates_info()[0])}\n\n"

    for item in top_items:
        name = item.get("l30", "نامشخص")
        producer = item.get("producer", "نامشخص")
        price_toman = item.get("pc", 0) / 10
        volume = item.get("volume_contract") or 0

        msg += f"🔹 {md.bold(name)}\n"
        msg += f"🏭 کارخانه: {producer}\n"
        msg += (
            f"💰 قیمت: {md.code(f'{price_toman:,.0f}')} تومان | "
            f"📦 حجم: {md.code(f'{volume:,}')} تن\n"
        )

        for key, desc in descriptions.items():
            if key in name:
                msg += f"💡 {desc}\n"
                break
        msg += "\n"

    msg += "_(بر اساس ۱۰ کالای با بالاترین ارزش معامله)_"
    return msg


# ---------------------------------------------------------
# Bot handlers
# ---------------------------------------------------------

dp = Dispatcher()


@dp.message(CommandStart())
async def command_start_handler(message: Message) -> None:
    welcome = (
        "سلام! به ربات دیجی نوسان در بله خوش آمدید. ✅\n\n"
        "از منوی زیر یکی از گزینه‌ها را انتخاب کنید:"
    )
    await message.answer(welcome, reply_markup=get_main_menu())


@dp.message()
async def handle_message(message: Message) -> None:
    text = message.text.strip() if message.text else ""
    time_str, _ = get_dates_info()

    if text in ("🚀 استارت / منوی اصلی", "/start"):
        await command_start_handler(message)

    elif text == "/test_kala":
        await message.answer("⏳ در حال دریافت اطلاعات تست کالا...")
        msg = await fetch_and_format_commodities()
        await message.answer(msg)

    elif text == "🪙 قیمت ارز دیجیتال":
        await message.answer("⏳ در حال دریافت اطلاعات...")

        crypto, crypto_time, crypto_stale = await get_crypto_prices()
        crypto_names = {
            "BTC": "بیت‌کوین",
            "ETH": "اتریوم",
            "BNB": "بایننس کوین",
            "TRX": "ترون",
            "SOL": "سولانا",
            "DOGE": "دوج کوین",
            "TON": "تون کوین",
        }

        msg_crypto = (
            f"🪙 {md.bold('قیمت لحظه‌ای ارز دیجیتال')}\n"
            f"⏰ {md.code(crypto_time or 'نامشخص')}\n"
        )
        if crypto_stale:
            msg_crypto += "⚠️ آخرین قیمت ثبت‌شده؛ دریافت جدید ناموفق بود.\n"
        msg_crypto += "\n"

        if crypto:
            for sym, price in crypto.items():
                msg_crypto += (
                    f"🔹 {md.bold(crypto_names.get(sym, sym))} "
                    f"({sym}): {md.code(price)} تومان\n"
                )
        else:
            msg_crypto += "⚠️ دریافت قیمت معتبر ممکن نشد."

        await message.answer(msg_crypto)
        await asyncio.sleep(1)

        usdt_prices, usdt_time, usdt_stale = await get_usdt_prices()
        msg_usdt = (
            f"💵 {md.bold('مقایسه قیمت تتر (USDT)')}\n"
            f"⏰ {md.code(usdt_time or 'نامشخص')}\n"
        )
        if usdt_stale:
            msg_usdt += "⚠️ آخرین قیمت ثبت‌شده؛ دریافت جدید ناموفق بود.\n"
        msg_usdt += "\n"

        if usdt_prices:
            sorted_prices = sorted(
                usdt_prices.items(), key=lambda item: item[1]
            )
            for index, (exchange, price) in enumerate(sorted_prices):
                label = " (ارزان‌ترین)" if index == 0 else ""
                msg_usdt += (
                    f"🔹 {exchange}: {md.code(f'{price:,}')} تومان{label}\n"
                )

            if len(sorted_prices) > 1:
                difference = sorted_prices[-1][1] - sorted_prices[0][1]
                msg_usdt += (
                    f"\n📊 اختلاف ارزان‌ترین و گران‌ترین: "
                    f"{md.code(f'{difference:,}')} تومان"
                )
        else:
            msg_usdt += "⚠️ دریافت قیمت معتبر ممکن نشد."

        await message.answer(msg_usdt)

    elif text == "💱 قیمت ارزها":
        prices, updated_at, stale = await get_currency_prices()
        msg = (
            f"💱 {md.bold('قیمت لحظه‌ای ارزها')}\n"
            f"⏰ {md.code(updated_at or 'نامشخص')}\n"
        )
        if stale:
            msg += "⚠️ آخرین قیمت ثبت‌شده؛ دریافت جدید ناموفق بود.\n"
        msg += "\n"

        labels = [
            ("dollar", "💵 دلار"),
            ("euro", "💶 یورو"),
            ("pound", "💷 پوند"),
            ("aed", "🇦🇪 درهم"),
            ("lira", "🇹🇷 لیر"),
            ("yuan", "🇨🇳 یوان"),
            ("cad", "🇨🇦 کانادا"),
            ("aud", "🇦🇺 استرالیا"),
        ]
        for key, label in labels:
            value = prices.get(key)
            display = f"{value:,}" if value else "ناموجود"
            msg += f"{label} = {md.code(display)} تومان\n"

        await message.answer(msg)

    elif text == "🌕 قیمت طلا و سکه":
        prices, updated_at, stale = await get_gold_prices()
        msg = (
            f"🌕 {md.bold('قیمت لحظه‌ای طلا و سکه')}\n"
            f"⏰ {md.code(updated_at or 'نامشخص')}\n"
        )
        if stale:
            msg += "⚠️ آخرین قیمت ثبت‌شده؛ دریافت جدید ناموفق بود.\n"
        msg += "\n"

        labels = [
            ("gold", "🥇 طلا ۱۸ عیار"),
            ("gold24", "🥇 طلا ۲۴ عیار"),
            ("coin", "🪙 سکه امامی"),
            ("bahar", "🪙 سکه بهار آزادی"),
            ("half", "🪙 نیم سکه"),
            ("quarter", "🪙 ربع سکه"),
        ]
        for key, label in labels:
            value = prices.get(key)
            display = f"{value:,}" if value else "ناموجود"
            msg += f"{label} = {md.code(display)} تومان\n"

        await message.answer(msg)

    elif text == "📈 شاخص بورس":
        await message.answer("⏳ در حال دریافت اطلاعات بورس...")
        if not BRS_API_KEY:
            await message.answer("❌ کلید سرویس بورس تنظیم نشده است.")
            return

        try:
            url = (
                "https://Api.Brsapi.ir/Tsetmc/Index.php"
                f"?key={BRS_API_KEY.strip()}&type=1"
            )
            res = await asyncio.to_thread(
                requests.get,
                url,
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=30,
            )

            if res.status_code == 200:
                data = res.json()
                index_val = data.get("index", 0)
                change_val = data.get("index_change", 0)
                equal_weight = data.get("index_equalWeight", 0)
                equal_weight_change = data.get("index_equalWeight_change", 0)
                state = data.get("state", "نامشخص")

                mv = data.get("mv", 0)
                tno = data.get("tno", 0)
                tval = data.get("tval", 0)
                tvol = data.get("tvol", 0)

                mv_str = f"{mv / 10000000000000:,.0f}"
                tval_str = f"{tval / 10000000000:,.0f}"

                msg = f"📈 {md.bold('وضعیت شاخص‌های بورس تهران')}\n"
                msg += f"⏰ {md.code(get_dates_info()[0])}\n\n"
                msg += (
                    f"🔹 {md.bold('شاخص کل:')} "
                    f"{md.code(f'{index_val:,.0f}')} "
                    f"(تغییر: {change_val:,.0f})\n"
                )
                msg += (
                    f"⚖️ {md.bold('شاخص هم‌وزن:')} "
                    f"{md.code(f'{equal_weight:,.0f}')} "
                    f"(تغییر: {equal_weight_change:,.0f})\n"
                )
                msg += f"🔴 وضعیت بازار: {md.code(state)}\n\n"
                msg += f"📊 {md.bold('آمار کلی بازار:')}\n"
                msg += f"💰 ارزش کل بازار: {md.code(mv_str)} هزار میلیارد تومان\n"
                msg += f"💵 ارزش معاملات: {md.code(tval_str)} میلیارد تومان\n"
                msg += f"🧾 تعداد معاملات: {md.code(f'{tno:,}')} معامله\n"
                msg += f"📦 حجم معاملات: {md.code(f'{tvol:,}')} سهم\n\n"
                msg += "_توجه: در پلن رایگان، آمار مربوط به آخرین روز معاملاتی است._"
                await message.answer(msg)
            else:
                await message.answer(
                    f"❌ خطا در دریافت اطلاعات. کد خطا: {res.status_code}"
                )
        except Exception:
            logger.exception("Stock index request failed")
            await message.answer("❌ خطا در دریافت اطلاعات بورس.")

    elif text in [
        "🧮 ماشین‌حساب طلا",
        "💸 ماشین‌حساب اقساط",
        "💱 مبدل هوشمند ارز",
        "🌤 وضعیت آب‌وهوا",
        "🚨 آلارم قیمتی",
        "🎁 دعوت از دوستان",
    ]:
        await message.answer(
            f"⏳ بخش {text} در حال توسعه است و به زودی به ربات اضافه می‌شود."
        )

    else:
        await message.answer("⏳ در حال پردازش...")
        result = await asyncio.to_thread(get_ai_response, text)
        await message.answer(result)


# ---------------------------------------------------------
# Startup
# ---------------------------------------------------------

async def main():
    if not BALE_TOKEN:
        raise RuntimeError("BALE_TOKEN environment variable is missing")

    bot = Bot(
        token=BALE_TOKEN,
        default=DefaultBotProperties(),
    )
    await dp.start_polling(bot)


if __name__ == "__main__":
    threading.Thread(
        target=run_health_server,
        daemon=True,
        name="health-server",
    ).start()

    logger.info("Starting Digi Navasan Bale bot...")
    asyncio.run(main())
