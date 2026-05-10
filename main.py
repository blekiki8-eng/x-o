import os, uuid, logging, asyncio
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

RATE = 44.50
logging.basicConfig(level=logging.INFO)
storage = MemoryStorage()
bot = Bot(token=API_TOKEN)
dp = Dispatcher(bot, storage=storage)

cluster = AsyncIOMotorClient(MONGO_URL)
db = cluster["game_bot_db"]
users_col = db["users"]
games_col = db["games"]

# --- СТАНИ ---
class DepositState(StatesGroup):
    wait_amount = State()
    wait_receipt = State()

class GameState(StatesGroup):
    wait_game_type = State()
    wait_size = State()
    wait_bet = State()

# --- КЛАВІАТУРИ ---
def main_menu(user_id):
    markup = ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add(KeyboardButton("👤 Профіль"), KeyboardButton("🎮 Ігри"))
    markup.add(KeyboardButton("💎 Баланс"))
    if user_id == ADMIN_ID:
        markup.add(KeyboardButton("🛡 Панель адміна"))
    return markup

def cancel_kb():
    return ReplyKeyboardMarkup(resize_keyboard=True).add(KeyboardButton("❌ Скасувати"))

def get_board_markup(game_id, board, size):
    markup = InlineKeyboardMarkup(row_width=size)
    btns = []
    for i in range(size*size):
        char = board[i] if board[i] != " " else "⬜️"
        btns.append(InlineKeyboardButton(char, callback_data=f"st:{game_id}:{i}"))
    return markup.add(*btns)

# --- ДОПОМІЖНІ ФУНКЦІЇ ---
async def get_u(m):
    uid = m.from_user.id
    u = await users_col.find_one({"_id": uid})
    if not u:
        u = {"_id": uid, "nickname": m.from_user.full_name, "balance": 0.0}
        await users_col.insert_one(u)
    return u

def check_win(b, s):
    lines = []
    for i in range(s):
        lines.append(list(range(i*s, (i+1)*s)))
        lines.append(list(range(i, s*s, s)))
    if s == 3:
        lines.append([0, 4, 8]); lines.append([2, 4, 6])
    else:
        lines.append([0, 5, 10, 15]); lines.append([3, 6, 9, 12])
    for r in lines:
        if all(b[r[i]] == b[r[0]] != " " for i in range(len(r))): return b[r[0]]
    return "draw" if " " not in b else None

# --- КОМАНДА СТАРТ (ОБРОБКА ПОСИЛАНЬ) ---
@dp.message_handler(commands=['start'], state="*")
async def start(m: types.Message, state: FSMContext):
    await state.finish()
    u = await get_u(m)
    args = m.get_args()
    
    if args and args.startswith("game_"):
        gid = args.replace("game_", "")
        g = await games_col.find_one({"game_id": gid, "status": "waiting"})
        if not g:
            return await m.answer("❌ Гра не знайдена.", reply_markup=main_menu(m.from_user.id))
        
        if g['creator_id'] == m.from_user.id:
            return await m.answer("⏳ Очікуйте суперника...")

        if u['balance'] < g['bet']:
            await users_col.update_one({"_id": g['creator_id']}, {"$inc": {"balance": g['bet']}})
            await games_col.delete_one({"game_id": gid})
            await bot.send_message(g['creator_id'], f"❌ У суперника не вистачає коштів, {g['bet']} 💎 повернуто.")
            return await m.answer("❌ У вас недостатньо коштів!")

        await users_col.update_one({"_id": m.from_user.id}, {"$inc": {"balance": -g['bet']}})
        await games_col.update_one({"game_id": gid}, {"$set": {"opponent_id": m.from_user.id, "status": "playing", "turn": g['creator_id']}})
        
        kb = get_board_markup(gid, g['board'], g['size'])
        await bot.send_message(g['creator_id'], "🎮 Гра почалася! Твій хід (❌)", reply_markup=kb)
        await m.answer("🎮 Гра почалася! Хід суперника (ти ⭕️)", reply_markup=kb)
        return

    await m.answer(f"Вітаємо, {u['nickname']}!", reply_markup=main_menu(m.from_user.id))

# --- ПРОФІЛЬ ТА БАЛАНС ---
@dp.message_handler(lambda m: m.text == "👤 Профіль", state="*")
async def profile(m: types.Message, state: FSMContext):
    await state.finish()
    u = await get_u(m)
    await m.answer(f"👤 **Профіль**\n\nID: `{m.from_user.id}`\nБаланс: {u['balance']} 💎", parse_mode="Markdown")

@dp.message_handler(lambda m: m.text == "💎 Баланс", state="*")
async def balance_m(m: types.Message, state: FSMContext):
    await state.finish()
    u = await get_u(m)
    kb = InlineKeyboardMarkup().add(InlineKeyboardButton("💳 Поповнити", callback_data="dep"))
    await m.answer(f"💰 Баланс: {u['balance']} 💎", reply_markup=kb)

# --- АДМІН ПАНЕЛЬ ---
@dp.message_handler(lambda m: m.text == "🛡 Панель адміна", state="*")
async def admin_p(m: types.Message, state: FSMContext):
    if m.from_user.id != ADMIN_ID: return
    await state.finish()
    users_count = await users_col.count_documents({})
    active_games = await games_col.count_documents({"status": "playing"})
    await m.answer(f"🛡 **Адмін-панель**\n\nКористувачів: {users_count}\nІгор зараз: {active_games}", parse_mode="Markdown")

# --- ПОПОВНЕННЯ (АДМІН ПРИЙМАЄ КВИТАНЦІЇ) ---
@dp.callback_query_handler(lambda c: c.data == "dep", state="*")
async def dep_start(c: types.CallbackQuery):
    await DepositState.wait_amount.set()
    await bot.send_message(c.from_user.id, "Введіть суму (💎):", reply_markup=cancel_kb())

@dp.message_handler(state=DepositState.wait_amount)
async def dep_amt(m: types.Message, state: FSMContext):
    if m.text == "❌ Скасувати":
        await state.finish()
        return await m.answer("Скасовано", reply_markup=main_menu(m.from_user.id))
    try:
        amt = float(m.text)
        uah = round(amt * RATE * 1.05, 2)
        await state.update_data(a=amt)
        await m.answer(f"Оплатіть {uah}₴ на картку:\n`5355 2800 2890 2177`\n\nНадішліть ФОТО квитанції:", parse_mode="Markdown")
        await DepositState.wait_receipt.set()
    except: await m.answer("Числом, будь ласка")

@dp.message_handler(state=DepositState.wait_receipt, content_types=['photo'])
async def dep_rec(m: types.Message, state: FSMContext):
    data = await state.get_data()
    kb = InlineKeyboardMarkup().add(
        InlineKeyboardButton("✅ Схвалити", callback_data=f"p_ok:{m.from_user.id}:{data['a']}"),
        InlineKeyboardButton("❌ Відхилити", callback_data=f"p_no:{m.from_user.id}")
    )
    await bot.send_photo(ADMIN_ID, m.photo[-1].file_id, caption=f"💰 Депозит: {data['a']} 💎\nВід: {m.from_user.id}", reply_markup=kb)
    await m.answer("✅ Квитанцію надіслано адміну!", reply_markup=main_menu(m.from_user.id))
    await state.finish()

@dp.callback_query_handler(lambda c: c.data.startswith(("p_ok:", "p_no:")), state="*")
async def adm_decide(c: types.CallbackQuery):
    if c.from_user.id != ADMIN_ID: return
    p = c.data.split(":")
    uid = int(p[1])
    if p[0] == "p_ok":
        amt = float(p[2])
        await users_col.update_one({"_id": uid}, {"$inc": {"balance": amt}})
        await bot.send_message(uid, f"✅ Ваш баланс поповнено на {amt} 💎!")
        await c.answer("Схвалено")
    else:
        await bot.send_message(uid, "❌ Ваш депозит було відхилено.")
        await c.answer("Відхилено")
    await c.message.delete()

# --- ЛОГІКА ГРИ ---
@dp.message_handler(lambda m: m.text == "🎮 Ігри", state="*")
async def game_m(m: types.Message):
    await GameState.wait_game_type.set()
    await m.answer("Оберіть:", reply_markup=ReplyKeyboardMarkup(resize_keyboard=True).add("❌ Хрестики нолики", "❌ Скасувати"))

@dp.message_handler(state=GameState.wait_game_type)
async def game_t(m: types.Message, state: FSMContext):
    if m.text == "❌ Хрестики нолики":
        await GameState.wait_size.set()
        await m.answer("Розмір:", reply_markup=ReplyKeyboardMarkup(resize_keyboard=True).add("3х3", "4х4", "❌ Скасувати"))
    else: await state.finish(); await m.answer("Меню", reply_markup=main_menu(m.from_user.id))

@dp.message_handler(state=GameState.wait_size)
async def game_s(m: types.Message, state: FSMContext):
    if m.text in ["3х3", "4х4"]:
        await state.update_data(s=3 if "3" in m.text else 4)
        await GameState.wait_bet.set()
        await m.answer("Ставка 💎:", reply_markup=cancel_kb())
    else: await state.finish(); await m.answer("Меню", reply_markup=main_menu(m.from_user.id))

@dp.message_handler(state=GameState.wait_bet)
async def game_b(m: types.Message, state: FSMContext):
    try:
        bet = float(m.text)
        u = await get_u(m)
        if u['balance'] < bet: return await m.answer("Мало 💎!")
        d = await state.get_data()
        gid = str(uuid.uuid4())[:8]
        await users_col.update_one({"_id": m.from_user.id}, {"$inc": {"balance": -bet}})
        await games_col.insert_one({"game_id": gid, "creator_id": m.from_user.id, "bet": bet, "size": d['s'], "board": [" "]*(d['s']**2), "status": "waiting"})
        link = f"https://t.me/{(await bot.get_me()).username}?start=game_{gid}"
        await m.answer(f"✅ Гра створена!\n`{link}`", parse_mode="Markdown", reply_markup=main_menu(m.from_user.id))
        await state.finish()
    except: await m.answer("Введіть число")

@dp.callback_query_handler(lambda c: c.data.startswith("st:"), state="*")
async def make_move(c: types.CallbackQuery):
    _, gid, idx = c.data.split(":"); idx = int(idx)
    g = await games_col.find_one({"game_id": gid})
    if not g or g['status'] != "playing" or c.from_user.id != g['turn']: return
    if g['board'][idx] != " ": return

    char = "❌" if c.from_user.id == g['creator_id'] else "⭕️"
    new_board = list(g['board']); new_board[idx] = char
    next_p = g['opponent_id'] if c.from_user.id == g['creator_id'] else g['creator_id']
    
    await games_col.update_one({"game_id": gid}, {"$set": {"board": new_board, "turn": next_p}})
    win = check_win(new_board, g['size'])
    
    if win:
        await games_col.update_one({"game_id": gid}, {"$set": {"status": "finished"}})
        if win == "draw":
            res = "🤝 Нічия! Повернення ставок."
            await users_col.update_many({"_id": {"$in": [g['creator_id'], g['opponent_id']]}}, {"$inc": {"balance": g['bet']}})
        else:
            winner = g['creator_id'] if win == "❌" else g['opponent_id']
            res = f"🎉 Переміг {win}!"
            await users_col.update_one({"_id": winner}, {"$inc": {"balance": g['bet']*2}})
        await bot.send_message(g['creator_id'], res, reply_markup=get_board_markup(gid, new_board, g['size']))
        await bot.send_message(g['opponent_id'], res, reply_markup=get_board_markup(gid, new_board, g['size']))
    else:
        kb = get_board_markup(gid, new_board, g['size'])
        await c.message.edit_text("⏳ Хід суперника...", reply_markup=kb)
        await bot.send_message(next_p, "🔔 Твій хід!", reply_markup=kb)

if __name__ == '__main__':
    executor.start_polling(dp, skip_updates=True)
