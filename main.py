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
    wait_mode = State()
    wait_bet = State()

# --- КЛАВІАТУРИ ---
def main_menu(user_id):
    markup = ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add(KeyboardButton("👤 Профіль"), KeyboardButton("🎮 Games"))
    markup.add(KeyboardButton("💎 Баланс"), KeyboardButton("🤝 Рефералка"))
    if user_id == ADMIN_ID:
        markup.add(KeyboardButton("🛡 Панель адміна"))
    return markup

def games_menu():
    return ReplyKeyboardMarkup(resize_keyboard=True).add(KeyboardButton("🎳 Боулінг"), KeyboardButton("❌ Скасувати"))

def match_selection_menu():
    return ReplyKeyboardMarkup(resize_keyboard=True).add(KeyboardButton("Матч 1"), KeyboardButton("Матч 5"), KeyboardButton("❌ Скасувати"))

def cancel_kb():
    return ReplyKeyboardMarkup(resize_keyboard=True).add(KeyboardButton("❌ Скасувати"))

async def get_u(user_id, referrer=None):
    u = await users_col.find_one({"_id": user_id})
    if not u:
        u = {"_id": user_id, "balance": 0.0, "total_deposited": 0.0, "referred_count": 0, "referrer": referrer}
        await users_col.insert_one(u)
        if referrer and str(referrer).isdigit():
            try: await users_col.update_one({"_id": int(referrer)}, {"$inc": {"referred_count": 1}})
            except: pass
    return u

# --- КОМАНДИ ТА СКАСУВАННЯ ---
@dp.message_handler(lambda m: m.text == "❌ Скасувати", state="*")
async def cancel(m: types.Message, state: FSMContext):
    await state.finish()
    await m.answer("Дію скасовано.", reply_markup=main_menu(m.from_user.id))

@dp.message_handler(commands=['start'], state="*")
async def start_cmd(m: types.Message, state: FSMContext):
    await state.finish()
    args = m.get_args()
    ref = args if (args and args.isdigit()) else None
    u = await get_u(m.from_user.id, ref)
    
    if args and args.startswith("game_"):
        gid = args.replace("game_", "")
        g = await games_col.find_one({"game_id": gid, "status": "waiting"})
        if g and g['creator_id'] != m.from_user.id:
            if u['balance'] < g['bet']: return await m.answer("❌ Недостатньо коштів на балансі!")
            await users_col.update_one({"_id": m.from_user.id}, {"$inc": {"balance": -g['bet']}})
            await games_col.update_one({"game_id": gid}, {"$set": {
                "opponent_id": m.from_user.id, "status": "playing", "turn": g['creator_id'],
                "c_throws": [], "o_throws": []
            }})
            await bot.send_message(g['creator_id'], f"🎮 Гра розпочата ({g['mode']})!\n**Твій хід 🎳**")
            await m.answer(f"🎮 Ви приєдналися! Ставка {g['bet']} 💎.\nЗараз хід суперника ⏳", reply_markup=main_menu(m.from_user.id))
            return
    await m.answer("👋 Вітаємо у Different Games!", reply_markup=main_menu(m.from_user.id))

# --- GAMES & BOWLING ---
@dp.message_handler(lambda m: m.text == "🎮 Games", state="*")
async def g_menu(m: types.Message):
    await m.answer("Оберіть гру:", reply_markup=games_menu())

@dp.message_handler(lambda m: m.text == "🎳 Боулінг", state="*")
async def b_menu(m: types.Message):
    await GameState.wait_mode.set()
    await m.answer("Оберіть режим:", reply_markup=match_selection_menu())

@dp.message_handler(lambda m: m.text in ["Матч 1", "Матч 5"], state=GameState.wait_mode)
async def mode_set(m: types.Message, state: FSMContext):
    await state.update_data(mode=m.text)
    await GameState.wait_bet.set()
    await m.answer(f"Обрано {m.text}. Введіть суму ставки 💎:", reply_markup=cancel_kb())

@dp.message_handler(state=GameState.wait_bet)
async def bet_set(m: types.Message, state: FSMContext):
    try:
        bet = float(m.text.replace(',', '.'))
        u = await get_u(m.from_user.id)
        if u['balance'] < bet: return await m.answer("❌ Недостатньо 💎 на балансі!")
        data = await state.get_data()
        gid = str(uuid.uuid4())[:8]
        await users_col.update_one({"_id": m.from_user.id}, {"$inc": {"balance": -bet}})
        await games_col.insert_one({"game_id": gid, "creator_id": m.from_user.id, "bet": bet, "status": "waiting", "mode": data['mode']})
        me = await bot.get_me()
        await m.answer(f"🎳 Гра створена ({data['mode']})!\nПосилання для друга:\n`https://t.me/{me.username}?start=game_{gid}`", reply_markup=main_menu(m.from_user.id), parse_mode="Markdown")
        await state.finish()
    except: await m.answer("Будь ласка, введіть число!")

# --- ЛОГІКА КИДКІВ ---
@dp.message_handler(content_types=types.ContentTypes.DICE, state="*")
async def dice_handler(m: types.Message):
    if m.dice.emoji != "🎳": return
    uid = m.from_user.id
    g = await games_col.find_one({"status": "playing", "$or": [{"creator_id": uid}, {"opponent_id": uid}]})
    if not g or g.get('turn') != uid: return 

    val = m.dice.value
    cid, oid, gid, mode = g['creator_id'], g['opponent_id'], g['game_id'], g.get('mode')
    max_r = 5 if mode == "Матч 5" else 1
    c_t, o_t = g.get('c_throws', []), g.get('o_throws', [])

    if uid == cid:
        c_t.append(val)
        await games_col.update_one({"game_id": gid}, {"$set": {"c_throws": c_t, "turn": oid}})
        await m.answer(f"Ви вибили {val} 🎳 (Раунд {len(c_t)}/{max_r})\n**Зараз хід суперника ⏳**")
        await bot.send_message(oid, f"Суперник вибив {val}. **Твій хід! 🎳**")
    else:
        o_t.append(val)
        if len(o_t) >= max_r:
            # ФІНАЛ
            cs, os = sum(c_t), sum(o_t)
            win = round(g['bet'] * 2, 2)
            await games_col.update_one({"game_id": gid}, {"$set": {"status": "finished", "o_throws": o_t}})
            if cs > os:
                await users_col.update_one({"_id": cid}, {"$inc": {"balance": win}})
                res_c, res_o = f"🏆 Перемога! +{win} 💎", "📉 На жаль, програш."
            elif os > cs:
                await users_col.update_one({"_id": oid}, {"$inc": {"balance": win}})
                res_o, res_c = f"🏆 Перемога! +{win} 💎", "📉 На жаль, програш."
            else:
                await users_col.update_many({"_id": {"$in":[cid,oid]}}, {"$inc": {"balance": g['bet']}})
                res_c = res_o = "🤝 Нічия! Кошти повернуто."
            
            await bot.send_message(cid, f"🏁 Гра завершена! Рахунок {cs}:{os}\n{res_c}")
            await bot.send_message(oid, f"🏁 Гра завершена! Рахунок {os}:{cs}\n{res_o}")
        else:
            await games_col.update_one({"game_id": gid}, {"$set": {"o_throws": o_t, "turn": cid}})
            next_r = len(o_t) + 1
            await m.answer(f"Ви вибили {val} 🎳\n**Зараз хід суперника (Раунд {next_r}) ⏳**")
            await bot.send_message(cid, f"Суперник вибив {val}. **Твій хід! (Раунд {next_r}) 🎳**")

# --- ПОПОВНЕННЯ ТА ВИВЕДЕННЯ ---
@dp.message_handler(lambda m: m.text == "💎 Баланс", state="*")
async def bal(m: types.Message):
    u = await get_u(m.from_user.id)
    kb = InlineKeyboardMarkup(row_width=2).add(
        InlineKeyboardButton("💳 Поповнити", callback_data="dep"),
        InlineKeyboardButton("📤 Вивести", callback_data="with")
    )
    await m.answer(f"💎 Твій баланс: {round(u['balance'], 2)} 💎", reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data == "dep", state="*")
async def dep_start(c: types.CallbackQuery):
    await DepositState.wait_amount.set()
    await bot.send_message(c.from_user.id, "💎 Введіть суму в 💎 (мін. 0.50):", reply_markup=cancel_kb())

@dp.message_handler(state=DepositState.wait_amount)
async def dep_amt(m: types.Message, state: FSMContext):
    try:
        amt = float(m.text.replace(',', '.'))
        if amt < 0.50: return await m.answer("❌ Мінімум 0.50 💎")
        pay = round((amt * RATE) * 1.05, 2)
        await state.update_data(amt=amt)
        text = (f"📥 **ЗАЯВКА НА ПОПОВНЕННЯ**\n\n💎 Отримаєте: {amt} 💎\n💳 До сплати: {pay} грн\n\n"
                f"📌 Карта Mono: `5355 2800 2890 2177` \n\nПісля оплати надішліть **скріншот квитанції** сюди:")
        await m.answer(text, parse_mode="Markdown")
        await DepositState.wait_receipt.set()
    except: await m.answer("Введіть число!")

@dp.message_handler(state=DepositState.wait_receipt, content_types=['photo'])
async def dep_rec(m: types.Message, state: FSMContext):
    d = await state.get_data()
    kb = InlineKeyboardMarkup().add(InlineKeyboardButton("✅ Підтвердити", callback_data=f"adm_ok:{m.from_user.id}:{d['amt']}"))
    await bot.send_photo(ADMIN_ID, m.photo[-1].file_id, caption=f"Поповнення {d['amt']} 💎 від {m.from_user.id}", reply_markup=kb)
    await m.answer("✅ Заявка надіслана. Очікуйте нарахування.", reply_markup=main_menu(m.from_user.id))
    await state.finish()

@dp.callback_query_handler(lambda c: c.data == "with", state="*")
async def w_start(c: types.CallbackQuery):
    u = await get_u(c.from_user.id)
    if u['balance'] < 4: return await bot.answer_callback_query(c.id, "Мінімальна сума виводу — 4 💎", show_alert=True)
    await WithdrawState.wait_amount.set()
    await bot.send_message(c.from_user.id, "Введіть суму виводу (мін. 4 💎):", reply_markup=cancel_kb())

@dp.message_handler(state=WithdrawState.wait_amount)
async def w_amt(m: types.Message, state: FSMContext):
    try:
        amt = float(m.text.replace(',', '.'))
        u = await get_u(m.from_user.id)
        if amt < 4 or amt > u['balance']: return await m.answer("Некоректна сума.")
        await state.update_data(amt=amt)
        await m.answer("Введіть дані для виплати:\n\nIBAN / Номер карти\nПІБ\nІПН (за наявності)", reply_markup=cancel_kb())
        await WithdrawState.wait_details.set()
    except: await m.answer("Введіть число!")

@dp.message_handler(state=WithdrawState.wait_details)
async def w_fin(m: types.Message, state: FSMContext):
    d = await state.get_data()
    await users_col.update_one({"_id": m.from_user.id}, {"$inc": {"balance": -d['amt']}})
    await bot.send_message(ADMIN_ID, f"📤 **ЗАПИТ НА ВИВІД**\n\nСума: {d['amt']} 💎\nКористувач: {m.from_user.id}\nДані:\n{m.text}")
    await m.answer("✅ Заявку прийнято. Виплата протягом 24 годин.", reply_markup=main_menu(m.from_user.id))
    await state.finish()

# --- АДМІН-ПІДТВЕРДЖЕННЯ ---
@dp.callback_query_handler(lambda c: c.data.startswith("adm_ok:"), state="*")
async def admin_ok(c: types.CallbackQuery):
    if c.from_user.id != ADMIN_ID: return
    _, uid, amt = c.data.split(":")
    await users_col.update_one({"_id": int(uid)}, {"$inc": {"balance": float(amt), "total_deposited": float(amt)}})
    await bot.send_message(int(uid), f"✅ Твій баланс поповнено на {amt} 💎! Приємної гри.")
    await c.message.delete()

# --- ПРОФІЛЬ ТА РЕФЕРАЛКА ---
@dp.message_handler(lambda m: m.text == "👤 Профіль", state="*")
async def prof(m: types.Message):
    u = await get_u(m.from_user.id)
    await m.answer(f"👤 **Твій профіль**\n\n🆔 ID: `{m.from_user.id}`\n💰 Баланс: {round(u['balance'], 2)} 💎\n👥 Запрошено друзів: {u.get('referred_count', 0)}", parse_mode="Markdown")

@dp.message_handler(lambda m: m.text == "🤝 Рефералка", state="*")
async def refs(m: types.Message):
    me = await bot.get_me()
    await m.answer(f"🤝 **Реферальна програма**\n\nЗапрошуй друзів та отримуй бонуси!\n\nТвоє посилання:\n`https://t.me/{me.username}?start={m.from_user.id}`", parse_mode="Markdown")

if __name__ == '__main__':
    executor.start_polling(dp, skip_updates=True)
