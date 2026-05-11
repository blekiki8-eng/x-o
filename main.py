import os, uuid, logging, asyncio, random
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
API_TOKEN = os.getenv("BOT_TOKEN") 
MONGO_URL = os.getenv("MONGO_URL")
ADMIN_ID = int(os.getenv("ADMIN_ID")) if os.getenv("ADMIN_ID") else 0

logging.basicConfig(level=logging.INFO)
bot = Bot(token=API_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

cluster = AsyncIOMotorClient(MONGO_URL)
db = cluster["game_bot_db"] 
users_col = db["users"]
games_col = db["games"]

# --- СТАНИ ---
class States(StatesGroup):
    wait_bet = State()
    wait_dep = State()
    wait_wit = State()
    wait_support = State()

# --- МОВНІ ПАКЕТИ ТА ТЕКСТИ ---
STRINGS = {
    'ua': {
        'profile': "👤 **Профіль:**\n🆔 Id: `{id}`\n💰 Баланс: `{bal}` 💎\n\n👥 Запрошених гравців: `{ref}`\n📈 Оборот: `{turn}`\n🎳 Зіграно ігор: `{games}`",
        'ref': "🤝 Вітаю, це реферальна система, за яку ви получаєте **0.01%** з виграшу гравців яких ви запросили!\n\n🔗 Посилання: `{link}`",
        'bal_text': "💰 Ваш баланс: `{bal}` 💎",
        'dep_min': "❌ Мінімальна сума поповнення: 0.50 💎",
        'wit_min': "❌ Мінімальна сума виведення: 4.0 💎",
        'game_created': "✅ **Гра створена!**\nГра: {type}\nСтавка: {bet} 💎\nРаундів: {rounds}\n\nКиньте посилання другу:\n`{link}`"
    }
}

# --- КЛАВІАТУРИ ---
def main_kb(uid, lang='ua'):
    kb = [
        [KeyboardButton(text="👤 Профіль"), KeyboardButton(text="🎮 Games")],
        [KeyboardButton(text="💎 Баланс"), KeyboardButton(text="🤝 Рефералка")],
        [KeyboardButton(text="⚙️ Налаштування")]
    ]
    if uid == ADMIN_ID: kb.append([KeyboardButton(text="🛡 Панель адміна")])
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

def games_choice_kb():
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="🎳"), KeyboardButton(text="🎲")], [KeyboardButton(text="⬅️ Назад")]], resize_keyboard=True)

def rounds_kb():
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="1 кидок"), KeyboardButton(text="5 кидків")], [KeyboardButton(text="⬅️ Назад")]], resize_keyboard=True)

# --- ЛОГІКА ЮЗЕРІВ ---
async def get_u(uid):
    u = await users_col.find_one({"_id": uid})
    if not u:
        u = {"_id": uid, "balance": 0.0, "referals_count": 0, "turnover": 0.0, "games_played": 0, "lang": "ua"}
        await users_col.insert_one(u)
    return u

# --- ОБРОБНИКИ ОСНОВНИХ КНОПОК ---

@dp.message(Command("start"))
async def cmd_start(m: types.Message, state: FSMContext):
    await state.clear()
    u = await get_u(m.from_user.id)
    # Рефералка та приєднання до гри
    if len(m.text.split()) > 1:
        param = m.text.split()[1]
        if param.startswith("game_"):
            gid = param.replace("game_", "")
            return await join_game(m, gid)
    await m.answer("💎 Вітаємо!", reply_markup=main_kb(m.from_user.id))

@dp.message(F.text == "👤 Профіль")
async def profile_cmd(m: types.Message):
    u = await get_u(m.from_user.id)
    await m.answer(STRINGS['ua']['profile'].format(id=m.from_user.id, bal=u['balance'], ref=u.get('referals_count',0), turn=u.get('turnover',0), games=u.get('games_played',0)), parse_mode="Markdown")

@dp.message(F.text == "🤝 Рефералка")
async def ref_cmd(m: types.Message):
    me = await bot.get_me()
    link = f"https://t.me/{me.username}?start={m.from_user.id}"
    await m.answer(STRINGS['ua']['ref'].format(link=link), parse_mode="Markdown")

# --- СИСТЕМА GAMES ---

@dp.message(F.text == "🎮 Games")
async def games_cmd(m: types.Message):
    await m.answer("Оберіть режим гри:", reply_markup=games_choice_kb())

@dp.message(F.text.in_(["🎳", "🎲"]))
async def choose_type(m: types.Message, state: FSMContext):
    await state.update_data(g_type=m.text)
    await m.answer("Оберіть кількість кидків:", reply_markup=rounds_kb())

@dp.message(F.text.in_(["1 кидок", "5 кидків"]))
async def choose_rounds(m: types.Message, state: FSMContext):
    r = 1 if "1" in m.text else 5
    await state.update_data(rounds=r)
    await m.answer(f"Введіть суму ставки (мінімум 0.05 💎):")
    await state.set_state(States.wait_bet)

@dp.message(States.wait_bet)
async def create_game_final(m: types.Message, state: FSMContext):
    try:
        bet = float(m.text)
        if bet < 0.05: return await m.answer("❌ Мінімальна ставка 0.05")
        u = await get_u(m.from_user.id)
        if u['balance'] < bet: return await m.answer("❌ Недостатньо балансу")
        
        data = await state.get_data()
        gid = str(uuid.uuid4())[:8]
        
        game_obj = {
            "game_id": gid, "creator_id": m.from_user.id, "opponent_id": None,
            "type": data['g_type'], "bet": bet, "rounds_total": data['rounds'],
            "status": "waiting", "c_score": [], "o_score": [], "turn": m.from_user.id
        }
        await games_col.insert_one(game_obj)
        
        me = await bot.get_me()
        link = f"https://t.me/{me.username}?start=game_{gid}"
        await m.answer(STRINGS['ua']['game_created'].format(type=data['g_type'], bet=bet, rounds=data['rounds'], link=link), reply_markup=main_kb(m.from_user.id), parse_mode="Markdown")
        await state.clear()
    except: await m.answer("❌ Введіть число")

# --- ЛОГІКА ГРИ (ПРИЄДНАННЯ ТА ХОДИ) ---

async def join_game(m: types.Message, gid):
    game = await games_col.find_one({"game_id": gid, "status": "waiting"})
    if not game: return await m.answer("❌ Гра не знайдена або вже почалась")
    if game['creator_id'] == m.from_user.id: return await m.answer("❌ Ви не можете грати самі з собою")
    
    u = await get_u(m.from_user.id)
    if u['balance'] < game['bet']: return await m.answer("❌ У вас недостатньо балансу для входу")
    
    # Списання ставок
    await users_col.update_many({"_id": {"$in": [game['creator_id'], m.from_user.id]}}, {"$inc": {"balance": -game['bet'], "turnover": game['bet'], "games_played": 1}})
    await games_col.update_one({"game_id": gid}, {"$set": {"opponent_id": m.from_user.id, "status": "playing"}})
    
    txt = f"Гра створена ✅\nГра: {game['type']}\nСтавка: {game['bet']}\nВиграш: {game['bet']*1.98}\n\nПершим ходить той, хто створив гру!"
    await bot.send_message(game['creator_id'], txt + "\n\n**Ваш хід! Кидайте емодзі гри!**")
    await m.answer(txt + "\n\nОчікуйте ходу суперника...")

@dp.message(F.dice)
async def handle_dice(m: types.Message):
    uid = m.from_user.id
    game = await games_col.find_one({"status": "playing", "$or": [{"creator_id": uid}, {"opponent_id": uid}]})
    if not game: return
    
    if game['turn'] != uid: return await m.answer("❌ Зараз не ваш хід!")
    
    # Перевірка чи той емодзі кинули
    emoji = "🎳" if game['type'] == "🎳" else "🎲"
    if m.dice.emoji != emoji: return await m.answer(f"❌ Кидайте {emoji}!")

    val = m.dice.value
    is_creator = (uid == game['creator_id'])
    
    if is_creator:
        await games_col.update_one({"game_id": game['game_id']}, {"$push": {"c_score": val}, "$set": {"turn": game['opponent_id']}})
        next_id = game['opponent_id']
    else:
        await games_col.update_one({"game_id": game['game_id']}, {"$push": {"o_score": val}, "$set": {"turn": game['creator_id']}})
        next_id = game['creator_id']

    await asyncio.sleep(3) # Чекаємо анімацію
    await m.answer(f"У вас випало/вибило {val} очок")
    
    # Перевірка кінця гри
    updated_game = await games_col.find_one({"game_id": game['game_id']})
    if len(updated_game['c_score']) == updated_game['rounds_total'] and len(updated_game['o_score']) == updated_game['rounds_total']:
        await finish_game(updated_game)
    else:
        await bot.send_message(next_id, f"Ваш хід! Кидайте {emoji}")

async def finish_game(g):
    sum_c = sum(g['c_score'])
    sum_o = sum(g['o_score'])
    win_sum = g['bet'] * 1.98
    
    res = f"Гра завершена 🏁\n\nРезультати:\nТворець: {sum_c}\nСуперник: {sum_o}\n\n"
    
    if sum_c > sum_o:
        await users_col.update_one({"_id": g['creator_id']}, {"$inc": {"balance": win_sum}})
        await bot.send_message(g['creator_id'], res + f"Ви перемогли! 🏆\n+{win_sum} 💎")
        await bot.send_message(g['opponent_id'], res + "Ви програли ❌")
    elif sum_o > sum_c:
        await users_col.update_one({"_id": g['opponent_id']}, {"$inc": {"balance": win_sum}})
        await bot.send_message(g['opponent_id'], res + f"Ви перемогли! 🏆\n+{win_sum} 💎")
        await bot.send_message(g['creator_id'], res + "Ви програли ❌")
    else:
        await users_col.update_one({"_id": g['creator_id']}, {"$inc": {"balance": g['bet']}})
        await users_col.update_one({"_id": g['opponent_id']}, {"$inc": {"balance": g['bet']}})
        msg = res + "Нічия! Ставки повернуто."
        await bot.send_message(g['creator_id'], msg)
        await bot.send_message(g['opponent_id'], msg)
    
    await games_col.update_one({"game_id": g['game_id']}, {"$set": {"status": "finished"}})

# --- БАЛАНС ---

@dp.message(F.text == "💎 Баланс")
async def balance_cmd(m: types.Message):
    u = await get_u(m.from_user.id)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Поповнити", callback_data="dep"), InlineKeyboardButton(text="Вивести", callback_data="wit")]])
    await m.answer(f"Ваш баланс: {u['balance']} 💎", reply_markup=kb)

@dp.callback_query(F.data == "dep")
async def dep_cb(cb: types.CallbackQuery, state: FSMContext):
    await cb.message.answer("Введіть суму для поповнення балансу (мінімум 0.50):")
    await state.set_state(States.wait_dep)

@dp.message(States.wait_dep)
async def dep_final(m: types.Message, state: FSMContext):
    try:
        val = float(m.text)
        if val < 0.5: return await m.answer("❌ Мінімум 0.50")
        await bot.send_message(ADMIN_ID, f"📥 ЗАЯВКА НА ПОПОВНЕННЯ\nВід: `{m.from_user.id}`\nСума: {val}")
        await m.answer("✅ Заявку надіслано! Чекайте на зарахування.")
        await state.clear()
    except: await m.answer("❌ Введіть число")

@dp.callback_query(F.data == "wit")
async def wit_cb(cb: types.CallbackQuery, state: FSMContext):
    await cb.message.answer("Введіть суму для виведення (мінімум 4.0):")
    await state.set_state(States.wait_wit)

@dp.message(States.wait_wit)
async def wit_final(m: types.Message, state: FSMContext):
    try:
        val = float(m.text)
        if val < 4.0: return await m.answer("❌ Мінімум 4.0")
        u = await get_u(m.from_user.id)
        if u['balance'] < val: return await m.answer("❌ Недостатньо коштів")
        await bot.send_message(ADMIN_ID, f"📤 ЗАЯВКА НА ВИВІД\nВід: `{m.from_user.id}`\nСума: {val}")
        await m.answer("✅ Заявку надіслано на перевірку.")
        await state.clear()
    except: await m.answer("❌ Введіть число")

# --- ІНШЕ ---
@dp.message(F.text == "⬅️ Назад")
async def back_cmd(m: types.Message):
    await m.answer("Головне меню:", reply_markup=main_kb(m.from_user.id))

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
