import os
import uuid
import logging
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

logging.basicConfig(level=logging.INFO)
storage = MemoryStorage()
bot = Bot(token=API_TOKEN)
dp = Dispatcher(bot, storage=storage)

cluster = AsyncIOMotorClient(MONGO_URL)
db = cluster["fishcash_game"]
users_col = db["users"]
games_col = db["games"]

# --- Стани (FSM) ---
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

def balance_keyboard():
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton("💳 Поповнення", callback_data="deposit"),
        InlineKeyboardButton("📤 Виведення", callback_data="withdraw")
    )
    return markup

def get_board_markup(game_id, board):
    markup = InlineKeyboardMarkup(row_width=3)
    buttons = []
    for i in range(9):
        char = board[i] if board[i] != " " else "⬜️"
        buttons.append(InlineKeyboardButton(char, callback_data=f"tic_{game_id}_{i}"))
    markup.add(*buttons)
    return markup

# --- Допоміжні функції ---
async def get_user(user_id, username="Гравець"):
    user = await users_col.find_one({"_id": user_id})
    if not user:
        user = {"_id": user_id, "username": username, "balance": 0}
        await users_col.insert_one(user)
    return user

def check_winner(b):
    win_coords = [(0,1,2), (3,4,5), (6,7,8), (0,3,6), (1,4,7), (2,5,8), (0,4,8), (2,4,6)]
    for r in win_coords:
        if b[r[0]] == b[r[1]] == b[r[2]] != " ": return b[r[0]]
    if " " not in b: return "draw"
    return None

# --- Старт та Профіль ---
@dp.message_handler(commands=['start'])
async def start_cmd(message: types.Message):
    await get_user(message.from_user.id, message.from_user.username)
    args = message.get_args()
    
    if args and args.startswith("game_"):
        game_id = args.split("_")[1]
        game = await games_col.find_one({"game_id": game_id, "status": "waiting"})
        
        if not game: return await message.answer("❌ Гра не знайдена або вже почалася.")
        if game['creator_id'] == message.from_user.id: return await message.answer("❌ Ви не можете грати самі з собою.")
        
        user = await get_user(message.from_user.id)
        if user['balance'] < game['bet']: return await message.answer(f"❌ Недостатньо балансу для ставки {game['bet']} 💎")

        await users_col.update_one({"_id": message.from_user.id}, {"$inc": {"balance": -game['bet']}})
        await games_col.update_one({"game_id": game_id}, {"$set": {"opponent_id": message.from_user.id, "status": "playing", "turn": game['creator_id']}})
        
        await message.answer(f"🎮 Гра почалася! Ставка: {game['bet']} 💎", reply_markup=main_menu())
        await bot.send_message(game['creator_id'], f"✅ Суперник знайдений! Твій хід (X):", reply_markup=get_board_markup(game_id, game['board']))
        return

    await message.answer("💎 Вітаємо у FishCash!", reply_markup=main_menu())

@dp.message_handler(lambda m: m.text == "👤 Профіль")
async def profile_view(message: types.Message):
    user = await get_user(message.from_user.id)
    await message.answer(f"👤 **Профіль**\n\n🆔 ID: `{user['_id']}`\n💰 Баланс: {user['balance']} 💎", parse_mode="Markdown")

@dp.message_handler(lambda m: m.text == "💎 Баланс")
async def balance_view(message: types.Message):
    user = await get_user(message.from_user.id)
    await message.answer(f"💎 **Ваш баланс: {user['balance']} 💎**", reply_markup=balance_keyboard(), parse_mode="Markdown")

# --- Логіка Ігор (Новий ланцюжок) ---
@dp.message_handler(lambda m: m.text == "🎮 Ігри")
async def games_view(message: types.Message):
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("❌ Хрестики-Нолики ⭕️", callback_data="tic_info"))
    await message.answer("Оберіть гру:", reply_markup=markup)

@dp.callback_query_handler(lambda c: c.data == "tic_info")
async def tic_select_mode(callback: types.CallbackQuery):
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("🟩 Матч 3х3", callback_data="tic_mode_3x3"))
    await bot.send_message(callback.from_user.id, "🕹 Виберіть тип матчу на Хрестики-Нолики:", reply_markup=markup)
    await callback.answer()

@dp.callback_query_handler(lambda c: c.data == "tic_mode_3x3")
async def tic_ask_bet(callback: types.CallbackQuery):
    await bot.send_message(callback.from_user.id, "💰 **Виберіть ставку для гри:**\n_(Мінімум: 0.05 💎)_", parse_mode="Markdown")
    await GameState.wait_bet.set()
    await callback.answer()

@dp.message_handler(state=GameState.wait_bet)
async def tic_create_link(message: types.Message, state: FSMContext):
    user = await get_user(message.from_user.id)
    try:
        bet = float(message.text.replace(',', '.'))
        if bet < 0.05:
            return await message.answer("❌ Мінімальна ставка — 0.05 💎")
        if user['balance'] < bet:
            return await message.answer(f"❌ Недостатньо балансу! У вас: {user['balance']} 💎")
        
        game_id = str(uuid.uuid4())[:8]
        # Списуємо ставку
        await users_col.update_one({"_id": message.from_user.id}, {"$inc": {"balance": -bet}})
        
        # Створюємо гру в БД
        await games_col.insert_one({
            "game_id": game_id,
            "creator_id": message.from_user.id,
            "opponent_id": None,
            "bet": bet,
            "board": [" "] * 9,
            "status": "waiting",
            "turn": None
        })
        
        me = await bot.get_me()
        link = f"https://t.me/{me.username}?start=game_{game_id}"
        
        await message.answer(
            f"✅ **Гра створена!**\n💰 Ставка: {bet} 💎\n\n⬇️ **Надішліть це посилання другу:**\n`{link}`",
            parse_mode="Markdown"
        )
        await state.finish()
        
    except ValueError:
        await message.answer("❌ Будь ласка, введіть число (наприклад: 0.5 або 10)")

# --- Обробка ходів (Tic-Tac-Toe) ---
@dp.callback_query_handler(lambda c: c.data.startswith('tic_'))
async def tic_move(callback: types.CallbackQuery):
    _, gid, idx = callback.data.split("_")
    idx, g = int(idx), await games_col.find_one({"game_id": gid})
    
    if not g or g['status'] != "playing" or callback.from_user.id != g['turn']:
        return await callback.answer("Не твій хід або гра завершена!", show_alert=True)
    
    if g['board'][idx] != " ": 
        return await callback.answer("Ця клітинка вже зайнята!")

    char = "X" if callback.from_user.id == g['creator_id'] else "O"
    new_b = list(g['board'])
    new_b[idx] = char
    win = check_winner(new_b)
    nxt = g['opponent_id'] if callback.from_user.id == g['creator_id'] else g['creator_id']
    
    if win:
        await games_col.update_one({"game_id": gid}, {"$set": {"status": "finished", "board": new_b}})
        if win == "draw":
            await users_col.update_many({"_id": {"$in": [g['creator_id'], g['opponent_id']]}}, {"$inc": {"balance": g['bet']}})
            res = f"🤝 **Нічия!**\nКошти повернуті обом гравцям."
        else:
            wid = g['creator_id'] if win == "X" else g['opponent_id']
            await users_col.update_one({"_id": wid}, {"$inc": {"balance": g['bet'] * 2}})
            res = f"🎉 Гравець **{char}** переміг!\n💰 Виграш: {g['bet'] * 2} 💎"
        
        await bot.edit_message_text(res, callback.message.chat.id, callback.message.message_id, 
                                   reply_markup=get_board_markup(gid, new_b), parse_mode="Markdown")
    else:
        await games_col.update_one({"game_id": gid}, {"$set": {"board": new_b, "turn": nxt}})
        await bot.edit_message_reply_markup(callback.message.chat.id, callback.message.message_id, 
                                           reply_markup=get_board_markup(gid, new_b))

# --- Поповнення (Адмін-система) ---
@dp.callback_query_handler(lambda c: c.data == "deposit")
async def deposit_init(callback: types.CallbackQuery):
    await bot.send_message(callback.from_user.id, "💰 Введіть суму поповнення в 💎:")
    await DepositState.wait_amount.set()
    await callback.answer()

@dp.message_handler(state=DepositState.wait_amount)
async def deposit_amt(message: types.Message, state: FSMContext):
    try:
        amt = float(message.text.replace(',', '.'))
        if amt <= 0: return await message.answer("Сума має бути більше 0")
        total_uah = (amt * EXCHANGE_RATE) * (1 + BONUS_PERCENT)
        await state.update_data(amount=amt)
        await message.answer(f"📑 Заявка: {amt} 💎\n💵 До оплати: **{total_uah:.2f}₴**\n\n💳 `5355 2800 2890 2177`\n\n📸 Надішліть фото чека:", parse_mode="Markdown")
        await DepositState.wait_receipt.set()
    except: await message.answer("Введіть число.")

@dp.message_handler(state=DepositState.wait_receipt, content_types=['photo'])
async def deposit_res(message: types.Message, state: FSMContext):
    data = await state.get_data()
    markup = InlineKeyboardMarkup().add(
        InlineKeyboardButton("✅ OK", callback_data=f"adm_confirm_{message.from_user.id}_{data['amount']}"),
        InlineKeyboardButton("❌ NO", callback_data=f"adm_reject_{message.from_user.id}")
    )
    await bot.send_photo(ADMIN_ID, message.photo[-1].file_id, 
                         caption=f"🔔 Чек! Юзер: @{message.from_user.username}\nСума: {data['amount']} 💎", reply_markup=markup)
    await message.answer("✅ Чек надіслано! Очікуйте.")
    await state.finish()

@dp.callback_query_handler(lambda c: c.data.startswith('adm_'))
async def admin_action(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID: return
    p = callback.data.split("_")
    if p[1] == "confirm":
        await users_col.update_one({"_id": int(p[2])}, {"$inc": {"balance": float(p[3])}})
        await bot.send_message(int(p[2]), f"✅ Баланс поповнено на {p[3]} 💎!")
    else:
        await bot.send_message(int(p[2]), "❌ Чек відхилено.")
    await callback.message.edit_caption(callback.message.caption + "\n\nОброблено.")
    await callback.answer()

if __name__ == '__main__':
    executor.start_polling(dp, skip_updates=True)
