import os, uuid, logging, asyncio, time
from aiogram import Bot, Dispatcher, executor, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

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

class DepositState(StatesGroup):
    wait_amount = State()
    wait_receipt = State()

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

# --- СКАСУВАННЯ ---
@dp.message_handler(lambda m: m.text == "❌ Скасувати", state="*")
async def cancel_handler(m: types.Message, state: FSMContext):
    if await state.get_state():
        await state.finish()
        await m.answer("Дію скасовано.", reply_markup=main_menu(m.from_user.id))

# --- СТАРТ ТА ПРИЄДНАННЯ ---
@dp.message_handler(commands=['start'], state="*")
async def start_cmd(m: types.Message, state: FSMContext):
    await state.finish()
    args = m.get_args()
    uid = m.from_user.id
    u = await get_u(uid, m.from_user.full_name)

    if args and args.startswith("game_"):
        gid = args.replace("game_", "")
        g = await games_col.find_one({"game_id": gid, "status": "waiting"})
        if g and g['creator_id'] != uid:
            if u['balance'] < g['bet']: return await m.answer("❌ Недостатньо коштів!")
            
            await users_col.update_one({"_id": uid}, {"$inc": {"balance": -round(float(g['bet']), 2)}})
            await games_col.update_one({"game_id": gid}, {"$set": {
                "opponent_id": uid, 
                "status": "playing", 
                "turn": g['creator_id'],
                "c_score": 0, "o_score": 0,
                "rounds_done": 0, "sub_round": 1 # 1 - кидає творець, 2 - суперник
            }})
            
            await bot.send_message(uid, f"🎮 Гра почалася! Режим: {g['mode']} ігор.")
            await bot.send_message(g['creator_id'], f"🎮 Суперник приєднався! Режим: {g['mode']} ігор.")
            await bot.send_message(g['creator_id'], "Ваш хід 🎳 (копіюйте для кидка)")
            return

    await m.answer("Вітаємо у Different Games!", reply_markup=main_menu(uid))

# --- ЛОГІКА КИДКА ---
@dp.message_handler(content_types=['dice'], state="*")
async def handle_bowling(m: types.Message):
    if m.dice.emoji != "🎳": return
    uid = m.from_user.id
    g = await games_col.find_one({"status": "playing", "$or": [{"creator_id": uid}, {"opponent_id": uid}]})
    if not g: return
    
    if g['turn'] != uid:
        return await m.answer("Зараз не ваш хід!")

    val = m.dice.value
    cid = g['creator_id']
    oid = g['opponent_id']

    if uid == cid: # Кинув творець
        new_score = g['c_score'] + val
        await games_col.update_one({"game_id": g['game_id']}, {"$set": {"c_score": new_score, "turn": oid, "last_val": val}})
        await m.answer(f"У вас вибило {val}, тепер хід суперника")
        await bot.send_message(oid, f"У суперника випало {val}, тепер ваш хід\n\nВаш хід 🎳 (копіюйте для кидка)")
    
    else: # Кинув суперник
        new_score = g['o_score'] + val
        rounds = g['rounds_done'] + 1
        await games_col.update_one({"game_id": g['game_id']}, {"$set": {"o_score": new_score, "turn": cid, "rounds_done": rounds}})
        
        if rounds >= g['mode']: # ГРА ЗАКІНЧЕНА
            win_sum = round(g['bet'] * 2, 2)
            res_c = f"Результат:\nВи: {g['c_score']}\nСуперник: {new_score}"
            res_o = f"Результат:\nВи: {new_score}\nСуперник: {g['c_score']}"
            
            if g['c_score'] > new_score:
                await users_col.update_one({"_id": cid}, {"$inc": {"balance": win_sum}})
                await bot.send_message(cid, f"{res_c}\n\nВітаю ви виграли {win_sum} 💎")
                await bot.send_message(oid, f"{res_o}\n\nНажаль ви програли")
            elif new_score > g['c_score']:
                await users_col.update_one({"_id": oid}, {"$inc": {"balance": win_sum}})
                await bot.send_message(oid, f"{res_o}\n\nВітаю ви виграли {win_sum} 💎")
                await bot.send_message(cid, f"{res_c}\n\nНажаль ви програли")
            else:
                await users_col.update_one({"_id": cid}, {"$inc": {"balance": g['bet']}})
                await users_col.update_one({"_id": oid}, {"$inc": {"balance": g['bet']}})
                await bot.send_message(cid, f"{res_c}\n\n🤝 Нічия! Ставки повернуто.")
                await bot.send_message(oid, f"{res_o}\n\n🤝 Нічия! Ставки повернуто.")
            
            await games_col.update_one({"game_id": g['game_id']}, {"$set": {"status": "finished"}})
        else:
            await m.answer(f"У вас вибило {val}, тепер хід суперника (Раунд {rounds+1}/{g['mode']})")
            await bot.send_message(cid, f"У суперника випало {val}, тепер ваш хід\n\nВаш хід 🎳 (копіюйте для кидка)")

# --- МЕНЮ ГРИ ---
@dp.message_handler(lambda m: m.text == "🎮 Боулінг", state="*")
async def bowling_start(m: types.Message):
    kb = ReplyKeyboardMarkup(resize_keyboard=True).add("1 гра", "5 ігор").add("❌ Скасувати")
    await GameState.wait_mode.set()
    await m.answer("Оберіть режим гри:", reply_markup=kb)

@dp.message_handler(state=GameState.wait_mode)
async def set_mode(m: types.Message, state: FSMContext):
    if m.text in ["1 гра", "5 ігор"]:
        mode = 1 if "1" in m.text else 5
        await state.update_data(m=mode)
        await GameState.wait_bet.set()
        await m.answer("Введіть ставку 💎:", reply_markup=cancel_kb())
    else: await m.answer("Використовуйте кнопки.")

@dp.message_handler(state=GameState.wait_bet)
async def create_game(m: types.Message, state: FSMContext):
    try:
        bet = round(float(m.text.replace(",", ".")), 2)
        u = await get_u(m.from_user.id)
        if u['balance'] < bet: return await m.answer("❌ Недостатньо коштів!")
        
        data = await state.get_data()
        gid = str(uuid.uuid4())[:8]
        link = f"https://t.me/{(await bot.get_me()).username}?start=game_{gid}"
        
        await users_col.update_one({"_id": m.from_user.id}, {"$inc": {"balance": -bet}})
        await games_col.insert_one({
            "game_id": gid, "creator_id": m.from_user.id, "bet": bet, 
            "mode": data['m'], "status": "waiting"
        })
        
        await m.answer(f"🎳 Гра на {data['m']} раундів створена!\n💰 Ставка: {bet} 💎\n\nНадішліть посилання:\n`{link}`", 
                       parse_mode="Markdown", reply_markup=main_menu(m.from_user.id))
        await state.finish()
    except: await m.answer("Введіть число!")

# --- (Інші функції: Профіль, Баланс, Адмінка залишаються без змін з минулого коду) ---
@dp.message_handler(lambda m: m.text == "👤 Профіль", state="*")
async def profile_handler(m: types.Message):
    u = await get_u(m.from_user.id)
    text = (f"👤 **Профіль**\n\n🆔 Ваш ID: `{m.from_user.id}`\n💰 Баланс: {round(u['balance'], 2)} 💎\n"
            f"🤝 Запрошено: {u.get('referrals_count', 0)}\n📥 Поповнено: {round(u.get('total_deposited', 0.0), 2)} 💎")
    await m.answer(text, parse_mode="Markdown")

@dp.message_handler(lambda m: m.text == "🤝 Рефералка", state="*")
async def ref_handler(m: types.Message):
    bot_u = (await bot.get_me()).username
    text = (f"🤝 **Реферальна система**\n\nЗапрошуйте друзів!\n`https://t.me/{bot_u}?start=ref_{m.from_user.id}`\n\n"
            f"Бонус: **0.01%** з виграшу друга.")
    await m.answer(text, parse_mode="Markdown")

@dp.message_handler(lambda m: m.text == "💎 Баланс", state="*")
async def balance_handler(m: types.Message):
    u = await get_u(m.from_user.id)
    kb = InlineKeyboardMarkup().add(InlineKeyboardButton("💳 Поповнити", callback_data="dep"), InlineKeyboardButton("📤 Вивести", callback_data="withdr"))
    await m.answer(f"💰 Ваш баланс: {round(u['balance'], 2)} 💎", reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data == "dep", state="*")
async def dep_start(c: types.CallbackQuery):
    await DepositState.wait_amount.set()
    await bot.send_message(c.from_user.id, "Введіть суму поповнення (💎) (мін. 0.50):", reply_markup=cancel_kb())

@dp.message_handler(state=DepositState.wait_amount)
async def dep_amt(m: types.Message, state: FSMContext):
    try:
        amt = float(m.text.replace(",", "."))
        if amt < 0.50: return await m.answer("❌ Мінімум 0.50")
        await state.update_data(a=amt)
        to_pay = round((amt * RATE) * 1.05, 2)
        await m.answer(f"📝 **Заявка на поповнення**\n1💲= 1 💎 {RATE}\nДо сплати: {to_pay} грн\nКарта: `5355 2800 2890 2177`", reply_markup=cancel_kb())
        await DepositState.wait_receipt.set()
    except: await m.answer("Число!")

@dp.message_handler(state=DepositState.wait_receipt, content_types=['photo'])
async def dep_receipt(m: types.Message, state: FSMContext):
    d = await state.get_data()
    kb = InlineKeyboardMarkup().add(InlineKeyboardButton("✅ Підтвердити", callback_data=f"adm_ok:{m.from_user.id}:{d['a']}"))
    await bot.send_photo(ADMIN_ID, m.photo[-1].file_id, caption=f"📥 {d['a']} 💎 ID: {m.from_user.id}", reply_markup=kb)
    await m.answer("Надіслано адміну.", reply_markup=main_menu(m.from_user.id))
    await state.finish()

@dp.callback_query_handler(lambda c: c.data.startswith("adm_ok:"), state="*")
async def admin_ok(c: types.CallbackQuery):
    if c.from_user.id != ADMIN_ID: return
    _, uid, amt = c.data.split(":")
    await users_col.update_one({"_id": int(uid)}, {"$inc": {"balance": float(amt), "total_deposited": float(amt)}})
    await bot.send_message(int(uid), f"✅ Баланс поповнено на {amt} 💎!")
    await c.message.delete()

if __name__ == '__main__':
    executor.start_polling(dp, skip_updates=True)
