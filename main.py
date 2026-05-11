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
stats_col = db["stats"]

# --- СТАНИ (FSM) ---
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
        u = {"_id": uid, "balance": 0.0, "referals_count": 0, "turnover": 0.0, "games_played": 0, "referrer": ref_id, "is_active_ref": False}
        await users_col.insert_one(u)
    return u

async def add_admin_profit(amount):
    await stats_col.update_one({"_id": "global"}, {"$inc": {"admin_profit": amount}}, upsert=True)

# --- ОБРОБКА СКАСУВАННЯ ---
@dp.message(F.text.in_(["❌ Скасувати", "⬅️ Назад"]))
async def global_cancel(m: types.Message, state: FSMContext):
    await state.clear()
    await m.answer("Повернення в головне меню ↩️", reply_markup=main_kb())

# --- РОЗДІЛ ІГОР (GAMES) ---
@dp.message(F.text == "🎮 Games")
async def games_menu(m: types.Message, state: FSMContext):
    await state.clear()
    kb = ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="Гра Боулінг 🎳"), KeyboardButton(text="Гра Кубик 🎲")],
        [KeyboardButton(text="⬅️ Назад")]
    ], resize_keyboard=True)
    await m.answer("Оберіть режим гри:", reply_markup=kb)

@dp.message(F.text.in_(["Гра Боулінг 🎳", "Гра Кубик 🎲"]))
async def choose_rounds(m: types.Message, state: FSMContext):
    g_type = "🎳" if "Боулінг" in m.text else "🎲"
    await state.update_data(g_type=g_type)
    await m.answer(f"Обрано {g_type}. Скільки раундів граємо?", reply_markup=rounds_kb())
    await state.set_state(GameStates.wait_rounds)

@dp.message(GameStates.wait_rounds, F.text.in_(["1 раунд", "5 раундів"]))
async def choose_bet(m: types.Message, state: FSMContext):
    r = 1 if "1" in m.text else 5
    await state.update_data(rounds=r)
    await m.answer(f"Гра на {r} раундів. Введіть суму вашої ставки (💎):", reply_markup=cancel_kb())
    await state.set_state(GameStates.wait_bet)

@dp.message(GameStates.wait_bet)
async def create_game_final(m: types.Message, state: FSMContext):
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
        await m.answer(f"✅ Гра на {data['rounds']} раундів створена!\n\nПосилання для суперника:\n`https://t.me/{me.username}?start=game_{gid}`", reply_markup=main_kb(), parse_mode="Markdown")
        await state.clear()
    except: await m.answer("Будь ласка, введіть число (суму ставки)!")

# --- ЛОГІКА КИДКІВ ТА ПЕРЕМОГИ ---
async def join_game_logic(m, gid):
    g = await games_col.find_one({"game_id": gid, "status": "waiting"})
    if not g or g['creator_id'] == m.from_user.id: return await m.answer("❌ Гра недоступна або ви її творець.")
    u2 = await get_u(m.from_user.id)
    if u2['balance'] < g['bet']: return await m.answer("❌ Недостатньо балансу для приєднання!")
    
    await users_col.update_many({"_id": {"$in": [g['creator_id'], m.from_user.id]}}, {"$inc": {"balance": -g['bet'], "games_played": 1}})
    await games_col.update_one({"game_id": gid}, {"$set": {"opponent_id": m.from_user.id, "status": "playing", "turn": g['creator_id']}})
    
    await bot.send_message(g['creator_id'], f"🎮 Суперник приєднався! Ваш хід — кидайте {g['type']}")
    await m.answer(f"🎮 Ви приєдналися. Очікуйте хід суперника...")

@dp.message(F.dice)
async def handle_dice(m: types.Message):
    g = await games_col.find_one({"status": "playing", "turn": m.from_user.id})
    if not g or m.dice.emoji != g['type']: return

    is_creator = (m.from_user.id == g['creator_id'])
    field = "c_score" if is_creator else "o_score"
    next_player = g['opponent_id'] if is_creator else g['creator_id']
    
    await games_col.update_one({"game_id": g['game_id']}, {"$push": {field: m.dice.value}, "$set": {"turn": next_player}})
    upd_g = await games_col.find_one({"game_id": g['game_id']})
    
    if len(upd_g['c_score']) == upd_g['rounds_total'] and len(upd_g['o_score']) == upd_g['rounds_total']:
        await finish_game(upd_g)
    else:
        await bot.send_message(next_player, f"Ваш хід! Суперник викинув {m.dice.value}. Кидайте {g['type']}")

async def finish_game(g):
    s1, s2 = sum(g['c_score']), sum(g['o_score'])
    win_sum = g['bet'] * 1.98
    res_text = f"🏁 Гра завершена! Рахунок {s1}:{s2}\n"
    
    if s1 == s2:
        await users_col.update_many({"_id": {"$in": [g['creator_id'], g['opponent_id']]}}, {"$inc": {"balance": g['bet']}})
        for uid in [g['creator_id'], g['opponent_id']]: await bot.send_message(uid, res_text + "🤝 Нічия! Ставки повернуті.")
    else:
        winner = g['creator_id'] if s1 > s2 else g['opponent_id']
        loser = g['opponent_id'] if s1 > s2 else g['creator_id']
        await users_col.update_one({"_id": winner}, {"$inc": {"balance": win_sum, "turnover": g['bet']}})
        
        u_win = await users_col.find_one({"_id": winner})
        if u_win.get("referrer") and u_win.get("is_active_ref"):
            await users_col.update_one({"_id": u_win["referrer"]}, {"$inc": {"balance": g['bet']*0.01}})
            await add_admin_profit(g['bet']*0.01)
        else: await add_admin_profit(g['bet']*0.02)
        
        await bot.send_message(winner, res_text + f"🏆 Ви перемогли! Виграш: {win_sum:.2f} 💎")
        await bot.send_message(loser, res_text + "❌ Ви програли.")
    await games_col.update_one({"game_id": g['game_id']}, {"$set": {"status": "finished"}})

# --- ФІНАНСИ (ПОПОВНЕННЯ ТА ВИВІД) ---
@dp.callback_query(F.data == "dep")
async def dep_start(cb: types.CallbackQuery, state: FSMContext):
    await state.set_state(FinanceStates.wait_dep_amount)
    await cb.message.answer("💎 Введіть суму поповнення (мін 0.50 💎):", reply_markup=cancel_kb())

@dp.message(FinanceStates.wait_dep_amount)
async def dep_amt(m: types.Message, state: FSMContext):
    try:
        amt = float(m.text.replace(",", "."))
        if amt < 0.5: return await m.answer("❌ Мінімум 0.50 💎")
        await state.update_data(amt=amt)
        total_uah = (amt * CURRATE) * 1.05
        text = (f"📊 **Заявка на поповнення:**\n\n💰 Сума: `{amt:.2f} 💎`\n💵 До оплати: `{total_uah:.2f} ГРН`\n"
                f"💳 **Mono:** `5355 2800 2890 2177`\n\nНадішліть скріншот чека одним повідомленням:")
        await m.answer(text, parse_mode="Markdown", reply_markup=cancel_kb())
        await state.set_state(FinanceStates.wait_receipt)
    except: await m.answer("Введіть коректне число!")

@dp.message(FinanceStates.wait_receipt, F.photo | F.document)
async def dep_receipt(m: types.Message, state: FSMContext):
    data = await state.get_data()
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Схвалити", callback_data=f"adm_ok_{m.from_user.id}_{data['amt']}"),
        InlineKeyboardButton(text="❌ Відхилити", callback_data=f"adm_no_{m.from_user.id}")
    ]])
    await bot.send_message(ADMIN_ID, f"🔔 Новий чек на {data['amt']} 💎 від {m.from_user.id}")
    if m.photo: await bot.send_photo(ADMIN_ID, m.photo[-1].file_id, reply_markup=kb)
    else: await bot.send_document(ADMIN_ID, m.document.file_id, reply_markup=kb)
    await m.answer("✅ Чек надіслано на перевірку!", reply_markup=main_kb())
    await state.clear()

@dp.callback_query(F.data == "with")
async def with_start(cb: types.CallbackQuery, state: FSMContext):
    await state.set_state(FinanceStates.wait_withdraw_amount)
    await cb.message.answer("📤 Введіть суму для виводу (мін 4.5 💎):", reply_markup=cancel_kb())

@dp.message(FinanceStates.wait_withdraw_amount)
async def with_amt(m: types.Message, state: FSMContext):
    try:
        amt = float(m.text.replace(",", "."))
        u = await get_u(m.from_user.id)
        if amt < 4.5 or u['balance'] < amt: return await m.answer("❌ Недостатньо балансу або сума менша 4.5")
        await state.update_data(w_amt=amt)
        await m.answer("💳 Введіть реквізити (Номер картки та ПІБ):", reply_markup=cancel_kb())
        await state.set_state(FinanceStates.wait_withdraw_details)
    except: await m.answer("Введіть число!")

@dp.message(FinanceStates.wait_withdraw_details)
async def with_final(m: types.Message, state: FSMContext):
    data = await state.get_data()
    await users_col.update_one({"_id": m.from_user.id}, {"$inc": {"balance": -data['w_amt']}})
    await bot.send_message(ADMIN_ID, f"📤 ЗАЯВКА НА ВИВІД\nЮзер: `{m.from_user.id}`\nСума: `{data['w_amt']} 💎`\nРеквізити: `{m.text}`")
    await m.answer("✅ Заявку на вивід прийнято!", reply_markup=main_kb())
    await state.clear()

# --- МЕНЮ ТА ПРОФІЛЬ ---
@dp.message(Command("start"))
async def start_handler(m: types.Message, state: FSMContext):
    await state.clear()
    args = m.text.split()
    ref_id = int(args[1]) if len(args) > 1 and args[1].isdigit() else None
    await get_u(m.from_user.id, ref_id)
    if len(args) > 1 and args[1].startswith("game_"):
        await join_game_logic(m, args[1].replace("game_", ""))
    await m.answer("💎 Вітаємо у Different Games!", reply_markup=main_kb())

@dp.message(F.text == "👤 Профіль")
async def profile_cmd(m: types.Message):
    u = await get_u(m.from_user.id)
    await m.answer(f"👤 **Профіль:**\n\n🆔: `{m.from_user.id}`\n💰 Баланс: `{u['balance']:.2f} 💎`\n🎳 Ігор зіграно: `{u['games_played']}`", parse_mode="Markdown")

@dp.message(F.text == "💎 Баланс")
async def bal_cmd(m: types.Message):
    u = await get_u(m.from_user.id)
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="📥 Поповнити", callback_data="dep"),
        InlineKeyboardButton(text="📤 Вивести", callback_data="with")
    ]])
    await m.answer(f"💰 Ваш поточний баланс: `{u['balance']:.2f} 💎`", reply_markup=kb, parse_mode="Markdown")

@dp.message(F.text == "🤝 Рефералка")
async def ref_cmd(m: types.Message):
    u = await get_u(m.from_user.id); me = await bot.get_me()
    await m.answer(f"🤝 **Реферальна система**\n\nВи отримуєте 1% від виграшу друзів!\nРефералів: `{u.get('referals_count',0)}`👤\n\nВаше посилання:\n`https://t.me/{me.username}?start={m.from_user.id}`", parse_mode="Markdown")

@dp.message(F.text == "⚙️ Налаштування")
async def sett_cmd(m: types.Message):
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🆘 Підтримка", url="https://t.me/vex0o0")]])
    if m.from_user.id == ADMIN_ID: kb.inline_keyboard.append([InlineKeyboardButton(text="👨‍💻 Адмін Панель", callback_data="admin_panel")])
    await m.answer("Налаштування та підтримка:", reply_markup=kb)

# --- АДМІН-ПАНЕЛЬ ТА ОБРОБКА АДМІНОМ ---
@dp.callback_query(F.data == "admin_panel")
async def admin_panel_cb(cb: types.CallbackQuery):
    if cb.from_user.id != ADMIN_ID: return
    stats = await stats_col.find_one({"_id": "global"})
    profit = stats.get("admin_profit", 0) if stats else 0
    t_turn = await users_col.find().sort("turnover", -1).limit(5).to_list(5)
    t_refs = await users_col.find().sort("referals_count", -1).limit(5).to_list(5)
    
    text = f"📊 **Статистика адміна**\n\nДохід: `{profit:.4f} 💎`\n\n🏆 **Топ Оборот:**\n"
    text += "\n".join([f"- `{u['_id']}`: {u.get('turnover',0):.2f}" for u in t_turn])
    text += "\n\n👥 **Топ Реферали:**\n"
    text += "\n".join([f"- `{u['_id']}`: {u.get('referals_count',0)}" for u in t_refs])
    await cb.message.answer(text, parse_mode="Markdown")

@dp.callback_query(F.data.startswith("adm_"))
async def adm_process(cb: types.CallbackQuery):
    _, act, uid, *amt = cb.data.split("_")
    uid = int(uid)
    if act == "ok":
        amount = float(amt[0]); u = await get_u(uid)
        if not u.get("is_active_ref") and u.get("referrer"):
            await users_col.update_one({"_id": u["referrer"]}, {"$inc": {"referals_count": 1}})
            await users_col.update_one({"_id": uid}, {"$set": {"is_active_ref": True}})
        await users_col.update_one({"_id": uid}, {"$inc": {"balance": amount}})
        await bot.send_message(uid, f"✅ Баланс поповнено на {amount} 💎!")
        await cb.message.edit_text("Схвалено ✅")
    else:
        await bot.send_message(uid, "❌ Ваш чек було відхилено."); await cb.message.edit_text("Відхилено ❌")

# --- ЗАПУСК ---
async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
