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
    return ReplyKeyboardMarkup(resize_keyboard=True).add(KeyboardButton("3х3"), KeyboardButton("4х4")).add(KeyboardButton("❌ Скасувати"))

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

# --- ПРОФІЛЬ (ВІДНОВЛЕНО) ---
@dp.message_handler(lambda m: m.text == "👤 Профіль", state="*")
async def profile(m: types.Message):
    u = await get_u(m)
    text = (
        f"👤 **Твій профіль**\n\n"
        f"📝 Нік: {u.get('nickname')}\n"
        f"🆔 ID: `{m.from_user.id}`\n"
        f"💰 Баланс: {round(u.get('balance', 0.0), 2)} 💎"
    )
    await m.answer(text, parse_mode="Markdown")

# --- ПОПОВНЕННЯ ТА КВИТАНЦІЇ (ВИПРАВЛЕНО) ---
@dp.message_handler(lambda m: m.text == "💎 Баланс", state="*")
async def balance_menu(m: types.Message):
    u = await get_u(m)
    kb = InlineKeyboardMarkup().add(InlineKeyboardButton("💳 Поповнити", callback_data="dep"))
    await m.answer(f"💰 Твій баланс: {round(u.get('balance', 0.0), 2)} 💎", reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data == "dep", state="*")
async def dep_start(c: types.CallbackQuery):
    await DepositState.wait_amount.set()
    await bot.send_message(c.from_user.id, "Введіть сумму в 💎\n*💎-$\n(Мінімум 0.50!)", reply_markup=cancel_kb())

@dp.message_handler(state=DepositState.wait_amount)
async def dep_amount(m: types.Message, state: FSMContext):
    if m.text == "❌ Скасувати":
        await state.finish()
        return await m.answer("Скасовано", reply_markup=main_menu(m.from_user.id))
    try:
        amt = float(m.text.replace(",", "."))
        if amt < 0.50: return await m.answer("❌ Мінімум 0.50!")
        uah = round((amt * RATE) * 1.05, 2)
        await state.update_data(diamonds=amt)
        text = (
            f"Заявка на поповнення балансу:\n"
            f"1💎= {RATE}₴\n"
            f"До оплати: {uah}₴ [+5%]\n\n"
            f"`5355 2800 2890 2177`\n\n"
            f"(Для підтвердження оплати обовʼязково скиньте квитанцію)"
        )
        await m.answer(text, parse_mode="Markdown", reply_markup=cancel_kb())
        await DepositState.wait_receipt.set()
    except: await m.answer("❌ Введіть число!")

@dp.message_handler(state=DepositState.wait_receipt, content_types=['photo'])
async def dep_receipt(m: types.Message, state: FSMContext):
    data = await state.get_data()
    diamonds = data['diamonds']
    
    # Кнопки для адміна (БЕЗ ПОМИЛОК)
    kb = InlineKeyboardMarkup().add(
        InlineKeyboardButton("✅ Схвалити", callback_data=f"payok:{m.from_user.id}:{diamonds}"),
        InlineKeyboardButton("❌ Відхилити", callback_data=f"payno:{m.from_user.id}")
    )
    
    await bot.send_photo(
        ADMIN_ID, 
        m.photo[-1].file_id, 
        caption=f"💰 Квитанція на {diamonds} 💎\nВід: {m.from_user.full_name}\nID: `{m.from_user.id}`",
        reply_markup=kb,
        parse_mode="Markdown"
    )
    await m.answer("✅ Очікуйте (до 60хв.)", reply_markup=main_menu(m.from_user.id))
    await state.finish()

# --- ОБРОБКА КНОПОК АДМІНОМ ---
@dp.callback_query_handler(lambda c: c.data.startswith(("payok:", "payno:")), state="*")
async def admin_pay_manage(c: types.CallbackQuery):
    if c.from_user.id != ADMIN_ID: return
    
    data = c.data.split(":")
    action = data[0]
    user_id = int(data[1])

    if action == "payok":
        amount = float(data[2])
        await users_col.update_one({"_id": user_id}, {"$inc": {"balance": amount}})
        try:
            await bot.send_message(user_id, f"✅ Ваша заявка схвалена! Нараховано {amount} 💎")
        except: pass
        await c.answer("Нараховано!")
    else:
        try:
            await bot.send_message(user_id, "❌ Ваша заявка на поповнення відхилена.")
        except: pass
        await c.answer("Відхилено")
    
    await c.message.delete()

# --- ІГРИ (НИЖНЯ ПАНЕЛЬ) ---
@dp.message_handler(lambda m: m.text == "🎮 Ігри", state="*")
async def games_start(m: types.Message):
    await GameState.wait_game_type.set()
    await m.answer("Оберіть гру:", reply_markup=games_choice_kb())

@dp.message_handler(state=GameState.wait_game_type)
async def game_type(m: types.Message, state: FSMContext):
    if m.text == "❌ Скасувати":
        await state.finish()
        return await m.answer("Скасовано", reply_markup=main_menu(m.from_user.id))
    if m.text == "❌ Хрестики нолики":
        await GameState.wait_size.set()
        await m.answer("Оберіть розмір поля:", reply_markup=modes_choice_kb())

@dp.message_handler(state=GameState.wait_size)
async def game_size(m: types.Message, state: FSMContext):
    if m.text == "❌ Скасувати":
        await state.finish()
        return await m.answer("Скасовано", reply_markup=main_menu(m.from_user.id))
    if m.text in ["3х3", "4х4"]:
        size = 3 if "3" in m.text else 4
        await state.update_data(sz=size)
        await GameState.wait_bet.set()
        await m.answer(f"Режим {m.text}. Введіть ставку 💎:", reply_markup=cancel_kb())

@dp.message_handler(state=GameState.wait_bet)
async def game_bet(m: types.Message, state: FSMContext):
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
        await m.answer(f"🎮 Гра {size}x{size} створена!\nСтавка: {bet} 💎\n\nВідправ посилання другу:\n`{link}`", 
                       parse_mode="Markdown", reply_markup=main_menu(m.from_user.id))
        await state.finish()
    except: await m.answer("❌ Введіть число!")

# --- ЗАГАЛЬНЕ СКАСУВАННЯ ---
@dp.message_handler(lambda m: m.text == "❌ Скасувати", state="*")
async def global_cancel(m: types.Message, state: FSMContext):
    await state.finish()
    await m.answer("Дію скасовано.", reply_markup=main_menu(m.from_user.id))

if __name__ == '__main__':
    executor.start_polling(dp, skip_updates=True)
