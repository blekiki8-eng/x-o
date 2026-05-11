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
CURRATE = 44.50  # Курс за 1 💎

logging.basicConfig(level=logging.INFO)
bot = Bot(token=API_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

cluster = AsyncIOMotorClient(MONGO_URL)
db = cluster["game_bot_db"] 
users_col = db["users"]
games_col = db["games"]
stats_col = db["stats"]

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

def rounds_kb():
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="1 раунд"), KeyboardButton(text="5 раундів")],
        [KeyboardButton(text="❌ Скасувати")]
    ], resize_keyboard=True)

# --- ДОПОМІЖНІ ФУНКЦІЇ ---
async def get_u(uid, ref_id=None):
    u = await users_col.find_one({"_id": uid})
    if not u:
        u = {
            "_id": uid, "balance": 0.0, "referals_count": 0, 
            "turnover": 0.0, "games_played": 0, "referrer": ref_id,
            "is_active_ref": False
        }
        await users_col.insert_one(u)
    return u

async def add_admin_profit(amount):
    await stats_col.update_one({"_id": "global"}, {"$inc": {"admin_profit": amount}}, upsert=True)

# --- СКАСУВАННЯ ---
@dp.message(F.text.in_(["❌ Скасувати", "⬅️ Назад"]))
async def global_cancel(m: types.Message, state: FSMContext):
    await state.clear()
    await m.answer("Повернення в головне меню ↩️", reply_markup=main_kb())

# --- ПРОФІЛЬ ---
@dp.message(F.text == "👤 Профіль")
async def profile_cmd(m: types.Message, state: FSMContext):
    await state.clear()
    u = await get_u(m.from_user.id)
    text = (f"👤 **Профіль:**\n\n🆔 Id: `{m.from_user.id}`\n💰 Баланс: `{u.get('balance', 0.0):.2f}` 💎\n\n"
            f"Запрошених: `{u.get('referals_count', 0)}`👤\nОборот: `{u.get('turnover', 0.0):.2f}` 💎\n"
            f"Ігор: `{u.get('games_played', 0)}` 🎳")
    await m.answer(text, parse_mode="Markdown")

# --- РОЗДІЛ GAMES ---
@dp.message(F.text == "🎮 Games")
async def games_menu(m: types.Message, state: FSMContext):
    await state.clear()
    kb = ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="Гра Боулінг 🎳"), KeyboardButton(text="Гра Кубик 🎲")],
        [KeyboardButton(text="⬅️ Назад")]
    ], resize_keyboard=True)
    await m.answer("Оберіть гру:", reply_markup=kb)

@dp.message(F.text.in_(["Гра Боулінг 🎳", "Гра Кубик 🎲"]))
async def choose_rounds(m: types.Message, state: FSMContext):
    g_type = "🎳" if "Боулінг" in m.text else "🎲"
    await state.update_data(g_type=g_type)
    await m.answer(f"Обрано {m.text}. Скільки раундів граємо?", reply_markup=rounds_kb())
    await state.set_state(GameStates.wait_rounds)

@dp.message(GameStates.wait_rounds, F.text.in_(["1 раунд", "5 раундів"]))
async def choose_bet(m: types.Message, state: FSMContext):
    r = 1 if "1" in m.text else 5
    await state.update_data(rounds=r)
    await m.answer(f"Гра на {r} р. Введіть ставку 💎:", reply_markup=cancel_kb())
    await state.set_state(GameStates.wait_bet)

@dp.message(GameStates.wait_bet)
async def create_game(m: types.Message, state: FSMContext):
    try:
        bet = float(m.text.replace(",", "."))
        u = await get_u(m.from_user.id)
        if u['balance'] < bet: return await m.answer("❌ Недостатньо балансу!")
        if bet <= 0: return await m.answer("❌ Ставка має бути більше 0")
        
        data = await state.get_data()
        gid = str(uuid.uuid4())[:8]
        await games_col.insert_one({
            "game_id": gid, "creator_id": m.from_user.id, "opponent_id": None,
            "type": data['g_type'], "bet": bet, "rounds_total": data['rounds'],
            "status": "waiting", "c_score": [], "o_score": []
        })
        me = await bot.get_me()
        await m.answer(f"✅ Гра створена!\n`https://t.me/{me.username}?start=game_{gid}`", reply_markup=main_kb(), parse_mode="Markdown")
        await state.clear()
    except: await m.answer("Введіть число!")

# --- СИСТЕМА ПОПОВНЕННЯ (НОРМ ЗАЯВКА) ---
@dp.callback_query(F.data == "dep")
async def dep_start(cb: types.CallbackQuery, state: FSMContext):
    await state.set_state(FinanceStates.wait_dep_amount)
    await cb.message.answer("💎 **Поповнення**\nВведіть суму в 💎 (мін 0.50):", reply_markup=cancel_kb(), parse_mode="Markdown")

@dp.message(FinanceStates.wait_dep_amount)
async def dep_amt(m: types.Message, state: FSMContext):
    try:
        amt = float(m.text.replace(",", "."))
        if amt < 0.5: return await m.answer("❌ Мінімум 0.50 💎")
        await state.update_data(amt=amt)
        total_uah = (amt * CURRATE) * 1.05
        text = (
            f"📊 **Заявка на поповнення:**\n\n"
            f"💰 Сума: `{amt:.2f} 💎`\n"
            f"💵 До оплати: `{total_uah:.2f} ГРН` (з комісією 5%)\n"
            f"📈 Курс: `{CURRATE} ГРН`\n\n"
            f"💳 **Реквізити (Mono):**\n`5355 2800 2890 2177`\n\n"
            f"Надішліть **скріншот чека** сюди:"
        )
        await m.answer(text, parse_mode="Markdown", reply_markup=cancel_kb())
        await state.set_state(FinanceStates.wait_receipt)
    except: await m.answer("Введіть число!")

@dp.message(FinanceStates.wait_receipt, F.photo | F.document)
async def dep_final(m: types.Message, state: FSMContext):
    data = await state.get_data(); amt = data['amt']; uid = m.from_user.id
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Схвалити", callback_data=f"adm_ok_{uid}_{amt}"),
        InlineKeyboardButton(text="❌ Відхилити", callback_data=f"adm_no_{uid}")
    ]])
    await bot.send_message(ADMIN_ID, f"🔔 Чек {amt} від {uid}")
    if m.photo: await bot.send_photo(ADMIN_ID, m.photo[-1].file_id, reply_markup=kb)
    else: await bot.send_document(ADMIN_ID, m.document.file_id, reply_markup=kb)
    await m.answer("✅ Квитанцію прийнято. Чекайте...", reply_markup=main_kb()); await state.clear()

# --- ВИВЕДЕННЯ ---
@dp.callback_query(F.data == "with")
async def with_start(cb: types.CallbackQuery, state: FSMContext):
    await state.set_state(FinanceStates.wait_withdraw_amount)
    await cb.message.answer("Введіть суму виводу (мін 4.5 💎):", reply_markup=cancel_kb())

@dp.message(FinanceStates.wait_withdraw_amount)
async def with_amt(m: types.Message, state: FSMContext):
    try:
        amt = float(m.text.replace(",","."))
        u = await get_u(m.from_user.id)
        if amt < 4.5 or u['balance'] < amt: return await m.answer("❌ Помилка балансу/суми")
        await state.update_data(w_amt=amt)
        await m.answer("Введіть реквізити (ІПН IBAN ПІБ):", reply_markup=cancel_kb())
        await state.set_state(FinanceStates.wait_withdraw_details)
    except: await m.answer("Число!")

@dp.message(FinanceStates.wait_withdraw_details)
async def with_final(m: types.Message, state: FSMContext):
    data = await state.get_data()
    await users_col.update_one({"_id": m.from_user.id}, {"$inc": {"balance": -data['w_amt']}})
    await bot.send_message(ADMIN_ID, f"📤 Вивід {data['w_amt']} від {m.from_user.id}\nДані: {m.text}")
    await m.answer("✅ Заявка надіслана", reply_markup=main_kb()); await state.clear()

# --- АДМІН ДІЇ ---
@dp.callback_query(F.data.startswith("adm_"))
async def adm_cb(cb: types.CallbackQuery):
    _, act, uid, *amt = cb.data.split("_"); uid = int(uid)
    if act == "ok":
        u = await get_u(uid)
        if not u.get("is_active_ref") and u.get("referrer"):
            await users_col.update_one({"_id": u["referrer"]}, {"$inc": {"referals_count": 1}})
            await users_col.update_one({"_id": uid}, {"$set": {"is_active_ref": True}})
        await users_col.update_one({"_id": uid}, {"$inc": {"balance": float(amt[0])}})
        await bot.send_message(uid, f"✅ Баланс поповнено!"); await cb.message.answer("OK")
    else: await bot.send_message(uid, "❌ Відхилено"); await cb.message.answer("NO")

# --- ЛОГІКА ІГОР (DICE) ---
async def join_game_logic(m, gid):
    g = await games_col.find_one({"game_id": gid, "status": "waiting"})
    if not g or g['creator_id'] == m.from_user.id: return await m.answer("❌ Гра недоступна")
    u2 = await get_u(m.from_user.id)
    if u2['balance'] < g['bet']: return await m.answer("❌ Мало коштів")
    await users_col.update_many({"_id": {"$in": [g['creator_id'], m.from_user.id]}}, {"$inc": {"balance": -g['bet'], "games_played": 1}})
    await games_col.update_one({"game_id": gid}, {"$set": {"opponent_id": m.from_user.id, "status": "playing", "turn": g['creator_id']}})
    await bot.send_message(g['creator_id'], f"Гру розпочато! Кидай {g['type']}")
    await m.answer("Гру розпочато! Чекай хід суперника...")

@dp.message(F.dice)
async def handle_dice(m: types.Message):
    g = await games_col.find_one({"status": "playing", "$or": [{"creator_id": m.from_user.id}, {"opponent_id": m.from_user.id}]})
    if not g or g['turn'] != m.from_user.id or m.dice.emoji != g['type']: return
    is_c = (m.from_user.id == g['creator_id'])
    await games_col.update_one({"game_id": g['game_id']}, {"$push": {"c_score" if is_c else "o_score": m.dice.value}, "$set": {"turn": g['opponent_id'] if is_c else g['creator_id']}})
    upd = await games_col.find_one({"game_id": g['game_id']})
    if len(upd['c_score']) == upd['rounds_total'] and len(upd['o_score']) == upd['rounds_total']:
        s1, s2 = sum(upd['c_score']), sum(upd['o_score'])
        win_sum = upd['bet'] * 1.98
        if s1 == s2:
            for uid in [upd['creator_id'], upd['opponent_id']]:
                await users_col.update_one({"_id": uid}, {"$inc": {"balance": upd['bet']}})
                await bot.send_message(uid, "Нічия! Повернення.")
        else:
            win_id = upd['creator_id'] if s1 > s2 else upd['opponent_id']
            lose_id = upd['opponent_id'] if s1 > s2 else upd['creator_id']
            await users_col.update_one({"_id": win_id}, {"$inc": {"balance": win_sum, "turnover": upd['bet']}})
            u_win = await users_col.find_one({"_id": win_id})
            if u_win.get("referrer") and u_win.get("is_active_ref"):
                await users_col.update_one({"_id": u_win["referrer"]}, {"$inc": {"balance": upd['bet']*0.01}})
                await add_admin_profit(upd['bet']*0.01)
            else: await add_admin_profit(upd['bet']*0.02)
            await bot.send_message(win_id, f"🏆 Win! +{win_sum:.2f}"); await bot.send_message(lose_id, "❌ Lose.")
        await games_col.update_one({"game_id": upd['game_id']}, {"$set": {"status": "finished"}})
    else: await bot.send_message(upd['turn'], f"Супернику випало {m.dice.value}. Твій хід!")

# --- МЕНЮ ТА ІНШЕ ---
@dp.message(F.text == "🤝 Рефералка")
async def ref_cmd(m: types.Message, state: FSMContext):
    await state.clear()
    u = await get_u(m.from_user.id); me = await bot.get_me()
    await m.answer(f"🤝 Реферали: `{u.get('referals_count',0)}`👤\n\nЛінк: `https://t.me/{me.username}?start={m.from_user.id}`", parse_mode="Markdown")

@dp.message(F.text == "💎 Баланс")
async def bal_cmd(m: types.Message, state: FSMContext):
    await state.clear()
    u = await get_u(m.from_user.id)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="📥 Поповнити", callback_data="dep"), InlineKeyboardButton(text="📤 Вивести", callback_data="with")]])
    await m.answer(f"💰 Баланс: `{u['balance']:.2f}` 💎", reply_markup=kb, parse_mode="Markdown")

@dp.message(F.text == "⚙️ Налаштування")
async def sett_cmd(m: types.Message, state: FSMContext):
    await state.clear()
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🆘 Підтримка", url="https://t.me/vex0o0")]])
    if m.from_user.id == ADMIN_ID: kb.inline_keyboard.append([InlineKeyboardButton(text="👨‍💻 Адмін Панель", callback_data="admin_panel")])
    await m.answer("⚙️ Налаштування:", reply_markup=kb)

@dp.callback_query(F.data == "admin_panel")
async def ap_cb(cb: types.CallbackQuery):
    stats = await stats_col.find_one({"_id": "global"})
    profit = stats.get("admin_profit", 0) if stats else 0
    t_turn = await users_col.find().sort("turnover", -1).limit(10).to_list(10)
    l_turn = "\n".join([f"{i+1}. `{u['_id']}` - {u.get('turnover',0):.2f}" for i, u in enumerate(t_turn)])
    t_refs = await users_col.find().sort("referals_count", -1).limit(10).to_list(10)
    l_refs = "\n".join([f"{i+1}. `{u['_id']}` - {u.get('referals_count',0)}👤" for i, u in enumerate(t_refs)])
    await cb.message.answer(f"📊 Дохід: {profit:.4f}\n\n🏆 Топ Оборот:\n{l_turn}\n\n👥 Топ Рефи:\n{l_refs}", parse_mode="Markdown")

@dp.message(Command("start"))
async def start_handler(m: types.Message, state: FSMContext):
    await state.clear(); args = m.text.split()
    ref_id = int(args[1]) if len(args) > 1 and args[1].isdigit() else None
    await get_u(m.from_user.id, ref_id)
    if len(args) > 1 and args[1].startswith("game_"): await join_game_logic(m, args[1].replace("game_", ""))
    await m.answer("💎 Вітаємо!", reply_markup=main_kb())

async def main(): await dp.start_polling(bot)
if __name__ == "__main__": asyncio.run(main())
