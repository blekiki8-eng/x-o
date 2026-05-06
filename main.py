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
                        await bot.send_message(uid, f"🎉 **Вітаю з перемогою!**\n💰 Виграш {round(game['bet']*2, 2)} 💎 нараховано!", parse_mode="Markdown")
                    else:
                        text = "❌ **Ви програли. Наступного разу пощастить!**"
                await bot.edit_message_text(text, uid, mid, reply_markup=markup, parse_mode="Markdown")
            else:
                text = "🎮 **Твій хід! (40 сек.)**" if uid == game['turn'] else "⏳ **Хід суперника...**"
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
        except: pass
        await asyncio.sleep(5)

# --- Обробник скасування ---
@dp.message_handler(state="*", text="❌ Скасувати")
async def cancel_handler(message: types.Message, state: FSMContext):
    await state.finish()
    await message.answer("Дію скасовано.", reply_markup=main_menu())

# --- Основні команди ---
@dp.message_handler(commands=['start'])
async def start_cmd(message: types.Message):
    user = await users_col.find_one({"_id": message.from_user.id})
    if not user:
        await users_col.insert_one({"_id": message.from_user.id, "username": message.from_user.username, "balance": 0.0})
    
    args = message.get_args()
    if args and args.startswith("game_"):
        game_id = args.split("_")[1]
        game = await games_col.find_one({"game_id": game_id, "status": "waiting"})
        if not game: return await message.answer("❌ Гра вже почалася або не знайдена.")
        if game['creator_id'] == message.from_user.id: return await message.answer("❌ Не можна грати з самим собою.")
        
        user = await users_col.find_one({"_id": message.from_user.id})
        if user['balance'] < game['bet']: return await message.answer("❌ Недостатньо балансу.")

        # Приєднання
        await users_col.update_one({"_id": message.from_user.id}, {"$inc": {"balance": -game['bet']}})
        msg_opp = await message.answer("🎮 Гра починається!", reply_markup=main_menu())
        
        update_data = {
            "opponent_id": message.from_user.id, "opponent_msg_id": msg_opp.message_id,
            "status": "playing", "turn": game['creator_id'], "last_move_time": time.time()
        }
        await games_col.update_one({"game_id": game_id}, {"$set": update_data})
        game.update(update_data)
        
        try: await bot.send_message(game['creator_id'], "✅ Суперник знайдений! Гра почалася.")
        except: pass
        
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

# --- СТАВКА ---
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
            return await message.answer("❌ Мало коштів!", reply_markup=main_menu())
        
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
    except: await message.answer("❌ Введіть число.", reply_markup=cancel_keyboard())

# --- ВИВЕДЕННЯ ---
@dp.callback_query_handler(lambda c: c.data == "withdraw_start")
async def withdraw_init(callback: types.CallbackQuery):
    await bot.send_message(callback.from_user.id, f"Виберіть сумму для виведення (мін. {MIN_WITHDRAW}💎)", reply_markup=cancel_keyboard())
    await WithdrawState.wait_amount.set()
    await callback.answer()

@dp.message_handler(state=WithdrawState.wait_amount)
async def withdraw_amount(message: types.Message, state: FSMContext):
    try:
        amt = float(message.text.replace(',', '.'))
        user = await users_col.find_one({"_id": message.from_user.id})
        if amt < MIN_WITHDRAW: return await message.answer(f"❌ Мінімум: {MIN_WITHDRAW} 💎")
        if user['balance'] < amt: return await message.answer("❌ Мало коштів.")
        await state.update_data
