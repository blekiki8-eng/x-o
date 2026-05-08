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

EXCHANGE_RATE = 44.50  # 1 💎 = 44.50 грн
MOVE_TIMEOUT = 30  

logging.basicConfig(level=logging.INFO)
storage = MemoryStorage()
bot = Bot(token=API_TOKEN)
dp = Dispatcher(bot, storage=storage)

cluster = AsyncIOMotorClient(MONGO_URL)
db = cluster["fishcash_game"]
users_col, games_col = db["users"], db["games"]

# --- СТАНИ FSM ---
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

# --- ДОПОМІЖНІ ФУНКЦІЇ ---
async def check_user(user: types.User):
    # Автоматично беремо ім'я з Telegram, якщо юзера немає в базі
    u = await users_col.find_one({"_id": user.id})
    if not u:
        new_user = {
            "_id": user.id,
            "nickname": user.full_name,
            "balance": 0.0,
            "banned": False
        }
        await users_col.insert_one(new_user)
        return new_user
    return u

# --- ОБРОБНИКИ КОМАНД ---
@dp.message_handler(commands=['start'], state="*")
async def start_cmd(m: types.Message, state: FSMContext):
    await state.finish()
    u = await check_user(m.from_user)
    if u.get("banned"): return await m.answer("🚫 Ви заблоковані.")

    # Перевірка входу в гру за посиланням
    args = m.get_args()
    if args and args.startswith("game_"):
        gid = args.split("_")[1]
        g = await games_col.find_one({"game_id": gid, "status": "waiting"})
        if g and g['creator_id'] != m.from_user.id:
            if u['balance'] >= g['bet']:
                await users_col.update_one({"_id": m.from_user.id}, {"$inc": {"balance": -g['bet']}})
                # Тут логіка запуску гри (попередні версії)
                await m.answer("🎮 Ви приєдналися до гри!")
                return
    
    await m.answer(f"Вітаємо, {u['nickname']}!", reply_markup=main_menu(m.from_user.id))

# --- ПРОФІЛЬ ---
@dp.message_handler(lambda m: m.text == "👤 Профіль", state="*")
async def profile_handler(m: types.Message):
    u = await check_user(m.from_user)
    text = (f"👤 **Твій профіль**\n\n"
            f"📝 Нік: `{u['nickname']}` (з ТГ)\n"
            f"🆔 ID: `{m.from_user.id}`\n"
            f"💰 Баланс: {round(u['balance'], 2)} 💎")
    await m.answer(text, parse_mode="Markdown")

# --- СИСТЕМА ПОПОВНЕННЯ (НОРМАЛЬНА) ---
@dp.message_handler(lambda m: m.text == "💎 Баланс", state="*")
async def balance_handler(m: types.Message):
    u = await check_user(m.from_user)
    kb = InlineKeyboardMarkup().add(
        InlineKeyboardButton("💳 Поповнити", callback_data="dep"),
        InlineKeyboardButton("📤 Вивести", callback_data="wit")
    )
    await m.answer(f"💎 Твій баланс: **{round(u['balance'], 2)} 💎**", reply_markup=kb, parse_mode="Markdown")

@dp.callback_query_handler(lambda c: c.data == "dep", state="*")
async def deposit_init(c: types.CallbackQuery):
    await DepositState.wait_amount.set()
    await bot.send_message(c.from_user.id, "💎 Введіть кількість 💎 для поповнення:", reply_markup=cancel_kb())

@dp.message_handler(state=DepositState.wait_amount)
async def deposit_amount(m: types.Message, state: FSMContext):
    try:
        amt = float(m.text)
        if amt <= 0: raise ValueError
        uah = round(amt * EXCHANGE_RATE * 1.05, 2) # +5% комісія
        await state.update_data(amount=amt)
        await m.answer(f"💵 До сплати: **{uah} грн**\n\n💳 Реквізити: `5355 2800 2890 2177`\n\nПісля оплати надішліть **фото чека** сюди:", parse_mode="Markdown")
        await DepositState.wait_receipt.set()
    except: await m.answer("❌ Введіть коректне число!")

@dp.message_handler(state=DepositState.wait_receipt, content_types=['photo'])
async def deposit_receipt(m: types.Message, state: FSMContext):
    data = await state.get_data()
    amt = data['amount']
    
    # Кнопки для адміна
    kb = InlineKeyboardMarkup().add(
        InlineKeyboardButton("✅ Підтвердити", callback_data=f"adm_ok_{m.from_user.id}_{amt}"),
        InlineKeyboardButton("❌ Відхилити", callback_data=f"adm_no_{m.from_user.id}")
    )
    
    await bot.send_photo(ADMIN_ID, m.photo[-1].file_id, 
                         caption=f"💰 Поповнення!\nГравець: {m.from_user.full_name}\nID: {m.from_user.id}\nСума: {amt} 💎", 
                         reply_markup=kb)
    
    await m.answer("⏳ Чек надіслано! Очікуйте підтвердження адміном.", reply_markup=main_menu(m.from_user.id))
    await state.finish()

# --- АДМІН-ПАНЕЛЬ ---
@dp.message_handler(lambda m: m.text == "🛡 Панель адміна", state="*")
async def admin_panel(m: types.Message):
    if m.from_user.id != ADMIN_ID: return
    users_count = await users_col.count_documents({})
    kb = InlineKeyboardMarkup().add(InlineKeyboardButton("👥 Список гравців", callback_data="admin_list_users"))
    await m.answer(f"🛡 Адмін-панель\nГравців: {users_count}", reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data == "admin_list_users", state="*")
async def admin_list(c: types.CallbackQuery):
    users = await users_col.find().to_list(50)
    kb = InlineKeyboardMarkup(row_width=1)
    for u in users:
        prefix = "🚫" if u.get("banned") else "👤"
        kb.add(InlineKeyboardButton(f"{prefix} {u['nickname']} | {u['balance']} 💎", callback_data=f"manage_{u['_id']}"))
    await c.message.edit_text("Керування гравцями:", reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data.startswith(('manage_', 'adm_ok_', 'adm_no_', 'areset_', 'aban_')), state="*")
async def admin_callback(c: types.CallbackQuery):
    if c.from_user.id != ADMIN_ID: return
    data = c.data.split("_")
    action = data[1]

    # Підтвердження поповнення
    if action == "ok":
        uid, amt = int(data[2]), float(data[3])
        await users_col.update_one({"_id": uid}, {"$inc": {"balance": amt}})
        await bot.send_message(uid, f"✅ Поповнення на {amt} 💎 зараховано!")
        await c.message.edit_caption("✅ Схвалено")
    
    elif action == "no":
        uid = int(data[2])
        await bot.send_message(uid, "❌ Ваше поповнення було відхилено. Перевірте дані.")
        await c.message.edit_caption("❌ Відхилено")

    # Керування гравцем
    elif action == "list": # Це просто повернення
        await admin_list(c)

    elif data[0] == "manage":
        uid = int(data[1])
        u = await users_col.find_one({"_id": uid})
        bt = "Розблокувати" if u.get("banned") else "Заблокувати"
        kb = InlineKeyboardMarkup(row_width=1).add(
            InlineKeyboardButton("💰 Обнулити баланс", callback_data=f"areset_{uid}"),
            InlineKeyboardButton(f"⛔️ {bt}", callback_data=f"aban_{uid}"),
            InlineKeyboardButton("⬅️ Назад", callback_data="admin_list_users")
        )
        await c.message.edit_text(f"Гравець: {u['nickname']}\nID: {uid}\nБаланс: {u['balance']} 💎", reply_markup=kb)

    elif data[0] == "areset":
        uid = int(data[1])
        await users_col.update_one({"_id": uid}, {"$set": {"balance": 0.0}})
        await c.answer("Баланс обнулено")
        await admin_list(c)

    elif data[0] == "aban":
        uid = int(data[1])
        u = await users_col.find_one({"_id": uid})
        await users_col.update_one({"_id": uid}, {"$set": {"banned": not u.get("banned", False)}})
        await c.answer("Статус змінено")
        await admin_list(c)

# --- СКАСУВАННЯ ---
@dp.message_handler(state="*", text="❌ Скасувати")
async def cancel_handler(m: types.Message, state: FSMContext):
    await state.finish()
    await m.answer("Дію скасовано.", reply_markup=main_menu(m.from_user.id))

if __name__ == '__main__':
    executor.start_polling(dp, skip_updates=True)
