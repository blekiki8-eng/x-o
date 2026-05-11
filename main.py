import os, uuid, logging, asyncio
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove, 
    InlineKeyboardMarkup, InlineKeyboardButton
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

def rounds_kb():
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="1 раунд"), KeyboardButton(text="5 раундів")],
        [KeyboardButton(text="⬅️ Назад")]
    ], resize_keyboard=True)

# --- ДОПОМІЖНІ ФУНКЦІЇ ---
async def get_u(uid):
    u = await users_col.find_one({"_id": uid})
    if not u:
        u = {"_id": uid, "balance": 0.0, "referals_count": 0, "turnover": 0.0, "games_played": 0}
        await users_col.insert_one(u)
    return u

# --- ОСНОВНІ КОМАНДИ ---

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
    text = (f"👤 **Профіль:**\n\n"
            f"🆔 Id: `{m.from_user.id}`\n"
            f"💰 Баланс: `{u['balance']:.2f}` 💎\n\n"
            f"Запрошених гравців: `{u.get('referals_count', 0)}`👤\n"
            f"Оборот: `{u.get('turnover', 0.0):.2f}` 💎\n"
            f"Зіграно ігор: `{u.get('games_played', 0)}` 🎳")
    await m.answer(text, parse_mode="Markdown")

# --- ЛОГІКА ІГОР ---

@dp.message(F.text == "🎮 Games")
async def games_menu(m: types.Message):
    kb = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="Гра Боулінг 🎳"), KeyboardButton(text="Гра Кубик 🎲")], [KeyboardButton(text="⬅️ Назад")]], resize_keyboard=True)
    await m.answer("Оберіть гру:", reply_markup=kb)

@dp.message(F.text.in_(["Гра Боулінг 🎳", "Гра Кубик 🎲"]))
async def choose_rounds(m: types.Message, state: FSMContext):
    g_type = "🎳" if "Боулінг" in m.text else "🎲"
    await state.update_data(g_type=g_type)
    await m.answer("На скільки раундів граємо?", reply_markup=rounds_kb())
    await state.set_state(GameStates.wait_rounds)

@dp.message(GameStates.wait_rounds, F.text.in_(["1 раунд", "5 раундів"]))
async def choose_bet(m: types.Message, state: FSMContext):
    r = 1 if "1" in m.text else 5
    await state.update_data(rounds=r)
    await m.answer(f"Ви обрали {r} раундів. Введіть суму ставки:", reply_markup=ReplyKeyboardRemove())
    await state.set_state(GameStates.wait_bet)

@dp.message(GameStates.wait_bet)
async def create_game_link(m: types.Message, state: FSMContext):
    try:
        bet = float(m.text.replace(",", "."))
        u = await get_u(m.from_user.id)
        if u['balance'] < bet: return await m.answer("❌ Недостатньо балансу")
        
        data = await state.get_data()
        gid = str(uuid.uuid4())[:8]
        
        await games_col.insert_one({
            "game_id": gid, "creator_id": m.from_user.id, "opponent_id": None,
            "type": data['g_type'], "bet": bet, "rounds_total": data['rounds'],
            "status": "waiting", "c_score": [], "o_score": []
        })
        
        me = await bot.get_me()
        link = f"https://t.me/{me.username}?start=game_{gid}"
        await m.answer(f"Посилання на гру: {link}\n\nПерешліть це посилання другу", reply_markup=main_kb())
        await state.clear()
    except: await m.answer("Введіть число")

async def join_game_logic(m, gid):
    g = await games_col.find_one({"game_id": gid, "status": "waiting"})
    if not g or g['creator_id'] == m.from_user.id: return await m.answer("❌ Гра недоступна")
    
    u2 = await get_u(m.from_user.id)
    if u2['balance'] < g['bet']: return await m.answer("❌ У вас недостатньо коштів")
    
    await users_col.update_many({"_id": {"$in": [g['creator_id'], m.from_user.id]}}, 
                                {"$inc": {"balance": -g['bet'], "turnover": g['bet'], "games_played": 1}})
    
    await games_col.update_one({"game_id": gid}, {"$set": {"opponent_id": m.from_user.id, "status": "playing", "turn": g['creator_id']}})
    
    msg = f"Гра створена ✅\nКількість раундів: {g['rounds_total']}"
    await bot.send_message(g['creator_id'], f"{msg}\n\nВаш хід! Кидайте стікер {g['type']}")
    await m.answer(f"{msg}\n\nЗараз ходить Гравець 1. Очікуйте.")

# --- ІГРОВИЙ ПРОЦЕС ---

@dp.message(F.dice)
async def handle_game_dice(m: types.Message):
    g = await games_col.find_one({"status": "playing", "$or": [{"creator_id": m.from_user.id}, {"opponent_id": m.from_user.id}]})
    if not g or g['turn'] != m.from_user.id or m.dice.emoji != g['type']: return
    
    val = m.dice.value
    is_c = (m.from_user.id == g['creator_id'])
    f_add = "c_score" if is_c else "o_score"
    next_p = g['opponent_id'] if is_c else g['creator_id']
    
    await games_col.update_one({"game_id": g['game_id']}, {"$push": {f_add: val}, "$set": {"turn": next_p}})
    
    # ПОВІДОМЛЕННЯ СУПЕРНИКУ (ЯК ТИ ПРОСИВ)
    await bot.send_dice(next_p, emoji=g['type'])
    await bot.send_message(next_p, f"Супернику випало {val}, тепер ваш хід!")

    upd = await games_col.find_one({"game_id": g['game_id']})
    if len(upd['c_score']) == upd['rounds_total'] and len(upd['o_score']) == upd['rounds_total']:
        s1, s2 = sum(upd['c_score']), sum(upd['o_score'])
        await process_finish(upd, s1, s2)

async def process_finish(g, s1, s2):
    win_sum = g['bet'] * 1.95
    if s1 > s2:
        await bot.send_message(g['creator_id'], f"Результат:\nУ вас: {s1}\nУ противника: {s2}\n\nВітаю ви виграли +{win_sum:.2f} 💎")
        await bot.send_message(g['opponent_id'], f"Результат:\nУ вас: {s2}\nУ противника: {s1}\n\nНажаль ви програли ❌")
        await users_col.update_one({"_id": g['creator_id']}, {"$inc": {"balance": win_sum}})
    elif s2 > s1:
        await bot.send_message(g['opponent_id'], f"Результат:\nУ вас: {s2}\nУ противника: {s1}\n\nВітаю ви виграли +{win_sum:.2f} 💎")
        await bot.send_message(g['creator_id'], f"Результат:\nУ вас: {s1}\nУ противника: {s2}\n\nНажаль ви програли ❌")
        await users_col.update_one({"_id": g['opponent_id']}, {"$inc": {"balance": win_sum}})
    else:
        for uid in [g['creator_id'], g['opponent_id']]:
            await bot.send_message(uid, f"Результат: {s1}:{s2}\nНічия! Ставки повернуто.")
            await users_col.update_one({"_id": uid}, {"$inc": {"balance": g['bet']}})
    await games_col.update_one({"game_id": g['game_id']}, {"$set": {"status": "finished"}})

# --- ФІНАНСОВА СИСТЕМА ---

@dp.message(F.text == "💎 Баланс")
async def balance_menu(m: types.Message):
    u = await get_u(m.from_user.id)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📥 Поповнити", callback_data="dep"),
         InlineKeyboardButton(text="📤 Вивести", callback_data="with")]
    ])
    await m.answer(f"💰 Ваш баланс: `{u['balance']:.2f}` 💎", reply_markup=kb, parse_mode="Markdown")

@dp.callback_query(F.data == "dep")
async def start_dep(cb: types.CallbackQuery, state: FSMContext):
    await cb.message.answer("Введіть суму поповнення (мін 0.5 💎):")
    await state.set_state(FinanceStates.wait_dep_amount)

@dp.message(FinanceStates.wait_dep_amount)
async def dep_amount(m: types.Message, state: FSMContext):
    try:
        amt = float(m.text.replace(",", "."))
        await state.update_data(amt=amt)
        total = (amt * CURRATE) * 1.05
        await m.answer(f"📥 **Заявка:**\nДо оплати: `{total:.2f} ГРН`\n💳 Карта: `5355 2800 2890 2177`\n\nНадішліть скрін чеку:", parse_mode="Markdown")
        await state.set_state(FinanceStates.wait_receipt)
    except: await m.answer("Введіть число")

@dp.message(FinanceStates.wait_receipt, F.photo | F.document)
async def dep_receipt(m: types.Message, state: FSMContext):
    data = await state.get_data(); amt = data['amt']; uid = m.from_user.id
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅", callback_data=f"adm_ok_{uid}_{amt}"),
        InlineKeyboardButton(text="❌", callback_data=f"adm_no_{uid}")
    ]])
    await bot.send_message(ADMIN_ID, f"🔔 Чек від `{uid}` на `{amt} 💎`", parse_mode="Markdown")
    if m.photo: await bot.send_photo(ADMIN_ID, m.photo[-1].file_id, reply_markup=kb)
    else: await bot.send_document(ADMIN_ID, m.document.file_id, reply_markup=kb)
    await m.answer("⏳ Чек надіслано на перевірку.")
    await state.clear()

@dp.callback_query(F.data == "with")
async def start_with(cb: types.CallbackQuery, state: FSMContext):
    await cb.message.answer("Сума для виводу:")
    await state.set_state(FinanceStates.wait_withdraw_amount)

@dp.message(FinanceStates.wait_withdraw_amount)
async def with_amt(m: types.Message, state: FSMContext):
    try:
        amt = float(m.text.replace(",", "."))
        u = await get_u(m.from_user.id)
        if u['balance'] < amt: return await m.answer("❌ Недостатньо 💎")
        await state.update_data(w_amt=amt)
        await m.answer("Вкажіть банк та номер карти:")
        await state.set_state(FinanceStates.wait_withdraw_details)
    except: await m.answer("Число!")

@dp.message(FinanceStates.wait_withdraw_details)
async def with_final(m: types.Message, state: FSMContext):
    data = await state.get_data(); amt = data['w_amt']
    await users_col.update_one({"_id": m.from_user.id}, {"$inc": {"balance": -amt}})
    await bot.send_message(ADMIN_ID, f"📤 ВИВІД: `{m.from_user.id}`\nСума: `{amt}`\nРеквізити: {m.text}")
    await m.answer("✅ Заявка прийнята.")
    await state.clear()

# --- АДМІНКА ПОПОВНЕНЬ ---
@dp.callback_query(F.data.startswith("adm_"))
async def admin_verify(cb: types.CallbackQuery):
    d = cb.data.split("_")
    action, uid, amt = d[1], int(d[2]), float(d[3] if len(d)>3 else 0)
    if action == "ok":
        await users_col.update_one({"_id": uid}, {"$inc": {"balance": amt}})
        await bot.send_message(uid, f"✅ Баланс поповнено на {amt} 💎")
        await cb.message.edit_caption(caption="Схвалено ✅")
    else:
        await bot.send_message(uid, "❌ Чек відхилено.")
        await cb.message.edit_caption(caption="Відхилено ❌")

# --- ІНШЕ ---

@dp.message(F.text == "🤝 Рефералка")
async def referal_menu(m: types.Message):
    me = await bot.get_me()
    await m.answer(f"🤝 Запрошуйте друзів та отримуйте % від виграшів!\n\n🔗 Посилання: `https://t.me/{me.username}?start={m.from_user.id}`", parse_mode="Markdown")

@dp.message(F.text == "⚙️ Налаштування")
async def settings(m: types.Message):
    await m.answer("⚙️ Налаштування:\n🆘 Підтримка: @vex0o0")

@dp.message(F.text == "⬅️ Назад")
async def back(m: types.Message):
    await m.answer("Головне меню:", reply_markup=main_kb())

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
