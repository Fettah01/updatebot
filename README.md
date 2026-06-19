# KPI Update Bot — Abdufattoh PM

Ikkala Google Sheet'dan (KPI + Meeting Tracker) o'qiydi, har manager bo'yicha
update tuzadi va Telegram guruhga har 2 kunda avtomatik tashlaydi.

## Ishga tushirish (5 qadam)

### 1. Telegram bot
- @BotFather → /newbot → token ol → `.env` ichidagi BOT_TOKEN ga yoz
- Botni guruhga qo'sh va admin qil

### 2. Google service account (sheet o'qishi uchun)
- console.cloud.google.com → yangi project
- "Google Sheets API" ni yoq
- Service Account yarat → JSON kalit yuklab ol → shu papkaga `service_account.json` deb qo'y
- JSON ichidagi `client_email` ni nusxa ol → IKKALA sheet'ni o'sha email'ga Viewer qilib share qil

### 3. Sozlash
- `.env.example` ni `.env` deb nomla
- BOT_TOKEN ni yoz (sheet ID'lar oldindan to'ldirilgan)

### 4. O'rnatish
```
pip install -r requirements.txt
```

### 5. Ishga tushir
```
python kpi_update_bot.py
```
- Guruhda /chatid → bergan ID ni .env dagi GROUP_CHAT_ID ga yoz → botni qayta ishga tushir
- Guruhda /update → darrov test update
- Har 2 kunda (ertalab 9:00) avtomatik tashlaydi

## Doimiy ishlashi (Railway)
1. GitHub repo och, fayllarni qo'y
2. railway.app → Deploy from GitHub
3. Variables ga .env dagilarni + service_account JSON ni qo'y
4. Start command: `python kpi_update_bot.py`

## Sozlamalar (.env)
- UPDATE_EVERY_DAYS — necha kunda bir (default 2)
- SEND_AT_HOUR — soat nechada (default 9)
- KPI_WORKSHEET / MEETING_WORKSHEET — tab nomlari boshqacha bo'lsa to'g'irlang
