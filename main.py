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
def main_menu():
    markup = ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add(KeyboardButton("👤 Профіль"), KeyboardButton("🎮 Ігри"))
    markup.add(KeyboardButton("💎 Баланс"))
    return markup

def cancel_keyboard():
    return ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True).add("❌ Скасувати")

def get_board_markup(game_id, board, size):
    markup = InlineKeyboardMarkup(row_width=size)
    btns = [InlineKeyboardButton(board[i] if board[i] != " " else "⬜️", callback_data=f"ticstep_{game_id}_{i}") for i in range(size*size)]
    return markup.add(*btns)

# --- ЛОГІКА ПЕРЕМОГИ ---
def check_winner(b, size):
    lines = []
    for i in range(size): lines.append(tuple(range(i*size, (i+1)*size)))
    for i in range(size): lines.append(tuple(range(i, size*size, size)))
    lines.append(tuple(range(0, size*size, size+1)))
    lines.append(tuple(range(size-1, size*size-1, size-1)))
    for r in lines:
        if all(b[r[i]] == b[r[0]] != " " for i in range(size)): return b[r[0]]
    return "draw" if " " not in b else None

async def update_game_messages(game, board, winner=None):
    size = game.get('size', 3)
    markup = get_board_markup(game['game_id'], board, size)
    for role in ['creator', 'opponent']:
        uid = game.get(f'{role}_id')
        mid = game.get(f'{role}_msg_id')
        if not uid or not mid: continue
        if winner:
            try: await bot.edit_message_reply_markup(uid, mid, reply_markup=None)
            except: pass
            res = "🤝 Нічия!" if winner == "draw" else ("🏁 Ви виграли!" if (winner == "X" and role == 'creator') or (winner == "O" and role == 'opponent') else "❌ Ви програли.")
            await bot.send_message(uid, res, reply_markup=main_menu())
        else:
            txt = "🎮 Твій хід!" if uid == game['turn'] else "⏳ Хід суперника..."
            try: await bot.edit_message_text(txt, uid, mid, reply_markup=markup)
            except: pass

# --- ОБРОБНИКИ СИСТЕМИ ---
@dp.message_handler(commands=['start'], state="*")
async def start_cmd(m: types.Message, state: FSMContext):
    await state.finish()
    if not await users_col.find_one({"_id": m.from_user.id}):
        await users_col.insert_one({"_id": m.from_user.id, "balance": 0.0})
    
    args = m.get_args()
    if args and args.startswith("game_"):
        gid = args.split("_")[1]
        g = await games_col.find_one({"game_id": gid, "status": "waiting"})
        if g:
            u = await users_col.find_one({"_id": m.from_user.id})
            if u['balance'] < g['bet']: return await m.answer("❌ Недостатньо балансу.")
            await users_col.update_one({"_id": m.from_user.id}, {"$inc": {"balance": -g['bet']}})
            
            # Старт гри
            start_info = f"🎮 Гра: {g['size']}x{g['size']}\n💰 Ставка: {g['bet']}\nГра почалася!"
            await bot.send_message(g['creator_id'], start_info + "\n❌ Ви за Хрестики")
            await m.answer(start_info + "\n⭕️ Ви за Нолики")

            msg_opp = await m.answer("⏳ Поле...", reply_markup=get_board_markup(gid, g['board'], g['size']))
            msg_cre = await bot.send_message(g['creator_id'], "⏳ Поле...", reply_markup=get_board_markup(gid, g['board'], g['size']))
            
            upd = {"opponent_id": m.from_user.id, "opponent_msg_id": msg_opp.message_id, "creator_msg_id": msg_cre.message_id, "status": "playing", "turn": g['creator_id'], "last_move_time": time.time()}
            await games_col.update_one({"game_id": gid}, {"$set": upd})
            g.update(upd)
            await update_game_messages(g, g['board'])
            return
    await m.answer("Головне меню", reply_markup=main_menu())

@dp.message_handler(lambda m: m.text == "👤 Профіль", state="*")
async def profile(m: types.Message):
    u = await users_col.find_one({"_id": m.from_user.id})
    await m.answer(f"🆔 ID: `{m.from_user.id}`\n💰 Баланс: {round(u['balance'], 2)} 💎", parse_mode="Markdown")

@dp.message_handler(lambda m: m.text == "💎 Баланс", state="*")
async def balance(m: types.Message):
    kb = InlineKeyboardMarkup().add(InlineKeyboardButton("💳 Поповнити", callback_data="dep"), InlineKeyboardButton("📤 Вивести", callback_data="wit"))
    await m.answer("Керування балансом:", reply_markup=kb)

# --- ПОПОВНЕННЯ (ADMIN) ---
@dp.callback_query_handler(lambda c: c.data == "dep", state="*")
async def dep_init(c: types.CallbackQuery):
    await DepositState.wait_amount.set()
    await bot.send_message(c.from_user.id, "💎 Введіть суму поповнення:", reply_markup=cancel_keyboard())
    await c.answer()

@dp.message_handler(state=DepositState.wait_amount)
async def dep_amt(m: types.Message, state: FSMContext):
    try:
        amt = float(m.text.replace(',', '.'))
        total = round((amt * EXCHANGE_RATE) * (1 + BONUS_PERCENT), 2)
        await state.update_data(a=amt)
        await m.answer(f"💳 Сплатіть {total} ₴ на карту `5355 2800 2890 2177` та надішліть фото чека.")
        await DepositState.wait_receipt.set()
    except: await m.answer("❌ Число!")

@dp.message_handler(state=DepositState.wait_receipt, content_types=['photo'])
async def dep_rec(m: types.Message, state: FSMContext):
    data = await state.get_data()
    kb = InlineKeyboardMarkup().add(InlineKeyboardButton("✅", callback_data=f"d_ok_{m.from_user.id}_{data['a']}"), InlineKeyboardButton("❌", callback_data=f"d_no_{m.from_user.id}"))
    await bot.send_photo(ADMIN_ID, m.photo[-1].file_id, caption=f"Поповнення {data['a']} від {m.from_user.id}", reply_markup=kb)
    await m.answer("✅ Чек надіслано!", reply_markup=main_menu())
    await state.finish()

@dp.callback_query_handler(lambda c: c.data.startswith('d_'), state="*")
async def adm_dep(c: types.CallbackQuery):
    if c.from_user.id != ADMIN_ID: return
    p = c.data.split("_")
    uid = int(p[2])
    if p[1] == "ok":
        await users_col.update_one({"_id": uid}, {"$inc": {"balance": float(p[3])}})
        await bot.send_message(uid, "✅ Баланс поповнено!")
    else: await bot.send_message(uid, "❌ Заявка відхилена.")
    await c.message.delete()
    await c.answer()

# --- ВИВЕДЕННЯ (ADMIN + IBAN/ІПН) ---
@dp.callback_query_handler(lambda c: c.data == "wit", state="*")
async def wit_init(c: types.CallbackQuery):
    await WithdrawState.wait_amount.set()
    await bot.send_message(c.from_user.id, f"📤 Сума (мін. {MIN_WITHDRAW}):", reply_markup=cancel_keyboard())
    await c.answer()

@dp.message_handler(state=WithdrawState.wait_amount)
async def wit_amt(m: types.Message, state: FSMContext):
    try:
        amt = float(m.text.replace(',', '.'))
        u = await users_col.find_one({"_id": m.from_user.id})
        if u['balance'] < amt or amt < MIN_WITHDRAW: return await m.answer("❌ Помилка суми.")
        await state.update_data(a=amt)
        await m.answer("Введіть IBAN, ІПН та ПІБ одним повідомленням:")
        await WithdrawState.wait_details.set()
    except: await m.answer("❌ Число!")

@dp.message_handler(state=WithdrawState.wait_details)
async def wit_det(m: types.Message, state: FSMContext):
    data = await state.get_data()
    await users_col.update_one({"_id": m.from_user.id}, {"$inc": {"balance": -data['a']}})
    kb = InlineKeyboardMarkup().add(InlineKeyboardButton("✅", callback_data=f"w_ok_{m.from_user.id}_{data['a']}"), InlineKeyboardButton("❌", callback_data=f"w_no_{m.from_user.id}_{data['a']}"))
    await bot.send_message(ADMIN_ID, f"📤 Вивід {data['a']}\nЮзер: {m.from_user.id}\nДані: {m.text}", reply_markup=kb)
    await m.answer("✅ Заявка на вивід надіслана!", reply_markup=main_menu())
    await state.finish()

@dp.callback_query_handler(lambda c: c.data.startswith('w_'), state="*")
async def adm_wit(c: types.CallbackQuery):
    if c.from_user.id != ADMIN_ID: return
    p = c.data.split("_")
    uid, amt = int(p[2]), float(p[3])
    if p[1] == "ok": await bot.send_message(uid, f"✅ Вивід {amt} виконано!")
    else:
        await users_col.update_one({"_id": uid}, {"$inc": {"balance": amt}})
        await bot.send_message(uid, "❌ Вивід відхилено. Кошти повернено.")
    await c.message.delete()
    await c.answer()

# --- СТВОРЕННЯ ГРИ ---
@dp.message_handler(lambda m: m.text == "🎮 Ігри", state="*")
async def games(m: types.Message):
    kb = InlineKeyboardMarkup().add(InlineKeyboardButton("❌ Хрестики-нолики ⭕️", callback_data="tic_i"))
    await m.answer("Оберіть гру:", reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data == "tic_i", state="*")
async def tic_i(c: types.CallbackQuery):
    kb = InlineKeyboardMarkup().add(InlineKeyboardButton("🟩 Створити матч", callback_data="tic_c"))
    await c.message.edit_text("🕹 Хрестики-нолики", reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data == "tic_c", state="*")
async def tic_c(c: types.CallbackQuery):
    await GameState.wait_bet.set()
    await bot.send_message(c.from_user.id, "💰 Ставка:", reply_markup=cancel_keyboard())
    await c.answer()

@dp.message_handler(state=GameState.wait_bet)
async def tic_bet(m: types.Message, state: FSMContext):
    try:
        bet = float(m.text.replace(',', '.'))
        u = await users_col.find_one({"_id": m.from_user.id})
        if u['balance'] < bet: return await m.answer("❌ Недостатньо коштів.")
        await state.update_data(b=bet)
        kb = InlineKeyboardMarkup().add(InlineKeyboardButton("3x3", callback_data="s_3"), InlineKeyboardButton("4x4", callback_data="s_4"))
        await m.answer("Розмір поля:", reply_markup=kb)
        await GameState.wait_size.set()
    except: await m.answer("❌ Число!")

@dp.callback_query_handler(state=GameState.wait_size)
async def tic_sz(c: types.CallbackQuery, state: FSMContext):
    sz = int(c.data.split("_")[1])
    data = await state.get_data()
    gid = str(uuid.uuid4())[:8]
    await users_col.update_one({"_id": c.from_user.id}, {"$inc": {"balance": -data['b']}})
    await games_col.insert_one({"game_id": gid, "creator_id": c.from_user.id, "bet": data['b'], "size": sz, "board": [" "]*(sz*sz), "status": "waiting", "last_move_time": time.time()})
    link = f"https://t.me/{(await bot.get_me()).username}?start=game_{gid}"
    await bot.send_message(c.from_user.id, f"🎮 Матч {sz}x{sz} створено!\nПосилання:\n{link}", reply_markup=main_menu())
    await state.finish()

@dp.callback_query_handler(lambda c: c.data.startswith('ticstep_'), state="*")
async def tic_st(c: types.CallbackQuery):
    p = c.data.split("_")
    g = await games_col.find_one({"game_id": p[1]})
    if not g or g['status'] != "playing" or c.from_user.id != g['turn'] or g['board'][int(p[2])] != " ": return
    nb = list(g['board'])
    nb[int(p[2])] = "X" if c.from_user.id == g['creator_id'] else "O"
    win = check_winner(nb, g['size'])
    nxt = g['opponent_id'] if c.from_user.id == g['creator_id'] else g['creator_id']
    await games_col.update_one({"game_id": p[1]}, {"$set": {"board": nb, "turn": nxt, "last_move_time": time.time()}})
    if win:
        await games_col.update_one({"game_id": p[1]}, {"$set": {"status": "finished"}})
        if win != "draw":
            wid = g['creator_id'] if win == "X" else g['opponent_id']
            await users_col.update_one({"_id": wid}, {"$inc": {"balance": g['bet']*2}})
        else: await users_col.update_many({"_id": {"$in": [g['creator_id'], g['opponent_id']]}}, {"$inc": {"balance": g['bet']}})
    g['board'], g['turn'] = nb, nxt
    await update_game_messages(g, nb, win)

# --- ТАЙМЕРИ ---
async def check_timers():
    while True:
        try:
            async for g in games_col.find({"status": "playing"}):
                if time.time() - g['last_move_time'] > MOVE_TIMEOUT:
                    win_id = g['opponent_id'] if g['turn'] == g['creator_id'] else g['creator_id']
                    await games_col.update_one({"game_id": g['game_id']}, {"$set": {"status": "finished"}})
                    await users_col.update_one({"_id": win_id}, {"$inc": {"balance": g['bet'] * 2}})
                    await update_game_messages(g, g['board'], winner=("X" if win_id == g['creator_id'] else "O"))
        except: pass
        await asyncio.sleep(5)

@dp.message_handler(state="*", text="❌ Скасувати")
async def cancel(m: types.Message, state: FSMContext):
    await state.finish()
    await m.answer("Скасовано.", reply_markup=main_menu())

if __name__ == '__main__':
    loop = asyncio.get_event_loop()
    loop.create_task(check_timers())
    executor.start_polling(dp, skip_updates=True)
