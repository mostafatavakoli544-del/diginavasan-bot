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

TEMPLATES = {
    "fiat_title": "💱 **قیمت لحظه‌ای ارزها**",
    "gold_title": "🌕 **قیمت لحظه‌ای طلا و سکه**",
    "crypto_title": "🪙 **قیمت لحظه‌ای ارز دیجیتال**",
    "footer": "\n*💰 قیمت لحظه‌ای دیجی نوسان 👇*\n@diginavasan"
}

def run_dummy_server():
    Handler = http.server.SimpleHTTPRequestHandler
    with socketserver.TCPServer(("", PORT), Handler) as httpd:
        print(f"Web server listening on port {PORT}")
        httpd.serve_forever()

def get_dates_info():
    tehran_tz = pytz.timezone('Asia/Tehran')
    now = datetime.now(tehran_tz)
    
    shamsi_date = jdatetime.date.fromgregorian(date=now.date()).strftime('%Y/%m/%d')
    hijri_date = convert.Gregorian(now.year, now.month, now.day).to_hijri()
    hijri_str = f"{hijri_date.year}/{hijri_date.month}/{hijri_date.day}"
    gregorian_str = now.strftime('%Y/%m/%d')
    days = ["دوشنبه", "سه‌شنبه", "چهارشنبه", "پنجشنبه", "جمعه", "شنبه", "یکشنبه"]
    day_name = days[now.weekday()]
    time_str = now.strftime('%H:%M')
    
    return day_name, shamsi_date, hijri_str, gregorian_str, time_str, now.hour

def is_working_hours():
    _, _, _, _, _, hour = get_dates_info()
    return 8 <= hour < 20

# --- گرفتن کل دیتا یکجا (برای سرعت بالاتر) ---
def fetch_all_rates():
    try:
        url = "https://nerkh.jahankhahan.shop/data/live.json"
        response = requests.get(url, timeout=10)
        if response.status_code == 200:
            return response.json().get('rates', {})
        return {}
    except Exception:
        return {}

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

async def send_fiat_prices(context: ContextTypes.DEFAULT_TYPE):
    _, _, _, _, t, _ = get_dates_info()
    r = fetch_all_rates()
    
    msg = f"""{TEMPLATES['fiat_title']}
⏰ ساعت آپدیت: `{t}`

💵 دلار = `{r.get('dollar', 0):,.0f}` تومان
💶 یورو = `{r.get('euro', 0):,.0f}` تومان
💷 پوند = `{r.get('pound', 0):,.0f}` تومان
🇦🇪 درهم = `{r.get('aed', 0):,.0f}` تومان
🇹🇷 لیر = `{r.get('lira', 0):,.0f}` تومان
🇨🇳 یوان = `{r.get('yuan', 0):,.0f}` تومان
🇨🇦 دلار کانادا = `{r.get('cad', 0):,.0f}` تومان
{TEMPLATES['footer']}"""
    await context.bot.send_message(chat_id=CHANNEL_ID, text=msg, parse_mode='Markdown')

async def send_gold_prices(context: ContextTypes.DEFAULT_TYPE):
    _, _, _, _, t, _ = get_dates_info()
    r = fetch_all_rates()
    
    msg = f"""{TEMPLATES['gold_title']}
⏰ ساعت آپدیت: `{t}`

🌕 طلا ۱۸ عیار = `{r.get('gold', 0):,.0f}` تومان
🌕 طلا ۲۴ عیار = `{r.get('gold24', 0):,.0f}` تومان
⚖️ مثقال طلا = `{r.get('mesghal', 0):,.0f}` تومان
🪙 سکه امامی = `{r.get('coin', 0):,.0f}` تومان
🪙 سکه بهار = `{r.get('bahar', 0):,.0f}` تومان
🪙 نیم سکه = `{r.get('half', 0):,.0f}` تومان
🪙 ربع سکه = `{r.get('quarter', 0):,.0f}` تومان
{TEMPLATES['footer']}"""
    await context.bot.send_message(chat_id=CHANNEL_ID, text=msg, parse_mode='Markdown')

async def send_crypto_prices(context: ContextTypes.DEFAULT_TYPE):
    _, _, _, _, t, _ = get_dates_info()
    r = fetch_all_rates()
    
    # برای بیت‌کوین از دیتای خود API استفاده می‌کنیم، برای اتریوم و تتر از نوبیتکس
    btc = f"{r.get('bitcoin', 0):,.0f}"
    eth = get_nobitex_price("eth")
    usdt = get_nobitex_price("usdt")
    
    msg = f"""{TEMPLATES['crypto_title']}
⏰ ساعت آپدیت: `{t}`

🟠 بیت‌کوین (BTC) = `{btc}` تومان
🔷 اتریوم (ETH) = `{eth}` تومان
🟢 تتر (USDT) = `{usdt}` تومان
{TEMPLATES['footer']}"""
    await context.bot.send_message(chat_id=CHANNEL_ID, text=msg, parse_mode='Markdown')

_rotation_index = 0

async def rotate_jobs(context: ContextTypes.DEFAULT_TYPE):
    if not is_working_hours():
        return
    global _rotation_index
    jobs = [send_fiat_prices, send_gold_prices, send_crypto_prices]
    current_job = jobs[_rotation_index % len(jobs)]
    _rotation_index += 1
    await current_job(context)

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
    
    app.job_queue.run_daily(send_morning_message, time=time(8, 0, tzinfo=tehran_tz))
    app.job_queue.run_daily(send_night_message, time=time(20, 0, tzinfo=tehran_tz))
    app.job_queue.run_repeating(rotate_jobs, interval=480, first=10)
    
    print("ربات روشن شد. سیستم زمان‌بندی فعال است...")
    app.run_polling()
