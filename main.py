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

MOVE_TIMEOUT = 30  
EXCHANGE_RATE, BONUS_PERCENT = 44.50, 0.05
MIN_WITHDRAW = 4.0

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
    if u and "nickname" in u:
        return u["nickname"]
    return "Гравець"

def get_board_markup(game_id, board, size):
    markup = InlineKeyboardMarkup(row_width=size)
    btns = [InlineKeyboardButton(board[i] if board[i] != " " else "⬜️", callback_data=f"ticstep_{game_id}_{i}") for i in range(size*size)]
    return markup.add(*btns)

def check_winner(b, size):
    lines = []
    for i in range(size):
        lines.append(tuple(range(i*size, (i+1)*size)))
        lines.append(tuple(range(i, size*size, size)))
    if size == 3:
        lines.append((0, 4, 8)); lines.append((2, 4, 6))
    else:
        lines.append((0, 5, 10, 15)); lines.append((3, 6, 9, 12))
    for r in lines:
        if all(b[r[i]] == b[r[0]] != " " for i in range(size)): return b[r[0]]
    return "draw" if " " not in b else None

# --- ГРА: ОНОВЛЕННЯ ПОВІДОМЛЕНЬ ---
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
            res = "🤝 Нічия!" if winner == "draw" else ("🏆 Ви виграли!" if (winner == "X" and role == 'creator') or (winner == "O" and role == 'opponent') else "❌ Ви програли.")
            await bot.send_message(uid, res, reply_markup=main_menu(uid))
        else:
            turn_nick = cr_nick if game['turn'] == game['creator_id'] else op_nick
            txt = f"❌ {cr_nick} VS ⭕️ {op_nick}\n\nЗараз ходить: **{turn_nick}**"
            try: await bot.edit_message_text(txt, uid, mid, reply_markup=markup, parse_mode="Markdown")
            except: pass

# --- ОБРОБНИКИ СИСТЕМИ ---
@dp.message_handler(commands=['start'], state="*")
async def start_cmd(m: types.Message, state: FSMContext):
    await state.finish()
    if await is_banned(m.from_user.id): return
    
    user = await users_col.find_one({"_id": m.from_user.id})
    if not user or "nickname" not in user:
        await m.answer("👋 Ласкаво просимо! Введіть ваш ігровий нікнейм:")
        await Registration.wait_nickname.set()
        return

    args = m.get_args()
    if args and args.startswith("game_"):
        gid = args.split("_")[1]
        g = await games_col.find_one({"game_id": gid, "status": "waiting"})
        if g and g['creator_id'] != m.from_user.id:
            if user['balance'] >= g['bet']:
                await users_col.update_one({"_id": m.from_user.id}, {"$inc": {"balance": -g['bet']}})
                msg_opp = await m.answer("🎮 Гра почалася!", reply_markup=get_board_markup(gid, g['board'], g['size']))
                msg_cre = await bot.send_message(g['creator_id'], "🎮 Гра почалася!", reply_markup=get_board_markup(gid, g['board'], g['size']))
                upd = {"opponent_id": m.from_user.id, "opponent_msg_id": msg_opp.message_id, "creator_msg_id": msg_cre.message_id, "status": "playing", "turn": g['creator_id'], "last_move_time": time.time()}
                await games_col.update_one({"game_id": gid}, {"$set": upd})
                g.update(upd)
                await update_game_messages(g, g['board'])
                return

    await m.answer(f"Головне меню", reply_markup=main_menu(m.from_user.id))

@dp.message_handler(state=Registration.wait_nickname)
async def reg_nick(m: types.Message, state: FSMContext):
    nick = m.text.strip()[:15]
    await users_col.update_one({"_id": m.from_user.id}, {"$set": {"nickname": nick, "balance": 0.0, "banned": False}}, upsert=True)
    await m.answer(f"✅ Нікнейм {nick} збережено!", reply_markup=main_menu(m.from_user.id))
    await state.finish()

# --- ПРОФІЛЬ ТА АДМІНКА ---
@dp.message_handler(lambda m: m.text == "👤 Профіль", state="*")
async def profile(m: types.Message):
    u = await users_col.find_one({"_id": m.from_user.id})
    if not u: return
    await m.answer(f"👤 Профіль: {u['nickname']}\n💰 Баланс: {u['balance']} 💎\n🆔 ID: `{m.from_user.id}`", parse_mode="Markdown")

@dp.message_handler(lambda m: m.text == "🛡 Панель адміна", state="*")
async def adm_panel(m: types.Message):
    if m.from_user.id != ADMIN_ID: return
    kb = InlineKeyboardMarkup().add(InlineKeyboardButton("👥 Список гравців", callback_data="alist"))
    await m.answer("🛡 Адмін-панель", reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data == "alist", state="*")
async def adm_list(c: types.CallbackQuery):
    users = await users_col.find().to_list(100)
    kb = InlineKeyboardMarkup(row_width=1)
    for u in users:
        kb.add(InlineKeyboardButton(f"{u.get('nickname')} | {u['balance']} 💎", callback_data=f"aman_{u['_id']}"))
    await c.message.edit_text("Гравці:", reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data.startswith(('aman_', 'ares_', 'abn_')), state="*")
async def adm_actions(c: types.CallbackQuery):
    p = c.data.split("_")
    act, uid = p[0], int(p[1])
    if act == "ares": await users_col.update_one({"_id": uid}, {"$set": {"balance": 0.0}})
    elif act == "abn":
        u = await users_col.find_one({"_id": uid})
        await users_col.update_one({"_id": uid}, {"$set": {"banned": not u.get("banned", False)}})
    
    u = await users_col.find_one({"_id": uid})
    kb = InlineKeyboardMarkup().add(
        InlineKeyboardButton("💰 Обнулити", callback_data=f"ares_{uid}"),
        InlineKeyboardButton("🚫 Бан/Розбан", callback_data=f"abn_{uid}"),
        InlineKeyboardButton("⬅️ Назад", callback_data="alist")
    )
    await c.message.edit_text(f"Гравець: {u.get('nickname')}\nБаланс: {u['balance']}\nБан: {u.get('banned')}", reply_markup=kb)

# --- БАЛАНС (ПОПОВНЕННЯ ТА ВИВІД) ---
@dp.message_handler(lambda m: m.text == "💎 Баланс", state="*")
async def bal(m: types.Message):
    u = await users_col.find_one({"_id": m.from_user.id})
    kb = InlineKeyboardMarkup().add(InlineKeyboardButton("💳 Поповнити", callback_data="dep"), InlineKeyboardButton("📤 Вивести", callback_data="wit"))
    await m.answer(f"Твій баланс: {u['balance']} 💎", reply_markup=kb)

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
    except: await m.answer("Число!")

@dp.message_handler(state=DepositState.wait_receipt, content_types=['photo'])
async def dep_rec(m: types.Message, state: FSMContext):
    d = await state.get_data()
    kb = InlineKeyboardMarkup().add(InlineKeyboardButton("✅", callback_data=f"dok_{m.from_user.id}_{d['a']}"), InlineKeyboardButton("❌", callback_data=f"dno_{m.from_user.id}"))
    await bot.send_photo(ADMIN_ID, m.photo[-1].file_id, caption=f"Поповнення {d['a']} від {m.from_user.id}", reply_markup=kb)
    await m.answer("Чек надіслано!", reply_markup=main_menu(m.from_user.id))
    await state.finish()

@dp.callback_query_handler(lambda c: c.data.startswith(('dok_', 'dno_')), state="*")
async def adm_dep_res(c: types.CallbackQuery):
    if c.from_user.id != ADMIN_ID: return
    p = c.data.split("_")
    uid = int(p[1])
    if p[0] == "dok":
        await users_col.update_one({"_id": uid}, {"$inc": {"balance": float(p[2])}})
        await bot.send_message(uid, "✅ Баланс поповнено!")
    await c.message.delete()

@dp.callback_query_handler(lambda c: c.data == "wit", state="*")
async def wit_start(c: types.CallbackQuery):
    await WithdrawState.wait_amount.set()
    await bot.send_message(c.from_user.id, "Сума виводу:", reply_markup=cancel_kb())

@dp.message_handler(state=WithdrawState.wait_amount)
async def wit_amt(m: types.Message, state: FSMContext):
    u = await users_col.find_one({"_id": m.from_user.id})
    try:
        a = float(m.text)
        if a > u['balance'] or a < MIN_WITHDRAW: return await m.answer("Помилка суми.")
        await state.update_data(a=a)
        await m.answer("Введіть IBAN та ПІБ:")
        await WithdrawState.wait_details.set()
    except: await m.answer("Число!")

@dp.message_handler(state=WithdrawState.wait_details)
async def wit_det(m: types.Message, state: FSMContext):
    d = await state.get_data()
    await users_col.update_one({"_id": m.from_user.id}, {"$inc": {"balance": -d['a']}})
    kb = InlineKeyboardMarkup().add(InlineKeyboardButton("✅ Оплачено", callback_data=f"wok_{m.from_user.id}"), InlineKeyboardButton("❌ Відмова", callback_data=f"wno_{m.from_user.id}_{d['a']}"))
    await bot.send_message(ADMIN_ID, f"📤 Вивід {d['a']} 💎\nДані: {m.text}", reply_markup=kb)
    await m.answer("Заявка надіслана!", reply_markup=main_menu(m.from_user.id))
    await state.finish()

@dp.callback_query_handler(lambda c: c.data.startswith(('wok_', 'wno_')), state="*")
async def adm_wit_res(c: types.CallbackQuery):
    p = c.data.split("_")
    uid = int(p[1])
    if p[0] == "wok": await bot.send_message(uid, "✅ Кошти виплачено!")
    else:
        await users_col.update_one({"_id": uid}, {"$inc": {"balance": float(p[2])}})
        await bot.send_message(uid, "❌ Вивід відхилено.")
    await c.message.delete()

# --- ІГРИ (ХРЕСТИКИ-НОЛИКИ) ---
@dp.message_handler(lambda m: m.text == "🎮 Ігри", state="*")
async def game_menu(m: types.Message):
    kb = InlineKeyboardMarkup().add(InlineKeyboardButton("❌ Хрестики-Нолики ⭕️", callback_data="gtic"))
    await m.answer("Оберіть гру:", reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data == "gtic", state="*")
async def gtic_start(c: types.CallbackQuery):
    await GameState.wait_bet.set()
    await bot.send_message(c.from_user.id, "Ставка 💎:", reply_markup=cancel_kb())

@dp.message_handler(state=GameState.wait_bet)
async def gtic_bet(m: types.Message, state: FSMContext):
    try:
        b = float(m.text)
        u = await users_col.find_one({"_id": m.from_user.id})
        if u['balance'] < b: return await m.answer("Недостатньо коштів.")
        await state.update_data(b=b)
        kb = InlineKeyboardMarkup().add(InlineKeyboardButton("3x3", callback_data="sz_3"), InlineKeyboardButton("4x4", callback_data="sz_4"))
        await m.answer("Розмір:", reply_markup=kb)
        await GameState.wait_size.set()
    except: await m.answer("Число!")

@dp.callback_query_handler(state=GameState.wait_size)
async def gtic_size(c: types.CallbackQuery, state: FSMContext):
    sz = int(c.data.split("_")[1])
    d = await state.get_data()
    gid = str(uuid.uuid4())[:8]
    await users_col.update_one({"_id": c.from_user.id}, {"$inc": {"balance": -d['b']}})
    await games_col.insert_one({"game_id": gid, "creator_id": c.from_user.id, "bet": d['b'], "size": sz, "board": [" "]*(sz*sz), "status": "waiting", "last_move_time": time.time()})
    link = f"https://t.me/{(await bot.get_me()).username}?start=game_{gid}"
    await bot.send_message(c.from_user.id, f"Матч створено!\nПосилання для друга:\n{link}", reply_markup=main_menu(c.from_user.id))
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
        else: await users_col.update_many({"_id": {"$in": [g['creator_id'], g['opponent_id']]}}, {"$inc": {"balance": g['bet']}})
    g['board'] = nb
    await update_game_messages(g, nb, win)

# --- ТАЙМЕРИ ---
async def timers():
    while True:
        async for g in games_col.find({"status": "playing"}):
            if time.time() - g['last_move_time'] > MOVE_TIMEOUT:
                win_id = g['opponent_id'] if g['turn'] == g['creator_id'] else g['creator_id']
                await games_col.update_one({"game_id": g['game_id']}, {"$set": {"status": "finished"}})
                await users_col.update_one({"_id": win_id}, {"$inc": {"balance": g['bet'] * 2}})
                await update_game_messages(g, g['board'], winner=("X" if win_id == g['creator_id'] else "O"))
        await asyncio.sleep(5)

@dp.message_handler(state="*", text="❌ Скасувати")
async def cancel(m: types.Message, state: FSMContext):
    await state.finish()
    await m.answer("Скасовано.", reply_markup=main_menu(m.from_user.id))

if __name__ == '__main__':
    loop = asyncio.get_event_loop()
    loop.create_task(timers())
    executor.start_polling(dp, skip_updates=True)
