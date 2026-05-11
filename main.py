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
    wait_support = State()
    wait_withdraw = State()

# --- МОВНІ ПАКЕТИ ТА КНОПКИ ---
STRINGS = {
    'ua': {
        'start': "✨ Вітаємо!",
        'profile': "👤 **Профіль:**\n🆔 Id: `{id}`\n💰 Баланс: `{bal}` 💎\n\n👥 Реферали: `{ref}`\n📈 Оборот: `{turn}`\n🎳 Ігор: `{games}`",
        'btn_profile': "👤 Профіль", 'btn_games': "🎮 Games", 'btn_balance': "💎 Баланс", 'btn_ref': "🤝 Рефералка", 'btn_settings': "⚙️ Налаштування",
        'choose_rounds': "Оберіть кількість раундів:",
        'bet_prompt': "Введіть суму ставки (раундів: {rounds}):",
        'balance': "💎 Баланс: `{bal}` 💎\n\n💳 Мін. поповнення: 0.5\n💳 Мін. виведення: 4.0",
        'dep_info': "📥 **ПОПОВНЕННЯ**\n\nКарта Mono: `5355 2800 2890 2177`\nМін: 0.5 💎",
        'wit_prompt': "Введіть суму та реквізити (Мін. 4):",
    },
    'ru': {
        'start': "✨ Добро пожаловать!",
        'profile': "👤 **Профиль:**\n🆔 Id: `{id}`\n💰 Баланс: `{bal}` 💎\n\n👥 Рефералы: `{ref}`\n📈 Оборот: `{turn}`\n🎳 Игр: `{games}`",
        'btn_profile': "👤 Профиль", 'btn_games': "🎮 Games", 'btn_balance': "💎 Баланс", 'btn_ref': "🤝 Рефералка", 'btn_settings': "⚙️ Настройки",
        'choose_rounds': "Выберите количество раундов:",
        'bet_prompt': "Введите сумму ставки (раундов: {rounds}):",
        'balance': "💎 Баланс: `{bal}` 💎\n\n💳 Мин. пополнение: 0.5\n💳 Мин. вывод: 4.0",
        'dep_info': "📥 **ПОПОЛНЕНИЕ**\n\nКарта Mono: `5355 2800 2890 2177`\nМин: 0.5 💎",
        'wit_prompt': "Введите сумму и реквизиты (Мин. 4):",
    },
    'en': {
        'start': "✨ Welcome!",
        'profile': "👤 **Profile:**\n🆔 Id: `{id}`\n💰 Balance: `{bal}` 💎\n\n👥 Referrals: `{ref}`\n📈 Turnover: `{turn}`\n🎳 Games: `{games}`",
        'btn_profile': "👤 Profile", 'btn_games': "🎮 Games", 'btn_balance': "💎 Balance", 'btn_ref': "🤝 Referral", 'btn_settings': "⚙️ Settings",
        'choose_rounds': "Choose number of rounds:",
        'bet_prompt': "Enter bet amount (rounds: {rounds}):",
        'balance': "💎 Balance: `{bal}` 💎\n\n💳 Min. deposit: 0.5\n💳 Min. withdraw: 4.0",
        'dep_info': "📥 **DEPOSIT**\n\nCard Mono: `5355 2800 2890 2177`\nMin: 0.5 💎",
        'wit_prompt': "Enter amount and details (Min. 4):",
    }
}

# --- ФУНКЦІЇ ---
async def get_u(uid):
    u = await users_col.find_one({"_id": uid})
    if not u:
        u = {"_id": uid, "balance": 0.0, "referals_count": 0, "turnover": 0.0, "games_played": 0, "lang": "ua", "is_blocked": False}
        await users_col.insert_one(u)
    return u

def main_kb(lang='ua', uid=0):
    s = STRINGS[lang]
    kb = [
        [KeyboardButton(text=s['btn_profile']), KeyboardButton(text=s['btn_games'])],
        [KeyboardButton(text=s['btn_balance']), KeyboardButton(text=s['btn_ref'])],
        [KeyboardButton(text=s['btn_settings'])]
    ]
    if uid == ADMIN_ID: kb.append([KeyboardButton(text="🛡 Панель адміна")])
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

# --- ОБРОБНИКИ ---

@dp.message(Command("start"))
async def cmd_start(m: types.Message, state: FSMContext):
    await state.clear()
    u = await get_u(m.from_user.id)
    await m.answer(STRINGS[u['lang']]['start'], reply_markup=main_kb(u['lang'], m.from_user.id))

# ПРОФІЛЬ
@dp.message(F.text.regexp(r"(Профіль|Профиль|Profile)"))
async def profile_cmd(m: types.Message):
    u = await get_u(m.from_user.id)
    lang = u.get('lang', 'ua')
    txt = STRINGS[lang]['profile'].format(id=m.from_user.id, bal=u['balance'], ref=u.get('referals_count', 0), turn=u.get('turnover', 0), games=u.get('games_played', 0))
    await m.answer(txt, parse_mode="Markdown")

# GAMES -> КНОПКА 🎳
@dp.message(F.text.contains("Games"))
async def games_menu(m: types.Message):
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🎳", callback_data="game_bowling_select")]])
    await m.answer("🕹 Games:", reply_markup=kb)

# ВИБІР РАУНДІВ
@dp.callback_query(F.data == "game_bowling_select")
async def round_select(cb: types.CallbackQuery):
    u = await get_u(cb.from_user.id)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="1 раунд", callback_data="round_1"), 
         InlineKeyboardButton(text="5 раундів", callback_data="round_5")]
    ])
    await cb.message.edit_text(STRINGS[u['lang']]['choose_rounds'], reply_markup=kb)

# СТАВКА
@dp.callback_query(F.data.startswith("round_"))
async def bet_init(cb: types.CallbackQuery, state: FSMContext):
    rounds = cb.data.split("_")[1]
    await state.update_data(rounds=rounds)
    u = await get_u(cb.from_user.id)
    await cb.message.answer(STRINGS[u['lang']]['bet_prompt'].format(rounds=rounds))
    await state.set_state(States.wait_bet)

@dp.message(States.wait_bet)
async def bet_finish(m: types.Message, state: FSMContext):
    try:
        bet = float(m.text)
        if bet < 0.1: return await m.answer("Мін. 0.1")
        data = await state.get_data()
        rounds = data['rounds']
        u = await get_u(m.from_user.id)
        
        if u['balance'] < bet: return await m.answer("Недостатньо 💎")
        
        gid = str(uuid.uuid4())[:8]
        await games_col.insert_one({"game_id": gid, "creator_id": m.from_user.id, "bet": bet, "rounds": rounds, "status": "waiting"})
        
        link = f"https://t.me/{(await bot.get_me()).username}?start=game_{gid}"
        await m.answer(f"🎳 Гра створена на **{rounds}** раундів!\nСтавка: {bet} 💎\n\n`{link}`", parse_mode="Markdown")
        await state.clear()
    except: await m.answer("Введіть число")

# БАЛАНС
@dp.message(F.text.regexp(r"(Баланс|Balance)"))
async def bal_cmd(m: types.Message):
    u = await get_u(m.from_user.id)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📥 Поповнити", callback_data="dep"), InlineKeyboardButton(text="📤 Вивести", callback_data="wit")]
    ])
    await m.answer(STRINGS[u['lang']]['balance'].format(bal=u['balance']), reply_markup=kb, parse_mode="Markdown")

@dp.callback_query(F.data == "dep")
async def dep_call(cb: types.CallbackQuery):
    u = await get_u(cb.from_user.id)
    await cb.message.answer(STRINGS[u['lang']]['dep_info'], parse_mode="Markdown")

@dp.callback_query(F.data == "wit")
async def wit_call(cb: types.CallbackQuery, state: FSMContext):
    u = await get_u(cb.from_user.id)
    await cb.message.answer(STRINGS[u['lang']]['wit_prompt'])
    await state.set_state(States.wait_withdraw)

@dp.message(States.wait_withdraw)
async def wit_final(m: types.Message, state: FSMContext):
    try:
        val = float(m.text.split()[0])
        if val < 4.0: return await m.answer("Мінімум 4 💎")
        await bot.send_message(ADMIN_ID, f"📤 ЗАПИТ: {m.from_user.id}\nСума/Дані: {m.text}")
        await m.answer("✅ Надіслано!")
        await state.clear()
    except: await m.answer("Помилка")

# НАЛАШТУВАННЯ (МОВА)
@dp.message(F.text.regexp(r"(Налаштування|Settings|Настройки)"))
async def set_cmd(m: types.Message):
    u = await get_u(m.from_user.id)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🌐 Language", callback_data="L_M"),
         InlineKeyboardButton(text="🆘 Support", callback_data="S_P")]
    ])
    await m.answer("⚙️", reply_markup=kb)

@dp.callback_query(F.data == "L_M")
async def L_M(cb: types.CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🇺🇦 UA", callback_data="set_ua"), InlineKeyboardButton(text="🇷🇺 RU", callback_data="set_ru"), InlineKeyboardButton(text="🇺🇸 EN", callback_data="set_en")]
    ])
    await cb.message.edit_text("Language:", reply_markup=kb)

@dp.callback_query(F.data.startswith("set_"))
async def set_l(cb: types.CallbackQuery):
    l = cb.data.split("_")[1]
    await users_col.update_one({"_id": cb.from_user.id}, {"$set": {"lang": l}})
    await cb.message.answer("✅", reply_markup=main_kb(l, cb.from_user.id))

@dp.message(F.text.contains("Рефералка") | F.text.contains("Referral"))
async def ref_cmd(m: types.Message):
    u = await get_u(m.from_user.id)
    bot_info = await bot.get_me()
    link = f"https://t.me/{bot_info.username}?start={m.from_user.id}"
    await m.answer(STRINGS[u['lang']]['ref_text'].format(link=link), parse_mode="Markdown")

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
