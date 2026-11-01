import os
import logging
import requests
import threading
import http.server
import socketserver
import pytz
import asyncio
import jdatetime
import json
from datetime import datetime
from io import BytesIO
from PIL import Image, ImageDraw, ImageFont
import arabic_reshaper
from bidi.algorithm import get_display

from baleio import Bot, Dispatcher, md
from baleio.client.default import DefaultBotProperties
from baleio.enums import ChatType
from baleio.filters import CommandStart
from baleio.types import Message, ReplyKeyboardMarkup, KeyboardButton

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

# ==========================================
# تنظیمات اصلی
# ==========================================
BALE_TOKEN = os.environ.get("BALE_TOKEN")
PORT = int(os.environ.get("PORT", 10000))
CHANNEL_ID = "@diginavasan"
ADMIN_ID = 718840144
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
BRS_API_KEY = os.environ.get("BRS_API_KEY")

FONT_PATH = "fonts/Vazirmatn-Bold.ttf"
CHANNEL_FOOTER = "\n\n────────────\n🤖 [ورود به ربات دیجی نوسان](https://ble.ir/diginavasan_bot) | 📢 [کانال دیجی نوسان](https://ble.ir/diginavasan)"

PLATE_DATA_URL = "https://raw.githubusercontent.com/mostafatavakoli544-del/diginavasan-bot/refs/heads/main/plates.json"
DEFAULT_PLATE_DATA = {
    "11": "تهران", "22": "تهران", "33": "تهران", "44": "تهران", "55": "تهران", "66": "تهران", "77": "تهران", "88": "تهران", "99": "تهران",
    "10": "تهران", "20": "تهران", "30": "تهران", "40": "تهران", "50": "تهران", "60": "تهران", "70": "تهران", "80": "تهران", "90": "تهران",
    "21": "البرز (کرج) / تهران", "38": "البرز (کرج) / تهران", "68": "البرز (کرج) / تهران", "78": "البرز (کرج) / تهران",
    "12": "خراسان رضوی", "32": "خراسان رضوی", "36": "خراسان رضوی", "42": "خراسان رضوی", "74": "خراسان رضوی",
    "13": "اصفهان", "23": "اصفهان", "43": "اصفهان", "53": "اصفهان", "67": "اصفهان",
    "63": "فارس", "73": "فارس", "83": "فارس", "93": "فارس",
    "14": "خوزستان", "24": "خوزستان", "34": "خوزستان",
    "15": "آذربایجان شرقی", "25": "آذربایجان شرقی", "35": "آذربایجان شرقی",
    "17": "آذربایجان غربی", "27": "آذربایجان غربی", "37": "آذربایجان غربی",
    "62": "مازندران", "72": "مازندران", "82": "مازندران", "92": "مازندران",
    "46": "گیلان", "56": "گیلان", "76": "گیلان",
    "45": "کرمان", "65": "کرمان", "75": "کرمان",
    "59": "گلستان", "69": "گلستان", "79": "قزوین", "89": "قزوین",
    "16": "قم", "18": "همدان", "28": "همدان", "19": "کرمانشاه", "29": "کرمانشاه",
    "26": "خراسان شمالی", "31": "لرستان", "41": "لرستان", "47": "مرکزی", "57": "مرکزی",
    "48": "بوشهر", "58": "بوشهر", "49": "کهگیلویه و بویراحمد", "51": "کردستان", "61": "کردستان",
    "52": "خراسان جنوبی", "54": "یزد", "64": "یزد", "71": "چهارمحال و بختیاری", "81": "چهارمحال و بختیاری",
    "84": "هرمزگان", "94": "هرمزگان", "85": "سیستان و بلوچستان", "95": "سیستان و بلوچستان",
    "86": "سمنان", "96": "سمنان", "87": "زنجان", "97": "زنجان", "91": "اردبیل", "98": "ایلام"
}
CURRENT_PLATE_DATA = DEFAULT_PLATE_DATA.copy()
CITY_TO_CODES = {}

INFO_CACHE = {}
user_states = {}

# 👇 این تابعی بود که جا افتاده بود 👇
def run_dummy_server():
    Handler = http.server.SimpleHTTPRequestHandler
    with socketserver.TCPServer(("", PORT), Handler) as httpd: httpd.serve_forever()

# ==========================================
# توابع کمکی و اینفوگرافیک
# ==========================================
def fa_text(text):
    if not text: return ""
    reshaped = arabic_reshaper.reshape(str(text))
    return get_display(reshaped)

def format_num(num):
    try: return f"{int(float(num)):,}"
    except: return "0"

def draw_rtl_text(draw, xy, text, font, fill, align="right"):
    bbox = draw.textbbox((0, 0), text, font=font)
    text_width = bbox[2] - bbox[0]
    if align == "right":
        draw.text((xy[0] - text_width, xy[1]), text, font=font, fill=fill)
    else:
        draw.text(xy, text, font=font, fill=fill)

def generate_infographic(data):
    width, height = 1000, 1500
    bg_color = (15, 25, 45)
    card_bg = (28, 45, 75)
    text_white = (255, 255, 255)
    text_gold = (255, 215, 0)
    
    img = Image.new('RGB', (width, height), bg_color)
    draw = ImageDraw.Draw(img)
    
    try:
        font_large = ImageFont.truetype(FONT_PATH, 50)
        font_medium = ImageFont.truetype(FONT_PATH, 35)
        font_small = ImageFont.truetype(FONT_PATH, 28)
        font_tiny = ImageFont.truetype(FONT_PATH, 22)
    except:
        font_large = font_medium = font_small = font_tiny = ImageFont.load_default()

    draw_rtl_text(draw, (width - 50, 50), fa_text("خلاصه وضعیت بازار"), font_large, text_white)
    now_str = datetime.now(pytz.timezone('Asia/Tehran')).strftime("%Y/%m/%d - %H:%M")
    draw_rtl_text(draw, (width - 50, 110), fa_text(now_str), font_small, (150, 150, 150))
    draw.line((50, 160, width-50, 160), fill=(100, 100, 100), width=2)

    # طلا و سکه
    y = 200
    draw.rounded_rectangle((50, y, width-50, y+220), radius=20, fill=card_bg)
    draw_rtl_text(draw, (width-80, y+20), fa_text("طلا و سکه"), font_medium, text_gold)
    draw_rtl_text(draw, (width-80, y+80), fa_text(f"طلای ۱۸ عیار: {format_num(data.get('gold_18', 0))} تومان"), font_small, text_white)
    draw_rtl_text(draw, (width-80, y+130), fa_text(f"سکه امامی: {format_num(data.get('coin', 0))} تومان"), font_small, text_white)

    # ارزها
    y = 450
    draw.rounded_rectangle((50, y, width-50, y+220), radius=20, fill=card_bg)
    draw_rtl_text(draw, (width-80, y+20), fa_text("ارزهای خارجی"), font_medium, text_gold)
    draw_rtl_text(draw, (width-80, y+80), fa_text(f"دلار: {format_num(data.get('dollar', 0))} تومان"), font_small, text_white)
    draw_rtl_text(draw, (width-80, y+130), fa_text(f"یورو: {format_num(data.get('euro', 0))} تومان"), font_small, text_white)

    # رمزارزها
    y = 700
    draw.rounded_rectangle((50, y, width-50, y+220), radius=20, fill=card_bg)
    draw_rtl_text(draw, (width-80, y+20), fa_text("رمزارزها"), font_medium, text_gold)
    draw_rtl_text(draw, (width-80, y+80), fa_text(f"بیت‌کوین: {format_num(data.get('btc', 0))} تومان"), font_small, text_white)
    draw_rtl_text(draw, (width-80, y+130), fa_text(f"اتریوم: {format_num(data.get('eth', 0))} تومان"), font_small, text_white)

    # تتر
    y = 950
    draw.rounded_rectangle((50, y, width-50, y+180), radius=20, fill=card_bg)
    draw_rtl_text(draw, (width-80, y+20), fa_text("تتر (USDT)"), font_medium, text_gold)
    draw_rtl_text(draw, (width-80, y+80), fa_text(f"بهترین قیمت: {format_num(data.get('usdt_best', 0))} تومان"), font_small, text_white)

    # نفت و نقره
    y = 1160
    draw.rounded_rectangle((50, y, width-50, y+180), radius=20, fill=card_bg)
    draw_rtl_text(draw, (width-80, y+20), fa_text("نفت و نقره (دلاری)"), font_medium, text_gold)
    draw_rtl_text(draw, (width-80, y+80), fa_text(f"نفت برنت: {data.get('oil_usd', 'N/A')} دلار"), font_small, text_white)
    draw_rtl_text(draw, (width-80, y+130), fa_text(f"نقره: {data.get('silver_usd', 'N/A')} دلار"), font_small, text_white)

    # فوتر
    draw.line((50, height-150, width-50, height-150), fill=(100, 100, 100), width=1)
    draw_rtl_text(draw, (width-50, height-120), fa_text("امکانات: قیمت لحظه‌ای | پلاک‌خوان | ماشین‌حساب | هوش مصنوعی"), font_tiny, (150, 150, 150))
    draw_rtl_text(draw, (width-50, height-80), fa_text("Digi Navasan"), font_small, text_gold)

    bio = BytesIO()
    img.save(bio, format="PNG")
    bio.seek(0)
    return bio.getvalue()

# ==========================================
# دریافت داده‌ها
# ==========================================
def get_nerkh_price(key):
    try:
        res = requests.get("https://nerkh.jahankhahan.shop/data/live.json", timeout=15)
        if res.status_code == 200: return int(res.json()['rates'].get(key, 0))
    except: pass
    return 0

def get_nobitex_token_price(symbol):
    try:
        url = f"https://apiv2.nobitex.ir/market/stats?srcCurrency={symbol}&dstCurrency=usdt"
        res = requests.get(url, timeout=15)
        if res.status_code == 200 and f"{symbol}-usdt" in res.json().get('stats', {}):
            return float(res.json()['stats'][f"{symbol}-usdt"]['latest'])
    except: pass
    return None

def get_multi_exchange_usdt_prices():
    results = {}
    try:
        res = requests.get("https://apiv2.nobitex.ir/market/stats?srcCurrency=usdt&dstCurrency=rls", timeout=15)
        if res.status_code == 200 and 'usdt-rls' in res.json().get('stats', {}):
            results['نوبیتکس'] = int(float(res.json()['stats']['usdt-rls']['latest']) / 10)
    except: pass
    return results

async def update_cache_task():
    global INFO_CACHE
    while True:
        try:
            usdt = get_multi_exchange_usdt_prices()
            INFO_CACHE = {
                "dollar": get_nerkh_price('dollar'), "euro": get_nerkh_price('euro'),
                "gold_18": get_nerkh_price('gold'), "coin": get_nerkh_price('coin'),
                "btc": 0, "eth": 0, # فعلا برای سادگی صفر گذاشتم
                "usdt_best": min(usdt.values()) if usdt else 0,
                "oil_usd": get_nobitex_token_price('usoon'), "silver_usd": get_nobitex_token_price('slvon')
            }
        except Exception as e:
            logging.error(f"Cache Update Error: {e}")
        await asyncio.sleep(60)

async def channel_scheduler(bot: Bot):
    last_sent_minute = -1
    while True:
        try:
            now = datetime.now(pytz.timezone('Asia/Tehran'))
            hour, minute = now.hour, now.minute
            if minute != last_sent_minute and 8 <= hour < 20:
                if minute == 0 and hour != 8 and hour != 20:
                    try:
                        img_bytes = generate_infographic(INFO_CACHE)
                        await bot.send_photo(chat_id=CHANNEL_ID, photo=img_bytes, caption="📊 خلاصه وضعیت بازار")
                    except Exception as e: logging.error(f"Img Error: {e}")
                last_sent_minute = minute
        except Exception as e: logging.error(f"Scheduler Error: {e}")
        await asyncio.sleep(15)

# ==========================================
# ربات
# ==========================================
def get_main_menu():
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="🚀 استارت / منوی اصلی")]], resize_keyboard=True)

dp = Dispatcher()

@dp.message(CommandStart())
async def command_start_handler(message: Message) -> None:
    if message.chat.type != ChatType.PRIVATE: return
    await message.answer("سلام! به ربات دیجی نوسان خوش آمدید. ✅", reply_markup=get_main_menu())

@dp.message()
async def handle_message(message: Message) -> None:
    if message.chat.type != ChatType.PRIVATE: return
    text = message.text.strip() if message.text else ""
    
    if text == "/test_info":
        await message.answer("⏳ در حال ساخت اینفوگرافیک تستی...")
        try:
            img_bytes = generate_infographic(INFO_CACHE)
            await message.answer_photo(photo=img_bytes, caption="📊 تست اینفوگرافیک")
        except Exception as e:
            await message.answer(f"❌ خطا: {str(e)}")
    elif text == "🚀 استارت / منوی اصلی" or text == "/start":
        await command_start_handler(message)

async def main() -> None:
    bot = Bot(token=BALE_TOKEN, default=DefaultBotProperties())
    asyncio.create_task(update_cache_task())
    asyncio.create_task(channel_scheduler(bot))
    await dp.start_polling(bot)

if __name__ == "__main__":
    threading.Thread(target=run_dummy_server, daemon=True).start()
    print("Starting Bale Bot...")
    asyncio.run(main())
