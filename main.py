import asyncio, json, logging, os, sqlite3, uuid
import aiohttp
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import BaseFilter, Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_IDS = {int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()}

# ======================= DB =======================
db = sqlite3.connect("bot.db")
db.row_factory = sqlite3.Row
db.executescript("""
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, balance INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS games(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT,
    need_server INTEGER DEFAULT 0, active INTEGER DEFAULT 1);
CREATE TABLE IF NOT EXISTS packages(id INTEGER PRIMARY KEY AUTOINCREMENT, game_id INTEGER,
    name TEXT, price INTEGER, api_id INTEGER, active INTEGER DEFAULT 1);
CREATE TABLE IF NOT EXISTS pay_methods(id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT,
    details TEXT, active INTEGER DEFAULT 1);
CREATE TABLE IF NOT EXISTS topups(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER,
    amount INTEGER, method TEXT, file_id TEXT, status TEXT DEFAULT 'pending');
CREATE TABLE IF NOT EXISTS orders(ref TEXT PRIMARY KEY, user_id INTEGER, game TEXT, package TEXT,
    price INTEGER, player_id TEXT, server_id TEXT, status TEXT, response TEXT);
""")
db.commit()

def q(sql, args=(), one=False):
    cur = db.execute(sql, args)
    db.commit()
    return cur.fetchone() if one else cur.fetchall()

def ex(sql, args=()):
    cur = db.execute(sql, args)
    db.commit()
    return cur.lastrowid

def get_s(key, default=""):
    r = q("SELECT value FROM settings WHERE key=?", (key,), True)
    return r["value"] if r else default

def set_s(key, value):
    ex("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))

def get_balance(uid):
    ex("INSERT OR IGNORE INTO users(id) VALUES(?)", (uid,))
    return q("SELECT balance FROM users WHERE id=?", (uid,), True)["balance"]

def add_balance(uid, amount):
    get_balance(uid)
    ex("UPDATE users SET balance=balance+? WHERE id=?", (amount, uid))

# --- birinchi ishga tushirishda boshlang'ich ma'lumotlar ---
if not get_s("welcome"):
    set_s("welcome", "Xush kelibsiz! O'yinni tanlang:")
if not get_s("api_url") and os.getenv("API_URL"):
    set_s("api_url", os.getenv("API_URL").rstrip("/"))
if not get_s("api_key") and os.getenv("API_KEY"):
    set_s("api_key", os.getenv("API_KEY"))
if not q("SELECT 1 FROM games LIMIT 1") and os.path.exists("products.json"):
    with open("products.json", encoding="utf-8") as f:
        for gname, g in json.load(f).items():
            gid = ex("INSERT INTO games(name,need_server) VALUES(?,?)", (gname, int(g.get("need_server", False))))
            for p in g["packages"]:
                ex("INSERT INTO packages(game_id,name,price,api_id) VALUES(?,?,?,?)",
                   (gid, p["name"], p["price"], p["package_id"]))

# ======================= Helpers =======================
def B(text, data): return InlineKeyboardButton(text=text, callback_data=data)
def KB(rows): return InlineKeyboardMarkup(inline_keyboard=rows)
def fmt(n): return f"{int(n):,}".replace(",", " ") + " so'm"
def num(text):
    t = (text or "").replace(" ", "").replace(",", "")
    return int(t) if t.isdigit() else None

async def edit(c: CallbackQuery, text, kb=None):
    try:
        await c.message.edit_text(text, reply_markup=kb)
    except TelegramBadRequest:
        pass
    await c.answer()

async def send_order(api_id, player_id, server_id, reference):
    url, key = get_s("api_url"), get_s("api_key")
    if not url or not key:
        return 0, {"error": "API manzil yoki kalit kiritilmagan"}
    payload = {"package_id": api_id, "player_id": player_id, "reference": reference}
    if server_id:
        payload["server_id"] = server_id
    headers = {"X-API-Key": key, "Content-Type": "application/json"}
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60)) as s:
            async with s.post(f"{url}/api/v1/order", json=payload, headers=headers) as r:
                text = await r.text()
                try:
                    data = json.loads(text)
                except Exception:
                    data = {"raw": text}
                return r.status, data
    except Exception as e:
        return 0, {"error": str(e)}

class IsAdmin(BaseFilter):
    async def __call__(self, event) -> bool:
        return event.from_user.id in ADMIN_IDS

class Buy(StatesGroup):
    player = State(); server = State()

class Topup(StatesGroup):
    amount = State(); receipt = State()

class Adm(StatesGroup):
    game_name = State(); game_server = State()
    pkg_name = State(); pkg_price = State(); pkg_api = State()
    edit_price = State(); edit_api = State()
    url = State(); key = State()
    pm_title = State(); pm_details = State()
    bc = State(); welcome = State(); bal = State()

user = Router()
admin = Router()
admin.message.filter(IsAdmin())
admin.callback_query.filter(IsAdmin())

# ======================= USER =======================
def main_kb():
    rows = [[B(g["name"], f"g:{g['id']}")] for g in q("SELECT * FROM games WHERE active=1")]
    rows.append([B("💰 Balans", "bal"), B("➕ Balans to'ldirish", "topup")])
    return KB(rows)

@user.message(CommandStart())
async def start(m: Message, state: FSMContext):
    await state.clear()
    get_balance(m.from_user.id)
    await m.answer(get_s("welcome"), reply_markup=main_kb())

@user.message(Command("balance"))
async def balance_cmd(m: Message):
    await m.answer(f"Balansingiz: {fmt(get_balance(m.from_user.id))}")

@user.callback_query(F.data == "bal")
async def bal(c: CallbackQuery):
    await c.answer(f"Balansingiz: {fmt(get_balance(c.from_user.id))}", show_alert=True)

@user.callback_query(F.data == "back")
async def back(c: CallbackQuery):
    await edit(c, get_s("welcome"), main_kb())

@user.callback_query(F.data.startswith("g:"))
async def pick_game(c: CallbackQuery):
    gid = int(c.data[2:])
    g = q("SELECT * FROM games WHERE id=?", (gid,), True)
    if not g:
        return await c.answer("Topilmadi", show_alert=True)
    pk = q("SELECT * FROM packages WHERE game_id=? AND active=1", (gid,))
    rows = [[B(f"{p['name']} — {fmt(p['price'])}", f"p:{p['id']}")] for p in pk]
    rows.append([B("⬅️ Orqaga", "back")])
    await edit(c, f"{g['name']} — paketni tanlang:" if pk else "Hozircha paketlar yo'q", KB(rows))

@user.callback_query(F.data.startswith("p:"))
async def pick_pkg(c: CallbackQuery, state: FSMContext):
    if get_s("maintenance") == "1":
        return await c.answer("🛠 Texnik ishlar ketmoqda, keyinroq urinib ko'ring", show_alert=True)
    await state.clear()
    await state.update_data(pid=int(c.data[2:]))
    await state.set_state(Buy.player)
    await c.message.answer("O'yinchi ID raqamini yuboring:")
    await c.answer()

def pkg_info(pid):
    return q("""SELECT p.*, g.name AS gname, g.need_server AS need_server FROM packages p
                JOIN games g ON g.id=p.game_id WHERE p.id=?""", (pid,), True)

@user.message(Buy.player)
async def got_player(m: Message, state: FSMContext):
    pid = (m.text or "").strip()
    if not pid.isdigit():
        return await m.answer("ID faqat raqamlardan iborat. Qayta yuboring:")
    await state.update_data(player_id=pid)
    d = await state.get_data()
    if pkg_info(d["pid"])["need_server"]:
        await state.set_state(Buy.server)
        return await m.answer("Server ID yuboring:")
    await confirm(m, state)

@user.message(Buy.server)
async def got_server(m: Message, state: FSMContext):
    sid = (m.text or "").strip()
    if not sid.isdigit():
        return await m.answer("Server ID faqat raqam. Qayta yuboring:")
    await state.update_data(server_id=sid)
    await confirm(m, state)

async def confirm(m: Message, state: FSMContext):
    d = await state.get_data()
    p = pkg_info(d["pid"])
    text = (f"🎮 {p['gname']}\n📦 {p['name']}\n💰 {fmt(p['price'])}\n🆔 ID: {d['player_id']}"
            + (f"\n🌐 Server: {d['server_id']}" if d.get("server_id") else "")
            + "\n\nTasdiqlaysizmi?")
    await m.answer(text, reply_markup=KB([[B("✅ Tasdiqlash", "ok"), B("❌ Bekor", "no")]]))

@user.callback_query(F.data == "no")
async def cancel(c: CallbackQuery, state: FSMContext):
    await state.clear()
    await edit(c, "Bekor qilindi. /start")

@user.callback_query(F.data == "ok")
async def do_order(c: CallbackQuery, state: FSMContext):
    d = await state.get_data()
    if not d or "player_id" not in d:
        return await c.answer("Sessiya tugagan, /start bosing", show_alert=True)
    await state.clear()
    if get_s("maintenance") == "1":
        return await edit(c, "🛠 Texnik ishlar ketmoqda, keyinroq urinib ko'ring.")
    uid = c.from_user.id
    p = pkg_info(d["pid"])
    if not p or not p["active"]:
        return await edit(c, "Bu paket mavjud emas.")
    if get_balance(uid) < p["price"]:
        return await edit(c, f"Balans yetarli emas. Balansingiz: {fmt(get_balance(uid))}\n/start → ➕ Balans to'ldirish")
    ref = f"o-{uid}-{uuid.uuid4().hex[:10]}"
    add_balance(uid, -p["price"])
    ex("INSERT INTO orders VALUES(?,?,?,?,?,?,?,?,?)",
       (ref, uid, p["gname"], p["name"], p["price"], d["player_id"], d.get("server_id"), "pending", ""))
    await edit(c, "⏳ Buyurtma yuborilmoqda...")
    status, data = await send_order(p["api_id"], d["player_id"], d.get("server_id"), ref)
    ok = 200 <= status < 300
    ex("UPDATE orders SET status=?, response=? WHERE ref=?",
       ("done" if ok else "failed", json.dumps(data, ensure_ascii=False), ref))
    if ok:
        await c.message.edit_text(f"✅ Buyurtma qabul qilindi!\n{p['gname']} — {p['name']}\nRaqam: {ref}")
    else:
        add_balance(uid, p["price"])
        await c.message.edit_text("❌ Buyurtma bajarilmadi, pul balansga qaytarildi.")
        for a in ADMIN_IDS:
            try:
                await c.bot.send_message(a, f"⚠️ Xato {ref}\nStatus: {status}\n{data}")
            except Exception:
                pass

# ---- balans to'ldirish (qo'lda tasdiqlanadigan to'lov) ----
@user.callback_query(F.data == "topup")
async def topup(c: CallbackQuery):
    pms = q("SELECT * FROM pay_methods WHERE active=1")
    if not pms:
        return await c.answer("To'lov usullari hozircha yo'q. Admin bilan bog'laning.", show_alert=True)
    rows = [[B(p["title"], f"tm:{p['id']}")] for p in pms] + [[B("⬅️ Orqaga", "back")]]
    await edit(c, "To'lov usulini tanlang:", KB(rows))

@user.callback_query(F.data.startswith("tm:"))
async def topup_method(c: CallbackQuery, state: FSMContext):
    pm = q("SELECT * FROM pay_methods WHERE id=?", (int(c.data[3:]),), True)
    if not pm:
        return await c.answer("Topilmadi", show_alert=True)
    await state.clear()
    await state.update_data(method=pm["title"])
    await state.set_state(Topup.amount)
    await c.message.answer(f"💳 {pm['title']}\n{pm['details']}\n\nNecha so'm to'ldirmoqchisiz? Summani yozing:")
    await c.answer()

@user.message(Topup.amount)
async def topup_amount(m: Message, state: FSMContext):
    a = num(m.text)
    if not a or a <= 0:
        return await m.answer("Summani raqamda yozing, masalan: 50000")
    await state.update_data(amount=a)
    await state.set_state(Topup.receipt)
    await m.answer(f"{fmt(a)} ni to'lang va to'lov chekining rasmini (skrinshot) yuboring:")

@user.message(Topup.receipt, F.photo)
async def topup_receipt(m: Message, state: FSMContext):
    d = await state.get_data()
    await state.clear()
    fid = m.photo[-1].file_id
    tid = ex("INSERT INTO topups(user_id,amount,method,file_id) VALUES(?,?,?,?)",
             (m.from_user.id, d["amount"], d["method"], fid))
    await m.answer("✅ Chek adminga yuborildi. Tasdiqlangach balansingiz to'ldiriladi.")
    cap = (f"💳 To'ldirish #{tid}\n👤 {m.from_user.full_name} (ID: {m.from_user.id})\n"
           f"💰 {fmt(d['amount'])}\n🏦 {d['method']}")
    kb = KB([[B("✅ Tasdiqlash", f"t:ok:{tid}"), B("❌ Rad etish", f"t:no:{tid}")]])
    for a in ADMIN_IDS:
        try:
            await m.bot.send_photo(a, fid, caption=cap, reply_markup=kb)
        except Exception:
            pass

@user.message(Topup.receipt)
async def topup_receipt_bad(m: Message):
    await m.answer("Iltimos, chek rasmini yuboring.")

# ======================= ADMIN =======================
def admin_menu():
    mt = "ON 🔴" if get_s("maintenance") == "1" else "OFF 🟢"
    return KB([
        [B("🎮 O'yinlar va paketlar", "a:games"), B("⚙️ API sozlamalari", "a:api")],
        [B("💳 To'lov usullari", "a:pm"), B("📊 Statistika", "a:stats")],
        [B("📋 Buyurtmalar", "a:orders"), B("📢 Xabar yuborish", "a:bc")],
        [B("💵 Balans qo'shish", "a:bal"), B("👋 Salomlashuv matni", "a:welcome")],
        [B(f"🛠 Texnik rejim: {mt}", "a:maint")],
    ])

@admin.message(Command("admin"))
async def admin_cmd(m: Message, state: FSMContext):
    await state.clear()
    await m.answer("🔧 Admin panel", reply_markup=admin_menu())

@admin.callback_query(F.data == "a:menu")
async def a_menu(c: CallbackQuery, state: FSMContext):
    await state.clear()
    await edit(c, "🔧 Admin panel", admin_menu())

@admin.callback_query(F.data == "a:maint")
async def a_maint(c: CallbackQuery):
    set_s("maintenance", "0" if get_s("maintenance") == "1" else "1")
    await edit(c, "🔧 Admin panel", admin_menu())

# ---- o'yinlar ----
@admin.callback_query(F.data == "a:games")
async def a_games(c: CallbackQuery, state: FSMContext):
    await state.clear()
    rows = [[B(f"{'✅' if g['active'] else '⛔'} {g['name']}", f"ag:{g['id']}")] for g in q("SELECT * FROM games")]
    rows += [[B("➕ O'yin qo'shish", "a:addgame")], [B("⬅️ Admin menyu", "a:menu")]]
    await edit(c, "🎮 O'yinlar:", KB(rows))

@admin.callback_query(F.data == "a:addgame")
async def a_addgame(c: CallbackQuery, state: FSMContext):
    await state.set_state(Adm.game_name)
    await c.message.answer("Yangi o'yin nomini yozing:")
    await c.answer()

@admin.message(Adm.game_name)
async def a_game_name(m: Message, state: FSMContext):
    await state.update_data(gname=m.text.strip())
    await state.set_state(Adm.game_server)
    await m.answer("Bu o'yinda Server ID kerakmi?", reply_markup=KB([[B("Ha", "ngs:1"), B("Yo'q", "ngs:0")]]))

@admin.callback_query(F.data.startswith("ngs:"), Adm.game_server)
async def a_game_server(c: CallbackQuery, state: FSMContext):
    d = await state.get_data()
    gid = ex("INSERT INTO games(name,need_server) VALUES(?,?)", (d["gname"], int(c.data[4:])))
    await state.clear()
    await edit(c, f"✅ '{d['gname']}' qo'shildi. Endi paket qo'shing:",
               KB([[B("➕ Paket qo'shish", f"apn:{gid}")], [B("⬅️ O'yinlar", "a:games")]]))

async def game_menu(c: CallbackQuery, gid: int):
    g = q("SELECT * FROM games WHERE id=?", (gid,), True)
    if not g:
        return await edit(c, "O'yin topilmadi", KB([[B("⬅️ O'yinlar", "a:games")]]))
    rows = [[B(f"{'✅' if p['active'] else '⛔'} {p['name']} — {fmt(p['price'])}", f"ap:{p['id']}")]
            for p in q("SELECT * FROM packages WHERE game_id=?", (gid,))]
    rows += [
        [B("➕ Paket qo'shish", f"apn:{gid}")],
        [B(f"🌐 Server ID: {'kerak' if g['need_server'] else 'kerak emas'}", f"ags:{gid}")],
        [B("🔁 Yoqish/o'chirish", f"agt:{gid}"), B("🗑 O'yinni o'chirish", f"agd:{gid}")],
        [B("⬅️ O'yinlar", "a:games")],
    ]
    await edit(c, f"🎮 {g['name']} ({'faol' if g['active'] else 'o‘chirilgan'})", KB(rows))

@admin.callback_query(F.data.startswith("ag:"))
async def ag(c: CallbackQuery):
    await game_menu(c, int(c.data[3:]))

@admin.callback_query(F.data.startswith("ags:"))
async def ags(c: CallbackQuery):
    gid = int(c.data[4:])
    ex("UPDATE games SET need_server=1-need_server WHERE id=?", (gid,))
    await game_menu(c, gid)

@admin.callback_query(F.data.startswith("agt:"))
async def agt(c: CallbackQuery):
    gid = int(c.data[4:])
    ex("UPDATE games SET active=1-active WHERE id=?", (gid,))
    await game_menu(c, gid)

@admin.callback_query(F.data.startswith("agd:"))
async def agd(c: CallbackQuery):
    gid = int(c.data[4:])
    ex("DELETE FROM packages WHERE game_id=?", (gid,))
    ex("DELETE FROM games WHERE id=?", (gid,))
    rows = [[B(f"{'✅' if g['active'] else '⛔'} {g['name']}", f"ag:{g['id']}")] for g in q("SELECT * FROM games")]
    rows += [[B("➕ O'yin qo'shish", "a:addgame")], [B("⬅️ Admin menyu", "a:menu")]]
    await edit(c, "🗑 O'chirildi. O'yinlar:", KB(rows))

# ---- paketlar ----
@admin.callback_query(F.data.startswith("apn:"))
async def apn(c: CallbackQuery, state: FSMContext):
    await state.update_data(gid=int(c.data[4:]))
    await state.set_state(Adm.pkg_name)
    await c.message.answer("Paket nomini yozing (masalan: 86 Diamonds):")
    await c.answer()

@admin.message(Adm.pkg_name)
async def pkg_name(m: Message, state: FSMContext):
    await state.update_data(pname=m.text.strip())
    await state.set_state(Adm.pkg_price)
    await m.answer("Sotuv narxini yozing (so'm):")

@admin.message(Adm.pkg_price)
async def pkg_price(m: Message, state: FSMContext):
    n = num(m.text)
    if not n:
        return await m.answer("Narxni raqamda yozing:")
    await state.update_data(price=n)
    await state.set_state(Adm.pkg_api)
    await m.answer("Provayderdagi package_id ni yozing:")

@admin.message(Adm.pkg_api)
async def pkg_api(m: Message, state: FSMContext):
    n = num(m.text)
    if n is None:
        return await m.answer("package_id raqam bo'lishi kerak:")
    d = await state.get_data()
    ex("INSERT INTO packages(game_id,name,price,api_id) VALUES(?,?,?,?)", (d["gid"], d["pname"], d["price"], n))
    await state.clear()
    await m.answer(f"✅ Paket qo'shildi: {d['pname']} — {fmt(d['price'])}",
                   reply_markup=KB([[B("⬅️ O'yinga qaytish", f"ag:{d['gid']}")]]))

async def pkg_menu(c: CallbackQuery, pid: int):
    p = q("SELECT * FROM packages WHERE id=?", (pid,), True)
    if not p:
        return await edit(c, "Paket topilmadi", KB([[B("⬅️ O'yinlar", "a:games")]]))
    await edit(c, f"📦 {p['name']}\n💰 Narx: {fmt(p['price'])}\n🔑 Provayder package_id: {p['api_id']}\nHolat: {'faol' if p['active'] else 'o‘chirilgan'}",
               KB([[B("✏️ Narxni o'zgartirish", f"ape:{pid}")],
                   [B("🔢 package_id ni o'zgartirish", f"app:{pid}")],
                   [B("🔁 Yoqish/o'chirish", f"apt:{pid}"), B("🗑 O'chirish", f"apd:{pid}")],
                   [B("⬅️ Orqaga", f"ag:{p['game_id']}")]]))

@admin.callback_query(F.data.startswith("ap:"))
async def ap(c: CallbackQuery):
    await pkg_menu(c, int(c.data[3:]))

@admin.callback_query(F.data.startswith("apt:"))
async def apt(c: CallbackQuery):
    pid = int(c.data[4:])
    ex("UPDATE packages SET active=1-active WHERE id=?", (pid,))
    await pkg_menu(c, pid)

@admin.callback_query(F.data.startswith("apd:"))
async def apd(c: CallbackQuery):
    p = q("SELECT game_id FROM packages WHERE id=?", (int(c.data[4:]),), True)
    ex("DELETE FROM packages WHERE id=?", (int(c.data[4:]),))
    await game_menu(c, p["game_id"]) if p else await c.answer()

@admin.callback_query(F.data.startswith("ape:"))
async def ape(c: CallbackQuery, state: FSMContext):
    await state.update_data(pid=int(c.data[4:]))
    await state.set_state(Adm.edit_price)
    await c.message.answer("Yangi narxni yozing (so'm):")
    await c.answer()

@admin.message(Adm.edit_price)
async def edit_price(m: Message, state: FSMContext):
    n = num(m.text)
    if not n:
        return await m.answer("Narxni raqamda yozing:")
    d = await state.get_data()
    ex("UPDATE packages SET price=? WHERE id=?", (n, d["pid"]))
    await state.clear()
    await m.answer(f"✅ Yangi narx: {fmt(n)}", reply_markup=KB([[B("⬅️ Paketga qaytish", f"ap:{d['pid']}")]]))

@admin.callback_query(F.data.startswith("app:"))
async def app_(c: CallbackQuery, state: FSMContext):
    await state.update_data(pid=int(c.data[4:]))
    await state.set_state(Adm.edit_api)
    await c.message.answer("Yangi provayder package_id ni yozing:")
    await c.answer()

@admin.message(Adm.edit_api)
async def edit_api(m: Message, state: FSMContext):
    n = num(m.text)
    if n is None:
        return await m.answer("Raqam yozing:")
    d = await state.get_data()
    ex("UPDATE packages SET api_id=? WHERE id=?", (n, d["pid"]))
    await state.clear()
    await m.answer("✅ Saqlandi", reply_markup=KB([[B("⬅️ Paketga qaytish", f"ap:{d['pid']}")]]))

# ---- API sozlamalari ----
async def api_screen(c: CallbackQuery):
    key = get_s("api_key")
    masked = (key[:4] + "…" + key[-3:]) if len(key) > 8 else ("•••" if key else "kiritilmagan")
    await edit(c, f"⚙️ API sozlamalari\n\n🌐 Manzil: {get_s('api_url') or 'kiritilmagan'}\n🔑 Kalit: {masked}",
               KB([[B("🌐 Manzilni o'zgartirish", "a:url")], [B("🔑 Kalitni o'zgartirish", "a:key")],
                   [B("🧪 Ulanishni tekshirish", "a:test")], [B("⬅️ Admin menyu", "a:menu")]]))

@admin.callback_query(F.data == "a:api")
async def a_api(c: CallbackQuery, state: FSMContext):
    await state.clear()
    await api_screen(c)

@admin.callback_query(F.data == "a:url")
async def a_url(c: CallbackQuery, state: FSMContext):
    await state.set_state(Adm.url)
    await c.message.answer("API manzilini yozing (masalan: https://ludovex.org):")
    await c.answer()

@admin.message(Adm.url)
async def set_url(m: Message, state: FSMContext):
    u = m.text.strip().rstrip("/")
    if not u.startswith("http"):
        return await m.answer("Manzil http:// yoki https:// bilan boshlanishi kerak:")
    set_s("api_url", u)
    await state.clear()
    await m.answer("✅ Manzil saqlandi", reply_markup=KB([[B("⚙️ API sozlamalari", "a:api")]]))

@admin.callback_query(F.data == "a:key")
async def a_key(c: CallbackQuery, state: FSMContext):
    await state.set_state(Adm.key)
    await c.message.answer("API kalitni yuboring (xavfsizlik uchun xabar o'chiriladi):")
    await c.answer()

@admin.message(Adm.key)
async def set_key(m: Message, state: FSMContext):
    set_s("api_key", m.text.strip())
    await state.clear()
    try:
        await m.delete()
    except Exception:
        pass
    await m.answer("✅ Kalit saqlandi", reply_markup=KB([[B("⚙️ API sozlamalari", "a:api")]]))

@admin.callback_query(F.data == "a:test")
async def a_test(c: CallbackQuery):
    url = get_s("api_url")
    if not url:
        return await c.answer("Avval manzilni kiriting", show_alert=True)
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as s:
            async with s.get(url, headers={"X-API-Key": get_s("api_key")}) as r:
                msg = f"Server javob berdi: HTTP {r.status}"
    except Exception as e:
        msg = f"Ulanib bo'lmadi: {e}"
    await c.answer(msg[:190], show_alert=True)

# ---- to'lov usullari ----
async def pm_screen(c: CallbackQuery):
    rows = [[B(f"{'✅' if p['active'] else '⛔'} {p['title']}", f"pm:{p['id']}")] for p in q("SELECT * FROM pay_methods")]
    rows += [[B("➕ To'lov usuli qo'shish", "a:addpm")], [B("⬅️ Admin menyu", "a:menu")]]
    await edit(c, "💳 To'lov usullari:", KB(rows))

@admin.callback_query(F.data == "a:pm")
async def a_pm(c: CallbackQuery, state: FSMContext):
    await state.clear()
    await pm_screen(c)

@admin.callback_query(F.data == "a:addpm")
async def a_addpm(c: CallbackQuery, state: FSMContext):
    await state.set_state(Adm.pm_title)
    await c.message.answer("To'lov usuli nomi (masalan: Uzcard, Humo, Click):")
    await c.answer()

@admin.message(Adm.pm_title)
async def pm_title(m: Message, state: FSMContext):
    await state.update_data(title=m.text.strip())
    await state.set_state(Adm.pm_details)
    await m.answer("Rekvizitlarni yozing (karta raqami, egasi ismi va h.k.):")

@admin.message(Adm.pm_details)
async def pm_details(m: Message, state: FSMContext):
    d = await state.get_data()
    ex("INSERT INTO pay_methods(title,details) VALUES(?,?)", (d["title"], m.text.strip()))
    await state.clear()
    await m.answer("✅ To'lov usuli qo'shildi", reply_markup=KB([[B("💳 To'lov usullari", "a:pm")]]))

async def pm_view(c: CallbackQuery, pid: int):
    p = q("SELECT * FROM pay_methods WHERE id=?", (pid,), True)
    if not p:
        return await pm_screen(c)
    await edit(c, f"💳 {p['title']}\n{p['details']}\nHolat: {'faol' if p['active'] else 'o‘chirilgan'}",
               KB([[B("🔁 Yoqish/o'chirish", f"pmt:{pid}"), B("🗑 O'chirish", f"pmd:{pid}")],
                   [B("⬅️ Orqaga", "a:pm")]]))

@admin.callback_query(F.data.startswith("pm:"))
async def pm_open(c: CallbackQuery):
    await pm_view(c, int(c.data[3:]))

@admin.callback_query(F.data.startswith("pmt:"))
async def pmt(c: CallbackQuery):
    pid = int(c.data[4:])
    ex("UPDATE pay_methods SET active=1-active WHERE id=?", (pid,))
    await pm_view(c, pid)

@admin.callback_query(F.data.startswith("pmd:"))
async def pmd(c: CallbackQuery):
    ex("DELETE FROM pay_methods WHERE id=?", (int(c.data[4:]),))
    await pm_screen(c)

# ---- to'ldirishni tasdiqlash ----
@admin.callback_query(F.data.startswith("t:"))
async def t_decide(c: CallbackQuery):
    _, act, tid = c.data.split(":")
    t = q("SELECT * FROM topups WHERE id=?", (int(tid),), True)
    if not t or t["status"] != "pending":
        return await c.answer("Bu so'rov allaqachon ko'rib chiqilgan", show_alert=True)
    ok = act == "ok"
    ex("UPDATE topups SET status=? WHERE id=?", ("approved" if ok else "rejected", int(tid)))
    if ok:
        add_balance(t["user_id"], t["amount"])
    try:
        await c.bot.send_message(t["user_id"],
            f"✅ Balansingiz {fmt(t['amount'])}ga to'ldirildi." if ok else "❌ To'lovingiz tasdiqlanmadi. Admin bilan bog'laning.")
    except Exception:
        pass
    try:
        await c.message.edit_caption(caption=(c.message.caption or "") + f"\n\n{'✅ Tasdiqlandi' if ok else '❌ Rad etildi'}")
    except Exception:
        pass
    await c.answer()

# ---- statistika, buyurtmalar ----
@admin.callback_query(F.data == "a:stats")
async def a_stats(c: CallbackQuery):
    users = q("SELECT COUNT(*) n FROM users", one=True)["n"]
    done = q("SELECT COUNT(*) n, COALESCE(SUM(price),0) s FROM orders WHERE status='done'", one=True)
    fail = q("SELECT COUNT(*) n FROM orders WHERE status='failed'", one=True)["n"]
    pend = q("SELECT COUNT(*) n FROM topups WHERE status='pending'", one=True)["n"]
    await edit(c, f"📊 Statistika\n\n👥 Foydalanuvchilar: {users}\n✅ Muvaffaqiyatli buyurtmalar: {done['n']}\n"
                  f"💰 Savdo summasi: {fmt(done['s'])}\n❌ Xato buyurtmalar: {fail}\n⏳ Kutilayotgan to'ldirishlar: {pend}",
               KB([[B("⬅️ Admin menyu", "a:menu")]]))

@admin.callback_query(F.data == "a:orders")
async def a_orders(c: CallbackQuery):
    rows = q("SELECT * FROM orders ORDER BY rowid DESC LIMIT 10")
    icon = {"done": "✅", "failed": "❌", "pending": "⏳"}
    text = "📋 Oxirgi 10 buyurtma:\n\n" + ("\n".join(
        f"{icon.get(o['status'], '')} {o['game']} {o['package']} | ID {o['player_id']} | user {o['user_id']}" for o in rows) or "Hali yo'q")
    await edit(c, text, KB([[B("⬅️ Admin menyu", "a:menu")]]))

# ---- balans qo'shish, xabar, salomlashuv ----
@admin.callback_query(F.data == "a:bal")
async def a_bal(c: CallbackQuery, state: FSMContext):
    await state.set_state(Adm.bal)
    await c.message.answer("Yozing: USER_ID SUMMA (masalan: 123456789 50000). Ayirish uchun minus: 123456789 -10000")
    await c.answer()

@admin.message(Adm.bal)
async def bal_do(m: Message, state: FSMContext):
    try:
        uid, amount = m.text.split()
        uid, amount = int(uid), int(amount)
    except Exception:
        return await m.answer("Format: USER_ID SUMMA")
    add_balance(uid, amount)
    await state.clear()
    await m.answer(f"✅ Yangi balans: {fmt(get_balance(uid))}")
    try:
        await m.bot.send_message(uid, f"Balansingiz {fmt(amount)}ga o'zgartirildi.")
    except Exception:
        pass

@admin.callback_query(F.data == "a:bc")
async def a_bc(c: CallbackQuery, state: FSMContext):
    await state.set_state(Adm.bc)
    await c.message.answer("Barcha foydalanuvchilarga yuboriladigan xabar matnini yozing:")
    await c.answer()

@admin.message(Adm.bc)
async def bc_do(m: Message, state: FSMContext):
    await state.clear()
    sent = 0
    for u in q("SELECT id FROM users"):
        try:
            await m.bot.send_message(u["id"], m.text)
            sent += 1
        except Exception:
            pass
        await asyncio.sleep(0.05)
    await m.answer(f"📢 {sent} ta foydalanuvchiga yuborildi.")

@admin.callback_query(F.data == "a:welcome")
async def a_welcome(c: CallbackQuery, state: FSMContext):
    await state.set_state(Adm.welcome)
    await c.message.answer("Yangi salomlashuv matnini yozing:")
    await c.answer()

@admin.message(Adm.welcome)
async def welcome_do(m: Message, state: FSMContext):
    set_s("welcome", m.text)
    await state.clear()
    await m.answer("✅ Saqlandi")

# ======================= RUN =======================
async def main():
    logging.basicConfig(level=logging.INFO)
    bot = Bot(BOT_TOKEN)
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(admin)
    dp.include_router(user)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
