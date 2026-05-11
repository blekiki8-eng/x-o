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

# --- ТЕКСТИ МОВАМИ ---
STRINGS = {
    'ua': {
        'profile': "👤 **Профіль:**\n🆔 Id: `{id}`\n💰 Баланс: `{bal}` 💎\n\n👥 Реферали: `{ref}`\n📈 Оборот: `{turn}`\n🎳 Ігор: `{games}`",
        'settings': "⚙️ Налаштування:",
        'support_prompt': "Напишіть ваше повідомлення для підтримки:",
        'lang_changed': "Мову змінено на Українську! 🇺🇦",
        'games_menu': "🎮 Оберіть гру:",
        'balance_menu': "💎 Ваш баланс: `{bal}` 💎\nОберіть дію:",
        'dep_text': "📥 **ПОПОВНЕННЯ**\nКарта Mono: `5355 2800 2890 2177`\nПісля оплати надішліть скріншот.",
        'wit_prompt': "Введіть суму та реквізити для виведення:"
    },
    'ru': {
        'profile': "👤 **Профиль:**\n🆔 Id: `{id}`\n💰 Баланс: `{bal}` 💎\n\n👥 Рефералы: `{ref}`\n📈 Оборот: `{turn}`\n🎳 Игр: `{games}`",
        'settings': "⚙️ Настройки:",
        'support_prompt': "Напишите ваше сообщение для поддержки:",
        'lang_changed': "Язык изменен на Русский! 🇷🇺",
        'games_menu': "🎮 Выберите игру:",
        'balance_menu': "💎 Ваш баланс: `{bal}` 💎\nВыберите действие:",
        'dep_text': "📥 **ПОПОЛНЕНИЕ**\nКарта Mono: `5355 2800 2890 2177`\nПосле оплаты скиньте скриншот.",
        'wit_prompt': "Введите сумму и реквизиты для вывода:"
    },
    'en': {
        'profile': "👤 **Profile:**\n🆔 Id: `{id}`\n💰 Balance: `{bal}` 💎\n\n👥 Referrals: `{ref}`\n📈 Turnover: `{turn}`\n🎳 Games: `{games}`",
        'settings': "⚙️ Settings:",
        'support_prompt': "Write your message to support:",
        'lang_changed': "Language changed to English! 🇺🇸",
        'games_menu': "🎮 Choose a game:",
        'balance_menu': "💎 Your balance: `{bal}` 💎\nChoose action:",
        'dep_text': "📥 **DEPOSIT**\nCard Mono: `5355 2800 2890 2177`\nSend a screenshot after payment.",
        'wit_prompt': "Enter amount and details for withdrawal:"
    }
}

# --- СТАНИ ---
class States(StatesGroup):
    wait_bet = State()
    wait_support = State()
    wait_withdraw = State()
    admin_reply = State()

# --- ДОПОМІЖНІ ---
async def get_u(user_id):
    u = await users_col.find_one({"_id": user_id})
    if not u:
        u = {"_id": user_id, "balance": 0.0, "referals_count": 0, "turnover": 0.0, "games_played": 0, "is_blocked": False, "lang": "ua"}
        await users_col.insert_one(u)
    return u

def main_menu(uid, lang='ua'):
    kb = [
        [KeyboardButton(text="👤 Профіль"), KeyboardButton(text="🎮 Games")],
        [KeyboardButton(text="💎 Баланс"), KeyboardButton(text="🤝 Рефералка")],
        [KeyboardButton(text="⚙️ Налаштування")]
    ]
    if uid == ADMIN_ID: kb.append([KeyboardButton(text="🛡 Панель адміна")])
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

# --- ОБРОБНИКИ ---

@dp.message(Command("start"))
async def cmd_start(m: types.Message, state: FSMContext):
    u = await get_u(m.from_user.id)
    await m.answer("💎 Welcome!", reply_markup=main_menu(m.from_user.id, u.get('lang', 'ua')))

@dp.message(F.text == "👤 Профіль")
async def profile_cmd(m: types.Message):
    u = await get_u(m.from_user.id)
    l = u.get('lang', 'ua')
    txt = STRINGS[l]['profile'].format(id=m.from_user.id, bal=u['balance'], ref=u['referals_count'], turn=u['turnover'], games=u['games_played'])
    await m.answer(txt, parse_mode="Markdown")

# --- ГРА ---
@dp.message(F.text == "🎮 Games")
async def games_menu(m: types.Message):
    u = await get_u(m.from_user.id)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🎳 Bowling", callback_data="play_bowling")]])
    await m.answer(STRINGS[u['lang']]['games_menu'], reply_markup=kb)

@dp.callback_query(F.data == "play_bowling")
async def bowl_start(cb: types.CallbackQuery, state: FSMContext):
    await cb.message.answer("Введіть суму ставки:")
    await state.set_state(States.wait_bet)

@dp.message(States.wait_bet)
async def create_game(m: types.Message, state: FSMContext):
    try:
        bet = float(m.text)
        u = await get_u(m.from_user.id)
        if u['balance'] < bet: return await m.answer("❌ Мало коштів")
        gid = str(uuid.uuid4())[:8]
        await games_col.insert_one({"game_id": gid, "creator_id": m.from_user.id, "bet": bet, "status": "waiting"})
        link = f"https://t.me/{(await bot.get_me()).username}?start=game_{gid}"
        await m.answer(f"🎳 Гра створена!\n{link}")
        await state.clear()
    except: await m.answer("Введіть число")

# --- БАЛАНС ---
@dp.message(F.text == "💎 Баланс")
async def balance_menu(m: types.Message):
    u = await get_u(m.from_user.id)
    l = u['lang']
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📥 Поповнити", callback_data="dep"), InlineKeyboardButton(text="📤 Вивести", callback_data="wit")]
    ])
    await m.answer(STRINGS[l]['balance_menu'].format(bal=u['balance']), reply_markup=kb)

@dp.callback_query(F.data == "dep")
async def deposit(cb: types.CallbackQuery):
    u = await get_u(cb.from_user.id)
    await cb.message.answer(STRINGS[u['lang']]['dep_text'], parse_mode="Markdown")

@dp.callback_query(F.data == "wit")
async def withdraw(cb: types.CallbackQuery, state: FSMContext):
    u = await get_u(cb.from_user.id)
    await cb.message.answer(STRINGS[u['lang']]['wit_prompt'])
    await state.set_state(States.wait_withdraw)

@dp.message(States.wait_withdraw)
async def process_wit(m: types.Message, state: FSMContext):
    await bot.send_message(ADMIN_ID, f"📤 **ЗАПИТ НА ВИВЕДЕННЯ**\nВід: `{m.from_user.id}`\nДані: {m.text}")
    await m.answer("✅ Запит надіслано адміну.")
    await state.clear()

# --- НАЛАШТУВАННЯ ТА ПІДТРИМКА ---
@dp.message(F.text == "⚙️ Налаштування")
async def settings(m: types.Message):
    u = await get_u(m.from_user.id)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🌐 Мова / Language", callback_data="set_lang")],
        [InlineKeyboardButton(text="🆘 Підтримка", callback_data="support")]
    ])
    await m.answer(STRINGS[u['lang']]['settings'], reply_markup=kb)

@dp.callback_query(F.data == "set_lang")
async def set_lang(cb: types.CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🇺🇦 Українська", callback_data="lang_ua")],
        [InlineKeyboardButton(text="🇷🇺 Русский", callback_data="lang_ru")],
        [InlineKeyboardButton(text="🇺🇸 English", callback_data="lang_en")]
    ])
    await cb.message.edit_text("Оберіть мову / Choose language:", reply_markup=kb)

@dp.callback_query(F.data.startswith("lang_"))
async def lang_apply(cb: types.CallbackQuery):
    l = cb.data.split("_")[1]
    await users_col.update_one({"_id": cb.from_user.id}, {"$set": {"lang": l}})
    await cb.message.answer(STRINGS[l]['lang_changed'], reply_markup=main_menu(cb.from_user.id, l))

@dp.callback_query(F.data == "support")
async def support_init(cb: types.CallbackQuery, state: FSMContext):
    u = await get_u(cb.from_user.id)
    await cb.message.answer(STRINGS[u['lang']]['support_prompt'])
    await state.set_state(States.wait_support)

@dp.message(States.wait_support)
async def support_send(m: types.Message, state: FSMContext):
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Відповісти", callback_data=f"reply_{m.from_user.id}")]])
    await bot.send_message(ADMIN_ID, f"📩 **НОВИЙ ТІКЕТ**\nВід: `{m.from_user.id}`\nТекст: {m.text}", reply_markup=kb)
    await m.answer("✅ Повідомлення надіслано підтримці.")
    await state.clear()

# --- ВІДПОВІДЬ АДМІНА ---
@dp.callback_query(F.data.startswith("reply_"))
async def admin_reply_start(cb: types.CallbackQuery, state: FSMContext):
    uid = cb.data.split("_")[1]
    await state.update_data(rep_id=uid)
    await cb.message.answer(f"Пишіть відповідь для {uid}:")
    await state.set_state(States.admin_reply)

@dp.message(States.admin_reply)
async def admin_reply_send(m: types.Message, state: FSMContext):
    data = await state.get_data()
    try:
        await bot.send_message(data['rep_id'], f"⚠️ **ВІДПОВІДЬ ПІДТРИМКИ:**\n\n{m.text}")
        await m.answer("✅ Відправлено гравцю!")
    except: await m.answer("❌ Помилка відправки")
    await state.clear()

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
