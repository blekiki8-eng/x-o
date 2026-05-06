import os
import uuid
import logging
from aiogram import Bot, Dispatcher, executor, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

load_dotenv()

# Налаштування (Змінні в Railway)
API_TOKEN = os.getenv("BOT_TOKEN")
MONGO_URL = os.getenv("MONGO_URL")

logging.basicConfig(level=logging.INFO)
bot = Bot(token=API_TOKEN)
dp = Dispatcher(bot)

# З'єднання з базою
cluster = AsyncIOMotorClient(MONGO_URL)
db = cluster["fishcash_game"]
users_col = db["users"]
games_col = db["games"]

# --- Допоміжні функції ---

async def get_user(user_id, username):
    user = await users_col.find_one({"_id": user_id})
    if not user:
        user = {"_id": user_id, "username": username, "balance": 0}
        await users_col.insert_one(user)
    return user

def get_board_markup(game_id, board):
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
    if " " not in b: return "draw"
    return None

# --- Обробники ---

@dp.message_handler(commands=['start'])
async def start(message: types.Message):
    user = await get_user(message.from_user.id, message.from_user.username or "Гравець")
    args = message.get_args()
    
    if args.startswith("game_"):
        game_id = args.split("_")[1]
        game = await games_col.find_one({"game_id": game_id, "status": "waiting"})
        
        if not game:
            return await message.answer("❌ Гра не знайдена або вже почалася.")
        if game['creator_id'] == message.from_user.id:
            return await message.answer("❌ Це ваше посилання. Перешліть його другу!")
        if user['balance'] < game['bet']:
            return await message.answer(f"❌ Недостатньо 💎. Треба {game['bet']}, а у вас {user['balance']}.")

        # Знімаємо ставку з другого гравця
        await users_col.update_one({"_id": message.from_user.id}, {"$inc": {"balance": -game['bet']}})
        await games_col.update_one({"game_id": game_id}, {
            "$set": {"opponent_id": message.from_user.id, "status": "playing", "turn": game['creator_id']}
        })
        
        await message.answer(f"🎮 Гра почалася! Ставка: {game['bet']} 💎. Ви граєте за 'O'.")
        await bot.send_message(game['creator_id'], "✅ Суперник приєднався! Ваш хід (X).", 
                               reply_markup=get_board_markup(game_id, game['board']))
        return

    await message.answer(f"💎 Баланс: {user['balance']} 💎\n\n"
                         "🕹 /tictactoe [ставка] — створити гру\n"
                         "💰 Вивід від 200 💎")

@dp.message_handler(commands=['tictactoe'])
async def create_game(message: types.Message):
    user = await get_user(message.from_user.id, message.from_user.username)
    try:
        bet = int(message.get_args())
        if bet < 5: return await message.answer("❌ Мінімальна ставка 5 💎")
        if user['balance'] < bet: return await message.answer("❌ Недостатньо 💎 на балансі")
        
        game_id = str(uuid.uuid4())[:8]
        new_game = {
            "game_id": game_id, "creator_id": message.from_user.id, "opponent_id": None,
            "bet": bet, "board": [" "] * 9, "status": "waiting", "turn": None
        }
        
        await users_col.update_one({"_id": message.from_user.id}, {"$inc": {"balance": -bet}})
        await games_col.insert_one(new_game)
        
        bot_info = await bot.get_me()
        link = f"https://t.me/{bot_info.username}?start=game_{game_id}"
        await message.answer(f"🎲 Гра створена!\n💰 Ставка: {bet} 💎\n\nНадішли посилання другу:\n`{link}`", parse_mode="Markdown")
    except:
        await message.answer("Введіть ставку, наприклад: `/tictactoe 10`", parse_mode="Markdown")

@dp.callback_query_handler(lambda c: c.data.startswith('cell_'))
async def process_move(callback: types.CallbackQuery):
    _, game_id, cell_idx = callback.data.split("_")
    cell_idx = int(cell_idx)
    game = await games_col.find_one({"game_id": game_id})
    
    if not game or game['status'] != "playing" or callback.from_user.id != game['turn']:
        return await callback.answer("Зараз не твій хід або гра закінчена!")
    
    if game['board'][cell_idx] != " ":
        return await callback.answer("Клітинка вже зайнята!")

    char = "X" if callback.from_user.id == game['creator_id'] else "O"
    new_board = list(game['board'])
    new_board[cell_idx] = char
    
    next_player = game['opponent_id'] if callback.from_user.id == game['creator_id'] else game['creator_id']
    winner = check_winner(new_board)
    
    if winner:
        await games_col.update_one({"game_id": game_id}, {"$set": {"status": "finished", "board": new_board}})
        if winner == "draw":
            await users_col.update_one({"_id": game['creator_id']}, {"$inc": {"balance": game['bet']}})
            await users_col.update_one({"_id": game['opponent_id']}, {"$inc": {"balance": game['bet']}})
            msg = "🤝 Нічия! Ставки повернуті."
        else:
            win_id = game['creator_id'] if winner == "X" else game['opponent_id']
            await users_col.update_one({"_id": win_id}, {"$inc": {"balance": game['bet'] * 2}})
            msg = f"🎉 Гравець {char} виграв {game['bet'] * 2} 💎!"
        
        await bot.edit_message_text(msg, callback.message.chat.id, callback.message.message_id, reply_markup=get_board_markup(game_id, new_board))
        await bot.send_message(next_player, msg)
    else:
        await games_col.update_one({"game_id": game_id}, {"$set": {"board": new_board, "turn": next_player}})
        await bot.edit_message_reply_markup(callback.message.chat.id, callback.message.message_id, reply_markup=get_board_markup(game_id, new_board))

if __name__ == '__main__':
    executor.start_polling(dp, skip_updates=True)
