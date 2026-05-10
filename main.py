import os, uuid, logging, asyncio, time
from aiogram import Bot, Dispatcher, executor, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

# --- НАЛАШТУВАННЯ ---
load_dotenv()
API_TOKEN = os.getenv("BOT_TOKEN")
MONGO_URL = os.getenv("MONGO_URL")
ADMIN_ID = int(os.getenv("ADMIN_ID")) if os.getenv("ADMIN_ID") else 0

RATE = 44.50
logging.basicConfig(level=logging.INFO)
storage = MemoryStorage()
bot = Bot(token=API_TOKEN)
dp = Dispatcher(bot, storage=storage)

cluster = AsyncIOMotorClient(MONGO_URL)
db = cluster["game_bot_db"]
users_col = db["users"]
games_col = db["games"]

# --- СТАНИ FSM ---
class DepositState(StatesGroup):
    wait_amount = State()
    wait_receipt = State()

class GameState(StatesGroup):
    wait_game_type = State()
    wait_size = State()
    wait_bet = State()

# --- КЛАВІАТУРИ ---
def main_menu(user_id):
    markup = ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add(KeyboardButton("👤 Профіль"), KeyboardButton("🎮 Ігри"))
    markup.add(KeyboardButton("💎 Баланс"))
    if user_id == ADMIN_ID:
        markup.add(KeyboardButton("🛡 Панель адміна"))
    return markup

def games_choice_kb():
    return ReplyKeyboardMarkup(resize_keyboard=True).add(KeyboardButton("❌ Хрестики нолики"), KeyboardButton("❌ Скасувати"))

def modes_choice_kb():
    markup = ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add(KeyboardButton("3х3"), KeyboardButton("4х4"))
    markup.add(KeyboardButton("❌ Скасувати"))
    return markup

def cancel_kb():
    return ReplyKeyboardMarkup(resize_keyboard=True).add(KeyboardButton("❌ Скасувати"))

# --- ДОПОМІЖНІ ФУНКЦІЇ ---
async def get_u(m):
    uid = m.from_user.id
    u = await users_col.find_one({"_id": uid})
    if not u:
        u = {"_id": uid, "nickname": m.from_user.full_name, "balance": 0.0, "banned": False}
        await users_col.insert_one(u)
    return u

def get_board_markup(game_id, board, size):
    markup = InlineKeyboardMarkup(row_width=size)
    btns = [InlineKeyboardButton(board[i] if board[i] != " " else "⬜️", callback_data=f"st:{game_id}:{i}") for i in range(size*size)]
    return markup.add(*btns)

# --- ВХІД В ГРУ ЧЕРЕЗ ПОСИЛАННЯ ---
@dp.message_handler(commands=['start'], state="*")
async def start(m: types.Message, state: FSMContext):
    await state.finish()
    u = await get_u(m)
    
    args = m.get_args()
    if args and args.startswith("game_"):
        gid = args.replace("game_", "")
        g = await games_col.find_one({"game_id": gid, "status": "waiting"})
        
        if not g:
            return await m.answer("❌ Гра не знайдена або вже почалася.", reply_markup=main_menu(m.from_user.id))
        
        if g['creator_id'] == m.from_user.id:
            return await m.answer("⏳ Це ваша гра. Очікуйте суперника.", reply_markup=main_menu(m.from_user.id))
            
        if u['balance'] < g['bet']:
            return await m.answer(f"❌ Недостатньо балансу для входу. Потрібно {g['bet']} 💎", reply_markup=main_menu(m.from_user.id))

        # Списання ставки та запуск гри
        await users_col.update_one({"_id": m.from_user.id}, {"$inc": {"balance": -g['bet']}})
        
        board_markup = get_board_markup(gid, g['board'], g['size'])
        
        # Повідомлення супернику
        m_op = await m.answer(f"🎮 Ви зайшли в гру!\nВаш хід — ⭕️ (після ходу суперника).", reply_markup=board_markup)
        
        # Повідомлення творцю
        m_cr = await bot.send_message(g['creator_id'], f"🎮 Суперник знайдений! Ваша черга ходити (❌).", reply_markup=board_markup)
        
        await games_col.update_one({"game_id": gid}, {"$set": {
            "opponent_id": m.from_user.id,
            "opponent_msg_id": m_op.message_id,
            "creator_msg_id": m_cr.message_id,
            "status": "playing",
            "turn": g['creator_id']
        }})
        return

    await m.answer(f"Привіт, {u['nickname']}!", reply_markup=main_menu(m.from_user.id))

# --- ЛОГІКА ІГОР (НИЖНЯ ПАНЕЛЬ) ---
@dp.message_handler(lambda m: m.text == "🎮 Ігри", state="*")
async def games_menu(m: types.Message):
    await GameState.wait_game_type.set()
    await m.answer("Оберіть гру:", reply_markup=games_choice_kb())

@dp.message_handler(state=GameState.wait_game_type)
async def choose_game(m: types.Message, state: FSMContext):
    if m.text == "❌ Скасувати":
        await state.finish()
        return await m.answer("Скасовано", reply_markup=main_menu(m.from_user.id))
    
    if m.text == "❌ Хрестики нолики":
        await GameState.wait_size.set()
        await m.answer("Оберіть розмір поля:", reply_markup=modes_choice_kb())
    else:
        await m.answer("Оберіть гру з меню внизу!")

@dp.message_handler(state=GameState.wait_size)
async def choose_size(m: types.Message, state: FSMContext):
    if m.text == "❌ Скасувати":
        await state.finish()
        return await m.answer("Скасовано", reply_markup=main_menu(m.from_user.id))
    
    if m.text in ["3х3", "4х4"]:
        size = 3 if "3" in m.text else 4
        await state.update_data(sz=size)
        await GameState.wait_bet.set()
        await m.answer(f"Режим {m.text}. Введіть ставку 💎:", reply_markup=cancel_kb())
    else:
        await m.answer("Оберіть режим з меню внизу!")

@dp.message_handler(state=GameState.wait_bet)
async def create_game(m: types.Message, state: FSMContext):
    if m.text == "❌ Скасувати":
        await state.finish()
        return await m.answer("Скасовано", reply_markup=main_menu(m.from_user.id))
    
    try:
        bet = float(m.text.replace(",", "."))
        u = await get_u(m)
        if u['balance'] < bet: return await m.answer("❌ Недостатньо балансу!")
        
        data = await state.get_data()
        size = data['sz']
        gid = str(uuid.uuid4())[:8]
        
        await users_col.update_one({"_id": m.from_user.id}, {"$inc": {"balance": -bet}})
        await games_col.insert_one({
            "game_id": gid, "creator_id": m.from_user.id, "bet": bet, 
            "size": size, "board": [" "]*(size*size), "status": "waiting"
        })
        
        link = f"https://t.me/{(await bot.get_me()).username}?start=game_{gid}"
        await m.answer(f"🎮 Гра {size}x{size} створена!\nСтавка: {bet} 💎\n\nВідправте посилання другу:\n`{link}`", parse_mode="Markdown", reply_markup=main_menu(m.from_user.id))
        await state.finish()
    except:
        await m.answer("❌ Введіть число!")

# --- БАЛАНС ---
@dp.message_handler(lambda m: m.text == "💎 Баланс", state="*")
async def bal_m(m: types.Message):
    u = await get_u(m)
    kb = InlineKeyboardMarkup().add(InlineKeyboardButton("💳 Поповнити", callback_data="dep"))
    await m.answer(f"💰 Баланс: {round(u.get('balance', 0.0), 2)} 💎", reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data == "dep", state="*")
async def dep_st(c: types.CallbackQuery):
    await DepositState.wait_amount.set()
    await bot.send_message(c.from_user.id, "Введіть сумму в 💎\n*💎-$\n(Мінімум 0.50!)", reply_markup=cancel_kb())

@dp.message_handler(state=DepositState.wait_amount)
async def dep_val(m: types.Message, state: FSMContext):
    if m.text == "❌ Скасувати":
        await state.finish()
        return await m.answer("Скасовано", reply_markup=main_menu(m.from_user.id))
    try:
        amt = float(m.text.replace(",", "."))
        if amt < 0.50: return await m.answer("❌ Мінімум 0.50!")
        uah = round((amt * RATE) * 1.05, 2)
        await state.update_data(d=amt)
        txt = f"Заявка на поповнення:\n1💎= {RATE}₴\nДо оплати: {uah}₴ [+5%]\n\n`5355 2800 2890 2177`"
        await m.answer(txt, parse_mode="Markdown", reply_markup=cancel_kb())
        await DepositState.wait_receipt.set()
    except: await m.answer("Введіть число!")

@dp.message_handler(state=DepositState.wait_receipt, content_types=['photo'])
async def dep_fin(m: types.Message, state: FSMContext):
    data = await state.get_data()
    kb = InlineKeyboardMarkup().add(InlineKeyboardButton("✅", callback_data=f"ok:{m.from_user.id}:{data['d']}"), InlineKeyboardButton("❌", callback_data=f"no:{m.from_user.id}"))
    await bot.send_photo(ADMIN_ID, m.photo[-1].file_id, caption=f"💎 {data['d']} від {m.from_user.id}", reply_markup=kb)
    await m.answer("✅ Очікуйте (до 60хв.)", reply_markup=main_menu(m.from_user.id))
    await state.finish()

# --- ПРОФІЛЬ ---
@dp.message_handler(lambda m: m.text == "👤 Профіль", state="*")
async def prof(m: types.Message):
    u = await get_u(m)
    await m.answer(f"👤 Нік: {u['nickname']}\n💰 Баланс: {round(u['balance'], 2)} 💎")

if __name__ == '__main__':
    executor.start_polling(dp, skip_updates=True)
