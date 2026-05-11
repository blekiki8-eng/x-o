import os, uuid, logging, asyncio
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    InlineKeyboardMarkup, InlineKeyboardButton, 
    ReplyKeyboardMarkup, KeyboardButton
)
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

# --- КОНФІГУРАЦІЯ ---
load_dotenv()
API_TOKEN = os.getenv("TEST_BOT_TOKEN") 
MONGO_URL = os.getenv("MONGO_URL")
ADMIN_ID = int(os.getenv("ADMIN_ID")) if os.getenv("ADMIN_ID") else 0

logging.basicConfig(level=logging.INFO)
bot = Bot(token=API_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

cluster = AsyncIOMotorClient(MONGO_URL)
db = cluster["test_game_bot_db"] 
users_col = db["users"]
games_col = db["games"]

# --- СТАНИ ---
class Form(StatesGroup):
    wait_bet = State()
    admin_broadcast = State()
    admin_write_user = State()

# --- КЛАВІАТУРИ ---
def main_menu(user_id):
    kb = [
        [KeyboardButton(text="👤 Профіль"), KeyboardButton(text="🎮 Games")],
        [KeyboardButton(text="💎 Баланс"), KeyboardButton(text="🤝 Рефералка")]
    ]
    if user_id == ADMIN_ID:
        kb.append([KeyboardButton(text="🛡 Панель адміна")])
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

# --- ЛОГІКА ЮЗЕРІВ ---
async def get_u(user_id):
    u = await users_col.find_one({"_id": user_id})
    if not u:
        u = {
            "_id": user_id, 
            "balance": 0.0, 
            "referals_count": 0, 
            "turnover": 0.0, 
            "games_played": 0,
            "is_blocked": False
        }
        await users_col.insert_one(u)
    return u

# --- ОБРОБНИКИ ---

@dp.message(Command("start"))
async def cmd_start(m: types.Message, state: FSMContext):
    await state.clear()
    uid = m.from_user.id
    u = await get_u(uid)
    
    if u.get('is_blocked'):
        return await m.answer("🚫 Ваш акаунт заблоковано адміністрацією.")

    # Перевірка реферала/гри в аргументах
    args = m.text.split()
    if len(args) > 1:
        payload = args[1]
        if payload.startswith("game_"):
            # Логіка приєднання до гри (як раніше)
            pass
        elif payload.isdigit() and int(payload) != uid:
            # Логіка реєстрації реферала
            if not await users_col.find_one({"_id": uid}):
                await users_col.update_one({"_id": int(payload)}, {"$inc": {"referals_count": 1}})

    await m.answer("✨ Ласкаво просимо до гри!", reply_markup=main_menu(uid))

# --- ПРОФІЛЬ (НОВИЙ ВИГЛЯД) ---
@dp.message(F.text == "👤 Профіль")
async def profile_cmd(m: types.Message):
    u = await get_u(m.from_user.id)
    text = (
        f"👤 **Профіль:**\n"
        f"🆔 Id: `{m.from_user.id}`\n"
        f"💰 Баланс: `{u['balance']}` 💎\n\n"
        f"👥 Запрошених гравців: `{u.get('referals_count', 0)}`👤\n"
        f"📈 Оборот: `{u.get('turnover', 0.0)}` 💎\n"
        f" bowling Зіграно ігор: `{u.get('games_played', 0)}`"
    )
    await m.answer(text, parse_mode="Markdown")

# --- РЕФЕРАЛКА (НОВИЙ ТЕКСТ) ---
@dp.message(F.text == "🤝 Рефералка")
async def referal_cmd(m: types.Message):
    me = await bot.get_me()
    link = f"https://t.me/{me.username}?start={m.from_user.id}"
    text = (
        f"🤝 **Вітаю! Це реферальна система.**\n\n"
        f"Ви отримуєте **0.01%** з виграшу гравців, яких ви запросили!\n\n"
        f"🔗 Ваше посилання: `{link}`"
    )
    await m.answer(text, parse_mode="Markdown")

# --- ПАНЕЛЬ АДМІНА ---
@dp.message(F.text == "🛡 Панель адміна")
async def admin_panel(m: types.Message):
    if m.from_user.id != ADMIN_ID: return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👥 Список гравців", callback_data="adm_list")],
        [InlineKeyboardButton(text="📢 Розсилка", callback_data="adm_bc")]
    ])
    await m.answer("⚙️ Меню адміністратора:", reply_markup=kb)

# СПИСОК ГРАВЦІВ
@dp.callback_query(F.data == "adm_list")
async def admin_list(cb: types.CallbackQuery):
    users = await users_col.find().limit(20).to_list(length=20)
    buttons = []
    for u in users:
        buttons.append([InlineKeyboardButton(text=f"👤 ID: {u['_id']}", callback_data=f"info_{u['_id']}")])
    
    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    await cb.message.edit_text("Оберіть гравця:", reply_markup=kb)

# КЕРУВАННЯ ГРАВЦЕМ
@dp.callback_query(F.data.startswith("info_"))
async def admin_user_info(cb: types.CallbackQuery):
    target_id = int(cb.data.split("_")[1])
    u = await users_col.find_one({"_id": target_id})
    
    status = "🚫 ЗАБЛОКОВАНИЙ" if u.get('is_blocked') else "✅ Активний"
    text = (
        f"📊 **Керування гравцем:** `{target_id}`\n\n"
        f"💰 Баланс: `{u['balance']}`\n"
        f"📈 Оборот: `{u.get('turnover', 0)}`\n"
        f"🎳 Ігор: `{u.get('games_played', 0)}`\n"
        f"⚙️ Статус: {status}"
    )
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🧹 Обнулити баланс", callback_data=f"m_reset_{target_id}")],
        [InlineKeyboardButton(text="🔒 Блок/Розблок", callback_data=f"m_block_{target_id}")],
        [InlineKeyboardButton(text="✉️ Написати йому", callback_data=f"m_write_{target_id}")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="adm_list")]
    ])
    await cb.message.edit_text(text, reply_markup=kb, parse_mode="Markdown")

# ДІЇ АДМІНА
@dp.callback_query(F.data.startswith("m_"))
async def admin_actions(cb: types.CallbackQuery, state: FSMContext):
    action = cb.data.split("_")[1]
    target_id = int(cb.data.split("_")[2])

    if action == "reset":
        await users_col.update_one({"_id": target_id}, {"$set": {"balance": 0.0}})
        await cb.answer("✅ Баланс обнулено")
        await admin_user_info(cb)
    
    elif action == "block":
        u = await users_col.find_one({"_id": target_id})
        new_status = not u.get('is_blocked', False)
        await users_col.update_one({"_id": target_id}, {"$set": {"is_blocked": new_status}})
        await cb.answer("✅ Статус змінено")
        await admin_user_info(cb)
    
    elif action == "write":
        await state.update_data(write_id=target_id)
        await cb.message.answer("Введіть текст повідомлення для юзера:")
        await state.set_state(Form.admin_write_user)

@dp.message(Form.admin_write_user)
async def admin_write_final(m: types.Message, state: FSMContext):
    data = await state.get_data()
    try:
        await bot.send_message(data['write_id'], f"💬 **Повідомлення від адміна:**\n\n{m.text}", parse_mode="Markdown")
        await m.answer("✅ Відправлено!")
    except:
        await m.answer("❌ Не вдалося надіслати.")
    await state.clear()

# --- GAMES & БАЛАНС (БЕЗ ЗМІН) ---
@dp.message(F.text == "🎮 Games")
async def games_cmd(m: types.Message):
    await m.answer("Оберіть гру (Bowling):")

@dp.message(F.text == "💎 Баланс")
async def balance_cmd(m: types.Message):
    u = await get_u(m.from_user.id)
    await m.answer(f"💎 Ваш баланс: {u['balance']} 💎")

# --- СТАРТ ---
async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
