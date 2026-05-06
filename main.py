import os
import uuid
import logging
from aiogram import Bot, Dispatcher, executor, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

load_dotenv()

# Налаштування
API_TOKEN = os.getenv("BOT_TOKEN")
MONGO_URL = os.getenv("MONGO_URL")
ADMIN_ID = os.getenv("ADMIN_ID") # Твій ID для сповіщень про вивід

logging.basicConfig(level=logging.INFO)

bot = Bot(token=API_TOKEN)
dp = Dispatcher(bot)

# Підключення до MongoDB Atlas
cluster = AsyncIOMotorClient(MONGO_URL)
db = cluster["fishcash_game"]
users_col = db["users"]
games_col = db["games"]

# --- Логіка Бази Даних ---

async def get_user(user_id, username):
    user = await users_col.find_one({"_id": user_id})
    if not user:
        user = {"_id": user_id, "username": username, "balance": 0}
        await users_col.insert_one(user)
    return user

# --- Ігрова логіка ---

def get_board_markup(game_id, board, turn_id):
    markup = InlineKeyboardMarkup(row_width=3)
    buttons = []
    for i in range(9):
        char = board[i] if board[i] != " " else "⬜️"
        buttons.append(InlineKeyboardButton(char, callback_data=f"cell_{game_id}_{i}"))
    markup.add(*buttons)
    return markup

def check_winner(b):
    win_coords = [(0,1,2), (3,4,5), (6,7,8), (0,3,6), (1,4,7), (2,5,8), (0,4,8), (2,4,6)]
    for r in win_coords:
        if b[r[0]] == b[r[1]] == b[r[2]] != " ":
            return b[r[0]]
    if " " not in b:
        return "draw"
    return None

# --- Обробники команд ---

@dp.message_handler(commands=['start'])
async def start(message: types.Message):
    user = await get_user(message.from_user.id, message.from_user.username)
    args = message.get_args()
    
    # Якщо гравець прийшов за посиланням на гру
    if args.startswith("game_"):
        game_id = args.split("_")[1]
        game = await games_col.find_one({"game_id": game_id, "status": "waiting"})
        
        if not game:
            return await message.answer("❌ Гра вже почалася або видалена.")
        if game['creator_id'] == message.from_user.id:
            return await message.answer("Ви не можете грати самі з собою.")
        if user['balance'] < game['bet']:
            return await message.answer(f"❌ Недостатньо 💎. Потрібно {game['bet']} 💎")

        # Початок гри
        await users_col.update_one({"_id": message.from_user.id}, {"$inc": {"balance": -game['bet']}})
        await games_col.update_one({"game_id": game_id}, {"$set": {"opponent_id": message.from_user.id, "status": "playing", "turn": game['creator_id']}})
        
        await message.answer("🎮 Гра почалася! Ви граєте за 'O'. Хід вашого суперника.")
        await bot.send_message(game['creator_id'], "🎮 Суперник приєднався! Ваш хід (X).", reply_markup=get_board_markup(game_id, game['board'], game['creator_id']))
        return

    await message.answer(f"Привіт! Твій баланс: {user['balance']} 💎\n\n"
                         "Створити гру: `/tictactoe [ставка]`\n"
                         "Мінімум 5 💎. Вивід від 200 💎", parse_mode="Markdown")

@dp.message_handler(commands=['tictactoe'])
async def create_game(message: types.Message):
    user = await get_user(message.from_user.id, message.from_user.username)
    try:
        bet = int(message.get_args())
        if bet < 5: return await message.answer("❌ Мінімум 5 💎")
        if user['balance'] < bet: return await message.answer("❌ Недостатньо 💎")
        
        game_id = str(uuid.uuid4())[:8]
        new_game = {
            "game_id": game_id,
            "creator_id": message.from_user.id,
            "opponent_id": None,
            "bet": bet,
            "board": [" "] * 9,
            "status": "waiting",
            "turn": None
        }
        
        await users_col.update_one({"_id": message.from_user.id}, {"$inc": {"balance": -bet}})
        await games_col.insert_one(new_game)
        
        bot_user = await bot.get_me()
        link = f"https://t.me/{bot_user.username}?start=game_{game_id}"
        await message.answer(f"💎 Ставка: {bet}\nВідправ посилання другу:\n`{link}`", parse_mode="Markdown")
        
    except:
        await message.answer("Напиши: `/tictactoe 5`", parse_mode="Markdown")

@dp.callback_query_handler(lambda c: c.data.startswith('cell_'))
async def process_move(callback: types.CallbackQuery):
    _, game_id, cell_idx = callback.data.split("_")
    cell_idx = int(cell_idx)
    game = await games_col.find_one({"game_id": game_id})
    
    if not game or game['status'] != "playing" or callback.from_user.id != game['turn']:
        return await callback.answer("Зараз не твій хід!")
    
    if game['board'][cell_idx] != " ":
        return await callback.answer("Клітинка зайнята!")

    # Робимо хід
    char = "X" if callback.from_user.id == game['creator_id'] else "O"
    new_board = game['board']
    new_board[cell_idx] = char
    
    next_turn = game['opponent_id'] if callback.from_user.id == game['creator_id'] else game['creator_id']
    
    winner = check_winner(new_board)
    
    if winner:
        await games_col.update_one({"game_id": game_id}, {"$set": {"status": "finished", "board": new_board}})
        if winner == "draw":
            await users_col.update_one({"_id": game['creator_id']}, {"$inc": {"balance": game['bet']}})
            await users_col.update_one({"_id": game['opponent_id']}, {"$inc": {"balance": game['bet']}})
            res_text = "🤝 Нічия! Ставки повернуто."
        else:
            win_id = game['creator_id'] if winner == "X" else game['opponent_id']
            await users_col.update_one({"_id": win_id}, {"$inc": {"balance": game['bet'] * 2}})
            res_text = f"🎉 Переміг гравць {winner}! Виграш: {game['bet']*2} 💎"
        
        await bot.edit_message_text(res_text, callback.message.chat.id, callback.message.message_id, reply_markup=get_board_markup(game_id, new_board, None))
        await bot.send_message(next_turn, res_text)
    else:
        await games_col.update_one({"game_id": game_id}, {"$set": {"board": new_board, "turn": next_turn}})
        await bot.edit_message_reply_markup(callback.message.chat.id, callback.message.message_id, reply_markup=get_board_markup(game_id, new_board, next_turn))
        await callback.answer()

if __name__ == '__main__':
    executor.start_polling(dp, skip_updates=True)
