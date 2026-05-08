import os, uuid, logging, asyncio, time
from aiogram import Bot, Dispatcher, executor, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

# --- КОНФІГУРАЦІЯ ---
load_dotenv()
API_TOKEN = os.getenv("BOT_TOKEN")
MONGO_URL = os.getenv("MONGO_URL")
ADMIN_ID = int(os.getenv("ADMIN_ID")) if os.getenv("ADMIN_ID") else 0

EXCHANGE_RATE = 44.50
MOVE_TIMEOUT = 60 # секунд на хід

logging.basicConfig(level=logging.INFO)
storage = MemoryStorage()
bot = Bot(token=API_TOKEN)
dp = Dispatcher(bot, storage=storage)

cluster = AsyncIOMotorClient(MONGO_URL)
db = cluster["fishcash_game"]
users_col, games_col = db["users"], db["games"]

# --- СТАНИ FSM ---
class DepositState(StatesGroup):
    wait_amount = State()
    wait_receipt = State()

class WithdrawState(StatesGroup):
    wait_amount = State()
    wait_details = State()

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
async def get_u(uid, user_obj=None):
    u = await users_col.find_one({"_id": uid})
    if not u and user_obj:
        u = {"_id": uid, "nickname": user_obj.full_name, "balance": 0.0, "banned": False}
        await users_col.insert_one(u)
    return u

def get_board_markup(game_id, board, size):
    markup = InlineKeyboardMarkup(row_width=size)
    btns = []
    for i in range(len(board)):
        char = board[i] if board[i] != " " else "⬜️"
        btns.append(InlineKeyboardButton(char, callback_data=f"step_{game_id}_{i}"))
    markup.add(*btns)
    return markup

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

async def update_game(game, board, winner=None):
    size = game['size']
    markup = get_board_markup(game['game_id'], board, size)
    cr = await get_u(game['creator_id'])
    op = await get_u(game['opponent_id'])
    
    for role in ['creator', 'opponent']:
        uid = game[f'{role}_id']
        mid = game[f'{role}_msg_id']
        if winner:
            res = "🤝 Нічия!" if winner == "draw" else ("🏆 Перемога!" if (winner == "X" and role == "creator") or (winner == "O" and role == "opponent") else "❌ Поразка")
            try: await bot.send_message(uid, f"Гра завершена!\nРезультат: {res}", reply_markup=main_menu(uid))
            except: pass
        else:
            turn_nick = cr['nickname'] if game['turn'] == cr['_id'] else op['nickname']
            txt = f"🎮 Гра {size}x{size}\n❌ {cr['nickname']} VS ⭕️ {op['nickname']}\n\nЗараз ходить: {turn_nick}"
            try: await bot.edit_message_text(txt, uid, mid, reply_markup=markup)
            except: pass

# --- ОСНОВНА ЛОГІКА ---
@dp.message_handler(commands=['start'], state="*")
async def start(m: types.Message, state: FSMContext):
    await state.finish()
    u = await get_u(m.from_user.id, m.from_user)
    
    args = m.get_args()
    if args and args.startswith("game_"):
        gid = args.split("_")[1]
        g = await games_col.find_one({"game_id": gid, "status": "waiting"})
        if g and g['creator_id'] != m.from_user.id:
            if u['balance'] >= g['bet']:
                await users_col.update_one({"_id": m.from_user.id}, {"$inc": {"balance": -g['bet']}})
                m_op = await m.answer("Гра починається!", reply_markup=get_board_markup(gid, g['board'], g['size']))
                m_cr = await bot.send_message(g['creator_id'], "Суперник знайдений!", reply_markup=get_board_markup(gid, g['board'], g['size']))
                upd = {"opponent_id": m.from_user.id, "opponent_msg_id": m_op.message_id, "creator_msg_id": m_cr.message_id, "status": "playing", "turn": g['creator_id'], "last_move": time.time()}
                await games_col.update_one({"game_id": gid}, {"$set": upd})
                g.update(upd)
                await update_game(g, g['board'])
                return
            else: await m.answer("❌ Мало коштів.")
    
    await m.answer(f"Привіт, {u['nickname']}!", reply_markup=main_menu(m.from_user.id))

@dp.message_handler(lambda m: m.text == "👤 Профіль", state="*")
async def profile(m: types.Message):
    u = await get_u(m.from_user.id)
    await m.answer(f"👤 **Профіль**\n\nНік: {u['nickname']}\nID: `{m.from_user.id}`\nБаланс: {u['balance']} 💎", parse_mode="Markdown")

@dp.message_handler(lambda m: m.text == "🎮 Ігри", state="*")
async def games_list(m: types.Message):
    kb = InlineKeyboardMarkup().add(InlineKeyboardButton("❌ Хрестики-Нолики ⭕️", callback_data="create_tic"))
    await m.answer("Оберіть гру:", reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data == "create_tic", state="*")
async def tic_bet(c: types.CallbackQuery):
    await GameState.wait_bet.set()
    await bot.send_message(c.from_user.id, "Введіть ставку (💎):", reply_markup=cancel_kb())

@dp.message_handler(state=GameState.wait_bet)
async def tic_size(m: types.Message, state: FSMContext):
    try:
        bet = float(m.text)
        u = await get_u(m.from_user.id)
        if u['balance'] < bet: return await m.answer("❌ Недостатньо балансу.")
        await state.update_data(bet=bet)
        kb = InlineKeyboardMarkup().add(InlineKeyboardButton("3x3", callback_data="set_3"), InlineKeyboardButton("4x4", callback_data="set_4"))
        await m.answer("Оберіть розмір поля:", reply_markup=kb)
        await GameState.wait_size.set()
    except: await m.answer("Введіть число!")

@dp.callback_query_handler(state=GameState.wait_size)
async def tic_finish(c: types.CallbackQuery, state: FSMContext):
    size = int(c.data.split("_")[1])
    data = await state.get_data()
    gid = str(uuid.uuid4())[:8]
    await users_col.update_one({"_id": c.from_user.id}, {"$inc": {"balance": -data['bet']}})
    await games_col.insert_one({
        "game_id": gid, "creator_id": c.from_user.id, "bet": data['bet'], 
        "size": size, "board": [" "]*(size*size), "status": "waiting"
    })
    link = f"https://t.me/{(await bot.get_me()).username}?start=game_{gid}"
    await bot.send_message(c.from_user.id, f"🎮 Гру створено!\n\nСтавка: {data['bet']} 💎\n\nВідправте посилання другу:\n{link}", reply_markup=main_menu(c.from_user.id))
    await state.finish()

@dp.callback_query_handler(lambda c: c.data.startswith("step_"))
async def tic_step(c: types.CallbackQuery):
    _, gid, idx = c.data.split("_")
    idx = int(idx)
    g = await games_col.find_one({"game_id": gid, "status": "playing"})
    if not g or g['turn'] != c.from_user.id: return
    if g['board'][idx] != " ": return

    nb = list(g['board'])
    nb[idx] = "X" if c.from_user.id == g['creator_id'] else "O"
    win = check_win(nb, g['size'])
    nxt = g['opponent_id'] if c.from_user.id == g['creator_id'] else g['creator_id']
    
    await games_col.update_one({"game_id": gid}, {"$set": {"board": nb, "turn": nxt, "last_move": time.time()}})
    
    if win:
        await games_col.update_one({"game_id": gid}, {"$set": {"status": "finished"}})
        if win != "draw":
            wid = g['creator_id'] if win == "X" else g['opponent_id']
            await users_col.update_one({"_id": wid}, {"$inc": {"balance": g['bet']*1.9}}) # 1.9х виграш
        else:
            await users_col.update_many({"_id": {"$in": [g['creator_id'], g['opponent_id']]}}, {"$inc": {"balance": g['bet']}})
    
    g['board'], g['turn'] = nb, nxt
    await update_game(g, nb, win)

# --- БАЛАНС ТА АДМІНКА ---
@dp.message_handler(lambda m: m.text == "💎 Баланс", state="*")
async def balance(m: types.Message):
    u = await get_u(m.from_user.id)
    kb = InlineKeyboardMarkup().add(InlineKeyboardButton("💳 Поповнити", callback_data="dep"), InlineKeyboardButton("📤 Вивести", callback_data="wit"))
    await m.answer(f"💎 Баланс: {u['balance']}", reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data == "dep", state="*")
async def dep_1(c: types.CallbackQuery):
    await DepositState.wait_amount.set()
    await bot.send_message(c.from_user.id, "Введіть суму в 💎:", reply_markup=cancel_kb())

@dp.message_handler(state=DepositState.wait_amount)
async def dep_2(m: types.Message, state: FSMContext):
    try:
        amt = float(m.text)
        await state.update_data(a=amt)
        await m.answer(f"Сплатіть {round(amt*EXCHANGE_RATE, 2)} грн на карту `5355 2800 2890 2177` та надішліть фото чека.")
        await DepositState.wait_receipt.set()
    except: await m.answer("Число!")

@dp.message_handler(state=DepositState.wait_receipt, content_types=['photo'])
async def dep_3(m: types.Message, state: FSMContext):
    data = await state.get_data()
    kb = InlineKeyboardMarkup().add(InlineKeyboardButton("✅", callback_data=f"admok_{m.from_user.id}_{data['a']}"), InlineKeyboardButton("❌", callback_data=f"admno_{m.from_user.id}"))
    await bot.send_photo(ADMIN_ID, m.photo[-1].file_id, caption=f"Депозит {data['a']} 💎 від {m.from_user.full_name}", reply_markup=kb)
    await m.answer("Очікуйте підтвердження.", reply_markup=main_menu(m.from_user.id))
    await state.finish()

@dp.message_handler(lambda m: m.text == "🛡 Панель адміна", state="*")
async def admin_panel(m: types.Message):
    if m.from_user.id != ADMIN_ID: return
    kb = InlineKeyboardMarkup().add(InlineKeyboardButton("👥 Гравці", callback_data="alist"))
    await m.answer("🛡 Адмінка", reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data == "alist", state="*")
async def admin_list(c: types.CallbackQuery):
    users = await users_col.find().to_list(100)
    kb = InlineKeyboardMarkup(row_width=1)
    for u in users:
        kb.add(InlineKeyboardButton(f"{u['nickname']} | {u['balance']} 💎", callback_data=f"mng_{u['_id']}"))
    await c.message.edit_text("Список:", reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data.startswith(("mng_", "admok_", "admno_")), state="*")
async def admin_btns(c: types.CallbackQuery):
    if c.from_user.id != ADMIN_ID: return
    p = c.data.split("_")
    
    if p[0] == "admok":
        uid, amt = int(p[1]), float(p[2])
        await users_col.update_one({"_id": uid}, {"$inc": {"balance": amt}})
        await bot.send_message(uid, f"✅ Баланс поповнено на {amt} 💎")
        await c.message.delete()
    elif p[0] == "mng":
        uid = int(p[1])
        u = await get_u(uid)
        kb = InlineKeyboardMarkup().add(InlineKeyboardButton("💰 Обнулити", callback_data=f"areset_{uid}"), InlineKeyboardButton("⬅️ Назад", callback_data="alist"))
        await c.message.edit_text(f"Юзер: {u['nickname']}\nБаланс: {u['balance']}", reply_markup=kb)

@dp.message_handler(state="*", text="❌ Скасувати")
async def cancl(m: types.Message, state: FSMContext):
    await state.finish()
    await m.answer("Скасовано", reply_markup=main_menu(m.from_user.id))

if __name__ == '__main__':
    executor.start_polling(dp, skip_updates=True)
