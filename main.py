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
ADMIN_ID = int(os.getenv("ADMIN_ID"))
CURRATE = 44.50 

logging.basicConfig(level=logging.INFO)
bot = Bot(token=API_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

cluster = AsyncIOMotorClient(MONGO_URL)
db = cluster["game_bot_db"] 
users_col = db["users"]
games_col = db["games"]

# --- СТАНИ ---
class GameStates(StatesGroup):
    wait_bet = State()
    wait_rounds = State()

class DepositStates(StatesGroup):
    wait_amount = State()
    wait_receipt = State()

# --- ДОПОМІЖНІ ФУНКЦІЇ ---
async def get_u(uid):
    u = await users_col.find_one({"_id": uid})
    if not u:
        u = {"_id": uid, "balance": 0.0, "referals_count": 0, "turnover": 0.0, "games_played": 0, "lang": "ua"}
        await users_col.insert_one(u)
    return u

def main_kb(uid):
    kb = [
        [KeyboardButton(text="👤 Профіль"), KeyboardButton(text="🎮 Games")],
        [KeyboardButton(text="💎 Баланс"), KeyboardButton(text="🤝 Рефералка")],
        [KeyboardButton(text="⚙️ Налаштування")]
    ]
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

def games_choice_kb():
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="🎳"), KeyboardButton(text="🎲")], [KeyboardButton(text="⬅️ Назад")]], resize_keyboard=True)

def rounds_kb():
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="1 кидок"), KeyboardButton(text="5 кидків")]], resize_keyboard=True)

# --- КОМАНДИ ---

@dp.message(Command("start"))
async def cmd_start(m: types.Message, state: FSMContext):
    await state.clear()
    await get_u(m.from_user.id)
    if len(m.text.split()) > 1:
        param = m.text.split()[1]
        if param.startswith("game_"):
            return await join_game_logic(m, param.replace("game_", ""))
    await m.answer("💎 Вітаємо!", reply_markup=main_kb(m.from_user.id))

@dp.message(F.text == "👤 Профіль")
async def profile_cmd(m: types.Message):
    u = await get_u(m.from_user.id)
    text = (f"👤 **Профіль:**\n🆔 Id: `{m.from_user.id}`\n💰 Баланс: `{u['balance']:.2f}` 💎\n\n"
            f"Запрошених гравців: `{u.get('referals_count', 0)}`👤\n"
            f"Оборот: `{u.get('turnover', 0.0):.2f}` 💎\n"
            f"Зіграно ігор: `{u.get('games_played', 0)}` 🎳")
    await m.answer(text, parse_mode="Markdown")

# --- ЛОГІКА GAMES (НОВА ПОСЛІДОВНІСТЬ) ---

@dp.message(F.text == "🎮 Games")
async def games_menu(m: types.Message):
    await m.answer("Оберіть гру:", reply_markup=games_choice_kb())

@dp.message(F.text.in_(["🎳", "🎲"]))
async def choose_type(m: types.Message, state: FSMContext):
    await state.update_data(g_type=m.text)
    await m.answer(f"Ви обрали {m.text}\nВведіть ставку (мінімум 0.05 💎):")
    await state.set_state(GameStates.wait_bet)

@dp.message(GameStates.wait_bet)
async def enter_bet(m: types.Message, state: FSMContext):
    try:
        bet = float(m.text)
        if bet < 0.05: return await m.answer("❌ Мінімальна ставка 0.05")
        u = await get_u(m.from_user.id)
        if u['balance'] < bet: return await m.answer("❌ Недостатньо 💎 на балансі")
        
        await state.update_data(bet=bet)
        await m.answer("Оберіть кількість раундів:", reply_markup=rounds_kb())
        await state.set_state(GameStates.wait_rounds)
    except: await m.answer("❌ Введіть число")

@dp.message(GameStates.wait_rounds, F.text.in_(["1 кидок", "5 кидків"]))
async def finish_creation(m: types.Message, state: FSMContext):
    data = await state.get_data()
    rounds = 1 if "1" in m.text else 5
    gid = str(uuid.uuid4())[:8]
    
    await games_col.insert_one({
        "game_id": gid, "creator_id": m.from_user.id, "opponent_id": None,
        "type": data['g_type'], "bet": data['bet'], "rounds_total": rounds,
        "status": "waiting", "c_score": [], "o_score": [], "turn": m.from_user.id
    })
    
    me = await bot.get_me()
    link = f"https://t.me/{me.username}?start=game_{gid}"
    await m.answer(f"✅ **Посилання на гру:**\n`{link}`\n\nПерешліть це посилання другу щоб разом зіграти", 
                   reply_markup=main_kb(m.from_user.id), parse_mode="Markdown")
    await state.clear()

async def join_game_logic(m, gid):
    g = await games_col.find_one({"game_id": gid, "status": "waiting"})
    if not g or g['creator_id'] == m.from_user.id: return await m.answer("❌ Гра недоступна")
    u = await get_u(m.from_user.id)
    if u['balance'] < g['bet']: return await m.answer("❌ Недостатньо 💎")
    
    await users_col.update_many({"_id": {"$in": [g['creator_id'], m.from_user.id]}}, {"$inc": {"balance": -g['bet'], "turnover": g['bet'], "games_played": 1}})
    await games_col.update_one({"game_id": gid}, {"$set": {"opponent_id": m.from_user.id, "status": "playing"}})
    
    msg = f"Гра створена ✅\nГра: {g['type']}\nСтавка: {g['bet']}\nВиграш: {g['bet']*1.98:.2f}\n\nПерший ходить той, хто створив матч!"
    await bot.send_message(g['creator_id'], msg + "\n\n**Ваш хід! Кидайте емодзі гри!**")
    await m.answer(msg + "\n\nОчікуйте ходу суперника...")

@dp.message(F.dice)
async def handle_dice(m: types.Message):
    g = await games_col.find_one({"status": "playing", "$or": [{"creator_id": m.from_user.id}, {"opponent_id": m.from_user.id}]})
    if not g or g['turn'] != m.from_user.id: return
    
    emoji = "🎳" if g['type'] == "🎳" else "🎲"
    if m.dice.emoji != emoji: return
    
    val = m.dice.value
    is_c = (m.from_user.id == g['creator_id'])
    field = "c_score" if is_c else "o_score"
    next_p = g['opponent_id'] if is_c else g['creator_id']
    
    await games_col.update_one({"game_id": g['game_id']}, {"$push": {field: val}, "$set": {"turn": next_p}})
    await asyncio.sleep(3)
    await m.answer(f"У вас випало/вибило {val} очок")
    
    upd = await games_col.find_one({"game_id": g['game_id']})
    if len(upd['c_score']) == upd['rounds_total'] and len(upd['o_score']) == upd['rounds_total']:
        sc, so = sum(upd['c_score']), sum(upd['o_score'])
        res = f"Гра завершена 🏁\nВи: {sc if is_c else so}\nСуперник: {so if is_c else sc}\n\n"
        if sc == so:
            await users_col.update_many({"_id": {"$in": [g['creator_id'], g['opponent_id']]}}, {"$inc": {"balance": g['bet']}})
            msg = res + "Нічия! Ставки повернуто."
            await bot.send_message(g['creator_id'], msg); await bot.send_message(g['opponent_id'], msg)
        else:
            win_id = g['creator_id'] if sc > so else g['opponent_id']
            lose_id = g['opponent_id'] if sc > so else g['creator_id']
            await users_col.update_one({"_id": win_id}, {"$inc": {"balance": g['bet']*1.98}})
            await bot.send_message(win_id, res + f"Ви перемогли! 🏆\n+{g['bet']*1.98:.2f} 💎")
            await bot.send_message(lose_id, res + "Ви програли ❌")
        await games_col.update_one({"game_id": g['game_id']}, {"$set": {"status": "finished"}})
    else:
        await bot.send_message(next_p, f"Тепер ваш хід! Кидайте {emoji}")

# --- БАЛАНС ТА ПОПОВНЕННЯ ---

@dp.message(F.text == "💎 Баланс")
async def bal_menu(m: types.Message):
    u = await get_u(m.from_user.id)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Поповнити", callback_data="dep"), InlineKeyboardButton(text="Вивести", callback_data="wit")]])
    await m.answer(f"💰 Ваш баланс: `{u['balance']:.2f}` 💎", reply_markup=kb, parse_mode="Markdown")

@dp.callback_query(F.data == "dep")
async def dep_start(cb: types.CallbackQuery, state: FSMContext):
    await cb.message.answer("Введіть суму для поповнення (мінімум 0.50):")
    await state.set_state(DepositStates.wait_amount)

@dp.message(DepositStates.wait_amount)
async def dep_amt(m: types.Message, state: FSMContext):
    try:
        amt = float(m.text)
        if amt < 0.5: return await m.answer("❌ Мінімум 0.50")
        to_pay = (amt * CURRATE) * 1.05
        await state.update_data(amt=amt)
        await m.answer(f"📥 **Заявка на поповнення:**\n\nКурс: `{CURRATE} ГРН`\nДо оплати: `{to_pay:.2f} ГРН` (+5%)\n\n💳 `5355 2800 2890 2177`\n\n⚠️ **Обовʼязково сюди кидайте квитанцію для підтвердження!**", parse_mode="Markdown")
        await state.set_state(DepositStates.wait_receipt)
    except: await m.answer("❌ Введіть число")

@dp.message(DepositStates.wait_receipt, F.photo | F.document)
async def dep_rec(m: types.Message, state: FSMContext):
    data = await state.get_data(); amt = data.get('amt'); uid = m.from_user.id
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅", callback_data=f"ap_ok_{uid}_{amt}"),
        InlineKeyboardButton(text="❌", callback_data=f"ap_no_{uid}")
    ]])
    await bot.send_message(ADMIN_ID, f"🔔 **Нова заявка на поповнення!**\nID: `{uid}`\nСума: `{amt} 💎`", parse_mode="Markdown")
    if m.photo: await bot.send_photo(ADMIN_ID, m.photo[-1].file_id, reply_markup=kb)
    else: await bot.send_document(ADMIN_ID, m.document.file_id, reply_markup=kb)
    await m.answer("⏳ Ваша квитанція надіслана адміну.")
    await state.clear()

@dp.callback_query(F.data.startswith("ap_"))
async def admin_action(cb: types.CallbackQuery):
    data = cb.data.split("_")
    action, target_id = data[1], int(data[2])
    if action == "ok":
        amount = float(data[3])
        await users_col.update_one({"_id": target_id}, {"$inc": {"balance": amount}})
        await bot.send_message(target_id, f"✅ Ваш баланс поповнено +{amount} 💎. Приємної гри :)")
        await cb.message.edit_caption(caption=f"{cb.message.caption}\n\n✅ **ПРИЙНЯТО**")
    else:
        await bot.send_message(target_id, "❌ Ваша заявка відхилена, напишіть адміністратору @vex0o0")
        await cb.message.edit_caption(caption=f"{cb.message.caption}\n\n❌ **ВІДХИЛЕНО**")
    await cb.answer()

# --- ІНШЕ ---

@dp.message(F.text == "🤝 Рефералка")
async def ref_cmd(m: types.Message):
    me = await bot.get_me()
    link = f"https://t.me/{me.username}?start={m.from_user.id}"
    await m.answer(f"🤝 Вітаю, це реферальна система, за яку ви получаєте **0.01%** з виграшу гравців яких ви запросили!\n\n🔗 Посилання: `{link}`", parse_mode="Markdown")

@dp.message(F.text == "⚙️ Налаштування")
async def settings(m: types.Message):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🇺🇦 Укр", callback_data="l_ua"), InlineKeyboardButton(text="🇷🇺 Рус", callback_data="l_ru"), InlineKeyboardButton(text="🇺🇸 Англ", callback_data="l_en")],
        [InlineKeyboardButton(text="🆘 Підтримка", url="https://t.me/vex0o0")]
    ])
    await m.answer("⚙️ **Налаштування:**", reply_markup=kb, parse_mode="Markdown")

@dp.message(F.text == "⬅️ Назад")
async def back(m: types.Message):
    await m.answer("Головне меню:", reply_markup=main_kb(m.from_user.id))

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
