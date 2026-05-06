import os
import uuid
import logging
import asyncio
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

EXCHANGE_RATE = 44.5  # 1💎 = 44.5₴
BONUS_PERCENT = 0.05  # +5% до суми

logging.basicConfig(level=logging.INFO)
storage = MemoryStorage()
bot = Bot(token=API_TOKEN)
dp = Dispatcher(bot, storage=storage)

# Підключення до БД
cluster = AsyncIOMotorClient(MONGO_URL)
db = cluster["fishcash_game"]
users_col = db["users"]
games_col = db["games"]

# --- Стани (FSM) ---
class DepositState(StatesGroup):
    wait_amount = State()
    wait_receipt = State()

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

def admin_confirm_kb(user_id, amount):
    markup = InlineKeyboardMarkup()
    markup.add(
        InlineKeyboardButton("✅ Прийняти", callback_data=f"adm_confirm_{user_id}_{amount}"),
        InlineKeyboardButton("❌ Відхилити", callback_data=f"adm_reject_{user_id}")
    )
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

# --- Обробники Команд ---
@dp.message_handler(commands=['start'])
async def start_cmd(message: types.Message):
    await get_user(message.from_user.id, message.from_user.username)
    args = message.get_args()
    
    if args and args.startswith("game_"):
        game_id = args.split("_")[1]
        game = await games_col.find_one({"game_id": game_id, "status": "waiting"})
        
        if not game: return await message.answer("❌ Гра не знайдена.")
        if game['creator_id'] == message.from_user.id: return await message.answer("❌ Не можна грати з собою.")
        
        user = await get_user(message.from_user.id)
        if user['balance'] < game['bet']: return await message.answer("❌ Недостатньо 💎.")

        await users_col.update_one({"_id": message.from_user.id}, {"$inc": {"balance": -game['bet']}})
        await games_col.update_one({"game_id": game_id}, {"$set": {"opponent_id": message.from_user.id, "status": "playing", "turn": game['creator_id']}})
        
        await message.answer(f"🎮 Гра почалася!", reply_markup=main_menu())
        await bot.send_message(game['creator_id'], "✅ Суперник приєднався! Твій хід (X):", reply_markup=get_board_markup(game_id, game['board']))
        return

    await message.answer("Вітаємо у FishCash! 💎", reply_markup=main_menu())

# --- Кнопки меню ---
@dp.message_handler(lambda m: m.text == "👤 Профіль")
async def profile_view(message: types.Message):
    user = await get_user(message.from_user.id)
    await message.answer(f"👤 **Профіль**\n\n🆔 ID: `{user['_id']}`\n💰 Баланс: {user['balance']} 💎", parse_mode="Markdown")

@dp.message_handler(lambda m: m.text == "💎 Баланс")
async def balance_view(message: types.Message):
    user = await get_user(message.from_user.id)
    await message.answer(f"💎 **Ваш баланс: {user['balance']} 💎**", reply_markup=balance_keyboard(), parse_mode="Markdown")

@dp.message_handler(lambda m: m.text == "🎮 Ігри")
async def games_view(message: types.Message):
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("❌ Хрестики-Нолики ⭕️", callback_data="create_tic"))
    await message.answer("Оберіть гру:", reply_markup=markup)

# --- Поповнення ---
@dp.callback_query_handler(lambda c: c.data == "deposit")
async def deposit_init(callback: types.CallbackQuery):
    await bot.send_message(callback.from_user.id, "💰 Введіть суму в 💎:")
    await DepositState.wait_amount.set()
    await callback.answer()

@dp.message_handler(state=DepositState.wait_amount)
async def deposit_amount(message: types.Message, state: FSMContext):
    try:
        amt = float(message.text)
        if amt <= 0: return await message.answer("❌ Більше 0!")
        
        total_uah = (amt * EXCHANGE_RATE) * (1 + BONUS_PERCENT)
        await state.update_data(amount=amt)
        
        msg = (f"📑 **Заявка: {amt} 💎**\n"
               f"📈 Курс: 1 💎 = {EXCHANGE_RATE}₴ (+5% бонус)\n"
               f"💵 **До оплати: {total_uah:.2f}₴**\n\n"
               f"💳 Картка: `5355 2800 2890 2177`\n\n"
               f"📸 **Надішліть фото чека:**")
        await message.answer(msg, parse_mode="Markdown")
        await DepositState.wait_receipt.set()
    except: await message.answer("❌ Введіть число.")

@dp.message_handler(state=DepositState.wait_receipt, content_types=['photo'])
async def deposit_receipt(message: types.Message, state: FSMContext):
    data = await state.get_data()
    await bot.send_photo(ADMIN_ID, message.photo[-1].file_id, 
                         caption=f"🔔 Чек!\nЮзер: @{message.from_user.username}\nID: `{message.from_user.id}`\n💎 Сума: {data['amount']}",
                         reply_markup=admin_confirm_kb(message.from_user.id, data['amount']), parse_mode="Markdown")
    await message.answer("✅ Чек надіслано адміну!")
    await state.finish()

# --- Адмінка ---
@dp.callback_query_handler(lambda c: c.data.startswith('adm_'))
async def admin_decision(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID: return await callback.answer("Зась!")
    
    p = callback.data.split("_")
    action, uid = p[1], int(p[2])
    
    if action == "confirm":
        amt = float(p[3])
        await users_col.update_one({"_id": uid}, {"$inc": {"balance": amt}})
        try: await bot.send_message(uid, f"✅ Баланс поповнено на {amt} 💎!")
        except: pass
        await callback.message.edit_caption(callback.message.caption + "\n\n✅ ПРИЙНЯТО")
    else:
        try: await bot.send_message(uid, "❌ Чек відхилено.")
        except: pass
        await callback.message.edit_caption(callback.message.caption + "\n\n❌ ВІДХИЛЕНО")
    await callback.answer()

# --- Гра ---
@dp.message_handler(commands=['tictactoe'])
async def tic_create(message: types.Message):
    user = await get_user(message.from_user.id)
    try:
        bet = int(message.get_args())
        if user['balance'] < bet: return await message.answer("❌ Недостатньо 💎")
        gid = str(uuid.uuid4())[:8]
        await users_col.update_one({"_id": message.from_user.id}, {"$inc": {"balance": -bet}})
        await games_col.insert_one({"game_id": gid, "creator_id": message.from_user.id, "opponent_id": None, "bet": bet, "board": [" "] * 9, "status": "waiting", "turn": None})
        me = await bot.get_me()
        await message.answer(f"🎲 Гра на {bet} 💎\nЛінк: `https://t.me/{me.username}?start=game_{gid}`", parse_mode="Markdown")
    except: await message.answer("`/tictactoe 10`")

@dp.callback_query_handler(lambda c: c.data == "create_tic")
async def tic_btn(callback: types.CallbackQuery):
    await bot.send_message(callback.from_user.id, "Введіть: `/tictactoe [ставка]`")
    await callback.answer()

@dp.callback_query_handler(lambda c: c.data.startswith('tic_'))
async def tic_move(callback: types.CallbackQuery):
    _, gid, idx = callback.data.split("_")
    idx, g = int(idx), await games_col.find_one({"game_id": gid})
    
    if not g or g['status'] != "playing" or callback.from_user.id != g['turn']:
        return await callback.answer("Не твій хід!")
    if g['board'][idx] != " ": return await callback.answer("Зайнято!")

    char = "X" if callback.from_user.id == g['creator_id'] else "O"
    new_b = list(g['board'])
    new_b[idx] = char
    win = check_winner(new_b)
    nxt = g['opponent_id'] if callback.from_user.id == g['creator_id'] else g['creator_id']
    
    if win:
        await games_col.update_one({"game_id": gid}, {"$set": {"status": "finished", "board": new_b}})
        if win == "draw":
            await users_col.update_many({"_id": {"$in": [g['creator_id'], g['opponent_id']]}}, {"$inc": {"balance": g['bet']}})
            res = "🤝 Нічия! Кошти повернуто."
        else:
            wid = g['creator_id'] if win == "X" else g['opponent_id']
            await users_col.update_one({"_id": wid}, {"$inc": {"balance": g['bet'] * 2}})
            res = f"🎉 {char} виграв {g['bet']*2} 💎!"
        await bot.edit_message_text(res, callback.message.chat.id, callback.message.message_id, reply_markup=get_board_markup(gid, new_b))
    else:
        await games_col.update_one({"game_id": gid}, {"$set": {"board": new_b, "turn": nxt}})
        await bot.edit_message_reply_markup(callback.message.chat.id, callback.message.message_id, reply_markup=get_board_markup(gid, new_b))

if __name__ == '__main__':
    executor.start_polling(dp, skip_updates=True)
