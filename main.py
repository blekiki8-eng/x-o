import os, uuid, logging, asyncio, time
from aiogram import Bot, Dispatcher, executor, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
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

MOVE_TIMEOUT = 30  
EXCHANGE_RATE, BONUS_PERCENT = 44.50, 0.05
MIN_DEPOSIT, MIN_WITHDRAW, MIN_BET = 0.50, 4.0, 0.05

logging.basicConfig(level=logging.INFO)
storage = MemoryStorage()
bot = Bot(token=API_TOKEN)
dp = Dispatcher(bot, storage=storage)

cluster = AsyncIOMotorClient(MONGO_URL)
db = cluster["fishcash_game"]
users_col, games_col = db["users"], db["games"]

# --- СТАНИ FSM ---
class Registration(StatesGroup):
    wait_nickname = State()

class DepositState(StatesGroup):
    wait_amount = State()
    wait_receipt = State()

class WithdrawState(StatesGroup):
    wait_amount = State()
    wait_details = State()

class GameState(StatesGroup):
    wait_bet = State()
    wait_size = State()

# --- ДОПОМІЖНІ ФУНКЦІЇ ---
def main_menu(user_id):
    markup = ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add(KeyboardButton("👤 Профіль"), KeyboardButton("🎮 Ігри"))
    markup.add(KeyboardButton("💎 Баланс"))
    if user_id == ADMIN_ID:
        markup.add(KeyboardButton("🛡 Панель адміна"))
    return markup

def cancel_kb():
    return ReplyKeyboardMarkup(resize_keyboard=True).add("❌ Скасувати")

async def is_banned(uid):
    u = await users_col.find_one({"_id": uid})
    return u.get("banned", False) if u else False

async def get_nick(uid):
    u = await users_col.find_one({"_id": uid})
    return u.get("nickname", "Гравець") if u else "Гравець"

def get_board_markup(game_id, board, size):
    markup = InlineKeyboardMarkup(row_width=size)
    btns = [InlineKeyboardButton(board[i] if board[i] != " " else "⬜️", callback_data=f"ticstep_{game_id}_{i}") for i in range(size*size)]
    return markup.add(*btns)

def check_winner(b, size):
    lines = []
    for i in range(size):
        lines.append(tuple(range(i*size, (i+1)*size))) # Горизонталі
        lines.append(tuple(range(i, size*size, size))) # Вертикалі
    lines.append(tuple(range(0, size*size, size+1))) # Діагональ 1
    lines.append(tuple(range(size-1, size*size-1, size-1))) # Діагональ 2
    for r in lines:
        if all(b[r[i]] == b[r[0]] != " " for i in range(size)): return b[r[0]]
    return "draw" if " " not in b else None

async def update_game_messages(game, board, winner=None):
    size = game.get('size', 3)
    markup = get_board_markup(game['game_id'], board, size)
    cr_nick = await get_nick(game['creator_id'])
    op_nick = await get_nick(game['opponent_id'])
    
    for role in ['creator', 'opponent']:
        uid = game.get(f'{role}_id')
        mid = game.get(f'{role}_msg_id')
        if not uid or not mid: continue
        if winner:
            try: await bot.edit_message_reply_markup(uid, mid, reply_markup=None)
            except: pass
            res = "🤝 Нічия!" if winner == "draw" else ("🏆 Ти переміг!" if (winner == "X" and role == 'creator') or (winner == "O" and role == 'opponent') else "❌ Поразка.")
            await bot.send_message(uid, res, reply_markup=main_menu(uid))
        else:
            turn_nick = cr_nick if game['turn'] == game['creator_id'] else op_nick
            txt = f"❌ **{cr_nick}** VS ⭕️ **{op_nick}**\n\nЗараз ходить: **{turn_nick}**"
            try: await bot.edit_message_text(txt, uid, mid, reply_markup=markup, parse_mode="Markdown")
            except: pass

# --- ОБРОБНИКИ СТАРТУ ---
@dp.message_handler(commands=['start'], state="*")
async def start_cmd(m: types.Message, state: FSMContext):
    if await is_banned(m.from_user.id): return
    u = await users_col.find_one({"_id": m.from_user.id})
    
    if not u:
        await m.answer("👋 Вітаємо! Для початку гри введіть свій нікнейм:")
        await Registration.wait_nickname.set()
        return

    args = m.get_args()
    if args and args.startswith("game_"):
        gid = args.split("_")[1]
        g = await games_col.find_one({"game_id": gid, "status": "waiting"})
        if g and g['creator_id'] != m.from_user.id:
            if u['balance'] >= g['bet']:
                await users_col.update_one({"_id": m.from_user.id}, {"$inc": {"balance": -g['bet']}})
                # Початок гри
                msg_opp = await m.answer("Завантаження гри...", reply_markup=get_board_markup(gid, g['board'], g['size']))
                msg_cre = await bot.send_message(g['creator_id'], "Суперник знайдений!", reply_markup=get_board_markup(gid, g['board'], g['size']))
                upd = {"opponent_id": m.from_user.id, "opponent_msg_id": msg_opp.message_id, "creator_msg_id": msg_cre.message_id, "status": "playing", "turn": g['creator_id'], "last_move_time": time.time()}
                await games_col.update_one({"game_id": gid}, {"$set": upd})
                g.update(upd)
                await update_game_messages(g, g['board'])
                return
            else: await m.answer("❌ Недостатньо балансу для входу в гру.")
    
    await m.answer(f"З поверненням, {u['nickname']}!", reply_markup=main_menu(m.from_user.id))

@dp.message_handler(state=Registration.wait_nickname)
async def reg_nick(m: types.Message, state: FSMContext):
    nick = m.text.strip()[:15]
    await users_col.insert_one({"_id": m.from_user.id, "nickname": nick, "balance": 0.0, "banned": False})
    await m.answer(f"✅ Приємно познайомитись, **{nick}**!", parse_mode="Markdown", reply_markup=main_menu(m.from_user.id))
    await state.finish()

# --- АДМІНКА ---
@dp.message_handler(lambda m: m.text == "🛡 Панель адміна", state="*")
async def adm_panel(m: types.Message):
    if m.from_user.id != ADMIN_ID: return
    kb = InlineKeyboardMarkup().add(InlineKeyboardButton("👥 Гравці", callback_data="adm_list"))
    await m.answer("🛡 Панель керування", reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data == "adm_list", state="*")
async def adm_list(c: types.CallbackQuery):
    users = await users_col.find().to_list(100)
    kb = InlineKeyboardMarkup(row_width=1)
    for u in users:
        st = "🚫" if u.get("banned") else "👤"
        kb.add(InlineKeyboardButton(f"{st} {u['nickname']} | {u['balance']} 💎", callback_data=f"man_{u['_id']}"))
    await c.message.edit_text("Список користувачів:", reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data.startswith(('man_', 'res_', 'bn_')), state="*")
async def adm_actions(c: types.CallbackQuery):
    if c.from_user.id != ADMIN_ID: return
    p = c.data.split("_")
    action, uid = p[0], int(p[1])
    
    if action == "res":
        await users_col.update_one({"_id": uid}, {"$set": {"balance": 0.0}})
    elif action == "bn":
        u = await users_col.find_one({"_id": uid})
        await users_col.update_one({"_id": uid}, {"$set": {"banned": not u.get("banned", False)}})
    
    # Оновлення меню керування
    u = await users_col.find_one({"_id": uid})
    bt = "Розблокувати" if u.get("banned") else "Заблокувати"
    kb = InlineKeyboardMarkup(row_width=1).add(
        InlineKeyboardButton("💰 Обнулити баланс", callback_data=f"res_{uid}"),
        InlineKeyboardButton(f"⛔️ {bt}", callback_data=f"bn_{uid}"),
        InlineKeyboardButton("⬅️ Назад", callback_data="adm_list")
    )
    await c.message.edit_text(f"Користувач: {u['nickname']}\nID: {uid}\nБаланс: {u['balance']}", reply_markup=kb)

# --- ГРОШІ (ПОПОВНЕННЯ ТА ВИВІД) ---
@dp.message_handler(lambda m: m.text == "💎 Баланс", state="*")
async def bal(m: types.Message):
    if await is_banned(m.from_user.id): return
    u = await users_col.find_one({"_id": m.from_user.id})
    kb = InlineKeyboardMarkup().add(InlineKeyboardButton("💳 Поповнити", callback_data="dep"), InlineKeyboardButton("📤 Вивести", callback_data="wit"))
    await m.answer(f"💰 Баланс: **{u['balance']} 💎**", reply_markup=kb, parse_mode="Markdown")

@dp.callback_query_handler(lambda c: c.data == "dep", state="*")
async def dep_start(c: types.CallbackQuery):
    await DepositState.wait_amount.set()
    await bot.send_message(c.from_user.id, "Введіть суму в 💎:", reply_markup=cancel_kb())

@dp.message_handler(state=DepositState.wait_amount)
async def dep_amt(m: types.Message, state: FSMContext):
    try:
        amt = float(m.text)
        await state.update_data(a=amt)
        await m.answer(f"Сплатіть {round(amt*EXCHANGE_RATE*1.05, 2)} ₴ на карту `5355 2800 2890 2177` та надішліть фото чека.")
        await DepositState.wait_receipt.set()
    except: await m.answer("❌ Введіть число!")

@dp.message_handler(state=DepositState.wait_receipt, content_types=['photo'])
async def dep_rec(m: types.Message, state: FSMContext):
    d = await state.get_data()
    n = await get_nick(m.from_user.id)
    kb = InlineKeyboardMarkup().add(InlineKeyboardButton("✅", callback_data=f"dok_{m.from_user.id}_{d['a']}"), InlineKeyboardButton("❌", callback_data=f"dno_{m.from_user.id}"))
    await bot.send_photo(ADMIN_ID, m.photo[-1].file_id, caption=f"Поповнення {d['a']} від {n} ({m.from_user.id})", reply_markup=kb)
    await m.answer("✅ Чек надіслано адміну.", reply_markup=main_menu(m.from_user.id))
    await state.finish()

@dp.callback_query_handler(lambda c: c.data.startswith(('dok', 'dno')), state="*")
async def adm_dep_ver(c: types.CallbackQuery):
    if c.from_user.id != ADMIN_ID: return
    p = c.data.split("_")
    uid = int(p[1])
    if p[0] == "dok":
        await users_col.update_one({"_id": uid}, {"$inc": {"balance": float(p[2])}})
        await bot.send_message(uid, "✅ Поповнення схвалено!")
    await c.message.delete()

# (Додай логіку виводу wit за аналогією з попередніми версіями)

# --- ГРА ---
@dp.message_handler(lambda m: m.text == "🎮 Ігри", state="*")
async def game_menu(m: types.Message):
    if await is_banned(m.from_user.id): return
    kb = InlineKeyboardMarkup().add(InlineKeyboardButton("❌ Хрестики-Нолики ⭕️", callback_data="tic_c"))
    await m.answer("Оберіть гру:", reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data == "tic_c", state="*")
async def tic_create(c: types.CallbackQuery):
    await GameState.wait_bet.set()
    await bot.send_message(c.from_user.id, "Введіть ставку 💎:", reply_markup=cancel_kb())

@dp.message_handler(state=GameState.wait_bet)
async def tic_bet(m: types.Message, state: FSMContext):
    try:
        b = float(m.text)
        u = await users_col.find_one({"_id": m.from_user.id})
        if u['balance'] < b: return await m.answer("❌ Мало грошей!")
        await state.update_data(b=b)
        kb = InlineKeyboardMarkup().add(InlineKeyboardButton("3x3", callback_data="s_3"), InlineKeyboardButton("4x4", callback_data="s_4"))
        await m.answer("Розмір поля:", reply_markup=kb)
        await GameState.wait_size.set()
    except: await m.answer("❌ Число!")

@dp.callback_query_handler(state=GameState.wait_size)
async def tic_size(c: types.CallbackQuery, state: FSMContext):
    sz = int(c.data.split("_")[1])
    d = await state.get_data()
    gid = str(uuid.uuid4())[:8]
    await users_col.update_one({"_id": c.from_user.id}, {"$inc": {"balance": -d['b']}})
    await games_col.insert_one({"game_id": gid, "creator_id": c.from_user.id, "bet": d['b'], "size": sz, "board": [" "]*(sz*sz), "status": "waiting", "last_move_time": time.time()})
    link = f"https://t.me/{(await bot.get_me()).username}?start=game_{gid}"
    await bot.send_message(c.from_user.id, f"🎮 Матч {sz}x{sz} створено!\nПосилання для друга:\n{link}", reply_markup=main_menu(c.from_user.id))
    await state.finish()

@dp.callback_query_handler(lambda c: c.data.startswith('ticstep_'), state="*")
async def tic_step(c: types.CallbackQuery):
    p = c.data.split("_")
    g = await games_col.find_one({"game_id": p[1]})
    if not g or g['status'] != "playing" or g['turn'] != c.from_user.id: return
    
    idx = int(p[2])
    if g['board'][idx] != " ": return
    
    nb = list(g['board'])
    nb[idx] = "X" if c.from_user.id == g['creator_id'] else "O"
    win = check_winner(nb, g['size'])
    nxt = g['opponent_id'] if c.from_user.id == g['creator_id'] else g['creator_id']
    
    await games_col.update_one({"game_id": p[1]}, {"$set": {"board": nb, "turn": nxt, "last_move_time": time.time()}})
    
    if win:
        await games_col.update_one({"game_id": p[1]}, {"$set": {"status": "finished"}})
        if win != "draw":
            wid = g['creator_id'] if win == "X" else g['opponent_id']
            await users_col.update_one({"_id": wid}, {"$inc": {"balance": g['bet']*2}})
        else:
            await users_col.update_many({"_id": {"$in": [g['creator_id'], g['opponent_id']]}}, {"$inc": {"balance": g['bet']}})
    
    g['board'] = nb
    await update_game_messages(g, nb, win)

# --- ТАЙМЕРИ ---
async def timer_loop():
    while True:
        async for g in games_col.find({"status": "playing"}):
            if time.time() - g['last_move_time'] > MOVE_TIMEOUT:
                win_id = g['opponent_id'] if g['turn'] == g['creator_id'] else g['creator_id']
                await games_col.update_one({"game_id": g['game_id']}, {"$set": {"status": "finished"}})
                await users_col.update_one({"_id": win_id}, {"$inc": {"balance": g['bet'] * 2}})
                await update_game_messages(g, g['board'], winner=("X" if win_id == g['creator_id'] else "O"))
        await asyncio.sleep(5)

@dp.message_handler(lambda m: m.text == "👤 Профіль", state="*")
async def prof(m: types.Message):
    if await is_banned(m.from_user.id): return
    u = await users_col.find_one({"_id": m.from_user.id})
    await m.answer(f"👤 Нік: {u['nickname']}\n💰 Баланс: {u['balance']} 💎", reply_markup=main_menu(m.from_user.id))

@dp.message_handler(state="*", text="❌ Скасувати")
async def can(m: types.Message, state: FSMContext):
    await state.finish()
    await m.answer("Скасовано.", reply_markup=main_menu(m.from_user.id))

if __name__ == '__main__':
    loop = asyncio.get_event_loop()
    loop.create_task(timer_loop())
    executor.start_polling(dp, skip_updates=True)
