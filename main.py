import os
import logging
import requests
import threading
import http.server
import socketserver
import pytz
import urllib.parse
import arabic_reshaper
import asyncio
import json
from bidi.algorithm import get_display
from io import BytesIO
from datetime import datetime, time
from PIL import Image, ImageDraw, ImageFont, features
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardButton, InlineKeyboardMarkup, Chat, ChatMember
from telegram.ext import (
    ApplicationBuilder, ContextTypes, CommandHandler, 
    ConversationHandler, MessageHandler, filters, ChatMemberHandler
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
UPSTASH_REDIS_REST_URL = os.environ.get("UPSTASH_REDIS_REST_URL")
UPSTASH_REDIS_REST_TOKEN = os.environ.get("UPSTASH_REDIS_REST_TOKEN")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

TEMPLATES = {
    "fiat_title": "💱 **قیمت لحظه‌ای ارزها**",
    "gold_title": "🌕 **قیمت لحظه‌ای طلا و سکه**",
    "crypto_title": "🪙 **قیمت لحظه‌ای ارز دیجیتال**",
    "footer": "\n*💰 قیمت لحظه‌ای دیجی نوسان 👇*\n@diginavasan"
}

HAS_RAQM = features.check("raqm")
user_alerts = {}

MENU_BUTTONS_REGEX = "^(🚀 استارت / منوی اصلی|🎁 دعوت از دوستان|💱 قیمت ارزها|🌕 قیمت طلا و سکه|🪙 قیمت ارز دیجیتال|🧮 ماشین‌حساب طلا|💸 ماشین‌حساب اقساط|💱 مبدل هوشمند ارز|🌤 وضعیت آب‌وهوا|🚨 آلارم قیمتی|🤖 چت با هوش مصنوعی)$"

# ==========================================
# 📦 توابع Upstash (دیتابیس)
# ==========================================
def upstash_request(command_path, method="GET"):
    if not UPSTASH_REDIS_REST_URL or not UPSTASH_REDIS_REST_TOKEN: return None
    try:
        url = f"{UPSTASH_REDIS_REST_URL}/{command_path}"
        headers = {"Authorization": f"Bearer {UPSTASH_REDIS_REST_TOKEN}"}
        if method == "GET": response = requests.get(url, headers=headers, timeout=10)
        else: response = requests.post(url, headers=headers, timeout=10)
        if response.status_code == 200: return response.json().get("result")
    except Exception: pass
    return None

def log_event(user_id, event_name, details=""):
    if not UPSTASH_REDIS_REST_URL or not UPSTASH_REDIS_REST_TOKEN: return
    try:
        event_data = json.dumps({"user_id": user_id, "event_name": event_name, "details": details, "created_at": datetime.now().isoformat()})
        encoded_data = urllib.parse.quote(event_data)
        requests.get(f"{UPSTASH_REDIS_REST_URL}/lpush/diginavasan_events/{encoded_data}", headers={"Authorization": f"Bearer {UPSTASH_REDIS_REST_TOKEN}"}, timeout=5)
    except Exception: pass

def set_group_owner(chat_id, owner_id):
    existing = upstash_request(f"get/group_owner_{chat_id}")
    if not existing:
        upstash_request(f"set/group_owner_{chat_id}/{owner_id}")
        upstash_request(f"sadd/partners_list/{owner_id}")

def add_user_to_partner(user_id, partner_id):
    is_new = upstash_request(f"sadd/bot_users/{user_id}")
    if is_new == 1: upstash_request(f"incr/partner_stats_{partner_id}")

def get_partner_stats(partner_id):
    res = upstash_request(f"get/partner_stats_{partner_id}")
    return int(res) if res else 0

def get_all_partners():
    res = upstash_request("smembers/partners_list")
    return res if isinstance(res, list) else []

def get_user_invites(user_id):
    res = upstash_request(f"get/partner_stats_{user_id}")
    return int(res) if res else 0
def get_today_str(): return datetime.now().strftime("%Y-%m-%d")
def get_ai_usage(user_id):
    res = upstash_request(f"get/ai_usage_{user_id}_{get_today_str()}")
    return int(res) if res else 0
def increment_ai_usage(user_id):
    key = f"ai_usage_{user_id}_{get_today_str()}"
    upstash_request(f"incr/{key}")
    upstash_request(f"expire/{key}/86400")

def add_channel_to_db(chat_id): upstash_request(f"sadd/diginavasan_channels/{chat_id}")
def remove_channel_from_db(chat_id): upstash_request(f"srem/diginavasan_channels/{chat_id}")
def get_all_channels_from_db():
    result = upstash_request("smembers/diginavasan_channels")
    return result if isinstance(result, list) else []

# ==========================================
# 🛠 سایر توابع پایه
# ==========================================
def run_dummy_server():
    Handler = http.server.SimpleHTTPRequestHandler
    with socketserver.TCPServer(("", PORT), Handler) as httpd: httpd.serve_forever()

def get_dates_info():
    now = datetime.now(pytz.timezone('Asia/Tehran'))
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
    except Exception: return 0

def get_nerkh_price_safe(keys_list):
    for key in keys_list:
        price = get_nerkh_price(key)
        if price > 0: return f"{price:,.0f}"
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
                    prices[symbol] = f"{int(float(res.json()['stats'][key]['latest']) / 10):,.0f}"
        return prices
    except Exception: return {}

def download_image(url, size=(60, 60)):
    try: return Image.open(BytesIO(requests.get(url, timeout=5).content)).convert("RGBA").resize(size, Image.Resampling.LANCZOS)
    except Exception: return None

def get_ai_response(prompt):
    try:
        url = "https://api.groq.com/openai/v1/chat/completions"
        headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
        data = {"model": "openai/gpt-oss-120b", "messages": [{"role": "system", "content": "You are a helpful assistant for a Persian Telegram bot named Diginavasan. Always answer in Persian."}, {"role": "user", "content": prompt}], "temperature": 0.7, "max_tokens": 1024}
        response = requests.post(url, headers=headers, json=data, timeout=30)
        if response.status_code == 200: return response.json()['choices'][0]['message']['content']
        else: return f"❌ خطا: {response.text}"
    except Exception as e: return f"❌ خطا: {str(e)}"

# ==========================================
# 🎨 توابع گرافیکی
# ==========================================
def create_gradient_bg(w, h):
    base = Image.new('RGB', (w, h), (15, 25, 45)); draw = ImageDraw.Draw(base)
    for y in range(h): draw.line([(0, y), (w, y)], fill=(int(15 + (5 - 15) * y / h), int(25 + (10 - 25) * y / h), int(45 + (20 - 45) * y / h)))
    return base
def create_gold_gradient(w, h):
    base = Image.new('RGB', (w, h)); draw = ImageDraw.Draw(base)
    for x in range(w): draw.line([(x, 0), (x, h)], fill=(int(218 + (184 - 218) * x / w), int(165 + (134 - 165) * x / w), int(32 + (11 - 32) * x / w)))
    return base
def generate_infographic():
    font_path = "vazir.ttf"
    if not os.path.exists(font_path):
        try:
            r = requests.get("https://cdn.jsdelivr.net/gh/rastikerdar/vazirmatn@v33.003/fonts/ttf/Vazirmatn-Bold.ttf", timeout=20)
            with open(font_path, 'wb') as f: f.write(r.content)
        except: pass
    w, h = 1080, 1920
    img = create_gradient_bg(w, h).convert("RGBA"); overlay = Image.new('RGBA', img.size, (0, 0, 0, 0)); draw = ImageDraw.Draw(overlay)
    try:
        font_title = ImageFont.truetype(font_path, 45); font_text = ImageFont.truetype(font_path, 32)
    except: font_title = font_text = ImageFont.load_default()
    def fa(text): return text if HAS_RAQM else get_display(arabic_reshaper.reshape(text))
    def draw_text(text, x, y, font, color, anchor, stroke=True):
        draw.text((x, y), text, font=font, fill=color, anchor=anchor, direction="rtl" if HAS_RAQM else "ltr", stroke_width=1 if stroke else 0, stroke_fill=(0,0,0))
    time_str, _ = get_dates_info()
    header_bg = create_gold_gradient(w-80, 120); mask = Image.new('L', (w-80, 120), 0); ImageDraw.Draw(mask).rounded_rectangle([(0, 0), (w-80, 120)], radius=20, fill=255)
    img.paste(header_bg, (40, 40), mask); draw.rounded_rectangle([(40, 40), (w-40, 160)], radius=20, outline=(255, 215, 0), width=3)
    draw_text("دیجی نوسان", w * 0.35, h * 0.05, font_title, (20, 30, 50), "mm", False); draw_text(time_str, w * 0.82, h * 0.05, font_text, (20, 30, 50), "mm", False)
    draw.rounded_rectangle([(40, 220), (480, 1850)], radius=30, fill=(255, 255, 255, 15), outline=(200, 160, 70, 200), width=2)
    draw.rounded_rectangle([(520, 220), (1040, 900)], radius=30, fill=(255, 255, 255, 15), outline=(200, 160, 70, 200), width=2)
    draw.rounded_rectangle([(520, 940), (1040, 1850)], radius=30, fill=(255, 255, 255, 15), outline=(200, 160, 70, 200), width=2)
    currencies = [("us", "دلار", get_nerkh_price_safe(["dollar"])), ("eu", "یورو", get_nerkh_price_safe(["euro"])), ("gb", "پوند", get_nerkh_price_safe(["pound"])), ("ae", "درهم", get_nerkh_price_safe(["aed"])), ("tr", "لیر", get_nerkh_price_safe(["lira"])), ("cn", "یوان", get_nerkh_price_safe(["yuan"])), ("ca", "کانادا", get_nerkh_price_safe(["cad"])), ("au", "استرالیا", get_nerkh_price_safe(["aud"]))]
    for i, (code, name, price) in enumerate(currencies):
        y = 280 + (i * (1850-280)/8) + ((1850-280)/16); icon = download_image(f"https://flagcdn.com/w80/{code}.png", (32, 32))
        if icon: img.paste(icon, (100 - 16, int(y) - 16), icon)
        draw_text(fa(f"{name}:"), 440, y, font_text, (200, 200, 200), "rm"); draw_text(fa(price), 440 - draw.textlength(fa(f"{name}:"), font=font_text) - 20, y, font_text, (255, 215, 0), "rm")
    gold_items = [("طلا ۱۸", get_nerkh_price_safe(["gold"])), ("سکه امامی", get_nerkh_price_safe(["coin"])), ("سکه بهار", get_nerkh_price_safe(["bahar"])), ("نیم سکه", get_nerkh_price_safe(["half"])), ("ربع سکه", get_nerkh_price_safe(["quarter"]))]
    for i, (name, price) in enumerate(gold_items):
        y = 280 + (i * (900-280)/6) + ((900-280)/12); draw_text(fa(f"{name}:"), 940, y, font_text, (200, 200, 200), "rm"); draw_text(fa(price), 940 - draw.textlength(fa(f"{name}:"), font=font_text) - 20, y, font_text, (255, 215, 0), "rm")
    crypto = get_crypto_prices_toman(); crypto_items = [(k, v) for k, v in crypto.items()] if crypto else [("خطا", "0")]
    for i, (name, price) in enumerate(crypto_items):
        y = 1000 + (i * (1850-1000)/4) + ((1850-1000)/8); draw_text(fa(f"{name}:"), 940, y, font_text, (200, 200, 200), "rm"); draw_text(fa(price), 940 - draw.textlength(fa(f"{name}:"), font=font_text) - 20, y, font_text, (255, 215, 0), "rm")
    final_img = Image.alpha_composite(img, overlay); bio = BytesIO(); final_img.convert("RGB").save(bio, 'JPEG', quality=95); bio.seek(0); return bio

# ==========================================
# 📢 ارسال‌ها (کانال و گروه‌ها)
# ==========================================
async def send_fiat_to_channel(context: ContextTypes.DEFAULT_TYPE, chat_id=CHANNEL_ID):
    time_str, _ = get_dates_info()
    msg = f"{TEMPLATES['fiat_title']}\n⏰ ساعت آپدیت: \u200e`{time_str}`\n\n💵 دلار = \u200e`{get_nerkh_price_safe(['dollar', 'usd'])}` تومان\n💶 یورو = \u200e`{get_nerkh_price_safe(['euro', 'eur'])}` تومان\n💷 پوند = \u200e`{get_nerkh_price_safe(['pound', 'gbp'])}` تومان\n🇦🇪 درهم = \u200e`{get_nerkh_price_safe(['aed'])}` تومان\n🇹🇷 لیر = \u200e`{get_nerkh_price_safe(['lira'])}` تومان\n🇨🇳 یوان = \u200e`{get_nerkh_price_safe(['yuan'])}` تومان\n{TEMPLATES['footer']}"
    await context.bot.send_message(chat_id=chat_id, text=msg, parse_mode='Markdown')
async def send_gold_to_channel(context: ContextTypes.DEFAULT_TYPE, chat_id=CHANNEL_ID):
    time_str, _ = get_dates_info()
    msg = f"{TEMPLATES['gold_title']}\n⏰ ساعت آپدیت: \u200e`{time_str}`\n\n🌕 طلا ۱۸ عیار = \u200e`{get_nerkh_price_safe(['gold'])}` تومان\n🪙 سکه امامی = \u200e`{get_nerkh_price_safe(['coin'])}` تومان\n🪙 سکه بهار = \u200e`{get_nerkh_price_safe(['bahar'])}` تومان\n🪙 نیم سکه = \u200e`{get_nerkh_price_safe(['half'])}` تومان\n🪙 ربع سکه = \u200e`{get_nerkh_price_safe(['quarter'])}` تومان\n{TEMPLATES['footer']}"
    await context.bot.send_message(chat_id=chat_id, text=msg, parse_mode='Markdown')
async def send_crypto_to_channel(context: ContextTypes.DEFAULT_TYPE, chat_id=CHANNEL_ID):
    time_str, _ = get_dates_info(); crypto = get_crypto_prices_toman()
    msg = f"{TEMPLATES['crypto_title']}\n⏰ ساعت آپدیت: \u200e`{time_str}`\n\n"
    for sym, prc in crypto.items(): msg += f"🔸 {sym} = \u200e`{prc}` تومان\n"
    msg += TEMPLATES['footer']
    await context.bot.send_message(chat_id=chat_id, text=msg, parse_mode='Markdown')
async def send_image_summary_to_channel(context: ContextTypes.DEFAULT_TYPE, chat_id=CHANNEL_ID):
    try: await context.bot.send_photo(chat_id=chat_id, photo=generate_infographic(), caption="📊 **خلاصه وضعیت بازار**\n\n💰 @diginavasan", parse_mode='Markdown')
    except: pass
async def send_morning_message(context: ContextTypes.DEFAULT_TYPE): await context.bot.send_message(chat_id=CHANNEL_ID, text="🌅 **بسم الله الرحمن الرحیم**\nصبح بخیر! بازار امروز آغاز شد. ☀️\n\n*💰 دیجی نوسان 👇*\n@diginavasan", parse_mode='Markdown')
async def send_night_message(context: ContextTypes.DEFAULT_TYPE): await context.bot.send_message(chat_id=CHANNEL_ID, text="🌙 **پایان نرخ بازار**\nبازار تعطیل شد. شب خوبی رو برای شما و خانواده‌تون آرزو می‌کنیم. 🌹\n\n*💰 دیجی نوسان 👇*\n@diginavasan", parse_mode='Markdown')
_rotation_index = 0
async def rotate_jobs(context: ContextTypes.DEFAULT_TYPE):
    if not is_working_hours(): return
    global _rotation_index
    await [send_fiat_to_channel, send_gold_to_channel, send_crypto_to_channel, send_image_summary_to_channel][_rotation_index % 4](context)
    _rotation_index += 1

async def track_chats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    result = update.my_chat_member
    if not result: return
    was_member = result.old_chat_member.status in [ChatMember.MEMBER, ChatMember.ADMINISTRATOR]
    is_member = result.new_chat_member.status in [ChatMember.MEMBER, ChatMember.ADMINISTRATOR]
    chat = update.effective_chat
    if chat.type in [Chat.CHANNEL, Chat.GROUP, Chat.SUPERGROUP]:
        if not was_member and is_member:
            add_channel_to_db(chat.id)
            adder_id = result.from_user.id if result.from_user else None
            if adder_id:
                set_group_owner(chat.id, adder_id)
                try:
                    await context.bot.send_message(chat_id=adder_id, text=f"سلام! ممنون که ربات دیجی‌نوسان رو به گروه/کانال «{chat.title}» اضافه کردی. 🌹\n\nبرای اینکه ببینی چند نفر از طریق لینک این گروه عضو ربات ما شدن و چقدر پورسانت گرفتی، کافیه دستور /status رو بزنی.", parse_mode='HTML')
                except:
                    if result.from_user and result.from_user.username:
                        await context.bot.send_message(chat_id=chat.id, text=f"@{result.from_user.username} عزیز، برای دریافت لینک اختصاصی و مشاهده آمار جذب ممبر، لطفاً به چت خصوصی ربات بیایید و /start را بزنید.")
        elif was_member and not is_member: remove_channel_from_db(chat.id)

async def send_hourly_summary_to_externals(context: ContextTypes.DEFAULT_TYPE):
    channels = get_all_channels_from_db()
    if not channels: return
    try:
        img_bio = await asyncio.to_thread(generate_infographic)
        for chat_id in channels:
            try:
                group_link = f"https://t.me/diginavasan_bot?start=group_{chat_id}"
                caption = f"📊 <b>خلاصه وضعیت بازار</b>\n\n💰 کانال ما: @diginavasan\n🤖 دریافت ربات اختصاصی این گروه:\n<a href='{group_link}'>👉 کلیک کنید 👈</a>"
                bio_copy = BytesIO(img_bio.getvalue()); bio_copy.name = 'summary.jpeg'
                await context.bot.send_photo(chat_id=chat_id, photo=bio_copy, caption=caption, parse_mode='HTML')
                await asyncio.sleep(1)
            except Exception as e:
                logging.error(f"Error sending to {chat_id}: {e}")
                remove_channel_from_db(chat_id)
    except Exception: pass

# ==========================================
# 🧠 سیستم لاگ و منو
# ==========================================
async def check_alerts(context: ContextTypes.DEFAULT_TYPE):
    global user_alerts
    for user_id in list(user_alerts.keys()):
        for alert in list(user_alerts[user_id]):
            asset, target = alert['asset'], alert['target']
            current_price = 0
            if asset == 'دلار': current_price = int(get_nerkh_price_safe(["dollar"]).replace(',', ''))
            elif asset == 'طلا': current_price = int(get_nerkh_price_safe(["gold"]).replace(',', ''))
            elif asset == 'بیت‌کوین':
                crypto = get_crypto_prices_toman()
                if 'BTC' in crypto: current_price = int(crypto['BTC'].replace(',', ''))
            if current_price > 0 and current_price >= target:
                try:
                    await context.bot.send_message(chat_id=user_id, text=f"🚨 <b>آلارم قیمتی شما فعال شد!</b>\n\nقیمت <b>{asset}</b> به {current_price:,.0f} تومان رسیده است (هدف شما: {target:,.0f} تومان).", parse_mode='HTML')
                    user_alerts[user_id].remove(alert)
                except Exception: pass
        if not user_alerts[user_id]: del user_alerts[user_id]

def get_main_menu():
    keyboard = [
        [KeyboardButton("🚀 استارت / منوی اصلی")],
        [KeyboardButton("💱 قیمت ارزها"), KeyboardButton("🌕 قیمت طلا و سکه"), KeyboardButton("🪙 قیمت ارز دیجیتال")],
        [KeyboardButton("🧮 ماشین‌حساب طلا"), KeyboardButton("💸 ماشین‌حساب اقساط"), KeyboardButton("💱 مبدل هوشمند ارز")],
        [KeyboardButton("🌤 وضعیت آب‌وهوا"), KeyboardButton("🚨 آلارم قیمتی"), KeyboardButton("🤖 چت با هوش مصنوعی")],
        [KeyboardButton("🎁 دعوت از دوستان")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, is_persistent=True)

def get_cancel_keyboard(): return ReplyKeyboardMarkup([["🔙 لغو / بازگشت"]], resize_keyboard=True)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id; first_name = update.effective_user.first_name or "کاربر"
    log_event(user_id, "start_bot")
    
    # بررسی لینک دعوت (پارتنر یا گروه)
    if context.args:
        arg = context.args[0]
        if arg.startswith("group_"):
            group_chat_id = arg.replace("group_", "")
            partner_id = upstash_request(f"get/group_owner_{group_chat_id}")
            if partner_id: add_user_to_partner(user_id, partner_id)
        else:
            # لینک دعوت معمولی (آیدی عددی دوست)
            try:
                inviter_id = int(arg)
                if inviter_id != user_id:
                    add_user_to_partner(user_id, inviter_id)
            except ValueError:
                pass

    welcome_msg = f"سلام **{first_name}** عزیز! به ربات دیجی نوسان خوش آمدید. ✅\n\n👇 **از منوی زیر یکی از گزینه‌ها را انتخاب کنید:**"
    await context.bot.send_message(chat_id=user_id, text=welcome_msg, parse_mode='Markdown', reply_markup=get_main_menu())

# ==========================================
# 📊 دستورات مخفی (Status و Admin Finance)
# ==========================================
async def partner_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    stats = get_partner_stats(user_id)
    if stats == 0:
        await update.message.reply_text("شما هنوز هیچ کاربری را به ربات ما نیاورده‌اید. کافیه ربات را به گروه/کانال خود اضافه کنید تا لینک اختصاصی شما ساخته شود.")
        return
    await update.message.reply_text(f"📊 <b>وضعیت شریک بنیان‌گذار</b>\n\n👤 آیدی عددی شما: <code>{user_id}</code>\n👥 تعداد کاربرانی که از طریق شما وارد شده‌اند: <b>{stats} نفر</b>\n\n💰 <i>پاداش شما به محض رسیدن ربات به ۱۰,۰۰۰ ممبر و شروع اسپانسرشیپ‌ها، بر اساس همین تعداد محاسبه و پرداخت خواهد شد.</i>", parse_mode='HTML')

async def admin_finance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    partners = get_all_partners()
    total_members = 0
    report = "📈 <b>گزارش مالی پارتنرها (فقط ادمین)</b>\n\n"
    for p in partners:
        cnt = get_partner_stats(p)
        total_members += cnt
        report += f"👤 ادمین <code>{p}</code>: {cnt} کاربر\n"
    report += f"\n➖➖➖➖➖➖➖➖\n👥 کل کاربران جذب‌شده توسط پارتنرها: <b>{total_members} نفر</b>\n💵 <i>برای محاسبه پورسانت، مبلغ کل تبلیغات را ضربدر ۴۰٪ کنید، سپس تقسیم بر کل ممبران کنید تا نرخ هر ممبر به دست آید.</i>"
    await update.message.reply_text(report, parse_mode='HTML')

# ==========================================
# 🤖 چت با هوش مصنوعی (با دکمه اشتراک‌گذاری)
# ==========================================
AI_CHAT = 60
async def ai_chat_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    log_event(user_id, "open_ai_chat")
    invites = get_user_invites(user_id)
    
    if invites == 0 and get_ai_usage(user_id) >= 3:
        invite_link = f"https://t.me/diginavasan_bot?start={user_id}"
        
        share_text = (
            "سلام رفیق! 👋\n\n"
            "یه ربات تلگرامی پیدا کردم که واقعاً به کارم میاد. اسمش @diginavasan_bot هست.\n\n"
            "چی داره؟\n"
            "📊 قیمت لحظه‌ای دلار، طلا، سکه و بیت‌کوین\n"
            "🧮 ماشین‌حساب طلا (برای وقتی می‌خوای طلا بخری)\n"
            "🤖 یه هوش مصنوعی خفن (می‌تونی هر سوالی داری بپرسی)\n\n"
            "من خودم روزانه چک می‌کنم. تو هم یه بار امتحانش کن، باحاله!\n\n"
            "👉 لینک ربات: {invite_link}\n\n"
            "راستی، یه کانال هم دارن که هر روز صبح قیمت‌های بازار رو میذارن. اگه دوست داشتی اونجا هم عضو شو:\n"
            "👉 کانال: @diginavasan"
        ).format(invite_link=invite_link)
        
        encoded_text = urllib.parse.quote(share_text)
        share_url = f"https://t.me/share/url?url={invite_link}&text={encoded_text}"
        
        msg = f"⚠️ <b>سهمیه رایگان امروز شما به پایان رسید!</b>\n\nشما امروز از ۳ سوال رایگان خود استفاده کرده‌اید.\nبرای استفاده <b>نامحدود</b> از هوش مصنوعی، کافیه فقط <b>۱ نفر</b> از دوستان خود را به ربات دعوت کنید.\n\nبرای دعوت، روی دکمه زیر بزنید و دوست خود را انتخاب کنید. بعد از اینکه دوستتان ربات را استارت کرد، قفل هوش مصنوعی برای شما باز می‌شود."
        
        keyboard = [[InlineKeyboardButton("📤 ارسال دعوت‌نامه به مخاطبین", url=share_url)]]
        
        await update.message.reply_text(msg, parse_mode='HTML', reply_markup=InlineKeyboardMarkup(keyboard))
        return ConversationHandler.END
        
    remaining = 3 - get_ai_usage(user_id) if invites == 0 else "نامحدود"
    await update.message.reply_text(f"🤖 **چت با هوش مصنوعی (Groq)**\n\nسهمیه باقیمانده امروز: {remaining}\n\nسوال خودت رو بپرس! (برای خروج روی دکمه «لغو» بزن)", reply_markup=get_cancel_keyboard())
    return AI_CHAT

async def ai_chat_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == "🔙 لغو / بازگشت": return await cancel(update, context)
    user_id = update.effective_user.id
    invites = get_user_invites(user_id)
    if invites == 0 and get_ai_usage(user_id) >= 3: return await ai_chat_start(update, context)
    await update.message.reply_text("⏳ در حال فکر کردن...", reply_markup=get_cancel_keyboard())
    response_text = await asyncio.to_thread(get_ai_response, update.message.text)
    if len(response_text) > 4000: response_text = response_text[:4000] + "\n\n...(ادامه پیام کوتاه شد)"
    if invites == 0:
        increment_ai_usage(user_id)
        remaining = 3 - get_ai_usage(user_id)
        response_text += f"\n\n---\n🔔 <i>سهمیه رایگان امروز شما: {remaining} سوال</i>"
    await update.message.reply_text(response_text, reply_markup=get_cancel_keyboard(), parse_mode='HTML')
    return AI_CHAT

# ==========================================
# 🧮 ماشین‌حساب طلا
# ==========================================
PRICE, WEIGHT, AJRAT, PROFIT, TAX = range(5)
async def gold_calc_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    log_event(update.effective_user.id, "open_gold_calculator")
    await context.bot.send_message(chat_id=update.effective_chat.id, text=f"🧮 <b>ماشین‌حساب طلا</b>\n\nلطفاً قیمت هر گرم طلای ۱۸ عیار را <b>به تومان</b> وارد کنید:\n(راهنما: قیمت لحظه‌ای بازار {get_nerkh_price_safe(['gold'])} تومان است)", parse_mode='HTML', reply_markup=get_cancel_keyboard())
    return PRICE
async def get_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == "🔙 لغو / بازگشت": return await cancel(update, context)
    try:
        context.user_data['price'] = float(update.message.text.replace(',', ''))
        await update.message.reply_text("چند گرم طلا می‌خواهید بخرید؟ (مثلاً 5)", reply_markup=get_cancel_keyboard())
        return WEIGHT
    except ValueError: await update.message.reply_text("لطفاً فقط عدد وارد کنید:", reply_markup=get_cancel_keyboard()); return PRICE
async def get_weight(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == "🔙 لغو / بازگشت": return await cancel(update, context)
    try:
        context.user_data['weight'] = float(update.message.text.replace(',', ''))
        await update.message.reply_text("درصد اجرت ساخت چقدر است؟ (اگر بدون اجرت است 0 وارد کنید)", reply_markup=get_cancel_keyboard())
        return AJRAT
    except ValueError: await update.message.reply_text("لطفاً فقط عدد وارد کنید:", reply_markup=get_cancel_keyboard()); return WEIGHT
async def get_ajrat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == "🔙 لغو / بازگشت": return await cancel(update, context)
    try:
        context.user_data['ajrat'] = float(update.message.text.replace(',', ''))
        await update.message.reply_text("درصد سود فروشنده چقدر است؟ (معمولاً 7)", reply_markup=get_cancel_keyboard())
        return PROFIT
    except ValueError: await update.message.reply_text("لطفاً فقط عدد وارد کنید:", reply_markup=get_cancel_keyboard()); return AJRAT
async def get_profit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == "🔙 لغو / بازگشت": return await cancel(update, context)
    try:
        context.user_data['profit'] = float(update.message.text.replace(',', ''))
        await update.message.reply_text("درصد مالیات بر ارزش افزوده چقدر است؟ (معمولاً 9)", reply_markup=get_cancel_keyboard())
        return TAX
    except ValueError: await update.message.reply_text("لطفاً فقط عدد وارد کنید:", reply_markup=get_cancel_keyboard()); return PROFIT
async def get_tax(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == "🔙 لغو / بازگشت": return await cancel(update, context)
    try:
        context.user_data['tax'] = float(update.message.text.replace(',', ''))
        p, w, a, pr, t = context.user_data['price'], context.user_data['weight'], context.user_data['ajrat'], context.user_data['profit'], context.user_data['tax']
        base = p * w; aj = base * (a / 100); prof = (base + aj) * (pr / 100); tax = (aj + prof) * (t / 100); final = base + aj + prof + tax
        log_event(update.effective_user.id, "calc_gold_done", f"weight_{w}_final_{final}")
        await update.message.reply_text(f"✅ <b>فاکتور محاسبه طلا</b>\n\n⚖️ وزن: {w} گرم\n💰 قیمت پایه: {base:,.0f}\n🛠 اجرت ({a}%): {aj:,.0f}\n📈 سود ({pr}%): {prof:,.0f}\n🧾 مالیات ({t}%): {tax:,.0f}\n➖➖➖➖➖➖➖➖\n💳 <b>قیمت نهایی: {final:,.0f} تومان</b>\n\n💰 @diginavasan", parse_mode='HTML', reply_markup=get_main_menu())
        return ConversationHandler.END
    except ValueError: await update.message.reply_text("لطفاً فقط عدد وارد کنید:", reply_markup=get_cancel_keyboard()); return TAX

# ==========================================
# 💸 ماشین‌حساب اقساط
# ==========================================
LOAN_AMT, LOAN_INT, LOAN_MONTHS = range(10, 13)
async def loan_calc_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    log_event(update.effective_user.id, "open_loan_calculator")
    await update.message.reply_text("💸 <b>ماشین‌حساب اقساط وام</b>\n\nلطفاً مبلغ کل وام را <b>به تومان</b> وارد کنید (مثلاً 100000000):", parse_mode='HTML', reply_markup=get_cancel_keyboard())
    return LOAN_AMT
async def loan_get_amt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == "🔙 لغو / بازگشت": return await cancel(update, context)
    try:
        context.user_data['loan_amt'] = float(update.message.text.replace(',', ''))
        await update.message.reply_text("درصد سود سالانه چقدر است؟ (مثلاً 23)", reply_markup=get_cancel_keyboard())
        return LOAN_INT
    except ValueError: await update.message.reply_text("لطفاً فقط عدد وارد کنید:", reply_markup=get_cancel_keyboard()); return LOAN_AMT
async def loan_get_int(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == "🔙 لغو / بازگشت": return await cancel(update, context)
    try:
        context.user_data['loan_int'] = float(update.message.text.replace(',', ''))
        await update.message.reply_text("در چند ماه (قسط) می‌خواهید تسویه کنید؟ (مثلاً 36)", reply_markup=get_cancel_keyboard())
        return LOAN_MONTHS
    except ValueError: await update.message.reply_text("لطفاً فقط عدد وارد کنید:", reply_markup=get_cancel_keyboard()); return LOAN_INT
async def loan_get_months(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == "🔙 لغو / بازگشت": return await cancel(update, context)
    try:
        months = int(update.message.text.replace(',', '')); P = context.user_data['loan_amt']; r = (context.user_data['loan_int'] / 100) / 12
        if r == 0: installment = P / months
        else: installment = P * r * ((1 + r)**months) / (((1 + r)**months) - 1)
        total_pay = installment * months; total_int = total_pay - P
        log_event(update.effective_user.id, "calc_loan_done", f"amount_{P}_months_{months}")
        await update.message.reply_text(f"✅ <b>نتیجه محاسبه وام</b>\n\n💵 مبلغ وام: {P:,.0f} تومان\n📊 سود سالانه: {context.user_data['loan_int']}%\n⏳ مدت: {months} ماه\n\n➖➖➖➖➖➖➖➖\n💳 <b>قسط ماهانه: {installment:,.0f} تومان</b>\n💰 <b>کل مبلغ پرداختی: {total_pay:,.0f} تومان</b>\n📈 <b>کل سود پرداختی: {total_int:,.0f} تومان</b>\n\n💰 @diginavasan", parse_mode='HTML', reply_markup=get_main_menu())
        return ConversationHandler.END
    except ValueError: await update.message.reply_text("لطفاً فقط عدد وارد کنید:", reply_markup=get_cancel_keyboard()); return LOAN_MONTHS

# ==========================================
# 💱 مبدل هوشمند ارز
# ==========================================
CURRENCY_AMT, CURRENCY_TYPE = range(20, 22)
async def currency_conv_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    log_event(update.effective_user.id, "open_currency_converter")
    await update.message.reply_text("💱 <b>مبدل هوشمند ارز</b>\n\nلطفاً مبلغ را به <b>تومان</b> وارد کنید (مثلاً 5000000):", parse_mode='HTML', reply_markup=get_cancel_keyboard())
    return CURRENCY_AMT
async def conv_get_amt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == "🔙 لغو / بازگشت": return await cancel(update, context)
    try:
        context.user_data['conv_amt'] = float(update.message.text.replace(',', ''))
        keyboard = [["دلار", "یورو", "پوند"], ["بیت‌کوین", "تتر"], ["🔙 لغو / بازگشت"]]
        await update.message.reply_text("می‌خواهید معادل چه چیزی را ببینید؟", reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True))
        return CURRENCY_TYPE
    except ValueError: await update.message.reply_text("لطفاً فقط عدد وارد کنید:", reply_markup=get_cancel_keyboard()); return CURRENCY_AMT
async def conv_get_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == "🔙 لغو / بازگشت": return await cancel(update, context)
    choice = update.message.text; amt = context.user_data['conv_amt']; result = 0
    if choice == "دلار": result = amt / get_nerkh_price("dollar") if get_nerkh_price("dollar") else 0
    elif choice == "یورو": result = amt / get_nerkh_price("euro") if get_nerkh_price("euro") else 0
    elif choice == "پوند": result = amt / get_nerkh_price("pound") if get_nerkh_price("pound") else 0
    elif choice in ["بیت‌کوین", "تتر"]:
        crypto = get_crypto_prices_toman(); sym = "BTC" if choice == "بیت‌کوین" else "USDT"
        if sym in crypto: result = amt / int(crypto[sym].replace(',', ''))
    log_event(update.effective_user.id, "calc_currency_done", f"to_{choice}")
    await update.message.reply_text(f"💱 {amt:,.0f} تومان معادل <b>{result:,.4f} {choice}</b> است.\n\n💰 @diginavasan", parse_mode='HTML', reply_markup=get_main_menu())
    return ConversationHandler.END

# ==========================================
# 🌤 وضعیت آب‌وهوا
# ==========================================
WEATHER_CITY = 40
async def weather_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    log_event(update.effective_user.id, "open_weather")
    await update.message.reply_text("🌤 <b>وضعیت آب‌وهوا</b>\n\nلطفاً نام شهر خود را وارد کنید (مثلاً تهران یا Mashhad):", parse_mode='HTML', reply_markup=get_cancel_keyboard())
    return WEATHER_CITY
async def weather_get_city(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == "🔙 لغو / بازگشت": return await cancel(update, context)
    city = urllib.parse.quote(update.message.text.strip())
    try:
        res = requests.get(f"https://wttr.in/{city}?format=j1", timeout=10)
        if res.status_code == 200:
            data = res.json()['current_condition'][0]; temp = data['temp_C']; desc = data['lang_fa'][0]['value'] if 'lang_fa' in data else data['weatherDesc'][0]['value']
            await update.message.reply_text(f"🌤 <b>وضعیت آب‌وهوای {update.message.text}</b>\n\n🌡 دما: {temp} درجه سانتی‌گراد\n☁️ وضعیت: {desc}\n\n💰 @diginavasan", parse_mode='HTML', reply_markup=get_main_menu())
        else: await update.message.reply_text("شهر پیدا نشد. لطفاً با حروف انگلیسی یا نام دقیق وارد کنید.", reply_markup=get_main_menu())
    except: await update.message.reply_text("خطا در دریافت اطلاعات آب‌وهوا.", reply_markup=get_main_menu())
    return ConversationHandler.END

# ==========================================
# 🚨 آلارم قیمتی
# ==========================================
ALERT_ASSET, ALERT_PRICE = range(50, 52)
async def alert_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    log_event(update.effective_user.id, "open_alert")
    user_id = update.effective_user.id
    invites = get_user_invites(user_id)
    current_alerts = len(user_alerts.get(user_id, []))
    if invites == 0 and current_alerts >= 1:
        invite_link = f"https://t.me/diginavasan_bot?start={user_id}"
        msg = f"⚠️ <b>شما ۱ آلارم رایگان خود را ثبت کرده‌اید.</b>\n\nبرای ثبت <b>نامحدود</b> آلارم قیمتی (طلا، دلار، بیت‌کوین)، کافیه فقط <b>۱ نفر</b> از دوستان خود را با لینک زیر به ربات دعوت کنید:\n\n🔗 <code>{invite_link}</code>\n\nبعد از دعوت، قفل آلارم‌ها برای همیشه باز می‌شود."
        await update.message.reply_text(msg, parse_mode='HTML', reply_markup=get_main_menu())
        return ConversationHandler.END
    keyboard = [["دلار", "طلا", "بیت‌کوین"], ["🔙 لغو / بازگشت"]]
    await update.message.reply_text("🚨 <b>تنظیم آلارم قیمتی</b>\n\nبرای کدام مورد می‌خواهید آلارم تنظیم کنید؟", parse_mode='HTML', reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True))
    return ALERT_ASSET
async def alert_get_asset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == "🔙 لغو / بازگشت": return await cancel(update, context)
    context.user_data['alert_asset'] = update.message.text
    await update.message.reply_text(f"قیمت هدف برای {update.message.text} را به <b>تومان</b> وارد کنید (مثلاً 80000):", parse_mode='HTML', reply_markup=get_cancel_keyboard())
    return ALERT_PRICE
async def alert_get_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == "🔙 لغو / بازگشت": return await cancel(update, context)
    try:
        target = int(update.message.text.replace(',', '')); user_id = update.effective_user.id
        if user_id not in user_alerts: user_alerts[user_id] = []
        user_alerts[user_id].append({'asset': context.user_data['alert_asset'], 'target': target})
        log_event(user_id, "set_alert", f"{context.user_data['alert_asset']}_{target}")
        invites = get_user_invites(user_id)
        limit_msg = "\n(شما به ۱ آلارم رایگان خود رسیده‌اید. برای آلارم بیشتر ۱ نفر را دعوت کنید)" if invites == 0 else ""
        await update.message.reply_text(f"✅ آلارم تنظیم شد.\n\nبه محض اینکه قیمت {context.user_data['alert_asset']} به {target:,.0f} تومان برسد، به شما اطلاع می‌دهم.\n(ربات هر ۵ دقیقه چک می‌کند){limit_msg}", reply_markup=get_main_menu())
        return ConversationHandler.END
    except ValueError: await update.message.reply_text("لطفاً فقط عدد وارد کنید:", reply_markup=get_cancel_keyboard()); return ALERT_PRICE

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("عملیات لغو شد. به منوی اصلی بازگشتید.", reply_markup=get_main_menu())
    return ConversationHandler.END

async def handle_menu_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip(); user_id = update.effective_user.id; chat_id = update.effective_chat.id
    if "استارت" in text or "منوی اصلی" in text: await start(update, context)
    elif "قیمت ارزها" in text: log_event(user_id, "view_fiat_prices"); await send_fiat_to_channel(context, chat_id=chat_id)
    elif "قیمت طلا و سکه" in text: log_event(user_id, "view_gold_prices"); await send_gold_to_channel(context, chat_id=chat_id)
    elif "قیمت ارز دیجیتال" in text: log_event(user_id, "view_crypto_prices"); await send_crypto_to_channel(context, chat_id=chat_id)
    elif "ماشین‌حساب طلا" in text: await gold_calc_start(update, context)
    elif "ماشین‌حساب اقساط" in text: await loan_calc_start(update, context)
    elif "مبدل هوشمند ارز" in text: await currency_conv_start(update, context)
    elif "وضعیت آب‌وهوا" in text: await weather_start(update, context)
    elif "آلارم قیمتی" in text: await alert_start(update, context)
    elif "چت با هوش مصنوعی" in text: await ai_chat_start(update, context)
    elif "دعوت از دوستان" in text:
        log_event(user_id, "view_invite_menu")
        try:
            invite_link = f"https://t.me/diginavasan_bot?start={user_id}"; invite_count = get_user_invites(user_id)
            invite_text = f"🎁 <b>دعوت از دوستان به دیجی نوسان</b> 🌟\n\n👥 تعداد افرادی که تاکنون دعوت کرده‌اید: <b>{invite_count}</b> نفر\n\n🔗 <b>لینک اختصاصی دعوت شما:</b>\n{invite_link}\n\nبا دعوت از ۱ نفر، قابلیت هوش مصنوعی و آلارم قیمتی برای شما نامحدود می‌شود!"
            share_text = f"سلام! خواستم این کانال و ربات فوق‌العاده رو بهت معرفی کنم. 🤝\n\n📢 کانال دیجی نوسان:\n👉 https://t.me/diginavasan\n\n🤖 ربات اختصاصی دیجی نوسان:\n👉 {invite_link}"
            keyboard = [[InlineKeyboardButton("📤 ارسال به مخاطبین", url=f"https://t.me/share/url?url={invite_link}&text={urllib.parse.quote(share_text)}")]]
            await context.bot.send_message(chat_id=chat_id, text=invite_text, parse_mode='HTML', reply_markup=InlineKeyboardMarkup(keyboard))
        except: pass

# ==========================================
# ⚙️ اجرای ربات
# ==========================================
if __name__ == '__main__':
    threading.Thread(target=run_dummy_server, daemon=True).start()
    app = ApplicationBuilder().token(TOKEN).build()
    
    fallbacks_list = [
        CommandHandler('cancel', cancel), 
        MessageHandler(filters.Regex("^🔙 لغو / بازگشت$"), cancel),
        MessageHandler(filters.Regex(MENU_BUTTONS_REGEX), handle_menu_text)
    ]

    app.add_handler(ConversationHandler(entry_points=[MessageHandler(filters.Regex("ماشین‌حساب طلا"), gold_calc_start)], states={PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.Regex("^🔙 لغو / بازگشت$"), get_price)], WEIGHT: [MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.Regex("^🔙 لغو / بازگشت$"), get_weight)], AJRAT: [MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.Regex("^🔙 لغو / بازگشت$"), get_ajrat)], PROFIT: [MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.Regex("^🔙 لغو / بازگشت$"), get_profit)], TAX: [MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.Regex("^🔙 لغو / بازگشت$"), get_tax)]}, fallbacks=fallbacks_list, allow_reentry=True))
    
    app.add_handler(ConversationHandler(entry_points=[MessageHandler(filters.Regex("ماشین‌حساب اقساط"), loan_calc_start)], states={LOAN_AMT: [MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.Regex("^🔙 لغو / بازگشت$"), loan_get_amt)], LOAN_INT: [MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.Regex("^🔙 لغو / بازگشت$"), loan_get_int)], LOAN_MONTHS: [MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.Regex("^🔙 لغو / بازگشت$"), loan_get_months)]}, fallbacks=fallbacks_list, allow_reentry=True))
    
    app.add_handler(ConversationHandler(entry_points=[MessageHandler(filters.Regex("مبدل هوشمند ارز"), currency_conv_start)], states={CURRENCY_AMT: [MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.Regex("^🔙 لغو / بازگشت$"), conv_get_amt)], CURRENCY_TYPE: [MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.Regex("^🔙 لغو / بازگشت$"), conv_get_type)]}, fallbacks=fallbacks_list, allow_reentry=True))
    
    app.add_handler(ConversationHandler(entry_points=[MessageHandler(filters.Regex("وضعیت آب‌وهوا"), weather_start)], states={WEATHER_CITY: [MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.Regex("^🔙 لغو / بازگشت$"), weather_get_city)]}, fallbacks=fallbacks_list, allow_reentry=True))
    
    app.add_handler(ConversationHandler(entry_points=[MessageHandler(filters.Regex("آلارم قیمتی"), alert_start)], states={ALERT_ASSET: [MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.Regex("^🔙 لغو / بازگشت$"), alert_get_asset)], ALERT_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.Regex("^🔙 لغو / بازگشت$"), alert_get_price)]}, fallbacks=fallbacks_list, allow_reentry=True))

    app.add_handler(ConversationHandler(entry_points=[MessageHandler(filters.Regex("چت با هوش مصنوعی"), ai_chat_start)], states={AI_CHAT: [MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.Regex("^🔙 لغو / بازگشت$"), ai_chat_message)]}, fallbacks=fallbacks_list, allow_reentry=True))

    app.add_handler(CommandHandler('start', start))
    app.add_handler(CommandHandler('status', partner_status))
    app.add_handler(CommandHandler('admin_finance', admin_finance))
    
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE, handle_menu_text))
    
    app.add_handler(ChatMemberHandler(track_chats, ChatMemberHandler.MY_CHAT_MEMBER))
    
    tehran_tz = pytz.timezone('Asia/Tehran')
    app.job_queue.run_daily(send_morning_message, time=time(8, 0, tzinfo=tehran_tz))
    app.job_queue.run_daily(send_night_message, time=time(20, 0, tzinfo=tehran_tz))
    app.job_queue.run_repeating(rotate_jobs, interval=900, first=10)
    app.job_queue.run_repeating(check_alerts, interval=300, first=10)
    app.job_queue.run_repeating(send_hourly_summary_to_externals, interval=3600, first=10)
    
    print("ربات روشن شد. سیستم دکمه دعوت اختصاصی فعال شد...")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)
