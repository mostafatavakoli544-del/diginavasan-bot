import os
import logging
import requests
import threading
import http.server
import socketserver
import pytz
import jdatetime
import urllib.parse
from hijri_converter import convert
from datetime import datetime, time
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, ContextTypes, CommandHandler, 
    ConversationHandler, MessageHandler, filters
)
from supabase import create_client, Client

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

TOKEN = os.environ.get("BOT_TOKEN")
PORT = int(os.environ.get("PORT", 10000))
CHANNEL_ID = "@diginavasan"
ADMIN_ID = 718840144

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

TEMPLATES = {
    "fiat_title": "💱 **قیمت لحظه‌ای ارزها**",
    "gold_title": "🌕 **قیمت لحظه‌ای طلا و سکه**",
    "crypto_title": "🪙 **قیمت لحظه‌ای ارز دیجیتال**",
    "footer": "\n*💰 قیمت لحظه‌ای دیجی نوسان 👇*\n@diginavasan"
}

# ==========================================
# ⚙️ توابع پایه
# ==========================================
def run_dummy_server():
    Handler = http.server.SimpleHTTPRequestHandler
    with socketserver.TCPServer(("", PORT), Handler) as httpd:
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

def get_nerkh_price(key):
    try:
        url = "https://nerkh.jahankhahan.shop/data/live.json"
        response = requests.get(url, timeout=10)
        if response.status_code == 200:
            data = response.json()
            price = data['rates'].get(key)
            if price:
                return int(price)
        return 0
    except Exception:
        return 0

def get_nerkh_price_safe(keys_list):
    for key in keys_list:
        price = get_nerkh_price(key)
        if price > 0:
            return f"{price:,.0f}"
    return "0"

def get_crypto_prices_toman(usd_price_float):
    if usd_price_float <= 0:
        return "نامشخص", "نامشخص", "نامشخص"
    
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
    
    try:
        url = "https://api.coingecko.com/api/v3/simple/price?ids=bitcoin,ethereum,tether&vs_currencies=usd"
        res = requests.get(url, headers=headers, timeout=10)
        if res.status_code == 200:
            data = res.json()
            btc = int(data['bitcoin']['usd'] * usd_price_float)
            eth = int(data['ethereum']['usd'] * usd_price_float)
            usdt = int(data['tether']['usd'] * usd_price_float)
            return f"{btc:,.0f}", f"{eth:,.0f}", f"{usdt:,.0f}"
    except Exception:
        pass

    try:
        def get_paprika(coin_id):
            url = f"https://api.coinpaprika.com/v1/tickers/{coin_id}"
            res = requests.get(url, headers=headers, timeout=10)
            if res.status_code == 200:
                return float(res.json()['quotes']['USD']['price'])
            return 0
            
        btc_usd = get_paprika("btc-bitcoin")
        eth_usd = get_paprika("eth-ethereum")
        usdt_usd = get_paprika("usdt-tether")
        
        if btc_usd > 0:
            btc = int(btc_usd * usd_price_float)
            eth = int(eth_usd * usd_price_float)
            usdt = int(usdt_usd * usd_price_float)
            return f"{btc:,.0f}", f"{eth:,.0f}", f"{usdt:,.0f}"
    except Exception:
        pass

    return "خطا", "خطا", "خطا"

# ==========================================
# ⚙️ ساخت پیام‌ها
# ==========================================
def generate_fiat_msg():
    _, _, _, _, t, _ = get_dates_info()
    usd = get_nerkh_price_safe(["dollar", "usd"])
    eur = get_nerkh_price_safe(["euro", "eur"])
    gbp = get_nerkh_price_safe(["pound", "gbp"])
    aed = get_nerkh_price_safe(["aed"])
    try_price = get_nerkh_price_safe(["lira"])
    cny = get_nerkh_price_safe(["yuan"])
    
    return f"""{TEMPLATES['fiat_title']}
⏰ ساعت آپدیت: \u200e`{t}`

💵 دلار = \u200e`{usd}` تومان
💶 یورو = \u200e`{eur}` تومان
💷 پوند = \u200e`{gbp}` تومان
🇦🇪 درهم = \u200e`{aed}` تومان
🇹🇷 لیر = \u200e`{try_price}` تومان
🇨🇳 یوان = \u200e`{cny}` تومان
{TEMPLATES['footer']}"""

def generate_gold_msg():
    _, _, _, _, t, _ = get_dates_info()
    gold18 = get_nerkh_price_safe(["gold"])
    coin_emami = get_nerkh_price_safe(["coin"])
    coin_bahar = get_nerkh_price_safe(["bahar"])
    coin_nim = get_nerkh_price_safe(["half"])
    coin_rob = get_nerkh_price_safe(["quarter"])
    
    return f"""{TEMPLATES['gold_title']}
⏰ ساعت آپدیت: \u200e`{t}`

🌕 طلا ۱۸ عیار = \u200e`{gold18}` تومان
🪙 سکه امامی = \u200e`{coin_emami}` تومان
🪙 سکه بهار = \u200e`{coin_bahar}` تومان
🪙 نیم سکه = \u200e`{coin_nim}` تومان
🪙 ربع سکه = \u200e`{coin_rob}` تومان
{TEMPLATES['footer']}"""

def generate_crypto_msg():
    _, _, _, _, t, _ = get_dates_info()
    usd_str = get_nerkh_price_safe(["dollar", "usd"])
    usd_float = float(usd_str.replace(',', '')) if usd_str != "0" else 0
    
    btc, eth, usdt = get_crypto_prices_toman(usd_float)
    
    return f"""{TEMPLATES['crypto_title']}
⏰ ساعت آپدیت: \u200e`{t}`

🟠 بیت‌کوین (BTC) = \u200e`{btc}` تومان
🔷 اتریوم (ETH) = \u200e`{eth}` تومان
🟢 تتر (USDT) = \u200e`{usdt}` تومان
{TEMPLATES['footer']}"""

# ==========================================
# ⚙️ ارسال خودکار به کانال
# ==========================================
async def send_fiat_to_channel(context: ContextTypes.DEFAULT_TYPE):
    await context.bot.send_message(chat_id=CHANNEL_ID, text=generate_fiat_msg(), parse_mode='Markdown')

async def send_gold_to_channel(context: ContextTypes.DEFAULT_TYPE):
    await context.bot.send_message(chat_id=CHANNEL_ID, text=generate_gold_msg(), parse_mode='Markdown')

async def send_crypto_to_channel(context: ContextTypes.DEFAULT_TYPE):
    await context.bot.send_message(chat_id=CHANNEL_ID, text=generate_crypto_msg(), parse_mode='Markdown')

async def send_morning_message(context: ContextTypes.DEFAULT_TYPE):
    day, shamsi, hijri, greg, t, _ = get_dates_info()
    msg = f"""🌅 **بسم الله الرحمن الرحیم**
صبح بخیر! بازار امروز آغاز شد. ☀️

📅 امروز: **{day}**
🗓 شمسی: \u200e`{shamsi}`
🌙 قمری: \u200e`{hijri}`
📆 میلادی: \u200e`{greg}`
⏰ ساعت: \u200e`{t}`

*💰 دیجی نوسان 👇*
@diginavasan"""
    await context.bot.send_message(chat_id=CHANNEL_ID, text=msg, parse_mode='Markdown')

async def send_night_message(context: ContextTypes.DEFAULT_TYPE):
    msg = f"""🌙 **پایان نرخ بازار**
بازار تعطیل شد. شب خوبی رو برای شما و خانواده‌تون آرزو می‌کنیم. 🌹

*💰 دیجی نوسان 👇*
@diginavasan"""
    await context.bot.send_message(chat_id=CHANNEL_ID, text=msg, parse_mode='Markdown')

_rotation_index = 0
async def rotate_jobs(context: ContextTypes.DEFAULT_TYPE):
    if not is_working_hours(): return
    global _rotation_index
    jobs = [send_fiat_to_channel, send_gold_to_channel, send_crypto_to_channel]
    current_job = jobs[_rotation_index % len(jobs)]
    _rotation_index += 1
    await current_job(context)

# ==========================================
# ⚙️ منوی اصلی
# ==========================================
def get_main_menu():
    keyboard = [
        [KeyboardButton("🚀 استارت / منوی اصلی")],
        [KeyboardButton("💱 قیمت ارزها"), KeyboardButton("🌕 قیمت طلا و سکه")],
        [KeyboardButton("🪙 قیمت ارز دیجیتال"), KeyboardButton("🧮 ماشین‌حساب طلا")],
        [KeyboardButton("🎁 دعوت از دوستان")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, is_persistent=True)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    first_name = update.effective_user.first_name or "کاربر"
    
    invite_count = 0
    try:
        response = supabase.table('users').select('*').eq('user_id', user_id).execute()
        
        if not response.data:
            invited_by = None
            if context.args:
                try:
                    invited_by = int(context.args[0])
                except ValueError:
                    pass
            
            supabase.table('users').insert({"user_id": user_id, "invited_by": invited_by}).execute()
            
            if invited_by:
                inviter_res = supabase.table('users').select('invite_count').eq('user_id', invited_by).execute()
                if inviter_res.data:
                    new_count = inviter_res.data[0]['invite_count'] + 1
                    supabase.table('users').update({"invite_count": new_count}).eq('user_id', invited_by).execute()
                    try:
                        await context.bot.send_message(chat_id=invited_by, text=f"🎉 یک کاربر جدید با لینک شما به ربات پیوست!\nتعداد کل دعوت‌های شما: {new_count}")
                    except:
                        pass
        else:
            invite_count = response.data[0].get('invite_count', 0)
    except Exception as e:
        print(f"DB Error: {e}")

    welcome_msg = f"""سلام **{first_name}** عزیز! به ربات دیجی نوسان خوش آمدید. ✅

👥 تعداد دعوت‌های شما تاکنون: **{invite_count}** نفر

👇 **از منوی زیر (پایین صفحه) یکی از گزینه‌ها را انتخاب کنید:**"""
    await context.bot.send_message(chat_id=user_id, text=welcome_msg, parse_mode='Markdown', reply_markup=get_main_menu())

# ==========================================
# ⚙️ مدیریت پیام‌های متنی
# ==========================================
async def handle_menu_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id

    if "استارت" in text or "منوی اصلی" in text:
        await start(update, context)
        return

    if "قیمت ارزها" in text:
        await context.bot.send_message(chat_id=chat_id, text=generate_fiat_msg(), parse_mode='Markdown')
    elif "قیمت طلا و سکه" in text:
        await context.bot.send_message(chat_id=chat_id, text=generate_gold_msg(), parse_mode='Markdown')
    elif "قیمت ارز دیجیتال" in text:
        await context.bot.send_message(chat_id=chat_id, text=generate_crypto_msg(), parse_mode='Markdown')
    elif "ماشین‌حساب طلا" in text:
        await gold_calc_start(update, context)
    elif "دعوت از دوستان" in text:
        try:
            invite_link = f"https://t.me/diginavasan_bot?start={user_id}"
            
            invite_count = 0
            try:
                res = supabase.table('users').select('invite_count').eq('user_id', user_id).execute()
                invite_count = res.data[0].get('invite_count', 0) if res.data else 0
            except Exception:
                pass
                
            invite_text = f"""🎁 **دعوت از دوستان به دیجی نوسان** 🌟

👥 تعداد افرادی که تاکنون دعوت کرده‌اید: **{invite_count}** نفر

🔗 **لینک اختصاصی دعوت شما:**
{invite_link}

برای دعوت دوستانتان، از دکمه زیر استفاده کنید:"""
            
            share_text = "سلام! به کانال دیجی نوسان بپیوندید تا از قیمت‌های لحظه‌ای بازار باخبر شوید. ربات اختصاصی ما هم امکانات ویژه‌ای داره."
            
            # ✅ اصلاح نهایی: استفاده از urlencode برای جلوگیری از خرابی لینک
            params = {"url": invite_link, "text": share_text}
            share_url = f"https://t.me/share/url?{urllib.parse.urlencode(params)}"
            
            keyboard = [[InlineKeyboardButton("📤 ارسال به مخاطبین", url=share_url)]]
            reply_markup = InlineKeyboardMarkup(keyboard)

            await context.bot.send_message(chat_id=chat_id, text=invite_text, parse_mode='Markdown', reply_markup=reply_markup)
        except Exception as e:
            print(f"Error in invite: {e}")
            await context.bot.send_message(chat_id=chat_id, text="خطایی در ساخت لینک دعوت رخ داد. لطفاً بعداً تلاش کنید.")

# ==========================================
# 🧮 ماشین‌حساب طلا
# ==========================================
PRICE, WEIGHT, AJRAT, PROFIT, TAX = range(5)

async def gold_calc_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id

    gold_price = get_nerkh_price_safe(["gold"])
    await context.bot.send_message(
        chat_id=chat_id,
        text=f"🧮 **ماشین‌حساب طلا**\n\n"
             f"لطفاً قیمت هر گرم طلای ۱۸ عیار را **به تومان** وارد کنید:\n"
             f"(راهنما: قیمت لحظه‌ای بازار \u200e`{gold_price}` تومان است)",
        parse_mode='Markdown'
    )
    return PRICE

async def get_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if "استارت" in update.message.text or "منوی اصلی" in update.message.text or "دعوت" in update.message.text:
        return ConversationHandler.END
    try:
        context.user_data['price'] = float(update.message.text.replace(',', ''))
        await update.message.reply_text("چند گرم طلا می‌خواهید بخرید؟ (مثلاً 5)")
        return WEIGHT
    except ValueError:
        await update.message.reply_text("لطفاً فقط عدد وارد کنید. قیمت را دوباره بزنید:")
        return PRICE

async def get_weight(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        context.user_data['weight'] = float(update.message.text.replace(',', ''))
        await update.message.reply_text("درصد اجرت ساخت چقدر است؟ (اگر بدون اجرت است 0 وارد کنید)")
        return AJRAT
    except ValueError:
        await update.message.reply_text("لطفاً فقط عدد وارد کنید. وزن را دوباره بزنید:")
        return WEIGHT

async def get_ajrat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        context.user_data['ajrat'] = float(update.message.text.replace(',', ''))
        await update.message.reply_text("درصد سود فروشنده چقدر است؟ (معمولاً 7)")
        return PROFIT
    except ValueError:
        await update.message.reply_text("لطفاً فقط عدد وارد کنید. درصد اجرت را دوباره بزنید:")
        return AJRAT

async def get_profit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        context.user_data['profit'] = float(update.message.text.replace(',', ''))
        await update.message.reply_text("درصد مالیات بر ارزش افزوده چقدر است؟ (اگر معاف است 0 وارد کنید، معمولاً 9)")
        return TAX
    except ValueError:
        await update.message.reply_text("لطفاً فقط عدد وارد کنید. درصد سود را دوباره بزنید:")
        return PROFIT

async def get_tax(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        context.user_data['tax'] = float(update.message.text.replace(',', ''))
        
        price = context.user_data['price']
        weight = context.user_data['weight']
        ajrat_pct = context.user_data['ajrat']
        profit_pct = context.user_data['profit']
        tax_pct = context.user_data['tax']
        
        base_price = price * weight
        ajrat_amount = base_price * (ajrat_pct / 100)
        profit_amount = (base_price + ajrat_amount) * (profit_pct / 100)
        tax_amount = (ajrat_amount + profit_amount) * (tax_pct / 100)
        
        final_price = base_price + ajrat_amount + profit_amount + tax_amount
        price_per_gram = final_price / weight if weight > 0 else 0
        
        result_msg = f"""✅ **فاکتور محاسبه طلا**

⚖️ وزن: \u200e`{weight}` گرم
💰 قیمت پایه هر گرم: \u200e`{price:,.0f}` تومان
💵 قیمت پایه کل: \u200e`{base_price:,.0f}` تومان

🛠 اجرت ساخت ({ajrat_pct}%): \u200e`{ajrat_amount:,.0f}` تومان
📈 سود فروشنده ({profit_pct}%): \u200e`{profit_amount:,.0f}` تومان
🧾 مالیات ({tax_pct}%): \u200e`{tax_amount:,.0f}` تومان

➖➖➖➖➖➖➖➖
💳 **قیمت نهایی: \u200e`{final_price:,.0f}` تومان**
⚖️ **قیمت نهایی هر گرم: \u200e`{price_per_gram:,.0f}` تومان**

*💰 دیجی نوسان 👇*
@diginavasan"""
        await update.message.reply_text(result_msg, parse_mode='Markdown', reply_markup=get_main_menu())
        return ConversationHandler.END
    except ValueError:
        await update.message.reply_text("لطفاً فقط عدد وارد کنید. درصد مالیات را دوباره بزنید:")
        return TAX

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("ماشین‌حساب لغو شد.", reply_markup=get_main_menu())
    return ConversationHandler.END

# ==========================================
# ⚙️ دستور آمار
# ==========================================
async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    
    try:
        res = supabase.table('users').select('*').execute()
        total_users = len(res.data)
        
        total_invites = 0
        for user in res.data:
            total_invites += user.get('invite_count', 0)
            
        stats_msg = f"""📊 **آمار ربات دیجی نوسان**

👥 تعداد کل کاربران: **{total_users}** نفر
🎁 تعداد کل دعوت‌های موفق: **{total_invites}** نفر"""
        await update.message.reply_text(stats_msg, parse_mode='Markdown')
    except Exception as e:
        await update.message.reply_text(f"خطا در خواندن دیتابیس: {e}")

# ==========================================
# ⚙️ اجرای ربات
# ==========================================
if __name__ == '__main__':
    threading.Thread(target=run_dummy_server, daemon=True).start()
    
    app = ApplicationBuilder().token(TOKEN).build()
    
    gold_conv_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("ماشین‌حساب طلا"), gold_calc_start)],
        states={
            PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_price)],
            WEIGHT: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_weight)],
            AJRAT: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_ajrat)],
            PROFIT: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_profit)],
            TAX: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_tax)],
        },
        fallbacks=[
            CommandHandler('cancel', cancel),
            MessageHandler(filters.Regex("^(🚀 استارت / منوی اصلی|🎁 دعوت از دوستان|💱 قیمت ارزها|🌕 قیمت طلا و سکه|🪙 قیمت ارز دیجیتال)$"), handle_menu_text)
        ],
        allow_reentry=True
    )
    
    app.add_handler(gold_conv_handler)
    app.add_handler(CommandHandler('start', start))
    app.add_handler(CommandHandler('stats', admin_stats))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_menu_text))
    
    tehran_tz = pytz.timezone('Asia/Tehran')
    app.job_queue.run_daily(send_morning_message, time=time(8, 0, tzinfo=tehran_tz))
    app.job_queue.run_daily(send_night_message, time=time(20, 0, tzinfo=tehran_tz))
    
    app.job_queue.run_repeating(rotate_jobs, interval=900, first=10)
    
    print("ربات روشن شد. باگ لینک دعوت رفع شد...")
    app.run_polling()
