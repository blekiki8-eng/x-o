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
CURRATE = 44.50  # Курс грн за 1 💎

logging.basicConfig(level=logging.INFO)
bot = Bot(token=API_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

cluster = AsyncIOMotorClient(MONGO_URL)
db = cluster["game_bot_db"] 
users_col = db["users"]
games_col = db["games"]
finance_col = db["finance"]

# --- СТАНИ FSM ---
class GameStates(StatesGroup):
    wait_rounds = State()
    wait_bet = State()

class FinanceStates(StatesGroup):
    wait_dep_amount = State()
    wait_receipt = State()
    wait_withdraw_amount = State()
    wait_withdraw_wallet = State()

# --- КЛАВІАТУРИ ---
def main_kb():
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="👤 Профіль"), KeyboardButton(text="🎮 Games")],
        [KeyboardButton(text="💎 Баланс"), KeyboardButton(text="🤝 Рефералка")],
        [KeyboardButton(text="⚙️ Налаштування")]
    ], resize_keyboard=True)

def cancel_kb():
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="❌ Скасувати")]], resize_keyboard=True)

async def get_u(uid, ref_id=None):
    u = await users_col.find_one({"_id": uid})
    if not u:
        u = {"_id": uid, "balance": 0.0, "referals_count": 0, "turnover": 0.0, "games_played": 0, "referrer": ref_id}
        await users_col.insert_one(u)
        if ref_id and ref_id != uid:
            await users_col.update_one({"_id": ref_id}, {"$inc": {"referals_count": 1}})
    return u

# --- ОБРОБНИК START / ПРИЄДНАННЯ ДО ГРИ ---
@dp.message(Command("start"))
async def cmd_start(m: types.Message, state: FSMContext):
    await state.clear()
    args = m.text.split()
    user_id = m.from_user.id
    
    ref_id = int(args[1]) if len(args) > 1 and args[1].isdigit() else None
    u = await get_u(user_id, ref_id)
    
    if len(args) > 1 and args[1].startswith("game_"):
        gid = args[1].replace("game_", "")
        g = await games_col.find_one({"game_id": gid, "status": "waiting"})
        
        if not g: return await m.answer("❌ Гра не знайдена або вже почалася.", reply_markup=main_kb())
        if g['creator_id'] == user_id: return await m.answer("❌ Ви не можете грати самі з собою.", reply_markup=main_kb())
        if u['balance'] < g['bet']: return await m.answer(f"❌ Недостатньо балансу! Треба {g['bet']} 💎", reply_markup=main_kb())
        
        await users_col.update_many({"_id": {"$in": [g['creator_id'], user_id]}}, {"$inc": {"balance": -g['bet']}})
        await games_col.update_one({"game_id": gid}, {"$set": {"opponent_id": user_id, "status": "playing", "turn": g['creator_id']}})
        
        await bot.send_message(g['creator_id'], f"🎮 Гравець приєднався!\n\n**Гра створена! Твій хід** `{g['type']}` (натисни щоб скопіювати)", parse_mode="Markdown")
        return await m.answer(f"🎮 Ви приєдналися до гри!\n\n**Очікуйте хід суперника** `{g['type']}`", parse_mode="Markdown", reply_markup=main_kb())

    await m.answer("💎 Вітаємо у Different Games!", reply_markup=main_kb())

# --- ПРОФІЛЬ ---
@dp.message(F.text == "👤 Профіль")
async def profile_cmd(m: types.Message):
    u = await get_u(m.from_user.id)
    await m.answer(
        f"👤 **Мій Профіль**\n\n"
        f"🆔 ID: `{u['_id']}`\n"
        f"💰 Баланс: `{u['balance']:.2f} 💎`\n"
        f"📈 Оборот: `{u.get('turnover', 0.0):.2f} 💎`\n"
        f"🎮 Ігор зіграно: `{u.get('games_played', 0)}`",
        parse_mode="Markdown"
    )

# --- РЕФЕРАЛКА ---
@dp.message(F.text == "🤝 Рефералка")
async def referal_cmd(m: types.Message):
    me = await bot.get_me()
    link = f"https://t.me/{me.username}?start={m.from_user.id}"
    await m.answer(
        "🤝 **Вітаю у Рефералка**\n\n"
        "Суть рефералки в тому, що коли ви запросили друга і він поповнив баланс і зіграв в одну гру, "
        "в якій він виграв — то з його виграшу вам капає на баланс **+0.01%**.\n\n"
        f"🔗 Ваше реферальне посилання:\n`{link}`",
        parse_mode="Markdown"
    )

# --- ГРИ (GAMES) ---
@dp.message(F.text == "🎮 Games")
async def games_menu(m: types.Message):
    kb = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="🎳 Боулінг"), KeyboardButton(text="🎲 Кубик")], [KeyboardButton(text="❌ Скасувати")]], resize_keyboard=True)
    await m.answer("Оберіть режим гри:", reply_markup=kb)

@dp.message(F.text.in_(["🎳 Боулінг", "🎲 Кубик"]))
async def g_mode(m: types.Message, state: FSMContext):
    await state.update_data(type="🎳" if "🎳" in m.text else "🎲")
    kb = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="1"), KeyboardButton(text="5")], [KeyboardButton(text="❌ Скасувати")]], resize_keyboard=True)
    await m.answer("Оберіть кількість раундів:", reply_markup=kb)
    await state.set_state(GameStates.wait_rounds)

@dp.message(GameStates.wait_rounds, F.text.in_(["1", "5"]))
async def g_rounds(m: types.Message, state: FSMContext):
    await state.update_data(r=int(m.text))
    await m.answer("Введіть ставку (💎):", reply_markup=cancel_kb())
    await state.set_state(GameStates.wait_bet)

@dp.message(GameStates.wait_bet)
async def g_create(m: types.Message, state: FSMContext):
    try:
        bet = float(m.text.replace(",", "."))
        u = await get_u(m.from_user.id)
        if u['balance'] < bet: return await m.answer("❌ Недостатньо 💎 на балансі!")
        d = await state.get_data()
        gid = str(uuid.uuid4())[:8]
        await games_col.insert_one({"game_id": gid, "creator_id": m.from_user.id, "status": "waiting", "type": d['type'], "bet": bet, "rounds_total": d['r'], "c_score": [], "o_score": []})
        me = await bot.get_me()
        await m.answer(f"✅ Гра створена! Ваш хід: `{d['type']}` (натисни щоб скопіювати)\n\n🔗 Посилання для опонента:\n`https://t.me/{me.username}?start=game_{gid}`", parse_mode="Markdown", reply_markup=main_kb())
        await state.clear()
    except: await m.answer("Введіть число!")

# --- ПРОЦЕС ГРИ (DICE) ---
@dp.message(F.dice)
async def dice_handler(m: types.Message):
    g = await games_col.find_one({"status": "playing", "turn": m.from_user.id})
    if not g or m.dice.emoji != g['type']: return
    
    is_c = (m.from_user.id == g['creator_id'])
    opp = g['opponent_id'] if is_c else g['creator_id']
    f = "c_score" if is_c else "o_score"
    
    await asyncio.sleep(4)  # Чекаємо поки анімація докрутиться
    val = m.dice.value
    await m.answer(f"У вас випало: {val}")
    await bot.send_message(opp, f"У суперника випало: {val}")
    
    await games_col.update_one({"game_id": g['game_id']}, {"$push": {f: val}})
    upd = await games_col.find_one({"game_id": g['game_id']})
    
    if len(upd['c_score']) == upd['rounds_total'] and len(upd['o_score']) == upd['rounds_total']:
        await finish_game(upd)
    else:
        await games_col.update_one({"game_id": g['game_id']}, {"$set": {"turn": opp}})
        await m.answer("Тепер хід суперника ⏳")
        await bot.send_message(opp, f"Тепер ваш хід! Кидай `{g['type']}`", parse_mode="Markdown")

async def finish_game(g):
    s1, s2 = sum(g['c_score']), sum(g['o_score'])
    for uid in [g['creator_id'], g['opponent_id']]:
        my_s, opp_s = (s1, s2) if uid == g['creator_id'] else (s2, s1)
        res_text = f"📊 **Результат гри**\n\nУ вас: `{my_s}`\nУ противника: `{opp_s}`\n\n"
        
        if s1 == s2:
            await users_col.update_one({"_id": uid}, {"$inc": {"balance": g['bet']}})
            await bot.send_message(uid, res_text + "🤝 Нічия! Ставки повернуті.")
        else:
            win_id = g['creator_id'] if s1 > s2 else g['opponent_id']
            if uid == win_id:
                win_amt = g['bet'] * 1.98
                await users_col.update_one({"_id": uid}, {"$inc": {"balance": win_amt, "turnover": g['bet'], "games_played": 1}})
                # Рефералка
                u_win = await users_col.find_one({"_id": uid})
                if u_win.get("referrer"):
                    await users_col.update_one({"_id": u_win['referrer']}, {"$inc": {"balance": win_amt * 0.0001}})
                await bot.send_message(uid, res_text + f"🏆 Вітаю ви виграли +{win_amt:.2f} 💎")
            else:
                await users_col.update_one({"_id": uid}, {"$inc": {"games_played": 1}})
                await bot.send_message(uid, res_text + "❌ Нажаль ви програли")
    await games_col.update_one({"game_id": g['game_id']}, {"$set": {"status": "finished"}})

# --- БАЛАНС / ПОПОВНЕННЯ / ВИВЕДЕННЯ ---
@dp.message(F.text == "💎 Баланс")
async def balance_menu(m: types.Message):
    u = await get_u(m.from_user.id)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📥 Поповнити", callback_data="dep"), 
         InlineKeyboardButton(text="📤 Вивести", callback_data="withdraw")]
    ])
    await m.answer(f"💰 Ваш баланс: `{u['balance']:.2f} 💎`", reply_markup=kb, parse_mode="Markdown")

@dp.callback_query(F.data == "dep")
async def dep_start(cb: types.CallbackQuery, state: FSMContext):
    await state.set_state(FinanceStates.wait_dep_amount)
    await cb.message.answer("💎 Введіть суму поповнення (у 💎):", reply_markup=cancel_kb())

@dp.message(FinanceStates.wait_dep_amount)
async def dep_amt(m: types.Message, state: FSMContext):
    try:
        amt = float(m.text.replace(",", "."))
        await state.update_data(amt=amt)
        await m.answer(f"Сума: {amt} 💎\nДо оплати: `{(amt * CURRATE) * 1.05:.2f} ГРН`\nРеквізити: `5355 2800 2890 2177`\n\nНадішліть фото чеку!", parse_mode="Markdown")
        await state.set_state(FinanceStates.wait_receipt)
    except: await m.answer("Введіть число!")

@dp.message(FinanceStates.wait_receipt, F.photo)
async def dep_receipt(m: types.Message, state: FSMContext):
    d = await state.get_data()
    tid = str(uuid.uuid4())[:8]
    await finance_col.insert_one({"_id": tid, "uid": m.from_user.id, "amt": d['amt'], "type": "dep"})
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✅ Схвалити", callback_data=f"f_ok_{tid}"), InlineKeyboardButton(text="❌ Відхилити", callback_data=f"f_no_{tid}")]])
    await bot.send_photo(ADMIN_ID, m.photo[-1].file_id, caption=f"📥 Чек на {d['amt']} від {m.from_user.id}", reply_markup=kb)
    await m.answer("✅ Чек надіслано!", reply_markup=main_kb())
    await state.clear()

@dp.callback_query(F.data == "withdraw")
async def withdraw_start(cb: types.CallbackQuery, state: FSMContext):
    await state.set_state(FinanceStates.wait_withdraw_amount)
    await cb.message.answer("💎 Скільки 💎 хочете вивести?", reply_markup=cancel_kb())

@dp.message(FinanceStates.wait_withdraw_amount)
async def draw_amt(m: types.Message, state: FSMContext):
    u = await get_u(m.from_user.id)
    try:
        amt = float(m.text)
        if u['balance'] < amt: return await m.answer("❌ Мало коштів!")
        await state.update_data(amt=amt)
        await m.answer("Введіть номер вашої картки/гаманця:")
        await state.set_state(FinanceStates.wait_withdraw_wallet)
    except: await m.answer("Введіть число!")

@dp.message(FinanceStates.wait_withdraw_wallet)
async def draw_final(m: types.Message, state: FSMContext):
    d = await state.get_data()
    tid = str(uuid.uuid4())[:8]
    await users_col.update_one({"_id": m.from_user.id}, {"$inc": {"balance": -d['amt']}})
    await finance_col.insert_one({"_id": tid, "uid": m.from_user.id, "amt": d['amt'], "type": "draw", "wallet": m.text})
    await bot.send_message(ADMIN_ID, f"📤 ЗАЯВКА НА ВИВІД\nЮзер: `{m.from_user.id}`\nСума: `{d['amt']} 💎`\nГаманець: `{m.text}`", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✅ Виплачено", callback_data=f"f_done_{tid}")]]))
    await m.answer("✅ Заявку на вивід прийнято!", reply_markup=main_kb())
    await state.clear()

# --- НАЛАШТУВАННЯ ТА АДМІНКА ---
@dp.message(F.text == "⚙️ Налаштування")
async def settings_cmd(m: types.Message):
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🆘 Підтримка", url="https://t.me/vex0o0")]])
    if m.from_user.id == ADMIN_ID:
        kb.inline_keyboard.append([InlineKeyboardButton(text="👨‍💻 Адмін Панель", callback_data="admin_panel")])
    await m.answer("⚙️ Налаштування:", reply_markup=kb)

@dp.callback_query(F.data == "admin_panel")
async def admin_panel(cb: types.CallbackQuery):
    if cb.from_user.id != ADMIN_ID: return
    users_count = await users_col.count_documents({})
    await cb.message.answer(f"📊 **Адмін Панель**\n\nВсього користувачів: `{users_count}`", parse_mode="Markdown")

@dp.callback_query(F.data.startswith("f_ok_"))
async def admin_f_ok(cb: types.CallbackQuery):
    tid = cb.data[5:]; f = await finance_col.find_one({"_id": tid})
    if f:
        await users_col.update_one({"_id": f['uid']}, {"$inc": {"balance": f['amt']}})
        await bot.send_message(f['uid'], f"✅ Баланс поповнено на {f['amt']} 💎")
        await cb.message.delete(); await finance_col.delete_one({"_id": tid})

@dp.callback_query(F.data.startswith("f_done_"))
async def admin_f_done(cb: types.CallbackQuery):
    await cb.message.answer("Виконано!"); await cb.message.delete()

@dp.message(F.text == "❌ Скасувати")
async def global_cancel(m: types.Message, state: FSMContext):
    await state.clear(); await m.answer("Дію скасовано", reply_markup=main_kb())

async def main(): await dp.start_polling(bot)
if __name__ == "__main__": asyncio.run(main())
