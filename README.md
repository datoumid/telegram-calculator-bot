# 🤖 Telegram Botlar To'plami

Ushbu loyihada ikkita to'liq ishga tushirilgan Telegram boti mavjud:
1. **📊 Shaxsiy Moliya va Hisob-kitob Boti** — [@DatoHisobchi_bot](https://t.me/DatoHisobchi_bot)
2. **🧮 Interaktiv Kalkulyator Boti** — [@caldfs_bot](https://t.me/caldfs_bot)

---

## 1. 📊 Shaxsiy Moliya Boti (@DatoHisobchi_bot)

Foydalanuvchining shaxsiy bank ko'chirmalari (PDF), naqd xarajatlari (ovozli xabarlar), xarid cheklari (fotosuratlar) va matnli xabarlar orqali ma'lumotlarni tahlil qiluvchi, toifalovchi hamda oylik/kunlik hisobot beruvchi aqlli bot.

### ✨ Asosiy imkoniyatlari:
- 📄 **Bank ko'chirmalari (PDF):** Click, Payme, Uzum Bank va boshqa banklarning PDF hisobotlaridan faqat karta xarajatlarini ajratib oladi (`To'lov turi: Karta`).
- 🎤 **Ovozli xabarlar (Audio):** Ovozdan xarajat summasi va nima uchun sarflanganini aniqlaydi (`To'lov turi: Naqd`).
- 📸 **Cheklar (Fotosuratlar):** Chekdagi umumiy summani, savdo nuqtasi nomini, sanasini va to'lov turini (Karta yoki Naqd) aniqlaydi.
- 💬 **Matnli xabarlar:** Masalan: `35000 tushlik`, `karta 180000 korzinka`, `25k taxi`.
- 🏷 **Toifalar bo'yicha taqsimot:**
  - 🥗 Oziq-ovqat va supermarket
  - ☕ Kafe va restoranlar
  - 🚕 Transport (yoqilg'i, taksi, jamoat transporti)
  - 💡 Kommunal, uy va aloqa xarajatlari
  - 💊 Salomatlik va sport
  - 🛍 Shaxsiy xaridlar va kiyim-kechak
  - 📚 Ta'lim va obunalar
  - 📦 Kutilmagan / Boshqa xarajatlar
- 📊 **Hisobotlar va Tahlil:**
  - Oylik va kunlik to'liq moliyaviy hisobot
  - To'lov turlari bo'yicha tahlil: `💳 Karta` vs `💵 Naqd` (summa va foiz ulushi)
  - Toifalar kesimidagi xarajatlar va foizlar
  - Excel / CSV formatida barcha operatsiyalarni yuklab olish
- 🤖 **Sun'iy Intellekt:** Google Gemini API multimodal modeli bilan to'liq integratsiya qilingan (xohlovchilar bot ichida `/setkey` orqali o'z kalitini kiritishi mumkin).

### 🚀 Ishga tushirish:
- `start_finance_bot.bat` fayliga sichqoncha bilan 2 marta bosing, yoki terminalda:
  ```bash
  python finance_bot.py
  ```

---

## 2. 🧮 Telegram Kalkulyator Boti (@caldfs_bot)

Telegram uchun qulay, interaktiv tugmalarga (Inline displey) ega kalkulyator bot.

### ✨ Xususiyatlari:
- **Interaktiv tugmali displey:** `0-9`, `+`, `−`, `×`, `÷`, `%`, `^`, `√`, `( )`, `C`, `⌫`, `±`, `=`
- **Matnli hisoblash:** Har qanday matematik ifodani to'g'ridan-to'g'ri yozish
- **Tarix funksiyasi:** Oxirgi amallarni ko'rish

### 🚀 Ishga tushirish:
- `start_bot.bat` fayliga bosing, yoki terminalda:
  ```bash
  python bot.py
  ```

---

## 🛠 O'rnatish (Installation)

```bash
pip install -r requirements.txt
```
