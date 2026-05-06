import os
import uuid
import logging
from aiogram import Bot, Dispatcher, executor, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
# ... (інші імпорти залишаються)

# Додай у globals:
games_col = db["games"]  # Колекція для ігор

# --- Функції для гри ---

def get_board_markup(game_id, board):
    markup = InlineKeyboardMarkup(row_width=3)
    buttons = []
    for i in range(9):
        char = board[i] if board[i] != " " else "⬜️"
        buttons.append(InlineKeyboardButton(char, callback_data=f"tic_{game_id}_{i}"))
    markup.add(*buttons)
    return markup

def check_winner(b):
    win_coords = [(0,1,2), (3,4,5), (6,7,8), (0,3,6), (1,4,7), (2,5,8), (0,4,8), (2,4,6)]
    for r in win_coords:
        if b[r[0]] == b[r[1]] == b[r[2]] != " ":
            return b[r[0]]
    if " " not in b: return "draw"
    return None

# --- Обробники меню Ігор ---

@dp.message_handler(lambda m: m.text == "🎮 Ігри")
async def games_menu(message: types.Message):
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("❌ Хрестики-Нолики ⭕️", callback_data="create_tic"))
    await message.answer("Оберіть гру:", reply_markup=markup)

@dp.callback_query_handler(lambda c: c.data == "create_tic")
async def ask_bet(callback: types.CallbackQuery):
    await bot.send_message(callback.from_user.id, "Введіть суму ставки (💎):")
    # Тут можна додати стан очікування ставки, але для простоти використаємо команду
    await bot.send_message(callback.from_user.id, "Або використайте команду: `/tictactoe [ставка]`", parse_mode="Markdown")
    await callback.answer()

@dp.message_handler(commands=['tictactoe'])
async def create_game(message: types.Message):
    user = await get_user(message.from_user.id)
    try:
        bet = int(message.get_args())
        if bet < 1: return await message.answer("❌ Мінімальна ставка — 1 💎")
        if user['balance'] < bet: return await message.answer("❌ Недостатньо балансу!")
        
        game_id = str(uuid.uuid4())[:8]
        new_game = {
            "game_id": game_id, "creator_id": message.from_user.id, "opponent_id": None,
            "bet": bet, "board": [" "] * 9, "status": "waiting", "turn": None
        }
        
        # Списуємо ставку відразу
        await users_col.update_one({"_id": message.from_user.id}, {"$inc": {"balance": -bet}})
        await games_col.insert_one(new_game)
        
        bot_info = await bot.get_me()
        link = f"https://t.me/{bot_info.username}?start=game_{game_id}"
        await message.answer(f"🎮 **Гра створена!**\n💰 Ставка: {bet} 💎\n\nНадішліть посилання другу:\n`{link}`", parse_mode="Markdown")
    except:
        await message.answer("Вкажіть ставку. Приклад: `/tictactoe 10`")

# --- Обробка ходів ---

@dp.callback_query_handler(lambda c: c.data.startswith('tic_'))
async def game_move(callback: types.CallbackQuery):
    _, game_id, cell_idx = callback.data.split("_")
    cell_idx = int(cell_idx)
    game = await games_col.find_one({"game_id": game_id})
    
    if not game or game['status'] != "playing":
        return await callback.answer("Гра вже завершена!")
    
    if callback.from_user.id != game['turn']:
        return await callback.answer("Зараз не твій хід!", show_alert=True)
    
    if game['board'][cell_idx] != " ":
        return await callback.answer("Клітинка вже зайнята!")

    # Робимо хід
    char = "X" if callback.from_user.id == game['creator_id'] else "O"
    new_board = list(game['board'])
    new_board[cell_idx] = char
    
    winner = check_winner(new_board)
    next_player = game['opponent_id'] if callback.from_user.id == game['creator_id'] else game['creator_id']
    
    if winner:
        await games_col.update_one({"game_id": game_id}, {"$set": {"status": "finished", "board": new_board}})
        if winner == "draw":
            await users_col.update_one({"_id": game['creator_id']}, {"$inc": {"balance": game['bet']}})
            await users_col.update_one({"_id": game['opponent_id']}, {"$inc": {"balance": game['bet']}})
            text = f"🤝 Нічия! Ставки повернуті."
        else:
            win_id = game['creator_id'] if winner == "X" else game['opponent_id']
            await users_col.update_one({"_id": win_id}, {"$inc": {"balance": game['bet'] * 2}})
            text = f"🎉 Гравець {char} переміг і забрав {game['bet'] * 2} 💎!"
        
        await bot.edit_message_text(text, callback.message.chat.id, callback.message.message_id, reply_markup=get_board_markup(game_id, new_board))
    else:
        await games_col.update_one({"game_id": game_id}, {"$set": {"board": new_board, "turn": next_player}})
        await bot.edit_message_reply_markup(callback.message.chat.id, callback.message.message_id, reply_markup=get_board_markup(game_id, new_board))
