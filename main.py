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

# --- ЛОГІКА ПЕРЕМОГИ (УНІВЕРСАЛЬНА) ---
def check_winner(b, size):
    # Визначаємо лінії для перевірки залежно від розміру
    lines = []
    # Горизонталі
    for i in range(size):
        lines.append(tuple(range(i*size, (i+1)*size)))
    # Вертикалі
    for i in range(size):
        lines.append(tuple(range(i, size*size, size)))
    # Діагоналі
    lines.append(tuple(range(0, size*size, size+1)))
    lines.append(tuple(range(size-1, size*size-1, size-1)))

    for r in lines:
        if all(b[r[i]] == b[r[0]] != " " for i in range(size)):
            return b[r[0]]
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
            res_text = "🤝 **Нічия!**" if winner == "draw" else ("🏁 **Ви виграли!** 🏆" if (winner == "X" and role == 'creator') or (winner == "O" and role == 'opponent') else "❌ **Ви програли.**")
            await bot.send_message(uid, res_text, parse_mode="Markdown", reply_markup=main_menu())
        else:
            text = "🎮 **Твій хід! (30 сек)**" if uid == game['turn'] else "⏳ **Хід суперника...**"
            try: await bot.edit_message_text(text, uid, mid, reply_markup=markup, parse_mode="Markdown")
            except: pass

# --- ОБРОБНИКИ СТАРТУ ТА ІГОР ---
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
            if g['creator_id'] == m.from_user.id: return await m.answer("❌ Не можна грати з собою.")
            u = await users_col.find_one({"_id": m.from_user.id})
            if u['balance'] < g['bet']: return await m.answer("❌ Недостатньо 💎")
            
            await users_col.update_one({"_id": m.from_user.id}, {"$inc": {"balance": -g['bet']}})
            
            info = f"🎮 **Гра: Хрестики-нолики {g['size']}x{g['size']}**\n💰 **Ставка: {g['bet']} 💎**\n🏆 **Виграш: {round(g['bet']*2, 2)} 💎**\n\nГра почалася ✅"
            for uid, role_text in [(g['creator_id'], "❌ Ви за **Хрестики**"), (m.from_user.id, "⭕️ Ви за **Нолики**")]:
                await bot.send_message(uid, info, parse_mode="Markdown")
                await bot.send_message(uid, role_text, parse_mode="Markdown")

            msg_opp = await m.answer("⏳ Поле...", reply_markup=get_board_markup(gid, g['board'], g['size']))
            msg_cre = await bot.send_message(g['creator_id'], "⏳ Поле...", reply_markup=get_board_markup(gid, g['board'], g['size']))
            
            upd = {"opponent_id": m.from_user.id, "opponent_msg_id": msg_opp.message_id, "creator_msg_id": msg_cre.message_id, "status": "playing", "turn": g['creator_id'], "last_move_time": time.time()}
            await games_col.update_one({"game_id": gid}, {"$set": upd})
            g.update(upd)
            await update_game_messages(g, g['board'])
            return
    await m.answer("Головне меню", reply_markup=main_menu())

@dp.callback_query_handler(lambda c: c.data == "tic_create", state="*")
async def tic_create_req(c: types.CallbackQuery):
    await bot.send_message(c.from_user.id, f"💰 Ставка (від {MIN_BET} 💎):", reply_markup=cancel_keyboard())
    await GameState.wait_bet.set()
    await c.answer()

@dp.message_handler(state=GameState.wait_bet)
async def tic_set_bet(m: types.Message, state: FSMContext):
    try:
        bet = float(m.text.replace(',', '.'))
        u = await users_col.find_one({"_id": m.from_user.id})
        if bet < MIN_BET or u['balance'] < bet: return await m.answer("❌ Помилка!")
        await state.update_data(bet=bet)
        
        kb = InlineKeyboardMarkup().add(
            InlineKeyboardButton("3x3", callback_data="size_3"),
            InlineKeyboardButton("4x4", callback_data="size_4")
        )
        await m.answer("Оберіть розмір поля:", reply_markup=kb)
        await GameState.wait_size.set()
    except: await m.answer("❌ Число!")

@dp.callback_query_handler(state=GameState.wait_size)
async def tic_set_size(c: types.CallbackQuery, state: FSMContext):
    size = int(c.data.split("_")[1])
    data = await state.get_data()
    bet = data['bet']
    gid = str(uuid.uuid4())[:8]
    
    await users_col.update_one({"_id": c.from_user.id}, {"$inc": {"balance": -bet}})
    await state.finish()
    
    link = f"https://t.me/{(await bot.get_me()).username}?start=game_{gid}"
    await bot.send_message(c.from_user.id, f"🎮 **Матч {size}x{size} створено!**\n💰 Ставка: {bet}\n\nПосилання:\n{link}", reply_markup=main_menu())
    
    await games_col.insert_one({
        "game_id": gid, "creator_id": c.from_user.id, "opponent_id": None, 
        "bet": bet, "size": size, "board": [" "]*(size*size), 
        "status": "waiting", "turn": None, "last_move_time": time.time()
    })
    await c.answer()

@dp.callback_query_handler(lambda c: c.data.startswith('ticstep_'), state="*")
async def tic_step(c: types.CallbackQuery):
    _, gid, idx = c.data.split("_")
    g = await games_col.find_one({"game_id": gid})
    if not g or g['status'] != "playing" or c.from_user.id != g['turn'] or g['board'][int(idx)] != " ":
        return await c.answer("Не твій хід!")
    
    nb = list(g['board'])
    nb[int(idx)] = "X" if c.from_user.id == g['creator_id'] else "O"
    win = check_winner(nb, g['size'])
    nxt = g['opponent_id'] if c.from_user.id == g['creator_id'] else g['creator_id']
    
    await games_col.update_one({"game_id": gid}, {"$set": {"board": nb, "turn": nxt, "last_move_time": time.time()}})
    
    if win:
        await games_col.update_one({"game_id": gid}, {"$set": {"status": "finished"}})
        if win != "draw":
            wid = g['creator_id'] if win == "X" else g['opponent_id']
            await users_col.update_one({"_id": wid}, {"$inc": {"balance": g['bet']*2}})
        else:
            await users_col.update_many({"_id": {"$in": [g['creator_id'], g['opponent_id']]}}, {"$inc": {"balance": g['bet']}})
    
    g['board'], g['turn'] = nb, nxt
    await update_game_messages(g, nb, win)
    await c.answer()

# --- ТУТ ПОВИННІ БУТИ ОБРОБНИКИ ДЛЯ ПРОФІЛЮ, БАЛАНСУ, ДЕПОЗИТУ ТА ВИВОДУ З ПОПЕРЕДНІХ ПОВІДОМЛЕНЬ ---
# (Залиш їх без змін, вони працюють так само)

@dp.message_handler(lambda m: m.text == "👤 Профіль", state="*")
async def profile_view(m: types.Message):
    u = await users_col.find_one({"_id": m.from_user.id})
    await m.answer(f"👤 **Профіль**\n🆔 ID: `{m.from_user.id}`\n💰 Баланс: {round(u['balance'], 2)} 💎", parse_mode="Markdown")

@dp.message_handler(lambda m: m.text == "💎 Баланс", state="*")
async def balance_view(m: types.Message):
    u = await users_col.find_one({"_id": m.from_user.id})
    kb = InlineKeyboardMarkup().add(InlineKeyboardButton("💳 Поповнити", callback_data="deposit"), InlineKeyboardButton("📤 Вивести", callback_data="withdraw_start"))
    await m.answer(f"💎 Баланс: **{round(u['balance'], 2)}**", reply_markup=kb, parse_mode="Markdown")

@dp.message_handler(lambda m: m.text == "🎮 Ігри", state="*")
async def games_menu(m: types.Message):
    kb = InlineKeyboardMarkup().add(InlineKeyboardButton("❌ Хрестики-нолики ⭕️", callback_data="tic_info"))
    await m.answer("Оберіть гру:", reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data == "tic_info", state="*")
async def tic_info(c: types.CallbackQuery):
    kb = InlineKeyboardMarkup().add(InlineKeyboardButton("🟩 Створити матч", callback_data="tic_create"))
    await c.message.edit_text("🕹 **Хрестики-нолики**", reply_markup=kb, parse_mode="Markdown")

# --- ЦИКЛ ТАЙМЕРІВ ---
async def check_timeouts():
    while True:
        try:
            async for game in games_col.find({"status": "playing"}):
                if time.time() - game.get('last_move_time', 0) > MOVE_TIMEOUT:
                    winner_id = game['opponent_id'] if game['turn'] == game['creator_id'] else game['creator_id']
                    await games_col.update_one({"game_id": game['game_id']}, {"$set": {"status": "finished"}})
                    await users_col.update_one({"_id": winner_id}, {"$inc": {"balance": game['bet'] * 2}})
                    await update_game_messages(game, game['board'], winner=("X" if winner_id == game['creator_id'] else "O"))
        except: pass
        await asyncio.sleep(3)

if __name__ == '__main__':
    loop = asyncio.get_event_loop()
    loop.create_task(check_timeouts())
    executor.start_polling(dp, skip_updates=True)
