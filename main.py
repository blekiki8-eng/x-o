import os, uuid, logging, asyncio
from aiogram import Bot, Dispatcher, executor, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

# --- НАЛАШТУВАННЯ ---
load_dotenv()
API_TOKEN = os.getenv("BOT_TOKEN")
MONGO_URL = os.getenv("MONGO_URL")
ADMIN_ID = int(os.getenv("ADMIN_ID")) if os.getenv("ADMIN_ID") else 0

RATE = 44.50  # Курс 1💎 = 44.50 грн
logging.basicConfig(level=logging.INFO)
storage = MemoryStorage()
bot = Bot(token=API_TOKEN)
dp = Dispatcher(bot, storage=storage)

cluster = AsyncIOMotorClient(MONGO_URL)
db = cluster["game_bot_db"]
users_col = db["users"]
games_col = db["games"]

# --- СТАНИ FSM ---
class DepositState(StatesGroup):
    wait_amount = State()
    wait_receipt = State()

class WithdrawState(StatesGroup):
    wait_amount = State()
    wait_details = State()

class GameState(StatesGroup):
    wait_mode = State()  # Вибір Матч 1 / Матч 5
    wait_bet = State()   # Введення ставки

# --- КЛАВІАТУРИ ---
def main_menu(user_id):
    markup = ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add(KeyboardButton("👤 Профіль"), KeyboardButton("🎮 Games"))
    markup.add(KeyboardButton("💎 Баланс"), KeyboardButton("🤝 Рефералка"))
    if user_id == ADMIN_ID:
        markup.add(KeyboardButton("🛡 Панель адміна"))
    return markup

def games_menu():
    markup = ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add(KeyboardButton("🎳 Боулінг"))
    markup.add(KeyboardButton("❌ Скасувати"))
    return markup

def match_selection_menu():
    markup = ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add(KeyboardButton("Матч 1"), KeyboardButton("Матч 5"))
    markup.add(KeyboardButton("❌ Скасувати"))
    return markup

def cancel_kb():
    return ReplyKeyboardMarkup(resize_keyboard=True).add(KeyboardButton("❌ Скасувати"))

async def get_u(user_id, referrer=None):
    u = await users_col.find_one({"_id": user_id})
    if not u:
        u = {
            "_id": user_id, 
            "balance": 0.0, 
            "total_deposited": 0.0, 
            "referred_count": 0,
            "referrer": referrer
        }
        await users_col.insert_one(u)
        if referrer:
            try: await users_col.update_one({"_id": int(referrer)}, {"$inc": {"referred_count": 1}})
            except: pass
    return u

# --- СИСТЕМА СКАСУВАННЯ (Пріоритетна) ---
@dp.message_handler(lambda m: m.text == "❌ Скасувати", state="*")
async def universal_cancel(m: types.Message, state: FSMContext):
    await state.finish()
    await m.answer("Дію скасовано. Повернення в меню.", reply_markup=main_menu(m.from_user.id))

# --- МЕНЮ GAMES ТА РЕЖИМИ ---
@dp.message_handler(lambda m: m.text == "🎮 Games", state="*")
async def games_choice(m: types.Message):
    await m.answer("Оберіть гру:", reply_markup=games_menu())

@dp.message_handler(lambda m: m.text == "🎳 Боулінг", state="*")
async def bowling_choice(m: types.Message):
    await GameState.wait_mode.set()
    await m.answer("Оберіть режим матчу:", reply_markup=match_selection_menu())

@dp.message_handler(lambda m: m.text in ["Матч 1", "Матч 5"], state=GameState.wait_mode)
async def bet_choice(m: types.Message, state: FSMContext):
    await state.update_data(mode=m.text)
    await GameState.wait_bet.set()
    await m.answer(f"Ви обрали {m.text}. Введіть суму ставки 💎:", reply_markup=cancel_kb())

# --- ГОЛОВНА ЛОГІКА ГРИ (Мульти-раунди) ---
@dp.message_handler(content_types=types.ContentTypes.DICE, state="*")
async def handle_bowling_dice(m: types.Message):
    if m.dice.emoji != "🎳": return
    uid = m.from_user.id
    
    g = await games_col.find_one({"status": "playing", "$or": [{"creator_id": uid}, {"opponent_id": uid}]})
    if not g: return # Повне ігнорування, якщо гри немає

    if g.get('turn') != uid:
        return await m.answer("Зараз не ваш хід! Зачекайте суперника.")

    val = m.dice.value
    cid, oid, gid = g['creator_id'], g['opponent_id'], g['game_id']
    mode = g.get('mode', 'Матч 1')
    max_throws = 5 if mode == "Матч 5" else 1

    c_throws = g.get('c_throws', [])
    o_throws = g.get('o_throws', [])

    if uid == cid:
        c_throws.append(val)
        current_round = len(c_throws)
        await games_col.update_one({"game_id": gid}, {"$set": {"c_throws": c_throws, "turn": oid}})
        await m.answer(f"Кидок №{current_round}: вибили {val} 🎳")
        await bot.send_message(oid, f"Суперник кинув на {val}. Ваш хід!")
    else:
        o_throws.append(val)
        current_round = len(o_throws)
        
        if current_round >= max_throws:
            # ФІНАЛ ГРИ
            c_score, o_score = sum(c_throws), sum(o_throws)
            win_sum = round(g['bet'] * 2, 2)
            await games_col.update_one({"game_id": gid}, {"$set": {"status": "finished", "o_throws": o_throws}})

            if c_score > o_score:
                await users_col.update_one({"_id": cid}, {"$inc": {"balance": win_sum}})
                res_c, res_o = f"🎉 Перемога! На баланс нараховано {win_sum} 💎", "😢 Ви програли."
            elif o_score > c_score:
                await users_col.update_one({"_id": oid}, {"$inc": {"balance": win_sum}})
                res_o, res_c = f"🎉 Перемога! На баланс нараховано {win_sum} 💎", "😢 Ви програли."
            else:
                await users_col.update_many({"_id": {"$in": [cid, oid]}}, {"$inc": {"balance": g['bet']}})
                res_c = res_o = "🤝 Нічия! Ставки повернуто."

            fin_text = f"📊 Результат ({mode}):\nТворець: {c_score}\nСуперник: {o_score}\n\n"
            await bot.send_message(cid, fin_text + res_c + "\n\nГра закінчена")
            await bot.send_message(oid, fin_text + res_o + "\n\nГра закінчена")
        else:
            await games_col.update_one({"game_id": gid}, {"$set": {"o_throws": o_throws, "turn": cid}})
            await m.answer(f"Кидок №{current_round}: вибили {val} 🎳")
            await bot.send_message(cid, f"Суперник кинув на {val}. Ваш черга кидати!")

# --- ПОПОВНЕННЯ (Мін 0.50) ---
@dp.callback_query_handler(lambda c: c.data == "dep", state="*")
async def dep_start(c: types.CallbackQuery):
    await DepositState.wait_amount.set()
    await bot.send_message(c.from_user.id, "💎 Введіть суму (мін. 0.50):", reply_markup=cancel_kb())

@dp.message_handler(state=DepositState.wait_amount)
async def dep_amount(m: types.Message, state: FSMContext):
    try:
        amt = float(m.text.replace(',', '.'))
        if amt < 0.50: return await m.answer("❌ Мінімум 0.50 💎")
        to_pay = round((amt * RATE) * 1.05, 2)
        await state.update_data(amt=amt)
        text = (
            f"➖➖➖➖➖➖➖➖➖➖\n📥 **ЗАЯВКА НА ПОПОВНЕННЯ**\n➖➖➖➖➖➖➖➖➖➖\n"
            f"💎 **Отримаєте:** {amt} 💎\n💳 **До сплати:** {to_pay} грн\n\n"
            f"📌 **Реквізити:**\n`5355 2800 2890 2177` (Mono)\n\n"
            f"Надішліть **скріншот квитанції** сюди:"
        )
        await m.answer(text, parse_mode="Markdown")
        await DepositState.wait_receipt.set()
    except: await m.answer("Введіть число!")

@dp.message_handler(state=DepositState.wait_receipt, content_types=['photo'])
async def dep_receipt(m: types.Message, state: FSMContext):
    d = await state.get_data()
    kb = InlineKeyboardMarkup().add(InlineKeyboardButton("✅ Ок", callback_data=f"adm_ok:{m.from_user.id}:{d['amt']}"))
    await bot.send_photo(ADMIN_ID, m.photo[-1].file_id, caption=f"📥 {d['amt']} 💎 від {m.from_user.id}", reply_markup=kb)
    await m.answer("✅ Надіслано на перевірку.", reply_markup=main_menu(m.from_user.id))
    await state.finish()

# --- ВИВЕДЕННЯ (Мін 4) ---
@dp.callback_query_handler(lambda c: c.data == "withdr", state="*")
async def withdr_start(c: types.CallbackQuery):
    u = await get_u(c.from_user.id)
    if u['balance'] < 4: return await bot.send_message(c.from_user.id, "❌ Мінімум 4 💎")
    await WithdrawState.wait_amount.set()
    await bot.send_message(c.from_user.id, "Введіть суму (мін. 4 💎):", reply_markup=cancel_kb())

@dp.message_handler(state=WithdrawState.wait_amount)
async def withdr_amt(m: types.Message, state: FSMContext):
    try:
        amt = float(m.text.replace(',', '.'))
        u = await get_u(m.from_user.id)
        if amt < 4 or amt > u['balance']: return await m.answer("❌ Помилка суми!")
        await state.update_data(amt=amt)
        await m.answer("Введіть дані для виводу коштів:\n\nIBAN\nІПН\nПІБ", reply_markup=cancel_kb())
        await WithdrawState.wait_details.set()
    except: await m.answer("Число!")

@dp.message_handler(state=WithdrawState.wait_details)
async def withdr_fin(m: types.Message, state: FSMContext):
    data = await state.get_data()
    await users_col.update_one({"_id": m.from_user.id}, {"$inc": {"balance": -data['amt']}})
    await bot.send_message(ADMIN_ID, f"📤 **ВИВІД**\nСума: {data['amt']}\nДані: {m.text}")
    await m.answer("✅ Заявку прийнято.", reply_markup=main_menu(m.from_user.id))
    await state.finish()

# --- СТАРТ ТА СТВОРЕННЯ ГРИ ---
@dp.message_handler(state=GameState.wait_bet)
async def set_bet(m: types.Message, state: FSMContext):
    try:
        bet = float(m.text.replace(',', '.'))
        u = await get_u(m.from_user.id)
        if u['balance'] < bet: return await m.answer("❌ Недостатньо коштів!")
        data = await state.get_data()
        gid = str(uuid.uuid4())[:8]
        await users_col.update_one({"_id": m.from_user.id}, {"$inc": {"balance": -bet}})
        await games_col.insert_one({
            "game_id": gid, "creator_id": m.from_user.id, "bet": bet, 
            "status": "waiting", "mode": data['mode'], "c_throws": [], "o_throws": []
        })
        me = await bot.get_me()
        await m.answer(f"🎳 Гра створена ({data['mode']})!\n`https://t.me/{me.username}?start=game_{gid}`", reply_markup=main_menu(m.from_user.id))
        await state.finish()
    except: await m.answer("Введіть число!")

@dp.message_handler(commands=['start'], state="*")
async def start_cmd(m: types.Message):
    args = m.get_args()
    ref = args if (args and args.isdigit()) else None
    u = await get_u(m.from_user.id, ref)
    if args and args.startswith("game_"):
        gid = args.replace("game_", "")
        g = await games_col.find_one({"game_id": gid, "status": "waiting"})
        if g and g['creator_id'] != m.from_user.id:
            if u['balance'] < g['bet']: return await m.answer("❌ Мало коштів!")
            await users_col.update_one({"_id": m.from_user.id}, {"$inc": {"balance": -g['bet']}})
            await games_col.update_one({"game_id": gid}, {"$set": {"opponent_id": m.from_user.id, "status": "playing", "turn": g['creator_id']}})
            await bot.send_message(g['creator_id'], "Суперник зайшов! Починайте кидати 🎳")
            await m.answer("Ви у грі! Очікуйте хід суперника...", reply_markup=main_menu(m.from_user.id))
            return
    await m.answer("Вітаємо!", reply_markup=main_menu(m.from_user.id))

# --- ПРОФІЛЬ / РЕФЕРАЛКА ---
@dp.message_handler(lambda m: m.text == "👤 Профіль", state="*")
async def profile_handler(m: types.Message):
    u = await get_u(m.from_user.id)
    await m.answer(f"👤 **Профіль**\n\n🆔 ID: `{m.from_user.id}`\n💰 Баланс: {round(u['balance'], 2)} 💎\n👥 Запрошено: {u.get('referred_count', 0)}")

@dp.message_handler(lambda m: m.text == "🤝 Рефералка", state="*")
async def ref_handler(m: types.Message):
    me = await bot.get_me()
    await m.answer(f"🤝 **Рефералка**\nПокликання:\n`https://t.me/{me.username}?start={m.from_user.id}`")

@dp.message_handler(lambda m: m.text == "💎 Баланс", state="*")
async def bal_handler(m: types.Message):
    u = await get_u(m.from_user.id)
    kb = InlineKeyboardMarkup().add(InlineKeyboardButton("💳 Поповнити", callback_data="dep"), InlineKeyboardButton("📤 Вивести", callback_data="withdr"))
    await m.answer(f"💎 Баланс: {round(u['balance'], 2)}", reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data.startswith("adm_ok:"), state="*")
async def admin_ok(c: types.CallbackQuery):
    if c.from_user.id != ADMIN_ID: return
    _, uid, amt = c.data.split(":")
    await users_col.update_one({"_id": int(uid)}, {"$inc": {"balance": float(amt), "total_deposited": float(amt)}})
    await bot.send_message(int(uid), f"✅ Баланс поповнено на {amt} 💎!")
    await c.message.delete()

if __name__ == '__main__':
    executor.start_polling(dp, skip_updates=True)
