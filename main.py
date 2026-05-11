import os, uuid, logging, asyncio
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, CommandObject
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

def rounds_kb():
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="1 раунд"), KeyboardButton(text="5 раундів")],
        [KeyboardButton(text="⬅️ Назад")]
    ], resize_keyboard=True)

# --- ДОПОМІЖНІ ФУНКЦІЇ ---
async def get_u(uid, ref_id=None):
    u = await users_col.find_one({"_id": uid})
    if not u:
        u = {
            "_id": uid, "balance": 0.0, "referals_count": 0, 
            "turnover": 0.0, "games_played": 0, "referrer": ref_id
        }
        await users_col.insert_one(u)
        if ref_id:
            await users_col.update_one({"_id": ref_id}, {"$inc": {"referals_count": 1}})
    return u

async def add_admin_profit(amount):
    await stats_col.update_one({"_id": "global"}, {"$inc": {"admin_profit": amount}}, upsert=True)

# --- АДМІН-КОМАНДИ ---

@dp.message(Command("pay"))
async def admin_pay(m: types.Message, command: CommandObject):
    if m.from_user.id != ADMIN_ID: return
    try:
        uid, amt = command.args.split()
        uid, amt = int(uid), float(amt)
        await users_col.update_one({"_id": uid}, {"$inc": {"balance": amt}})
        await m.answer(f"✅ Нараховано {amt} 💎 користувачу `{uid}`")
        await bot.send_message(uid, f"🔔 Адмін нарахував вам `{amt}` 💎 на баланс.")
    except: await m.answer("Формат: `/pay ID сума`")

@dp.message(Command("bonus"))
async def admin_bonus(m: types.Message, command: CommandObject):
    if m.from_user.id != ADMIN_ID: return
    try:
        uid, amt = command.args.split()
        uid, amt = int(uid), float(amt)
        await users_col.update_one({"_id": uid}, {"$inc": {"balance": amt}})
        await m.answer(f"🏆 Бонус {amt} 💎 нараховано топу `{uid}`")
        await bot.send_message(uid, f"🏆 Вітаємо! Ви отримали бонус `{amt}` 💎 як топ-гравець!")
    except: await m.answer("Формат: `/bonus ID сума`")

# --- ПРОФІЛЬ ТА МЕНЮ ---

@dp.message(Command("start"))
async def cmd_start(m: types.Message, state: FSMContext):
    await state.clear()
    args = m.text.split()
    ref_id = int(args[1]) if len(args) > 1 and args[1].isdigit() else None
    await get_u(m.from_user.id, ref_id)
    if len(args) > 1 and args[1].startswith("game_"):
        return await join_game_logic(m, args[1].replace("game_", ""))
    await m.answer("💎 Вітаємо у Different Games!", reply_markup=main_kb())

@dp.message(F.text == "👤 Профіль")
async def profile_cmd(m: types.Message):
    u = await get_u(m.from_user.id)
    text = (f"👤 **Профіль:**\n\n🆔 Id: `{m.from_user.id}`\n💰 Баланс: `{u['balance']:.2f}` 💎\n\n"
            f"Запрошених гравців: `{u.get('referals_count', 0)}`👤\n"
            f"Оборот: `{u.get('turnover', 0.0):.2f}` 💎\n"
            f"Зіграно ігор: `{u.get('games_played', 0)}` 🎳")
    await m.answer(text, parse_mode="Markdown")

@dp.message(F.text == "⚙️ Налаштування")
async def settings(m: types.Message):
    buttons = [[InlineKeyboardButton(text="🆘 Підтримка", url="https://t.me/vex0o0")]]
    if m.from_user.id == ADMIN_ID:
        buttons.append([InlineKeyboardButton(text="👨‍💻 Адмін Панель", callback_data="admin_panel")])
    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    await m.answer("⚙️ **Налаштування:**", reply_markup=kb, parse_mode="Markdown")

@dp.callback_query(F.data == "admin_panel")
async def admin_panel_cb(cb: types.CallbackQuery):
    if cb.from_user.id != ADMIN_ID: return
    stats = await stats_col.find_one({"_id": "global"})
    profit = stats.get("admin_profit", 0.0) if stats else 0.0
    top_users = await users_col.find().sort("turnover", -1).limit(10).to_list(10)
    top_text = "\n".join([f"{i+1}. `{u['_id']}` — {u['turnover']:.2f}" for i, u in enumerate(top_users)])
    text = (f"👨‍💻 **Адмін Панель**\n\n📈 Дохід з виграшів: `{profit:.4f}` 💎\n\n"
            f"🏆 **Топ-10 по обороту:**\n{top_text}")
    await cb.message.edit_text(text, parse_mode="Markdown")

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
async def create_game(m: types.Message, state: FSMContext):
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
        await m.answer(f"Посилання на гру: `https://t.me/{me.username}?start=game_{gid}`", reply_markup=main_kb(), parse_mode="Markdown")
        await state.clear()
    except: await m.answer("Введіть число")

async def join_game_logic(m, gid):
    g = await games_col.find_one({"game_id": gid, "status": "waiting"})
    if not g or g['creator_id'] == m.from_user.id: return await m.answer("❌ Гра недоступна")
    u2 = await get_u(m.from_user.id)
    if u2['balance'] < g['bet']: return await m.answer("❌ Мало коштів")
    await users_col.update_many({"_id": {"$in": [g['creator_id'], m.from_user.id]}}, {"$inc": {"balance": -g['bet'], "turnover": g['bet'], "games_played": 1}})
    await games_col.update_one({"game_id": gid}, {"$set": {"opponent_id": m.from_user.id, "status": "playing", "turn": g['creator_id']}})
    await bot.send_message(g['creator_id'], f"Гра створена ✅\nВаш хід! Кидайте {g['type']}")
    await m.answer(f"Гра створена ✅\nОчікуйте ходу суперника...")

@dp.message(F.dice)
async def handle_game_dice(m: types.Message):
    g = await games_col.find_one({"status": "playing", "$or": [{"creator_id": m.from_user.id}, {"opponent_id": m.from_user.id}]})
    if not g or g['turn'] != m.from_user.id or m.dice.emoji != g['type']: return
    val = m.dice.value
    is_c = (m.from_user.id == g['creator_id'])
    f_add = "c_score" if is_c else "o_score"
    next_p = g['opponent_id'] if is_c else g['creator_id']
    await games_col.update_one({"game_id": g['game_id']}, {"$push": {f_add: val}, "$set": {"turn": next_p}})
    
    await bot.send_dice(next_p, emoji=g['type'])
    await bot.send_message(next_p, f"Супернику випало {val}, тепер ваш хід!")

    upd = await games_col.find_one({"game_id": g['game_id']})
    if len(upd['c_score']) == upd['rounds_total'] and len(upd['o_score']) == upd['rounds_total']:
        s1, s2 = sum(upd['c_score']), sum(upd['o_score'])
        await process_finish(upd, s1, s2)

async def process_finish(g, s1, s2):
    win_sum = g['bet'] * 1.98
    ref_share = g['bet'] * 0.01
    adm_share = g['bet'] * 0.01

    if s1 == s2:
        for uid in [g['creator_id'], g['opponent_id']]:
            await users_col.update_one({"_id": uid}, {"$inc": {"balance": g['bet']}})
            await bot.send_message(uid, f"Результат {s1}:{s2} — Нічия! Ставки повернуто.")
    else:
        win_id = g['creator_id'] if s1 > s2 else g['opponent_id']
        lose_id = g['opponent_id'] if s1 > s2 else g['creator_id']
        w_score, l_score = (s1, s2) if s1 > s2 else (s2, s1)
        
        await users_col.update_one({"_id": win_id}, {"$inc": {"balance": win_sum}})
        u_win = await users_col.find_one({"_id": win_id})
        ref = u_win.get("referrer")
        if ref:
            await users_col.update_one({"_id": ref}, {"$inc": {"balance": ref_share}})
            await add_admin_profit(adm_share)
            try: await bot.send_message(ref, f"🤝 Бонус за виграш реферала: `{ref_share:.4f}` 💎")
            except: pass
        else:
            await add_admin_profit(g['bet'] * 0.02)

        await bot.send_message(win_id, f"Результат:\nУ вас: {w_score}\nУ противника: {l_score}\n\nВітаю ви виграли +{win_sum:.2f} 💎")
        await bot.send_message(lose_id, f"Результат:\nУ вас: {l_score}\nУ противника: {w_score}\n\nНажаль ви програли ❌")
    
    await games_col.update_one({"game_id": g['game_id']}, {"$set": {"status": "finished"}})

# --- ФІНАНСИ ---

@dp.message(F.text == "💎 Баланс")
async def balance_menu(m: types.Message):
    u = await get_u(m.from_user.id)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="📥 Поповнити", callback_data="dep"), InlineKeyboardButton(text="📤 Вивести", callback_data="with")]])
    await m.answer(f"💰 Ваш баланс: `{u['balance']:.2f}` 💎", reply_markup=kb, parse_mode="Markdown")

@dp.callback_query(F.data == "dep")
async def start_dep(cb: types.CallbackQuery, state: FSMContext):
    await cb.message.answer("Сума поповнення (мін 0.5 💎):")
    await state.set_state(FinanceStates.wait_dep_amount)

@dp.message(FinanceStates.wait_dep_amount)
async def dep_amount(m: types.Message, state: FSMContext):
    try:
        amt = float(m.text.replace(",", "."))
        await state.update_data(amt=amt)
        total = (amt * CURRATE) * 1.05
        await m.answer(f"До оплати: `{total:.2f} ГРН`\n💳 Карта: `5355 2800 2890 2177`\n\nНадішліть чек:")
        await state.set_state(FinanceStates.wait_receipt)
    except: await m.answer("Число!")

@dp.message(FinanceStates.wait_receipt, F.photo | F.document)
async def dep_receipt(m: types.Message, state: FSMContext):
    data = await state.get_data(); amt = data['amt']; uid = m.from_user.id
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✅", callback_data=f"adm_ok_{uid}_{amt}"), InlineKeyboardButton(text="❌", callback_data=f"adm_no_{uid}")]])
    await bot.send_message(ADMIN_ID, f"🔔 Чек від `{uid}` на `{amt} 💎`", parse_mode="Markdown")
    if m.photo: await bot.send_photo(ADMIN_ID, m.photo[-1].file_id, reply_markup=kb)
    else: await bot.send_document(ADMIN_ID, m.document.file_id, reply_markup=kb)
    await m.answer("⏳ Чек надіслано."); await state.clear()

@dp.callback_query(F.data == "with")
async def start_with(cb: types.CallbackQuery, state: FSMContext):
    await cb.message.answer("Сума для виводу:"); await state.set_state(FinanceStates.wait_withdraw_amount)

@dp.message(FinanceStates.wait_withdraw_amount)
async def with_amt(m: types.Message, state: FSMContext):
    try:
        amt = float(m.text.replace(",", "."))
        u = await get_u(m.from_user.id)
        if u['balance'] < amt: return await m.answer("❌ Недостатньо")
        await state.update_data(w_amt=amt); await m.answer("Вкажіть реквізити:"); await state.set_state(FinanceStates.wait_withdraw_details)
    except: await m.answer("Число!")

@dp.message(FinanceStates.wait_withdraw_details)
async def with_final(m: types.Message, state: FSMContext):
    data = await state.get_data(); amt = data['w_amt']
    await users_col.update_one({"_id": m.from_user.id}, {"$inc": {"balance": -amt}})
    await bot.send_message(ADMIN_ID, f"📤 ВИВІД: `{m.from_user.id}`\nСума: `{amt}`\nРеквізити: {m.text}")
    await m.answer("✅ Заявка прийнята."); await state.clear()

@dp.callback_query(F.data.startswith("adm_"))
async def admin_verify(cb: types.CallbackQuery):
    d = cb.data.split("_")
    action, uid, amt = d[1], int(d[2]), float(d[3] if len(d)>3 else 0)
    if action == "ok":
        await users_col.update_one({"_id": uid}, {"$inc": {"balance": amt}})
        await bot.send_message(uid, f"✅ Баланс поповнено на {amt} 💎")
        await cb.message.edit_caption(caption="Схвалено ✅")
    else:
        await bot.send_message(uid, "❌ Чек відхилено."); await cb.message.edit_caption(caption="Відхилено ❌")

@dp.message(F.text == "🤝 Рефералка")
async def ref(m: types.Message):
    me = await bot.get_me()
    await m.answer(f"🔗 Посилання: `https://t.me/{me.username}?start={m.from_user.id}`", parse_mode="Markdown")

@dp.message(F.text == "⬅️ Назад")
async def back(m: types.Message): await m.answer("Меню:", reply_markup=main_kb())

async def main(): await dp.start_polling(bot)
if __name__ == "__main__": asyncio.run(main())
