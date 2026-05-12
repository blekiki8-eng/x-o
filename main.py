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

# --- СТАНИ FSM ---
class GameStates(StatesGroup):
    wait_rounds = State()
    wait_bet = State()

class FinanceStates(StatesGroup):
    wait_dep_amount = State()
    wait_receipt = State()
    wait_draw_sum = State()
    wait_draw_details = State()

# --- ДОПОМІЖНІ ФУНКЦІЇ ---
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

# --- START ТА ВХІД У ГРУ ---
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
        gid = args[1].replace("game_", "").replace("`", "").strip()
        g = await games_col.find_one({"game_id": gid, "status": "waiting"})
        
        if not g: return await m.answer("❌ Гра не знайдена.", reply_markup=main_kb())
        if g['creator_id'] == user_id: return await m.answer("❌ Це ваша гра.", reply_markup=main_kb())
        if u['balance'] < g['bet']: return await m.answer(f"❌ Потрібно {g['bet']} 💎", reply_markup=main_kb())
        
        await users_col.update_many({"_id": {"$in": [g['creator_id'], user_id]}}, {"$inc": {"balance": -g['bet']}})
        await games_col.update_one({"game_id": gid}, {"$set": {"opponent_id": user_id, "status": "playing", "turn": g['creator_id']}})
        
        await bot.send_message(g['creator_id'], f"🎮 Гравець приєднався! Твій хід: `{g['type']}`", parse_mode="Markdown")
        return await m.answer(f"🎮 Ви приєдналися! Хід суперника: `{g['type']}`", reply_markup=main_kb(), parse_mode="Markdown")

    await m.answer("💎 Different Games вітає вас!", reply_markup=main_kb())

# --- ІГРОВА ЛОГІКА ---
@dp.message(F.text == "🎮 Games")
async def games_menu(m: types.Message):
    kb = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="🎳 Боулінг"), KeyboardButton(text="🎲 Кубик")], [KeyboardButton(text="❌ Скасувати")]], resize_keyboard=True)
    await m.answer("Оберіть режим:", reply_markup=kb)

@dp.message(F.text.in_(["🎳 Боулінг", "🎲 Кубик"]))
async def g_mode(m: types.Message, state: FSMContext):
    await state.update_data(type="🎳" if "🎳" in m.text else "🎲")
    await m.answer("Раунди (1 або 5):", reply_markup=ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="1"), KeyboardButton(text="5")], [KeyboardButton(text="❌ Скасувати")]], resize_keyboard=True))
    await state.set_state(GameStates.wait_rounds)

@dp.message(GameStates.wait_rounds, F.text.in_(["1", "5"]))
async def g_rounds(m: types.Message, state: FSMContext):
    await state.update_data(r=int(m.text))
    await m.answer("Введіть ставку:", reply_markup=cancel_kb())
    await state.set_state(GameStates.wait_bet)

@dp.message(GameStates.wait_bet)
async def g_create(m: types.Message, state: FSMContext):
    try:
        bet = float(m.text.replace(",", "."))
        if bet < 0.07: return await m.answer("❌ Мінімум 0.07")
        u = await get_u(m.from_user.id); d = await state.get_data(); gid = str(uuid.uuid4())[:8]
        if u['balance'] < bet: return await m.answer("❌ Мало коштів")
        await games_col.insert_one({"game_id": gid, "creator_id": m.from_user.id, "status": "waiting", "type": d['type'], "bet": bet, "rounds_total": d['r'], "c_score": [], "o_score": []})
        me = await bot.get_me()
        await m.answer(f"✅ Готово! Твій хід: `{d['type']}`\n\n🔗 Посилання:\nhttps://t.me/{me.username}?start=game_{gid}", reply_markup=main_kb(), parse_mode="Markdown")
        await state.clear()
    except: await m.answer("Число!")

@dp.message(F.dice)
async def dice_handler(m: types.Message):
    g = await games_col.find_one({"status": "playing", "turn": m.from_user.id})
    if not g or m.dice.emoji != g['type']: return
    opp = g['opponent_id'] if m.from_user.id == g['creator_id'] else g['creator_id']
    f = "c_score" if m.from_user.id == g['creator_id'] else "o_score"
    await bot.send_message(opp, "Суперник кинув стікер:")
    await m.forward(opp)
    await asyncio.sleep(4)
    await games_col.update_one({"game_id": g['game_id']}, {"$push": {f: m.dice.value}})
    upd = await games_col.find_one({"game_id": g['game_id']})
    if len(upd['c_score']) == upd['rounds_total'] and len(upd['o_score']) == upd['rounds_total']:
        await finish_game(upd)
    else:
        await games_col.update_one({"game_id": g['game_id']}, {"$set": {"turn": opp}})
        await m.answer(f"Випало: {m.dice.value}. Чекаємо суперника...")
        await bot.send_message(opp, f"Твій хід! Натисни, щоб скопіювати стікер: `{g['type']}`", parse_mode="Markdown")

async def finish_game(g):
    s1, s2 = sum(g['c_score']), sum(g['o_score'])
    for uid in [g['creator_id'], g['opponent_id']]:
        my_s = s1 if uid == g['creator_id'] else s2
        op_s = s2 if uid == g['creator_id'] else s1
        
        # ТЕКСТ РЕЗУЛЬТАТУ ЗА ТВОЇМ ШАБЛОНОМ
        res_text = f"Результат гри:\nВи: {my_s}\nСуперник: {op_s}\n\n"
        
        if s1 == s2:
            await users_col.update_one({"_id": uid}, {"$inc": {"balance": g['bet']}})
            await bot.send_message(uid, res_text + "🤝 Нічия! Ставка повернена.")
        elif my_s > op_s:
            raw_win = g['bet'] * 2
            u = await users_col.find_one({"_id": uid})
            
            # ЛОГІКА КОМІСІЇ 2%
            if u.get("referrer"):
                adm_fee = raw_win * 0.01
                ref_fee = raw_win * 0.01
                await users_col.update_one({"_id": u['referrer']}, {"$inc": {"balance": ref_fee}})
                try: await bot.send_message(u['referrer'], f"🤝 Реф. бонус: +{ref_fee:.4f} 💎")
                except: pass
            else:
                adm_fee = raw_win * 0.02  # Всі 2% адміну, якщо немає реферера
                ref_fee = 0
            
            net_win = raw_win - adm_fee - ref_fee
            await users_col.update_one({"_id": uid}, {"$inc": {"balance": net_win, "turnover": g['bet'], "games_played": 1}})
            await stats_col.update_one({"_id": "global"}, {"$inc": {"admin_balance": adm_fee}}, upsert=True)
            await bot.send_message(uid, res_text + f"Вітаю з перемогою +{net_win:.2f}")
        else:
            await users_col.update_one({"_id": uid}, {"$inc": {"games_played": 1}})
            await bot.send_message(uid, res_text + "Вітаю ви програли")
            
    await games_col.update_one({"game_id": g['game_id']}, {"$set": {"status": "finished"}})

# --- ФІНАНСИ ---
@dp.message(F.text == "💎 Баланс")
async def balance_menu(m: types.Message):
    u = await get_u(m.from_user.id)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="📥 Поповнити", callback_data="dep"), InlineKeyboardButton(text="📤 Вивести", callback_data="draw")]])
    await m.answer(f"💰 Баланс: `{u['balance']:.2f} 💎`", reply_markup=kb, parse_mode="Markdown")

@dp.callback_query(F.data == "dep")
async def dep_s(cb: types.CallbackQuery, state: FSMContext):
    await state.set_state(FinanceStates.wait_dep_amount)
    await cb.message.answer("💎 Вкажіть суму:", reply_markup=cancel_kb())

@dp.message(FinanceStates.wait_dep_amount)
async def dep_a(m: types.Message, state: FSMContext):
    try:
        amt = float(m.text)
        if amt < 0.50: return await m.answer("❌ Мінімум 0.50")
        await state.update_data(amt=amt)
        pay = (amt * CURRATE) * 1.05
        await m.answer(f"Заявка на поповнення:\nСума: {amt} 💎\nОплата: {pay:.2f} ГРН\nРеквізити: 5355 2800 2890 2177\n\nНадішліть ФОТО чеку!", reply_markup=cancel_kb())
        await state.set_state(FinanceStates.wait_receipt)
    except: await m.answer("Число!")

@dp.message(FinanceStates.wait_receipt, F.photo)
async def dep_r(m: types.Message, state: FSMContext):
    d = await state.get_data(); tid = str(uuid.uuid4())[:8]
    await finance_col.insert_one({"_id": tid, "uid": m.from_user.id, "amt": d['amt'], "type": "dep"})
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✅ Схвалити", callback_data=f"fok_{tid}"), InlineKeyboardButton(text="❌ Відхилити", callback_data=f"fno_{tid}")]])
    await bot.send_photo(ADMIN_ID, m.photo[-1].file_id, caption=f"📥 Поповнення {d['amt']} від {m.from_user.id}", reply_markup=kb)
    await m.answer("✅ Чек надіслано!", reply_markup=main_kb()); await state.clear()

# --- СТАНДАРТНІ КОМАНДИ ---
@dp.message(F.text == "👤 Профіль")
async def profile_cmd(m: types.Message):
    u = await get_u(m.from_user.id)
    await m.answer(f"👤 Профіль\nID: `{u['_id']}`\n💰 Баланс: `{u['balance']:.2f} 💎`", parse_mode="Markdown")

@dp.message(F.text == "🤝 Рефералка")
async def referal_cmd(m: types.Message):
    me = await bot.get_me()
    await m.answer(f"🤝 Рефералка\nПосилання: https://t.me/{me.username}?start={m.from_user.id}")

@dp.message(F.text == "⚙️ Налаштування")
async def sett_cmd(m: types.Message):
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🆘 Support", url="https://t.me/vex0o0")]])
    if m.from_user.id == ADMIN_ID: kb.inline_keyboard.append([InlineKeyboardButton(text="👨‍💻 Admin", callback_data="admin")])
    await m.answer("⚙️ Налаштування:", reply_markup=kb)

@dp.callback_query(F.data == "admin")
async def adm_p(cb: types.CallbackQuery):
    res_a = await stats_col.find_one({"_id": "global"})
    total_a = res_a.get("admin_balance", 0) if res_a else 0
    await cb.message.answer(f"👨‍💻 Admin\nМій баланс (комісії): {total_a:.2f} 💎")

@dp.message(F.text == "❌ Скасувати")
async def global_cancel(m: types.Message, state: FSMContext):
    await state.clear()
    await m.answer("Скасовано.", reply_markup=main_kb())

async def main(): await dp.start_polling(bot)
if __name__ == "__main__": asyncio.run(main())
