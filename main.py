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

# --- НАЛАШТУВАННЯ ---
load_dotenv()
# Переконайся, що в Railway змінна називається BOT_TOKEN
API_TOKEN = os.getenv("BOT_TOKEN") 
MONGO_URL = os.getenv("MONGO_URL")
ADMIN_ID = int(os.getenv("ADMIN_ID")) if os.getenv("ADMIN_ID") else 0

logging.basicConfig(level=logging.INFO)
bot = Bot(token=API_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# Підключення до оригінальної бази
cluster = AsyncIOMotorClient(MONGO_URL)
db = cluster["game_bot_db"] 
users_col = db["users"]
games_col = db["games"]

# --- СТАНИ ---
class AdminStates(StatesGroup):
    wait_message_text = State()

class GameStates(StatesGroup):
    wait_bet = State()

# --- ГОЛОВНЕ МЕНЮ (Точно як на скріні) ---
def get_main_kb(user_id):
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

# --- ОБРОБНИКИ КОМАНД ---

@dp.message(Command("start"))
async def cmd_start(m: types.Message, state: FSMContext):
    await state.clear()
    uid = m.from_user.id
    u = await get_u(uid)
    
    if u.get('is_blocked'):
        return await m.answer("🚫 Ваш акаунт заблоковано.")

    args = m.text.split()
    if len(args) > 1:
        payload = args[1]
        # Реферальна система
        if payload.isdigit() and int(payload) != uid:
            if not await users_col.find_one({"_id": uid}):
                await users_col.update_one({"_id": int(payload)}, {"$inc": {"referals_count": 1}})
        # Приєднання до гри
        elif payload.startswith("game_"):
            gid = payload.replace("game_", "")
            game = await games_col.find_one({"game_id": gid, "status": "waiting"})
            if game and game['creator_id'] != uid and u['balance'] >= game['bet']:
                await users_col.update_one({"_id": uid}, {"$inc": {"balance": -game['bet'], "turnover": game['bet'], "games_played": 1}})
                await games_col.update_one({"game_id": gid}, {"$set": {"opponent_id": uid, "status": "playing", "turn": game['creator_id']}})
                await bot.send_message(game['creator_id'], "🔔 Гравець приєднався! Кидайте 🎳")
                return await m.answer("🕹 Ви приєдналися до гри!")

    await m.answer("💎 Вітаємо в системі!", reply_markup=get_main_kb(uid))

# --- ПРОФІЛЬ (НОВИЙ ФОРМАТ) ---
@dp.message(F.text == "👤 Профіль")
async def profile_cmd(m: types.Message):
    u = await get_u(m.from_user.id)
    text = (
        f"👤 **Профіль:**\n"
        f"🆔 Id: `{m.from_user.id}`\n"
        f"💰 Баланс: `{u['balance']}` 💎\n\n"
        f"👥 Запрошених гравців: `{u.get('referals_count', 0)}`👤\n"
        f"📈 Оборот: `{u.get('turnover', 0.0)}` 💎\n"
        f"🎳 Зіграно ігор: `{u.get('games_played', 0)}`"
    )
    await m.answer(text, parse_mode="Markdown")

# --- РЕФЕРАЛКА (НОВИЙ ТЕКСТ) ---
@dp.message(F.text == "🤝 Рефералка")
async def referal_cmd(m: types.Message):
    bot_user = await bot.get_me()
    link = f"https://t.me/{bot_user.username}?start={m.from_user.id}"
    text = (
        f"🤝 **Вітаю! Це реферальна система.**\n\n"
        f"Ви отримуєте **0.01%** з виграшу гравців, яких ви запросили!\n\n"
        f"🔗 Ваше посилання: `{link}`"
    )
    await m.answer(text, parse_mode="Markdown")

# --- ПАНЕЛЬ АДМІНА (КЕРУВАННЯ ГРАВЦЯМИ) ---
@dp.message(F.text == "🛡 Панель адміна")
async def admin_panel(m: types.Message):
    if m.from_user.id != ADMIN_ID: return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👥 Список гравців", callback_data="adm_list_all")]
    ])
    await m.answer("🛡 Керування оригінальним ботом:", reply_markup=kb)

@dp.callback_query(F.data == "adm_list_all")
async def adm_list_all(cb: types.CallbackQuery):
    users = await users_col.find().limit(30).to_list(length=30)
    buttons = [[InlineKeyboardButton(text=f"👤 ID: {u['_id']}", callback_data=f"manage_{u['_id']}")] for u in users]
    await cb.message.edit_text("Оберіть гравця із зареєстрованих:", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))

@dp.callback_query(F.data.startswith("manage_"))
async def manage_user_card(cb: types.CallbackQuery):
    target_id = int(cb.data.split("_")[1])
    u = await users_col.find_one({"_id": target_id})
    
    status = "🔴 ЗАБАНЕНИЙ" if u.get('is_blocked') else "🟢 Активний"
    text = (
        f"📊 **КАРТКА ГРАВЦЯ:** `{target_id}`\n\n"
        f"💰 Баланс: `{u['balance']}` 💎\n"
        f"📈 Оборот: `{u.get('turnover', 0)}` 💎\n"
        f"🎳 Ігор: `{u.get('games_played', 0)}` \n"
        f"⚡️ Статус: {status}"
    )
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🧹 Обнулити баланс", callback_data=f"op_zero_{target_id}")],
        [InlineKeyboardButton(text="🚫 Заблокувати/Розблокувати", callback_data=f"op_ban_{target_id}")],
        [InlineKeyboardButton(text="✉️ Написати йому", callback_data=f"op_msg_{target_id}")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="adm_list_all")]
    ])
    await cb.message.edit_text(text, reply_markup=kb, parse_mode="Markdown")

@dp.callback_query(F.data.startswith("op_"))
async def admin_ops(cb: types.CallbackQuery, state: FSMContext):
    action = cb.data.split("_")[1]
    tid = int(cb.data.split("_")[2])

    if action == "zero":
        await users_col.update_one({"_id": tid}, {"$set": {"balance": 0.0}})
        await cb.answer("✅ Баланс очищено")
        await manage_user_card(cb)
    elif action == "ban":
        u = await users_col.find_one({"_id": tid})
        new_s = not u.get('is_blocked', False)
        await users_col.update_one({"_id": tid}, {"$set": {"is_blocked": new_s}})
        await cb.answer("✅ Статус змінено")
        await manage_user_card(cb)
    elif action == "msg":
        await state.update_data(target_msg=tid)
        await cb.message.answer(f"Введіть повідомлення для гравця {tid}:")
        await state.set_state(AdminStates.wait_message_text)

@dp.message(AdminStates.wait_message_text)
async def admin_confirm_msg(m: types.Message, state: FSMContext):
    data = await state.get_data()
    try:
        await bot.send_message(data['target_msg'], f"📩 **Повідомлення від адміністрації:**\n\n{m.text}", parse_mode="Markdown")
        await m.answer("✅ Надіслано!")
    except:
        await m.answer("❌ Не вдалося відправити.")
    await state.clear()

# --- ІНШІ РОЗДІЛИ ---
@dp.message(F.text == "🎮 Games")
async def games_page(m: types.Message):
    await m.answer("🎮 Оберіть гру в меню нижче:")

@dp.message(F.text == "💎 Баланс")
async def balance_page(m: types.Message):
    u = await get_u(m.from_user.id)
    await m.answer(f"💎 Ваш баланс: `{u['balance']}` 💎", parse_mode="Markdown")

# --- ЗАПУСК ---
async def main():
    print("Оригінальний бот запущений!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
