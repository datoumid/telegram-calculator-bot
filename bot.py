import telebot
from telebot import types
import math
import ast
import operator
import re
import os
import sys

TOKEN = "8673100891:AAH62FeEigIe4BHjBDJiJAjfRHvUiXsyAo4"

bot = telebot.TeleBot(TOKEN, parse_mode="HTML")

# Har bir foydalanuvchi holati (state) va tarixi
user_sessions = {}
user_history = {}

# Xavfsiz matematik amallar (Safe Math Evaluator)
SAFE_OPERATORS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}

def safe_eval(expr_str):
    """
    AST orqali xavfsiz matematik hisoblash.
    Hech qanday zararli kod (exec/eval) ishlatilmaydi.
    """
    expr_str = expr_str.replace("×", "*").replace("÷", "/").replace("−", "-").replace("^", "**")
    expr_str = expr_str.replace(":", "/").replace(",", ".")
    expr_str = re.sub(r'(\d+)\s*[xX]\s*(\d+)', r'\1*\2', expr_str)
    
    # Kvadrat ildiz (√) ni almashtirish
    expr_str = re.sub(r'√(\d+(\.\d+)?)', r'sqrt(\1)', expr_str)

    def _eval(node):
        if isinstance(node, ast.Expression):
            return _eval(node.body)
        elif isinstance(node, ast.Constant): # Python 3.8+ raqamlar
            if isinstance(node.value, (int, float)):
                return node.value
            raise ValueError("Faqat raqamlar qabul qilinadi")
        elif isinstance(node, ast.BinOp):
            op_type = type(node.op)
            if op_type not in SAFE_OPERATORS:
                raise ValueError(f"Qo'llab-quvvatlanmaydigan amal: {node.op}")
            left = _eval(node.left)
            right = _eval(node.right)
            if op_type == ast.Div and right == 0:
                raise ZeroDivisionError("Nolga bo'lish mumkin emas!")
            return SAFE_OPERATORS[op_type](left, right)
        elif isinstance(node, ast.UnaryOp):
            op_type = type(node.op)
            if op_type not in SAFE_OPERATORS:
                raise ValueError("Qo'llab-quvvatlanmaydigan unar amal")
            operand = _eval(node.operand)
            return SAFE_OPERATORS[op_type](operand)
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id == "sqrt":
                if len(node.args) != 1:
                    raise ValueError("sqrt faqat bitta argument oladi")
                val = _eval(node.args[0])
                if val < 0:
                    raise ValueError("Manfiy sondan ildiz chiqarib bo'lmaydi")
                return math.sqrt(val)
            raise ValueError("Noma'lum funksiya")
        else:
            raise ValueError("Noto'g'ri ifoda")

    try:
        parsed = ast.parse(expr_str, mode='eval')
        res = _eval(parsed)
        # Butun son bo'lsa butun, o'nli bo'lsa chiroyli yaxlitlash
        if isinstance(res, float):
            if res.is_integer():
                return int(res)
            return round(res, 8)
        return res
    except ZeroDivisionError:
        return "Nolga bo'lish mumkin emas!"
    except Exception:
        return "Xato!"

def get_calculator_markup():
    """Interaktiv inline kalkulyator klaviaturasi"""
    markup = types.InlineKeyboardMarkup(row_width=4)
    
    # 1-qator: Maxsus amallar
    btn_c = types.InlineKeyboardButton("C", callback_data="calc_C")
    btn_back = types.InlineKeyboardButton("⌫", callback_data="calc_BACK")
    btn_mod = types.InlineKeyboardButton("%", callback_data="calc_%")
    btn_div = types.InlineKeyboardButton("÷", callback_data="calc_÷")
    
    # 2-qator
    btn_7 = types.InlineKeyboardButton("7", callback_data="calc_7")
    btn_8 = types.InlineKeyboardButton("8", callback_data="calc_8")
    btn_9 = types.InlineKeyboardButton("9", callback_data="calc_9")
    btn_mul = types.InlineKeyboardButton("×", callback_data="calc_×")
    
    # 3-qator
    btn_4 = types.InlineKeyboardButton("4", callback_data="calc_4")
    btn_5 = types.InlineKeyboardButton("5", callback_data="calc_5")
    btn_6 = types.InlineKeyboardButton("6", callback_data="calc_6")
    btn_sub = types.InlineKeyboardButton("−", callback_data="calc_−")
    
    # 4-qator
    btn_1 = types.InlineKeyboardButton("1", callback_data="calc_1")
    btn_2 = types.InlineKeyboardButton("2", callback_data="calc_2")
    btn_3 = types.InlineKeyboardButton("3", callback_data="calc_3")
    btn_add = types.InlineKeyboardButton("+", callback_data="calc_+")
    
    # 5-qator
    btn_sign = types.InlineKeyboardButton("±", callback_data="calc_SIGN")
    btn_0 = types.InlineKeyboardButton("0", callback_data="calc_0")
    btn_dot = types.InlineKeyboardButton(".", callback_data="calc_.")
    btn_eq = types.InlineKeyboardButton("=", callback_data="calc_=")

    # 6-qator: Qo'shimcha qulayliklar
    btn_sqrt = types.InlineKeyboardButton("√", callback_data="calc_√")
    btn_pow = types.InlineKeyboardButton("^", callback_data="calc_^")
    btn_lparen = types.InlineKeyboardButton("(", callback_data="calc_(")
    btn_rparen = types.InlineKeyboardButton(")", callback_data="calc_)")

    markup.add(btn_c, btn_back, btn_mod, btn_div)
    markup.add(btn_7, btn_8, btn_9, btn_mul)
    markup.add(btn_4, btn_5, btn_6, btn_sub)
    markup.add(btn_1, btn_2, btn_3, btn_add)
    markup.add(btn_sign, btn_0, btn_dot, btn_eq)
    markup.add(btn_sqrt, btn_pow, btn_lparen, btn_rparen)
    
    return markup

def get_main_menu_keyboard():
    """Asosiy pastki menyu tugmalari"""
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    btn1 = types.KeyboardButton("🧮 Yangi kalkulyator")
    btn2 = types.KeyboardButton("📜 Tarix")
    btn3 = types.KeyboardButton("ℹ️ Yordam")
    btn4 = types.KeyboardButton("🧹 Tarixni tozalash")
    markup.add(btn1, btn2)
    markup.add(btn3, btn4)
    return markup

def format_calc_text(expression="0", result=None):
    text = (
        "<b>╔═══════════════════════╗</b>\n"
        "<b>║   🧮 QULAY KALKULYATOR   ║</b>\n"
        "<b>╚═══════════════════════╝</b>\n\n"
        f"📟 <b>Displey:</b> <code>{expression}</code>\n"
    )
    if result is not None:
        text += f"💡 <b>Natija:</b> <code>{result}</code>\n"
    text += "\n<i>Tugmalarni bosib hisoblang yoki to'g'ridan-to'g'ri misol yozib yuboring (masalan: 125*4+50):</i>"
    return text

@bot.message_handler(commands=['start'])
def handle_start(message):
    user_id = message.from_user.id
    user_sessions[user_id] = "0"
    welcome_msg = (
        f"Assalomu alaykum, <b>{message.from_user.first_name}</b>!\n\n"
        "🧮 <b>Telegram Kalkulyator Botiga xush kelibsiz!</b>\n\n"
        "✨ <b>Imkoniyatlar:</b>\n"
        "• Ekrandagi tugmalar orqali hisoblash\n"
        "• To'g'ridan-to'g'ri matn ko'rinishida misol yozish (masalan: <code>250 * 4 / 2</code>)\n"
        "• Amallar: <code>+</code>, <code>−</code>, <code>×</code>, <code>÷</code>, <code>%</code>, <code>^</code> (daraja), <code>√</code> (ildiz)\n"
        "• Hisoblash tarixi va tozalash\n\n"
        "Pastdagi tugmalardan foydalaning 👇"
    )
    bot.send_message(
        message.chat.id, 
        welcome_msg, 
        reply_markup=get_main_menu_keyboard()
    )
    bot.send_message(
        message.chat.id, 
        format_calc_text("0"), 
        reply_markup=get_calculator_markup()
    )

@bot.message_handler(commands=['help'])
def handle_help(message):
    help_text = (
        "📖 <b>Botdan foydalanish yo'riqnomasi:</b>\n\n"
        "1️⃣ <b>Tugmali kalkulyator:</b> Xabardagi inline tugmalarni bosing. Har bir bosish natijani darhol ekranda yangilaydi.\n"
        "2️⃣ <b>Yozma rejim:</b> Shunchaki misolni xabar sifatida yuboring, masalan:\n"
        "   • <code>12 * (45 + 15)</code>\n"
        "   • <code>1500 / 25</code>\n"
        "   • <code>2 ^ 8</code>\n"
        "   • <code>√144</code>\n\n"
        "3️⃣ <b>Maxsus tugmalar:</b>\n"
        "   • <b>C</b> - butunlay tozalash\n"
        "   • <b>⌫</b> - oxirgi belgini o'chirish\n"
        "   • <b>±</b> - ishorani o'zgartirish\n"
        "   • <b>=</b> - hisoblash"
    )
    bot.send_message(message.chat.id, help_text)

@bot.message_handler(func=lambda msg: msg.text == "🧮 Yangi kalkulyator")
def menu_new_calculator(message):
    user_id = message.from_user.id
    user_sessions[user_id] = "0"
    bot.send_message(
        message.chat.id, 
        format_calc_text("0"), 
        reply_markup=get_calculator_markup()
    )

@bot.message_handler(func=lambda msg: msg.text == "📜 Tarix")
def menu_history(message):
    user_id = message.from_user.id
    history = user_history.get(user_id, [])
    if not history:
        bot.send_message(message.chat.id, "📭 Hozircha hisoblash tarixi mavjud emas.")
        return
    
    text = "📜 <b>Oxirgi hisoblashlar tarixi:</b>\n\n"
    for i, item in enumerate(reversed(history[-10:]), 1):
        text += f"{i}. <code>{item['expr']}</code> = <b>{item['res']}</b>\n"
    bot.send_message(message.chat.id, text)

@bot.message_handler(func=lambda msg: msg.text == "🧹 Tarixni tozalash")
def menu_clear_history(message):
    user_id = message.from_user.id
    user_history[user_id] = []
    bot.send_message(message.chat.id, "✅ Tarix muvaffaqiyatli tozalandi!")

@bot.message_handler(func=lambda msg: msg.text == "ℹ️ Yordam")
def menu_help(message):
    handle_help(message)

# Inline tugmalar callback handler
@bot.callback_query_handler(func=lambda call: call.data.startswith("calc_"))
def callback_calculator(call):
    user_id = call.from_user.id
    action = call.data.replace("calc_", "")
    
    current_expr = user_sessions.get(user_id, "0")
    result_text = None
    
    if action == "C":
        current_expr = "0"
    elif action == "BACK":
        if len(current_expr) > 1 and current_expr not in ["0", "Xato!", "Nolga bo'lish mumkin emas!"]:
            current_expr = current_expr[:-1]
        else:
            current_expr = "0"
    elif action == "=":
        if current_expr in ["0", "Xato!", "Nolga bo'lish mumkin emas!"]:
            bot.answer_callback_query(call.id, "Misol kiritilmadi!")
            return
        
        calc_result = safe_eval(current_expr)
        result_text = str(calc_result)
        
        # Tarixga qo'shish
        if str(calc_result) not in ["Xato!", "Nolga bo'lish mumkin emas!"]:
            if user_id not in user_history:
                user_history[user_id] = []
            user_history[user_id].append({"expr": current_expr, "res": calc_result})
            user_sessions[user_id] = str(calc_result)
        else:
            user_sessions[user_id] = "0"
    elif action == "SIGN":
        if current_expr != "0" and current_expr not in ["Xato!", "Nolga bo'lish mumkin emas!"]:
            if current_expr.startswith("-"):
                current_expr = current_expr[1:]
            else:
                current_expr = "-" + current_expr
    elif action in ["+", "−", "×", "÷", "%", "^"]:
        if current_expr in ["Xato!", "Nolga bo'lish mumkin emas!"]:
            current_expr = "0"
        # Oxirgi belgi operator bo'lsa, uni almashtirish
        if current_expr and current_expr[-1] in ["+", "−", "×", "÷", "%", "^"]:
            current_expr = current_expr[:-1] + action
        else:
            current_expr += action
    elif action == ".":
        if current_expr in ["0", "Xato!", "Nolga bo'lish mumkin emas!"]:
            current_expr = "0."
        else:
            # Oxirgi son qismida nuqta bormi tekshirish
            parts = re.split(r'[\+\−\×\÷\%\^\(\)]', current_expr)
            if parts and "." not in parts[-1]:
                current_expr += "."
    elif action == "√":
        if current_expr == "0" or current_expr in ["Xato!", "Nolga bo'lish mumkin emas!"]:
            current_expr = "√"
        else:
            current_expr += "√"
    elif action in ["(", ")"]:
        if current_expr == "0":
            current_expr = action
        else:
            current_expr += action
    else: # Raqamlar 0-9
        if current_expr in ["0", "Xato!", "Nolga bo'lish mumkin emas!"]:
            current_expr = action
        else:
            current_expr += action

    if action != "=":
        user_sessions[user_id] = current_expr

    new_display = format_calc_text(current_expr, result_text)
    
    try:
        bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            text=new_display,
            reply_markup=get_calculator_markup()
        )
    except Exception:
        pass
    
    bot.answer_callback_query(call.id)

# Foydalanuvchi to'g'ridan-to'g'ri matn yuborganda hisoblash
@bot.message_handler(func=lambda msg: True)
def handle_text_calc(message):
    text = message.text.strip()
    # Matematik belgilarni tekshirish
    if re.search(r'[\d\+\-\*\/\^\%\(\)\.]', text):
        res = safe_eval(text)
        if str(res) not in ["Xato!", "Nolga bo'lish mumkin emas!"]:
            user_id = message.from_user.id
            if user_id not in user_history:
                user_history[user_id] = []
            user_history[user_id].append({"expr": text, "res": res})
            
            resp = (
                f"🧮 <b>Ifoda:</b> <code>{text}</code>\n"
                f"✅ <b>Natija:</b> <code>{res}</code>"
            )
            bot.reply_to(message, resp)
        else:
            bot.reply_to(message, f"⚠️ Ifodani hisoblab bo'lmadi yoki xatolik mavjud: <code>{res}</code>")
    else:
        bot.reply_to(message, "Iltimos, matematik ifoda kiriting yoki menyudagi tugmalardan foydalaning.")

if __name__ == '__main__':
    print("Bot muvaffaqiyatli ishga tushdi...")
    bot.infinity_polling()
