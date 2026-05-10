import os, uuid, logging, asyncio, time
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
    wait_mode = State()
    wait_bet = State()

# --- КЛАВІАТУРИ ---
def main_menu(user_id):
    markup = ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add(KeyboardButton("👤 Профіль"), KeyboardButton("🎮 Боулінг"))
    markup.add(KeyboardButton("💎 Баланс"), KeyboardButton("🤝 Рефералка"))
    if user_id == ADMIN_ID:
        markup.add(KeyboardButton("🛡 Панель адміна"))
    return markup

def cancel_kb():
    return ReplyKeyboardMarkup(resize_keyboard=True).add(KeyboardButton("❌ Скасувати"))

async def get_u(user_id, name="Користувач"):
    u = await users_col.find_one({"_id": user_id})
    if not u:
        u = {"_id": user_id, "nickname": name, "balance": 0.0, "invited_by": None, "referrals_count": 0, "total_deposited": 0.0}
        await users_col.insert_one(u)
    return u

# --- ОБРОБКА КИДКА БОУЛІНГА (ЦЕНТРАЛЬНА ЛОГІКА) ---
@dp.message_handler(content_types=types.ContentTypes.DICE, state="*")
async def handle_bowling_dice(m: types.Message):
    if m.dice.emoji != "🎳": return
    
    uid = m.from_user.id
    g = await games_col.find_one({"status": "playing", "$or": [{"creator_id": uid}, {"opponent_id": uid}]})
    
    if not g: return
    if g['turn'] != uid:
        return await m.answer("Зараз не ваш хід! Очікуйте суперника.")

    val = m.dice.value
    cid, oid = g['creator_id'], g['opponent_id']
    gid = g['game_id']
    
    # Хід Креатора (Гравець 1)
    if uid == cid:
        new_score = g.get('c_score', 0) + val
        await games_col.update_one({"game_id": gid}, {"$set": {"c_score": new_score, "turn": oid}})
        
        await bot.send_message(cid, f"Бот: у вас випало {val}, тепер хід суперника")
        await bot.send_message(oid, f"Бот: у суперника випало {val}, тепер ваш хід\n\nВаш хід `🎳` (натисніть щоб копіювати)", parse_mode="MarkdownV2")
    
    # Хід Суперника (Гравець 2)
    else:
        new_score = g.get('o_score', 0) + val
        rounds_done = g.get('rounds_done', 0) + 1
        
        if rounds_done >= g['mode']:
            # ФІНАЛ ГРИ
            win_sum = round(g['bet'] * 2, 2)
            total_c, total_o = g['c_score'], new_score
            
            res_base = "Результат:\nВи: {y}\nСуперник: {o}"
            
            if total_c > total_o:
                await users_col.update_one({"_id": cid}, {"$inc": {"balance": win_sum}})
                await bot.send_message(cid, f"{res_base.format(y=total_c, o=total_o)}\n\nВітаю ви виграли {win_sum} 💎")
                await bot.send_message(oid, f"{res_base.format(y=total_o, o=total_c)}\n\nНажаль ви програли")
            elif total_o > total_c:
                await users_col.update_one({"_id": oid}, {"$inc": {"balance": win_sum}})
                await bot.send_message(oid, f"{res_base.format(y=total_o, o=total_c)}\n\nВітаю ви виграли {win_sum} 💎")
                await bot.send_message(cid, f"{res_base.format(y=total_c, o=total_o)}\n\nНажаль ви програли")
            else:
                await users_col.update_many({"_id": {"$in": [cid, oid]}}, {"$inc": {"balance": g['bet']}})
                await bot.send_message(cid, f"Нічия {total_c}:{total_o}! Ставку повернуто.")
                await bot.send_message(oid, f"Нічия {total_o}:{total_c}! Ставку повернуто.")
            
            await games_col.update_one({"game_id": gid}, {"$set": {"status": "finished"}})
        else:
            # ПЕРЕХІД НА НАСТУПНИЙ МАТЧ
            await games_col.update_one({"game_id": gid}, {"$set": {"o_score": new_score, "turn": cid, "rounds_done": rounds_done}})
            await bot.send_message(oid, f"Бот: у вас випало {val}, тепер хід суперника")
            await bot.send_message(cid, f"Бот: у суперника випало {val}, тепер ваш хід (Матч {rounds_done+1}/{g['mode']})\n\nВаш хід `🎳` (натисніть щоб копіювати)", parse_mode="MarkdownV2")

# --- СИСТЕМА ПОПОВНЕННЯ ТА ВИВОДУ ---
@dp.callback_query_handler(lambda c: c.data == "dep", state="*")
async def dep_start(c: types.CallbackQuery):
    await DepositState.wait_amount.set()
    await bot.send_message(c.from_user.id, "Введіть суму поповнення (💎):", reply_markup=cancel_kb())

@dp.message_handler(state=DepositState.wait_amount)
async def dep_amt(m: types.Message, state: FSMContext):
    if m.text == "❌ Скасувати":
        await state.finish()
        return await m.answer("Скасовано", reply_markup=main_menu(m.from_user.id))
    try:
        amt = float(m.text.replace(",", "."))
        to_pay = round((amt * RATE) * 1.05, 2)
        await state.update_data(a=amt)
        text = (f"📝 **Заявка на поповнення**\n1💲= 1 💎 {RATE}\nДо сплати: {to_pay} грн (+5%)\n"
                f"Карта: `5355 2800 2890 2177`\n\nНадішліть квитанцію (фото):")
        await m.answer(text, parse_mode="Markdown")
        await DepositState.wait_receipt.set()
    except: await m.answer("Введіть число!")

@dp.message_handler(state=DepositState.wait_receipt, content_types=['photo'])
async def dep_rec(m: types.Message, state: FSMContext):
    d = await state.get_data()
    kb = InlineKeyboardMarkup().add(InlineKeyboardButton("✅ Підтвердити", callback_data=f"adm_ok:{m.from_user.id}:{d['a']}"))
    await bot.send_photo(ADMIN_ID, m.photo[-1].file_id, caption=f"📥 Поповнення {d['a']} 💎 від {m.from_user.id}", reply_markup=kb)
    await m.answer("✅ Надіслано адміну.", reply_markup=main_menu(m.from_user.id))
    await state.finish()

@dp.callback_query_handler(lambda c: c.data == "withdr", state="*")
async def withdr_start(c: types.CallbackQuery):
    await WithdrawState.wait_amount.set()
    await bot.send_message(c.from_user.id, "Введіть суму виводу 💎:", reply_markup=cancel_kb())

@dp.message_handler(state=WithdrawState.wait_amount)
async def withdr_amt(m: types.Message, state: FSMContext):
    if m.text == "❌ Скасувати":
        await state.finish()
        return await m.answer("Скасовано", reply_markup=main_menu(m.from_user.id))
    try:
        amt = float(m.text.replace(",", "."))
        u = await get_u(m.from_user.id)
        if u['balance'] < amt: return await m.answer("❌ Недостатньо коштів!")
        await state.update_data(a=amt)
        await m.answer("Введіть номер карти для виплати:", reply_markup=cancel_kb())
        await WithdrawState.wait_details.set()
    except: await m.answer("Введіть число!")

@dp.message_handler(state=WithdrawState.wait_details)
async def withdr_fin(m: types.Message, state: FSMContext):
    if m.text == "❌ Скасувати":
        await state.finish()
        return await m.answer("Скасовано", reply_markup=main_menu(m.from_user.id))
    d = await state.get_data()
    await users_col.update_one({"_id": m.from_user.id}, {"$inc": {"balance": -d['a']}})
    await bot.send_message(ADMIN_ID, f"📤 **ВИВІД**\n💎 {d['a']} 💎\n💳 `{m.text}`\n🆔 {m.from_user.id}")
    await m.answer("✅ Заявку на вивід прийнято.", reply_markup=main_menu(m.from_user.id))
    await state.finish()

# --- СТАРТ ТА СТВОРЕННЯ ГРИ ---
@dp.message_handler(commands=['start'], state="*")
async def start_cmd(m: types.Message, state: FSMContext):
    await state.finish()
    args = m.get_args()
    if args and args.startswith("game_"):
        gid = args.replace("game_", "")
        g = await games_col.find_one({"game_id": gid, "status": "waiting"})
        u = await get_u(m.from_user.id)
        if g and g['creator_id'] != m.from_user.id:
            if u['balance'] < g['bet']: return await m.answer("❌ Недостатньо коштів!")
            await users_col.update_one({"_id": m.from_user.id}, {"$inc": {"balance": -g['bet']}})
            await games_col.update_one({"game_id": gid}, {"$set": {"opponent_id": m.from_user.id, "status": "playing", "turn": g['creator_id'], "c_score": 0, "o_score": 0, "rounds_done": 0}})
            
            info = f"Нова гра в Боулінг 🎳\nМатчів: {g['mode']}\nСтавка: {g['bet']} 💎\nВиграш: {round(g['bet']*2, 2)} 💎"
            await bot.send_message(g['creator_id'], info)
            await bot.send_message(m.from_user.id, info)
            await bot.send_message(g['creator_id'], "Ваш хід `🎳` (натисніть щоб копіювати)", parse_mode="MarkdownV2")
            return
    await m.answer("Вітаємо у Different Games!", reply_markup=main_menu(m.from_user.id))

@dp.message_handler(lambda m: m.text == "🎮 Боулінг", state="*")
async def bowl_menu(m: types.Message):
    kb = ReplyKeyboardMarkup(resize_keyboard=True).add("1 гра", "5 ігор", "❌ Скасувати")
    await GameState.wait_mode.set()
    await m.answer("Оберіть режим:", reply_markup=kb)

@dp.message_handler(state=GameState.wait_mode)
async def bowl_mode(m: types.Message, state: FSMContext):
    if m.text == "❌ Скасувати":
        await state.finish()
        return await m.answer("Скасовано", reply_markup=main_menu(m.from_user.id))
    mode = 1 if "1" in m.text else 5
    await state.update_data(m=mode)
    await GameState.wait_bet.set()
    await m.answer("Введіть ставку 💎:", reply_markup=cancel_kb())

@dp.message_handler(state=GameState.wait_bet)
async def bowl_bet(m: types.Message, state: FSMContext):
    if m.text == "❌ Скасувати":
        await state.finish()
        return await m.answer("Скасовано", reply_markup=main_menu(m.from_user.id))
    try:
        bet = round(float(m.text.replace(",", ".")), 2)
        u = await get_u(m.from_user.id)
        if u['balance'] < bet: return await m.answer("❌ Мало коштів!")
        
        d = await state.get_data(); gid = str(uuid.uuid4())[:8]
        await users_col.update_one({"_id": m.from_user.id}, {"$inc": {"balance": -bet}})
        await games_col.insert_one({"game_id": gid, "creator_id": m.from_user.id, "bet": bet, "mode": d['m'], "status": "waiting"})
        
        link = f"https://t.me/{(await bot.get_me()).username}?start=game_{gid}"
        await m.answer(f"🎳 Гра на {d['m']} матч(ів) створена!\nПосилання: `{link}`", parse_mode="Markdown", reply_markup=main_menu(m.from_user.id))
        await state.finish()
    except: await m.answer("Введіть число!")

# --- ПРОФІЛЬ ТА АДМІНКА ---
@dp.message_handler(lambda m: m.text == "👤 Профіль", state="*")
async def profile(m: types.Message):
    u = await get_u(m.from_user.id)
    await m.answer(f"👤 Профіль\n🆔 ID: `{m.from_user.id}`\n💰 Баланс: {round(u['balance'], 2)} 💎", parse_mode="Markdown")

@dp.message_handler(lambda m: m.text == "💎 Баланс", state="*")
async def balance(m: types.Message):
    u = await get_u(m.from_user.id)
    kb = InlineKeyboardMarkup().add(InlineKeyboardButton("💳 Поповнити", callback_data="dep"), InlineKeyboardButton("📤 Вивести", callback_data="withdr"))
    await m.answer(f"💰 Ваш баланс: {round(u['balance'], 2)} 💎", reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data.startswith("adm_ok:"), state="*")
async def admin_ok(c: types.CallbackQuery):
    if c.from_user.id != ADMIN_ID: return
    _, uid, amt = c.data.split(":")
    await users_col.update_one({"_id": int(uid)}, {"$inc": {"balance": float(amt), "total_deposited": float(amt)}})
    await bot.send_message(int(uid), f"✅ Баланс поповнено на {amt} 💎!")
    await c.message.delete()

if __name__ == '__main__':
    executor.start_polling(dp, skip_updates=True)
