import os
import logging
import requests
import threading
import http.server
import socketserver
import pytz
import jdatetime
import urllib.parse
from io import BytesIO
from hijri_converter import convert
from datetime import datetime, time
from PIL import Image, ImageDraw, ImageFont
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
OIL_API_KEY = os.environ.get("OIL_API_KEY", "") # کلید رایگان OilPriceAPI را اینجا یا در Render تنظیم کن

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

TEMPLATES = {
    "fiat_title": "💱 قیمت لحظه‌ای ارزها",
    "gold_title": "🌕 قیمت لحظه‌ای طلا و سکه",
    "crypto_title": "🪙 قیمت لحظه‌ای ارز دیجیتال",
    "footer": "\n💰 قیمت لحظه‌ای دیجی نوسان 👇\n@diginavasan"
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

def get_crypto_prices_toman():
    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        # لیست گسترده‌تر ارزهای دیجیتال
        ids = "bitcoin,ethereum,tether,binancecoin,solana,ripple,dogecoin"
        url = f"https://api.coingecko.com/api/v3/simple/price?ids={ids}&vs_currencies=usd"
        res = requests.get(url, headers=headers, timeout=10)
        if res.status_code == 200:
            data = res.json()
            usd_str = get_nerkh_price_safe(["dollar", "usd"])
            usd_float = float(usd_str.replace(',', '')) if usd_str != "0" else 0
            
            if usd_float > 0:
                prices = {}
                for coin_id, coin_name in [("bitcoin", "BTC"), ("ethereum", "ETH"), ("tether", "USDT"), 
                                           ("binancecoin", "BNB"), ("solana", "SOL"), ("ripple", "XRP"), ("dogecoin", "DOGE")]:
                    usd_val = data[coin_id]['usd']
                    prices[coin_name] = f"{int(usd_val * usd_float):,.0f}"
                return prices
    except Exception as e:
        print(f"Crypto error: {e}")
    return {}

def get_oil_price():
    if not OIL_API_KEY:
        return "کلید API نفت تنظیم نشده"
    try:
        headers = {'Authorization': f'Token {OIL_API_KEY}'}
        url = "https://api.oilpriceapi.com/v1/prices/latest?by_code=BRENT_CRUDE_USD"
        res = requests.get(url, headers=headers, timeout=10)
        if res.status_code == 200:
            data = res.json()
            return f"${data['data']['price']:.2f}"
        return "خطا در دریافت"
    except Exception:
        return "خطا"

# ==========================================
# 🎨 تابع ساخت عکس اینفوگرافیک
# ==========================================
def generate_infographic(title, data_lines):
    # ابعاد عکس (مناسب تلگرام)
    width, height = 800, 600
    # رنگ‌ها
    bg_color = (15, 25, 45) # سرمه‌ای تیره
    title_color = (255, 215, 0) # طلایی
    text_color = (255, 255, 255) # سفید
    accent_color = (50, 150, 255) # آبی
    
    img = Image.new('RGB', (width, height), color=bg_color)
    draw = ImageDraw.Draw(img)
    
    # تلاش برای استفاده از فونت پیش‌فرض یا فونت فارسی (در صورت وجود)
    try:
        # در صورت آپلود فونت فارسی مثل Vazirmatn، مسیر آن را اینجا بده
        font_title = ImageFont.truetype("arial.ttf", 40) 
        font_text = ImageFont.truetype("arial.ttf", 30)
    except IOError:
        font_title = ImageFont.load_default()
        font_text = ImageFont.load_default()

    # رسم خط بالایی (تزئینی)
    draw.line([(50, 50), (width-50, 50)], fill=accent_color, width=3)
    
    # نوشتن تایتل
    draw.text((50, 70), title, font=font_title, fill=title_color)
    
    # نوشتن تاریخ
    _, _, _, greg, t, _ = get_dates_info()
    draw.text((50, 130), f"⏰ آخرین آپدیت: {t}", font=font_text, fill=accent_color)
    
    # رسم خطوط داده‌ها
    y_offset = 200
    for line in data_lines:
        draw.text((50, y_offset), line, font=font_text, fill=text_color)
        y_offset += 45
        
    # رسم فوتر
    draw.line([(50, height-80), (width-50, height-80)], fill=accent_color, width=3)
    draw.text((50, height-60), "@diginavasan | دیجی نوسان", font=font_text, fill=title_color)
    
    # ذخیره در حافظه
    bio = BytesIO()
    bio.name = 'infographic.jpeg'
    img.save(bio, 'JPEG')
    bio.seek(0)
    return bio

# ==========================================
# ⚙️ ارسال به کانال (متنی و عکسی)
# ==========================================
async def send_fiat_to_channel(context: ContextTypes.DEFAULT_TYPE):
    usd = get_nerkh_price_safe(["dollar", "usd"])
    eur = get_nerkh_price_safe(["euro", "eur"])
    gbp = get_nerkh_price_safe(["pound", "gbp"])
    aed = get_nerkh_price_safe(["aed"])
    try_price = get_nerkh_price_safe(["lira"])
    cny = get_nerkh_price_safe(["yuan"])
    cad = get_nerkh_price_safe(["cad"])
    aud = get_nerkh_price_safe(["aud"])
    jpy = get_nerkh_price_safe(["jpy"])
    
    lines = [
        f"💵 دلار: {usd} تومان",
        f"💶 یورو: {eur} تومان",
        f"💷 پوند: {gbp} تومان",
        f"🇦🇪 درهم: {aed} تومان",
        f"🇹🇷 لیر: {try_price} تومان",
        f"🇨🇳 یوان: {cny} تومان",
        f"🇨🇦 دلار کانادا: {cad} تومان",
        f"🇦🇺 دلار استرالیا: {aud} تومان",
    ]
    
    # ارسال عکس
    img_bio = generate_infographic("💱 قیمت لحظه‌ای ارزها", lines)
    await context.bot.send_photo(chat_id=CHANNEL_ID, photo=img_bio, caption="📊 @diginavasan")

async def send_gold_to_channel(context: ContextTypes.DEFAULT_TYPE):
    gold18 = get_nerkh_price_safe(["gold"])
    coin_emami = get_nerkh_price_safe(["coin"])
    coin_bahar = get_nerkh_price_safe(["bahar"])
    coin_nim = get_nerkh_price_safe(["half"])
    coin_rob = get_nerkh_price_safe(["quarter"])
    
    lines = [
        f"🌕 طلا ۱۸ عیار: {gold18} تومان",
        f"🪙 سکه امامی: {coin_emami} تومان",
        f"🪙 سکه بهار: {coin_bahar} تومان",
        f"🪙 نیم سکه: {coin_nim} تومان",
        f"🪙 ربع سکه: {coin_rob} تومان",
    ]
    
    img_bio = generate_infographic("🌕 قیمت لحظه‌ای طلا و سکه", lines)
    await context.bot.send_photo(chat_id=CHANNEL_ID, photo=img_bio, caption="📊 @diginavasan")

async def send_crypto_to_channel(context: ContextTypes.DEFAULT_TYPE):
    crypto_prices = get_crypto_prices_toman()
    lines = []
    if crypto_prices:
        for symbol, price in crypto_prices.items():
            lines.append(f"🪙 {symbol}: {price} تومان")
    else:
        lines.append("⚠️ خطا در دریافت قیمت ارزهای دیجیتال")
        
    img_bio = generate_infographic("🪙 قیمت لحظه‌ای ارز دیجیتال", lines)
    await context.bot.send_photo(chat_id=CHANNEL_ID, photo=img_bio, caption="📊 @diginavasan")

async def send_oil_to_channel(context: ContextTypes.DEFAULT_TYPE):
    oil_price = get_oil_price()
    lines = [f"🛢 نفت برنت: {oil_price} دلار"]
    
    img_bio = generate_infographic("🛢 قیمت نفت", lines)
    await context.bot.send_photo(chat_id=CHANNEL_ID, photo=img_bio, caption="📊 @diginavasan")

async def send_morning_message(context: ContextTypes.DEFAULT_TYPE):
    day, shamsi, hijri, greg, t, _ = get_dates_info()
    msg = f"""🌅 <b>بسم الله الرحمن الرحیم</b>
صبح بخیر! بازار امروز آغاز شد. ☀️

📅 امروز: <b>{day}</b>
🗓 شمسی: {shamsi}
🌙 قمری: {hijri}
📆 میلادی: {greg}
⏰ ساعت: {t}

💰 دیجی نوسان 👇
@diginavasan"""
    await context.bot.send_message(chat_id=CHANNEL_ID, text=msg, parse_mode='HTML')

async def send_night_message(context: ContextTypes.DEFAULT_TYPE):
    msg = f"""🌙 <b>پایان نرخ بازار</b>
بازار تعطیل شد. شب خوبی رو برای شما و خانواده‌تون آرزو می‌کنیم. 🌹

💰 دیجی نوسان 👇
@diginavasan"""
    await context.bot.send_message(chat_id=CHANNEL_ID, text=msg, parse_mode='HTML')

_rotation_index = 0
async def rotate_jobs(context: ContextTypes.DEFAULT_TYPE):
    if not is_working_hours(): return
    global _rotation_index
    # چرخش بین ۴ تصویر
    jobs = [send_fiat_to_channel, send_gold_to_channel, send_crypto_to_channel, send_oil_to_channel]
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
                try: invited_by = int(context.args[0])
                except ValueError: pass
            supabase.table('users').insert({"user_id": user_id, "invited_by": invited_by}).execute()
        else:
            invite_count = response.data[0].get('invite_count', 0)
    except Exception as e:
        print(f"DB Error: {e}")

    welcome_msg = f"""سلام <b>{first_name}</b> عزیز! به ربات دیجی نوسان خوش آمدید. ✅

👥 تعداد دعوت‌های شما تاکنون: <b>{invite_count}</b> نفر

👇 از منوی زیر یکی از گزینه‌ها را انتخاب کنید:"""
    await context.bot.send_message(chat_id=user_id, text=welcome_msg, parse_mode='HTML', reply_markup=get_main_menu())

# ==========================================
# ⚙️ مدیریت منو
# ==========================================
async def handle_menu_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id

    if "استارت" in text or "منوی اصلی" in text:
        await start(update, context)
        return

    if "قیمت ارزها" in text:
        usd = get_nerkh_price_safe(["dollar", "usd"])
        eur = get_nerkh_price_safe(["euro", "eur"])
        gbp = get_nerkh_price_safe(["pound", "gbp"])
        aed = get_nerkh_price_safe(["aed"])
        try_price = get_nerkh_price_safe(["lira"])
        cny = get_nerkh_price_safe(["yuan"])
        cad = get_nerkh_price_safe(["cad"])
        aud = get_nerkh_price_safe(["aud"])
        lines = [f"💵 دلار: {usd}", f"💶 یورو: {eur}", f"💷 پوند: {gbp}", f"🇦🇪 درهم: {aed}", 
                 f"🇹🇷 لیر: {try_price}", f"🇨🇳 یوان: {cny}", f"🇨🇦 دلار کانادا: {cad}", f"🇦🇺 دلار استرالیا: {aud}"]
        img_bio = generate_infographic("💱 قیمت لحظه‌ای ارزها", lines)
        await context.bot.send_photo(chat_id=chat_id, photo=img_bio)
    elif "قیمت طلا و سکه" in text:
        gold18 = get_nerkh_price_safe(["gold"])
        coin_emami = get_nerkh_price_safe(["coin"])
        coin_bahar = get_nerkh_price_safe(["bahar"])
        coin_nim = get_nerkh_price_safe(["half"])
        coin_rob = get_nerkh_price_safe(["quarter"])
        lines = [f"🌕 طلا ۱۸: {gold18}", f"🪙 سکه امامی: {coin_emami}", f"🪙 سکه بهار: {coin_bahar}", f"🪙 نیم: {coin_nim}", f"🪙 ربع: {coin_rob}"]
        img_bio = generate_infographic("🌕 قیمت طلا و سکه", lines)
        await context.bot.send_photo(chat_id=chat_id, photo=img_bio)
    elif "قیمت ارز دیجیتال" in text:
        crypto_prices = get_crypto_prices_toman()
        lines = [f"🪙 {sym}: {prc}" for sym, prc in crypto_prices.items()] if crypto_prices else ["خطا در دریافت"]
        img_bio = generate_infographic("🪙 قیمت ارز دیجیتال", lines)
        await context.bot.send_photo(chat_id=chat_id, photo=img_bio)
    elif "ماشین‌حساب طلا" in text:
        await gold_calc_start(update, context)
    elif "دعوت از دوستان" in text:
        try:
            invite_link = f"https://t.me/diginavasan_bot?start={user_id}"
            invite_count = 0
            try:
                res = supabase.table('users').select('invite_count').eq('user_id', user_id).execute()
                invite_count = res.data[0].get('invite_count', 0) if res.data else 0
            except Exception: pass
                
            invite_text = f"""🎁 <b>دعوت از دوستان به دیجی نوسان</b> 🌟

👥 تعداد افرادی که تاکنون دعوت کرده‌اید: <b>{invite_count}</b> نفر

🔗 <b>لینک اختصاصی دعوت شما:</b>
{invite_link}

برای دعوت دوستانتان، از دکمه زیر استفاده کنید:"""
            
            share_text = f"""سلام! خواستم این کانال و ربات فوق‌العاده رو بهت معرفی کنم. 🤝

📢 کانال دیجی نوسان:
ما در این کانال قیمت‌های لحظه‌ای بازار (دلار، طلا، سکه، ارز دیجیتال و بورس) رو به صورت خودکار و منظم به‌روزرسانی می‌کنیم. برای اینکه همیشه از نرخ‌های بازار باخبر باشی، حتماً توی کانال ما عضو شو:
👉 https://t.me/diginavasan

🤖 ربات اختصاصی دیجی نوسان:
علاوه بر کانال، ما یک ربات هم داریم که امکانات خیلی بیشتری مثل «ماشین‌حساب طلا»، محاسبه اقساط و ده‌ها ابزار دیگه رو به صورت رایگان در اختیارت می‌ذاره. اگه دوست داشتی، می‌تونی از ربات ما هم استفاده کنی و دستور /start رو بزنی:
👉 {invite_link}"""
            
            encoded_text = urllib.parse.quote(share_text)
            share_url = f"https://t.me/share/url?url={invite_link}&text={encoded_text}"
            
            keyboard = [[InlineKeyboardButton("📤 ارسال به مخاطبین", url=share_url)]]
            reply_markup = InlineKeyboardMarkup(keyboard)

            await context.bot.send_message(chat_id=chat_id, text=invite_text, parse_mode='HTML', reply_markup=reply_markup)
        except Exception as e:
            await context.bot.send_message(chat_id=chat_id, text=f"❌ خطای فنی: {e}")

# ==========================================
# 🧮 ماشین‌حساب طلا
# ==========================================
PRICE, WEIGHT, AJRAT, PROFIT, TAX = range(5)

async def gold_calc_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    gold_price = get_nerkh_price_safe(["gold"])
    await context.bot.send_message(
        chat_id=chat_id,
        text=f"🧮 <b>ماشین‌حساب طلا</b>\n\nلطفاً قیمت هر گرم طلای ۱۸ عیار را <b>به تومان</b> وارد کنید:\n(راهنما: قیمت لحظه‌ای بازار {gold_price} تومان است)",
        parse_mode='HTML'
    )
    return PRICE

async def get_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
        
        result_msg = f"""✅ <b>فاکتور محاسبه طلا</b>

⚖️ وزن: {weight} گرم
💰 قیمت پایه هر گرم: {price:,.0f} تومان
💵 قیمت پایه کل: {base_price:,.0f} تومان

🛠 اجرت ساخت ({ajrat_pct}%): {ajrat_amount:,.0f} تومان
📈 سود فروشنده ({profit_pct}%): {profit_amount:,.0f} تومان
🧾 مالیات ({tax_pct}%): {tax_amount:,.0f} تومان

➖➖➖➖➖➖➖➖
💳 <b>قیمت نهایی: {final_price:,.0f} تومان</b>
⚖️ <b>قیمت نهایی هر گرم: {price_per_gram:,.0f} تومان</b>

💰 دیجی نوسان 👇
@diginavasan"""
        await update.message.reply_text(result_msg, parse_mode='HTML', reply_markup=get_main_menu())
        return ConversationHandler.END
    except ValueError:
        await update.message.reply_text("لطفاً فقط عدد وارد کنید. درصد مالیات را دوباره بزنید:")
        return TAX

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("ماشین‌حساب لغو شد.", reply_markup=get_main_menu())
    return ConversationHandler.END

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
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_menu_text))
    
    tehran_tz = pytz.timezone('Asia/Tehran')
    app.job_queue.run_daily(send_morning_message, time=time(8, 0, tzinfo=tehran_tz))
    app.job_queue.run_daily(send_night_message, time=time(20, 0, tzinfo=tehran_tz))
    
    # زمان‌بندی: هر ۹۰۰ ثانیه (۱۵ دقیقه) یک عکس اینفوگرافیک ارسال میشه
    app.job_queue.run_repeating(rotate_jobs, interval=900, first=10)
    
    print("ربات روشن شد. سیستم ارسال عکس و نفت فعال شد...")
    app.run_polling()
