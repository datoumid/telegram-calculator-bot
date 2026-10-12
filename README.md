# 🧮 Telegram Kalkulyator Boti (@caldfs_bot)

Telegram uchun qulay, interaktiv tugmalarga (Inline va Reply tugmalar) ega kalkulyator bot.

## ✨ Xususiyatlari
- **Interaktiv tugmali displey (Inline Keyboard):** Xuddi haqiqiy kalkulyatordek raqam va amallarni tugmalar orqali bosish, ekranning bir zumda jonli yangilanishi.
- **Tugmalar to'plami:**
  - Raqamlar: `0` - `9`, nuqta `.`
  - Asosiy amallar: `+`, `−`, `×`, `÷`
  - Murakkab amallar: `%` (foiz), `^` (daraja), `√` (ildiz), `( )` (qavslar)
  - Boshqaruv tugmalari: `C` (tozalash), `⌫` (oxirgi belgini o'chirish), `±` (ishora), `=` (hisoblash)
- **Matnli rejim:** Foydalanuvchi to'g'ridan-to'g'ri xabar sifatida ifoda yozsa ham (masalan, `25 * 4 + 10` yoki `1200 / 4`), bot darhol javobini hisoblab beradi.
- **Tarix funksiyasi:** Oxirgi bajarilgan 10 ta amallar ro'yxatini ko'rish va tozalash.
- **Xavfsiz hisoblash:** `AST` tahlilchisi asosida ishlaydi (eval kabi xavfsizlikka tahdid solmaydi).

## 🚀 Ishga tushirish

1. **Bir martalik kutubxona o'rnatish:**
   ```bash
   pip install -r requirements.txt
   ```

2. **Botni yoqish:**
   - `start_bot.bat` fayliga sichqoncha bilan 2 marta bosing, yoki terminalda:
   ```bash
   python bot.py
   ```

3. **Telegramda sinab ko'rish:**
   - Telegramda [@caldfs_bot](https://t.me/caldfs_bot) ni qidiring yoki `/start` buyrug'ini yuboring.
