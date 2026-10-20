import os
import logging
import requests
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

TOKEN = os.environ.get("BOT_TOKEN")

# تابع جدید برای گرفتن قیمت از API نرخ‌ها
def get_nerkh_price(key):
    try:
        url = "https://nerkh.jahankhahan.shop/data/live.json"
        response = requests.get(url, timeout=10)
        if response.status_code == 200:
            data = response.json()
            # این API قیمت‌ها رو داخل کلید rates برمی‌گردونه
            price = data['rates'].get(key, {}).get('price')
            if price:
                return f"{price:,.0f}"
        return "نامشخص"
    except Exception as e:
        print(f"Error fetching {key}: {e}")
        return "خطا"

# تابع گرفتن قیمت تتر از نوبیتکس (این همون قبلیه که درست کار می‌کرد)
def get_usdt_nobitex():
    try:
        url = "https://apiv2.nobitex.ir/market/stats?srcCurrency=usdt&dstCurrency=rls"
        response = requests.get(url, timeout=10)
        if response.status_code == 200:
            data = response.json()
            price_rial = float(data['stats']['usdt-rls']['latest'])
            price_toman = int(price_rial / 10)
            return f"{price_toman:,.0f}"
        return "نامشخص"
    except Exception as e:
        print(f"Error fetching Nobitex USDT: {e}")
        return "خطا"

async def send_prices_to_chat(context: ContextTypes.DEFAULT_TYPE, chat_id):
    try:
        # گرفتن قیمت‌ها از API جدید
        usd = get_nerkh_price("dollar")
        gold18 = get_nerkh_price("gold_18k")
        coin_emami = get_nerkh_price("sekke_emami")
        
        # گرفتن تتر از نوبیتکس
        usdt = get_usdt_nobitex()

        msg = f"""💰 **قیمت لحظه‌ای**

💵 قیمت دلار = `{usd}` تومان
🟢 قیمت تتر (نوبیتکس) = `{usdt}` تومان
🌕 قیمت طلا ۱۸ عیار = `{gold18}` تومان
🪙 سکه امامی = `{coin_emami}` تومان

*💰 قیمت لحظه‌ای دیجی نوسان 👇*
@diginavasan"""
        await context.bot.send_message(chat_id=chat_id, text=msg, parse_mode='Markdown')
    except Exception as e:
        print(f"❌ خطا در ساخت گزارش: {e}")

async def start(update, context):
    await context.bot.send_message(chat_id=update.effective_chat.id, text="سلام! ربات دیجی نوسان با موفقیت فعال شد. ✅\nبرای دریافت قیمت‌ها، دستور /price را بزنید.")
    
async def price(update, context):
    await send_prices_to_chat(context, update.effective_chat.id)

if __name__ == '__main__':
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler('start', start))
    app.add_handler(CommandHandler('price', price))
    
    print("ربات روشن شد...")
    app.run_polling()
