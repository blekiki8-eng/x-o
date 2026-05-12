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
finance_col = db["finance"]
admin_col = db["admin_data"]

# --- СТАНИ ---
class GameStates(StatesGroup):
    wait_rounds = State()
    wait_bet = State()

class FinanceStates(StatesGroup):
    wait_dep_amount = State()
    wait_receipt = State()
    wait_with_amount = State()
    wait_with_details = State()

# --- КЛАВІАТУРИ ---
def main_kb():
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="👤 Профіль"), KeyboardButton(text="🎮 Games")],
        [KeyboardButton(text="💎 Баланс"), KeyboardButton(text="🤝 Рефералка")],
        [KeyboardButton(text="⚙️ Налаштування")]
    ], resize_keyboard=True)

def cancel_kb():
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="❌ Скасувати")]], resize_keyboard=True)

# --- ДОПОМІЖНІ ФУНКЦІЇ ---
async def get_u(uid, ref_id=None):
    u = await users_col.find_one({"_id": uid})
    if not u:
        u = {"_id": uid, "balance": 0.0, "referals_count": 0, "turnover": 0.0, "games_played": 0, "referrer": ref_id}
        await users_col.insert_one(u)
        if ref_id and ref_id != uid:
            await users_col.update_one({"_id": ref_id}, {"$inc": {"referals_count": 1}})
    return u

async def add_admin_profit(amt):
    await admin_col.update_one({"_id": "stats"}, {"$inc": {"total_profit": amt}}, upsert=True)

# --- ОБРОБКА КОМАНД ---
@dp.message(F.text == "❌ Скасувати")
async def cancel_action(m: types.Message, state: FSMContext):
    await state.clear()
    await m.answer("Дію скасовано.", reply_markup=main_kb())

@dp.message(F.text == "👤 Профіль")
async def profile_cmd(m: types.Message):
    u = await get_u(m.from_user.id)
    await m.answer(f"👤 **Профіль**\n\n🆔 ID: `{m.from_user.id}`\n💰 Баланс: `{u['balance']:.2f} 💎`\n📈 Оборот: `{u.get('turnover', 0.0):.2f} 💎`\n🎮 Ігор: `{u.get('games_played', 0)}` 🕹\n👥 Рефералів: {u.get('referals_count', 0)}", parse_mode="Markdown")

@dp.message(F.text == "🤝 Рефералка")
async def referal_cmd(m: types.Message):
    u = await get_u(m.from_user.id)
    bot_info = await bot.get_me()
    link = f"https://t.me/{bot_info.username}?start={m.from_user.id}"
    await m.answer(f"🤝 **Реферальна система**\n\n🔗 Твоє посилання:\n`{link}`\n\n👥 Запрошено друзів: `{u.get('referals_count', 0)}` осіб\n🎁 Ти отримуєш 1% від кожного ВИГРАШУ твого реферала!", parse_mode="Markdown")

# --- ФІНАНСИ ---
@dp.message(F.text == "💎 Баланс")
async def balance_cmd(m: types.Message):
    u = await get_u(m.from_user.id)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="📥 Поповнити", callback_data="dep"), InlineKeyboardButton(text="📤 Вивести", callback_data="with")]])
    await m.answer(f"💰 Ваш баланс: `{u['balance']:.2f} 💎`", reply_markup=kb, parse_mode="Markdown")

@dp.callback_query(F.data == "dep")
async def dep_start(cb: types.CallbackQuery, state: FSMContext):
    await state.set_state(FinanceStates.wait_dep_amount)
    await cb.message.answer("💎 Введіть суму поповнення (мін. 0.50 💎):", reply_markup=cancel_kb())

@dp.message(FinanceStates.wait_dep_amount)
async def dep_amt(m: types.Message, state: FSMContext):
    try:
        amt = float(m.text.replace(",", "."))
        if amt < 0.5: return await m.answer("❌ Мінімальна сума поповнення — 0.50 💎")
        await state.update_data(amt=amt)
        total_pay = amt * CURRATE
        await m.answer(f"Курс: `{CURRATE}`\nДо оплати: `{total_pay:.2f} ГРН`\n\nРеквізити: `5355 2800 2890 2177`\n\n**Обовʼязково сюди кидайте квитанцію для підтвердження!**", parse_mode="Markdown")
        await state.set_state(FinanceStates.wait_receipt)
    except: await m.answer("Будь ласка, введіть число.")

@dp.message(FinanceStates.wait_receipt, F.photo)
async def dep_receipt(m: types.Message, state: FSMContext):
    d = await state.get_data()
    tid = str(uuid.uuid4())[:8]
    await finance_col.insert_one({"_id": tid, "uid": m.from_user.id, "amt": d['amt'], "type": "dep"})
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✅ Схвалити", callback_data=f"f_ok_{tid}"), InlineKeyboardButton(text="❌ Відхилити", callback_data=f"f_no_{tid}")]])
    await bot.send_photo(ADMIN_ID, m.photo[-1].file_id, caption=f"📥 Депозит `{d['amt']} 💎` від `{m.from_user.id}`", reply_markup=kb)
    await m.answer("✅ Квитанцію надіслано! Очікуйте підтвердження.", reply_markup=main_kb())
    await state.clear()

@dp.callback_query(F.data == "with")
async def with_start(cb: types.CallbackQuery, state: FSMContext):
    u = await get_u(cb.from_user.id)
    if u['balance'] < 4: return await cb.answer("❌ Мінімальний вивід — 4 💎", show_alert=True)
    await state.set_state(FinanceStates.wait_with_amount)
    await cb.message.answer("📤 Введіть суму виводу (мін. 4 💎):", reply_markup=cancel_kb())

@dp.message(FinanceStates.wait_with_amount)
async def with_amt(m: types.Message, state: FSMContext):
    try:
        amt = float(m.text.replace(",", "."))
        u = await get_u(m.from_user.id)
        if amt < 4 or amt > u['balance']: return await m.answer("❌ Недостатньо коштів або сума менша 4 💎")
        await state.update_data(amt=amt)
        await m.answer("💳 Введіть номер вашої карти:", reply_markup=cancel_kb())
        await state.set_state(FinanceStates.wait_with_details)
    except: await m.answer("Введіть число.")

@dp.message(FinanceStates.wait_with_details)
async def with_final(m: types.Message, state: FSMContext):
    d = await state.get_data()
    await users_col.update_one({"_id": m.from_user.id}, {"$inc": {"balance": -d['amt']}})
    await bot.send_message(ADMIN_ID, f"📤 ЗАЯВКА НА ВИВІД\nЮзер: `{m.from_user.id}`\nСума: `{d['amt']} 💎`\nКарта: `{m.text}`")
    await m.answer("✅ Заявка на вивід прийнята!", reply_markup=main_kb())
    await state.clear()

# --- ІГРИ (БОУЛІНГ / КУБИК) ---
@dp.message(F.text == "🎮 Games")
async def games_menu(m: types.Message):
    kb = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="Боулінг 🎳"), KeyboardButton(text="Кубик 🎲")], [KeyboardButton(text="❌ Скасувати")]], resize_keyboard=True)
    await m.answer("Оберіть гру:", reply_markup=kb)

@dp.message(F.text.in_(["Боулінг 🎳", "Кубик 🎲"]))
async def g_rounds(m: types.Message, state: FSMContext):
    await state.update_data(type="🎳" if "Боу" in m.text else "🎲")
    kb = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="1 раунд"), KeyboardButton(text="5 раундів")], [KeyboardButton(text="❌ Скасувати")]], resize_keyboard=True)
    await m.answer("Скільки раундів граємо?", reply_markup=kb)
    await state.set_state(GameStates.wait_rounds)

@dp.message(GameStates.wait_rounds, F.text.in_(["1 раунд", "5 раундів"]))
async def g_bet(m: types.Message, state: FSMContext):
    await state.update_data(r=1 if "1" in m.text else 5)
    await m.answer("Введіть ставку (💎):", reply_markup=cancel_kb())
    await state.set_state(GameStates.wait_bet)

@dp.message(GameStates.wait_bet)
async def g_create(m: types.Message, state: FSMContext):
    try:
        bet = float(m.text.replace(",", "."))
        u = await get_u(m.from_user.id)
        if u['balance'] < bet: return await m.answer("❌ Недостатньо балансу!")
        d = await state.get_data()
        gid = str(uuid.uuid4())[:8]
        await games_col.insert_one({"game_id": gid, "creator_id": m.from_user.id, "opponent_id": None, "type": d['type'], "bet": bet, "rounds_total": d['r'], "status": "waiting", "c_score": [], "o_score": []})
        bot_me = await bot.get_me()
        await m.answer(f"✅ Гру створено!\n🔗 Посилання для друга:\n`https://t.me/{bot_me.username}?start=game_{gid}`", reply_markup=main_kb())
        await state.clear()
    except: await m.answer("Введіть число.")

@dp.message(F.dice)
async def dice_handler(m: types.Message):
    g = await games_col.find_one({"status": "playing", "turn": m.from_user.id})
    if not g or m.dice.emoji != g['type']: return
    is_c = (m.from_user.id == g['creator_id'])
    opp = g['opponent_id'] if is_c else g['creator_id']
    score_key = "c_score" if is_c else "o_score"
    
    await games_col.update_one({"game_id": g['game_id']}, {"$push": {score_key: m.dice.value}})
    await m.forward(opp)
    
    upd_g = await games_col.find_one({"game_id": g['game_id']})
    if len(upd_g['c_score']) == upd_g['rounds_total'] and len(upd_g['o_score']) == upd_g['rounds_total']:
        await finish_game(upd_g)
    else:
        await games_col.update_one({"game_id": g['game_id']}, {"$set": {"turn": opp}})
        await bot.send_message(opp, f"Твій хід! Кидай {g['type']}")

async def finish_game(g):
    s1, s2 = sum(g['c_score']), sum(g['o_score'])
    await add_admin_profit(g['bet'] * 0.01) # 1% адміну
    if s1 == s2:
        await users_col.update_many({"_id": {"$in": [g['creator_id'], g['opponent_id']]}}, {"$inc": {"balance": g['bet']}})
        for uid in [g['creator_id'], g['opponent_id']]: await bot.send_message(uid, "🤝 Нічия! Ставки повернуті.")
    else:
        win_id = g['creator_id'] if s1 > s2 else g['opponent_id']
        lose_id = g['opponent_id'] if s1 > s2 else g['creator_id']
        
        # Переможцю (ставка + 98% зверху)
        win_amt = g['bet'] * 1.98
        await users_col.update_one({"_id": win_id}, {"$inc": {"balance": win_amt, "turnover": g['bet'], "games_played": 1}})
        await users_col.update_one({"_id": lose_id}, {"$inc": {"games_played": 1}})
        
        # Реферальний 1% запрошувачу
        u_win = await users_col.find_one({"_id": win_id})
        if u_win.get("referrer"):
            ref_bonus = g['bet'] * 0.01
            await users_col.update_one({"_id": u_win['referrer']}, {"$inc": {"balance": ref_bonus}})
            try: await bot.send_message(u_win['referrer'], f"💰 Ваш реферал виграв! Вам бонус: `+{ref_bonus:.2f} 💎`", parse_mode="Markdown")
            except: pass

        await bot.send_message(win_id, f"🏆 Перемога! Нараховано: `{win_amt:.2f} 💎`", parse_mode="Markdown")
        await bot.send_message(lose_id, "❌ Ви програли.")
    await games_col.update_one({"game_id": g['game_id']}, {"$set": {"status": "finished"}})

# --- АДМІН-ПАНЕЛЬ ---
@dp.callback_query(F.data == "admin_panel")
async def admin_panel(cb: types.CallbackQuery):
    if cb.from_user.id != ADMIN_ID: return
    
    top_t = await users_col.find().sort("turnover", -1).limit(10).to_list(10)
    top_r = await users_col.find().sort("referals_count", -1).limit(10).to_list(10)
    adm_st = await admin_col.find_one({"_id": "stats"})
    profit = adm_st.get("total_profit", 0) if adm_st else 0

    text = f"📊 **АДМІН ПАНЕЛЬ**\n💰 Каса (1%): `{profit:.2f} 💎`\n\n🏆 **Топ-10 Оборот:**\n"
    for i, u in enumerate(top_t, 1): text += f"{i}. `{u['_id']}` — {u.get('turnover',0):.1f}\n"
    text += "\n👥 **Топ-10 Рефералів:**\n"
    for i, u in enumerate(top_r, 1): text += f"{i}. `{u['_id']}` — {u.get('referals_count',0)}\n"
    await cb.message.answer(text, parse_mode="Markdown")

@dp.message(F.text == "⚙️ Налаштування")
async def settings(m: types.Message):
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🆘 Підтримка", url="https://t.me/vex0o0")]])
    if m.from_user.id == ADMIN_ID:
        kb.inline_keyboard.append([InlineKeyboardButton(text="👨‍💻 Адмін Панель", callback_data="admin_panel")])
    await m.answer("Налаштування:", reply_markup=kb)

# --- START ---
@dp.message(Command("start"))
async def cmd_start(m: types.Message, state: FSMContext):
    await state.clear()
    args = m.text.split()
    rid = None
    if len(args) > 1:
        if args[1].startswith("game_"):
            gid = args[1][5:]
            g = await games_col.find_one({"game_id": gid, "status": "waiting"})
            if g and g['creator_id'] != m.from_user.id:
                u = await get_u(m.from_user.id)
                if u['balance'] < g['bet']: return await m.answer("❌ Поповніть баланс!")
                await users_col.update_many({"_id": {"$in": [g['creator_id'], m.from_user.id]}}, {"$inc": {"balance": -g['bet']}})
                await games_col.update_one({"game_id": gid}, {"$set": {"opponent_id": m.from_user.id, "status": "playing", "turn": g['creator_id']}})
                await bot.send_message(g['creator_id'], f"🎮 Суперник приєднався! Твій хід `{g['type']}`")
                return await m.answer(f"🎮 Ти приєднався! Першим ходить суперник.", reply_markup=main_kb())
        elif args[1].isdigit(): rid = int(args[1])
    await get_u(m.from_user.id, rid)
    await m.answer("💎 Вітаємо у боті!", reply_markup=main_kb())

# --- ОБРОБКА ПІДТВЕРДЖЕНЬ ДЕПОЗИТІВ ---
@dp.callback_query(F.data.startswith("f_ok_"))
async def f_approve(cb: types.CallbackQuery):
    tid = cb.data[5:]
    f = await finance_col.find_one({"_id": tid})
    if f:
        await users_col.update_one({"_id": f['uid']}, {"$inc": {"balance": f['amt']}})
        await bot.send_message(f['uid'], f"✅ Баланс поповнено на `{f['amt']} 💎`!", parse_mode="Markdown")
        await cb.message.edit_caption(caption=cb.message.caption + "\n\n✅ ПРИЙНЯТО")
        await finance_col.delete_one({"_id": tid})

@dp.callback_query(F.data.startswith("f_no_"))
async def f_decline(cb: types.CallbackQuery):
    tid = cb.data[5:]
    f = await finance_col.find_one({"_id": tid})
    if f:
        await bot.send_message(f['uid'], "❌ Ваша квитанція відхилена.")
        await cb.message.edit_caption(caption=cb.message.caption + "\n\n❌ ВІДХИЛЕНО")
        await finance_col.delete_one({"_id": tid})

async def main(): await dp.start_polling(bot)
if __name__ == "__main__": asyncio.run(main())
