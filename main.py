import os
import logging
import requests
import threading
import http.server
import socketserver
import pytz
import jdatetime
import urllib.parse
import arabic_reshaper
import re
import asyncio
from bidi.algorithm import get_display
from io import BytesIO
from hijri_converter import convert
from datetime import datetime, time
from PIL import Image, ImageDraw, ImageFont, features
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
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

TEMPLATES = {
    "fiat_title": "💱 **قیمت لحظه‌ای ارزها**",
    "gold_title": "🌕 **قیمت لحظه‌ای طلا و سکه**",
    "crypto_title": "🪙 **قیمت لحظه‌ای ارز دیجیتال**",
    "footer": "\n*💰 قیمت لحظه‌ای دیجی نوسان 👇*\n@diginavasan"
}

HAS_RAQM = features.check("raqm")
user_alerts = {}

def log_event(user_id, event_name):
    try:
        url = f"{SUPABASE_URL}/rest/v1/user_events"
        headers = {
            "apikey": SUPABASE_KEY,
            "Authorization": f"Bearer {SUPABASE_KEY}",
            "Content-Type": "application/json",
            "Prefer": "return=representation"
        }
        data = {"user_id": user_id, "event_name": event_name}
        requests.post(url, json=data, headers=headers, timeout=5)
    except Exception:
        pass

def run_dummy_server():
    Handler = http.server.SimpleHTTPRequestHandler
    with socketserver.TCPServer(("", PORT), Handler) as httpd:
        httpd.serve_forever()

def get_dates_info():
    tehran_tz = pytz.timezone('Asia/Tehran')
    now = datetime.now(tehran_tz)
    return now.strftime('%H:%M'), now.hour

def is_working_hours():
    _, hour = get_dates_info()
    return 8 <= hour < 20

def get_nerkh_price(key):
    try:
        response = requests.get("https://nerkh.jahankhahan.shop/data/live.json", timeout=10)
        if response.status_code == 200:
            price = response.json()['rates'].get(key)
            return int(price) if price else 0
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
        coins = {"BTC": "btc", "ETH": "eth", "USDT": "usdt", "BNB": "bnb"}
        prices = {}
        for symbol, coin_id in coins.items():
            url = f"https://apiv2.nobitex.ir/market/stats?srcCurrency={coin_id}&dstCurrency=rls"
            res = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=5)
            if res.status_code == 200:
                key = f"{coin_id}-rls"
                if key in res.json().get('stats', {}):
                    price_toman = int(float(res.json()['stats'][key]['latest']) / 10)
                    prices[symbol] = f"{price_toman:,.0f}"
        return prices
    except Exception:
        return {}

def download_image(url, size=(60, 60)):
    try:
        response = requests.get(url, timeout=5)
        img = Image.open(BytesIO(response.content)).convert("RGBA")
        return img.resize(size, Image.Resampling.LANCZOS)
    except Exception:
        return None

# ==========================================
# 🤖 تابع اتصال به هوش مصنوعی Groq
# ==========================================
def get_ai_response(prompt):
    try:
        url = "https://api.groq.com/openai/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {GROQ_API_KEY}",
            "Content-Type": "application/json"
        }
        data = {
            "model": "openai/gpt-oss-120b",
            "messages": [
                {"role": "system", "content": "You are a helpful assistant for a Persian Telegram bot named Diginavasan. Always answer in Persian."},
                {"role": "user", "content": prompt}
            ],
            "temperature": 0.7,
            "max_tokens": 1024
        }
        response = requests.post(url, headers=headers, json=data, timeout=30)
        if response.status_code == 200:
            return response.json()['choices'][0]['message']['content']
        else:
            return f"❌ خطا در ارتباط با هوش مصنوعی (کد {response.status_code}): {response.text}"
    except Exception as e:
        return f"❌ خطا: {str(e)}"

async def check_alerts(context: ContextTypes.DEFAULT_TYPE):
    global user_alerts
    for user_id in list(user_alerts.keys()):
        for alert in list(user_alerts[user_id]):
            asset = alert['asset']
            target = alert['target']
            current_price = 0
            
            if asset == 'دلار':
                current_price = int(get_nerkh_price_safe(["dollar"]).replace(',', ''))
            elif asset == 'طلا':
                current_price = int(get_nerkh_price_safe(["gold"]).replace(',', ''))
            elif asset == 'بیت‌کوین':
                crypto = get_crypto_prices_toman()
                if 'BTC' in crypto:
                    current_price = int(crypto['BTC'].replace(',', ''))
            
            if current_price > 0 and current_price >= target:
                try:
                    await context.bot.send_message(
                        chat_id=user_id,
                        text=f"🚨 <b>آلارم قیمتی شما فعال شد!</b>\n\nقیمت <b>{asset}</b> به {current_price:,.0f} تومان رسیده است (هدف شما: {target:,.0f} تومان).",
                        parse_mode='HTML'
                    )
                    user_alerts[user_id].remove(alert)
                except Exception:
                    pass
        
        if not user_alerts[user_id]:
            del user_alerts[user_id]

def create_gradient_bg(w, h):
    base = Image.new('RGB', (w, h), (15, 25, 45))
    top, bottom = (15, 25, 45), (5, 10, 20)
    draw = ImageDraw.Draw(base)
    for y in range(h):
        draw.line([(0, y), (w, y)], fill=(int(top[0] + (bottom[0] - top[0]) * y / h), int(top[1] + (bottom[1] - top[1]) * y / h), int(top[2] + (bottom[2] - top[2]) * y / h)))
    return base

def create_gold_gradient(w, h):
    base = Image.new('RGB', (w, h))
    draw = ImageDraw.Draw(base)
    left, right = (218, 165, 32), (184, 134, 11)
    for x in range(w):
        draw.line([(x, 0), (x, h)], fill=(int(left[0] + (right[0] - left[0]) * x / w), int(left[1] + (right[1] - left[1]) * x / w), int(left[2] + (right[2] - left[2]) * x / w)))
    return base

def generate_infographic():
    font_path = "vazir.ttf"
    if not os.path.exists(font_path):
        try:
            r = requests.get("https://cdn.jsdelivr.net/gh/rastikerdar/vazirmatn@v33.003/fonts/ttf/Vazirmatn-Bold.ttf", timeout=20)
            with open(font_path, 'wb') as f: f.write(r.content)
        except: pass

    w, h = 1080, 1920
    img = create_gradient_bg(w, h).convert("RGBA")
    overlay = Image.new('RGBA', img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    
    try:
        font_title = ImageFont.truetype(font_path, 45)
        font_text = ImageFont.truetype(font_path, 32) 
    except:
        font_title = font_text = ImageFont.load_default()

    def fa(text): return text if HAS_RAQM else get_display(arabic_reshaper.reshape(text))
    def draw_text(text, x, y, font, color, anchor, stroke=True):
        draw.text((x, y), text, font=font, fill=color, anchor=anchor, direction="rtl" if HAS_RAQM else "ltr", stroke_width=1 if stroke else 0, stroke_fill=(0,0,0))

    time_str, _ = get_dates_info()
    
    header_bg = create_gold_gradient(w-80, 120)
    mask = Image.new('L', (w-80, 120), 0)
    ImageDraw.Draw(mask).rounded_rectangle([(0, 0), (w-80, 120)], radius=20, fill=255)
    img.paste(header_bg, (40, 40), mask)
    draw.rounded_rectangle([(40, 40), (w-40, 160)], radius=20, outline=(255, 215, 0), width=3)
    draw_text("دیجی نوسان", w * 0.35, h * 0.05, font_title, (20, 30, 50), "mm", False)
    draw_text(time_str, w * 0.82, h * 0.05, font_text, (20, 30, 50), "mm", False)

    draw.rounded_rectangle([(40, 220), (480, 1850)], radius=30, fill=(255, 255, 255, 15), outline=(200, 160, 70, 200), width=2)
    draw.rounded_rectangle([(520, 220), (1040, 900)], radius=30, fill=(255, 255, 255, 15), outline=(200, 160, 70, 200), width=2)
    draw.rounded_rectangle([(520, 940), (1040, 1850)], radius=30, fill=(255, 255, 255, 15), outline=(200, 160, 70, 200), width=2)

    currencies = [("us", "دلار", get_nerkh_price_safe(["dollar"])), ("eu", "یورو", get_nerkh_price_safe(["euro"])), ("gb", "پوند", get_nerkh_price_safe(["pound"])), ("ae", "درهم", get_nerkh_price_safe(["aed"])), ("tr", "لیر", get_nerkh_price_safe(["lira"])), ("cn", "یوان", get_nerkh_price_safe(["yuan"])), ("ca", "کانادا", get_nerkh_price_safe(["cad"])), ("au", "استرالیا", get_nerkh_price_safe(["aud"]))]
    for i, (code, name, price) in enumerate(currencies):
        y = 280 + (i * (1850-280)/8) + ((1850-280)/16)
        icon = download_image(f"https://flagcdn.com/w80/{code}.png", (32, 32))
        if icon: img.paste(icon, (100 - 16, int(y) - 16), icon)
        draw_text(fa(f"{name}:"), 440, y, font_text, (200, 200, 200), "rm")
        draw_text(fa(price), 440 - draw.textlength(fa(f"{name}:"), font=font_text) - 20, y, font_text, (255, 215, 0), "rm")

    gold_items = [("طلا ۱۸", get_nerkh_price_safe(["gold"])), ("سکه امامی", get_nerkh_price_safe(["coin"])), ("سکه بهار", get_nerkh_price_safe(["bahar"])), ("نیم سکه", get_nerkh_price_safe(["half"])), ("ربع سکه", get_nerkh_price_safe(["quarter"]))]
    for i, (name, price) in enumerate(gold_items):
        y = 280 + (i * (900-280)/6) + ((900-280)/12)
        draw_text(fa(f"{name}:"), 940, y, font_text, (200, 200, 200), "rm")
        draw_text(fa(price), 940 - draw.textlength(fa(f"{name}:"), font=font_text) - 20, y, font_text, (255, 215, 0), "rm")

    crypto = get_crypto_prices_toman()
    crypto_items = [(k, v) for k, v in crypto.items()] if crypto else [("خطا", "0")]
    for i, (name, price) in enumerate(crypto_items):
        y = 1000 + (i * (1850-1000)/4) + ((1850-1000)/8)
        draw_text(fa(f"{name}:"), 940, y, font_text, (200, 200, 200), "rm")
        draw_text(fa(price), 940 - draw.textlength(fa(f"{name}:"), font=font_text) - 20, y, font_text, (255, 215, 0), "rm")

    final_img = Image.alpha_composite(img, overlay)
    bio = BytesIO()
    final_img.convert("RGB").save(bio, 'JPEG', quality=95)
    bio.seek(0)
    return bio

async def send_fiat_to_channel(context: ContextTypes.DEFAULT_TYPE, chat_id=CHANNEL_ID):
    time_str, _ = get_dates_info()
    msg = f"{TEMPLATES['fiat_title']}\n⏰ ساعت آپدیت: \u200e`{time_str}`\n\n💵 دلار = \u200e`{get_nerkh_price_safe(['dollar', 'usd'])}` تومان\n💶 یورو = \u200e`{get_nerkh_price_safe(['euro', 'eur'])}` تومان\n💷 پوند = \u200e`{get_nerkh_price_safe(['pound', 'gbp'])}` تومان\n🇦🇪 درهم = \u200e`{get_nerkh_price_safe(['aed'])}` تومان\n🇹🇷 لیر = \u200e`{get_nerkh_price_safe(['lira'])}` تومان\n🇨🇳 یوان = \u200e`{get_nerkh_price_safe(['yuan'])}` تومان\n{TEMPLATES['footer']}"
    await context.bot.send_message(chat_id=chat_id, text=msg, parse_mode='Markdown')

async def send_gold_to_channel(context: ContextTypes.DEFAULT_TYPE, chat_id=CHANNEL_ID):
    time_str, _ = get_dates_info()
    msg = f"{TEMPLATES['gold_title']}\n⏰ ساعت آپدیت: \u200e`{time_str}`\n\n🌕 طلا ۱۸ عیار = \u200e`{get_nerkh_price_safe(['gold'])}` تومان\n🪙 سکه امامی = \u200e`{get_nerkh_price_safe(['coin'])}` تومان\n🪙 سکه بهار = \u200e`{get_nerkh_price_safe(['bahar'])}` تومان\n🪙 نیم سکه = \u200e`{get_nerkh_price_safe(['half'])}` تومان\n🪙 ربع سکه = \u200e`{get_nerkh_price_safe(['quarter'])}` تومان\n{TEMPLATES['footer']}"
    await context.bot.send_message(chat_id=chat_id, text=msg, parse_mode='Markdown')

async def send_crypto_to_channel(context: ContextTypes.DEFAULT_TYPE, chat_id=CHANNEL_ID):
    time_str, _ = get_dates_info()
    crypto = get_crypto_prices_toman()
    msg = f"{TEMPLATES['crypto_title']}\n⏰ ساعت آپدیت: \u200e`{time_str}`\n\n"
    for sym, prc in crypto.items(): msg += f"🔸 {sym} = \u200e`{prc}` تومان\n"
    msg += TEMPLATES['footer']
    await context.bot.send_message(chat_id=chat_id, text=msg, parse_mode='Markdown')

async def send_image_summary_to_channel(context: ContextTypes.DEFAULT_TYPE, chat_id=CHANNEL_ID):
    try:
        await context.bot.send_photo(chat_id=chat_id, photo=generate_infographic(), caption="📊 **خلاصه وضعیت بازار**\n\n💰 @diginavasan", parse_mode='Markdown')
    except: pass

async def send_morning_message(context: ContextTypes.DEFAULT_TYPE):
    await context.bot.send_message(chat_id=CHANNEL_ID, text="🌅 **بسم الله الرحمن الرحیم**\nصبح بخیر! بازار امروز آغاز شد. ☀️\n\n*💰 دیجی نوسان 👇*\n@diginavasan", parse_mode='Markdown')

async def send_night_message(context: ContextTypes.DEFAULT_TYPE):
    await context.bot.send_message(chat_id=CHANNEL_ID, text="🌙 **پایان نرخ بازار**\nبازار تعطیل شد. شب خوبی رو برای شما و خانواده‌تون آرزو می‌کنیم. 🌹\n\n*💰 دیجی نوسان 👇*\n@diginavasan", parse_mode='Markdown')

_rotation_index = 0
async def rotate_jobs(context: ContextTypes.DEFAULT_TYPE):
    if not is_working_hours(): return
    global _rotation_index
    jobs = [send_fiat_to_channel, send_gold_to_channel, send_crypto_to_channel, send_image_summary_to_channel]
    await jobs[_rotation_index % len(jobs)](context)
    _rotation_index += 1

def get_main_menu():
    keyboard = [
        [KeyboardButton("🚀 استارت / منوی اصلی")],
        [KeyboardButton("💱 قیمت ارزها"), KeyboardButton("🌕 قیمت طلا و سکه"), KeyboardButton("🪙 قیمت ارز دیجیتال")],
        [KeyboardButton("🧮 ماشین‌حساب طلا"), KeyboardButton("💸 ماشین‌حساب اقساط"), KeyboardButton("💱 مبدل هوشمند ارز")],
        [KeyboardButton("📅 تبدیل تاریخ"), KeyboardButton("🌤 وضعیت آب‌وهوا"), KeyboardButton("🚨 آلارم قیمتی")],
        [KeyboardButton("🤖 چت با هوش مصنوعی")],
        [KeyboardButton("🎁 دعوت از دوستان")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, is_persistent=True)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    first_name = update.effective_user.first_name or "کاربر"
    log_event(user_id, "start_bot")
    
    invite_count = 0
    try:
        response = supabase.table('users').select('*').eq('user_id', user_id).execute()
        if not response.data:
            invited_by = int(context.args[0]) if context.args and context.args[0].isdigit() else None
            supabase.table('users').insert({"user_id": user_id, "invited_by": invited_by}).execute()
        else: invite_count = response.data[0].get('invite_count', 0)
    except: pass

    welcome_msg = f"سلام **{first_name}** عزیز! به ربات دیجی نوسان خوش آمدید. ✅\n\n👥 تعداد دعوت‌های شما تاکنون: **{invite_count}** نفر\n\n👇 **از منوی زیر یکی از گزینه‌ها را انتخاب کنید:**"
    await context.bot.send_message(chat_id=user_id, text=welcome_msg, parse_mode='Markdown', reply_markup=get_main_menu())

async def handle_menu_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id

    if "استارت" in text or "منوی اصلی" in text:
        await start(update, context)
    elif "قیمت ارزها" in text:
        log_event(user_id, "view_fiat_prices")
        await send_fiat_to_channel(context, chat_id=chat_id)
    elif "قیمت طلا و سکه" in text:
        log_event(user_id, "view_gold_prices")
        await send_gold_to_channel(context, chat_id=chat_id)
    elif "قیمت ارز دیجیتال" in text:
        log_event(user_id, "view_crypto_prices")
        await send_crypto_to_channel(context, chat_id=chat_id)
    elif "ماشین‌حساب طلا" in text:
        log_event(user_id, "open_gold_calculator")
        await gold_calc_start(update, context)
    elif "ماشین‌حساب اقساط" in text:
        await loan_calc_start(update, context)
    elif "مبدل هوشمند ارز" in text:
        await currency_conv_start(update, context)
    elif "تبدیل تاریخ" in text:
        await date_conv_start(update, context)
    elif "وضعیت آب‌وهوا" in text:
        await weather_start(update, context)
    elif "آلارم قیمتی" in text:
        await alert_start(update, context)
    elif "چت با هوش مصنوعی" in text:
        await ai_chat_start(update, context)
    elif "دعوت از دوستان" in text:
        log_event(user_id, "view_invite_menu")
        try:
            invite_link = f"https://t.me/diginavasan_bot?start={user_id}"
            invite_count = 0
            try:
                res = supabase.table('users').select('invite_count').eq('user_id', user_id).execute()
                invite_count = res.data[0].get('invite_count', 0) if res.data else 0
            except: pass
                
            invite_text = f"🎁 <b>دعوت از دوستان به دیجی نوسان</b> 🌟\n\n👥 تعداد افرادی که تاکنون دعوت کرده‌اید: <b>{invite_count}</b> نفر\n\n🔗 <b>لینک اختصاصی دعوت شما:</b>\n{invite_link}\n\nبرای دعوت دوستانتان، از دکمه زیر استفاده کنید:"
            share_text = f"سلام! خواستم این کانال و ربات فوق‌العاده رو بهت معرفی کنم. 🤝\n\n📢 کانال دیجی نوسان:\n👉 https://t.me/diginavasan\n\n🤖 ربات اختصاصی دیجی نوسان:\n👉 {invite_link}"
            encoded_text = urllib.parse.quote(share_text)
            keyboard = [[InlineKeyboardButton("📤 ارسال به مخاطبین", url=f"https://t.me/share/url?url={invite_link}&text={encoded_text}")]]
            await context.bot.send_message(chat_id=chat_id, text=invite_text, parse_mode='HTML', reply_markup=InlineKeyboardMarkup(keyboard))
        except: pass

# ==========================================
# 🤖 هندلر چت با هوش مصنوعی
# ==========================================
AI_CHAT = 60
async def ai_chat_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🤖 **چت با هوش مصنوعی (Groq)**\n\nسوال خودت رو بپرس، من جواب می‌دم! (برای خروج روی دکمه «لغو» بزن)", reply_markup=ReplyKeyboardMarkup([["لغو"]], resize_keyboard=True, one_time_keyboard=True))
    return AI_CHAT

async def ai_chat_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = update.message.text
    if user_text == "لغو":
        return await cancel(update, context)
    
    await update.message.reply_text("⏳ در حال فکر کردن...", reply_markup=ReplyKeyboardMarkup([["لغو"]], resize_keyboard=True))
    response_text = await asyncio.to_thread(get_ai_response, user_text)
    
    if len(response_text) > 4000:
        response_text = response_text[:4000] + "\n\n...(ادامه پیام کوتاه شد)"
        
    await update.message.reply_text(response_text, reply_markup=ReplyKeyboardMarkup([["لغو"]], resize_keyboard=True))
    return AI_CHAT

# ==========================================
# 🧮 ماشین‌حساب طلا
# ==========================================
PRICE, WEIGHT, AJRAT, PROFIT, TAX = range(5)
async def gold_calc_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await context.bot.send_message(chat_id=update.effective_chat.id, text=f"🧮 <b>ماشین‌حساب طلا</b>\n\nلطفاً قیمت هر گرم طلای ۱۸ عیار را <b>به تومان</b> وارد کنید:\n(راهنما: قیمت لحظه‌ای بازار {get_nerkh_price_safe(['gold'])} تومان است)", parse_mode='HTML')
    return PRICE
async def get_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        context.user_data['price'] = float(update.message.text.replace(',', ''))
        await update.message.reply_text("چند گرم طلا می‌خواهید بخرید؟ (مثلاً 5)")
        return WEIGHT
    except ValueError: await update.message.reply_text("لطفاً فقط عدد وارد کنید:"); return PRICE
async def get_weight(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        context.user_data['weight'] = float(update.message.text.replace(',', ''))
        await update.message.reply_text("درصد اجرت ساخت چقدر است؟ (اگر بدون اجرت است 0 وارد کنید)")
        return AJRAT
    except ValueError: await update.message.reply_text("لطفاً فقط عدد وارد کنید:"); return WEIGHT
async def get_ajrat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        context.user_data['ajrat'] = float(update.message.text.replace(',', ''))
        await update.message.reply_text("درصد سود فروشنده چقدر است؟ (معمولاً 7)")
        return PROFIT
    except ValueError: await update.message.reply_text("لطفاً فقط عدد وارد کنید:"); return AJRAT
async def get_profit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        context.user_data['profit'] = float(update.message.text.replace(',', ''))
        await update.message.reply_text("درصد مالیات بر ارزش افزوده چقدر است؟ (معمولاً 9)")
        return TAX
    except ValueError: await update.message.reply_text("لطفاً فقط عدد وارد کنید:"); return PROFIT
async def get_tax(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        context.user_data['tax'] = float(update.message.text.replace(',', ''))
        p, w, a, pr, t = context.user_data['price'], context.user_data['weight'], context.user_data['ajrat'], context.user_data['profit'], context.user_data['tax']
        base = p * w
        aj = base * (a / 100)
        prof = (base + aj) * (pr / 100)
        tax = (aj + prof) * (t / 100)
        final = base + aj + prof + tax
        await update.message.reply_text(f"✅ <b>فاکتور محاسبه طلا</b>\n\n⚖️ وزن: {w} گرم\n💰 قیمت پایه: {base:,.0f}\n🛠 اجرت ({a}%): {aj:,.0f}\n📈 سود ({pr}%): {prof:,.0f}\n🧾 مالیات ({t}%): {tax:,.0f}\n➖➖➖➖➖➖➖➖\n💳 <b>قیمت نهایی: {final:,.0f} تومان</b>\n\n💰 @diginavasan", parse_mode='HTML', reply_markup=get_main_menu())
        return ConversationHandler.END
    except ValueError: await update.message.reply_text("لطفاً فقط عدد وارد کنید:"); return TAX

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("عملیات لغو شد.", reply_markup=get_main_menu())
    return ConversationHandler.END

# ==========================================
# 💸 ماشین‌حساب اقساط
# ==========================================
LOAN_AMT, LOAN_INT, LOAN_MONTHS = range(10, 13)
async def loan_calc_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("💸 <b>ماشین‌حساب اقساط وام</b>\n\nلطفاً مبلغ کل وام را <b>به تومان</b> وارد کنید (مثلاً 100000000):", parse_mode='HTML')
    return LOAN_AMT
async def loan_get_amt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        context.user_data['loan_amt'] = float(update.message.text.replace(',', ''))
        await update.message.reply_text("درصد سود سالانه چقدر است؟ (مثلاً 23)")
        return LOAN_INT
    except ValueError: await update.message.reply_text("لطفاً فقط عدد وارد کنید:"); return LOAN_AMT
async def loan_get_int(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        context.user_data['loan_int'] = float(update.message.text.replace(',', ''))
        await update.message.reply_text("در چند ماه (قسط) می‌خواهید تسویه کنید؟ (مثلاً 36)")
        return LOAN_MONTHS
    except ValueError: await update.message.reply_text("لطفاً فقط عدد وارد کنید:"); return LOAN_INT
async def loan_get_months(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        months = int(update.message.text.replace(',', ''))
        P = context.user_data['loan_amt']
        r = (context.user_data['loan_int'] / 100) / 12
        if r == 0: installment = P / months
        else: installment = P * r * ((1 + r)**months) / (((1 + r)**months) - 1)
        total_pay = installment * months
        total_int = total_pay - P
        await update.message.reply_text(f"✅ <b>نتیجه محاسبه وام</b>\n\n💵 مبلغ وام: {P:,.0f} تومان\n📊 سود سالانه: {context.user_data['loan_int']}%\n⏳ مدت: {months} ماه\n\n➖➖➖➖➖➖➖➖\n💳 <b>قسط ماهانه: {installment:,.0f} تومان</b>\n💰 <b>کل مبلغ پرداختی: {total_pay:,.0f} تومان</b>\n📈 <b>کل سود پرداختی: {total_int:,.0f} تومان</b>\n\n💰 @diginavasan", parse_mode='HTML', reply_markup=get_main_menu())
        return ConversationHandler.END
    except ValueError: await update.message.reply_text("لطفاً فقط عدد وارد کنید:"); return LOAN_MONTHS

# ==========================================
# 💱 مبدل هوشمند ارز
# ==========================================
CURRENCY_AMT, CURRENCY_TYPE = range(20, 22)
async def currency_conv_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("💱 <b>مبدل هوشمند ارز</b>\n\nلطفاً مبلغ را به <b>تومان</b> وارد کنید (مثلاً 5000000):", parse_mode='HTML')
    return CURRENCY_AMT
async def conv_get_amt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        context.user_data['conv_amt'] = float(update.message.text.replace(',', ''))
        keyboard = [["دلار", "یورو", "پوند"], ["بیت‌کوین", "تتر"]]
        await update.message.reply_text("می‌خواهید معادل چه چیزی را ببینید؟", reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True))
        return CURRENCY_TYPE
    except ValueError: await update.message.reply_text("لطفاً فقط عدد وارد کنید:"); return CURRENCY_AMT
async def conv_get_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    choice = update.message.text
    amt = context.user_data['conv_amt']
    result = 0
    if choice == "دلار": result = amt / get_nerkh_price("dollar") if get_nerkh_price("dollar") else 0
    elif choice == "یورو": result = amt / get_nerkh_price("euro") if get_nerkh_price("euro") else 0
    elif choice == "پوند": result = amt / get_nerkh_price("pound") if get_nerkh_price("pound") else 0
    elif choice in ["بیت‌کوین", "تتر"]:
        crypto = get_crypto_prices_toman()
        sym = "BTC" if choice == "بیت‌کوین" else "USDT"
        if sym in crypto: result = amt / int(crypto[sym].replace(',', ''))
    
    await update.message.reply_text(f"💱 {amt:,.0f} تومان معادل <b>{result:,.4f} {choice}</b> است.\n\n💰 @diginavasan", parse_mode='HTML', reply_markup=get_main_menu())
    return ConversationHandler.END

# ==========================================
# 📅 تبدیل تاریخ (اصلاح‌شده)
# ==========================================
DATE_INPUT = 30
async def date_conv_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📅 <b>تبدیل تاریخ</b>\n\nلطفاً یک تاریخ را با فرمت <b>سال/ماه/روز</b> وارد کنید (مثلاً 1403/05/01 یا 2024/07/22):\n\n<i>ربات به صورت هوشمند تشخیص می‌دهد شمسی است یا میلادی.</i>", parse_mode='HTML')
    return DATE_INPUT
async def date_get_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw_text = update.message.text.strip()
    try:
        parts = re.split(r'[\/\-\s]+', raw_text)
        if len(parts) != 3: raise ValueError
        nums = [int(p) for p in parts]
        a, b, c = nums[0], nums[1], nums[2]
        
        if a > 1500: y, m, d = a, b, c
        elif c > 1500: y, m, d = c, b, a
        else: y, m, d = a, b, c

        if y > 1500: 
            miladi = datetime(y, m, d)
            shamsi = jdatetime.date.fromgregorian(date=miladi)
            ghamari = convert.Gregorian(y, m, d).to_hijri()
            msg = f"📅 <b>نتیجه تبدیل تاریخ</b>\n\n📆 میلادی: {y}/{m}/{d}\n☀️ شمسی: {shamsi.year}/{shamsi.month}/{shamsi.day}\n🌙 قمری: {ghamari.year}/{ghamari.month}/{ghamari.day}"
        else: 
            shamsi = jdatetime.date(y, m, d)
            miladi = shamsi.togregorian()
            ghamari = convert.Jalali(y, m, d).to_hijri()
            msg = f"📅 <b>نتیجه تبدیل تاریخ</b>\n\n☀️ شمسی: {y}/{m}/{d}\n📆 میلادی: {miladi.year}/{miladi.month}/{miladi.day}\n🌙 قمری: {ghamari.year}/{ghamari.month}/{ghamari.day}"
        
        await update.message.reply_text(f"{msg}\n\n💰 @diginavasan", parse_mode='HTML', reply_markup=get_main_menu())
        return ConversationHandler.END
    except Exception:
        await update.message.reply_text("فرمت وارد شده صحیح نیست. لطفاً دقیقاً مثل 1403/05/01 وارد کنید:\n(برای بازگشت به منو روی دکمه زیر بزنید)", reply_markup=get_main_menu())
        return ConversationHandler.END

# ==========================================
# 🌤 وضعیت آب‌وهوا
# ==========================================
WEATHER_CITY = 40
async def weather_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🌤 <b>وضعیت آب‌وهوا</b>\n\nلطفاً نام شهر خود را وارد کنید (مثلاً تهران یا Mashhad):", parse_mode='HTML')
    return WEATHER_CITY
async def weather_get_city(update: Update, context: ContextTypes.DEFAULT_TYPE):
    city = urllib.parse.quote(update.message.text.strip())
    try:
        res = requests.get(f"https://wttr.in/{city}?format=j1", timeout=10)
        if res.status_code == 200:
            data = res.json()['current_condition'][0]
            temp = data['temp_C']
            desc = data['lang_fa'][0]['value'] if 'lang_fa' in data else data['weatherDesc'][0]['value']
            await update.message.reply_text(f"🌤 <b>وضعیت آب‌وهوای {update.message.text}</b>\n\n🌡 دما: {temp} درجه سانتی‌گراد\n☁️ وضعیت: {desc}\n\n💰 @diginavasan", parse_mode='HTML', reply_markup=get_main_menu())
        else:
            await update.message.reply_text("شهر پیدا نشد. لطفاً با حروف انگلیسی یا نام دقیق وارد کنید.", reply_markup=get_main_menu())
    except:
        await update.message.reply_text("خطا در دریافت اطلاعات آب‌وهوا.", reply_markup=get_main_menu())
    return ConversationHandler.END

# ==========================================
# 🚨 آلارم قیمتی
# ==========================================
ALERT_ASSET, ALERT_PRICE = range(50, 52)
async def alert_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [["دلار", "طلا", "بیت‌کوین"], ["لغو"]]
    await update.message.reply_text("🚨 <b>تنظیم آلارم قیمتی</b>\n\nبرای کدام مورد می‌خواهید آلارم تنظیم کنید؟", parse_mode='HTML', reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True))
    return ALERT_ASSET
async def alert_get_asset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == "لغو": return await cancel(update, context)
    context.user_data['alert_asset'] = update.message.text
    await update.message.reply_text(f"قیمت هدف برای {update.message.text} را به <b>تومان</b> وارد کنید (مثلاً 80000):", parse_mode='HTML')
    return ALERT_PRICE
async def alert_get_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        target = int(update.message.text.replace(',', ''))
        user_id = update.effective_user.id
        if user_id not in user_alerts: user_alerts[user_id] = []
        user_alerts[user_id].append({'asset': context.user_data['alert_asset'], 'target': target})
        await update.message.reply_text(f"✅ آلارم تنظیم شد.\n\nبه محض اینکه قیمت {context.user_data['alert_asset']} به {target:,.0f} تومان برسد، به شما اطلاع می‌دهم.\n(ربات هر ۵ دقیقه چک می‌کند)", reply_markup=get_main_menu())
        return ConversationHandler.END
    except ValueError:
        await update.message.reply_text("لطفاً فقط عدد وارد کنید:")
        return ALERT_PRICE

if __name__ == '__main__':
    threading.Thread(target=run_dummy_server, daemon=True).start()
    app = ApplicationBuilder().token(TOKEN).build()
    
    app.add_handler(ConversationHandler(entry_points=[MessageHandler(filters.Regex("ماشین‌حساب طلا"), gold_calc_start)], states={PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_price)], WEIGHT: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_weight)], AJRAT: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_ajrat)], PROFIT: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_profit)], TAX: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_tax)]}, fallbacks=[CommandHandler('cancel', cancel), MessageHandler(filters.Regex("^(🚀 استارت / منوی اصلی|🎁 دعوت از دوستان|💱 قیمت ارزها|🌕 قیمت طلا و سکه|🪙 قیمت ارز دیجیتال)$"), handle_menu_text)], allow_reentry=True))
    
    app.add_handler(ConversationHandler(entry_points=[MessageHandler(filters.Regex("ماشین‌حساب اقساط"), loan_calc_start)], states={LOAN_AMT: [MessageHandler(filters.TEXT & ~filters.COMMAND, loan_get_amt)], LOAN_INT: [MessageHandler(filters.TEXT & ~filters.COMMAND, loan_get_int)], LOAN_MONTHS: [MessageHandler(filters.TEXT & ~filters.COMMAND, loan_get_months)]}, fallbacks=[CommandHandler('cancel', cancel), MessageHandler(filters.Regex("^(🚀 استارت / منوی اصلی|🎁 دعوت از دوستان|💱 قیمت ارزها|🌕 قیمت طلا و سکه|🪙 قیمت ارز دیجیتال)$"), handle_menu_text)], allow_reentry=True))
    
    app.add_handler(ConversationHandler(entry_points=[MessageHandler(filters.Regex("مبدل هوشمند ارز"), currency_conv_start)], states={CURRENCY_AMT: [MessageHandler(filters.TEXT & ~filters.COMMAND, conv_get_amt)], CURRENCY_TYPE: [MessageHandler(filters.TEXT & ~filters.COMMAND, conv_get_type)]}, fallbacks=[CommandHandler('cancel', cancel)], allow_reentry=True))
    
    app.add_handler(ConversationHandler(entry_points=[MessageHandler(filters.Regex("تبدیل تاریخ"), date_conv_start)], states={DATE_INPUT: [MessageHandler(filters.TEXT & ~filters.COMMAND, date_get_input)]}, fallbacks=[CommandHandler('cancel', cancel)], allow_reentry=True))
    
    app.add_handler(ConversationHandler(entry_points=[MessageHandler(filters.Regex("وضعیت آب‌وهوا"), weather_start)], states={WEATHER_CITY: [MessageHandler(filters.TEXT & ~filters.COMMAND, weather_get_city)]}, fallbacks=[CommandHandler('cancel', cancel)], allow_reentry=True))
    
    app.add_handler(ConversationHandler(entry_points=[MessageHandler(filters.Regex("آلارم قیمتی"), alert_start)], states={ALERT_ASSET: [MessageHandler(filters.TEXT & ~filters.COMMAND, alert_get_asset)], ALERT_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, alert_get_price)]}, fallbacks=[CommandHandler('cancel', cancel)], allow_reentry=True))

    app.add_handler(ConversationHandler(entry_points=[MessageHandler(filters.Regex("چت با هوش مصنوعی"), ai_chat_start)], states={AI_CHAT: [MessageHandler(filters.TEXT & ~filters.COMMAND, ai_chat_message)]}, fallbacks=[CommandHandler('cancel', cancel), MessageHandler(filters.Regex("^لغو$"), cancel)], allow_reentry=True))

    app.add_handler(CommandHandler('start', start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_menu_text))
    
    tehran_tz = pytz.timezone('Asia/Tehran')
    app.job_queue.run_daily(send_morning_message, time=time(8, 0, tzinfo=tehran_tz))
    app.job_queue.run_daily(send_night_message, time=time(20, 0, tzinfo=tehran_tz))
    app.job_queue.run_repeating(rotate_jobs, interval=900, first=10)
    app.job_queue.run_repeating(check_alerts, interval=300, first=10)
    
    print("ربات روشن شد. هوش مصنوعی و تمامی ابزارها فعال شدند...")
    app.run_polling()
