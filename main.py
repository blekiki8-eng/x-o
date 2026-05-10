import os, uuid, logging, asyncio, time
from aiogram import Bot, Dispatcher, executor, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

# --- НАЛАШТУВАННЯ ---
load_dotenv()
API_TOKEN = os.getenv("BOT_TOKEN")
MONGO_URL = os.getenv("MONGO_URL")
ADMIN_ID = int(os.getenv("ADMIN_ID")) if os.getenv("ADMIN_ID") else 0

EXCHANGE_RATE = 44.50
logging.basicConfig(level=logging.INFO)
storage = MemoryStorage()
bot = Bot(token=API_TOKEN)
dp = Dispatcher(bot, storage=storage)

cluster = AsyncIOMotorClient(MONGO_URL)
db = cluster["game_database"]
users_col, games_col = db["users"], db["games"]

# --- СТАНИ FSM ---
class DepositState(StatesGroup):
    wait_amount = State()
    wait_receipt = State()

class GameState(StatesGroup):
    wait_bet = State()
    wait_size = State()

# --- КЛАВІАТУРИ ---
def main_menu(user_id):
    markup = ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add(KeyboardButton("👤 Профіль"), KeyboardButton("🎮 Ігри"))
    markup.add(KeyboardButton("💎 Баланс"))
    if user_id == ADMIN_ID:
        markup.add(KeyboardButton("🛡 Панель адміна"))
    return markup

def cancel_kb():
    return ReplyKeyboardMarkup(resize_keyboard=True).add("❌ Скасувати")

# --- ДОПОМІЖНІ ФУНКЦІЇ ---
async def get_u(m):
    uid = m.from_user.id
    u = await users_col.find_one({"_id": uid})
    if not u:
        u = {"_id": uid, "nickname": m.from_user.full_name, "balance": 0.0, "banned": False}
        await users_col.insert_one(u)
    return u

def get_board_markup(game_id, board, size):
    markup = InlineKeyboardMarkup(row_width=size)
    btns = [InlineKeyboardButton(board[i] if board[i] != " " else "⬜️", callback_data=f"st:{game_id}:{i}") for i in range(size*size)]
    return markup.add(*btns)

def check_win(b, s):
    lines = []
    for i in range(s):
        lines.append(list(range(i*s, (i+1)*s))) # Горизонталі
        lines.append(list(range(i, s*s, s)))     # Вертикалі
    lines.append(list(range(0, s*s, s+1)))       # Діагональ 1
    lines.append(list(range(s-1, s*s-1, s-1)))   # Діагональ 2
    for r in lines:
        if all(b[r[i]] == b[r[0]] != " " for i in range(s)): return b[r[0]]
    return "draw" if " " not in b else None

# --- ОБРОБНИКИ КОМАНД ---
@dp.message_handler(commands=['start'], state="*")
async def start(m: types.Message, state: FSMContext):
    await state.finish()
    u = await get_u(m)
    
    args = m.get_args()
    if args and args.startswith("game_"):
        gid = args.split("_")[1]
        g = await games_col.find_one({"game_id": gid, "status": "waiting"})
        if g and g['creator_id'] != m.from_user.id:
            if u['balance'] >= g['bet']:
                await users_col.update_one({"_id": m.from_user.id}, {"$inc": {"balance": -g['bet']}})
                m_op = await m.answer("Приєднуємось...", reply_markup=get_board_markup(gid, g['board'], g['size']))
                m_cr = await bot.send_message(g['creator_id'], "Суперник знайдений!", reply_markup=get_board_markup(gid, g['board'], g['size']))
                upd = {"opponent_id": m.from_user.id, "opponent_msg_id": m_op.message_id, "creator_msg_id": m_cr.message_id, "status": "playing", "turn": g['creator_id'], "last_move": time.time()}
                await games_col.update_one({"game_id": gid}, {"$set": upd})
                return
    await m.answer(f"Вітаємо, {u['nickname']}!", reply_markup=main_menu(m.from_user.id))

@dp.message_handler(lambda m: m.text == "👤 Профіль", state="*")
async def profile(m: types.Message):
    u = await get_u(m)
    await m.answer(f"👤 **Профіль**\n\n📝 Нік: {u.get('nickname')}\n🆔 ID: `{m.from_user.id}`\n💰 Баланс: {u.get('balance', 0.0)} 💎", parse_mode="Markdown")

# --- АДМІН-ПАНЕЛЬ ---
@dp.message_handler(lambda m: m.text == "🛡 Панель адміна", state="*")
async def admin_panel(m: types.Message):
    if m.from_user.id != ADMIN_ID: return
    kb = InlineKeyboardMarkup().add(InlineKeyboardButton("👥 Список гравців", callback_data="alist"))
    await m.answer("🛡 Адмін-панель", reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data == "alist", state="*")
async def admin_list(c: types.CallbackQuery):
    users = await users_col.find().to_list(100)
    kb = InlineKeyboardMarkup(row_width=1)
    for u in users:
        name = u.get('nickname', 'Гість')[:15]
        kb.add(InlineKeyboardButton(f"{name} | {u.get('balance', 0)} 💎", callback_data=f"u:{u['_id']}"))
    await c.message.edit_text("Список гравців:", reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data.startswith(("u:", "res:", "ban:")), state="*")
async def admin_actions(c: types.CallbackQuery):
    if c.from_user.id != ADMIN_ID: return
    act, uid = c.data.split(":")
    uid = int(uid)
    if act == "u":
        u = await users_col.find_one({"_id": uid})
        kb = InlineKeyboardMarkup().add(
            InlineKeyboardButton("💰 Обнулити", callback_data=f"res:{uid}"),
            InlineKeyboardButton("🚫 Бан", callback_data=f"ban:{uid}"),
            InlineKeyboardButton("⬅️ Назад", callback_data="alist")
        )
        await c.message.edit_text(f"Гравець: {u['nickname']}\nБаланс: {u['balance']} 💎", reply_markup=kb)
    elif act == "res":
        await users_col.update_one({"_id": uid}, {"$set": {"balance": 0.0}})
        await c.answer("Баланс обнулено")
        await admin_list(c)
    elif act == "ban":
        u = await users_col.find_one({"_id": uid})
        await users_col.update_one({"_id": uid}, {"$set": {"banned": not u.get("banned", False)}})
        await c.answer("Статус змінено")
        await admin_list(c)

# --- БАЛАНС ТА ПОПОВНЕННЯ ---
@dp.message_handler(lambda m: m.text == "💎 Баланс", state="*")
async def balance(m: types.Message):
    u = await get_u(m)
    kb = InlineKeyboardMarkup().add(InlineKeyboardButton("💳 Поповнити", callback_data="dep"))
    await m.answer(f"💰 Баланс: {u.get('balance', 0.0)} 💎", reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data == "dep", state="*")
async def dep_start(c: types.CallbackQuery):
    await DepositState.wait_amount.set()
    await bot.send_message(c.from_user.id, "Введіть суму в 💎:", reply_markup=cancel_kb())

@dp.message_handler(state=DepositState.wait_amount)
async def dep_amt(m: types.Message, state: FSMContext):
    try:
        amt = float(m.text)
        await state.update_data(a=amt)
        await m.answer(f"Сплатіть {round(amt*EXCHANGE_RATE, 2)} грн на `5355 2800 2890 2177` та надішліть фото чека.")
        await DepositState.wait_receipt.set()
    except: await m.answer("Введіть число!")

@dp.message_handler(state=DepositState.wait_receipt, content_types=['photo'])
async def dep_rec(m: types.Message, state: FSMContext):
    data = await state.get_data()
    kb = InlineKeyboardMarkup().add(
        InlineKeyboardButton("✅", callback_data=f"dok:{m.from_user.id}:{data['a']}"),
        InlineKeyboardButton("❌", callback_data=f"dno:{m.from_user.id}")
    )
    await bot.send_photo(ADMIN_ID, m.photo[-1].file_id, caption=f"Депозит {data['a']} 💎 від {m.from_user.full_name}", reply_markup=kb)
    await m.answer("Очікуйте підтвердження.")
    await state.finish()

@dp.callback_query_handler(lambda c: c.data.startswith(("dok:", "dno:")), state="*")
async def admin_dep_res(c: types.CallbackQuery):
    p = c.data.split(":")
    uid = int(p[1])
    if p[0] == "dok":
        await users_col.update_one({"_id": uid}, {"$inc": {"balance": float(p[2])}})
        await bot.send_message(uid, "✅ Поповнено!")
    await c.message.delete()

# --- ІГРИ ---
@dp.message_handler(lambda m: m.text == "🎮 Ігри", state="*")
async def games_menu(m: types.Message):
    kb = InlineKeyboardMarkup().add(InlineKeyboardButton("❌ Хрестики-Нолики ⭕️", callback_data="gtic"))
    await m.answer("Оберіть гру:", reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data == "gtic", state="*")
async def tic_bet(c: types.CallbackQuery):
    await GameState.wait_bet.set()
    await bot.send_message(c.from_user.id, "Введіть ставку 💎:", reply_markup=cancel_kb())

@dp.message_handler(state=GameState.wait_bet)
async def tic_size(m: types.Message, state: FSMContext):
    try:
        bet = float(m.text)
        u = await get_u(m)
        if u['balance'] < bet: return await m.answer("Мало 💎")
        await state.update_data(b=bet)
        kb = InlineKeyboardMarkup().add(InlineKeyboardButton("3x3", callback_data="sz:3"), InlineKeyboardButton("4x4", callback_data="sz:4"))
        await m.answer("Розмір поля:", reply_markup=kb)
        await GameState.wait_size.set()
    except: await m.answer("Число!")

@dp.callback_query_handler(state=GameState.wait_size)
async def tic_create(c: types.CallbackQuery, state: FSMContext):
    size = int(c.data.split(":")[1])
    d = await state.get_data()
    gid = str(uuid.uuid4())[:8]
    await users_col.update_one({"_id": c.from_user.id}, {"$inc": {"balance": -d['b']}})
    await games_col.insert_one({"game_id": gid, "creator_id": c.from_user.id, "bet": d['b'], "size": size, "board": [" "]*(size*size), "status": "waiting"})
    link = f"https://t.me/{(await bot.get_me()).username}?start=game_{gid}"
    await bot.send_message(c.from_user.id, f"Гра створена!\nПосилання: {link}", reply_markup=main_menu(c.from_user.id))
    await state.finish()

@dp.message_handler(state="*", text="❌ Скасувати")
async def cancl(m: types.Message, state: FSMContext):
    await state.finish()
    await m.answer("Скасовано", reply_markup=main_menu(m.from_user.id))

if __name__ == '__main__':
    executor.start_polling(dp, skip_updates=True)
