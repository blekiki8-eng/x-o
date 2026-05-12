import os, uuid, logging, asyncio
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, CommandObject
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
finance_col = db["finance"]
stats_col = db["stats"]

# --- СТАНИ ---
class GameStates(StatesGroup):
    wait_rounds = State()
    wait_bet = State()

class FinanceStates(StatesGroup):
    wait_dep_amount = State()
    wait_receipt = State()
    wait_draw_sum = State()
    wait_draw_details = State()

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

# --- АДМІН КОМАНДИ ---
@dp.message(Command("give_prize"))
async def adm_give_prize(m: types.Message, command: CommandObject):
    if m.from_user.id != ADMIN_ID: return
    try:
        uid, amt = int(command.args.split()[0]), float(command.args.split()[1])
        await users_col.update_one({"_id": uid}, {"$inc": {"balance": amt}})
        await m.answer(f"🎁 Приз {amt} 💎 видано {uid}")
        await bot.send_message(uid, f"🎁 Ви отримали приз: +{amt} 💎")
    except: await m.answer("Формат: /give_prize [ID] [сума]")

@dp.message(Command("add_balance"))
async def adm_add_bal(m: types.Message, command: CommandObject):
    if m.from_user.id != ADMIN_ID: return
    try:
        uid, amt = int(command.args.split()[0]), float(command.args.split()[1])
        await users_col.update_one({"_id": uid}, {"$inc": {"balance": amt}})
        await m.answer(f"✅ Баланс {uid} поповнено на {amt}")
        await bot.send_message(uid, f"💳 Нараховано баланс: +{amt} 💎")
    except: await m.answer("Формат: /add_balance [ID] [сума]")

# --- ОБРОБНИК START ---
@dp.message(Command("start"))
async def cmd_start(m: types.Message, state: FSMContext):
    await state.clear()
    args = m.text.split()
    user_id = m.from_user.id
    
    ref_id = None
    if len(args) > 1 and args[1].isdigit():
        ref_id = int(args[1])
    u = await get_u(user_id, ref_id)
    
    if len(args) > 1 and args[1].startswith("game_"):
        # ПОВНЕ ОЧИЩЕННЯ ID ГРИ ВІД ЗАЙВИХ СИМВОЛІВ
        gid = args[1].replace("game_", "").replace("`", "").replace("'", "").strip()
        g = await games_col.find_one({"game_id": gid, "status": "waiting"})
        
        if not g: 
            return await m.answer("❌ Гра не знайдена. Можливо, вона вже почалася або посилання невірне.", reply_markup=main_kb())
        if g['creator_id'] == user_id: 
            return await m.answer("❌ Ви не можете грати самі з собою.", reply_markup=main_kb())
        if u['balance'] < g['bet']: 
            return await m.answer(f"❌ Недостатньо 💎. Потрібно: {g['bet']}", reply_markup=main_kb())
        
        await users_col.update_many({"_id": {"$in": [g['creator_id'], user_id]}}, {"$inc": {"balance": -g['bet']}})
        await games_col.update_one({"game_id": gid}, {"$set": {"opponent_id": user_id, "status": "playing", "turn": g['creator_id']}})
        
        await bot.send_message(g['creator_id'], f"🎮 Гравець приєднався! Твій хід {g['type']}")
        return await m.answer(f"🎮 Ви приєдналися! Очікуйте хід суперника {g['type']}", reply_markup=main_kb())

    await m.answer("💎 Вітаємо в Different Games!", reply_markup=main_kb())

# --- ПРОФІЛЬ ТА РЕФЕРАЛКА ---
@dp.message(F.text == "👤 Профіль")
async def profile_cmd(m: types.Message):
    u = await get_u(m.from_user.id)
    await m.answer(f"👤 **Профіль**\n\n🆔 ID: `{u['_id']}`\n💰 Баланс: `{u['balance']:.2f} 💎`"
                   f"\n📈 Оборот: `{u.get('turnover', 0.0):.2f} 💎`"
                   f"\n👥 Рефералів: `{u.get('referals_count', 0)}`", parse_mode="Markdown")

@dp.message(F.text == "🤝 Рефералка")
async def referal_cmd(m: types.Message):
    me = await bot.get_me()
    link = f"https://t.me/{me.username}?start={m.from_user.id}"
    await m.answer(f"🤝 **Реферальна програма**\n\nВи отримуєте 1% від кожного виграшу реферала!\n\n🔗 Твоє посилання:\n{link}")

# --- ГРИ ---
@dp.message(F.text == "🎮 Games")
async def games_menu(m: types.Message):
    kb = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="🎳 Боулінг"), KeyboardButton(text="🎲 Кубик")], [KeyboardButton(text="❌ Скасувати")]], resize_keyboard=True)
    await m.answer("Оберіть режим гри:", reply_markup=kb)

@dp.message(F.text.in_(["🎳 Боулінг", "🎲 Кубик"]))
async def g_mode(m: types.Message, state: FSMContext):
    await state.update_data(type="🎳" if "🎳" in m.text else "🎲")
    await m.answer("Кількість раундів:", reply_markup=ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="1"), KeyboardButton(text="5")], [KeyboardButton(text="❌ Скасувати")]], resize_keyboard=True))
    await state.set_state(GameStates.wait_rounds)

@dp.message(GameStates.wait_rounds, F.text.in_(["1", "5"]))
async def g_rounds(m: types.Message, state: FSMContext):
    await state.update_data(r=int(m.text))
    await m.answer("Введіть суму ставки (мін. 0.07 💎):", reply_markup=cancel_kb())
    await state.set_state(GameStates.wait_bet)

@dp.message(GameStates.wait_bet)
async def g_create(m: types.Message, state: FSMContext):
    try:
        bet = float(m.text.replace(",", "."))
        if bet < 0.07: return await m.answer("❌ Мінімум 0.07")
        u = await get_u(m.from_user.id); gid = str(uuid.uuid4())[:8]; d = await state.get_data()
        if u['balance'] < bet: return await m.answer("❌ Недостатньо коштів")
        await games_col.insert_one({"game_id": gid, "creator_id": m.from_user.id, "status": "waiting", "type": d['type'], "bet": bet, "rounds_total": d['r'], "c_score": [], "o_score": []})
        me = await bot.get_me()
        link = f"https://t.me/{me.username}?start=game_{gid}"
        await m.answer(f"✅ Гра створена! Твій хід {d['type']}\n\n🔗 Посилання для суперника:\n{link}", reply_markup=main_kb())
        await state.clear()
    except: await m.answer("Введіть число!")

@dp.message(F.dice)
async def dice_handler(m: types.Message):
    g = await games_col.find_one({"status": "playing", "turn": m.from_user.id})
    if not g or m.dice.emoji != g['type']: return
    opp = g['opponent_id'] if m.from_user.id == g['creator_id'] else g['creator_id']
    f = "c_score" if m.from_user.id == g['creator_id'] else "o_score"
    await bot.send_message(opp, "Суперник зробив хід:")
    await m.forward(opp)
    await asyncio.sleep(4)
    val = m.dice.value
    await games_col.update_one({"game_id": g['game_id']}, {"$push": {f: val}})
    upd = await games_col.find_one({"game_id": g['game_id']})
    if len(upd['c_score']) == upd['rounds_total'] and len(upd['o_score']) == upd['rounds_total']:
        await finish_game(upd)
    else:
        await games_col.update_one({"game_id": g['game_id']}, {"$set": {"turn": opp}})
        await m.answer(f"Випало: {val}. Чекаємо хід суперника...")
        await bot.send_message(opp, f"Твій хід! Кидай {g['type']}")

async def finish_game(g):
    s1, s2 = sum(g['c_score']), sum(g['o_score'])
    for uid in [g['creator_id'], g['opponent_id']]:
        win = (uid == g['creator_id'] and s1 > s2) or (uid == g['opponent_id'] and s2 > s1)
        if s1 == s2:
            await users_col.update_one({"_id": uid}, {"$inc": {"balance": g['bet']}})
            await bot.send_message(uid, f"🤝 Нічия ({s1}:{s2}). Ставка повернена.")
        elif win:
            raw_win = g['bet'] * 2
            adm_comm = raw_win * 0.01
            ref_comm = raw_win * 0.01
            net_win = raw_win - adm_comm - ref_comm
            await users_col.update_one({"_id": uid}, {"$inc": {"balance": net_win, "turnover": g['bet'], "games_played": 1}})
            await stats_col.update_one({"_id": "global"}, {"$inc": {"admin_balance": adm_comm}}, upsert=True)
            u = await users_col.find_one({"_id": uid})
            if u.get("referrer"):
                await users_col.update_one({"_id": u['referrer']}, {"$inc": {"balance": ref_comm}})
                try: await bot.send_message(u['referrer'], f"🤝 Реф. бонус: +{ref_comm:.4f} 💎")
                except: pass
            await bot.send_message(uid, f"🏆 Перемога! +{net_win:.2f} 💎 ({s1}:{s2})")
        else:
            await users_col.update_one({"_id": uid}, {"$inc": {"games_played": 1}})
            await bot.send_message(uid, f"❌ Програш ({s1}:{s2})")
    await games_col.update_one({"game_id": g['game_id']}, {"$set": {"status": "finished"}})

# --- БАЛАНС / ПОПОВНЕННЯ ---
@dp.message(F.text == "💎 Баланс")
async def balance_menu(m: types.Message):
    u = await get_u(m.from_user.id)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="📥 Поповнити", callback_data="dep"), InlineKeyboardButton(text="📤 Вивести", callback_data="draw")]])
    await m.answer(f"💰 Ваш баланс: `{u['balance']:.2f} 💎`", reply_markup=kb, parse_mode="Markdown")

@dp.callback_query(F.data == "dep")
async def dep_s(cb: types.CallbackQuery, state: FSMContext):
    await state.set_state(FinanceStates.wait_dep_amount)
    await cb.message.answer("💎 Введіть суму в 💎 (мін. 0.50):", reply_markup=cancel_kb())

@dp.message(FinanceStates.wait_dep_amount)
async def dep_a(m: types.Message, state: FSMContext):
    try:
        amt = float(m.text)
        if amt < 0.50: return await m.answer("❌ Мінімум 0.50")
        await state.update_data(amt=amt)
        pay_sum = (amt * CURRATE) * 1.05
        # ЧИСТИЙ ТЕКСТ БЕЗ ДУЖОК
        msg = (
            f"Заявка на поповнення:\n"
            f"Сума: {amt} 💎\n"
            f"Оплата: {pay_sum:.2f} ГРН\n"
            f"Реквізити: 5355 2800 2890 2177\n\n"
            f"Надішліть ФОТО чеку!"
        )
        await m.answer(msg, reply_markup=cancel_kb())
        await state.set_state(FinanceStates.wait_receipt)
    except: await m.answer("Введіть число!")

@dp.message(FinanceStates.wait_receipt, F.photo)
async def dep_r(m: types.Message, state: FSMContext):
    d = await state.get_data(); tid = str(uuid.uuid4())[:8]
    await finance_col.insert_one({"_id": tid, "uid": m.from_user.id, "amt": d['amt'], "type": "dep"})
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✅ Схвалити", callback_data=f"fok_{tid}"), InlineKeyboardButton(text="❌ Відхилити", callback_data=f"fno_{tid}")]])
    await bot.send_photo(ADMIN_ID, m.photo[-1].file_id, caption=f"📥 Депозит {d['amt']} від {m.from_user.id}", reply_markup=kb)
    await m.answer("✅ Чек надіслано на перевірку!", reply_markup=main_kb()); await state.clear()

@dp.callback_query(F.data == "draw")
async def draw_s(cb: types.CallbackQuery, state: FSMContext):
    await state.set_state(FinanceStates.wait_draw_sum)
    await cb.message.answer("💎 Сума виводу (мін. 4.50):", reply_markup=cancel_kb())

@dp.message(FinanceStates.wait_draw_sum)
async def draw_1(m: types.Message, state: FSMContext):
    try:
        amt = float(m.text); u = await get_u(m.from_user.id)
        if amt < 4.50 or u['balance'] < amt: return await m.answer("❌ Недостатньо коштів.")
        await state.update_data(amt=amt)
        await m.answer("Вкажіть реквізити (IBAN/Карта, ПІБ):")
        await state.set_state(FinanceStates.wait_draw_details)
    except: await m.answer("Введіть число!")

@dp.message(FinanceStates.wait_draw_details)
async def draw_2(m: types.Message, state: FSMContext):
    d = await state.get_data(); tid = str(uuid.uuid4())[:8]
    await users_col.update_one({"_id": m.from_user.id}, {"$inc": {"balance": -d['amt']}})
    await finance_col.insert_one({"_id": tid, "uid": m.from_user.id, "amt": d['amt'], "type": "draw", "info": m.text})
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✅ Виплачено", callback_data=f"dok_{tid}"), InlineKeyboardButton(text="❌ Відхилити", callback_data=f"dno_{tid}")]])
    await bot.send_message(ADMIN_ID, f"📤 ВИВІД {d['amt']} 💎\nID: {m.from_user.id}\nРеквізити: {m.text}", reply_markup=kb)
    await m.answer("✅ Заявку на вивід прийнято!", reply_markup=main_kb()); await state.clear()

# --- АДМІН ПАНЕЛЬ ---
@dp.message(F.text == "⚙️ Налаштування")
async def sett_cmd(m: types.Message):
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🆘 Підтримка", url="https://t.me/vex0o0")]])
    if m.from_user.id == ADMIN_ID: kb.inline_keyboard.append([InlineKeyboardButton(text="👨‍💻 Admin", callback_data="admin")])
    await m.answer("⚙️ Налаштування:", reply_markup=kb)

@dp.callback_query(F.data == "admin")
async def adm_p(cb: types.CallbackQuery):
    c = await users_col.count_documents({})
    res_u = await users_col.aggregate([{"$group": {"_id": None, "total": {"$sum": "$balance"}}}]).to_list(1)
    total_u = res_u[0]['total'] if res_u else 0
    res_a = await stats_col.find_one({"_id": "global"})
    total_a = res_a.get("admin_balance", 0) if res_a else 0
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🏆 Топ Рефералів", callback_data="adm_top_ref")],
        [InlineKeyboardButton(text="📊 Топ Оборотів", callback_data="adm_top_turn")]
    ])
    await cb.message.answer(f"👨‍💻 **Адмінка**\nЮзерів: {c}\nБаланс юзерів: {total_u:.2f} 💎\nМій баланс: {total_a:.2f} 💎", reply_markup=kb)

@dp.callback_query(F.data.startswith(("fok_", "fno_", "dok_", "dno_")))
async def fin_handle(cb: types.CallbackQuery):
    act, tid = cb.data.split("_")
    f = await finance_col.find_one({"_id": tid})
    if not f: return
    if act == "fok":
        await users_col.update_one({"_id": f['uid']}, {"$inc": {"balance": f['amt']}})
        await bot.send_message(f['uid'], f"✅ Поповнення схвалено! +{f['amt']} 💎")
    elif act == "fno":
        await bot.send_message(f['uid'], "❌ Поповнення відхилено адміном.")
    elif act == "dok":
        await bot.send_message(f['uid'], "✅ Вивід успішно виплачено!")
    elif act == "dno":
        await users_col.update_one({"_id": f['uid']}, {"$inc": {"balance": f['amt']}})
        await bot.send_message(f['uid'], "❌ Вивід відхилено. Кошти повернуто на баланс.")
    await finance_col.delete_one({"_id": tid}); await cb.message.delete()

@dp.message(F.text == "❌ Скасувати")
async def global_cancel(m: types.Message, state: FSMContext):
    await state.clear()
    await m.answer("Дію скасовано.", reply_markup=main_kb())

async def main(): await dp.start_polling(bot)
if __name__ == "__main__": asyncio.run(main())
