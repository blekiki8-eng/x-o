import os
import uuid
import logging
import asyncio
import time
from aiogram import Bot, Dispatcher, executor, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

load_dotenv()

# --- Налаштування ---
API_TOKEN = os.getenv("BOT_TOKEN")
MONGO_URL = os.getenv("MONGO_URL")
ADMIN_ID = int(os.getenv("ADMIN_ID")) if os.getenv("ADMIN_ID") else 0
MOVE_TIMEOUT = 40 
EXCHANGE_RATE = 44.5
BONUS_PERCENT = 0.05
MIN_DEPOSIT = 0.50

logging.basicConfig(level=logging.INFO)
storage = MemoryStorage()
bot = Bot(token=API_TOKEN)
dp = Dispatcher(bot, storage=storage)

cluster = AsyncIOMotorClient(MONGO_URL)
db = cluster["fishcash_game"]
users_col = db["users"]
games_col = db["games"]

# --- Стани ---
class DepositState(StatesGroup):
    wait_amount = State()
    wait_receipt = State()

class GameState(StatesGroup):
    wait_bet = State()

# --- Клавіатури ---
def main_menu():
    keyboard = ReplyKeyboardMarkup(resize_keyboard=True)
    keyboard.add(KeyboardButton("👤 Профіль"), KeyboardButton("🎮 Ігри"))
    keyboard.add(KeyboardButton("💎 Баланс"))
    return keyboard

def get_board_markup(game_id, board):
    markup = InlineKeyboardMarkup(row_width=3)
    buttons = []
    for i in range(9):
        char = board[i] if board[i] != " " else "⬜️"
        buttons.append(InlineKeyboardButton(char, callback_data=f"tic_{game_id}_{i}"))
    markup.add(*buttons)
    return markup

# --- Логіка гри ---
def check_winner(b):
    win_coords = [(0,1,2), (3,4,5), (6,7,8), (0,3,6), (1,4,7), (2,5,8), (0,4,8), (2,4,6)]
    for r in win_coords:
        if b[r[0]] == b[r[1]] == b[r[2]] != " ": return b[r[0]]
    if " " not in b: return "draw"
    return None

async def update_game_messages(game, board, winner=None):
    game_id = game['game_id']
    markup = get_board_markup(game_id, board)
    players = [(game['creator_id'], game.get('creator_msg_id')), (game['opponent_id'], game.get('opponent_msg_id'))]

    for uid, mid in players:
        if not mid: continue
        try:
            if winner:
                if winner == "draw":
                    text = "🤝 **Нічия! Ставки повернуті на баланс.**"
                else:
                    symbol = "X" if uid == game['creator_id'] else "O"
                    if winner == symbol:
                        text = "🏁 **Гру завершено!**"
                        await bot.send_message(uid, f"🎉 **Вітаю з перемогою!**\n💰 Виграш {game['bet']*2} 💎 нарахований на твій баланс!", parse_mode="Markdown")
                    else:
                        text = "❌ **Ви програли. Наступного разу пощастить!**"
                await bot.edit_message_text(text, uid, mid, reply_markup=markup, parse_mode="Markdown")
            else:
                text = "🎮 **Твій хід! (Залишилось 40 сек.)**" if uid == game['turn'] else "⏳ **Хід суперника... Очікуйте.**"
                await bot.edit_message_text(text, uid, mid, reply_markup=markup, parse_mode="Markdown")
        except: pass

async def check_game_timeouts():
    while True:
        current_time = time.time()
        async for game in games_col.find({"status": "playing"}):
            if current_time - game['last_move_time'] > MOVE_TIMEOUT:
                winner_id = game['opponent_id'] if game['turn'] == game['creator_id'] else game['creator_id']
                win_char = "O" if winner_id == game['opponent_id'] else "X"
                await games_col.update_one({"game_id": game['game_id']}, {"$set": {"status": "finished"}})
                await users_col.update_one({"_id": winner_id}, {"$inc": {"balance": game['bet'] * 2}})
                await update_game_messages(game, game['board'], winner=win_char)
        await asyncio.sleep(3)

# --- Обробники ---
@dp.message_handler(commands=['start'])
async def start_cmd(message: types.Message):
    user = await users_col.find_one({"_id": message.from_user.id})
    if not user:
        await users_col.insert_one({"_id": message.from_user.id, "username": message.from_user.username, "balance": 0})
    
    args = message.get_args()
    if args and args.startswith("game_"):
        game_id = args.split("_")[1]
        game = await games_col.find_one({"game_id": game_id, "status": "waiting"})
        if not game: return await message.answer("❌ Гра не знайдена.")
        if game['creator_id'] == message.from_user.id: return await message.answer("❌ Ви не можете грати з собою.")
        
        user = await users_col.find_one({"_id": message.from_user.id})
        if user['balance'] < game['bet']: return await message.answer("❌ Недостатньо балансу.")

        await users_col.update_one({"_id": message.from_user.id}, {"$inc": {"balance": -game['bet']}})
        msg_opp = await message.answer("🎮 Завантаження поля...")
        
        update_data = {"opponent_id": message.from_user.id, "opponent_msg_id": msg_opp.message_id, "status": "playing", "turn": game['creator_id'], "last_move_time": time.time()}
        await games_col.update_one({"game_id": game_id}, {"$set": update_data})
        game.update(update_data)
        await update_game_messages(game, game['board'])
        return
    await message.answer("💎 Вітаємо у FishCash!", reply_markup=main_menu())

# --- БАЛАНС (Виправлено) ---
@dp.message_handler(lambda m: m.text == "💎 Баланс")
async def balance_view(message: types.Message):
    user = await users_col.find_one({"_id": message.from_user.id})
    balance = user['balance'] if user else 0
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton("💳 Поповнення", callback_data="deposit"), 
        InlineKeyboardButton("📤 Виведення", callback_data="withdraw")
    )
    # Тут прибрано курс за запитом
    await message.answer(f"💎 **Ваш баланс: {balance} 💎**", reply_markup=markup, parse_mode="Markdown")

@dp.callback_query_handler(lambda c: c.data == "deposit")
async def dep_start(callback: types.CallbackQuery):
    await bot.send_message(callback.from_user.id, f"💰 Введіть суму в 💎 (мінімум {MIN_DEPOSIT}):")
    await DepositState.wait_amount.set()
    await callback.answer()

# --- ЗАЯВКА НА ПОПОВНЕННЯ (Оновлено структуру) ---
@dp.message_handler(state=DepositState.wait_amount)
async def dep_amt(message: types.Message, state: FSMContext):
    try:
        amt = float(message.text.replace(',', '.'))
        if amt < MIN_DEPOSIT:
            return await message.answer(f"❌ Мінімальна сума поповнення: {MIN_DEPOSIT} 💎")
        
        base_price = amt * EXCHANGE_RATE
        total_with_bonus = base_price * (1 + BONUS_PERCENT)
        
        await state.update_data(amount=amt)
        
        caption = (
            f"📄 **Заявка на поповнення:**\n\n"
            f"До сплати: {base_price:.2f} грн\n"
            f"Курс: {EXCHANGE_RATE:.2f}\n"
            f"До сплати: {total_with_bonus:.2f} грн (+5%)\n\n"
            f"`5355 2800 2890 2177`\n"
            f"(Обовʼязково сюди кидайте квитанцію для підтвердження)"
        )
        await message.answer(caption, parse_mode="Markdown")
        await DepositState.wait_receipt.set()
    except: await message.answer("❌ Введіть число.")

@dp.message_handler(state=DepositState.wait_receipt, content_types=['photo'])
async def dep_rec(message: types.Message, state: FSMContext):
    data = await state.get_data()
    kb = InlineKeyboardMarkup().add(
        InlineKeyboardButton("✅ ТАК", callback_data=f"adm_confirm_{message.from_user.id}_{data['amount']}"), 
        InlineKeyboardButton("❌ НІ", callback_data=f"adm_reject_{message.from_user.id}")
    )
    await bot.send_photo(ADMIN_ID, message.photo[-1].file_id, caption=f"Чек від {message.from_user.id}\nСума: {data['amount']} 💎", reply_markup=kb)
    await message.answer("✅ Чек надіслано! Очікуйте підтвердження адміністратором.")
    await state.finish()

@dp.callback_query_handler(lambda c: c.data.startswith('adm_'))
async def adm_act(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID: return
    p = callback.data.split("_")
    uid = int(p[2])
    if p[1] == "confirm":
        await users_col.update_one({"_id": uid}, {"$inc": {"balance": float(p[3])}})
        await bot.send_message(uid, f"✅ Баланс поповнено на {p[3]} 💎!")
    else: await bot.send_message(uid, "❌ Ваш чек був відхилений.")
    await callback.message.edit_caption("Оброблено.")

# --- Решта функцій ігор ---
@dp.message_handler(lambda m: m.text == "🎮 Ігри")
async def games_menu(message: types.Message):
    markup = InlineKeyboardMarkup().add(InlineKeyboardButton("❌ Хрестики-Нолики ⭕️", callback_data="tic_info"))
    await message.answer("Оберіть гру:", reply_markup=markup)

@dp.callback_query_handler(lambda c: c.data == "tic_info")
async def tic_info(callback: types.CallbackQuery):
    markup = InlineKeyboardMarkup().add(InlineKeyboardButton("🟩 Матч 3х3", callback_data="tic_create"))
    await callback.message.answer("🕹 Виберіть тип матчу:", reply_markup=markup)
    await callback.answer()

@dp.callback_query_handler(lambda c: c.data == "tic_create")
async def tic_bet_req(callback: types.CallbackQuery):
    await callback.message.answer("💰 Введіть ставку (мін. 0.05 💎):")
    await GameState.wait_bet.set()
    await callback.answer()

@dp.message_handler(state=GameState.wait_bet)
async def tic_final(message: types.Message, state: FSMContext):
    try:
        bet = float(message.text.replace(',', '.'))
        user = await users_col.find_one({"_id": message.from_user.id})
        if bet < 0.05 or user['balance'] < bet: return await message.answer("❌ Недостатньо балансу або замала ставка!")
        
        gid = str(uuid.uuid4())[:8]
        await users_col.update_one({"_id": message.from_user.id}, {"$inc": {"balance": -bet}})
        msg = await message.answer("⏳ Створення матчу...")
        await games_col.insert_one({"game_id": gid, "creator_id": message.from_user.id, "opponent_id": None, "bet": bet, "board": [" "]*9, "status": "waiting", "turn": None, "creator_msg_id": msg.message_id, "last_move_time": time.time()})
        await message.answer(f"🔗 Посилання для друга:\n`https://t.me/{(await bot.get_me()).username}?start=game_{gid}`", parse_mode="Markdown")
        await state.finish()
    except: await message.answer("❌ Введіть число.")

@dp.callback_query_handler(lambda c: c.data.startswith('tic_'))
async def tic_step(callback: types.CallbackQuery):
    _, gid, idx = callback.data.split("_")
    g = await games_col.find_one({"game_id": gid})
    if not g or g['status'] != "playing" or callback.from_user.id != g['turn'] or g['board'][int(idx)] != " ":
        return await callback.answer("Не твій хід!")

    new_board = list(g['board'])
    new_board[int(idx)] = "X" if callback.from_user.id == g['creator_id'] else "O"
    winner = check_winner(new_board)
    nxt = g['opponent_id'] if callback.from_user.id == g['creator_id'] else g['creator_id']
    
    await games_col.update_one({"game_id": gid}, {"$set": {"board": new_board, "turn": nxt, "last_move_time": time.time()}})
    g['board'], g['turn'] = new_board, nxt
    
    if winner:
        await games_col.update_one({"game_id": gid}, {"$set": {"status": "finished"}})
        if winner != "draw":
            wid = g['creator_id'] if winner == "X" else g['opponent_id']
            await users_col.update_one({"_id": wid}, {"$inc": {"balance": g['bet']*2}})
        else: await users_col.update_many({"_id": {"$in": [g['creator_id'], g['opponent_id']]}}, {"$inc": {"balance": g['bet']}})
    await update_game_messages(g, new_board, winner)
    await callback.answer()

@dp.message_handler(lambda m: m.text == "👤 Профіль")
async def profile_v(message: types.Message):
    user = await users_col.find_one({"_id": message.from_user.id})
    await message.answer(f"👤 Профіль\n🆔 ID: `{message.from_user.id}`\n💰 Баланс: {user['balance'] if user else 0} 💎", parse_mode="Markdown")

if __name__ == '__main__':
    loop = asyncio.get_event_loop()
    loop.create_task(check_game_timeouts())
    executor.start_polling(dp, skip_updates=True)
