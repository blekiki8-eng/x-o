import os, uuid, logging, asyncio, time
from aiogram import Bot, Dispatcher, executor, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

# --- КОНФІГУРАЦІЯ ---
load_dotenv()
API_TOKEN = os.getenv("BOT_TOKEN")
MONGO_URL = os.getenv("MONGO_URL")
ADMIN_ID = int(os.getenv("ADMIN_ID")) if os.getenv("ADMIN_ID") else 0

EXCHANGE_RATE = 44.50
MIN_WITHDRAW = 4.0

logging.basicConfig(level=logging.INFO)
storage = MemoryStorage()
bot = Bot(token=API_TOKEN)
dp = Dispatcher(bot, storage=storage)

cluster = AsyncIOMotorClient(MONGO_URL)
db = cluster["fishcash_game"]
users_col = db["users"]
games_col = db["games"]

# --- СТАНИ ---
class Registration(StatesGroup):
    wait_nickname = State()

class WithdrawState(StatesGroup):
    wait_amount = State()
    wait_details = State()

# --- КЛАВІАТУРИ ---
def main_menu(user_id):
    markup = ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add(KeyboardButton("👤 Профіль"), KeyboardButton("🎮 Ігри"))
    markup.add(KeyboardButton("💎 Баланс"))
    if user_id == ADMIN_ID:
        markup.add(KeyboardButton("🛡 Панель адміна"))
    return markup

# --- ОБРОБНИК /START ---
@dp.message_handler(commands=['start'], state="*")
async def start_cmd(m: types.Message, state: FSMContext):
    await state.finish()
    user = await users_col.find_one({"_id": m.from_user.id})
    
    if not user:
        await m.answer("👋 Вітаю! Будь ласка, введіть ваш нікнейм для реєстрації:")
        await Registration.wait_nickname.set()
    else:
        await m.answer(f"З поверненням, {user.get('nickname', 'Гравець')}!", reply_markup=main_menu(m.from_user.id))

@dp.message_handler(state=Registration.wait_nickname)
async def register_nickname(m: types.Message, state: FSMContext):
    nick = m.text.strip()[:15]
    if len(nick) < 2:
        return await m.answer("❌ Нік занадто короткий. Спробуйте ще раз:")
    
    await users_col.update_one(
        {"_id": m.from_user.id},
        {"$set": {"nickname": nick, "balance": 0.0, "banned": False}},
        upsert=True
    )
    await m.answer(f"✅ Нікнейм **{nick}** встановлено!", parse_mode="Markdown", reply_markup=main_menu(m.from_user.id))
    await state.finish()

# --- ПРОФІЛЬ (ВИПРАВЛЕНО) ---
@dp.message_handler(lambda m: m.text == "👤 Профіль", state="*")
async def profile_view(m: types.Message):
    user = await users_col.find_one({"_id": m.from_user.id})
    if not user:
        return await start_cmd(m, None)
    
    nick = user.get("nickname", "Не встановлено")
    bal = user.get("balance", 0.0)
    
    text = (
        f"👤 **Твій профіль**\n\n"
        f"📝 Нік: `{nick}`\n"
        f"🆔 ID: `{m.from_user.id}`\n"
        f"💰 Баланс: {round(bal, 2)} 💎"
    )
    await m.answer(text, parse_mode="Markdown")

# --- АДМІН-ПАНЕЛЬ (ВИПРАВЛЕНО) ---
@dp.message_handler(lambda m: m.text == "🛡 Панель адміна", state="*")
async def admin_panel(m: types.Message):
    if m.from_user.id != ADMIN_ID:
        return
    count = await users_col.count_documents({})
    kb = InlineKeyboardMarkup().add(InlineKeyboardButton("👥 Список гравців", callback_data="adm_list"))
    await m.answer(f"🛡 **Адмін-панель**\n\nВсього гравців: {count}", reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data == "adm_list", state="*")
async def admin_list(c: types.CallbackQuery):
    if c.from_user.id != ADMIN_ID: return
    
    users = await users_col.find().to_list(length=50)
    kb = InlineKeyboardMarkup(row_width=1)
    
    for u in users:
        # Безпечне отримання ніка та балансу
        nick = u.get("nickname", "Без ніка")
        uid = u.get("_id")
        bal = u.get("balance", 0.0)
        status = "🚫" if u.get("banned") else "👤"
        
        kb.add(InlineKeyboardButton(
            f"{status} {nick} | {bal} 💎", 
            callback_data=f"man_{uid}"
        ))
    
    await c.message.edit_text("Оберіть гравця для керування:", reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data.startswith(('man_', 'res_', 'bn_')), state="*")
async def admin_actions(c: types.CallbackQuery):
    if c.from_user.id != ADMIN_ID: return
    
    parts = c.data.split("_")
    action = parts[0]
    target_id = int(parts[1])
    
    if action == "res":
        await users_col.update_one({"_id": target_id}, {"$set": {"balance": 0.0}})
        await c.answer("Баланс обнулено")
    elif action == "bn":
        u = await users_col.find_one({"_id": target_id})
        new_status = not u.get("banned", False)
        await users_col.update_one({"_id": target_id}, {"$set": {"banned": new_status}})
        await c.answer("Статус змінено")

    # Повернення до картки юзера
    u = await users_col.find_one({"_id": target_id})
    bt = "Розблокувати" if u.get("banned") else "Заблокувати"
    
    kb = InlineKeyboardMarkup(row_width=1).add(
        InlineKeyboardButton("💰 Обнулити баланс", callback_data=f"res_{target_id}"),
        InlineKeyboardButton(f"⛔️ {bt}", callback_data=f"bn_{target_id}"),
        InlineKeyboardButton("⬅️ Назад", callback_data="adm_list")
    )
    
    await c.message.edit_text(
        f"👤 Гравець: {u.get('nickname')}\n"
        f"🆔 ID: {target_id}\n"
        f"💰 Баланс: {u.get('balance')} 💎\n"
        f"Статус: {'Заблокований' if u.get('banned') else 'Активний'}",
        reply_markup=kb
    )

# --- БАЛАНС ---
@dp.message_handler(lambda m: m.text == "💎 Баланс", state="*")
async def balance_menu(m: types.Message):
    u = await users_col.find_one({"_id": m.from_user.id})
    if not u: return
    
    kb = InlineKeyboardMarkup().add(
        InlineKeyboardButton("💳 Поповнити", callback_data="dep"),
        InlineKeyboardButton("📤 Вивести", callback_data="wit")
    )
    await m.answer(f"Твій баланс: **{u.get('balance', 0.0)} 💎**", reply_markup=kb, parse_mode="Markdown")

# --- СКАСУВАННЯ ---
@dp.message_handler(state="*", text="❌ Скасувати")
async def cancel_handler(m: types.Message, state: FSMContext):
    await state.finish()
    await m.answer("Дію скасовано.", reply_markup=main_menu(m.from_user.id))

if __name__ == '__main__':
    executor.start_polling(dp, skip_updates=True)
