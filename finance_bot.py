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
from telebot import types, apihelper

apihelper.ENABLE_MIDDLEWARE = True

# Modern Google GenAI SDK
try:
    from google import genai
    from google.genai import types as genai_types
    HAS_GENAI = True
except ImportError:
    HAS_GENAI = False

HAS_GEMINI = HAS_GENAI

try:
    import pypdf
    HAS_PYPDF = True
except ImportError:
    HAS_PYPDF = False

try:
    import docx
    HAS_DOCX = True
except ImportError:
    HAS_DOCX = False

try:
    import openpyxl
    HAS_OPENPYXL = True
except ImportError:
    HAS_OPENPYXL = False

import zipfile
import xml.etree.ElementTree as ET
import csv
from html.parser import HTMLParser

class SimpleHTMLTextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.text_parts = []
    def handle_data(self, data):
        c = data.strip()
        if c:
            self.text_parts.append(c)
    def get_text(self):
        return "\n".join(self.text_parts)

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
            msg_id INTEGER,
            bot_msg_id INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    try:
        cur.execute("ALTER TABLE transactions ADD COLUMN msg_id INTEGER")
    except Exception:
        pass
    try:
        cur.execute("ALTER TABLE transactions ADD COLUMN bot_msg_id INTEGER")
    except Exception:
        pass
    # Foydalanuvchi sozlamalari (masalan, shaxsiy Gemini API kaliti)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS user_settings (
            user_id INTEGER PRIMARY KEY,
            gemini_api_key TEXT,
            currency TEXT DEFAULT 'so''m'
        )
    """)
    # Chatdagi xabarlarni kuzatish (tozalash uchun)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS chat_messages (
            chat_id INTEGER,
            message_id INTEGER,
            PRIMARY KEY (chat_id, message_id)
        )
    """)
    conn.commit()
    conn.close()

def get_connection():
    return sqlite3.connect(DB_PATH)

def record_msg(chat_id, message_id):
    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("INSERT OR IGNORE INTO chat_messages (chat_id, message_id) VALUES (?, ?)", (chat_id, message_id))
        conn.commit()
        conn.close()
    except Exception:
        pass

def get_tracked_messages(chat_id):
    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("SELECT message_id FROM chat_messages WHERE chat_id = ? ORDER BY message_id DESC", (chat_id,))
        rows = [r[0] for r in cur.fetchall()]
        conn.close()
        return rows
    except Exception:
        return []

def remove_tracked_messages(chat_id):
    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("DELETE FROM chat_messages WHERE chat_id = ?", (chat_id,))
        conn.commit()
        conn.close()
    except Exception:
        pass

def clear_entire_chat(chat_id, current_msg_id):
    tracked = set(get_tracked_messages(chat_id))
    # Joriy xabardan orqaga 350 ta ID ni ham qo'shamiz (barcha avval yuborilgan fayllar va bot xabarlari uchun)
    for mid in range(max(1, current_msg_id - 350), current_msg_id + 1):
        tracked.add(mid)
        
    msg_ids = sorted(list(tracked), reverse=True)
    
    # 50 tadan bo'lib o'chirish (Telegram Bot API cheklovi)
    for i in range(0, len(msg_ids), 50):
        chunk = msg_ids[i:i + 50]
        try:
            bot.delete_messages(chat_id, chunk)
        except Exception:
            for mid in chunk:
                try:
                    bot.delete_message(chat_id, mid)
                except Exception:
                    pass
                    
    remove_tracked_messages(chat_id)
    
    try:
        new_msg = bot.send_message(
            chat_id,
            "🧹 <b>Chat muvaffaqiyatli tozalandi!</b>\n\n"
            "Barcha yuborilgan fayllar, ovozlar, chek rasmlari va bot xabarlari chatdan o'chirildi.\n\n"
            "<i>💡 Maslahat: Agar Telegram ilovangizda butun chat tarixini bir zumda 100% yo'qotmoqchi bo'lsangiz, Telegram chat sozlamalaridagi 'Tarixni tozalash' (Очистить историю) menyusidan ham foydalanishingiz mumkin.</i>",
            reply_markup=get_main_keyboard()
        )
        record_msg(chat_id, new_msg.message_id)
    except Exception:
        pass

# Avtomatik xabarlarni kuzatish (barcha kiruvchi va chiquvchi xabarlar uchun)
_orig_send_message = bot.send_message
def _tracked_send_message(*args, **kwargs):
    res = _orig_send_message(*args, **kwargs)
    if res and hasattr(res, 'chat') and hasattr(res, 'message_id'):
        record_msg(res.chat.id, res.message_id)
    return res
bot.send_message = _tracked_send_message

_orig_reply_to = bot.reply_to
def _tracked_reply_to(*args, **kwargs):
    res = _orig_reply_to(*args, **kwargs)
    if res and hasattr(res, 'chat') and hasattr(res, 'message_id'):
        record_msg(res.chat.id, res.message_id)
    return res
bot.reply_to = _tracked_reply_to

_orig_send_document = bot.send_document
def _tracked_send_document(*args, **kwargs):
    res = _orig_send_document(*args, **kwargs)
    if res and hasattr(res, 'chat') and hasattr(res, 'message_id'):
        record_msg(res.chat.id, res.message_id)
    return res
bot.send_document = _tracked_send_document

@bot.middleware_handler(update_types=['message'])
def _track_incoming_message(bot_instance, message):
    if message and hasattr(message, 'chat') and hasattr(message, 'message_id'):
        record_msg(message.chat.id, message.message_id)

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

def add_transaction(user_id, tx_date, title, category, payment_type, amount, tx_type='expense', source='text', msg_id=None, bot_msg_id=None):
    conn = get_connection()
    cur = conn.cursor()
    now_time = datetime.now().strftime("%H:%M")
    if not tx_date:
        tx_date = date.today().strftime("%Y-%m-%d")
    
    cur.execute("""
        INSERT INTO transactions (user_id, date, time, title, category, payment_type, amount, type, source, msg_id, bot_msg_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (user_id, tx_date, now_time, title, category, payment_type, amount, tx_type, source, msg_id, bot_msg_id))
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
    cur.execute("SELECT msg_id, bot_msg_id FROM transactions WHERE user_id = ? AND id = ?", (user_id, tx_id))
    row = cur.fetchone()
    cur.execute("DELETE FROM transactions WHERE id = ? AND user_id = ?", (tx_id, user_id))
    affected = cur.rowcount
    conn.commit()
    conn.close()
    msg_ids = (row[0], row[1]) if row else (None, None)
    return (affected > 0), msg_ids

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
    for attempt in range(2):
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
            print(f"Gemini Vision Error (attempt {attempt+1}): {e}")
            if attempt == 0:
                import time; time.sleep(1)
                continue
            return None

def call_gemini_audio(api_key, audio_bytes, mime_type="audio/ogg"):
    if not HAS_GENAI or not api_key:
        return None
    for attempt in range(2):
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
            print(f"Gemini Audio Error (attempt {attempt+1}): {e}")
            if attempt == 0:
                import time; time.sleep(1)
                continue
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

# --- HUJJATLAR VA MATN REDAKTORI FAYLLARI BILAN ISHLASH ---
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

def extract_text_from_document(file_path, file_name):
    """
    Har qanday matn redaktori va ofis formatlaridan (Word, Excel, PDF, TXT, CSV, RTF, ODT, HTML, JSON, MD) matn ajratib oladi.
    """
    ext = os.path.splitext(file_name.lower())[1]
    
    # 1. PDF
    if ext == ".pdf":
        return extract_text_from_pdf(file_path)
        
    # 2. Oddiy matn fayllari: TXT, LOG, MD, INI, CFG, TEXT
    if ext in [".txt", ".text", ".log", ".md", ".markdown", ".ini", ".cfg"]:
        for enc in ['utf-8-sig', 'utf-8', 'cp1251', 'latin-1']:
            try:
                with open(file_path, 'r', encoding=enc) as f:
                    return f.read()
            except UnicodeDecodeError:
                continue
            except Exception:
                break
        return ""
        
    # 3. CSV / TSV
    if ext in [".csv", ".tsv"]:
        delimiter = '\t' if ext == ".tsv" else ','
        for enc in ['utf-8-sig', 'utf-8', 'cp1251', 'latin-1']:
            try:
                rows_text = []
                with open(file_path, 'r', encoding=enc) as f:
                    reader = csv.reader(f, delimiter=delimiter)
                    for r in reader:
                        if any(c.strip() for c in r):
                            rows_text.append(" | ".join([c.strip() for c in r]))
                if rows_text:
                    return "\n".join(rows_text)
            except Exception:
                continue
        return ""
        
    # 4. JSON
    if ext == ".json":
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            return json.dumps(data, ensure_ascii=False, indent=2)
        except Exception:
            try:
                with open(file_path, 'r', encoding='cp1251') as f:
                    return f.read()
            except Exception:
                return ""
                
    # 5. HTML / HTM
    if ext in [".html", ".htm"]:
        for enc in ['utf-8', 'cp1251', 'latin-1']:
            try:
                with open(file_path, 'r', encoding=enc) as f:
                    html_content = f.read()
                parser = SimpleHTMLTextExtractor()
                parser.feed(html_content)
                txt = parser.get_text()
                if txt.strip():
                    return txt
            except Exception:
                continue
        return ""
        
    # 6. RTF (Rich Text Format - WordPad va boshqalar)
    if ext == ".rtf":
        try:
            with open(file_path, 'r', encoding='latin-1', errors='ignore') as f:
                rtf_content = f.read()
            clean = re.sub(r'\\[a-z]{1,32}(-?\d+)? ?', ' ', rtf_content)
            clean = re.sub(r'[{}]', ' ', clean)
            lines = [l.strip() for l in clean.splitlines() if l.strip()]
            return "\n".join(lines)
        except Exception:
            return ""
            
    # 7. Word: DOCX
    if ext == ".docx":
        # python-docx orqali o'qish
        if HAS_DOCX:
            try:
                doc = docx.Document(file_path)
                paras = [p.text for p in doc.paragraphs if p.text.strip()]
                for table in doc.tables:
                    for row in table.rows:
                        row_vals = [cell.text.strip() for cell in row.cells if cell.text.strip()]
                        if row_vals:
                            paras.append(" | ".join(row_vals))
                if paras:
                    return "\n".join(paras)
            except Exception as e:
                print(f"docx library error: {e}")
        # Zaxira (mustaqil ZIP+XML orqali)
        try:
            with zipfile.ZipFile(file_path) as z:
                xml_content = z.read('word/document.xml')
                tree = ET.fromstring(xml_content)
                paras = []
                for p in tree.iter():
                    if p.tag.endswith('}p'):
                        p_text = "".join(node.text for node in p.iter() if node.tag.endswith('}t') and node.text)
                        if p_text.strip():
                            paras.append(p_text.strip())
                if paras:
                    return "\n".join(paras)
        except Exception as e:
            print(f"docx fallback error: {e}")
            
    # 8. Excel: XLSX
    if ext in [".xlsx", ".xlsm"]:
        if HAS_OPENPYXL:
            try:
                wb = openpyxl.load_workbook(file_path, data_only=True)
                sheets_text = []
                for sheet in wb.worksheets:
                    for row in sheet.iter_rows(values_only=True):
                        vals = [str(v).strip() for v in row if v is not None and str(v).strip()]
                        if vals:
                            sheets_text.append(" | ".join(vals))
                if sheets_text:
                    return "\n".join(sheets_text)
            except Exception as e:
                print(f"openpyxl error: {e}")
        try:
            with zipfile.ZipFile(file_path) as z:
                shared = []
                if 'xl/sharedStrings.xml' in z.namelist():
                    s_tree = ET.fromstring(z.read('xl/sharedStrings.xml'))
                    for node in s_tree.iter():
                        if node.tag.endswith('}t') and node.text:
                            shared.append(node.text)
                return "\n".join(shared)
        except Exception:
            pass
            
    # 9. OpenDocument: ODT (LibreOffice / OpenOffice)
    if ext == ".odt":
        try:
            with zipfile.ZipFile(file_path) as z:
                xml_content = z.read('content.xml')
                tree = ET.fromstring(xml_content)
                texts = []
                for node in tree.iter():
                    if node.text and node.text.strip():
                        texts.append(node.text.strip())
                return "\n".join(texts)
        except Exception:
            pass

    # 10. Universal zaxira (har qanday boshqa faylni xom matn sifatida ochish)
    for enc in ['utf-8', 'cp1251', 'latin-1']:
        try:
            with open(file_path, 'r', encoding=enc, errors='ignore') as f:
                content = f.read(50000)
                printable = sum(1 for c in content if c.isprintable() or c in '\n\r\t')
                if printable > len(content) * 0.7:
                    return content
        except Exception:
            continue
            
    return ""

def parse_document_with_gemini(api_key, text, file_name="hujjat"):
    if not HAS_GENAI or not api_key:
        return None
    try:
        client = genai.Client(api_key=api_key)
        prompt = (
            f"Foydalanuvchi yuborgan hujjat ({file_name}) ichidan barcha xarajatlarni (chiqimlarni) ajratib oling. "
            "Bu matn redaktorida yozilgan qaydlar, Word hujjati, Excel jadvali, CSV fayl yoki bank ko'chirmasi bo'lishi mumkin. "
            "Har bir xarajat uchun quyidagi ma'lumotlarni aniqlang:\n"
            "- date: Sana YYYY-MM-DD ko'rinishida (agar sanasi aniq bo'lmasa, bugungi sana: " + date.today().strftime('%Y-%m-%d') + ")\n"
            "- title: Xarid qilingan narsa, to'lov yoki do'kon nomi\n"
            "- category: Quyidagi toifalardan mos keladigani:\n"
            "  * Oziq-ovqat va supermarket\n"
            "  * Kafe va restoranlar\n"
            "  * Transport (yoqilg'i, taksi, jamoat transporti)\n"
            "  * Kommunal, uy va aloqa xarajatlari\n"
            "  * Salomatlik va sport\n"
            "  * Shaxsiy xaridlar va kiyim-kechak\n"
            "  * Ta'lim va obunalar\n"
            "  * Kutilmagan / Boshqa xarajatlar\n"
            "- payment_type: 'Karta' yoki 'Naqd'\n"
            "- amount: Faqat son (masalan: 45000)\n\n"
            "Natijani FAQAT JSON ro'yxat (list of objects) shaklida qaytaring, boshqa hech qanday izoh qo'shmang:\n\n"
            + text[:30000]
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
        print(f"Document Gemini error: {e}")
        return None

def parse_bank_statement_with_gemini(api_key, text):
    return parse_document_with_gemini(api_key, text, "bank_statement")

def parse_bank_statement_heuristic(text):
    """
    Bank ko'chirmalari (Click, Payme, Uzum, banklar) ichidan tranzaksiyalarni qidirish
    """
    transactions = []
    lines = text.split("\n")
    
    date_pattern = r'(\d{2}[./-]\d{2}[./-]\d{4}|\d{4}-\d{2}-\d{2})'
    
    for line in lines:
        line_clean = line.strip()
        if not line_clean:
            continue
        d_match = re.search(date_pattern, line_clean)
        if d_match:
            tx_date_str = d_match.group(1)
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
            
            nums = re.findall(r'[-–—]?\s*(\d{1,3}(?:[\s.,]\d{3})*(?:[.,]\d{2})?)\s*(?:UZS|so\'m|сум)?', line_clean)
            if nums:
                for n in nums:
                    val_str = n.replace(" ", "").replace(",", "")
                    try:
                        amt = float(val_str)
                        if 1000 <= amt <= 100000000:
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

def parse_document_heuristic(text):
    """
    Hujjat ichidan xarajatlarni heuristik ajratib olish (Bank ko'chirmasi + Matn redaktori qaydlari)
    """
    # 1. Bank ko'chirmasi shaklida qidirish
    txs = parse_bank_statement_heuristic(text)
    if txs:
        return txs

    # 2. Oddiy matn redaktori qaydlari (masalan: Notepad, Word, RTF da har bir qatorda xarajat)
    transactions = []
    lines = text.split("\n")
    for line in lines:
        line_clean = line.strip()
        if not line_clean or len(line_clean) < 3:
            continue
        parsed = parse_simple_text_expense(line_clean)
        if parsed and isinstance(parsed, dict) and parsed.get("amount"):
            tx_date = parsed.get("date", date.today().strftime("%Y-%m-%d"))
            title = parsed.get("title", "Xarajat")
            category = local_categorize(title)
            payment_type = parsed.get("payment_type", "Naqd")
            amount = float(parsed["amount"])
            transactions.append({
                "date": tx_date,
                "title": title[:40].strip(),
                "amount": amount,
                "category": category,
                "payment_type": payment_type
            })
            
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
    b6 = types.KeyboardButton("🗑 Tarixni o'chirish")
    b7 = types.KeyboardButton("🧹 Butun chatni tozalash")
    markup.add(b1, b2)
    markup.add(b3, b4)
    markup.add(b5, b6)
    markup.add(b7)
    return markup

def get_delete_single_markup(tx_id):
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("🗑 Ushbu xarajatni o'chirish", callback_data=f"del_{tx_id}"))
    return markup

def get_recent_markup(rows):
    markup = types.InlineKeyboardMarkup(row_width=5)
    buttons = []
    for idx, r in enumerate(rows, start=1):
        tx_id = r[0]
        buttons.append(types.InlineKeyboardButton(f"🗑 {idx}", callback_data=f"del_{tx_id}"))
    if buttons:
        markup.add(*buttons)
        markup.add(types.InlineKeyboardButton("🧹 Barcha tarixni tozalash", callback_data="ask_clear_all"))
        markup.add(types.InlineKeyboardButton("🗑 Butun chatni tozalash", callback_data="ask_clear_chat"))
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
        f"3. 📄 <b>Har qanday hujjat yoki fayl:</b> Word (.docx), Excel (.xlsx), PDF, TXT, CSV, RTF va boshqa istalgan matn redaktori formatidagi faylni yuboring (xarajatlarni avtomatik ajratib olaman).\n"
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

@bot.message_handler(func=lambda msg: msg.text in ["🧾 Oxirgi amallar", "🗑 Tarixni o'chirish"] or msg.text in ["/history", "/delete", "/clear"])
def handle_recent(message):
    user_id = message.from_user.id
    rows = get_recent_transactions(user_id, limit=10)
    
    if not rows:
        bot.send_message(message.chat.id, "Sizda hali saqlangan xarajatlar mavjud emas.")
        return
        
    msg = "🧾 <b>So'nggi xarajatlar ro'yxati:</b>\n"
    msg += "<i>O'chirmoqchi bo'lgan xarajat raqamini pastdagi tugmalardan tanlang:</i>\n\n"
    
    num_emojis = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
    for idx, r in enumerate(rows, start=1):
        num_str = num_emojis[idx-1] if idx <= 10 else f"{idx}."
        icon = CATEGORY_ICONS.get(r[3], "•")
        pt = "💳" if r[4] == "Karta" else "💵"
        msg += f"{num_str} <code>{r[1]}</code> | {icon} <b>{r[2]}</b> ({pt}): <code>{format_currency(r[5])}</code>\n"
        
    markup = get_recent_markup(rows)
    bot.send_message(message.chat.id, msg, reply_markup=markup)

@bot.message_handler(func=lambda msg: msg.text == "➕ Xarajat kiritish")
def handle_add_expense_prompt(message):
    msg = (
        "➕ <b>Xarajat kiritish juda oson:</b>\n\n"
        "1. Matn yozing: <code>45000 tushlik</code> yoki <code>karta 120000 korzinka</code>\n"
        "2. Yoki ovozli xabar (audio) yuboring;\n"
        "3. Yoki xarid chekini rasmga olib yuboring;\n"
        "4. Yoki istalgan matn/hujjat faylini yuboring (Word, Excel, PDF, TXT, CSV, RTF va h.k.)."
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

# --- TARIXNI O'CHIRISH VA CALLBACK HANDLERS ---
@bot.callback_query_handler(func=lambda call: True)
def handle_callback_query(call):
    user_id = call.from_user.id
    data = call.data
    
    if data.startswith("del_"):
        try:
            tx_id = int(data.split("_")[1])
            success, (orig_msg_id, orig_bot_msg_id) = delete_transaction(user_id, tx_id)
            if success:
                bot.answer_callback_query(call.id, "Xarajat o'chirildi! 🗑")
                # 1. Foydalanuvchining asl yuborgan fayli (ovoz, rasm, PDF, matn)ni chatdan o'chirish
                target_msg = orig_msg_id
                if not target_msg and call.message.reply_to_message:
                    target_msg = call.message.reply_to_message.message_id
                if target_msg:
                    try:
                        bot.delete_message(call.message.chat.id, target_msg)
                    except Exception:
                        pass
                
                # 2. Agar bitta xarajat kartochkasi bo'lsa (reply_markup bitta del tugmasiga ega bo'lsa)
                is_single = (call.message.reply_markup and len(call.message.reply_markup.keyboard) <= 1)
                if is_single:
                    try:
                        bot.delete_message(call.message.chat.id, call.message.message_id)
                    except Exception:
                        try:
                            bot.edit_message_text(
                                "🗑 <b>Xarajat va yuborilgan fayl chatdan o'chirildi.</b>",
                                chat_id=call.message.chat.id,
                                message_id=call.message.message_id
                            )
                        except Exception:
                            pass
                else:
                    # Oxirgi amallar ro'yxatidan o'chirilgan bo'lsa, ro'yxatni yangilash
                    rows = get_recent_transactions(user_id, limit=10)
                    if not rows:
                        try:
                            bot.edit_message_text("🧾 Barcha so'nggi xarajatlar o'chirildi.", chat_id=call.message.chat.id, message_id=call.message.message_id)
                        except Exception:
                            pass
                    else:
                        num_emojis = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
                        msg = "🧾 <b>So'nggi xarajatlar ro'yxati:</b>\n<i>O'chirmoqchi bo'lgan xarajat raqamini tanlang:</i>\n\n"
                        for idx, r in enumerate(rows, start=1):
                            num_str = num_emojis[idx-1] if idx <= 10 else f"{idx}."
                            icon = CATEGORY_ICONS.get(r[3], "•")
                            pt = "💳" if r[4] == "Karta" else "💵"
                            msg += f"{num_str} <code>{r[1]}</code> | {icon} <b>{r[2]}</b> ({pt}): <code>{format_currency(r[5])}</code>\n"
                        markup = get_recent_markup(rows)
                        try:
                            bot.edit_message_text(msg, chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=markup)
                        except Exception:
                            pass
            else:
                bot.answer_callback_query(call.id, "Topilmadi yoki allaqachon o'chirilgan.")
        except Exception:
            bot.answer_callback_query(call.id, "Xatolik yuz berdi.")
            
    elif data == "ask_clear_all":
        confirm_markup = types.InlineKeyboardMarkup(row_width=2)
        confirm_markup.add(
            types.InlineKeyboardButton("✅ Ha, tozalash", callback_data="do_clear_all"),
            types.InlineKeyboardButton("❌ Bekor qilish", callback_data="cancel_clear")
        )
        bot.answer_callback_query(call.id)
        try:
            bot.edit_message_text(
                "⚠️ <b>Diqqat!</b>\n\nBarcha xarajatlar tarixini butunlay o'chirib tashlamoqchimisiz?\nBu amalni ortga qaytarib bo'lmaydi.",
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                reply_markup=confirm_markup
            )
        except Exception:
            pass
            
    elif data == "do_clear_all":
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("DELETE FROM transactions WHERE user_id = ?", (user_id,))
        count = cur.rowcount
        conn.commit()
        conn.close()
        
        bot.answer_callback_query(call.id, "Barcha tarix tozalandi!")
        try:
            bot.edit_message_text(
                f"🧹 <b>Barcha xarajatlar tarixi muvaffaqiyatli tozalandi!</b>\n(Jami o'chirilgan yozuvlar soni: {count} ta)",
                chat_id=call.message.chat.id,
                message_id=call.message.message_id
            )
        except Exception:
            pass
            
    elif data == "ask_clear_chat":
        confirm_markup = types.InlineKeyboardMarkup(row_width=2)
        confirm_markup.add(
            types.InlineKeyboardButton("✅ Ha, chatni tozalash", callback_data="do_clear_chat"),
            types.InlineKeyboardButton("❌ Bekor qilish", callback_data="cancel_clear")
        )
        bot.answer_callback_query(call.id)
        try:
            bot.edit_message_text(
                "🧹 <b>Butun chatni tozalash:</b>\n\n"
                "Chatdagi siz yuborgan barcha ovozli xabarlar, chek fotosuratlari, PDF fayllar va bot xabarlari o'chirib tashlanadi.\n\n"
                "<i>(Eslatma: Xarajatlar statistikasi bazada saqlanib qoladi).</i>\n\n"
                "Rostdan ham chatni tozalashni xohlaysizmi?",
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                reply_markup=confirm_markup
            )
        except Exception:
            pass
            
    elif data == "do_clear_chat":
        bot.answer_callback_query(call.id, "Chat tozalanmoqda... 🧹")
        clear_entire_chat(call.message.chat.id, call.message.message_id)
            
    elif data == "cancel_clear":
        bot.answer_callback_query(call.id, "Bekor qilindi.")
        try:
            bot.edit_message_text(
                "❌ Amaliyot bekor qilindi.",
                chat_id=call.message.chat.id,
                message_id=call.message.message_id
            )
        except Exception:
            pass

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
        if api_key and HAS_GENAI:
            parsed = call_gemini_audio(api_key, file_bytes, mime_type="audio/ogg")
            
        if not parsed:
            if not api_key:
                err_text = (
                    "⚠️ Ovozli xabarni avtomatik eshitish uchun <b>Gemini API kaliti</b> zarur.\n"
                    "Iltimos, <code>/setkey KALITINGIZ</code> orqali kalitingizni kiriting yoki xarajatni matn ko'rinishida yozing (masalan: <code>35000 tushlik</code>)."
                )
            else:
                err_text = (
                    "⚠️ Ovozli xabarni tushunib bo'lmadi yoki AI serverida vaqtinchalik yuklama mavjud.\n"
                    "Iltimos, ovozli xabarni qaytadan yuboring yoki xarajatni matn ko'rinishida yozing (masalan: <code>35000 tushlik</code>)."
                )
            bot.edit_message_text(
                err_text,
                chat_id=message.chat.id,
                message_id=status_msg.message_id
            )
            return
            
        # Ovozli xabarlar odatda "Naqd" to'lov turi
        if not parsed.get("payment_type"):
            parsed["payment_type"] = "Naqd"
        if not parsed.get("date"):
            parsed["date"] = date.today().strftime("%Y-%m-%d")
            
        tx_id = add_transaction(
            user_id=user_id,
            tx_date=parsed["date"],
            title=parsed["title"],
            category=parsed.get("category", "Kutilmagan / Boshqa xarajatlar"),
            payment_type=parsed["payment_type"],
            amount=parsed["amount"],
            source="voice",
            msg_id=message.message_id
        )
        
        resp_text = format_confirmation(parsed)
        bot.edit_message_text(resp_text, chat_id=message.chat.id, message_id=status_msg.message_id, reply_markup=get_delete_single_markup(tx_id))
        
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
        if api_key and HAS_GENAI:
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
            
        tx_id = add_transaction(
            user_id=user_id,
            tx_date=parsed["date"],
            title=parsed["title"],
            category=parsed.get("category", "Kutilmagan / Boshqa xarajatlar"),
            payment_type=parsed["payment_type"],
            amount=parsed["amount"],
            source="photo",
            msg_id=message.message_id
        )
        
        resp_text = format_confirmation(parsed)
        bot.edit_message_text(resp_text, chat_id=message.chat.id, message_id=status_msg.message_id, reply_markup=get_delete_single_markup(tx_id))
        
    except Exception as e:
        bot.edit_message_text(f"❌ Xatolik yuz berdi: {e}", chat_id=message.chat.id, message_id=status_msg.message_id)

# --- HAR QANDAY MATN REDAKTORI VA HUJJAT FAYLLARI BILAN ISHLASH ---
@bot.message_handler(content_types=['document'])
def handle_document_file(message):
    doc = message.document
    user_id = message.from_user.id
    api_key = get_gemini_key(user_id)
    file_name = doc.file_name or "hujjat.txt"
    ext = os.path.splitext(file_name.lower())[1]
    
    status_msg = bot.reply_to(message, f"📄 <b>{file_name}</b> yuklab olinmoqda va tahlil qilinmoqda...")
    
    temp_path = None
    try:
        file_info = bot.get_file(doc.file_id)
        file_bytes = bot.download_file(file_info.file_path)
        
        with tempfile.NamedTemporaryFile(delete=False, suffix=ext or ".tmp") as f:
            f.write(file_bytes)
            temp_path = f.name
            
        doc_text = extract_text_from_document(temp_path, file_name)
        if not doc_text or not doc_text.strip():
            bot.edit_message_text(
                f"⚠️ <b>{file_name}</b> faylidan matn ajratib bo'lmadi.\n"
                "Iltimos, faylda matn yoki xarajat ma'lumotlari mavjudligini tekshiring.",
                chat_id=message.chat.id,
                message_id=status_msg.message_id
            )
            return
            
        transactions = []
        if api_key and HAS_GENAI:
            transactions = parse_document_with_gemini(api_key, doc_text, file_name) or []
            
        if not transactions:
            # Lokal tahlilchi (Bank ko'chirmalari yoki oddiy satrma-satr qaydlar)
            transactions = parse_document_heuristic(doc_text)
            
        if not transactions:
            bot.edit_message_text(
                f"⚠️ <b>{file_name}</b> faylidan xarajatlar topilmadi.\n\n"
                "To'liq AI tahlil uchun Gemini kalitini kiritib ko'ring: <code>/setkey KALIT</code>",
                chat_id=message.chat.id,
                message_id=status_msg.message_id
            )
            return
            
        saved_count = 0
        total_sum = 0
        preview_text = ""
        
        for tx in transactions:
            dt = tx.get("date", date.today().strftime("%Y-%m-%d"))
            title = tx.get("title", "Xarajat")
            cat = tx.get("category", local_categorize(title))
            ptype = tx.get("payment_type", "Karta")
            amt = float(tx.get("amount", 0))
            if amt > 0:
                add_transaction(user_id, dt, title, cat, ptype, amt, source=ext[1:] if ext else "doc", msg_id=message.message_id)
                saved_count += 1
                total_sum += amt
                if saved_count <= 5:
                    p_icon = "💳" if ptype == "Karta" else "💵"
                    preview_text += f"• <code>{dt}</code> | <b>{title[:18]}</b> ({p_icon}): <code>{format_currency(amt)}</code>\n"
                    
        res_msg = (
            f"✅ <b>{file_name}</b> muvaffaqiyatli qayta ishlandi!\n\n"
            f"📥 <b>Kiritilgan amallar soni:</b> {saved_count} ta\n"
            f"💰 <b>Jami xarajat:</b> <code>{format_currency(total_sum)}</code>\n\n"
            f"<b>Hujjatdan namunalar:</b>\n{preview_text}"
        )
        if saved_count > 5:
            res_msg += f"<i>...va yana {saved_count - 5} ta operatsiya saqlandi.</i>\n"
            
        res_msg += "\nTo'liq hisobotni ko'rish uchun <b>📊 Oylik hisobot</b> tugmasini bosing."
        
        # Agar bitta xarajat kiritilgan bo'lsa, o'chirish tugmachasini qo'shamiz
        reply_markup = None
        if saved_count == 1 and len(transactions) == 1:
            recent = get_recent_transactions(user_id, limit=1)
            if recent:
                reply_markup = get_delete_single_markup(recent[0][0])
                
        bot.edit_message_text(res_msg, chat_id=message.chat.id, message_id=status_msg.message_id, reply_markup=reply_markup)
        
    except Exception as e:
        bot.edit_message_text(f"❌ Xatolik yuz berdi: {e}", chat_id=message.chat.id, message_id=status_msg.message_id)
    finally:
        if temp_path and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except Exception:
                pass

# --- BUTUN CHATNI TOZALASH HANDLER ---
@bot.message_handler(func=lambda msg: msg.text == "🧹 Butun chatni tozalash" or msg.text in ["/clearchat", "/clear_chat"])
def handle_clear_chat_prompt(message):
    record_msg(message.chat.id, message.message_id)
    confirm_markup = types.InlineKeyboardMarkup(row_width=2)
    confirm_markup.add(
        types.InlineKeyboardButton("✅ Ha, chatni tozalash", callback_data="do_clear_chat"),
        types.InlineKeyboardButton("❌ Bekor qilish", callback_data="cancel_clear")
    )
    sent = bot.reply_to(
        message,
        "🧹 <b>Chatni tozalash:</b>\n\n"
        "Siz yuborgan barcha fayllar (ovozli xabarlar, chek fotosuratlari, PDF) hamda bot xabarlari chatdan to'liq o'chirib tashlanadi.\n"
        "<i>(Xarajatlar bazadagi statistikalarda saqlanib qoladi).</i>\n\n"
        "Davom etasizmi?",
        reply_markup=confirm_markup
    )
    record_msg(message.chat.id, sent.message_id)

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
    if api_key and HAS_GENAI:
        parsed = call_gemini_text(api_key, text)
        
    if not parsed:
        parsed = parse_simple_text_expense(text)
        
    if parsed and isinstance(parsed, dict) and parsed.get("amount"):
        tx_date = parsed.get("date", date.today().strftime("%Y-%m-%d"))
        title = parsed.get("title", "Xarajat")
        category = local_categorize(title)
        payment_type = parsed.get("payment_type", "Naqd")
        amount = float(parsed["amount"])
        
        tx_id = add_transaction(user_id, tx_date, title, category, payment_type, amount, source="text", msg_id=message.message_id)
        
        resp = format_confirmation({
            "date": tx_date,
            "title": title,
            "category": category,
            "payment_type": payment_type,
            "amount": amount
        })
        bot.reply_to(message, resp, reply_markup=get_delete_single_markup(tx_id))
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
