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

RATE = 44.50  
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
    wait_mode = State()  # Вибір матчу
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
        u = {"_id": user_id, "balance": 0.0, "total_deposited": 0.0, "referred_count": 0, "referrer": referrer}
        await users_col.insert_one(u)
        if referrer:
            try: await users_col.update_one({"_id": int(referrer)}, {"$inc": {"referred_count": 1}})
            except: pass
    return u

# --- СИСТЕМА СКАСУВАННЯ ---
@dp.message_handler(lambda m: m.text == "❌ Скасувати", state="*")
async def universal_cancel(m: types.Message, state: FSMContext):
    await state.finish()
    await m.answer("Повернення в головне меню.", reply_markup=main_menu(m.from_user.id))

# --- МЕНЮ GAMES ---
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
    await m.answer(f"Ви обрали {m.text}. Тепер введіть суму ставки 💎:", reply_markup=cancel_kb())

# --- ЛОГІКА ГРИ (БОУЛІНГ) ---
@dp.message_handler(content_types=types.ContentTypes.DICE, state="*")
async def handle_bowling_dice(m: types.Message):
    if m.dice.emoji != "🎳": return
    uid = m.from_user.id
    g = await games_col.find_one({"status": "playing", "$or": [{"creator_id": uid}, {"opponent_id": uid}]})
    if not g: return 

    if g.get('turn') != uid:
        return await m.answer("Зараз не ваш хід!")

    val = m.dice.value
    cid, oid, gid = g['creator_id'], g['opponent_id'], g['game_id']
    
    if uid == cid:
        await games_col.update_one({"game_id": gid}, {"$set": {"c_score": val, "turn": oid}})
        await m.answer(f"Ваш результат: {val}. Чекаємо суперника...")
        await bot.send_message(oid, f"Суперник кинув на {val}. Ваш хід 🎳!")
    else:
        c_score = g.get('c_score', 0)
        win_sum = round(g['bet'] * 2, 2)
        await games_col.update_one({"game_id": gid}, {"$set": {"status": "finished", "o_score": val}})

        if c_score > val:
            await users_col.update_one({"_id": cid}, {"$inc": {"balance": win_sum}})
            msg_c, msg_o = f"🎉 Перемога! +{win_sum} 💎 на баланс.", "😢 Ви програли."
        elif val > c_score:
            await users_col.update_one({"_id": oid}, {"$inc": {"balance": win_sum}})
            msg_o, msg_c = f"🎉 Перемога! +{win_sum} 💎 на баланс.", "😢 Ви програли."
        else:
            await users_col.update_many({"_id": {"$in": [cid, oid]}}, {"$inc": {"balance": g['bet']}})
            msg_c = msg_o = "🤝 Нічия! Ставки повернуто."

        await bot.send_message(cid, f"Результат: Ви {c_score} vs {val}\n{msg_c}")
        await bot.send_message(oid, f"Результат: Ви {val} vs {c_score}\n{msg_o}")

# --- ПОПОВНЕННЯ (Нормальна заявка) ---
@dp.callback_query_handler(lambda c: c.data == "dep", state="*")
async def dep_start(c: types.CallbackQuery):
    await DepositState.wait_amount.set()
    await bot.send_message(c.from_user.id, "💎 Введіть суму в 💎, яку хочете отримати (мін. 0.50):", reply_markup=cancel_kb())

@dp.message_handler(state=DepositState.wait_amount)
async def dep_amount(m: types.Message, state: FSMContext):
    try:
        amt = float(m.text.replace(',', '.'))
        if amt < 0.50: return await m.answer("❌ Мінімум 0.50 💎")
        to_pay = round((amt * RATE) * 1.05, 2)
        await state.update_data(amt=amt)
        
        text = (
            f"➖➖➖➖➖➖➖➖➖➖\n"
            f"📥 **ЗАЯВКА НА ПОПОВНЕННЯ**\n"
            f"➖➖➖➖➖➖➖➖➖➖\n"
            f"💎 **До отримання:** {amt} 💎\n"
            f"💳 **До сплати:** {to_pay} UAH\n\n"
            f"📌 **Реквізити для оплати:**\n"
            f"`5355 2800 2890 2177` (Mono)\n\n"
            f"⚠️ **Важливо:**\n"
            f"Після переказу обов'язково надішліть **скріншот квитанції** сюди в чат. "
            f"Адміністратор перевірить оплату та нарахує кошти."
        )
        await m.answer(text, parse_mode="Markdown")
        await DepositState.wait_receipt.set()
    except: await m.answer("Введіть число!")

@dp.message_handler(state=DepositState.wait_receipt, content_types=['photo'])
async def dep_receipt(m: types.Message, state: FSMContext):
    d = await state.get_data()
    kb = InlineKeyboardMarkup().add(InlineKeyboardButton("✅ Підтвердити", callback_data=f"adm_ok:{m.from_user.id}:{d['amt']}"))
    await bot.send_photo(ADMIN_ID, m.photo[-1].file_id, caption=f"💰 Поповнення: {d['amt']} 💎\nID: {m.from_user.id}", reply_markup=kb)
    await m.answer("✅ Ваша квитанція прийнята на розгляд! Очікуйте повідомлення про нарахування.", reply_markup=main_menu(m.from_user.id))
    await state.finish()

# --- СТАРТ ТА СТВОРЕННЯ ГРИ ---
@dp.message_handler(state=GameState.wait_bet)
async def set_bet(m: types.Message, state: FSMContext):
    try:
        bet = float(m.text.replace(',', '.'))
        u = await get_u(m.from_user.id)
        if u['balance'] < bet: return await m.answer("❌ Недостатньо коштів!")
        
        data = await state.get_data()
        mode = data.get('mode')
        gid = str(uuid.uuid4())[:8]
        
        await users_col.update_one({"_id": m.from_user.id}, {"$inc": {"balance": -bet}})
        await games_col.insert_one({"game_id": gid, "creator_id": m.from_user.id, "bet": bet, "status": "waiting", "mode": mode})
        
        me = await bot.get_me()
        await m.answer(f"🎳 **Гра створена ({mode})!**\nНадішліть посилання другу:\n`https://t.me/{me.username}?start=game_{gid}`", reply_markup=main_menu(m.from_user.id), parse_mode="Markdown")
        await state.finish()
    except: await m.answer("Введіть число!")

# --- БАЛАНС / ПРОФІЛЬ / РЕФЕРАЛКА ---
@dp.message_handler(lambda m: m.text == "💎 Баланс", state="*")
async def balance_menu(m: types.Message):
    u = await get_u(m.from_user.id)
    kb = InlineKeyboardMarkup().add(InlineKeyboardButton("💳 Поповнити", callback_data="dep"), InlineKeyboardButton("📤 Вивести", callback_data="withdr"))
    await m.answer(f"💎 Ваш баланс: {round(u['balance'], 2)} 💎", reply_markup=kb)

@dp.message_handler(lambda m: m.text == "👤 Профіль", state="*")
async def profile_handler(m: types.Message):
    u = await get_u(m.from_user.id)
    await m.answer(f"👤 **Ваш профіль**\n\n🆔 ID: `{m.from_user.id}`\n💰 Баланс: {round(u['balance'], 2)} 💎\n👥 Рефералів: {u.get('referred_count', 0)}", parse_mode="Markdown")

@dp.message_handler(lambda m: m.text == "🤝 Рефералка", state="*")
async def ref_handler(m: types.Message):
    me = await bot.get_me()
    await m.answer(f"🤝 **Реферальна програма**\n\nТвоє посилання:\n`https://t.me/{me.username}?start={m.from_user.id}`", parse_mode="Markdown")

@dp.message_handler(commands=['start'], state="*")
async def start_cmd(m: types.Message):
    args = m.get_args()
    ref = args if (args and args.isdigit()) else None
    u = await get_u(m.from_user.id, ref)
    if args and args.startswith("game_"):
        gid = args.replace("game_", "")
        g = await games_col.find_one({"game_id": gid, "status": "waiting"})
        if g and g['creator_id'] != m.from_user.id:
            if u['balance'] < g['bet']: return await m.answer("❌ Недостатньо коштів!")
            await users_col.update_one({"_id": m.from_user.id}, {"$inc": {"balance": -g['bet']}})
            await games_col.update_one({"game_id": gid}, {"$set": {"opponent_id": m.from_user.id, "status": "playing", "turn": g['creator_id']}})
            await bot.send_message(g['creator_id'], "Суперник прийняв виклик! Ваш хід 🎳")
            await m.answer("Ви прийняли виклик! Чекаємо хід суперника...", reply_markup=main_menu(m.from_user.id))
            return
    await m.answer("Вітаємо у Different Games!", reply_markup=main_menu(m.from_user.id))

@dp.callback_query_handler(lambda c: c.data.startswith("adm_ok:"), state="*")
async def admin_confirm(c: types.CallbackQuery):
    if c.from_user.id != ADMIN_ID: return
    _, uid, amt = c.data.split(":")
    await users_col.update_one({"_id": int(uid)}, {"$inc": {"balance": float(amt), "total_deposited": float(amt)}})
    await bot.send_message(int(uid), f"✅ Баланс поповнено на {amt} 💎!")
    await c.message.delete()

if __name__ == '__main__':
    executor.start_polling(dp, skip_updates=True)
