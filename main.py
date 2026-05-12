import os, uuid, logging, asyncio
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    ReplyKeyboardMarkup, KeyboardButton, 
    InlineKeyboardMarkup, InlineKeyboardButton
)
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

# --- КОНФІГУРАЦІЯ ---
load_dotenv()
API_TOKEN = os.getenv("BOT_TOKEN") 
MONGO_URL = os.getenv("MONGO_URL")
ADMIN_ID = int(os.getenv("ADMIN_ID"))
CURRATE = 44.50 

logging.basicConfig(level=logging.INFO)
bot = Bot(token=API_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

cluster = AsyncIOMotorClient(MONGO_URL)
db = cluster["game_bot_db"] 
users_col = db["users"]
games_col = db["games"]
finance_col = db["finance"]

# --- СТАНИ ---
class GameStates(StatesGroup):
    wait_rounds = State()
    wait_bet = State()

class FinanceStates(StatesGroup):
    wait_dep_amount = State()
    wait_receipt = State()
    wait_withdraw_amount = State()
    wait_withdraw_details = State()

# --- КЛАВІАТУРИ ---
def main_kb():
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="👤 Профіль"), KeyboardButton(text="🎮 Games")],
        [KeyboardButton(text="💎 Баланс"), KeyboardButton(text="🤝 Рефералка")],
        [KeyboardButton(text="⚙️ Налаштування")]
    ], resize_keyboard=True)

def cancel_kb():
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="❌ Скасувати")]], resize_keyboard=True)

# --- БАЗОВІ ФУНКЦІЇ (ВИПРАВЛЕНО РЕФЕРАЛКУ) ---
async def get_u(uid, ref_id=None):
    u = await users_col.find_one({"_id": uid})
    if not u:
        # Створення нового користувача
        u = {
            "_id": uid, 
            "balance": 0.0, 
            "referals_count": 0, 
            "turnover": 0.0, 
            "games_played": 0, 
            "referrer": ref_id
        }
        await users_col.insert_one(u)
        # Якщо є реферер — додаємо йому +1 до лічильника
        if ref_id and ref_id != uid:
            await users_col.update_one({"_id": ref_id}, {"$inc": {"referals_count": 1}})
    return u

# --- АДМІН КОМАНДИ ---
@dp.message(Command("give_bonus"))
async def adm_give_bonus(m: types.Message):
    if m.from_user.id != ADMIN_ID: return
    try:
        _, uid, amt = m.text.split()
        await users_col.update_one({"_id": int(uid)}, {"$inc": {"balance": float(amt)}})
        await m.answer(f"✅ Бонус `{amt} 💎` нараховано користувачу `{uid}`")
        await bot.send_message(int(uid), f"🎁 Адміністратор нарахував вам бонус: `+{amt} 💎`!")
    except: await m.answer("Формат: `/give_bonus ID сума`")

@dp.message(Command("set_balance"))
async def adm_set_bal(m: types.Message):
    if m.from_user.id != ADMIN_ID: return
    try:
        _, uid, amt = m.text.split()
        await users_col.update_one({"_id": int(uid)}, {"$set": {"balance": float(amt)}})
        await m.answer(f"✅ Баланс користувача `{uid}` встановлено на `{amt} 💎`")
    except: await m.answer("Формат: `/set_balance ID сума`")

# --- ГЛОБАЛЬНІ ОБРОБНИКИ СКАСУВАННЯ (ВИПРАВЛЯЮТЬ ЦИКЛ ПОМИЛОК) ---
@dp.message(F.text.in_(["❌ Скасувати", "⬅️ Назад"]))
async def global_cancel(m: types.Message, state: FSMContext):
    await state.clear()
    await m.answer("Дію скасовано. Повернення в меню.", reply_markup=main_kb())

# --- ЛОГІКА ІГОР ---
@dp.message(F.text == "🎮 Games")
async def games_menu(m: types.Message):
    kb = ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="Гра Боулінг 🎳"), KeyboardButton(text="Гра Кубик 🎲")],
        [KeyboardButton(text="⬅️ Назад")]
    ], resize_keyboard=True)
    await m.answer("Оберіть режим гри:", reply_markup=kb)

@dp.message(F.text.in_(["Гра Боулінг 🎳", "Гра Кубик 🎲"]))
async def choose_rounds(m: types.Message, state: FSMContext):
    g_type = "🎳" if "Боулінг" in m.text else "🎲"
    await state.update_data(g_type=g_type)
    kb = ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="1 раунд"), KeyboardButton(text="5 раундів")],
        [KeyboardButton(text="❌ Скасувати")]
    ], resize_keyboard=True)
    await m.answer(f"Ви обрали {g_type}. Оберіть кількість раундів:", reply_markup=kb)
    await state.set_state(GameStates.wait_rounds)

@dp.message(GameStates.wait_rounds, F.text.in_(["1 раунд", "5 раундів"]))
async def choose_bet(m: types.Message, state: FSMContext):
    r = 1 if "1" in m.text else 5
    await state.update_data(rounds=r)
    await m.answer(f"Введіть суму вашої ставки (💎):", reply_markup=cancel_kb())
    await state.set_state(GameStates.wait_bet)

@dp.message(GameStates.wait_bet)
async def process_bet(m: types.Message, state: FSMContext):
    # Жорстка перевірка на кнопки, щоб уникнути помилки "Введіть число"
    if m.text in ["❌ Скасувати", "👤 Профіль", "🎮 Games", "💎 Баланс", "🤝 Рефералка"]: return
    
    try:
        bet = float(m.text.replace(",", "."))
        u = await get_u(m.from_user.id)
        if u['balance'] < bet: return await m.answer("❌ Недостатньо коштів!")
        
        data = await state.get_data()
        gid = str(uuid.uuid4())[:8]
        await games_col.insert_one({
            "game_id": gid, "creator_id": m.from_user.id, "opponent_id": None,
            "type": data['g_type'], "bet": bet, "rounds_total": data['rounds'],
            "status": "waiting", "c_score": [], "o_score": []
        })
        me = await bot.get_me()
        await m.answer(f"✅ Гра створена!\n🔗 Посилання для друга:\n`https://t.me/{me.username}?start=game_{gid}`", reply_markup=main_kb(), parse_mode="Markdown")
        await state.clear()
    except:
        await m.answer("Будь ласка, введіть числове значення ставки!")

@dp.message(F.dice)
async def handle_dice(m: types.Message):
    g = await games_col.find_one({"status": "playing", "turn": m.from_user.id})
    if not g or m.dice.emoji != g['type']: return

    is_c = (m.from_user.id == g['creator_id'])
    opp_id = g['opponent_id'] if is_c else g['creator_id']
    field = "c_score" if is_c else "o_score"
    
    await games_col.update_one({"game_id": g['game_id']}, {"$push": {field: m.dice.value}})
    await m.forward(chat_id=opp_id)
    
    # Емодзі у форматі `code` для швидкого копіювання одним тапом
    await bot.send_message(
        opp_id, 
        f"👀 Суперник вибив: **{m.dice.value}**\n\n🔔 **ТВІЙ ХІД!**\nКидай `{g['type']}` сюди!", 
        parse_mode="Markdown"
    )

    upd = await games_col.find_one({"game_id": g['game_id']})
    if len(upd['c_score']) == upd['rounds_total'] and len(upd['o_score']) == upd['rounds_total']:
        await finish_game(upd)
    else:
        next_t = upd['creator_id'] if len(upd['c_score']) == len(upd['o_score']) else upd['opponent_id']
        await games_col.update_one({"game_id": g['game_id']}, {"$set": {"turn": next_t}})

async def finish_game(g):
    s1, s2 = sum(g['c_score']), sum(g['o_score'])
    win_sum = g['bet'] * 1.98
    
    if s1 == s2:
        await users_col.update_many({"_id": {"$in": [g['creator_id'], g['opponent_id']]}}, {"$inc": {"balance": g['bet']}})
        for uid in [g['creator_id'], g['opponent_id']]: await bot.send_message(uid, f"🤝 Нічия ({s1}:{s2})! Ставки повернуті.")
    else:
        win_id = g['creator_id'] if s1 > s2 else g['opponent_id']
        lose_id = g['opponent_id'] if s1 > s2 else g['creator_id']
        await users_col.update_one({"_id": win_id}, {"$inc": {"balance": win_sum, "turnover": g['bet']}})
        await bot.send_message(win_id, f"🏆 Ви перемогли! Нараховано: `+{win_sum:.2f} 💎`", parse_mode="Markdown")
        await bot.send_message(lose_id, "❌ Ви програли.")
    
    await games_col.update_one({"game_id": g['game_id']}, {"$set": {"status": "finished"}})

# --- ПРОФІЛЬ ТА РЕФЕРАЛКА ---
@dp.message(F.text == "👤 Профіль")
async def profile_cmd(m: types.Message):
    u = await get_u(m.from_user.id)
    text = (f"👤 **Ваш Профіль**\n\n🆔 ID: `{m.from_user.id}`\n💰 Баланс: `{u['balance']:.2f} 💎`\n"
            f"📈 Оборот: `{u.get('turnover', 0.0):.2f} 💎`\n👥 Рефералів: `{u.get('referals_count', 0)}` осіб")
    await m.answer(text, parse_mode="Markdown")

@dp.message(F.text == "🤝 Рефералка")
async def ref_cmd(m: types.Message):
    u = await get_u(m.from_user.id)
    me = await bot.get_me()
    link = f"https://t.me/{me.username}?start={m.from_user.id}"
    text = (f"🤝 **Реферальна система**\n\nЗапрошуй друзів та отримуй бонуси від їхніх ігор!\n"
            f"👥 Вже запрошено: `{u.get('referals_count', 0)}` осіб\n\n🔗 Твоє посилання:\n`{link}`")
    await m.answer(text, parse_mode="Markdown")

# --- СТАРТ ТА ПРИЄДНАННЯ ---
@dp.message(Command("start"))
async def start_handler(m: types.Message, state: FSMContext):
    await state.clear()
    args = m.text.split()
    
    ref_id = None
    if len(args) > 1:
        if args[1].startswith("game_"):
            gid = args[1].replace("game_", "")
            g = await games_col.find_one({"game_id": gid, "status": "waiting"})
            if g and g['creator_id'] != m.from_user.id:
                u2 = await get_u(m.from_user.id)
                if u2['balance'] >= g['bet']:
                    await users_col.update_many({"_id": {"$in": [g['creator_id'], m.from_user.id]}}, {"$inc": {"balance": -g['bet']}})
                    await games_col.update_one({"game_id": gid}, {"$set": {"opponent_id": m.from_user.id, "status": "playing", "turn": g['creator_id']}})
                    await bot.send_message(g['creator_id'], f"🎮 Гра почалася!\n🎲 Твій хід! Кидай `{g['type']}`")
                    await m.answer(f"🎮 Ви приєдналися! Очікуйте на хід суперника...")
                else: await m.answer("❌ Недостатньо коштів!")
        elif args[1].isdigit():
            ref_id = int(args[1])
    
    await get_u(m.from_user.id, ref_id)
    await m.answer("💎 Ласкаво просимо!", reply_markup=main_kb())

async def main(): await dp.start_polling(bot)
if __name__ == "__main__": asyncio.run(main())
