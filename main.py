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

RATE = 44.50  # Курс за 1 💎
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
    wait_bet = State()

# --- КЛАВІАТУРИ ---
def main_menu(user_id):
    markup = ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add(KeyboardButton("👤 Профіль"), KeyboardButton("🎮 Боулінг"))
    markup.add(KeyboardButton("💎 Баланс"))
    if user_id == ADMIN_ID:
        markup.add(KeyboardButton("🛡 Панель адміна"))
    return markup

def cancel_kb():
    return ReplyKeyboardMarkup(resize_keyboard=True).add(KeyboardButton("❌ Скасувати"))

async def get_u(user_id):
    u = await users_col.find_one({"_id": user_id})
    if not u:
        u = {"_id": user_id, "balance": 0.0, "total_deposited": 0.0}
        await users_col.insert_one(u)
    return u

# --- СКАСУВАННЯ ---
@dp.message_handler(lambda m: m.text == "❌ Скасувати", state="*")
async def cancel_handler(m: types.Message, state: FSMContext):
    await state.finish()
    await m.answer("Дію скасовано.", reply_markup=main_menu(m.from_user.id))

# --- ЛОГІКА ГРИ (БОУЛІНГ) ---
@dp.message_handler(content_types=types.ContentTypes.DICE, state="*")
async def handle_bowling_dice(m: types.Message):
    if m.dice.emoji != "🎳": return
    
    uid = m.from_user.id
    # Шукаємо тільки АКТИВНУ гру
    g = await games_col.find_one({"status": "playing", "$or": [{"creator_id": uid}, {"opponent_id": uid}]})
    
    # ПОВНЕ ІГНОРУВАННЯ, якщо немає активної гри
    if not g: return 

    if g.get('turn') != uid:
        return await m.answer("Зараз не ваш хід! Зачекайте суперника.")

    val = m.dice.value
    cid, oid = g['creator_id'], g['opponent_id']
    gid = g['game_id']
    
    if uid == cid:
        await games_col.update_one({"game_id": gid}, {"$set": {"c_score": val, "turn": oid}})
        await m.answer(f"У вас вибило {val}\nТепер хід суперника ⏳")
        await bot.send_message(oid, f"У суперника випало {val}\nТепер ваш хід 🎳: `🎳`", parse_mode="Markdown")
    else:
        c_score = g.get('c_score', 0)
        o_score = val
        win_sum = round(g['bet'] * 2, 2)
        
        # Закриваємо гру перед нарахуванням
        await games_col.update_one({"game_id": gid}, {"$set": {"status": "finished", "o_score": val}})

        if c_score > o_score:
            await users_col.update_one({"_id": cid}, {"$inc": {"balance": win_sum}})
            res_c, res_o = f"Вітаю 🎉 Ви перемогли!\nНа баланс нараховано виграш {win_sum} 💎", "Нажаль ви програли 😢"
        elif o_score > c_score:
            await users_col.update_one({"_id": oid}, {"$inc": {"balance": win_sum}})
            res_o, res_c = f"Вітаю 🎉 Ви перемогли!\nНа баланс нараховано виграш {win_sum} 💎", "Нажаль ви програли 😢"
        else:
            await users_col.update_many({"_id": {"$in": [cid, oid]}}, {"$inc": {"balance": g['bet']}})
            res_c = res_o = "Нічия! Ставки повернуто 🤝"

        await bot.send_message(cid, f"Результат:\nВи: {c_score}\nСуперник: {o_score}\n\n{res_c}\n\nГра закінчена")
        await bot.send_message(oid, f"Результат:\nВи: {o_score}\nСуперник: {c_score}\n\n{res_o}\n\nГра закінчена")

# --- ПРОФІЛЬ ТА БАЛАНС ---
@dp.message_handler(lambda m: m.text == "👤 Профіль", state="*")
async def profile_handler(m: types.Message):
    u = await get_u(m.from_user.id)
    text = (f"👤 **Ваш профіль**\n\n"
            f"🆔 ID: `{m.from_user.id}`\n"
            f"💰 Баланс: {round(u['balance'], 2)} 💎\n"
            f"📥 Всього поповнено: {u.get('total_deposited', 0)} 💎")
    await m.answer(text, parse_mode="Markdown", reply_markup=main_menu(m.from_user.id))

@dp.message_handler(lambda m: m.text == "💎 Баланс", state="*")
async def balance_menu(m: types.Message):
    u = await get_u(m.from_user.id)
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(InlineKeyboardButton("💳 Поповнити", callback_data="dep"),
           InlineKeyboardButton("📤 Вивести", callback_data="withdr"))
    await m.answer(f"💎 Ваш поточний баланс: {round(u['balance'], 2)} 💎", reply_markup=kb)

# --- СИСТЕМА ПОПОВНЕННЯ (Мін 0.50) ---
@dp.callback_query_handler(lambda c: c.data == "dep", state="*")
async def dep_start(c: types.CallbackQuery):
    await DepositState.wait_amount.set()
    await bot.send_message(c.from_user.id, "Введіть суму поповнення в 💎 (мін. 0.50):", reply_markup=cancel_kb())

@dp.message_handler(state=DepositState.wait_amount)
async def dep_amount(m: types.Message, state: FSMContext):
    try:
        amt = float(m.text.replace(',', '.'))
        if amt < 0.50: return await m.answer("❌ Мінімальна сума поповнення — 0.50 💎")
        
        to_pay = round((amt * RATE) * 1.05, 2) # Комісія 5%
        await state.update_data(amt=amt)
        
        text = (f"📝 **Заявка на поповнення**\n\n"
                f"💎 До отримання: {amt} 💎\n"
                f"💳 До сплати: {to_pay} грн\n\n"
                f"📌 Реквізити (Карта): `5355 2800 2890 2177`\n\n"
                f"Після оплати надішліть **ФОТО квитанції**:")
        await m.answer(text, parse_mode="Markdown")
        await DepositState.wait_receipt.set()
    except: await m.answer("Введіть число! Наприклад: 0.5 чи 10")

@dp.message_handler(state=DepositState.wait_receipt, content_types=['photo'])
async def dep_receipt(m: types.Message, state: FSMContext):
    data = await state.get_data()
    kb = InlineKeyboardMarkup().add(InlineKeyboardButton("✅ Підтвердити", callback_data=f"adm_ok:{m.from_user.id}:{data['amt']}"))
    await bot.send_photo(ADMIN_ID, m.photo[-1].file_id, caption=f"📥 Поповнення {data['amt']} 💎\nID: {m.from_user.id}", reply_markup=kb)
    await m.answer("✅ Квитанцію надіслано адміну. Очікуйте нарахування.", reply_markup=main_menu(m.from_user.id))
    await state.finish()

# --- СИСТЕМА ВИВЕДЕННЯ (Мін 4) ---
@dp.callback_query_handler(lambda c: c.data == "withdr", state="*")
async def withdr_start(c: types.CallbackQuery):
    u = await get_u(c.from_user.id)
    if u['balance'] < 4: 
        return await bot.send_message(c.from_user.id, "❌ Мінімальна сума для виведення — 4 💎")
    
    await WithdrawState.wait_amount.set()
    await bot.send_message(c.from_user.id, "Введіть суму для виведення 💎 (мін. 4):", reply_markup=cancel_kb())

@dp.message_handler(state=WithdrawState.wait_amount)
async def withdr_amt(m: types.Message, state: FSMContext):
    try:
        amt = float(m.text.replace(',', '.'))
        u = await get_u(m.from_user.id)
        if amt < 4: return await m.answer("❌ Мінімальна сума виводу — 4 💎")
        if amt > u['balance']: return await m.answer("❌ Недостатньо коштів на балансі!")
        
        await state.update_data(amt=amt)
        await m.answer("Введіть номер вашої карти для виплати:")
        await WithdrawState.wait_details.set()
    except: await m.answer("Введіть число!")

@dp.message_handler(state=WithdrawState.wait_details)
async def withdr_fin(m: types.Message, state: FSMContext):
    data = await state.get_data()
    await users_col.update_one({"_id": m.from_user.id}, {"$inc": {"balance": -data['amt']}})
    
    await bot.send_message(ADMIN_ID, f"📤 **ЗАЯВКА НА ВИВІД**\n\nСума: {data['amt']} 💎\nКарта: `{m.text}`\nID: {m.from_user.id}")
    await m.answer("✅ Заявку на вивід прийнято. Очікуйте виплату протягом 24 годин.", reply_markup=main_menu(m.from_user.id))
    await state.finish()

# --- АДМІНІСТРУВАННЯ ---
@dp.callback_query_handler(lambda c: c.data.startswith("adm_ok:"), state="*")
async def admin_confirm(c: types.CallbackQuery):
    if c.from_user.id != ADMIN_ID: return
    _, uid, amt = c.data.split(":")
    await users_col.update_one({"_id": int(uid)}, {"$inc": {"balance": float(amt), "total_deposited": float(amt)}})
    await bot.send_message(int(uid), f"✅ Баланс поповнено на {amt} 💎! Приємної гри.")
    await c.message.edit_caption(caption="✅ Нараховано успішно")

# --- ГРА: СТВОРЕННЯ ТА СТАРТ ---
@dp.message_handler(commands=['start'], state="*")
async def start_cmd(m: types.Message, state: FSMContext):
    args = m.get_args()
    if args and args.startswith("game_"):
        gid = args.replace("game_", "")
        g = await games_col.find_one({"game_id": gid, "status": "waiting"})
        u = await get_u(m.from_user.id)
        if g and g['creator_id'] != m.from_user.id:
            if u['balance'] < g['bet']: return await m.answer("❌ Недостатньо коштів!")
            
            await users_col.update_one({"_id": m.from_user.id}, {"$inc": {"balance": -g['bet']}})
            await games_col.update_one({"game_id": gid}, {"$set": {"opponent_id": m.from_user.id, "status": "playing", "turn": g['creator_id']}})
            
            info = f"Два гравці в зборі ✅\nСтавка: {g['bet']} 💎"
            await bot.send_message(g['creator_id'], info + "\nВаш хід 🎳")
            await bot.send_message(m.from_user.id, info + "\nЧекаємо на хід суперника...")
            return
    await m.answer("Вітаємо у Different Games!", reply_markup=main_menu(m.from_user.id))

@dp.message_handler(lambda m: m.text == "🎮 Боулінг", state="*")
async def create_game(m: types.Message):
    await GameState.wait_bet.set()
    await m.answer("Введіть ставку 💎 для гри в Боулінг:", reply_markup=cancel_kb())

@dp.message_handler(state=GameState.wait_bet)
async def set_bet(m: types.Message, state: FSMContext):
    try:
        bet = float(m.text.replace(',', '.'))
        u = await get_u(m.from_user.id)
        if bet <= 0: return await m.answer("Ставка повинна бути більше нуля!")
        if u['balance'] < bet: return await m.answer("❌ Недостатньо коштів на балансі!")
        
        gid = str(uuid.uuid4())[:8]
        await users_col.update_one({"_id": m.from_user.id}, {"$inc": {"balance": -bet}})
        await games_col.insert_one({"game_id": gid, "creator_id": m.from_user.id, "bet": bet, "status": "waiting"})
        
        me = await bot.get_me()
        await m.answer(f"🎳 Гра створена!\n`https://t.me/{me.username}?start=game_{gid}`", 
                       parse_mode="Markdown", reply_markup=main_menu(m.from_user.id))
        await state.finish()
    except: await m.answer("Введіть коректну суму!")

if __name__ == '__main__':
    executor.start_polling(dp, skip_updates=True)
