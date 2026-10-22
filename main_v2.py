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
from pictex import Canvas, Row, Column, Text, Image as PicImage, Shadow, LinearGradient
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

# ==========================================
# 🎨 تابع ساخت عکس (کاملاً گرافیکی با PicTex)
# ==========================================
def generate_infographic_pictex():
    # ۱. دانلود فونت
    font_path = "vazir.ttf"
    if not os.path.exists(font_path):
        r = requests.get("https://cdn.jsdelivr.net/gh/rastikerdar/vazirmatn@v33.003/fonts/ttf/Vazirmatn-Bold.ttf")
        with open(font_path, 'wb') as f:
            f.write(r.content)
    
    # ۲. دریافت داده‌ها
    time_str, _ = get_dates_info()
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

    # ۳. ساخت استایل‌ها
    title_style = Text("").font_family(font_path).font_size(45).font_weight(700).color("#FFD700")
    text_style = Text("").font_family(font_path).font_size(28).color("white")
    small_style = Text("").font_family(font_path).font_size(22).color("#E0E0E0")

    # ۴. ساخت بخش هدر
    header = Row(
        Text("دیجی نوسان").font_family(font_path).font_size(50).font_weight(700).color("#FFD700"),
        Text(time_str).font_family(font_path).font_size(35).color("#AAAAAA"),
    ).width("100%").justify_content("space-between").align_items("center").padding(30)

    # ۵. ساخت بخش ارزها (ستون چپ)
    currencies = [
        (f"دلار: {usd}", "us"), (f"یورو: {eur}", "eu"), (f"پوند: {gbp}", "gb"), (f"درهم: {aed}", "ae"),
        (f"لیر: {lira}", "tr"), (f"یوان: {cny}", "cn"), (f"دلار کانادا: {cad}", "ca"), (f"دلار استرالیا: {aud}", "au")
    ]
    
    currency_rows = []
    for text, country_code in currencies:
        flag = PicImage(f"https://flagcdn.com/w80/{country_code}.png").size(40, 40).border_radius("50%")
        row = Row(flag, Text(text).font_family(font_path).font_size(30).color("white")).align_items("center").gap(15)
        currency_rows.append(row)

    currency_section = Column(*currency_rows).background_color("#1E2A38").border_radius(20).padding(20).gap(15).width("45%")

    # ۶. ساخت بخش طلا و کریپتو (ستون راست)
    gold_items = [
        (f"طلا ۱۸: {gold18}", "https://cdn-icons-png.flaticon.com/512/2845/2845687.png"),
        (f"طلا ۲۴: {gold24}", "https://cdn-icons-png.flaticon.com/512/2845/2845687.png"),
        (f"سکه امامی: {coin_emami}", "https://cdn-icons-png.flaticon.com/512/217/217488.png"),
        (f"سکه بهار: {coin_bahar}", "https://cdn-icons-png.flaticon.com/512/217/217488.png"),
        (f"نیم سکه: {coin_nim}", "https://cdn-icons-png.flaticon.com/512/217/217488.png"),
        (f"ربع سکه: {coin_rob}", "https://cdn-icons-png.flaticon.com/512/217/217488.png")
    ]
    
    gold_rows = []
    for text, icon_url in gold_items:
        icon = PicImage(icon_url).size(35, 35)
        row = Row(Text(text).font_family(font_path).font_size(28).color("white"), icon).align_items("center").justify_content("flex-end").gap(10)
        gold_rows.append(row)

    gold_section = Column(*gold_rows).background_color("#1E2A38").border_radius(20).padding(20).gap(12).width("50%")

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

    crypto_rows = []
    for text, icon_url in crypto_items:
        icon = PicImage(icon_url).size(35, 35)
        row = Row(Text(text).font_family(font_path).font_size(28).color("white"), icon).align_items("center").justify_content("flex-end").gap(10)
        crypto_rows.append(row)

    crypto_section = Column(*crypto_rows).background_color("#1E2A38").border_radius(20).padding(20).gap(12).width("50%")

    right_column = Column(gold_section, crypto_section).width("55%").gap(20)

    # ۷. چیدمان نهایی
    main_layout = Column(
        header,
        Row(currency_section, right_column).width("100%").align_items("flex-start").gap(20),
    ).background_color("#0F1A26").padding(30).gap(20)

    # ۸. رندر و خروجی
    canvas = Canvas(width=1080, height=1920)
    image = canvas.render(main_layout)
    
    bio = BytesIO()
    bio.name = 'summary.jpeg'
    image.save(bio, 'JPEG', quality=95)
    bio.seek(0)
    return bio

# ==========================================
# ⚙️ ارسال به کانال
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
    try:
        img_bio = generate_infographic_pictex()
        await context.bot.send_photo(
            chat_id=chat_id, 
            photo=img_bio, 
            caption="📊 **خلاصه وضعیت بازار**\n\n💰 @diginavasan",
            parse_mode='Markdown'
        )
    except Exception as e:
        print(f"خطا در ساخت عکس: {e}")

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
    global _rotation_index
    if not is_working_hours(): return
    
    # چرخش ۴ مرحله‌ای: ۳ پیام متنی + ۱ عکس
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
    
    # چرخش هر ۱۵ دقیقه (۹۰۰ ثانیه)
    app.job_queue.run_repeating(rotate_jobs, interval=900, first=10)
    
    print("ربات روشن شد. نسخه گرافیکی PicTex (بدون قالب) فعال است...")
    app.run_polling()
