DONAT BOT - ISHGA TUSHIRISH

1) Python 3.10+ o'rnating (python.org). O'rnatayotganda "Add to PATH" ni belgilang.
2) .env faylini oching va to'ldiring:
   BOT_TOKEN  - @BotFather dan olingan token
   ADMIN_IDS  - sizning Telegram ID (@userinfobot dan bilib oling)
   API_URL    - provayder manzili (https://...)
   API_KEY    - provayder kaliti
3) Kutubxonalarni o'rnating:
   pip install -r requirements.txt
4) Botni ishga tushiring:
   python main.py     (Windows: run.bat ni ikki marta bosing)
5) Telegramda botingizga /start, keyin /admin yozing.

API manzil va kalitni keyinroq admin paneldan ham o'zgartirsa bo'ladi.
Serverda doimiy ishlashi uchun: pip install -r requirements.txt, so'ng
   nohup python3 main.py &
yoki systemd/pm2 ishlating.
