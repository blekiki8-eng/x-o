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

logging.basicConfig(level=logging.INFO)
storage = MemoryStorage()
bot = Bot(token=API_TOKEN)
dp = Dispatcher(bot, storage=storage)

cluster = AsyncIOMotorClient(MONGO_URL)
db = cluster["fishcash_game"]
users_col = db["users"]
games_col = db["games"]

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
    """ Оновлює повідомлення в обох гравців одночасно """
    game_id = game['game_id']
    markup = get_board_markup(game_id, board)
    
    players = [
        (game['creator_id'], game['creator_msg_id']),
        (game['opponent_id'], game['opponent_msg_id'])
    ]

    for uid, mid in players:
        if not mid: continue
        try:
            if winner:
                if winner == "draw":
                    text = "🤝 **Нічия! Ставки повернуті.**"
                else:
                    symbol = "X" if uid == game['creator_id'] else "O"
                    if winner == symbol:
                        text = f"🎉 **Вітаю з перемогою! Виграш {game['bet']*2} 💎 нарахований на баланс!**"
                    else:
                        text = "❌ **Ви програли. Наступного разу пощастить!**"
                await bot.edit_message_text(text, uid, mid, reply_markup=markup, parse_mode="Markdown")
            else:
                # Визначаємо чий хід
                if uid == game['turn']:
                    text = "🎮 **Твій хід! (Залишилось 40 сек.)**"
                else:
                    text = "⏳ **Хід суперника... Очікуйте.**"
                await bot.edit_message_text(text, uid, mid, reply_markup=markup, parse_mode="Markdown")
        except Exception as e:
            logging.error(f"Помилка оновлення повідомлення для {uid}: {e}")

# --- Фоновий таймер ---
async def check_game_timeouts():
    while True:
        current_time = time.time()
        active_games = games_col.find({"status": "playing"})
        async for game in active_games:
            if current_time - game['last_move_time'] > MOVE_TIMEOUT:
                # Програш по таймауту
                loser_id = game['turn']
                winner_id = game['opponent_id'] if loser_id == game['creator_id'] else game['creator_id']
                win_char = "O" if winner_id == game['opponent_id'] else "X"
                
                await games_col.update_one({"game_id": game['game_id']}, {"$set": {"status": "finished"}})
                await users_col.update_one({"_id": winner_id}, {"$inc": {"balance": game['bet'] * 2}})
                await update_game_messages(game, game['board'], winner=win_char)
        await asyncio.sleep(3)

# --- Обробники ---
@dp.message_handler(commands=['start'])
async def start_cmd(message: types.Message):
    args = message.get_args()
    if args and args.startswith("game_"):
        game_id = args.split("_")[1]
        game = await games_col.find_one({"game_id": game_id, "status": "waiting"})
        
        if not game: return await message.answer("❌ Гра вже почалася або не існує.")
        
        user = await users_col.find_one({"_id": message.from_user.id})
        if not user or user['balance'] < game['bet']: 
            return await message.answer("❌ Недостатньо балансу.")

        # Початок гри для другого гравця
        await users_col.update_one({"_id": message.from_user.id}, {"$inc": {"balance": -game['bet']}})
        msg_opp = await message.answer("🎮 Готуємо поле...")
        
        # Оновлюємо дані гри
        new_data = {
            "opponent_id": message.from_user.id,
            "opponent_msg_id": msg_opp.message_id,
            "status": "playing",
            "turn": game['creator_id'],
            "last_move_time": time.time()
        }
        await games_col.update_one({"game_id": game_id}, {"$set": new_data})
        
        # Запускаємо повідомлення обом
        game.update(new_data)
        await update_game_messages(game, game['board'])
        return

    await message.answer("💎 Вітаємо у FishCash!", reply_markup=main_menu())

@dp.message_handler(lambda m: m.text == "🎮 Ігри")
async def games_view(message: types.Message):
    markup = InlineKeyboardMarkup().add(InlineKeyboardButton("❌ Хрестики-Нолики ⭕️", callback_data="tic_mode"))
    await message.answer("Оберіть гру:", reply_markup=markup)

@dp.callback_query_handler(lambda c: c.data == "tic_mode")
async def tic_mode(callback: types.CallbackQuery):
    markup = InlineKeyboardMarkup().add(InlineKeyboardButton("🟩 Матч 3х3", callback_data="tic_start"))
    await callback.message.answer("🕹 Виберіть формат:", reply_markup=markup)
    await callback.answer()

@dp.callback_query_handler(lambda c: c.data == "tic_start")
async def tic_bet(callback: types.CallbackQuery):
    await callback.message.answer("💰 Введіть ставку (напр. 0.5):")
    await GameState.wait_bet.set()
    await callback.answer()

@dp.message_handler(state=GameState.wait_bet)
async def tic_create(message: types.Message, state: FSMContext):
    try:
        bet = float(message.text.replace(',', '.'))
        user = await users_col.find_one({"_id": message.from_user.id})
        if bet < 0.05 or user['balance'] < bet: return await message.answer("❌ Помилка балансу/ставки.")
        
        game_id = str(uuid.uuid4())[:8]
        await users_col.update_one({"_id": message.from_user.id}, {"$inc": {"balance": -bet}})
        msg_creator = await message.answer("⏳ Створюємо матч...")
        
        await games_col.insert_one({
            "game_id": game_id, "creator_id": message.from_user.id, "opponent_id": None,
            "bet": bet, "board": [" "] * 9, "status": "waiting", "turn": None,
            "creator_msg_id": msg_creator.message_id, "opponent_msg_id": None,
            "last_move_time": time.time()
        })
        
        me = await bot.get_me()
        await message.answer(f"🔗 Посилання для друга (натисни, щоб скопіювати):\n`https://t.me/{me.username}?start=game_{game_id}`", parse_mode="Markdown")
        await state.finish()
    except: await message.answer("Введіть число.")

@dp.callback_query_handler(lambda c: c.data.startswith('tic_'))
async def tic_move(callback: types.CallbackQuery):
    _, gid, idx = callback.data.split("_")
    idx = int(idx)
    g = await games_col.find_one({"game_id": gid})
    
    if not g or g['status'] != "playing" or callback.from_user.id != g['turn']:
        return await callback.answer("Не твій хід!", show_alert=True)
    
    if g['board'][idx] != " ": return await callback.answer("Зайнято!")

    char = "X" if callback.from_user.id == g['creator_id'] else "O"
    new_board = list(g['board'])
    new_board[idx] = char
    winner = check_winner(new_board)
    next_player = g['opponent_id'] if callback.from_user.id == g['creator_id'] else g['creator_id']
    
    # Оновлення в БД
    await games_col.update_one({"game_id": gid}, {
        "$set": {"board": new_board, "turn": next_player, "last_move_time": time.time()}
    })
    
    # Отримуємо оновлені дані гри для повідомлень
    g['board'] = new_board
    g['turn'] = next_player
    
    if winner:
        await games_col.update_one({"game_id": gid}, {"$set": {"status": "finished"}})
        if winner == "draw":
            await users_col.update_many({"_id": {"$in": [g['creator_id'], g['opponent_id']]}}, {"$inc": {"balance": g['bet']}})
        else:
            wid = g['creator_id'] if winner == "X" else g['opponent_id']
            await users_col.update_one({"_id": wid}, {"$inc": {"balance": g['bet'] * 2}})
        await update_game_messages(g, new_board, winner=winner)
    else:
        await update_game_messages(g, new_board)
    
    await callback.answer()

@dp.message_handler(lambda m: m.text == "👤 Профіль")
async def profile(message: types.Message):
    user = await users_col.find_one({"_id": message.from_user.id})
    await message.answer(f"👤 Профіль\n🆔 ID: `{message.from_user.id}`\n💰 Баланс: {user['balance'] if user else 0} 💎", parse_mode="Markdown")

if __name__ == '__main__':
    loop = asyncio.get_event_loop()
    loop.create_task(check_game_timeouts())
    executor.start_polling(dp, skip_updates=True)
