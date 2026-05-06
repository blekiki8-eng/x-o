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

# --- Конфігурація ---
API_TOKEN = os.getenv("BOT_TOKEN")
MONGO_URL = os.getenv("MONGO_URL")
ADMIN_ID = int(os.getenv("ADMIN_ID")) if os.getenv("ADMIN_ID") else 0

MOVE_TIMEOUT = 40 
EXCHANGE_RATE = 44.50
BONUS_PERCENT = 0.05
MIN_DEPOSIT = 0.50
MIN_WITHDRAW = 4.0

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

class WithdrawState(StatesGroup):
    wait_amount = State()
    wait_details = State()

class GameState(StatesGroup):
    wait_bet = State()

# --- Клавіатури ---
def main_menu():
    keyboard = ReplyKeyboardMarkup(resize_keyboard=True)
    keyboard.add(KeyboardButton("👤 Профіль"), KeyboardButton("🎮 Ігри"))
    keyboard.add(KeyboardButton("💎 Баланс"))
    return keyboard

def cancel_keyboard():
    markup = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    markup.add(KeyboardButton("❌ Скасувати"))
    return markup

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
                    text = "🤝 **Нічия! Ставки повернуті.**"
                else:
                    symbol = "X" if uid == game['creator_id'] else "O"
                    if winner == symbol:
                        text = "🏁 **Гру завершено!**"
                        await bot.send_message(uid, f"🎉 **Вітаю з перемогою!**\n💰 Виграш {round(game['bet']*2, 2)} 💎 нарахований на твій баланс!", parse_mode="Markdown")
                    else:
                        text = "❌ **Ви програли. Наступного разу пощастить!**"
                await bot.edit_message_text(text, uid, mid, reply_markup=markup, parse_mode="Markdown")
            else:
                text = "🎮 **Твій хід! (Залишилось 40 сек.)**" if uid == game['turn'] else "⏳ **Хід суперника... Очікуйте.**"
                await bot.edit_message_text(text, uid, mid, reply_markup=markup, parse_mode="Markdown")
        except: pass

async def check_game_timeouts():
    while True:
        try:
            current_time = time.time()
            async for game in games_col.find({"status": "playing", "last_move_time": {"$exists": True}}):
                if current_time - game['last_move_time'] > MOVE_TIMEOUT:
                    winner_id = game['opponent_id'] if game['turn'] == game['creator_id'] else game['creator_id']
                    win_char = "O" if winner_id == game['opponent_id'] else "X"
                    await games_col.update_one({"game_id": game['game_id']}, {"$set": {"status": "finished"}})
                    await users_col.update_one({"_id": winner_id}, {"$inc": {"balance": game['bet'] * 2}})
                    await update_game_messages(game, game['board'], winner=win_char)
        except Exception as e:
            logging.error(f"Timer error: {e}")
        await asyncio.sleep(5)

# --- Обробник скасування ---
@dp.message_handler(state="*", text="❌ Скасувати")
async def cancel_handler(message: types.Message, state: FSMContext):
    await state.finish()
    await message.answer("Дію скасовано.", reply_markup=main_menu())

# --- Обробники ---
@dp.message_handler(commands=['start'])
async def start_cmd(message: types.Message):
    user = await users_col.find_one({"_id": message.from_user.id})
    if not user:
        await users_col.insert_one({"_id": message.from_user.id, "username": message.from_user.username, "balance": 0.0})
    
    args = message.get_args()
    if args and args.startswith("game_"):
        game_id = args.split("_")[1]
        game = await games_col.find_one({"game_id": game_id, "status": "waiting"})
        if not game: return await message.answer("❌ Гра не знайдена.")
        if game['creator_id'] == message.from_user.id: return await message.answer("❌ Не можна грати з собою.")
        user = await users_col.find_one({"_id": message.from_user.id})
        if user['balance'] < game['bet']: return await message.answer("❌ Недостатньо балансу.")
        await users_col.update_one({"_id": message.from_user.id}, {"$inc": {"balance": -game['bet']}})
        msg_opp = await message.answer("🎮 Завантаження...")
        update_data = {"opponent_id": message.from_user.id, "opponent_msg_id": msg_opp.message_id, "status": "playing", "turn": game['creator_id'], "last_move_time": time.time()}
        await games_col.update_one({"game_id": game_id}, {"$set": update_data})
        game.update(update_data)
        await update_game_messages(game, game['board'])
        return
    await message.answer("Вітаю! Натискайте на кнопки", reply_markup=main_menu())

@dp.message_handler(lambda m: m.text == "💎 Баланс")
async def balance_view(message: types.Message):
    user = await users_col.find_one({"_id": message.from_user.id})
    balance = round(user['balance'], 2) if user else 0
    markup = InlineKeyboardMarkup(row_width=2).add(
        InlineKeyboardButton("💳 Поповнення", callback_data="deposit"), 
        InlineKeyboardButton("📤 Виведення", callback_data="withdraw_start")
    )
    await message.answer(f"💎 **Ваш баланс: {balance} 💎**", reply_markup=markup, parse_mode="Markdown")

@dp.message_handler(lambda m: m.text == "👤 Профіль")
async def profile_v(message: types.Message):
    user = await users_col.find_one({"_id": message.from_user.id})
    balance = round(user['balance'], 2) if user else 0
    await message.answer(f"👤 Профіль\n🆔 ID: `{message.from_user.id}`\n💰 Баланс: {balance} 💎", parse_mode="Markdown")

@dp.message_handler(lambda m: m.text == "🎮 Ігри")
async def games_menu(message: types.Message):
    markup = InlineKeyboardMarkup().add(InlineKeyboardButton("❌ Хрестики-Нолики ⭕️", callback_data="tic_info"))
    await message.answer("Оберіть гру:", reply_markup=markup)

# --- ОБРОБНИК СТАВКИ ---
@dp.message_handler(state=GameState.wait_bet)
async def tic_final(message: types.Message, state: FSMContext):
    if message.text in ["👤 Профіль", "🎮 Ігри", "💎 Баланс"]:
        await state.finish()
        if message.text == "👤 Профіль": return await profile_v(message)
        if message.text == "🎮 Ігри": return await games_menu(message)
        if message.text == "💎 Баланс": return await balance_view(message)
        return

    try:
        bet = float(message.text.replace(',', '.'))
        user = await users_col.find_one({"_id": message.from_user.id})
        if bet < 0.05: return await message.answer("❌ Мінімальна ставка 0.05 💎")
        if not user or user['balance'] < bet:
            await state.finish()
            return await message.answer("❌ Мало коштів на балансі!", reply_markup=main_menu())
        
        gid = str(uuid.uuid4())[:8]
        await users_col.update_one({"_id": message.from_user.id}, {"$inc": {"balance": -bet}})
        msg = await message.answer("⏳ Створення гри...", reply_markup=main_menu())
        await games_col.insert_one({
            "game_id": gid, "creator_id": message.from_user.id, "opponent_id": None, 
            "bet": bet, "board": [" "]*9, "status": "waiting", "turn": None, 
            "creator_msg_id": msg.message_id, "last_move_time": time.time()
        })
        await message.answer(f"🔗 Посилання:\n`https://t.me/{(await bot.get_me()).username}?start=game_{gid}`", parse_mode="Markdown")
        await state.finish()
    except ValueError:
        await message.answer("❌ Введіть число (суму ставки) або натисніть кнопку скасування.", reply_markup=cancel_keyboard())

# --- СИСТЕМА ВИВЕДЕННЯ ---
@dp.callback_query_handler(lambda c: c.data == "withdraw_start")
async def withdraw_init(callback: types.CallbackQuery):
    await bot.send_message(callback.from_user.id, f"Виберіть сумму для виведення (мінімум {MIN_WITHDRAW}💎)", reply_markup=cancel_keyboard())
    await WithdrawState.wait_amount.set()
    await callback.answer()

@dp.message_handler(state=WithdrawState.wait_amount)
async def withdraw_amount(message: types.Message, state: FSMContext):
    try:
        amt = float(message.text.replace(',', '.'))
        user = await users_col.find_one({"_id": message.from_user.id})
        if amt < MIN_WITHDRAW: return await message.answer(f"❌ Мінімум: {MIN_WITHDRAW} 💎")
        if user['balance'] < amt: return await message.answer("❌ Недостатньо коштів.")
        await state.update_data(withdraw_amt=amt)
        await message.answer("Введіть ваш IBAN, ІПН та ПІБ", reply_markup=cancel_keyboard())
        await WithdrawState.wait_details.set()
    except: await message.answer("❌ Введіть число.")

@dp.message_handler(state=WithdrawState.wait_details)
async def withdraw_final(message: types.Message, state: FSMContext):
    data = await state.get_data()
    amt = data['withdraw_amt']
    await users_col.update_one({"_id": message.from_user.id}, {"$inc": {"balance": -amt}})
    admin_text = f"📤 **Заявка на вивід**\n\nДані: `{message.text}`\n💰 Сума: **{amt} 💎**\n👤 ID: `{message.from_user.id}`"
    await bot.send_message(ADMIN_ID, admin_text, parse_mode="Markdown")
    await message.answer("✅ Заявка прийнята! Виплата протягом 24 годин.", reply_markup=main_menu())
    await state.finish()

# --- ПОПОВНЕННЯ ТА АДМІН ---
@dp.callback_query_handler(lambda c: c.data == "deposit")
async def dep_start(callback: types.CallbackQuery):
    await bot.send_message(callback.from_user.id, f"💰 Введіть суму в 💎 (мін. {MIN_DEPOSIT}):", reply_markup=cancel_keyboard())
    await DepositState.wait_amount.set()
    await callback.answer()

@dp.message_handler(state=DepositState.wait_amount)
async def dep_amt(message: types.Message, state: FSMContext):
    try:
        amt = float(message.text.replace(',', '.'))
        if amt < MIN_DEPOSIT: return await message.answer(f"❌ Мінімум: {MIN_DEPOSIT} 💎")
        total_uah = (amt * EXCHANGE_RATE) * (1 + BONUS_PERCENT)
        await state.update_data(amount=amt)
        caption = f"📄 **Заявка на поповнення:**\n\nКурс: 1💎={EXCHANGE_RATE:.2f}₴\nДо сплати: {round(total_uah, 2)} ₴ (+5%)\n\n`5355 2800 2890 2177`\n\n(Обовʼязково сюди кидайте квитанцію)"
        await message.answer(caption, parse_mode="Markdown", reply_markup=cancel_keyboard())
        await DepositState.wait_receipt.set()
    except: await message.answer("❌ Введіть число.")

@dp.message_handler(state=DepositState.wait_receipt, content_types=['photo'])
async def dep_rec(message: types.Message, state: FSMContext):
    data = await state.get_data()
    kb = InlineKeyboardMarkup().add(InlineKeyboardButton("✅ ТАК", callback_data=f"ok_{message.from_user.id}_{data['amount']}"), InlineKeyboardButton("❌ НІ", callback_data=f"no_{message.from_user.id}"))
    await bot.send_photo(ADMIN_ID, message.photo[-1].file_id, caption=f"Чек від `{message.from_user.id}`\nСума: {data['amount']} 💎", reply_markup=kb, parse_mode="Markdown")
    await message.answer("✅ Чек надіслано!", reply_markup=main_menu())
    await state.finish()

@dp.callback_query_handler(lambda c: c.data.startswith(('ok_', 'no_')))
async def adm_action(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID: return await callback.answer("❌ Не адмін!")
    parts = callback.data.split("_")
    action, target_uid = parts[0], int(parts[1])
    if action == "ok":
        amount = float(parts[2])
        await users_col.update_one({"_id": target_uid}, {"$inc": {"balance": amount}})
        try: await bot.send_message(target_uid, f"✅ Баланс поповнено на {amount} 💎!")
        except: pass
        await callback.message.edit_caption(f"✅ Нараховано {amount} 💎 для {target_uid}")
    else:
        try: await bot.send_message(target_uid, "❌ Чек відхилено.")
        except: pass
        await callback.message.edit_caption(f"❌ Відхилено")
    await callback.answer("Виконано")

# --- ГРА ТА КНОПКИ ---
@dp.callback_query_handler(lambda c: c.data == "tic_info")
async def tic_info(callback: types.CallbackQuery):
    markup = InlineKeyboardMarkup().add(InlineKeyboardButton("🟩 Матч 3х3", callback_data="tic_create"))
    await callback.message.answer("🕹 Виберіть тип матчу:", reply_markup=markup)
    await callback.answer()

@dp.callback_query_handler(lambda c: c.data == "tic_create")
async def tic_bet_req(callback: types.CallbackQuery):
    await callback.message.answer("💰 Введіть ставку (мін. 0.05 💎):", reply_markup=cancel_keyboard())
    await GameState.wait_bet.set()
    await callback.answer()

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

if __name__ == '__main__':
    loop = asyncio.get_event_loop()
    loop.create_task(check_game_timeouts())
    executor.start_polling(dp, skip_updates=True)
