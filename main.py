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

# --- СКАСУВАННЯ ТА НАЗАД ---
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
    # Тут ми НЕ чистимо стан повністю, а переходимо до вибору раундів
    g_type = "🎳" if "Боулінг" in m.text else "🎲"
    await state.update_data(g_type=g_type)
    await m.answer(f"Обрано {m.text}. Скільки раундів граємо?", reply_markup=rounds_kb())
    await state.set_state(GameStates.wait_rounds)

@dp.message(GameStates.wait_rounds, F.text.in_(["1 раунд", "5 раундів"]))
async def choose_bet(m: types.Message, state: FSMContext):
    r = 1 if "1" in m.text else 5
    await state.update_data(rounds=r)
    await m.answer(f"Гра на {r} раундів. Введіть вашу ставку 💎:", reply_markup=cancel_kb())
    await state.set_state(GameStates.wait_bet)

@dp.message(GameStates.wait_bet)
async def create_game(m: types.Message, state: FSMContext):
    if m.text == "❌ Скасувати": return # Захист, якщо спрацює фільтр стану
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
        await m.answer(f"✅ Гра створена!\n\nПосилання для суперника:\n`https://t.me/{me.username}?start=game_{gid}`", reply_markup=main_kb(), parse_mode="Markdown")
        await state.clear()
    except: await m.answer("Будь ласка, введіть число (суму ставки)!")

# --- ЛОГІКА КИДКІВ ---
async def join_game_logic(m, gid):
    g = await games_col.find_one({"game_id": gid, "status": "waiting"})
    if not g or g['creator_id'] == m.from_user.id: return await m.answer("❌ Гра вже зайнята або недоступна.")
    u2 = await get_u(m.from_user.id)
    if u2['balance'] < g['bet']: return await m.answer("❌ Недостатньо коштів для ставки!")
    
    await users_col.update_many({"_id": {"$in": [g['creator_id'], m.from_user.id]}}, {"$inc": {"balance": -g['bet'], "games_played": 1}})
    await games_col.update_one({"game_id": gid}, {"$set": {"opponent_id": m.from_user.id, "status": "playing", "turn": g['creator_id']}})
    
    await bot.send_message(g['creator_id'], f"🎮 Гравець приєднався! Твій хід — кидай {g['type']}")
    await m.answer(f"🎮 Ти приєднався! Ставка {g['bet']} 💎 списана. Очікуй хід суперника...")

@dp.message(F.dice)
async def handle_dice(m: types.Message):
    g = await games_col.find_one({"status": "playing", "$or": [{"creator_id": m.from_user.id}, {"opponent_id": m.from_user.id}]})
    if not g or g['turn'] != m.from_user.id or m.dice.emoji != g['type']: return
    
    is_c = (m.from_user.id == g['creator_id'])
    field = "c_score" if is_c else "o_score"
    next_turn = g['opponent_id'] if is_c else g['creator_id']
    
    await games_col.update_one({"game_id": g['game_id']}, {"$push": {field: m.dice.value}, "$set": {"turn": next_turn}})
    
    upd = await games_col.find_one({"game_id": g['game_id']})
    if len(upd['c_score']) == upd['rounds_total'] and len(upd['o_score']) == upd['rounds_total']:
        await process_finish(upd, sum(upd['c_score']), sum(upd['o_score']))
    else:
        await bot.send_message(next_turn, f"Твій хід! Супернику випало {m.dice.value}. Кидай {g['type']}")

async def process_finish(g, s1, s2):
    win_sum = g['bet'] * 1.98
    if s1 == s2:
        for uid in [g['creator_id'], g['opponent_id']]:
            await users_col.update_one({"_id": uid}, {"$inc": {"balance": g['bet']}})
            await bot.send_message(uid, f"🤝 Нічия ({s1}:{s2})! Ставки повернуто.")
    else:
        win_id = g['creator_id'] if s1 > s2 else g['opponent_id']
        lose_id = g['opponent_id'] if s1 > s2 else g['creator_id']
        
        await users_col.update_one({"_id": win_id}, {"$inc": {"balance": win_sum, "turnover": g['bet']}})
        u_win = await users_col.find_one({"_id": win_id})
        
        # Рефералка (1%)
        if u_win.get("referrer") and u_win.get("is_active_ref"):
            await users_col.update_one({"_id": u_win["referrer"]}, {"$inc": {"balance": g['bet']*0.01}})
            await add_admin_profit(g['bet']*0.01)
        else:
            await add_admin_profit(g['bet']*0.02)
            
        await bot.send_message(win_id, f"🏆 Перемога! Рахунок {s1}:{s2}. Ви виграли {win_sum:.2f} 💎")
        await bot.send_message(lose_id, f"❌ Програш. Рахунок {s1}:{s2}.")
    
    await games_col.update_one({"game_id": g['game_id']}, {"$set": {"status": "finished"}})

# --- ФІНАНСИ ---
@dp.callback_query(F.data == "dep")
async def dep_start(cb: types.CallbackQuery, state: FSMContext):
    await state.set_state(FinanceStates.wait_dep_amount)
    await cb.message.answer("Введіть суму поповнення (мін 0.50 💎):", reply_markup=cancel_kb())

@dp.message(FinanceStates.wait_dep_amount)
async def dep_amt(m: types.Message, state: FSMContext):
    try:
        amt = float(m.text.replace(",","."))
        if amt < 0.5: return await m.answer("❌ Мінімальна сума 0.50 💎")
        await state.update_data(amt=amt)
        total = (amt * CURRATE) * 1.05
        await m.answer(f"До оплати: {total:.2f} ГРН\nКарта: `5355 2800 2890 2177`\n\nНадішліть фото квитанції:", reply_markup=cancel_kb())
        await state.set_state(FinanceStates.wait_receipt)
    except: await m.answer("Введіть число!")

@dp.message(FinanceStates.wait_receipt, F.photo | F.document)
async def dep_final(m: types.Message, state: FSMContext):
    data = await state.get_data()
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Схвалити", callback_data=f"adm_ok_{m.from_user.id}_{data['amt']}"),
        InlineKeyboardButton(text="❌ Відхилити", callback_data=f"adm_no_{m.from_user.id}")
    ]])
    await bot.send_message(ADMIN_ID, f"🔔 Чек на {data['amt']} від `{m.from_user.id}`")
    if m.photo: await bot.send_photo(ADMIN_ID, m.photo[-1].file_id, reply_markup=kb)
    else: await bot.send_document(ADMIN_ID, m.document.file_id, reply_markup=kb)
    await m.answer("⏳ Квитанція надіслана. Очікуйте підтвердження.", reply_markup=main_kb())
    await state.clear()

@dp.callback_query(F.data.startswith("adm_"))
async def adm_callback(cb: types.CallbackQuery):
    _, act, uid, *amt = cb.data.split("_")
    uid = int(uid)
    if act == "ok":
        amount = float(amt[0])
        u = await get_u(uid)
        # Активуємо реферала, якщо це перший донат
        if not u.get("is_active_ref") and u.get("referrer"):
            await users_col.update_one({"_id": u["referrer"]}, {"$inc": {"referals_count": 1}})
            await users_col.update_one({"_id": uid}, {"$set": {"is_active_ref": True}})
        await users_col.update_one({"_id": uid}, {"$inc": {"balance": amount}})
        await bot.send_message(uid, f"✅ Ваш баланс поповнено на {amount} 💎!")
        await cb.message.answer("Схвалено ✅")
    else:
        await bot.send_message(uid, "❌ Ваш чек було відхилено."); await cb.message.answer("Відхилено ❌")

@dp.callback_query(F.data == "with")
async def with_start(cb: types.CallbackQuery, state: FSMContext):
    await state.set_state(FinanceStates.wait_withdraw_amount)
    await cb.message.answer("Введіть суму для виводу (мін 4.5 💎):", reply_markup=cancel_kb())

@dp.message(FinanceStates.wait_withdraw_amount)
async def with_amt(m: types.Message, state: FSMContext):
    try:
        amt = float(m.text.replace(",","."))
        u = await get_u(m.from_user.id)
        if amt < 4.5: return await m.answer("❌ Мінімальна сума 4.5 💎")
        if u['balance'] < amt: return await m.answer("❌ Недостатньо балансу!")
        await state.update_data(w_amt=amt)
        await m.answer("Введіть реквізити (ІПН IBAN ПІБ):", reply_markup=cancel_kb())
        await state.set_state(FinanceStates.wait_withdraw_details)
    except: await m.answer("Введіть число!")

@dp.message(FinanceStates.wait_withdraw_details)
async def with_final(m: types.Message, state: FSMContext):
    data = await state.get_data()
    await users_col.update_one({"_id": m.from_user.id}, {"$inc": {"balance": -data['w_amt']}})
    await bot.send_message(ADMIN_ID, f"📤 ЗАЯВКА НА ВИВІД\nЮзер: `{m.from_user.id}`\nСума: {data['w_amt']}\nДані: {m.text}")
    await m.answer("✅ Заявка на вивід прийнята.", reply_markup=main_kb()); await state.clear()

# --- МЕНЮ ТА АДМІНКА ---
@dp.message(F.text == "🤝 Рефералка")
async def ref_cmd(m: types.Message, state: FSMContext):
    await state.clear()
    u = await get_u(m.from_user.id); me = await bot.get_me()
    await m.answer(f"🤝 **Реферальна система**\n\nЗапрошуйте друзів та отримуйте 1% від їх виграшів!\n\nЗапрошено: `{u.get('referals_count',0)}`👤\n\nЛінк: `https://t.me/{me.username}?start={m.from_user.id}`", parse_mode="Markdown")

@dp.message(F.text == "💎 Баланс")
async def bal_cmd(m: types.Message, state: FSMContext):
    await state.clear()
    u = await get_u(m.from_user.id)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="📥 Поповнити", callback_data="dep"), InlineKeyboardButton(text="📤 Вивести", callback_data="with")]])
    await m.answer(f"💰 Ваш баланс: `{u['balance']:.2f}` 💎", reply_markup=kb, parse_mode="Markdown")

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
    l_turn = "\n".join([f"{i+1}. `{u['_id']}` — {u.get('turnover',0):.2f}" for i, u in enumerate(t_turn)])
    t_refs = await users_col.find().sort("referals_count", -1).limit(10).to_list(10)
    l_refs = "\n".join([f"{i+1}. `{u['_id']}` — {u.get('referals_count',0)}👤" for i, u in enumerate(t_refs)])
    await cb.message.answer(f"📊 **Адмін Статистика**\nДохід: `{profit:.4f}`\n\n🏆 **Топ 10 Оборот:**\n{l_turn}\n\n👥 **Топ 10 Реферали:**\n{l_refs}", parse_mode="Markdown")

@dp.message(Command("start"))
async def start_handler(m: types.Message, state: FSMContext):
    await state.clear()
    args = m.text.split()
    ref_id = int(args[1]) if len(args) > 1 and args[1].isdigit() else None
    await get_u(m.from_user.id, ref_id)
    if len(args) > 1 and args[1].startswith("game_"): 
        await join_game_logic(m, args[1].replace("game_", ""))
    else:
        await m.answer("💎 Вітаємо у Different Games!", reply_markup=main_kb())

async def main(): await dp.start_polling(bot)
if __name__ == "__main__": asyncio.run(main())
