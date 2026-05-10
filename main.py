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

class WithdrawState(StatesGroup):
    wait_amount = State()
    wait_details = State()

class GameState(StatesGroup):
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

# --- СТАРТ ---
@dp.message_handler(commands=['start'], state="*")
async def start_cmd(m: types.Message, state: FSMContext):
    await state.finish()
    args = m.get_args()
    uid = m.from_user.id
    u = await get_u(uid, m.from_user.full_name)

    if args:
        if args.startswith("game_"):
            gid = args.replace("game_", "")
            g = await games_col.find_one({"game_id": gid, "status": "waiting"})
            if g and g['creator_id'] != uid:
                if u['balance'] < g['bet']: return await m.answer("❌ Недостатньо коштів!")
                
                await users_col.update_one({"_id": uid}, {"$inc": {"balance": -round(float(g['bet']), 2)}})
                await games_col.update_one({"game_id": gid}, {"$set": {"opponent_id": uid, "status": "playing"}})
                
                # Початок гри для обох
                await bot.send_message(g['creator_id'], "🎮 Гра почалася! Кидаємо кулі...")
                await bot.send_message(uid, "🎮 Гра почалася! Кидаємо кулі...")

                # Кидок першого (творця)
                v1 = await bot.send_dice(g['creator_id'], emoji="🎳")
                # Кидок другого (суперника)
                v2 = await bot.send_dice(uid, emoji="🎳")
                
                # Дублюємо анімацію обом (щоб бачили результати один одного)
                await bot.send_dice(g['creator_id'], emoji="🎳") # Візуалізація кидка суперника для творця
                await bot.send_dice(uid, emoji="🎳") # Візуалізація кидка творця для суперника

                await asyncio.sleep(4) # Чекаємо анімацію
                
                res1 = v1.dice.value
                res2 = v2.dice.value
                
                win_sum = round(g['bet'] * 2, 2)
                
                if res1 > res2:
                    await users_col.update_one({"_id": g['creator_id']}, {"$inc": {"balance": win_sum}})
                    winner_id, winner_name = g['creator_id'], "Творець"
                    res_text = f"🏆 Переміг творець гри ({res1} vs {res2})!"
                elif res2 > res1:
                    await users_col.update_one({"_id": uid}, {"$inc": {"balance": win_sum}})
                    winner_id, winner_name = uid, "Суперник"
                    res_text = f"🏆 Переміг суперник ({res2} vs {res1})!"
                else:
                    await users_col.update_one({"_id": g['creator_id']}, {"$inc": {"balance": g['bet']}})
                    await users_col.update_one({"_id": uid}, {"$inc": {"balance": g['bet']}})
                    res_text = f"🤝 Нічия ({res1} vs {res2})! Ставки повернуто."
                    winner_id = None

                # Реф бонус
                if winner_id:
                    win_u = await users_col.find_one({"_id": winner_id})
                    if win_u.get("invited_by"):
                        bonus = round(win_sum * 0.0001, 6)
                        await users_col.update_one({"_id": win_u["invited_by"]}, {"$inc": {"balance": bonus}})

                await bot.send_message(g['creator_id'], res_text)
                await bot.send_message(uid, res_text)
                await games_col.update_one({"game_id": gid}, {"$set": {"status": "finished", "winner": winner_id}})
                return

        elif args.startswith("ref_"):
            ref_id = int(args.split("_")[1])
            if ref_id != uid and not u.get("invited_by"):
                await users_col.update_one({"_id": uid}, {"$set": {"invited_by": ref_id}})
                await users_col.update_one({"_id": ref_id}, {"$inc": {"referrals_count": 1}})

    await m.answer("Вітаємо у Different Games!", reply_markup=main_menu(uid))

# --- ПРОФІЛЬ ---
@dp.message_handler(lambda m: m.text == "👤 Профіль", state="*")
async def profile_handler(m: types.Message):
    u = await get_u(m.from_user.id)
    text = (f"👤 **Профіль**\n\n🆔 Ваш ID: `{m.from_user.id}`\n💰 Баланс: {round(u['balance'], 2)} 💎\n"
            f"🤝 Запрошено: {u.get('referrals_count', 0)}\n📥 Поповнено: {round(u.get('total_deposited', 0.0), 2)} 💎")
    await m.answer(text, parse_mode="Markdown")

# --- РЕФЕРАЛКА ---
@dp.message_handler(lambda m: m.text == "🤝 Рефералка", state="*")
async def ref_handler(m: types.Message):
    bot_u = (await bot.get_me()).username
    text = (f"🤝 **Реферальна система**\n\nЗапрошуйте друзів та получайте процент з них!\n\nВаше посилання:\n`https://t.me/{bot_u}?start=ref_{m.from_user.id}`\n\n"
            f"Коли ви запросили друга по силці то після кожного його виграшу вам нараховується **0.01%** з його виграшу!")
    await m.answer(text, parse_mode="Markdown")

# --- БАЛАНС / ПОПОВНЕННЯ (ФОРМАТ) ---
@dp.message_handler(lambda m: m.text == "💎 Баланс", state="*")
async def balance_handler(m: types.Message):
    u = await get_u(m.from_user.id)
    kb = InlineKeyboardMarkup(row_width=2).add(
        InlineKeyboardButton("💳 Поповнити", callback_data="dep"),
        InlineKeyboardButton("📤 Вивести", callback_data="withdr")
    )
    await m.answer(f"💰 Ваш баланс: {round(u['balance'], 2)} 💎", reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data == "dep", state="*")
async def dep_start(c: types.CallbackQuery):
    await DepositState.wait_amount.set()
    await bot.send_message(c.from_user.id, "Введіть суму поповнення (💎) (мін. 0.50):", reply_markup=cancel_kb())

@dp.message_handler(state=DepositState.wait_amount)
async def dep_amt(m: types.Message, state: FSMContext):
    if m.text == "❌ Скасувати": return
    try:
        amt = float(m.text.replace(",", "."))
        if amt < 0.50: return await m.answer("❌ Мінімум 0.50 💎")
        await state.update_data(a=amt)
        to_pay = round((amt * RATE) * 1.05, 2)
        text = (f"📝 **Заявка на поповнення**\n"
                f"1💲= 1 💎 {RATE}\n"
                f"До сплати: {to_pay} грн (+5%)\n\n"
                f"Карта: `5355 2800 2890 2177`\n\n"
                f"(Обовʼязково квитанцію сюди кидайте для підтвердження оплати)")
        await m.answer(text, parse_mode="Markdown")
        await DepositState.wait_receipt.set()
    except: await m.answer("Введіть число!")

@dp.message_handler(state=DepositState.wait_receipt, content_types=['photo'])
async def dep_receipt(m: types.Message, state: FSMContext):
    d = await state.get_data()
    kb = InlineKeyboardMarkup().add(InlineKeyboardButton("✅ Підтвердити", callback_data=f"adm_ok:{m.from_user.id}:{d['a']}"))
    await bot.send_photo(ADMIN_ID, m.photo[-1].file_id, caption=f"📥 Поповнення {d['a']} 💎 від {m.from_user.id}", reply_markup=kb)
    await m.answer("✅ Квитанцію надіслано. Очікуйте підтвердження.", reply_markup=main_menu(m.from_user.id))
    await state.finish()

# --- ВИВІД ---
@dp.callback_query_handler(lambda c: c.data == "withdr", state="*")
async def withdr_start(c: types.CallbackQuery):
    await WithdrawState.wait_amount.set()
    await bot.send_message(c.from_user.id, "Введіть суму виводу 💎:", reply_markup=cancel_kb())

@dp.message_handler(state=WithdrawState.wait_amount)
async def withdr_amt(m: types.Message, state: FSMContext):
    if m.text == "❌ Скасувати": return
    try:
        amt = float(m.text.replace(",", "."))
        u = await get_u(m.from_user.id)
        if u['balance'] < amt: return await m.answer("❌ Недостатньо коштів!")
        await state.update_data(a=amt)
        await m.answer("Введіть номер карти:", reply_markup=cancel_kb())
        await WithdrawState.wait_details.set()
    except: await m.answer("Введіть число!")

@dp.message_handler(state=WithdrawState.wait_details)
async def withdr_fin(m: types.Message, state: FSMContext):
    if m.text == "❌ Скасувати": return
    d = await state.get_data()
    await users_col.update_one({"_id": m.from_user.id}, {"$inc": {"balance": -d['a']}})
    await bot.send_message(ADMIN_ID, f"📤 **ВИВІД**\n💎 {d['a']} 💎\n💳 `{m.text}`\n🆔 {m.from_user.id}")
    await m.answer("✅ Заявка на вивід прийнята.", reply_markup=main_menu(m.from_user.id))
    await state.finish()

# --- ІГРИ (БОУЛІНГ) ---
@dp.message_handler(lambda m: m.text == "🎮 Боулінг", state="*")
async def game_start_menu(m: types.Message):
    await GameState.wait_bet.set()
    await m.answer("Введіть ставку 💎 для гри в Боулінг:", reply_markup=cancel_kb())

@dp.message_handler(state=GameState.wait_bet)
async def game_create(m: types.Message, state: FSMContext):
    if m.text == "❌ Скасувати": return
    try:
        bet = round(float(m.text.replace(",", ".")), 2)
        u = await get_u(m.from_user.id)
        if u['balance'] < bet: return await m.answer("❌ Недостатньо коштів!")
        
        gid = str(uuid.uuid4())[:8]
        bot_u = (await bot.get_me()).username
        link = f"https://t.me/{bot_u}?start=game_{gid}"
        
        await m.answer(f"🎳 Гра створена!\n💰 Ставка: {bet} 💎\n\nНадішліть посилання другу:\n`{link}`", parse_mode="Markdown", reply_markup=main_menu(m.from_user.id))
        
        await users_col.update_one({"_id": m.from_user.id}, {"$inc": {"balance": -bet}})
        await games_col.insert_one({"game_id": gid, "creator_id": m.from_user.id, "bet": bet, "status": "waiting"})
        await state.finish()
    except: await m.answer("Введіть число!")

# --- АДМІНКА ---
@dp.callback_query_handler(lambda c: c.data.startswith("adm_ok:"), state="*")
async def admin_ok(c: types.CallbackQuery):
    if c.from_user.id != ADMIN_ID: return
    _, uid, amt = c.data.split(":")
    await users_col.update_one({"_id": int(uid)}, {"$inc": {"balance": float(amt), "total_deposited": float(amt)}})
    await bot.send_message(int(uid), f"✅ Баланс поповнено на {amt} 💎!")
    await c.message.delete()

if __name__ == '__main__':
    executor.start_polling(dp, skip_updates=True)
