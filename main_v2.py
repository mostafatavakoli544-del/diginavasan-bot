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
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

TEMPLATES = {
    "fiat_title": "💱 **قیمت لحظه‌ای ارزها**",
    "gold_title": "🌕 **قیمت لحظه‌ای طلا و سکه**",
    "crypto_title": "🪙 **قیمت لحظه‌ای ارز دیجیتال**",
    "footer": "\n*💰 قیمت لحظه‌ای دیجی نوسان 👇*\n@diginavasan"
}

HAS_RAQM = features.check("raqm")

# ==========================================
# ⚙️ توابع پایه (دقیقا مثل نسخه قبلی)
# ==========================================
def run_dummy_server():
    Handler = http.server.SimpleHTTPRequestHandler
    with socketserver.TCPServer(("", PORT), Handler) as httpd:
        httpd.serve_forever()

def get_dates_info():
    tehran_tz = pytz.timezone('Asia/Tehran')
    now = datetime.now(tehran_tz)
    time_str = now.strftime('%H:%M')
    return time_str, now.hour

def is_working_hours():
    _, hour = get_dates_info()
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
        coins = {"BTC": "btc", "ETH": "eth", "USDT": "usdt", "BNB": "bnb"}
        prices = {}
        for symbol, coin_id in coins.items():
            url = f"https://apiv2.nobitex.ir/market/stats?srcCurrency={coin_id}&dstCurrency=rls"
            res = requests.get(url, headers=headers, timeout=5)
            if res.status_code == 200:
                data = res.json()
                key = f"{coin_id}-rls"
                if 'stats' in data and key in data['stats']:
                    price_toman = int(float(data['stats'][key]['latest']) / 10)
                    prices[symbol] = f"{price_toman:,.0f}"
        return prices
    except Exception:
        return {}

def download_image(url, size=(60, 60)):
    try:
        response = requests.get(url, timeout=5)
        img = Image.open(BytesIO(response.content)).convert("RGBA")
        img = img.resize(size, Image.Resampling.LANCZOS)
        return img
    except Exception as e:
        print(f"Failed to load icon: {e}")
        return None

# ==========================================
# 🎨 تابع ساخت عکس (نسخه هوشمند v2)
# ==========================================
def generate_infographic():
    font_path = "vazir.ttf"
    if not os.path.exists(font_path):
        try:
            font_url = "https://cdn.jsdelivr.net/gh/rastikerdar/vazirmatn@v33.003/fonts/ttf/Vazirmatn-Bold.ttf"
            r = requests.get(font_url, timeout=20)
            with open(font_path, 'wb') as f:
                f.write(r.content)
        except Exception as e:
            print(f"Font download failed: {e}")

    try:
        img = Image.open("template.jpg").convert("RGBA")
    except Exception:
        img = Image.new('RGB', (1080, 1920), color=(15, 25, 45))
    
    draw = ImageDraw.Draw(img)
    w, h = img.size
    scale = w / 1080.0
    
    try:
        font_title = ImageFont.truetype(font_path, int(45 * scale))
        font_text_left = ImageFont.truetype(font_path, int(32 * scale)) 
        font_text_right = ImageFont.truetype(font_path, int(30 * scale))
    except Exception:
        font_title = ImageFont.load_default()
        font_text_left = ImageFont.load_default()
        font_text_right = ImageFont.load_default()

    def fa(text):
        if HAS_RAQM: return text
        return get_display(arabic_reshaper.reshape(text))

    # استروک مشکی برای خوانایی
    def draw_text(text, x, y, font, color, anchor, stroke=True):
        sw = 1 if stroke else 0
        if HAS_RAQM:
            draw.text((x, y), text, font=font, fill=color, anchor=anchor, direction="rtl", stroke_width=sw, stroke_fill=(0,0,0))
        else:
            draw.text((x, y), text, font=font, fill=color, anchor=anchor, stroke_width=sw, stroke_fill=(0,0,0))

    time_str, _ = get_dates_info()
    
    # هدر
    draw_text("دیجی نوسان", w * 0.35, h * 0.065, font_title, (20, 30, 50), "mm", stroke=False)
    draw_text(time_str, w * 0.82, h * 0.065, font_text_right, (20, 30, 50), "mm", stroke=False)

    # داده‌ها
    usd = get_nerkh_price_safe(["dollar"])
    eur = get_nerkh_price_safe(["euro"])
    gbp = get_nerkh_price_safe(["pound"])
    aed = get_nerkh_price_safe(["aed"])
    lira = get_nerkh_price_safe(["lira"])
    cny = get_nerkh_price_safe(["yuan"])
    cad = get_nerkh_price_safe(["cad"])
    aud = get_nerkh_price_safe(["aud"])
    
    gold18 = get_nerkh_price_safe(["gold"])
    gold24 = get_nerkh_price_safe(["gold24"])
    coin_emami = get_nerkh_price_safe(["coin"])
    coin_bahar = get_nerkh_price_safe(["bahar"])
    coin_nim = get_nerkh_price_safe(["half"])
    coin_rob = get_nerkh_price_safe(["quarter"])
    
    crypto = get_crypto_prices_toman()
    icon_size = int(32 * scale)

    # ۳. ستون چپ (ارزها)
    y_start_left = h * 0.235
    y_step_left = (h * 0.68) / 8
    
    currencies = [
        ("us", f"دلار: {usd}"), ("eu", f"یورو: {eur}"), ("gb", f"پوند: {gbp}"), ("ae", f"درهم: {aed}"),
        ("tr", f"لیر: {lira}"), ("cn", f"یوان: {cny}"), ("ca", f"دلار کانادا: {cad}"), ("au", f"دلار استرالیا: {aud}")
    ]
    
    for i, (country_code, text) in enumerate(currencies):
        y_pos = y_start_left + (i * y_step_left) + (y_step_left / 2)
        icon = download_image(f"https://flagcdn.com/w80/{country_code}.png", size=(icon_size, icon_size))
        if icon:
            img.paste(icon, (int(w * 0.08) - icon_size//2, int(y_pos) - icon_size//2), icon)
        draw_text(fa(text), w * 0.43, y_pos, font_text_left, (255, 255, 255), "rm")

    # ۴. کادر بالا سمت راست (طلا و سکه)
    y_start_top = h * 0.235
    y_step_top = (h * 0.31) / 6
    
    gold_items = [
        (f"طلا ۱۸: {gold18}", "https://cdn-icons-png.flaticon.com/512/2845/2845687.png"),
        (f"طلا ۲۴: {gold24}", "https://cdn-icons-png.flaticon.com/512/2845/2845687.png"),
        (f"سکه امامی: {coin_emami}", "https://cdn-icons-png.flaticon.com/512/217/217488.png"),
        (f"سکه بهار: {coin_bahar}", "https://cdn-icons-png.flaticon.com/512/217/217488.png"),
        (f"نیم سکه: {coin_nim}", "https://cdn-icons-png.flaticon.com/512/217/217488.png"),
        (f"ربع سکه: {coin_rob}", "https://cdn-icons-png.flaticon.com/512/217/217488.png")
    ]

    for i, (text, icon_url) in enumerate(gold_items):
        y_pos = y_start_top + (i * y_step_top) + (y_step_top / 2)
        icon = download_image(icon_url, size=(icon_size, icon_size))
        if icon:
            img.paste(icon, (int(w * 0.91) - icon_size//2, int(y_pos) - icon_size//2), icon)
        # متن ها دقیقا از لبه کادر تا لوگو فاصله گرفتن
        draw_text(fa(text), w * 0.86, y_pos, font_text_right, (255, 255, 255), "rm")

    # ۵. کادر پایین سمت راست (کریپتو)
    y_start_bot = h * 0.615
    y_step_bot = (h * 0.245) / 4
    
    crypto_icons = {
        "BTC": "https://cryptologos.cc/logos/bitcoin-btc-logo.png",
        "ETH": "https://cryptologos.cc/logos/ethereum-eth-logo.png",
        "USDT": "https://cryptologos.cc/logos/tether-usdt-logo.png",
        "BNB": "https://cryptologos.cc/logos/bnb-bnb-logo.png"
    }

    if crypto:
        crypto_items = [(f"{sym}: {prc}", crypto_icons.get(sym, crypto_icons["BTC"])) for sym, prc in crypto.items()]
    else:
        crypto_items = [("خطا در دریافت", crypto_icons["BTC"])]

    for i, (text, icon_url) in enumerate(crypto_items):
        y_pos = y_start_bot + (i * y_step_bot) + (y_step_bot / 2)
        icon = download_image(icon_url, size=(icon_size, icon_size))
        if icon:
            img.paste(icon, (int(w * 0.91) - icon_size//2, int(y_pos) - icon_size//2), icon)
        draw_text(fa(text), w * 0.86, y_pos, font_text_right, (255, 255, 255), "rm")

    bio = BytesIO()
    bio.name = 'summary.jpeg'
    img = img.convert("RGB")
    img.save(bio, 'JPEG', quality=95)
    bio.seek(0)
    return bio

# ==========================================
# ⚙️ بقیه کدها دقیقا کپی میشن...
# ==========================================
async def send_fiat_to_channel(context: ContextTypes.DEFAULT_TYPE):
    time_str, _ = get_dates_info()
    usd = get_nerkh_price_safe(["dollar", "usd"])
    eur = get_nerkh_price_safe(["euro", "eur"])
    gbp = get_nerkh_price_safe(["pound", "gbp"])
    aed = get_nerkh_price_safe(["aed"])
    try_price = get_nerkh_price_safe(["lira"])
    cny = get_nerkh_price_safe(["yuan"])
    
    msg = f"""{TEMPLATES['fiat_title']}
⏰ ساعت آپدیت: \u200e`{time_str}`

💵 دلار = \u200e`{usd}` تومان
💶 یورو = \u200e`{eur}` تومان
💷 پوند = \u200e`{gbp}` تومان
🇦🇪 درهم = \u200e`{aed}` تومان
🇹🇷 لیر = \u200e`{try_price}` تومان
🇨🇳 یوان = \u200e`{cny}` تومان
{TEMPLATES['footer']}"""
    await context.bot.send_message(chat_id=CHANNEL_ID, text=msg, parse_mode='Markdown')

async def send_gold_to_channel(context: ContextTypes.DEFAULT_TYPE):
    time_str, _ = get_dates_info()
    gold18 = get_nerkh_price_safe(["gold"])
    coin_emami = get_nerkh_price_safe(["coin"])
    coin_bahar = get_nerkh_price_safe(["bahar"])
    coin_nim = get_nerkh_price_safe(["half"])
    coin_rob = get_nerkh_price_safe(["quarter"])
    
    msg = f"""{TEMPLATES['gold_title']}
⏰ ساعت آپدیت: \u200e`{time_str}`

🌕 طلا ۱۸ عیار = \u200e`{gold18}` تومان
🪙 سکه امامی = \u200e`{coin_emami}` تومان
🪙 سکه بهار = \u200e`{coin_bahar}` تومان
🪙 نیم سکه = \u200e`{coin_nim}` تومان
🪙 ربع سکه = \u200e`{coin_rob}` تومان
{TEMPLATES['footer']}"""
    await context.bot.send_message(chat_id=CHANNEL_ID, text=msg, parse_mode='Markdown')

async def send_crypto_to_channel(context: ContextTypes.DEFAULT_TYPE):
    time_str, _ = get_dates_info()
    crypto = get_crypto_prices_toman()
    
    msg = f"""{TEMPLATES['crypto_title']}
⏰ ساعت آپدیت: \u200e`{time_str}`

"""
    if crypto:
        for sym, prc in crypto.items():
            msg += f"🔸 {sym} = \u200e`{prc}` تومان\n"
    else:
        msg += "⚠️ خطا در دریافت اطلاعات\n"
        
    msg += TEMPLATES['footer']
    await context.bot.send_message(chat_id=CHANNEL_ID, text=msg, parse_mode='Markdown')

async def send_image_summary_to_channel(context: ContextTypes.DEFAULT_TYPE, chat_id=CHANNEL_ID):
    img_bio = generate_infographic()
    await context.bot.send_photo(
        chat_id=chat_id, 
        photo=img_bio, 
        caption="📊 **خلاصه وضعیت بازار**\n\n💰 @diginavasan",
        parse_mode='Markdown'
    )

async def send_morning_message(context: ContextTypes.DEFAULT_TYPE):
    msg = f"""🌅 **بسم الله الرحمن الرحیم**
صبح بخیر! بازار امروز آغاز شد. ☀️

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
    jobs = [send_fiat_to_channel, send_gold_to_channel, send_crypto_to_channel, send_image_summary_to_channel]
    current_job = jobs[_rotation_index % len(jobs)]
    _rotation_index += 1
    await current_job(context)

def get_main_menu():
    keyboard = [
        [KeyboardButton("🚀 استارت / منوی اصلی")],
        [KeyboardButton("💱 قیمت ارزها"), KeyboardButton("🌕 قیمت طلا و سکه")],
        [KeyboardButton("🪙 قیمت ارز دیجیتال"), KeyboardButton("🧮 ماشین‌حساب طلا")],
        [KeyboardButton("🖼️ تست عکس (TEMP)")],
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

    welcome_msg = f"""سلام **{first_name}** عزیز! به ربات دیجی نوسان خوش آمدید. ✅

👥 تعداد دعوت‌های شما تاکنون: **{invite_count}** نفر

👇 **از منوی زیر (پایین صفحه) یکی از گزینه‌ها را انتخاب کنید:**"""
    await context.bot.send_message(chat_id=user_id, text=welcome_msg, parse_mode='Markdown', reply_markup=get_main_menu())

async def handle_menu_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id

    if "استارت" in text or "منوی اصلی" in text:
        await start(update, context)
        return

    if "قیمت ارزها" in text:
        await send_fiat_to_channel(context)
    elif "قیمت طلا و سکه" in text:
        await send_gold_to_channel(context)
    elif "قیمت ارز دیجیتال" in text:
        await send_crypto_to_channel(context)
    elif "تست عکس" in text:
        await send_image_summary_to_channel(context, chat_id=chat_id)
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
# ⚙️ اجرای ربات (تغییر یافته برای main_v2)
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
            MessageHandler(filters.Regex("^(🚀 استارت / منوی اصلی|🎁 دعوت از دوستان|💱 قیمت ارزها|🌕 قیمت طلا و سکه|🪙 قیمت ارز دیجیتال|🖼️ تست عکس \\(TEMP\\))$"), handle_menu_text)
        ],
        allow_reentry=True
    )
    
    app.add_handler(gold_conv_handler)
    app.add_handler(CommandHandler('start', start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_menu_text))
    
    tehran_tz = pytz.timezone('Asia/Tehran')
    app.job_queue.run_daily(send_morning_message, time=time(8, 0, tzinfo=tehran_tz))
    app.job_queue.run_daily(send_night_message, time=time(20, 0, tzinfo=tehran_tz))
    
    app.job_queue.run_repeating(rotate_jobs, interval=900, first=10)
    
    print("نسخه ۲ ربات روشن شد. لایه‌بندی هوشمند فعال است...")
    app.run_polling()
