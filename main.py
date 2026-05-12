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

# --- START & JOIN GAME (КРИТИЧНО: ПЕРЕВІРКА ПОСИЛАННЯ) ---
@dp.message(Command("start"))
async def cmd_start(m: types.Message, state: FSMContext):
    await state.clear()
    args = m.text.split()
    
    if len(args) > 1:
        param = args[1]
        
        # ЛОГІКА ВХОДУ В ГРУ
        if param.startswith("game_"):
            gid = param.replace("game_", "")
            g = await games_col.find_one({"game_id": gid, "status": "waiting"})
            
            if not g:
                return await m.answer("❌ Гра не знайдена або вже завершена.")
            
            if g['creator_id'] == m.from_user.id:
                return await m.answer("❌ Ви не можете грати проти самого себе.")
            
            u = await get_u(m.from_user.id)
            if u['balance'] < g['bet']:
                return await m.answer(f"❌ Недостатньо балансу! Ставка: `{g['bet']} 💎`", parse_mode="Markdown")
            
            # Списання та старт
            await users_col.update_many({"_id": {"$in": [g['creator_id'], m.from_user.id]}}, {"$inc": {"balance": -g['bet']}})
            await games_col.update_one({"game_id": gid}, {"$set": {"opponent_id": m.from_user.id, "status": "playing", "turn": g['creator_id']}})
            
            await bot.send_message(g['creator_id'], f"🎮 Суперник приєднався! Твій хід — кидай `{g['type']}`")
            return await m.answer(f"🎮 Ви приєдналися до гри! Першим ходить опонент.", reply_markup=main_kb())
            
        # ЛОГІКА РЕФЕРАЛКИ
        elif param.isdigit():
            await get_u(m.from_user.id, int(param))
            return await m.answer("💎 Ви зареєстровані за реферальним посиланням!", reply_markup=main_kb())

    await get_u(m.from_user.id)
    await m.answer("💎 Ласкаво просимо!", reply_markup=main_kb())

# --- ПРОФІЛЬ ТА РЕФЕРАЛКА ---
@dp.message(F.text == "👤 Профіль")
async def profile_cmd(m: types.Message):
    u = await get_u(m.from_user.id)
    await m.answer(f"👤 **Профіль**\n\n🆔 ID: `{m.from_user.id}`\n💰 Баланс: `{u['balance']:.2f} 💎`\n📈 Оборот: `{u.get('turnover', 0.0):.2f} 💎`\n🎮 Ігор: `{u.get('games_played', 0)}` 🕹\n👥 Рефералів: {u.get('referals_count', 0)}", parse_mode="Markdown")

@dp.message(F.text == "🤝 Рефералка")
async def referal_cmd(m: types.Message):
    u = await get_u(m.from_user.id)
    me = await bot.get_me()
    link = f"https://t.me/{me.username}?start={m.from_user.id}"
    await m.answer(f"🤝 **Реферальна система**\n\n🔗 Посилання:\n`{link}`\n\n👥 Запрошено: `{u.get('referals_count', 0)}` осіб\n🎁 Вам нараховується 1% від кожного виграшу друга!", parse_mode="Markdown")

# --- ФІНАНСИ (ПОПОВНЕННЯ +5%) ---
@dp.message(F.text == "💎 Баланс")
async def balance_cmd(m: types.Message):
    u = await get_u(m.from_user.id)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="📥 Поповнити", callback_data="dep"), InlineKeyboardButton(text="📤 Вивести", callback_data="with")]])
    await m.answer(f"💰 Ваш баланс: `{u['balance']:.2f} 💎`", reply_markup=kb, parse_mode="Markdown")

@dp.callback_query(F.data == "dep")
async def dep_start(cb: types.CallbackQuery, state: FSMContext):
    await state.set_state(FinanceStates.wait_dep_amount)
    await cb.message.answer("💎 Введіть суму в 💎 (мін. 0.50):", reply_markup=cancel_kb())

@dp.message(FinanceStates.wait_dep_amount)
async def dep_amt(m: types.Message, state: FSMContext):
    if m.text == "❌ Скасувати": return
    try:
        amt = float(m.text.replace(",", "."))
        if amt < 0.5: return await m.answer("❌ Мінімальна сума — 0.50 💎")
        await state.update_data(amt=amt)
        total_pay = (amt * CURRATE) * 1.05
        await m.answer(f"Курс: `{CURRATE}`\nДо оплати: `{total_pay:.2f} ГРН` (+5%)\n\nРеквізити: `5355 2800 2890 2177`\n\n**Обовʼязково сюди кидайте квитанцію для підтвердження!**", parse_mode="Markdown")
        await state.set_state(FinanceStates.wait_receipt)
    except: await m.answer("Введіть число.")

@dp.message(FinanceStates.wait_receipt, F.photo)
async def dep_receipt(m: types.Message, state: FSMContext):
    d = await state.get_data()
    tid = str(uuid.uuid4())[:8]
    await finance_col.insert_one({"_id": tid, "uid": m.from_user.id, "amt": d['amt']})
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✅ ✅", callback_data=f"f_ok_{tid}"), InlineKeyboardButton(text="❌ ❌", callback_data=f"f_no_{tid}")]])
    await bot.send_photo(ADMIN_ID, m.photo[-1].file_id, caption=f"📥 Поповнення {d['amt']} від `{m.from_user.id}`", reply_markup=kb)
    await m.answer("✅ Чек надіслано на перевірку!", reply_markup=main_kb())
    await state.clear()

# --- ІГРИ ТА ЛОГІКА (1% АДМІНУ ТА РЕФУ) ---
@dp.message(F.text == "🎮 Games")
async def games_menu(m: types.Message):
    kb = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="🎳 Боулінг"), KeyboardButton(text="🎲 Кубик")], [KeyboardButton(text="❌ Скасувати")]], resize_keyboard=True)
    await m.answer("Оберіть режим:", reply_markup=kb)

@dp.message(F.text.in_(["🎳 Боулінг", "🎲 Кубик"]))
async def g_rounds(m: types.Message, state: FSMContext):
    await state.update_data(type="🎳" if "Боу" in m.text else "🎲")
    kb = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="1 раунд"), KeyboardButton(text="5 раундів")], [KeyboardButton(text="❌ Скасувати")]], resize_keyboard=True)
    await m.answer("Кількість раундів:", reply_markup=kb)
    await state.set_state(GameStates.wait_rounds)

@dp.message(GameStates.wait_rounds, F.text.in_(["1 раунд", "5 раундів"]))
async def g_bet(m: types.Message, state: FSMContext):
    await state.update_data(r=1 if "1" in m.text else 5)
    await m.answer("Ваша ставка (💎):", reply_markup=cancel_kb())
    await state.set_state(GameStates.wait_bet)

@dp.message(GameStates.wait_bet)
async def g_create(m: types.Message, state: FSMContext):
    if m.text == "❌ Скасувати": return
    try:
        bet = float(m.text.replace(",", "."))
        u = await get_u(m.from_user.id)
        if u['balance'] < bet: return await m.answer("❌ Недостатньо балансу!")
        d = await state.get_data()
        gid = str(uuid.uuid4())[:8]
        await games_col.insert_one({"game_id": gid, "creator_id": m.from_user.id, "opponent_id": None, "type": d['type'], "bet": bet, "rounds_total": d['r'], "status": "waiting", "c_score": [], "o_score": []})
        bot_u = await bot.get_me()
        await m.answer(f"✅ Гру створено!\n🔗 Посилання:\n`https://t.me/{bot_u.username}?start=game_{gid}`", reply_markup=main_kb())
        await state.clear()
    except: await m.answer("Введіть число.")

@dp.message(F.dice)
async def dice_handler(m: types.Message):
    g = await games_col.find_one({"status": "playing", "turn": m.from_user.id})
    if not g or m.dice.emoji != g['type']: return
    is_c = (m.from_user.id == g['creator_id'])
    opp = g['opponent_id'] if is_c else g['creator_id']
    f = "c_score" if is_c else "o_score"
    await games_col.update_one({"game_id": g['game_id']}, {"$push": {f: m.dice.value}})
    await m.forward(opp)
    upd = await games_col.find_one({"game_id": g['game_id']})
    if len(upd['c_score']) == upd['rounds_total'] and len(upd['o_score']) == upd['rounds_total']:
        await finish_game(upd)
    else:
        await games_col.update_one({"game_id": g['game_id']}, {"$set": {"turn": opp}})
        await bot.send_message(opp, f"Твій хід! Кидай `{g['type']}`")

async def finish_game(g):
    s1, s2 = sum(g['c_score']), sum(g['o_score'])
    await add_admin_profit(g['bet'] * 0.01)
    if s1 == s2:
        await users_col.update_many({"_id": {"$in": [g['creator_id'], g['opponent_id']]}}, {"$inc": {"balance": g['bet']}})
        for uid in [g['creator_id'], g['opponent_id']]: await bot.send_message(uid, "🤝 Нічия!")
    else:
        win_id = g['creator_id'] if s1 > s2 else g['opponent_id']
        lose_id = g['opponent_id'] if s1 > s2 else g['creator_id']
        await users_col.update_one({"_id": win_id}, {"$inc": {"balance": g['bet']*1.98, "turnover": g['bet'], "games_played": 1}})
        await users_col.update_one({"_id": lose_id}, {"$inc": {"games_played": 1}})
        u_win = await users_col.find_one({"_id": win_id})
        if u_win.get("referrer"):
            await users_col.update_one({"_id": u_win['referrer']}, {"$inc": {"balance": g['bet']*0.01}})
            try: await bot.send_message(u_win['referrer'], f"💰 Реферал виграв! Вам +{g['bet']*0.01:.2f} 💎")
            except: pass
        await bot.send_message(win_id, f"🏆 Виграш: `{g['bet']*1.98:.2f} 💎`", parse_mode="Markdown")
        await bot.send_message(lose_id, "❌ Ви програли.")
    await games_col.update_one({"game_id": g['game_id']}, {"$set": {"status": "finished"}})

# --- АДМІНКА (ТІЛЬКИ ДЛЯ АДМІНА) ---
@dp.callback_query(F.data == "admin_panel")
async def adm_view(cb: types.CallbackQuery):
    if cb.from_user.id != ADMIN_ID: return
    t_t = await users_col.find().sort("turnover", -1).limit(10).to_list(10)
    t_r = await users_col.find().sort("referals_count", -1).limit(10).to_list(10)
    st = await admin_col.find_one({"_id": "stats"})
    p = st.get("total_profit", 0) if st else 0
    txt = f"📊 **АДМІН ПАНЕЛЬ**\n💰 Каса: `{p:.2f} 💎`\n\n🏆 **ТОП-10 Оборот:**\n"
    for i, u in enumerate(t_t, 1): txt += f"{i}. `{u['_id']}` - {u.get('turnover',0):.1f}\n"
    txt += "\n👥 **ТОП-10 Реферали:**\n"
    for i, u in enumerate(t_r, 1): txt += f"{i}. `{u['_id']}` - {u.get('referals_count',0)}\n"
    await cb.message.answer(txt, parse_mode="Markdown")

@dp.message(F.text == "⚙️ Налаштування")
async def settings(m: types.Message):
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🆘 Підтримка", url="https://t.me/vex0o0")]])
    if m.from_user.id == ADMIN_ID:
        kb.inline_keyboard.append([InlineKeyboardButton(text="👨‍💻 Адмін Панель", callback_data="admin_panel")])
    await m.answer("Налаштування:", reply_markup=kb)

# --- ПІДТВЕРДЖЕННЯ ЧЕКІВ ---
@dp.callback_query(F.data.startswith("f_ok_"))
async def f_ok(cb: types.CallbackQuery):
    tid = cb.data[5:]; f = await finance_col.find_one({"_id": tid})
    if f:
        await users_col.update_one({"_id": f['uid']}, {"$inc": {"balance": f['amt']}})
        await bot.send_message(f['uid'], f"✅ Поповнено на `{f['amt']} 💎`!")
        await cb.message.delete(); await finance_col.delete_one({"_id": tid})

@dp.callback_query(F.data.startswith("f_no_"))
async def f_no(cb: types.CallbackQuery):
    tid = cb.data[5:]; f = await finance_col.find_one({"_id": tid})
    if f:
        await bot.send_message(f['uid'], "❌ Чек відхилено."); await cb.message.delete()
        await finance_col.delete_one({"_id": tid})

@dp.message(F.text == "❌ Скасувати")
async def global_cancel(m: types.Message, state: FSMContext):
    await state.clear(); await m.answer("Скасовано.", reply_markup=main_kb())

async def main(): await dp.start_polling(bot)
if __name__ == "__main__": asyncio.run(main())
