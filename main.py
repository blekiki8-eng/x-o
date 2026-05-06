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
EXCHANGE_RATE = 44.5
BONUS_PERCENT = 0.05
MOVE_TIMEOUT = 40  # Секунд на хід

logging.basicConfig(level=logging.INFO)
storage = MemoryStorage()
bot = Bot(token=API_TOKEN)
dp = Dispatcher(bot, storage=storage)

cluster = AsyncIOMotorClient(MONGO_URL)
db = cluster["fishcash_game"]
users_col = db["users"]
games_col = db["games"]

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

async def get_user(user_id, username="Гравець"):
    user = await users_col.find_one({"_id": user_id})
    if not user:
        user = {"_id": user_id, "username": username, "balance": 0}
        await users_col.insert_one(user)
    return user

# --- Фонова перевірка таймера ---
async def check_game_timeouts():
    while True:
        current_time = time.time()
        active_games = games_col.find({"status": "playing"})
        async for game in active_games:
            if current_time - game['last_move_time'] > MOVE_TIMEOUT:
                # Той, чий зараз хід — програв по таймауту
                loser_id = game['turn']
                winner_id = game['opponent_id'] if loser_id == game['creator_id'] else game['creator_id']
                
                await games_col.update_one({"game_id": game['game_id']}, {"$set": {"status": "finished"}})
                await users_col.update_one({"_id": winner_id}, {"$inc": {"balance": game['bet'] * 2}})
                
                text = f"⏰ Час вийшов! Гравець не встиг зробити хід.\n🎉 Перемога присуджена опоненту! Виграш: {game['bet']*2} 💎"
                
                for uid, mid in [(game['creator_id'], game['creator_msg_id']), (game['opponent_id'], game['opponent_msg_id'])]:
                    try: await bot.send_message(uid, text)
                    except: pass
        await asyncio.sleep(5)

# --- Обробники ---
@dp.message_handler(commands=['start'])
async def start_cmd(message: types.Message):
    await get_user(message.from_user.id, message.from_user.username)
    args = message.get_args()
    
    if args and args.startswith("game_"):
        game_id = args.split("_")[1]
        game = await games_col.find_one({"game_id": game_id, "status": "waiting"})
        
        if not game: return await message.answer("❌ Гра не знайдена.")
        if game['creator_id'] == message.from_user.id: return await message.answer("❌ Це ваше посилання.")
        
        user = await get_user(message.from_user.id)
        if user['balance'] < game['bet']: return await message.answer("❌ Недостатньо балансу.")

        # Початок гри
        await users_col.update_one({"_id": message.from_user.id}, {"$inc": {"balance": -game['bet']}})
        
        # Надсилаємо поле другому гравцю
        msg_opp = await message.answer(f"🎮 Гра почалася! Твій хід буде другим (O).\nСтавка: {game['bet']} 💎", reply_markup=get_board_markup(game_id, game['board']))
        
        # Оновлюємо гру в БД (зберігаємо ID обох повідомлень)
        await games_col.update_one({"game_id": game_id}, {"$set": {
            "opponent_id": message.from_user.id,
            "status": "playing",
            "turn": game['creator_id'],
            "last_move_time": time.time(),
            "opponent_msg_id": msg_opp.message_id
        }})
        
        # Повідомляємо першого гравця
        await bot.send_message(game['creator_id'], "✅ Суперник приєднався! Твій хід (X):", reply_markup=get_board_markup(game_id, game['board']))
        return

    await message.answer("💎 Вітаємо у FishCash!", reply_markup=main_menu())

@dp.message_handler(lambda m: m.text == "🎮 Ігри")
async def games_view(message: types.Message):
    markup = InlineKeyboardMarkup().add(InlineKeyboardButton("❌ Хрестики-Нолики ⭕️", callback_data="tic_info"))
    await message.answer("Оберіть гру:", reply_markup=markup)

@dp.callback_query_handler(lambda c: c.data == "tic_info")
async def tic_mode(callback: types.CallbackQuery):
    markup = InlineKeyboardMarkup().add(InlineKeyboardButton("🟩 Матч 3х3", callback_data="tic_mode_3x3"))
    await callback.message.answer("🕹 Виберіть тип матчу:", reply_markup=markup)
    await callback.answer()

@dp.callback_query_handler(lambda c: c.data == "tic_mode_3x3")
async def tic_bet(callback: types.CallbackQuery):
    await callback.message.answer("💰 Введіть ставку (мін. 0.05 💎):")
    await GameState.wait_bet.set()
    await callback.answer()

@dp.message_handler(state=GameState.wait_bet)
async def tic_create(message: types.Message, state: FSMContext):
    user = await get_user(message.from_user.id)
    try:
        bet = float(message.text.replace(',', '.'))
        if bet < 0.05 or user['balance'] < bet:
            return await message.answer("❌ Помилка ставки або балансу.")
        
        game_id = str(uuid.uuid4())[:8]
        await users_col.update_one({"_id": message.from_user.id}, {"$inc": {"balance": -bet}})
        
        # Створюємо гру і зберігаємо ID повідомлення творця
        msg_creator = await message.answer(f"✅ Гра створена! Ставка: {bet} 💎\nЧекаємо на суперника...")
        
        await games_col.insert_one({
            "game_id": game_id, "creator_id": message.from_user.id, "opponent_id": None,
            "bet": bet, "board": [" "] * 9, "status": "waiting", "turn": None,
            "creator_msg_id": msg_creator.message_id, "opponent_msg_id": None,
            "last_move_time": time.time()
        })
        
        me = await bot.get_me()
        await message.answer(f"🔗 Посилання для друга:\n`https://t.me/{me.username}?start=game_{game_id}`", parse_mode="Markdown")
        await state.finish()
    except: await message.answer("Введіть число.")

@dp.callback_query_handler(lambda c: c.data.startswith('tic_'))
async def tic_move(callback: types.CallbackQuery):
    _, gid, idx = callback.data.split("_")
    idx, g = int(idx), await games_col.find_one({"game_id": gid})
    
    if not g or g['status'] != "playing" or callback.from_user.id != g['turn']:
        return await callback.answer("Не твій хід!", show_alert=True)
    
    if g['board'][idx] != " ": return await callback.answer("Зайнято!")

    char = "X" if callback.from_user.id == g['creator_id'] else "O"
    new_b = list(g['board'])
    new_b[idx] = char
    win = check_winner(new_b)
    nxt = g['opponent_id'] if callback.from_user.id == g['creator_id'] else g['creator_id']
    
    # Оновлюємо базу
    await games_col.update_one({"game_id": gid}, {"$set": {"board": new_b, "turn": nxt, "last_move_time": time.time()}})

    # Оновлюємо повідомлення у ОБОХ гравців
    markup = get_board_markup(gid, new_b)
    
    for uid, mid in [(g['creator_id'], g['creator_msg_id']), (g['opponent_id'], g['opponent_msg_id'])]:
        try:
            if win:
                if win == "draw": text = "🤝 Нічия!"
                else: text = f"🎉 Перемога {win}!"
                await bot.edit_message_text(text, uid, mid, reply_markup=markup)
            else:
                text = "🎮 Твій хід!" if uid == nxt else "⏳ Хід суперника..."
                await bot.edit_message_text(text, uid, mid, reply_markup=markup)
        except: pass

    if win:
        await games_col.update_one({"game_id": gid}, {"$set": {"status": "finished"}})
        if win == "draw":
            await users_col.update_many({"_id": {"$in": [g['creator_id'], g['opponent_id']]}}, {"$inc": {"balance": g['bet']}})
        else:
            wid = g['creator_id'] if win == "X" else g['opponent_id']
            await users_col.update_one({"_id": wid}, {"$inc": {"balance": g['bet'] * 2}})
    await callback.answer()

# --- Решта функцій (Профіль, Баланс) залишаються без змін ---
@dp.message_handler(lambda m: m.text == "👤 Профіль")
async def profile_view(message: types.Message):
    user = await get_user(message.from_user.id)
    await message.answer(f"👤 Профіль\n🆔 ID: {user['_id']}\n💰 Баланс: {user['balance']} 💎")

@dp.message_handler(lambda m: m.text == "💎 Баланс")
async def balance_view(message: types.Message):
    user = await get_user(message.from_user.id)
    kb = InlineKeyboardMarkup().add(InlineKeyboardButton("💳 Поповнити", callback_data="deposit"))
    await message.answer(f"💎 Баланс: {user['balance']} 💎", reply_markup=kb)

if __name__ == '__main__':
    # Запускаємо таймер у фоні
    loop = asyncio.get_event_loop()
    loop.create_task(check_game_timeouts())
    executor.start_polling(dp, skip_updates=True)
