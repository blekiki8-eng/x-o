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

# --- МОВНІ ПАКЕТИ ТА КНОПКИ ---
STRINGS = {
    'ua': {
        'start': "✨ Вітаємо в системі!",
        'profile': "👤 **Профіль:**\n🆔 Id: `{id}`\n💰 Баланс: `{bal}` 💎\n\n👥 Реферали: `{ref}`\n📈 Оборот: `{turn}`\n🎳 Ігор: `{games}`",
        'btn_profile': "👤 Профіль", 'btn_games': "🎮 Games", 'btn_balance': "💎 Баланс", 'btn_ref': "🤝 Рефералка", 'btn_settings': "⚙️ Налаштування",
        'ref_text': "🤝 **Рефералка**\n\nВи отримуєте **0.01%** з виграшу друзів!\n\n🔗 Посилання: `{link}`",
        'balance': "💎 Ваш баланс: `{bal}` 💎\n\n💳 Мін. поповнення: `0.5` 💎\n💳 Мін. виведення: `4.0` 💎",
        'dep_info': "📥 **ПОПОВНЕННЯ**\n\nКарта Mono: `5355 2800 2890 2177`\nМінімальна сума: **0.5 💎**\n\nНадішліть скріншот підтримці після оплати.",
        'wit_prompt': "Введіть суму та реквізити для виведення (Мін. 4 💎):",
        'wit_min_err': "❌ Помилка: мінімальна сума виведення 4 💎",
        'support_prompt': "Напишіть повідомлення для підтримки:",
        'support_ok': "✅ Відправлено!",
        'bowl_bet': "Введіть суму ставки для Боулінгу:"
    },
    'ru': {
        'start': "✨ Добро пожаловать!",
        'profile': "👤 **Профиль:**\n🆔 Id: `{id}`\n💰 Баланс: `{bal}` 💎\n\n👥 Рефералы: `{ref}`\n📈 Оборот: `{turn}`\n🎳 Игр: `{games}`",
        'btn_profile': "👤 Профиль", 'btn_games': "🎮 Games", 'btn_balance': "💎 Баланс", 'btn_ref': "🤝 Рефералка", 'btn_settings': "⚙️ Настройки",
        'ref_text': "🤝 **Рефералка**\n\nВы получаете **0.01%** с выигрыша друзей!\n\n🔗 Ссылка: `{link}`",
        'balance': "💎 Ваш баланс: `{bal}` 💎\n\n💳 Мин. пополнение: `0.5` 💎\n💳 Мин. вывод: `4.0` 💎",
        'dep_info': "📥 **ПОПОЛНЕНИЕ**\n\nКарта Mono: `5355 2800 2890 2177`\nМинимальная сумма: **0.5 💎**\n\nОтправьте скриншот поддержке после оплаты.",
        'wit_prompt': "Введите сумму и реквизиты для вывода (Мин. 4 💎):",
        'wit_min_err': "❌ Ошибка: минимальная сумма вывода 4 💎",
        'support_prompt': "Напишите сообщение поддержке:",
        'support_ok': "✅ Отправлено!",
        'bowl_bet': "Введите сумму ставки для Боулинга:"
    },
    'en': {
        'start': "✨ Welcome to the bot!",
        'profile': "👤 **Profile:**\n🆔 Id: `{id}`\n💰 Balance: `{bal}` 💎\n\n👥 Referrals: `{ref}`\n📈 Turnover: `{turn}`\n🎳 Games: `{games}`",
        'btn_profile': "👤 Profile", 'btn_games': "🎮 Games", 'btn_balance': "💎 Balance", 'btn_ref': "🤝 Referral", 'btn_settings': "⚙️ Settings",
        'ref_text': "🤝 **Referral**\n\nYou get **0.01%** from friends' winnings!\n\n🔗 Link: `{link}`",
        'balance': "💎 Your balance: `{bal}` 💎\n\n💳 Min. deposit: `0.5` 💎\n💳 Min. withdraw: `4.0` 💎",
        'dep_info': "📥 **DEPOSIT**\n\nCard Mono: `5355 2800 2890 2177`\nMin amount: **0.5 💎**\n\nSend a screenshot to support after payment.",
        'wit_prompt': "Enter amount and details for withdrawal (Min 4 💎):",
        'wit_min_err': "❌ Error: minimum withdrawal is 4 💎",
        'support_prompt': "Write a message to support:",
        'support_ok': "✅ Sent!",
        'bowl_bet': "Enter bet amount for Bowling:"
    }
}

class States(StatesGroup):
    wait_support = State()
    wait_withdraw = State()
    wait_bet = State()

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
    lang = u.get('lang', 'ua')
    await m.answer(STRINGS[lang]['start'], reply_markup=main_kb(lang, m.from_user.id))

# ПРОФІЛЬ (ПРАЦЮЄ ДЛЯ ВСІХ)
@dp.message(F.text.contains("Профіль") | F.text.contains("Профиль") | F.text.contains("Profile"))
async def profile_cmd(m: types.Message):
    u = await get_u(m.from_user.id)
    lang = u.get('lang', 'ua')
    txt = STRINGS[lang]['profile'].format(id=m.from_user.id, bal=u['balance'], ref=u.get('referals_count', 0), turn=u.get('turnover', 0), games=u.get('games_played', 0))
    await m.answer(txt, parse_mode="Markdown")

# GAMES & BOWLING
@dp.message(F.text.contains("Games"))
async def games_menu(m: types.Message):
    u = await get_u(m.from_user.id)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🎳 Bowling", callback_data="bowl_play")]])
    await m.answer(STRINGS[u.get('lang', 'ua')]['btn_games'], reply_markup=kb)

@dp.callback_query(F.data == "bowl_play")
async def bowl_start(cb: types.CallbackQuery, state: FSMContext):
    u = await get_u(cb.from_user.id)
    await cb.message.answer(STRINGS[u.get('lang', 'ua')]['bowl_bet'])
    await state.set_state(States.wait_bet)

@dp.message(States.wait_bet)
async def create_bowl(m: types.Message, state: FSMContext):
    try:
        bet = float(m.text)
        if bet < 0.1: return await m.answer("❌ Мін. ставка 0.1")
        u = await get_u(m.from_user.id)
        if u['balance'] < bet: return await m.answer("❌ Недостатньо коштів")
        
        gid = str(uuid.uuid4())[:8]
        await games_col.insert_one({"game_id": gid, "creator_id": m.from_user.id, "bet": bet, "status": "waiting"})
        link = f"https://t.me/{(await bot.get_me()).username}?start=game_{gid}"
        await m.answer(f"🎳 **Гра створена!**\nСтавка: {bet} 💎\n\nВідправте посилання другу:\n`{link}`", parse_mode="Markdown")
        await state.clear()
    except: await m.answer("❌ Введіть число")

# БАЛАНС (МІН 0.5 ТА 4)
@dp.message(F.text.contains("Баланс") | F.text.contains("Balance"))
async def bal_cmd(m: types.Message):
    u = await get_u(m.from_user.id)
    lang = u.get('lang', 'ua')
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📥 Поповнити", callback_data="dep"), InlineKeyboardButton(text="📤 Вивести", callback_data="wit")]
    ])
    await m.answer(STRINGS[lang]['balance'].format(bal=u['balance']), reply_markup=kb, parse_mode="Markdown")

@dp.callback_query(F.data == "dep")
async def dep_call(cb: types.CallbackQuery):
    u = await get_u(cb.from_user.id)
    await cb.message.answer(STRINGS[u.get('lang', 'ua')]['dep_info'], parse_mode="Markdown")

@dp.callback_query(F.data == "wit")
async def wit_call(cb: types.CallbackQuery, state: FSMContext):
    u = await get_u(cb.from_user.id)
    await cb.message.answer(STRINGS[u.get('lang', 'ua')]['wit_prompt'])
    await state.set_state(States.wait_withdraw)

@dp.message(States.wait_withdraw)
async def wit_final(m: types.Message, state: FSMContext):
    try:
        val = float(m.text.split()[0])
        if val < 4.0:
            u = await get_u(m.from_user.id)
            return await m.answer(STRINGS[u.get('lang', 'ua')]['wit_min_err'])
        await bot.send_message(ADMIN_ID, f"📤 **ЗАПИТ ВИВЕДЕННЯ**\nID: `{m.from_user.id}`\nСума/Дані: {m.text}")
        await m.answer("✅ Запит надіслано!")
        await state.clear()
    except: await m.answer("❌ Введіть суму першим числом")

# РЕФЕРАЛКА
@dp.message(F.text.contains("Рефералка") | F.text.contains("Referral"))
async def ref_cmd(m: types.Message):
    u = await get_u(m.from_user.id)
    bot_info = await bot.get_me()
    link = f"https://t.me/{bot_info.username}?start={m.from_user.id}"
    await m.answer(STRINGS[u.get('lang', 'ua')]['ref_text'].format(link=link), parse_mode="Markdown")

# НАЛАШТУВАННЯ (ЗМІНА МОВИ ТА КНОПОК)
@dp.message(F.text.contains("Налаштування") | F.text.contains("Settings") | F.text.contains("Настройки"))
async def settings_cmd(m: types.Message):
    u = await get_u(m.from_user.id)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🌐 Language", callback_data="choose_lang")],
        [InlineKeyboardButton(text="🆘 Support", callback_data="supp")]
    ])
    await m.answer(STRINGS[u.get('lang', 'ua')]['btn_settings'], reply_markup=kb)

@dp.callback_query(F.data == "choose_lang")
async def lang_menu(cb: types.CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🇺🇦 UA", callback_data="set_ua"), InlineKeyboardButton(text="🇷🇺 RU", callback_data="set_ru"), InlineKeyboardButton(text="🇺🇸 EN", callback_data="set_en")]
    ])
    await cb.message.edit_text("Select language:", reply_markup=kb)

@dp.callback_query(F.data.startswith("set_"))
async def set_lang(cb: types.CallbackQuery):
    new_lang = cb.data.split("_")[1]
    await users_col.update_one({"_id": cb.from_user.id}, {"$set": {"lang": new_lang}})
    await cb.message.answer("✅ Done!", reply_markup=main_kb(new_lang, cb.from_user.id))

@dp.callback_query(F.data == "supp")
async def supp_call(cb: types.CallbackQuery, state: FSMContext):
    u = await get_u(cb.from_user.id)
    await cb.message.answer(STRINGS[u.get('lang', 'ua')]['support_prompt'])
    await state.set_state(States.wait_support)

@dp.message(States.wait_support)
async def supp_send(m: types.Message, state: FSMContext):
    await bot.send_message(ADMIN_ID, f"🆘 **SUPPORT**\nID: `{m.from_user.id}`\nText: {m.text}")
    u = await get_u(m.from_user.id)
    await m.answer(STRINGS[u.get('lang', 'ua')]['support_ok'])
    await state.clear()

# ПАНЕЛЬ АДМІНА
@dp.message(F.text == "🛡 Панель адміна")
async def admin_panel(m: types.Message):
    if m.from_user.id != ADMIN_ID: return
    users = await users_col.find().limit(10).to_list(length=10)
    btns = [[InlineKeyboardButton(text=f"👤 {u['_id']}", callback_data=f"adm_{u['_id']}")] for u in users]
    await m.answer("🛡 Останні гравці:", reply_markup=InlineKeyboardMarkup(inline_keyboard=btns))

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
