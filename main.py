import os
import logging
import requests
import threading
import http.server
import socketserver
import pytz
import jdatetime
from hijri_converter import convert
from datetime import datetime, time
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

TOKEN = os.environ.get("BOT_TOKEN")
PORT = int(os.environ.get("PORT", 10000))
CHANNEL_ID = "@diginavasan"

# ==========================================
# ⚙️ تنظیمات و متن‌های قابل ویرایش
# ==========================================
TEMPLATES = {
    "fiat_title": "💱 **قیمت لحظه‌ای ارزها**",
    "gold_title": "🌕 **قیمت لحظه‌ای طلا و سکه**",
    "crypto_title": "🪙 **قیمت لحظه‌ای ارز دیجیتال**",
    "footer": "\n*💰 قیمت لحظه‌ای دیجی نوسان 👇*\n@diginavasan"
}

# ==========================================
# ⚙️ توابع پایه (وب‌سرور و زمان)
# ==========================================
def run_dummy_server():
    Handler = http.server.SimpleHTTPRequestHandler
    with socketserver.TCPServer(("", PORT), Handler) as httpd:
        print(f"Web server listening on port {PORT}")
        httpd.serve_forever()

def get_dates_info():
    tehran_tz = pytz.timezone('Asia/Tehran')
    now = datetime.now(tehran_tz)
    
    # تاریخ شمسی
    shamsi_date = jdatetime.date.fromgregorian(date=now.date()).strftime('%Y/%m/%d')
    
    # تاریخ قمری
    hijri_date = convert.Gregorian(now.year, now.month, now.day).to_hijri()
    hijri_str = f"{hijri_date.year}/{hijri_date.month}/{hijri_date.day}"
    
    # تاریخ میلادی
    gregorian_str = now.strftime('%Y/%m/%d')
    
    # روز هفته
    days = ["دوشنبه", "سه‌شنبه", "چهارشنبه", "پنجشنبه", "جمعه", "شنبه", "یکشنبه"]
    day_name = days[now.weekday()]
    
    time_str = now.strftime('%H:%M')
    
    return day_name, shamsi_date, hijri_str, gregorian_str, time_str, now.hour

def is_working_hours():
    _, _, _, _, _, hour = get_dates_info()
    return 8 <= hour < 20  # فقط بین ۸ صبح تا ۸ شب

# ==========================================
# ⚙️ توابع گرفتن قیمت
# ==========================================
def get_nerkh_price(key):
    try:
        url = "https://nerkh.jahankhahan.shop/data/live.json"
        response = requests.get(url, timeout=10)
        if response.status_code == 200:
            data = response.json()
            price = data['rates'].get(key)
            if price:
                return f"{price:,.0f}"
        return "نامشخص"
    except Exception:
        return "خطا"

def get_nobitex_price(symbol):
    try:
        url = f"https://apiv2.nobitex.ir/market/stats?srcCurrency={symbol}&dstCurrency=rls"
        response = requests.get(url, timeout=10)
        if response.status_code == 200:
            data = response.json()
            key = f"{symbol}-rls"
            if key in data['stats']:
                price_toman = int(float(data['stats'][key]['latest']) / 10)
                return f"{price_toman:,.0f}"
        return "نامشخص"
    except Exception:
        return "خطا"

# ==========================================
# ⚙️ پیام‌های صبح و شب
# ==========================================
async def send_morning_message(context: ContextTypes.DEFAULT_TYPE):
    day, shamsi, hijri, greg, t, _ = get_dates_info()
    
    msg = f"""🌅 **بسم الله الرحمن الرحیم**
صبح بخیر! بازار امروز آغاز شد. ☀️

📅 امروز: **{day}**
🗓 شمسی: `{shamsi}`
🌙 قمری: `{hijri}`
📆 میلادی: `{greg}`
⏰ ساعت: `{t}`

*💰 دیجی نوسان 👇*
@diginavasan"""
    await context.bot.send_message(chat_id=CHANNEL_ID, text=msg, parse_mode='Markdown')

async def send_night_message(context: ContextTypes.DEFAULT_TYPE):
    msg = f"""🌙 **پایان نرخ بازار**
بازار تعطیل شد. شب خوبی رو برای شما و خانواده‌تون آرزو می‌کنیم. 🌹

*💰 دیجی نوسان 👇*
@diginavasan"""
    await context.bot.send_message(chat_id=CHANNEL_ID, text=msg, parse_mode='Markdown')

# ==========================================
# ⚙️ پیام‌های چرخشی نرخ‌ها
# ==========================================
async def send_fiat_prices(context: ContextTypes.DEFAULT_TYPE):
    _, _, _, _, t, _ = get_dates_info()
    usd = get_nerkh_price("dollar")
    eur = get_nerkh_price("euro")
    gbp = get_nerkh_price("pound")
    aed = get_nerkh_price("dirham")
    try_price = get_nerkh_price("lira")
    cny = get_nerkh_price("yuan")
    
    msg = f"""{TEMPLATES['fiat_title']}
⏰ ساعت آپدیت: `{t}`

💵 دلار = `{usd}` تومان
💶 یورو = `{eur}` تومان
💷 پوند = `{gbp}` تومان
🇦🇪 درهم = `{aed}` تومان
🇹🇷 لیر = `{try_price}` تومان
🇨🇳 یوان = `{cny}` تومان
{TEMPLATES['footer']}"""
    await context.bot.send_message(chat_id=CHANNEL_ID, text=msg, parse_mode='Markdown')

async def send_gold_prices(context: ContextTypes.DEFAULT_TYPE):
    _, _, _, _, t, _ = get_dates_info()
    gold18 = get_nerkh_price("gold")
    coin_emami = get_nerkh_price("emami_coin")
    coin_nim = get_nerkh_price("half_coin")
    coin_rob = get_nerkh_price("quarter_coin")
    
    msg = f"""{TEMPLATES['gold_title']}
⏰ ساعت آپدیت: `{t}`

🌕 طلا ۱۸ عیار = `{gold18}` تومان
🪙 سکه امامی = `{coin_emami}` تومان
🪙 نیم سکه = `{coin_nim}` تومان
🪙 ربع سکه = `{coin_rob}` تومان
{TEMPLATES['footer']}"""
    await context.bot.send_message(chat_id=CHANNEL_ID, text=msg, parse_mode='Markdown')

async def send_crypto_prices(context: ContextTypes.DEFAULT_TYPE):
    _, _, _, _, t, _ = get_dates_info()
    btc = get_nobitex_price("btc")
    eth = get_nobitex_price("eth")
    usdt = get_nobitex_price("usdt")
    
    msg = f"""{TEMPLATES['crypto_title']}
⏰ ساعت آپدیت: `{t}`

🟠 بیت‌کوین (BTC) = `{btc}` تومان
🔷 اتریوم (ETH) = `{eth}` تومان
🟢 تتر (USDT) = `{usdt}` تومان
{TEMPLATES['footer']}"""
    await context.bot.send_message(chat_id=CHANNEL_ID, text=msg, parse_mode='Markdown')

# ==========================================
# ⚙️ سیستم چرخش
# ==========================================
_rotation_index = 0

async def rotate_jobs(context: ContextTypes.DEFAULT_TYPE):
    if not is_working_hours():
        return
        
    global _rotation_index
    jobs = [send_fiat_prices, send_gold_prices, send_crypto_prices]
    
    current_job = jobs[_rotation_index % len(jobs)]
    _rotation_index += 1
    
    print(f"ارسال پیام چرخشی شماره {_rotation_index % len(jobs) + 1}...")
    await current_job(context)

# ==========================================
# ⚙️ دستورات تست
# ==========================================
async def start(update, context):
    await context.bot.send_message(chat_id=update.effective_chat.id, text="ربات مجهز به سیستم زمان‌بندی شد. ✅")

async def test_morning(update, context):
    await send_morning_message(context)

async def test_night(update, context):
    await send_night_message(context)

async def test_fiat(update, context):
    await send_fiat_prices(context)

async def test_gold(update, context):
    await send_gold_prices(context)

async def test_crypto(update, context):
    await send_crypto_prices(context)

if __name__ == '__main__':
    threading.Thread(target=run_dummy_server, daemon=True).start()
    
    app = ApplicationBuilder().token(TOKEN).build()
    
    app.add_handler(CommandHandler('start', start))
    app.add_handler(CommandHandler('test_morning', test_morning))
    app.add_handler(CommandHandler('test_night', test_night))
    app.add_handler(CommandHandler('fiat', test_fiat))
    app.add_handler(CommandHandler('gold', test_gold))
    app.add_handler(CommandHandler('crypto', test_crypto))
    
    tehran_tz = pytz.timezone('Asia/Tehran')
    
    # ۱. پیام صبح‌بخیر رأس ساعت ۸:۰۰ صبح
    app.job_queue.run_daily(send_morning_message, time=time(8, 0, tzinfo=tehran_tz))
    
    # ۲. پیام پایان بازار رأس ساعت ۸:۰۰ شب
    app.job_queue.run_daily(send_night_message, time=time(20, 0, tzinfo=tehran_tz))
    
    # ۳. چرخش پیام‌ها هر ۸ دقیقه (۴۸۰ ثانیه) - فقط در ساعات کاری
    app.job_queue.run_repeating(rotate_jobs, interval=480, first=10)
    
    print("ربات روشن شد. سیستم زمان‌بندی (صبح، شب، چرخش) فعال است...")
    app.run_polling()
