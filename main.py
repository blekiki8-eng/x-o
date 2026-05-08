import os, uuid, logging, asyncio, time
from aiogram import Bot, Dispatcher, executor, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
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

MOVE_TIMEOUT = 30  
EXCHANGE_RATE, BONUS_PERCENT = 44.50, 0.05
MIN_DEPOSIT, MIN_WITHDRAW, MIN_BET = 0.50, 4.0, 0.05

logging.basicConfig(level=logging.INFO)
storage = MemoryStorage()
bot = Bot(token=API_TOKEN)
dp = Dispatcher(bot, storage=storage)

cluster = AsyncIOMotorClient(MONGO_URL)
db = cluster["fishcash_game"]
users_col, games_col = db["users"], db["games"]

# --- СТАНИ FSM ---
class Registration(StatesGroup):
    wait_nickname = State()

class DepositState(StatesGroup):
    wait_amount = State()
    wait_receipt = State()

class WithdrawState(StatesGroup):
    wait_amount = State()
    wait_details = State()

class GameState(StatesGroup):
    wait_bet = State()
    wait_size = State()

# --- КЛАВІАТУРИ ---
def main_menu(user_id):
    markup = ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add(KeyboardButton("👤 Профіль"), KeyboardButton("🎮 Ігри"))
    markup.add(KeyboardButton("💎 Баланс"))
    if user_id == ADMIN_ID:
        markup.add(KeyboardButton("🛡 Панель адміна"))
    return markup

def cancel_kb():
    return ReplyKeyboardMarkup(resize_keyboard=True).add("❌ Скасувати")

async def get_nick(uid):
    u = await users_col.find_one({"_id": uid})
    return u.get("nickname", "Гравець") if u else "Гравець"

# --- ОБРОБНИКИ СТАРТУ ТА РЕЄСТРАЦІЇ ---
@dp.message_handler(commands=['start'], state="*")
async def start_cmd(m: types.Message, state: FSMContext):
    await state.finish()
    u = await users_col.find_one({"_id": m.from_user.id})
    
    if not u:
        await m.answer("👋 Вітаємо! Введіть ваш ігровий нікнейм:")
        await Registration.wait_nickname.set()
        return

    if u.get("banned"):
        return await m.answer("🚫 Ви заблоковані.")

    # Перевірка на вхід у гру за посиланням
    args = m.get_args()
    if args and args.startswith("game_"):
        gid = args.split("_")[1]
        g = await games_col.find_one({"game_id": gid, "status": "waiting"})
        if g and g['creator_id'] != m.from_user.id:
            if u['balance'] >= g['bet']:
                await users_col.update_one({"_id": m.from_user.id}, {"$inc": {"balance": -g['bet']}})
                # Початок гри (спрощено)
                await m.answer("Гра починається!", reply_markup=main_menu(m.from_user.id))
                return
            else:
                await m.answer("❌ Недостатньо балансу.")

    await m.answer(f"Привіт, {u['nickname']}!", reply_markup=main_menu(m.from_user.id))

@dp.message_handler(state=Registration.wait_nickname)
async def reg_nickname(m: types.Message, state: FSMContext):
    nick = m.text.strip()[:15]
    if len(nick) < 2:
        return await m.answer("Нік занадто короткий!")
    
    await users_col.insert_one({
        "_id": m.from_user.id, 
        "nickname": nick, 
        "balance": 0.0, 
        "banned": False
    })
    await m.answer(f"✅ Нік **{nick}** збережено!", parse_mode="Markdown", reply_markup=main_menu(m.from_user.id))
    await state.finish()

# --- ПРОФІЛЬ ТА БАЛАНС ---
@dp.message_handler(lambda m: m.text == "👤 Профіль", state="*")
async def profile_handler(m: types.Message):
    u = await users_col.find_one({"_id": m.from_user.id})
    if not u: return
    text = (f"👤 **Твій профіль**\n\n"
            f"📝 Нік: `{u['nickname']}`\n"
            f"🆔 ID: `{m.from_user.id}`\n"
            f"💰 Баланс: {round(u['balance'], 2)} 💎")
    await m.answer(text, parse_mode="Markdown", reply_markup=main_menu(m.from_user.id))

@dp.message_handler(lambda m: m.text == "💎 Баланс", state="*")
async def balance_handler(m: types.Message):
    u = await users_col.find_one({"_id": m.from_user.id})
    if not u: return
    kb = InlineKeyboardMarkup().add(
        InlineKeyboardButton("💳 Поповнити", callback_data="dep"),
        InlineKeyboardButton("📤 Вивести", callback_data="wit")
    )
    await m.answer(f"💎 Баланс: **{u['balance']}**", reply_markup=kb, parse_mode="Markdown")

# --- АДМІН-СИСТЕМА ---
@dp.message_handler(lambda m: m.text == "🛡 Панель адміна", state="*")
async def admin_panel(m: types.Message):
    if m.from_user.id != ADMIN_ID: return
    users_count = await users_col.count_documents({})
    kb = InlineKeyboardMarkup().add(InlineKeyboardButton("👥 Список гравців", callback_data="adm_list"))
    await m.answer(f"🛡 **Адмін-панель**\nГравців у базі: {users_count}", reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data == "adm_list", state="*")
async def adm_list_callback(c: types.CallbackQuery):
    if c.from_user.id != ADMIN_ID: return
    users = await users_col.find().to_list(length=50)
    kb = InlineKeyboardMarkup(row_width=1)
    for u in users:
        st = "🚫" if u.get("banned") else "👤"
        kb.add(InlineKeyboardButton(f"{st} {u['nickname']} | {u['balance']} 💎", callback_data=f"man_{u['_id']}"))
    await c.message.edit_text("Оберіть гравця:", reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data.startswith(('man_', 'res_', 'bn_')), state="*")
async def adm_actions(c: types.CallbackQuery):
    if c.from_user.id != ADMIN_ID: return
    data = c.data.split("_")
    action, uid = data[0], int(data[1])
    
    if action == "res":
        await users_col.update_one({"_id": uid}, {"$set": {"balance": 0.0}})
        await c.answer("Баланс обнулено")
    elif action == "bn":
        u = await users_col.find_one({"_id": uid})
        await users_col.update_one({"_id": uid}, {"$set": {"banned": not u.get("banned", False)}})
        await c.answer("Статус змінено")

    # Повернення до меню керування конкретним юзером
    u = await users_col.find_one({"_id": uid})
    bt = "Розблокувати" if u.get("banned") else "Заблокувати"
    kb = InlineKeyboardMarkup(row_width=1).add(
        InlineKeyboardButton("💰 Обнулити баланс", callback_data=f"res_{uid}"),
        InlineKeyboardButton(f"⛔️ {bt}", callback_data=f"bn_{uid}"),
        InlineKeyboardButton("⬅️ Назад до списку", callback_data="adm_list")
    )
    await c.message.edit_text(f"Керування: {u['nickname']}\nID: {uid}\nБаланс: {u['balance']}\nБан: {u.get('banned')}", reply_markup=kb)

# --- ВИВЕДЕННЯ КОШТІВ ---
@dp.callback_query_handler(lambda c: c.data == "wit", state="*")
async def wit_start(c: types.CallbackQuery):
    await WithdrawState.wait_amount.set()
    await bot.send_message(c.from_user.id, "Введіть суму виводу 💎:", reply_markup=cancel_kb())
    await c.answer()

@dp.message_handler(state=WithdrawState.wait_amount)
async def wit_amount(m: types.Message, state: FSMContext):
    try:
        amt = float(m.text)
        u = await users_col.find_one({"_id": m.from_user.id})
        if amt < MIN_WITHDRAW or u['balance'] < amt:
            return await m.answer(f"❌ Мінімальний вивід {MIN_WITHDRAW} або недостатньо коштів.")
        await state.update_data(a=amt)
        await m.answer("Введіть дані для виводу:\n1. IBAN\n2. ІПН\n3. ПІБ", reply_markup=cancel_kb())
        await WithdrawState.wait_details.set()
    except: await m.answer("Введіть число!")

@dp.message_handler(state=WithdrawState.wait_details)
async def wit_final(m: types.Message, state: FSMContext):
    data = await state.get_data()
    amt = data['a']
    await users_col.update_one({"_id": m.from_user.id}, {"$inc": {"balance": -amt}})
    
    kb = InlineKeyboardMarkup().add(
        InlineKeyboardButton("✅ Виплачено", callback_data=f"wok_{m.from_user.id}_{amt}"),
        InlineKeyboardButton("❌ Відмова", callback_data=f"wno_{m.from_user.id}_{amt}")
    )
    await bot.send_message(ADMIN_ID, f"📤 Заявка на вивід!\nГроші: {amt} 💎\nНік: {await get_nick(m.from_user.id)}\nРеквізити:\n{m.text}", reply_markup=kb)
    await m.answer("✅ Заявка надіслана адміну!", reply_markup=main_menu(m.from_user.id))
    await state.finish()

@dp.callback_query_handler(lambda c: c.data.startswith(('wok_', 'wno_')), state="*")
async def admin_wit_decision(c: types.CallbackQuery):
    if c.from_user.id != ADMIN_ID: return
    p = c.data.split("_")
    uid, amt = int(p[1]), float(p[2])
    if p[0] == "wok":
        await bot.send_message(uid, f"✅ Ваш вивід на {amt} 💎 успішно виконано!")
    else:
        await users_col.update_one({"_id": uid}, {"$inc": {"balance": amt}})
        await bot.send_message(uid, f"❌ Вивід на {amt} 💎 відхилено. Кошти повернуто.")
    await c.message.delete()

# --- ІНШІ ОБРОБНИКИ (ПОПОВНЕННЯ, ІГРИ) ---
@dp.message_handler(state="*", text="❌ Скасувати")
async def cancel_all(m: types.Message, state: FSMContext):
    await state.finish()
    await m.answer("Скасовано.", reply_markup=main_menu(m.from_user.id))

# (Логіка гри Хрестики-нолики та поповнення залишається як у попередніх версіях)

if __name__ == '__main__':
    executor.start_polling(dp, skip_updates=True)
