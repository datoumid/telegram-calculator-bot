import os
import re
import sys
import json
import sqlite3
import tempfile
from datetime import datetime, date
from pathlib import Path
from dotenv import load_dotenv
import warnings
warnings.filterwarnings("ignore")

try:
    if sys.stdout.encoding != 'utf-8':
        sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

# .env faylini yuklash
load_dotenv()

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "8930567763:AAHmzla7oOlJYxv8jCFwq-g91c2PcvbNgOA")
GLOBAL_GEMINI_KEY = os.getenv("GEMINI_API_KEY", "")

import telebot
from telebot import types

# Modern Google GenAI SDK
try:
    from google import genai
    from google.genai import types as genai_types
    HAS_GENAI = True
except ImportError:
    HAS_GENAI = False

try:
    import pypdf
    HAS_PYPDF = True
except ImportError:
    HAS_PYPDF = False

bot = telebot.TeleBot(TOKEN, parse_mode="HTML")

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "finance.db")

# Toifalar ro'yxati
CATEGORIES = [
    "Oziq-ovqat va supermarket",
    "Kafe va restoranlar",
    "Transport (yoqilg'i, taksi, jamoat transporti)",
    "Kommunal, uy va aloqa xarajatlari",
    "Salomatlik va sport",
    "Shaxsiy xaridlar va kiyim-kechak",
    "Ta'lim va obunalar",
    "Kutilmagan / Boshqa xarajatlar"
]

CATEGORY_ICONS = {
    "Oziq-ovqat va supermarket": "🥗",
    "Kafe va restoranlar": "☕",
    "Transport (yoqilg'i, taksi, jamoat transporti)": "🚕",
    "Kommunal, uy va aloqa xarajatlari": "💡",
    "Salomatlik va sport": "💊",
    "Shaxsiy xaridlar va kiyim-kechak": "🛍",
    "Ta'lim va obunalar": "📚",
    "Kutilmagan / Boshqa xarajatlar": "📦"
}

# --- MA'LUMOTLAR BAZASI (SQLITE) ---
def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    # Tranzaksiyalar jadvali
    cur.execute("""
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            date TEXT NOT NULL,
            time TEXT NOT NULL,
            title TEXT NOT NULL,
            category TEXT NOT NULL,
            payment_type TEXT NOT NULL,
            amount REAL NOT NULL,
            type TEXT DEFAULT 'expense',
            source TEXT DEFAULT 'text',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    # Foydalanuvchi sozlamalari (masalan, shaxsiy Gemini API kaliti)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS user_settings (
            user_id INTEGER PRIMARY KEY,
            gemini_api_key TEXT,
            currency TEXT DEFAULT 'so''m'
        )
    """)
    conn.commit()
    conn.close()

def get_connection():
    return sqlite3.connect(DB_PATH)

def get_gemini_key(user_id):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT gemini_api_key FROM user_settings WHERE user_id = ?", (user_id,))
    row = cur.fetchone()
    conn.close()
    if row and row[0]:
        return row[0].strip()
    return GLOBAL_GEMINI_KEY.strip()

def set_gemini_key(user_id, key):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO user_settings (user_id, gemini_api_key)
        VALUES (?, ?)
        ON CONFLICT(user_id) DO UPDATE SET gemini_api_key=excluded.gemini_api_key
    """, (user_id, key.strip()))
    conn.commit()
    conn.close()

def add_transaction(user_id, tx_date, title, category, payment_type, amount, tx_type='expense', source='text'):
    conn = get_connection()
    cur = conn.cursor()
    now_time = datetime.now().strftime("%H:%M")
    if not tx_date:
        tx_date = date.today().strftime("%Y-%m-%d")
    
    cur.execute("""
        INSERT INTO transactions (user_id, date, time, title, category, payment_type, amount, type, source)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (user_id, tx_date, now_time, title, category, payment_type, amount, tx_type, source))
    tx_id = cur.lastrowid
    conn.commit()
    conn.close()
    return tx_id

def get_monthly_data(user_id, year=None, month=None):
    if not year or not month:
        now = datetime.now()
        year = now.year
        month = now.month
    
    prefix = f"{year:04d}-{month:02d}%"
    conn = get_connection()
    cur = conn.cursor()
    
    cur.execute("""
        SELECT id, date, title, category, payment_type, amount
        FROM transactions
        WHERE user_id = ? AND date LIKE ? AND type = 'expense'
        ORDER BY date DESC, id DESC
    """, (user_id, prefix))
    rows = cur.fetchall()
    conn.close()
    return rows

def get_daily_data(user_id, tx_date=None):
    if not tx_date:
        tx_date = date.today().strftime("%Y-%m-%d")
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, date, title, category, payment_type, amount
        FROM transactions
        WHERE user_id = ? AND date = ? AND type = 'expense'
        ORDER BY id DESC
    """, (user_id, tx_date))
    rows = cur.fetchall()
    conn.close()
    return rows

def get_recent_transactions(user_id, limit=10):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, date, title, category, payment_type, amount
        FROM transactions
        WHERE user_id = ?
        ORDER BY id DESC
        LIMIT ?
    """, (user_id, limit))
    rows = cur.fetchall()
    conn.close()
    return rows

def delete_transaction(user_id, tx_id):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM transactions WHERE id = ? AND user_id = ?", (tx_id, user_id))
    affected = cur.rowcount
    conn.commit()
    conn.close()
    return affected > 0

# --- LOKAL HEURISTIK TAHLILCHI (OFFLINE FALLBACK) ---
def local_categorize(text):
    t = text.lower()
    
    rules = [
        ("Oziq-ovqat va supermarket", ['korzinka', 'makro', 'havas', 'bozor', 'supermarket', 'non', 'go\'sht', 'gosht', 'un', 'sut', 'suv', 'meva', 'sabzavot', 'oziq', 'bazar', 'prodaja']),
        ("Kafe va restoranlar", ['osh', 'somsa', 'lagmon', 'kafe', 'restoran', 'kofe', 'coffee', 'choyxona', 'tushlik', 'obed', 'kechki ovqat', 'nonushta', 'burger', 'lavash', 'fast food', 'pitsa', 'pizza', 'dodo', 'evos', 'kfc']),
        ("Transport (yoqilg'i, taksi, jamoat transporti)", ['taksi', 'taxi', 'yandex', 'benzin', 'gaz', 'propan', 'metan', 'yoqilgi', 'zapravka', 'avtobus', 'metro', 'moy', 'avto', 'proyezd']),
        ("Kommunal, uy va aloqa xarajatlari", ['svet', 'elektr', 'gaz', 'suv', 'musor', 'kommunal', 'kvartira', 'arenda', 'ijara', 'wifi', 'internet', 'ucell', 'beeline', 'mobiuz', 'uztelecom', 'telefon', 'paynet']),
        ("Salomatlik va sport", ['dori', 'dorixona', 'apteka', 'vrach', 'shifoxona', 'poliklinika', 'stomatolog', 'sport', 'zal', 'fitnes', 'fitness', 'trenirovka', 'klinika', 'tahlil']),
        ("Shaxsiy xaridlar va kiyim-kechak", ['kiyim', 'koylak', 'shim', 'oyoq kiyim', 'tufli', 'krossovka', 'bozorlik', 'parfyum', 'atir', 'sovg\'a', 'savdo', 'xarid', 'magazin', 'zara', 'shopping']),
        ("Ta'lim va obunalar", ['kurs', 'kitob', 'talim', 'oqish', 'maktab', 'universitet', 'kontrakt', 'obuna', 'subscription', 'telegram premium', 'youtube', 'repititor', 'ucheba']),
    ]
    
    for cat, keywords in rules:
        for kw in keywords:
            if kw in t:
                return cat
    return "Kutilmagan / Boshqa xarajatlar"

def parse_simple_text_expense(text):
    """
    Oddiy matnli xabarni tahlil qilish:
    Masalan: '35000 tushlik', 'karta 120000 bozor', 'korzinka 150 000 naqd'
    """
    t = text.strip()
    payment_type = "Naqd"
    if re.search(r'\b(karta|kartadan|karta orqali|plastik|uzcard|humo|click|payme)\b', t, re.IGNORECASE):
        payment_type = "Karta"
    elif re.search(r'\b(naqd|nal|cash)\b', t, re.IGNORECASE):
        payment_type = "Naqd"
    
    # Raqam/summani topish (masalan: 35000, 150 000, 25k, 50 ming)
    num_match = re.search(r'(\d+[\d\s.,]*)\s*(k|ming|mln|so\'m|som)?(?!\w)', t, re.IGNORECASE)
    if not num_match:
        return None
    
    raw_num = num_match.group(1).replace(" ", "").replace(",", "")
    unit = (num_match.group(2) or "").lower()
    
    try:
        amount = float(raw_num)
        if unit in ['ming', 'k']:
            amount *= 1000
        elif unit in ['mln']:
            amount *= 1000000
    except ValueError:
        return None
    
    if amount <= 0:
        return None
    
    # Nomi/izohini qolgan so'zlardan ajratib olish
    clean_title = re.sub(r'(\d+[\d\s.,]*)\s*(k|ming|mln|so\'m|som)?(?!\w)', '', t, count=1, flags=re.IGNORECASE)
    clean_title = re.sub(r'\b(karta|kartadan|karta orqali|plastik|uzcard|humo|click|payme|naqd|nal|cash)\b', '', clean_title, flags=re.IGNORECASE).strip()
    
    if not clean_title:
        clean_title = "Noma'lum xarajat"
        category = "Kutilmagan / Boshqa xarajatlar"
    else:
        category = local_categorize(clean_title)
        
    return {
        "title": clean_title.capitalize(),
        "amount": amount,
        "category": category,
        "payment_type": payment_type,
        "date": date.today().strftime("%Y-%m-%d")
    }

# --- GEMINI AI INTEGRATSIYASI ---
GEMINI_SYSTEM_PROMPT = """Siz shaxsiy moliyani yuritish bo'yicha professional yordamchisiz.
Vazifangiz: foydalanuvchining bank ko'chirmalari (PDF/matn), ovozli xabarlari (audio) va xarid cheklari (fotosuratlar) orqali kiritilgan ma'lumotlarni aniq tahlil qilish, toifalash va JSON formatida qaytarish.

TOIFALAR TUZILMASI (Faqat shu toifalardan birini tanlang):
- Oziq-ovqat va supermarket
- Kafe va restoranlar
- Transport (yoqilg'i, taksi, jamoat transporti)
- Kommunal, uy va aloqa xarajatlari
- Salomatlik va sport
- Shaxsiy xaridlar va kiyim-kechak
- Ta'lim va obunalar
- Kutilmagan / Boshqa xarajatlar

TO'LOV TURI QOIDALARI:
- Ovozli xabarlarda: agar to'lov turi alohida aytilmasa, odatda "Naqd" deb belgilang.
- Cheklarda: chekda ko'rsatilgan to'lov turini inobatga oling (Karta yoki Naqd).
- Bank ko'chirmalarida: "Karta" deb belgilang.

SANA:
- Agar sana aytilmagan yoki ko'rsatilmagan bo'lsa, joriy sanani (YYYY-MM-DD) qo'ying.

JAVOB FORMATI:
Faqat va faqat JSON obyektini yoki ro'yxatini qaytaring (hech qanday qo'shimcha matnsiz yoki markdown formatlashsiz):
Yakka xarajat uchun:
{
  "title": "Korzinka (yoki xarid qilingan joy / tovar)",
  "amount": 125000,
  "category": "Oziq-ovqat va supermarket",
  "payment_type": "Karta",
  "date": "2026-10-12"
}
Ko'p xarajatlar (masalan, bank ko'chirmasi) uchun:
[
  { "title": "...", "amount": 10000, "category": "...", "payment_type": "Karta", "date": "2026-10-12" }
]
"""

def call_gemini_vision(api_key, image_bytes, prompt="Ushbu chek fotosuratidan ma'lumotlarni o'qing"):
    if not HAS_GENAI or not api_key:
        return None
    try:
        client = genai.Client(api_key=api_key)
        part = genai_types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg")
        config = genai_types.GenerateContentConfig(system_instruction=GEMINI_SYSTEM_PROMPT)
        response = client.models.generate_content(
            model="gemini-3.8-flash",
            contents=[part, prompt],
            config=config
        )
        txt = response.text.strip()
        txt = re.sub(r'^```json\s*', '', txt)
        txt = re.sub(r'```$', '', txt).strip()
        return json.loads(txt)
    except Exception as e:
        print(f"Gemini Vision Error: {e}")
        return None

def call_gemini_audio(api_key, audio_bytes, mime_type="audio/ogg"):
    if not HAS_GENAI or not api_key:
        return None
    try:
        client = genai.Client(api_key=api_key)
        part = genai_types.Part.from_bytes(data=audio_bytes, mime_type=mime_type)
        prompt = "Ushbu ovozli xabarni eshitib, xarajat summasi va nima uchun sarflanganini aniqlang. Bugungi sana: " + date.today().strftime("%Y-%m-%d")
        config = genai_types.GenerateContentConfig(system_instruction=GEMINI_SYSTEM_PROMPT)
        response = client.models.generate_content(
            model="gemini-3.8-flash",
            contents=[part, prompt],
            config=config
        )
        txt = response.text.strip()
        txt = re.sub(r'^```json\s*', '', txt)
        txt = re.sub(r'```$', '', txt).strip()
        return json.loads(txt)
    except Exception as e:
        print(f"Gemini Audio Error: {e}")
        return None

def call_gemini_text(api_key, user_text):
    if not HAS_GENAI or not api_key:
        return None
    try:
        client = genai.Client(api_key=api_key)
        prompt = f"Foydalanuvchi xabari: '{user_text}'. Bugungi sana: {date.today().strftime('%Y-%m-%d')}."
        config = genai_types.GenerateContentConfig(system_instruction=GEMINI_SYSTEM_PROMPT)
        response = client.models.generate_content(
            model="gemini-3.8-flash",
            contents=prompt,
            config=config
        )
        txt = response.text.strip()
        txt = re.sub(r'^```json\s*', '', txt)
        txt = re.sub(r'```$', '', txt).strip()
        return json.loads(txt)
    except Exception as e:
        print(f"Gemini Text Error: {e}")
        return None

# --- PDF BANL KO'CHIRMALARI BILAN ISHLASH ---
def extract_text_from_pdf(pdf_path):
    if not HAS_PYPDF:
        return ""
    try:
        reader = pypdf.PdfReader(pdf_path)
        all_text = []
        for page in reader.pages:
            t = page.extract_text()
            if t:
                all_text.append(t)
        return "\n".join(all_text)
    except Exception as e:
        print(f"PDF extract error: {e}")
        return ""

def parse_bank_statement_with_gemini(api_key, text):
    if not HAS_GENAI or not api_key:
        return None
    try:
        client = genai.Client(api_key=api_key)
        prompt = (
            "Ushbu bank ko'chirmasidan FAQAT karta xarajatlarini (chiqimlarni) ajratib oling. "
            "Har bir xarajat uchun sana, to'lov qilingan joy va aniq summani qayd eting. "
            "To'lov turi: 'Karta'. Natijani JSON ro'yxat shaklida qaytaring:\n\n" + text[:15000]
        )
        config = genai_types.GenerateContentConfig(system_instruction=GEMINI_SYSTEM_PROMPT)
        response = client.models.generate_content(
            model="gemini-3.8-flash",
            contents=prompt,
            config=config
        )
        txt = response.text.strip()
        txt = re.sub(r'^```json\s*', '', txt)
        txt = re.sub(r'```$', '', txt).strip()
        data = json.loads(txt)
        if isinstance(data, dict):
            data = [data]
        return data
    except Exception as e:
        print(f"PDF Gemini error: {e}")
        return None

def parse_bank_statement_heuristic(text):
    """
    PDF ichidan tranzaksiyalarni qidirish (O'zbekiston banklari: Click, Payme, Uzum, boshqalar uchun)
    """
    transactions = []
    lines = text.split("\n")
    
    # Qidiruv shabloni: Sana (DD.MM.YYYY yoki YYYY-MM-DD), Nomi va Summa
    date_pattern = r'(\d{2}[./-]\d{2}[./-]\d{4}|\d{4}-\d{2}-\d{2})'
    amount_pattern = r'(\d+[\d\s.,]*)\s*(UZS|so\'m|сум)?'
    
    for line in lines:
        line_clean = line.strip()
        if not line_clean:
            continue
        # Salbiy yoki chiqim amallari (- belgisi yoki O'tkazma/Xarid)
        d_match = re.search(date_pattern, line_clean)
        if d_match:
            tx_date_str = d_match.group(1)
            # Standartlashtirish YYYY-MM-DD
            try:
                if "." in tx_date_str:
                    parts = tx_date_str.split(".")
                    if len(parts[0]) == 4:
                        parsed_date = f"{parts[0]}-{parts[1]}-{parts[2]}"
                    else:
                        parsed_date = f"{parts[2]}-{parts[1]}-{parts[0]}"
                else:
                    parsed_date = tx_date_str
            except Exception:
                parsed_date = date.today().strftime("%Y-%m-%d")
            
            # Summa qidirish
            nums = re.findall(r'[-–—]?\s*(\d{1,3}(?:[\s.,]\d{3})*(?:[.,]\d{2})?)\s*(?:UZS|so\'m|сум)?', line_clean)
            if nums:
                for n in nums:
                    val_str = n.replace(" ", "").replace(",", "")
                    try:
                        amt = float(val_str)
                        if 1000 <= amt <= 100000000:
                            # Nomini aniqlash
                            title_part = re.sub(date_pattern, '', line_clean)
                            title_part = re.sub(r'[-–—]?\s*\d+[\d\s.,]*\s*(?:UZS|so\'m|сум)?', '', title_part).strip()
                            if not title_part or len(title_part) < 3:
                                title_part = "Bank karta to'lovi"
                            
                            category = local_categorize(title_part)
                            transactions.append({
                                "date": parsed_date,
                                "title": title_part[:40].strip(),
                                "amount": amt,
                                "category": category,
                                "payment_type": "Karta"
                            })
                            break
                    except ValueError:
                        continue
                        
    return transactions

# --- FORMATLASH VA CHIQISH ---
def format_currency(amount):
    return f"{amount:,.0f}".replace(",", " ") + " so'm"

def format_confirmation(tx):
    icon = CATEGORY_ICONS.get(tx.get('category', ''), '🏷')
    payment_icon = "💳" if tx.get('payment_type') == "Karta" else "💵"
    
    return (
        f"✅ <b>Xarajat muvaffaqiyatli saqlandi!</b>\n\n"
        f"📅 <b>Sana:</b> <code>{tx.get('date')}</code>\n"
        f"🏪 <b>Nomi:</b> <b>{tx.get('title')}</b>\n"
        f"{icon} <b>Toifasi:</b> {tx.get('category')}\n"
        f"{payment_icon} <b>To'lov turi:</b> {tx.get('payment_type')}\n"
        f"💰 <b>Summasi:</b> <code>{format_currency(tx.get('amount'))}</code>"
    )

def generate_monthly_report(user_id, year=None, month=None):
    if not year or not month:
        now = datetime.now()
        year = now.year
        month = now.month
        
    rows = get_monthly_data(user_id, year, month)
    
    month_names = {
        1: "Yanvar", 2: "Fevral", 3: "Mart", 4: "Aprel",
        5: "May", 6: "Iyun", 7: "Iyul", 8: "Avgust",
        9: "Sentyabr", 10: "Oktyabr", 11: "Noyabr", 12: "Dekabr"
    }
    month_title = f"{year}-yil {month_names.get(month, '')}"
    
    if not rows:
        return f"📊 <b>{month_title} oyi bo'yicha hisobot:</b>\n\nHozircha ushbu oyda hech qanday xarajat qayd etilmagan."
    
    total_spent = sum(r[5] for r in rows)
    card_spent = sum(r[5] for r in rows if r[4] == "Karta")
    cash_spent = sum(r[5] for r in rows if r[4] == "Naqd")
    
    card_pct = (card_spent / total_spent * 100) if total_spent > 0 else 0
    cash_pct = (cash_spent / total_spent * 100) if total_spent > 0 else 0
    
    # Toifalar bo'yicha taqsimot
    cat_totals = {}
    for r in rows:
        cat = r[3]
        cat_totals[cat] = cat_totals.get(cat, 0) + r[5]
        
    sorted_cats = sorted(cat_totals.items(), key=lambda x: x[1], reverse=True)
    
    # Hisobot matni
    msg = (
        f"📊 <b>{month_title} oyi moliyaviy hisoboti:</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💵 <b>Jami sarflangan mablag':</b> <code>{format_currency(total_spent)}</code>\n\n"
        f"💳 <b>To'lov turlari bo'yicha:</b>\n"
        f"• 💳 Karta: <code>{format_currency(card_spent)}</code> (<b>{card_pct:.1f}%</b>)\n"
        f"• 💵 Naqd: <code>{format_currency(cash_spent)}</code> (<b>{cash_pct:.1f}%</b>)\n\n"
        f"🏷 <b>Toifalar kesimidagi taqsimot:</b>\n"
    )
    
    for cat, val in sorted_cats:
        icon = CATEGORY_ICONS.get(cat, "•")
        pct = (val / total_spent * 100) if total_spent > 0 else 0
        msg += f"{icon} {cat}: <code>{format_currency(val)}</code> (<b>{pct:.1f}%</b>)\n"
        
    msg += "\n📋 <b>Oxirgi amallar jadvali:</b>\n"
    msg += "<code>[Sana       | Nomi         | To'lov | Summa]</code>\n"
    msg += "<code>──────────────────────────────────────</code>\n"
    
    for r in rows[:10]: # Oxirgi 10 tasini ko'rsatish
        dt = r[1][5:] # MM-DD
        nm = (r[2][:12] + "..") if len(r[2]) > 12 else r[2].ljust(14)
        pt = "Karta" if r[4] == "Karta" else "Naqd "
        amt = f"{r[5]:,.0f}".replace(",", " ")
        msg += f"<code>{dt} | {nm} | {pt} | {amt}</code>\n"
        
    if len(rows) > 10:
        msg += f"\n<i>...va yana {len(rows) - 10} ta xarajat mavjud.</i>"
        
    return msg

# --- TUGMALAR (KEYBOARDS) ---
def get_main_keyboard():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    b1 = types.KeyboardButton("📊 Oylik hisobot")
    b2 = types.KeyboardButton("📅 Bugungi hisobot")
    b3 = types.KeyboardButton("➕ Xarajat kiritish")
    b4 = types.KeyboardButton("🧾 Oxirgi amallar")
    b5 = types.KeyboardButton("📁 Excel/CSV eksport")
    b6 = types.KeyboardButton("⚙️ Sozlamalar (API Key)")
    markup.add(b1, b2)
    markup.add(b3, b4)
    markup.add(b5, b6)
    return markup

# Foydalanuvchi holati (FSM / state)
user_states = {}

# --- BOT HANDLERS ---
@bot.message_handler(commands=['start', 'help'])
def send_welcome(message):
    user_id = message.from_user.id
    init_db()
    
    welcome_text = (
        f"Assalomu alaykum, <b>{message.from_user.first_name}</b>! 👋\n\n"
        f"Men sizning <b>shaxsiy hisob-kitob va moliya bo'yicha aqlli yordamchingizman</b>.\n\n"
        f"<b>Qanday ma'lumot kiritishingiz mumkin?</b>\n"
        f"1. 🎤 <b>Ovozli xabar:</b> '<i>Bugun 45 mingga tushlik qildim</i>' deb ayting (To'lov: Naqd).\n"
        f"2. 📸 <b>Chek fotosurati:</b> Xarid chekini rasmga olib yuboring (Summa, do'kon va to'lov turini o'zim aniqlayman).\n"
        f"3. 📄 <b>PDF bank ko'chirmasi:</b> Click, Payme yoki bank ilovasidan olingan PDF ni yuboring (Karta xarajatlarini ajratib olaman).\n"
        f"4. 💬 <b>Oddiy matn:</b> Masalan, <code>50000 tushlik</code> yoki <code>karta 180000 korzinka</code>.\n\n"
        f"Quyidagi tugmalardan foydalanishingiz mumkin:"
    )
    bot.send_message(message.chat.id, welcome_text, reply_markup=get_main_keyboard())

@bot.message_handler(func=lambda msg: msg.text == "📊 Oylik hisobot")
def handle_monthly_report(message):
    report = generate_monthly_report(message.from_user.id)
    bot.send_message(message.chat.id, report)

@bot.message_handler(func=lambda msg: msg.text == "📅 Bugungi hisobot")
def handle_daily_report(message):
    user_id = message.from_user.id
    today_str = date.today().strftime("%Y-%m-%d")
    rows = get_daily_data(user_id, today_str)
    
    if not rows:
        bot.send_message(message.chat.id, f"📅 <b>Bugungi sana ({today_str}):</b>\n\nBugun hali hech qanday xarajat kiritilmadi.")
        return
        
    total_spent = sum(r[5] for r in rows)
    msg = f"📅 <b>Bugungi ({today_str}) xarajatlar:</b>\n"
    msg += f"━━━━━━━━━━━━━━━━━━━━━━\n"
    msg += f"💵 <b>Jami sarf:</b> <code>{format_currency(total_spent)}</code>\n\n"
    
    for r in rows:
        icon = CATEGORY_ICONS.get(r[3], "•")
        pt = "💳 Karta" if r[4] == "Karta" else "💵 Naqd"
        msg += f"{icon} <b>{r[2]}</b> ({pt}): <code>{format_currency(r[5])}</code>\n"
        
    bot.send_message(message.chat.id, msg)

@bot.message_handler(func=lambda msg: msg.text == "🧾 Oxirgi amallar")
def handle_recent(message):
    user_id = message.from_user.id
    rows = get_recent_transactions(user_id, limit=10)
    
    if not rows:
        bot.send_message(message.chat.id, "Sizda hali saqlangan xarajatlar mavjud emas.")
        return
        
    msg = "🧾 <b>So'nggi 10 ta xarajat:</b>\n\n"
    for r in rows:
        icon = CATEGORY_ICONS.get(r[3], "•")
        pt = "💳" if r[4] == "Karta" else "💵"
        msg += f"• <code>{r[1]}</code> | {icon} <b>{r[2]}</b> ({pt}): <code>{format_currency(r[5])}</code>\n"
        
    bot.send_message(message.chat.id, msg)

@bot.message_handler(func=lambda msg: msg.text == "➕ Xarajat kiritish")
def handle_add_expense_prompt(message):
    msg = (
        "➕ <b>Xarajat kiritish juda oson:</b>\n\n"
        "1. Matn yozing: <code>45000 tushlik</code> yoki <code>karta 120000 korzinka</code>\n"
        "2. Yoki ovozli xabar (audio) yuboring;\n"
        "3. Yoki xarid chekini rasmga olib yuboring;\n"
        "4. Yoki bank PDF ko'chirmasini yuboring."
    )
    bot.send_message(message.chat.id, msg)

@bot.message_handler(func=lambda msg: msg.text == "📁 Excel/CSV eksport")
def handle_export(message):
    user_id = message.from_user.id
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT date, time, title, category, payment_type, amount, type, source
        FROM transactions
        WHERE user_id = ?
        ORDER BY date DESC, id DESC
    """, (user_id,))
    rows = cur.fetchall()
    conn.close()
    
    if not rows:
        bot.send_message(message.chat.id, "Eksport qilish uchun xarajatlar mavjud emas.")
        return
        
    csv_lines = ["Sana,Vaqt,Nomi,Toifasi,Tolov turi,Summasi,Turi,Manba"]
    for r in rows:
        title_escaped = f'"{r[2]}"'
        csv_lines.append(f"{r[0]},{r[1]},{title_escaped},{r[3]},{r[4]},{r[5]},{r[6]},{r[7]}")
        
    csv_data = "\n".join(csv_lines).encode("utf-8-sig")
    
    bio = tempfile.NamedTemporaryFile(delete=False, suffix=".csv")
    bio.write(csv_data)
    bio.close()
    
    with open(bio.name, "rb") as doc:
        bot.send_document(
            message.chat.id,
            doc,
            visible_file_name=f"xarajatlar_{date.today().strftime('%Y_%m_%d')}.csv",
            caption="📊 Barcha xarajatlaringiz ro'yxati (Excel va boshqa dasturlarda ochiladi)."
        )
    try:
        os.remove(bio.name)
    except Exception:
        pass

@bot.message_handler(func=lambda msg: msg.text == "⚙️ Sozlamalar (API Key)" or msg.text == "/settings")
def handle_settings(message):
    user_id = message.from_user.id
    key = get_gemini_key(user_id)
    masked_key = (key[:8] + "..." + key[-4:]) if key else "O'rnatilmagan ❌"
    
    msg = (
        f"⚙️ <b>Sozlamalar va Sun'iy Intellekt:</b>\n\n"
        f"🔑 <b>Google Gemini API Key:</b> <code>{masked_key}</code>\n\n"
        f"Gemini API kaliti chek fotosuratlari va ovozli xabarlarni juda aniq tahlil qilish uchun kerak.\n\n"
        f"Kalitni kiritish uchun quyidagi formatda yozib yuboring:\n"
        f"<code>/setkey AIzaSy...sizning_kalitingiz</code>\n\n"
        f"<i>Agar kalitingiz bo'lmasa, uni <a href='https://aistudio.google.com/app/apikey'>Google AI Studio</a> saytidan bepul olishingiz mumkin.</i>"
    )
    bot.send_message(message.chat.id, msg, disable_web_page_preview=True)

@bot.message_handler(commands=['setkey'])
def handle_set_key(message):
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        bot.reply_to(message, "Iltimos, kalitni kiriting:\nMasalan: <code>/setkey AIzaSy...</code>")
        return
    key = parts[1].strip()
    set_gemini_key(message.from_user.id, key)
    bot.reply_to(message, "✅ <b>Gemini API kalitingiz muvaffaqiyatli saqlandi!</b>\nEndi ovozli xabarlar va cheklar avtomatik AI orqali tahlil qilinadi.")

# --- OVOZLI XABARLAR BILAN ISHLASH ---
@bot.message_handler(content_types=['voice', 'audio'])
def handle_voice_message(message):
    user_id = message.from_user.id
    api_key = get_gemini_key(user_id)
    
    status_msg = bot.reply_to(message, "🎧 Ovozli xabar tinglanmoqda va tahlil qilinmoqda...")
    
    try:
        file_info = bot.get_file(message.voice.file_id if message.voice else message.audio.file_id)
        file_bytes = bot.download_file(file_info.file_path)
        
        parsed = None
        if api_key and HAS_GEMINI:
            parsed = call_gemini_audio(api_key, file_bytes, mime_type="audio/ogg")
            
        if not parsed:
            bot.edit_message_text(
                "⚠️ Ovozli xabarni avtomatik eshitish uchun <b>Gemini API kaliti</b> zarur.\n"
                "Iltimos, <code>/setkey KALITINGIZ</code> orqali kalitingizni kiriting yoki xarajatni matn ko'rinishida yozing (masalan: <code>35000 tushlik</code>).",
                chat_id=message.chat.id,
                message_id=status_msg.message_id
            )
            return
            
        # Ovozli xabarlar odatda "Naqd" to'lov turi
        if not parsed.get("payment_type"):
            parsed["payment_type"] = "Naqd"
        if not parsed.get("date"):
            parsed["date"] = date.today().strftime("%Y-%m-%d")
            
        add_transaction(
            user_id=user_id,
            tx_date=parsed["date"],
            title=parsed["title"],
            category=parsed.get("category", "Kutilmagan / Boshqa xarajatlar"),
            payment_type=parsed["payment_type"],
            amount=parsed["amount"],
            source="voice"
        )
        
        resp_text = format_confirmation(parsed)
        bot.edit_message_text(resp_text, chat_id=message.chat.id, message_id=status_msg.message_id)
        
    except Exception as e:
        bot.edit_message_text(f"❌ Xatolik yuz berdi: {e}", chat_id=message.chat.id, message_id=status_msg.message_id)

# --- CHEK FOTOSURATLARI BILAN ISHLASH ---
@bot.message_handler(content_types=['photo'])
def handle_photo_receipt(message):
    user_id = message.from_user.id
    api_key = get_gemini_key(user_id)
    
    status_msg = bot.reply_to(message, "🔍 Chek fotosurati o'qilmoqda va tahlil qilinmoqda...")
    
    try:
        photo = message.photo[-1] # Eng yuqori sifatli rasm
        file_info = bot.get_file(photo.file_id)
        image_bytes = bot.download_file(file_info.file_path)
        
        parsed = None
        if api_key and HAS_GEMINI:
            parsed = call_gemini_vision(api_key, image_bytes)
            
        if not parsed:
            bot.edit_message_text(
                "⚠️ Chek rasmini avtomatik o'qish (Vision OCR) uchun <b>Gemini API kaliti</b> talab qilinadi.\n"
                "Iltimos, <code>/setkey KALITINGIZ</code> orqali kalitingizni kiriting yoki xarajatni matn ko'rinishida yozing (masalan: <code>120000 bozor</code>).",
                chat_id=message.chat.id,
                message_id=status_msg.message_id
            )
            return
            
        if not parsed.get("date"):
            parsed["date"] = date.today().strftime("%Y-%m-%d")
        if not parsed.get("payment_type"):
            parsed["payment_type"] = "Karta"
            
        add_transaction(
            user_id=user_id,
            tx_date=parsed["date"],
            title=parsed["title"],
            category=parsed.get("category", "Kutilmagan / Boshqa xarajatlar"),
            payment_type=parsed["payment_type"],
            amount=parsed["amount"],
            source="photo"
        )
        
        resp_text = format_confirmation(parsed)
        bot.edit_message_text(resp_text, chat_id=message.chat.id, message_id=status_msg.message_id)
        
    except Exception as e:
        bot.edit_message_text(f"❌ Xatolik yuz berdi: {e}", chat_id=message.chat.id, message_id=status_msg.message_id)

# --- PDF BANK KO'CHIRMALARI BILAN ISHLASH ---
@bot.message_handler(content_types=['document'])
def handle_pdf_statement(message):
    doc = message.document
    if not doc.file_name.lower().endswith(".pdf"):
        bot.reply_to(message, "Iltimos, bank ko'chirmasini <b>PDF</b> formatida yuboring.")
        return
        
    user_id = message.from_user.id
    api_key = get_gemini_key(user_id)
    
    status_msg = bot.reply_to(message, "📄 Bank ko'chirmasi (PDF) yuklab olinmoqda va tahlil qilinmoqda...")
    
    temp_path = None
    try:
        file_info = bot.get_file(doc.file_id)
        file_bytes = bot.download_file(file_info.file_path)
        
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as f:
            f.write(file_bytes)
            temp_path = f.name
            
        pdf_text = extract_text_from_pdf(temp_path)
        if not pdf_text.strip():
            bot.edit_message_text("⚠️ PDF fayldan matn ajratib bo'lmadi (ehtimol skanerlangan rasm bo'lishi mumkin).", chat_id=message.chat.id, message_id=status_msg.message_id)
            return
            
        transactions = []
        if api_key and HAS_GEMINI:
            transactions = parse_bank_statement_with_gemini(api_key, pdf_text) or []
            
        if not transactions:
            # Lokal tahlilchi bilan harakat qilib ko'rish
            transactions = parse_bank_statement_heuristic(pdf_text)
            
        if not transactions:
            bot.edit_message_text(
                "⚠️ Bank ko'chirmasidan xarajatlar topilmadi.\n"
                "To'liq tahlil uchun Gemini kalitini qo'shib ko'ring: <code>/setkey KALIT</code>",
                chat_id=message.chat.id,
                message_id=status_msg.message_id
            )
            return
            
        # Tranzaksiyalarni saqlash (Bank ko'chirmasida faqat "Karta")
        saved_count = 0
        total_sum = 0
        preview_text = ""
        
        for tx in transactions:
            dt = tx.get("date", date.today().strftime("%Y-%m-%d"))
            title = tx.get("title", "Bank to'lovi")
            cat = tx.get("category", local_categorize(title))
            amt = float(tx.get("amount", 0))
            if amt > 0:
                add_transaction(user_id, dt, title, cat, "Karta", amt, source="pdf")
                saved_count += 1
                total_sum += amt
                if saved_count <= 5:
                    preview_text += f"• <code>{dt}</code> | <b>{title[:18]}</b>: <code>{format_currency(amt)}</code>\n"
                    
        res_msg = (
            f"✅ <b>Bank ko'chirmasi muvaffaqiyatli qayta ishlandi!</b>\n\n"
            f"📥 <b>Kiritilgan amallar soni:</b> {saved_count} ta\n"
            f"💳 <b>To'lov turi:</b> Karta\n"
            f"💰 <b>Jami xarajat:</b> <code>{format_currency(total_sum)}</code>\n\n"
            f"<b>Ko'chirmadan namunalar:</b>\n{preview_text}"
        )
        if saved_count > 5:
            res_msg += f"<i>...va yana {saved_count - 5} ta operatsiya saqlandi.</i>\n"
            
        res_msg += "\nTo'liq hisobotni ko'rish uchun <b>📊 Oylik hisobot</b> tugmasini bosing."
        bot.edit_message_text(res_msg, chat_id=message.chat.id, message_id=status_msg.message_id)
        
    except Exception as e:
        bot.edit_message_text(f"❌ Xatolik yuz berdi: {e}", chat_id=message.chat.id, message_id=status_msg.message_id)
    finally:
        if temp_path and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except Exception:
                pass

# --- MATN ORQALI KIRITISH ---
@bot.message_handler(func=lambda msg: True)
def handle_text_expense(message):
    user_id = message.from_user.id
    text = message.text.strip()
    
    # Buyruqlar yoki menyu tugmalari emasligini tekshirish
    if text.startswith("/"):
        return
        
    api_key = get_gemini_key(user_id)
    
    parsed = None
    if api_key and HAS_GEMINI:
        parsed = call_gemini_text(api_key, text)
        
    if not parsed:
        parsed = parse_simple_text_expense(text)
        
    if parsed and isinstance(parsed, dict) and parsed.get("amount"):
        tx_date = parsed.get("date", date.today().strftime("%Y-%m-%d"))
        title = parsed.get("title", "Xarajat")
        category = parsed.get("category", local_categorize(title))
        payment_type = parsed.get("payment_type", "Naqd")
        amount = float(parsed["amount"])
        
        add_transaction(user_id, tx_date, title, category, payment_type, amount, source="text")
        
        resp = format_confirmation({
            "date": tx_date,
            "title": title,
            "category": category,
            "payment_type": payment_type,
            "amount": amount
        })
        bot.reply_to(message, resp)
    else:
        bot.reply_to(
            message,
            "⚠️ Xarajatni tushunib bo'lmadi.\n\n"
            "Misol uchun shunday yozing:\n"
            "• <code>35000 tushlik</code> (Naqd)\n"
            "• <code>karta 150000 korzinka</code> (Karta)\n"
            "• Yoki chek rasmini / ovozli xabar yuboring."
        )

if __name__ == "__main__":
    init_db()
    print("DatoHisobchi_bot muvaffaqiyatli ishga tushdi...")
    bot.infinity_polling()
