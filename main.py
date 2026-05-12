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
finance_col = db["finance"]

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

# --- БАЗОВІ ФУНКЦІЇ ---
async def get_u(uid, ref_id=None):
    u = await users_col.find_one({"_id": uid})
    if not u:
        u = {"_id": uid, "balance": 0.0, "referals_count": 0, "turnover": 0.0, "games_played": 0, "referrer": ref_id, "is_active_ref": False}
        await users_col.insert_one(u)
        if ref_id:
            await users_col.update_one({"_id": ref_id}, {"$inc": {"referals_count": 1}})
    return u

# --- ЛОГІКА ІГОР ---
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
    await m.answer(f"Обрано {g_type}. Скільки раундів?", reply_markup=rounds_kb())
    await state.set_state(GameStates.wait_rounds)

@dp.message(GameStates.wait_rounds, F.text.in_(["1 раунд", "5 раундів"]))
async def choose_bet(m: types.Message, state: FSMContext):
    r = 1 if "1" in m.text else 5
    await state.update_data(rounds=r)
    await m.answer(f"Введіть суму ставки (💎):", reply_markup=cancel_kb())
    await state.set_state(GameStates.wait_bet)

@dp.message(GameStates.wait_bet)
async def create_game(m: types.Message, state: FSMContext):
    try:
        bet = float(m.text.replace(",", "."))
        u = await get_u(m.from_user.id)
        if u['balance'] < bet: return await m.answer("❌ Недостатньо балансу!")
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

@dp.message(F.dice)
async def handle_dice(m: types.Message):
    g = await games_col.find_one({"status": "playing", "turn": m.from_user.id})
    if not g or m.dice.emoji != g['type']: return

    is_c = (m.from_user.id == g['creator_id'])
    opp_id = g['opponent_id'] if is_c else g['creator_id']
    field = "c_score" if is_c else "o_score"
    
    await games_col.update_one({"game_id": g['game_id']}, {"$push": {field: m.dice.value}})
    
    # ПЕРЕСИЛАННЯ (FORWARD) ДЛЯ ЕФЕКТУ КІМНАТИ
    await m.forward(chat_id=opp_id)
    await bot.send_message(
        opp_id, 
        f"👀 У суперника ({m.from_user.first_name}): **{m.dice.value}**\n\n"
        f"🔔 **ТВІЙ ХІД!**\nКидай {g['type']} сюди!", 
        parse_mode="Markdown"
    )

    upd = await games_col.find_one({"game_id": g['game_id']})
    if len(upd['c_score']) == upd['rounds_total'] and len(upd['o_score']) == upd['rounds_total']:
        await finish_game(upd)
    else:
        next_t = upd['creator_id'] if len(upd['c_score']) == len(upd['o_score']) else upd['opponent_id']
        await games_col.update_one({"game_id": g['game_id']}, {"$set": {"turn": next_t}})

async def finish_game(g):
    s1, s2 = sum(g['c_score']), sum(g['o_score'])
    win_sum = g['bet'] * 1.98
    ref_bonus = g['bet'] * 0.01

    if s1 == s2:
        await users_col.update_many({"_id": {"$in": [g['creator_id'], g['opponent_id']]}}, {"$inc": {"balance": g['bet']}})
        for uid in [g['creator_id'], g['opponent_id']]: await bot.send_message(uid, "🤝 Нічия! Ставки повернуті.")
    else:
        win_id = g['creator_id'] if s1 > s2 else g['opponent_id']
        lose_id = g['opponent_id'] if s1 > s2 else g['creator_id']
        await users_col.update_one({"_id": win_id}, {"$inc": {"balance": win_sum, "turnover": g['bet']}})
        
        u_win = await users_col.find_one({"_id": win_id})
        if u_win.get("referrer"):
            ref_id = u_win["referrer"]
            await users_col.update_one({"_id": ref_id}, {"$inc": {"balance": ref_bonus}})
            try: await bot.send_message(ref_id, f"💲 **Ваш реферал виграв!** ❗️\nВи отримали: `+{ref_bonus:.2f} 💎` (1%)", parse_mode="Markdown")
            except: pass

        await bot.send_message(win_id, f"🏆 Ви перемогли! Виграш: `+{win_sum:.2f} 💎`", parse_mode="Markdown")
        await bot.send_message(lose_id, f"❌ Ви програли.")
    await games_col.update_one({"game_id": g['game_id']}, {"$set": {"status": "finished"}})

# --- ПРОФІЛЬ ТА БАЛАНС ---
@dp.message(F.text == "👤 Профіль")
async def profile_cmd(m: types.Message):
    u = await get_u(m.from_user.id)
    text = (f"👤 **Ваш Профіль**\n\n🆔 ID: `{m.from_user.id}`\n💰 Баланс: `{u['balance']:.2f} 💎`\n"
            f"📈 Оборот: `{u.get('turnover', 0.0):.2f} 💎`\n👥 Всього запрошено: `{u.get('referals_count', 0)}` осіб\n"
            f"🎳 Ігор зіграно: `{u['games_played']}`")
    await m.answer(text, parse_mode="Markdown")

@dp.message(F.text == "💎 Баланс")
async def balance_cmd(m: types.Message):
    u = await get_u(m.from_user.id)
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="📥 Поповнити", callback_data="dep"),
        InlineKeyboardButton(text="📤 Вивести", callback_data="with")
    ]])
    await m.answer(f"💰 Баланс: `{u['balance']:.2f}` 💎", reply_markup=kb, parse_mode="Markdown")

@dp.message(F.text == "🤝 Рефералка")
async def ref_cmd(m: types.Message):
    u = await get_u(m.from_user.id); me = await bot.get_me()
    text = (f"🤝 **Реферальна система**\n\nОтримуйте 1% від кожного виграшу друга!\n"
            f"👤 Запрошено: `{u.get('referals_count', 0)}` осіб\n\n🔗 Посилання:\n`https://t.me/{me.username}?start={m.from_user.id}`")
    await m.answer(text, parse_mode="Markdown")

# --- ПОПОВНЕННЯ (КВИТАНЦІЯ АДМІНУ) ---
@dp.callback_query(F.data == "dep")
async def dep_start(cb: types.CallbackQuery, state: FSMContext):
    await state.set_state(FinanceStates.wait_dep_amount)
    await cb.message.answer("💎 Введіть суму поповнення (💎):", reply_markup=cancel_kb())

@dp.message(FinanceStates.wait_dep_amount)
async def dep_amt(m: types.Message, state: FSMContext):
    try:
        amt = float(m.text.replace(",", "."))
        total_uah = (amt * CURRATE) * 1.05
        await state.update_data(amt=amt)
        await m.answer(f"💳 **Оплата поповнення**\n\nСума: `{amt:.2f} 💎`\nДо оплати: `{total_uah:.2f} ГРН`\n\nКарта Mono: `5355 2800 2890 2177`\n\n**Надішліть фото квитанції:**", parse_mode="Markdown")
        await state.set_state(FinanceStates.wait_receipt)
    except: await m.answer("Введіть число!")

@dp.message(FinanceStates.wait_receipt, F.photo | F.document)
async def dep_receipt(m: types.Message, state: FSMContext):
    data = await state.get_data(); t_id = str(uuid.uuid4())[:12]
    await finance_col.insert_one({"_id": t_id, "user_id": m.from_user.id, "amount": data['amt'], "status": "pending"})
    
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Схвалити", callback_data=f"dep_ok_{t_id}"),
        InlineKeyboardButton(text="❌ Відхилити", callback_data=f"dep_no_{t_id}")
    ]])
    caption = f"🔔 **Чек на {data['amt']} 💎**\n👤 Від: `{m.from_user.id}`"
    
    if m.photo:
        await bot.send_photo(ADMIN_ID, m.photo[-1].file_id, caption=caption, reply_markup=kb, parse_mode="Markdown")
    else:
        await bot.send_document(ADMIN_ID, m.document.file_id, caption=caption, reply_markup=kb, parse_mode="Markdown")
    
    await m.answer("✅ Квитанція прийнята! Очікуйте зарахування.", reply_markup=main_kb()); await state.clear()

@dp.callback_query(F.data.startswith("dep_"))
async def adm_dep(cb: types.CallbackQuery):
    _, act, t_id = cb.data.split("_")
    trans = await finance_col.find_one({"_id": t_id})
    if not trans or trans['status'] != "pending": return await cb.answer("Вже оброблено!")
    
    if act == "ok":
        await finance_col.update_one({"_id": t_id}, {"$set": {"status": "completed"}})
        await users_col.update_one({"_id": trans['user_id']}, {"$inc": {"balance": trans['amount']}})
        await bot.send_message(trans['user_id'], f"✅ Зараховано {trans['amount']} 💎!")
        await cb.message.edit_caption(caption=cb.message.caption + "\n\n✅ СХВАЛЕНО")
    else:
        await finance_col.update_one({"_id": t_id}, {"$set": {"status": "rejected"}})
        await bot.send_message(trans['user_id'], "❌ Квитанція відхилена.")
        await cb.message.edit_caption(caption=cb.message.caption + "\n\n❌ ВІДХИЛЕНО")

# --- ВИВЕДЕННЯ ТА ІНШЕ ---
@dp.callback_query(F.data == "with")
async def with_start(cb: types.CallbackQuery, state: FSMContext):
    await state.set_state(FinanceStates.wait_withdraw_amount)
    await cb.message.answer("📤 Введіть суму виводу (мін 4.0):", reply_markup=cancel_kb())

@dp.message(FinanceStates.wait_withdraw_amount)
async def with_amt(m: types.Message, state: FSMContext):
    try:
        amt = float(m.text.replace(",", "."))
        u = await get_u(m.from_user.id)
        if amt < 4.0 or u['balance'] < amt: return await m.answer("❌ Недостатньо коштів")
        await state.update_data(w_amt=amt)
        await m.answer("📝 Введіть дані для виплати (IBAN, ІПН, ПІБ):", reply_markup=cancel_kb())
        await state.set_state(FinanceStates.wait_withdraw_details)
    except: await m.answer("Число!")

@dp.message(FinanceStates.wait_withdraw_details)
async def with_final(m: types.Message, state: FSMContext):
    data = await state.get_data(); t_id = str(uuid.uuid4())[:12]
    await users_col.update_one({"_id": m.from_user.id}, {"$inc": {"balance": -data['w_amt']}})
    await finance_col.insert_one({"_id": t_id, "user_id": m.from_user.id, "amount": data['w_amt'], "details": m.text, "status": "w_pending"})
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Виконано", callback_data=f"with_ok_{t_id}"),
        InlineKeyboardButton(text="❌ Відхилити", callback_data=f"with_no_{t_id}")
    ]])
    await bot.send_message(ADMIN_ID, f"📤 **ВИВІД**\n💰 `{data['w_amt']} 💎`\n👤 `{m.from_user.id}`\n📄 `{m.text}`", reply_markup=kb, parse_mode="Markdown")
    await m.answer("✅ Заявку на вивід прийнято!", reply_markup=main_kb()); await state.clear()

@dp.callback_query(F.data.startswith("with_"))
async def adm_with(cb: types.CallbackQuery):
    _, act, t_id = cb.data.split("_")
    trans = await finance_col.find_one({"_id": t_id})
    if not trans or trans['status'] != "w_pending": return await cb.answer("Вже оброблено!")
    if act == "ok":
        await finance_col.update_one({"_id": t_id}, {"$set": {"status": "w_completed"}})
        await bot.send_message(trans['user_id'], "✅ Ваші кошти виплачено!"); await cb.message.edit_text("✅ ГОТОВО")
    else:
        await finance_col.update_one({"_id": t_id}, {"$set": {"status": "w_rejected"}})
        await users_col.update_one({"_id": trans['user_id']}, {"$inc": {"balance": trans['amount']}})
        await bot.send_message(trans['user_id'], "❌ Вивід відхилено. Повернення 💎"); await cb.message.edit_text("❌ ВІДХИЛЕНО")

@dp.message(Command("start"))
async def start_handler(m: types.Message, state: FSMContext):
    await state.clear(); args = m.text.split()
    ref_id = int(args[1]) if len(args) > 1 and args[1].isdigit() else None
    await get_u(m.from_user.id, ref_id)
    if len(args) > 1 and args[1].startswith("game_"):
        gid = args[1].replace("game_", "")
        g = await games_col.find_one({"game_id": gid, "status": "waiting"})
        if g and g['creator_id'] != m.from_user.id:
            u2 = await get_u(m.from_user.id)
            if u2['balance'] >= g['bet']:
                await users_col.update_many({"_id": {"$in": [g['creator_id'], m.from_user.id]}}, {"$inc": {"balance": -g['bet'], "games_played": 1}})
                await games_col.update_one({"game_id": gid}, {"$set": {"opponent_id": m.from_user.id, "status": "playing", "turn": g['creator_id']}})
                await bot.send_message(g['creator_id'], f"🎮 Гра почалася!\n🎲 **Ваш хід!**\nКидай {g['type']}!")
                await m.answer(f"🎮 Приєдналися! Очікуйте хід суперника...")
    await m.answer("💎 Ласкаво просимо!", reply_markup=main_kb())

@dp.message(F.text == "⚙️ Налаштування")
async def sett_cmd(m: types.Message):
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🆘 Підтримка", url="https://t.me/vex0o0")]])
    if m.from_user.id == ADMIN_ID: kb.inline_keyboard.append([InlineKeyboardButton(text="👨‍💻 Адмін Панель", callback_data="admin_panel")])
    await m.answer("Налаштування:", reply_markup=kb)

@dp.callback_query(F.data == "admin_panel")
async def admin_panel(cb: types.CallbackQuery):
    if cb.from_user.id != ADMIN_ID: return
    stats = await stats_col.find_one({"_id": "global"})
    profit = stats.get("admin_profit", 0) if stats else 0
    await cb.message.answer(f"📊 **Адмін статистика**\n\nПрибуток: `{profit:.4f} 💎`", parse_mode="Markdown")

async def main(): await dp.start_polling(bot)
if __name__ == "__main__": asyncio.run(main())
