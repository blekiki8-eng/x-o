import os, uuid, logging, asyncio
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove, InlineKeyboardMarkup, InlineKeyboardButton
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

# --- НАЛАШТУВАННЯ ---
load_dotenv()
API_TOKEN = os.getenv("BOT_TOKEN") 
MONGO_URL = os.getenv("MONGO_URL")
ADMIN_ID = int(os.getenv("ADMIN_ID"))

logging.basicConfig(level=logging.INFO)
bot = Bot(token=API_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

cluster = AsyncIOMotorClient(MONGO_URL)
db = cluster["game_bot_db"] 
users_col = db["users"]
games_col = db["games"]

class GameStates(StatesGroup):
    wait_bet = State()

# --- КЛАВІАТУРИ ---
def main_kb():
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="👤 Профіль"), KeyboardButton(text="🎮 Games")],
        [KeyboardButton(text="💎 Баланс"), KeyboardButton(text="🤝 Рефералка")],
        [KeyboardButton(text="⚙️ Налаштування")]
    ], resize_keyboard=True)

def games_kb():
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="Гра Боулінг 🎳"), KeyboardButton(text="Гра Кубик 🎲")],
        [KeyboardButton(text="⬅️ Назад")]
    ], resize_keyboard=True)

# --- ДОПОМІЖНІ ФУНКЦІЇ ---
async def get_u(uid):
    u = await users_col.find_one({"_id": uid})
    if not u:
        u = {"_id": uid, "balance": 0.0, "referals_count": 0, "turnover": 0.0, "games_played": 0}
        await users_col.insert_one(u)
    return u

# --- КОМАНДИ ТА ПАНЕЛІ ---

@dp.message(Command("start"))
async def cmd_start(m: types.Message, state: FSMContext):
    await state.clear()
    await get_u(m.from_user.id)
    if len(m.text.split()) > 1:
        param = m.text.split()[1]
        if param.startswith("game_"):
            return await join_game_logic(m, param.replace("game_", ""))
    await m.answer("💎 Вітаємо у Different Games!", reply_markup=main_kb())

@dp.message(F.text == "👤 Профіль")
async def profile_cmd(m: types.Message):
    u = await get_u(m.from_user.id)
    text = (f"👤 **Профіль:**\n\n🆔 Id: `{m.from_user.id}`\n💰 Баланс: `{u['balance']:.2f}` 💎\n\n"
            f"Запрошених гравців: `{u.get('referals_count', 0)}`👤\n"
            f"Оборот: `{u.get('turnover', 0.0):.2f}` 💎\n"
            f"Зіграно ігор: `{u.get('games_played', 0)}` 🎳")
    await m.answer(text, parse_mode="Markdown")

@dp.message(F.text == "🤝 Рефералка")
async def ref_cmd(m: types.Message):
    me = await bot.get_me()
    link = f"https://t.me/{me.username}?start={m.from_user.id}"
    await m.answer(f"🤝 **Реферальна система**\n\nВи отримуєте бонус з кожного виграшу друга!\n\n🔗 Ваше посилання:\n`{link}`", parse_mode="Markdown")

@dp.message(F.text == "⚙️ Налаштування")
async def settings(m: types.Message):
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🆘 Підтримка", url="https://t.me/vex0o0")]])
    await m.answer("⚙️ **Налаштування:**", reply_markup=kb, parse_mode="Markdown")

@dp.message(F.text == "⬅️ Назад")
async def back_to_main(m: types.Message):
    await m.answer("Головне меню:", reply_markup=main_kb())

# --- ЛОГІКА ІГОР ---

@dp.message(F.text == "🎮 Games")
async def games_menu(m: types.Message):
    await m.answer("Оберіть гру:", reply_markup=games_kb())

@dp.message(F.text.in_(["Гра Боулінг 🎳", "Гра Кубик 🎲"]))
async def choose_game(m: types.Message, state: FSMContext):
    g_type = "🎳" if "Боулінг" in m.text else "🎲"
    await state.update_data(g_type=g_type)
    await m.answer("Введіть ставку (мінімум 0.05):", reply_markup=ReplyKeyboardRemove())
    await state.set_state(GameStates.wait_bet)

@dp.message(GameStates.wait_bet)
async def enter_bet(m: types.Message, state: FSMContext):
    try:
        bet = float(m.text.replace(",", "."))
        u = await get_u(m.from_user.id)
        if u['balance'] < bet: return await m.answer("❌ Недостатньо балансу")
        
        data = await state.get_data()
        gid = str(uuid.uuid4())[:8]
        
        await games_col.insert_one({
            "game_id": gid, "creator_id": m.from_user.id, "opponent_id": None,
            "type": data['g_type'], "bet": bet, "status": "waiting"
        })
        
        me = await bot.get_me()
        link = f"https://t.me/{me.username}?start=game_{gid}"
        await m.answer(f"Посилання на гру: {link}\n\nПерешліть це посилання другу", reply_markup=main_kb())
        await state.clear()
    except: await m.answer("Введіть число!")

async def join_game_logic(m, gid):
    g = await games_col.find_one({"game_id": gid, "status": "waiting"})
    if not g or g['creator_id'] == m.from_user.id: return await m.answer("❌ Гра недоступна")
    
    u2 = await get_u(m.from_user.id)
    if u2['balance'] < g['bet']: return await m.answer("❌ Мало коштів")
    
    await users_col.update_many({"_id": {"$in": [g['creator_id'], m.from_user.id]}}, {"$inc": {"balance": -g['bet'], "games_played": 1}})
    await games_col.update_one({"game_id": gid}, {"$set": {"opponent_id": m.from_user.id, "status": "playing", "turn": g['creator_id'], "c_score": None, "o_score": None}})
    
    await bot.send_message(g['creator_id'], "Гра створена ✅\n\nВаш хід! Кидайте стікер " + g['type'])
    await m.answer("Гра створена ✅\n\nЗараз ходить перший гравець. Очікуйте.")

@dp.message(F.dice)
async def handle_dice(m: types.Message):
    g = await games_col.find_one({"status": "playing", "$or": [{"creator_id": m.from_user.id}, {"opponent_id": m.from_user.id}]})
    if not g or g['turn'] != m.from_user.id: return
    
    if m.dice.emoji != g['type']: return
    
    val = m.dice.value
    is_c = (m.from_user.id == g['creator_id'])
    
    if is_c:
        await games_col.update_one({"game_id": g['game_id']}, {"$set": {"c_score": val, "turn": g['opponent_id']}})
        await asyncio.sleep(3)
        await bot.send_message(g['opponent_id'], f"Суперник кинув {val}.\n\nТепер ваш хід! Кидайте {g['type']}")
    else:
        await games_col.update_one({"game_id": g['game_id']}, {"$set": {"o_score": val, "status": "finished"}})
        await asyncio.sleep(3)
        
        c_score = g['c_score']
        o_score = val
        win_sum = g['bet'] * 1.95

        # Логіка персональних повідомлень
        if c_score > o_score:
            # Гравець 1 (Творець) виграв
            await bot.send_message(g['creator_id'], f"Результат:\nУ вас: {c_score}\nУ противника: {o_score}\n\nВітаю ви виграли +{win_sum:.2f} 💎")
            await bot.send_message(g['opponent_id'], f"Результат:\nУ вас: {o_score}\nУ противника: {c_score}\n\nНажаль ви програли ❌")
            await users_col.update_one({"_id": g['creator_id']}, {"$inc": {"balance": win_sum}})
        elif o_score > c_score:
            # Гравець 2 (Опонент) виграв
            await bot.send_message(g['opponent_id'], f"Результат:\nУ вас: {o_score}\nУ противника: {c_score}\n\nВітаю ви виграли +{win_sum:.2f} 💎")
            await bot.send_message(g['creator_id'], f"Результат:\nУ вас: {c_score}\nУ противника: {o_score}\n\nНажаль ви програли ❌")
            await users_col.update_one({"_id": g['opponent_id']}, {"$inc": {"balance": win_sum}})
        else:
            # Нічия
            for uid in [g['creator_id'], g['opponent_id']]:
                await bot.send_message(uid, f"Результат:\nВи: {c_score}\nСуперник: {o_score}\n\nНічия! Ставки повернуто.")
                await users_col.update_one({"_id": uid}, {"$inc": {"balance": g['bet']}})

# --- ІНШЕ (БАЛАНС) ---

@dp.message(F.text == "💎 Баланс")
async def bal_menu(m: types.Message):
    u = await get_u(m.from_user.id)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Поповнити", callback_data="dep")]])
    await m.answer(f"💰 Ваш баланс: `{u['balance']:.2f}` 💎", reply_markup=kb, parse_mode="Markdown")

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
