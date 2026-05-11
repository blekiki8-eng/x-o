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

# --- МОВНІ ПАКЕТИ ---
STRINGS = {
    'ua': {
        'start': "✨ Вітаємо в системі!",
        'profile': "👤 **Профіль:**\n🆔 Id: `{id}`\n💰 Баланс: `{bal}` 💎\n\n👥 Запрошених гравців: `{ref}`\n📈 Оборот: `{turn}`\n🎳 Зіграно ігор: `{games}`",
        'ref_text': "🤝 **Реферальна система**\n\nВи отримуєте **0.01%** з виграшу гравців, яких ви запросили!\n\n🔗 Посилання: `{link}`",
        'balance': "💎 Ваш баланс: `{bal}` 💎",
        'dep_info': "📥 **ПОПОВНЕННЯ**\n\nКарта Mono: `5355 2800 2890 2177`\n\nПісля оплати надішліть скріншот у підтримку.",
        'wit_prompt': "Напишіть суму та реквізити для виведення:",
        'settings': "⚙️ Налаштування:",
        'support_prompt': "Напишіть ваше повідомлення для підтримки:",
        'support_ok': "✅ Повідомлення надіслано адміну.",
        'lang_confirm': "Мову змінено на Українську! 🇺🇦"
    },
    'ru': {
        'start': "✨ Добро пожаловать!",
        'profile': "👤 **Профиль:**\n🆔 Id: `{id}`\n💰 Баланс: `{bal}` 💎\n\n👥 Приглашенных игроков: `{ref}`\n📈 Оборот: `{turn}`\n🎳 Сыграно игр: `{games}`",
        'ref_text': "🤝 **Реферальная система**\n\nВы получаете **0.01%** с выигрыша игроков, которых вы пригласили!\n\n🔗 Ссылка: `{link}`",
        'balance': "💎 Ваш баланс: `{bal}` 💎",
        'dep_info': "📥 **ПОПОЛНЕНИЕ**\n\nКарта Mono: `5355 2800 2890 2177`\n\nПосле оплаты отправьте скриншот в поддержку.",
        'wit_prompt': "Напишите сумму и реквизиты для вывода:",
        'settings': "⚙️ Настройки:",
        'support_prompt': "Напишите ваше сообщение для поддержки:",
        'support_ok': "✅ Сообщение отправлено админу.",
        'lang_confirm': "Язык изменен на Русский! 🇷🇺"
    },
    'en': {
        'start': "✨ Welcome to the bot!",
        'profile': "👤 **Profile:**\n🆔 Id: `{id}`\n💰 Balance: `{bal}` 💎\n\n👥 Referrals: `{ref}`\n📈 Turnover: `{turn}`\n🎳 Games played: `{games}`",
        'ref_text': "🤝 **Referral System**\n\nGain **0.01%** from the winnings of players you invited!\n\n🔗 Your link: `{link}`",
        'balance': "💎 Your balance: `{bal}` 💎",
        'dep_info': "📥 **DEPOSIT**\n\nMono Card: `5355 2800 2890 2177`\n\nSend a screenshot to support after payment.",
        'wit_prompt': "Enter the amount and details for withdrawal:",
        'settings': "⚙️ Settings:",
        'support_prompt': "Write your message for support:",
        'support_ok': "✅ Message sent to admin.",
        'lang_confirm': "Language changed to English! 🇺🇸"
    }
}

class UserStates(StatesGroup):
    wait_support = State()
    wait_withdraw = State()

class AdminStates(StatesGroup):
    wait_reply = State()

async def get_u(uid):
    u = await users_col.find_one({"_id": uid})
    if not u:
        u = {"_id": uid, "balance": 0.0, "referals_count": 0, "turnover": 0.0, "games_played": 0, "lang": "ua", "is_blocked": False}
        await users_col.insert_one(u)
    # Якщо юзер є, але немає поля lang, додаємо його
    if 'lang' not in u:
        u['lang'] = 'ua'
        await users_col.update_one({"_id": uid}, {"$set": {"lang": "ua"}})
    return u

def main_kb(uid, lang='ua'):
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
    await state.clear()
    u = await get_u(m.from_user.id)
    lang = u.get('lang', 'ua')
    await m.answer(STRINGS[lang]['start'], reply_markup=main_kb(m.from_user.id, lang))

@dp.message(F.text == "👤 Профіль")
async def profile_cmd(m: types.Message):
    u = await get_u(m.from_user.id)
    lang = u.get('lang', 'ua')
    txt = STRINGS[lang]['profile'].format(id=m.from_user.id, bal=u['balance'], ref=u['referals_count'], turn=u['turnover'], games=u['games_played'])
    await m.answer(txt, parse_mode="Markdown")

@dp.message(F.text == "🤝 Рефералка")
async def ref_cmd(m: types.Message):
    u = await get_u(m.from_user.id)
    lang = u.get('lang', 'ua')
    bot_me = await bot.get_me()
    link = f"https://t.me/{bot_me.username}?start={m.from_user.id}"
    await m.answer(STRINGS[lang]['ref_text'].format(link=link), parse_mode="Markdown")

@dp.message(F.text == "💎 Баланс")
async def bal_cmd(m: types.Message):
    u = await get_u(m.from_user.id)
    lang = u.get('lang', 'ua')
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📥 Поповнити", callback_data="btn_dep"),
         InlineKeyboardButton(text="📤 Вивести", callback_data="btn_wit")]
    ])
    await m.answer(STRINGS[lang]['balance'].format(bal=u['balance']), reply_markup=kb, parse_mode="Markdown")

@dp.callback_query(F.data == "btn_dep")
async def call_dep(cb: types.CallbackQuery):
    u = await get_u(cb.from_user.id)
    await cb.message.answer(STRINGS[u.get('lang', 'ua')]['dep_info'], parse_mode="Markdown")

@dp.callback_query(F.data == "btn_wit")
async def call_wit(cb: types.CallbackQuery, state: FSMContext):
    u = await get_u(cb.from_user.id)
    await cb.message.answer(STRINGS[u.get('lang', 'ua')]['wit_prompt'])
    await state.set_state(UserStates.wait_withdraw)

@dp.message(UserStates.wait_withdraw)
async def wit_step2(m: types.Message, state: FSMContext):
    await bot.send_message(ADMIN_ID, f"📤 **ЗАПИТ НА ВИВЕДЕННЯ**\nЮзер: `{m.from_user.id}`\nДані: {m.text}")
    await m.answer("✅ Запит прийнято.")
    await state.clear()

@dp.message(F.text == "⚙️ Налаштування")
async def settings_cmd(m: types.Message):
    u = await get_u(m.from_user.id)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🌐 Мова / Language", callback_data="set_l")],
        [InlineKeyboardButton(text="🆘 Підтримка", callback_data="set_s")]
    ])
    await m.answer(STRINGS[u.get('lang', 'ua')]['settings'], reply_markup=kb)

@dp.callback_query(F.data == "set_l")
async def set_l(cb: types.CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🇺🇦 UA", callback_data="l_ua"), InlineKeyboardButton(text="🇷🇺 RU", callback_data="l_ru"), InlineKeyboardButton(text="🇺🇸 EN", callback_data="l_en")]
    ])
    await cb.message.edit_text("Select language:", reply_markup=kb)

@dp.callback_query(F.data.startswith("l_"))
async def l_apply(cb: types.CallbackQuery):
    lang = cb.data.split("_")[1]
    await users_col.update_one({"_id": cb.from_user.id}, {"$set": {"lang": lang}})
    await cb.message.answer(STRINGS[lang]['lang_confirm'], reply_markup=main_kb(cb.from_user.id, lang))

@dp.callback_query(F.data == "set_s")
async def support_init(cb: types.CallbackQuery, state: FSMContext):
    u = await get_u(cb.from_user.id)
    await cb.message.answer(STRINGS[u.get('lang', 'ua')]['support_prompt'])
    await state.set_state(UserStates.wait_support)

@dp.message(UserStates.wait_support)
async def support_finish(m: types.Message, state: FSMContext):
    u = await get_u(m.from_user.id)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Відповісти", callback_data=f"adm_rep_{m.from_user.id}")]])
    await bot.send_message(ADMIN_ID, f"🆘 **ПИТАННЯ ВІД {m.from_user.id}**\n\n{m.text}", reply_markup=kb)
    await m.answer(STRINGS[u.get('lang', 'ua')]['support_ok'])
    await state.clear()

@dp.callback_query(F.data.startswith("adm_rep_"))
async def adm_rep_start(cb: types.CallbackQuery, state: FSMContext):
    target = cb.data.split("_")[2]
    await state.update_data(to_id=target)
    await cb.message.answer(f"Пишіть відповідь для `{target}`:")
    await state.set_state(AdminStates.wait_reply)

@dp.message(AdminStates.wait_reply)
async def adm_rep_finish(m: types.Message, state: FSMContext):
    data = await state.get_data()
    try:
        await bot.send_message(data['to_id'], f"⚠️ **ВІДПОВІДЬ ПІДТРИМКИ:**\n\n{m.text}")
        await m.answer("✅ Надіслано.")
    except: await m.answer("❌ Помилка")
    await state.clear()

@dp.message(F.text == "🛡 Панель адміна")
async def adm_panel(m: types.Message):
    if m.from_user.id != ADMIN_ID: return
    users = await users_col.find().limit(15).to_list(length=15)
    btns = [[InlineKeyboardButton(text=f"👤 {u['_id']} | {u['balance']}💎", callback_data=f"info_{u['_id']}")] for u in users]
    await m.answer("👥 Останні гравці:", reply_markup=InlineKeyboardMarkup(inline_keyboard=btns))

@dp.callback_query(F.data.startswith("info_"))
async def info_user(cb: types.CallbackQuery):
    uid = int(cb.data.split("_")[1])
    u = await users_col.find_one({"_id": uid})
    text = f"👤 ID: `{uid}`\n💰 Баланс: `{u['balance']}`\n📈 Оборот: `{u.get('turnover', 0)}`"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🧹 Обнулити", callback_data=f"z_{uid}"), InlineKeyboardButton(text="🚫 Бан", callback_data=f"b_{uid}")]
    ])
    await cb.message.edit_text(text, reply_markup=kb, parse_mode="Markdown")

@dp.callback_query(F.data.startswith("z_"))
async def zero_bal(cb: types.CallbackQuery):
    uid = int(cb.data.split("_")[1])
    await users_col.update_one({"_id": uid}, {"$set": {"balance": 0.0}})
    await cb.answer("Баланс обнулено")

@dp.message(F.text == "🎮 Games")
async def games_cmd(m: types.Message):
    await m.answer("🎮 Ігри скоро будуть тут!")

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
