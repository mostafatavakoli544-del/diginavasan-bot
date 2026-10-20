import os
import logging
import requests
import threading
import http.server
import socketserver
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

TOKEN = os.environ.get("BOT_TOKEN")
PORT = int(os.environ.get("PORT", 10000))

# --- بخش وب‌سرور الکی برای راضی کردن رندر ---
def run_dummy_server():
    Handler = http.server.SimpleHTTPRequestHandler
    with socketserver.TCPServer(("", PORT), Handler) as httpd:
        print(f"Web server listening on port {PORT}")
        httpd.serve_forever()

# --- توابع ربات ---
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
    except Exception as e:
        return "خطا"

def get_usdt_nobitex():
    try:
        url = "https://apiv2.nobitex.ir/market/stats?srcCurrency=usdt&dstCurrency=rls"
        response = requests.get(url, timeout=10)
        if response.status_code == 200:
            data = response.json()
            price_toman = int(float(data['stats']['usdt-rls']['latest']) / 10)
            return f"{price_toman:,.0f}"
        return "نامشخص"
    except Exception:
        return "خطا"

async def send_prices_to_chat(context: ContextTypes.DEFAULT_TYPE, chat_id):
    usd = get_nerkh_price("dollar")
    gold18 = get_nerkh_price("gold")
    coin_emami = get_nerkh_price("emami_coin")
    usdt = get_usdt_nobitex()

    msg = f"""💰 **قیمت لحظه‌ای**

💵 قیمت دلار = `{usd}` تومان
🟢 قیمت تتر (نوبیتکس) = `{usdt}` تومان
🌕 قیمت طلا ۱۸ عیار = `{gold18}` تومان
🪙 سکه امامی = `{coin_emami}` تومان

*💰 قیمت لحظه‌ای دیجی نوسان 👇*
@diginavasan"""
    await context.bot.send_message(chat_id=chat_id, text=msg, parse_mode='Markdown')

async def start(update, context):
    await context.bot.send_message(chat_id=update.effective_chat.id, text="سلام! ربات دیجی نوسان فعال شد. ✅")
    
async def price(update, context):
    await send_prices_to_chat(context, update.effective_chat.id)

if __name__ == '__main__':
    # روشن کردن وب‌سرور الکی در پس‌زمینه
    threading.Thread(target=run_dummy_server, daemon=True).start()
    
    # روشن کردن ربات
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler('start', start))
    app.add_handler(CommandHandler('price', price))
    
    print("ربات روشن شد...")
    app.run_polling()
