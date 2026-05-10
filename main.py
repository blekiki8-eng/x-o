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
MOVE_TIMEOUT = 40 # секунд на хід
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
    wait_data = State()

class GameState(StatesGroup):
    wait_game_type = State()
    wait_size = State()
    wait_bet = State()

# --- КЛАВІАТУРИ ---
def main_menu(user_id):
    markup = ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add(KeyboardButton("👤 Профіль"), KeyboardButton("🎮 Ігри"))
    markup.add(KeyboardButton("💎 Баланс"), KeyboardButton("🤝 Рефералка"))
    if user_id == ADMIN_ID:
        markup.add(KeyboardButton("🛡 Панель адміна"))
    return markup

def cancel_kb():
    return ReplyKeyboardMarkup(resize_keyboard=True).add(KeyboardButton("❌ Скасувати"))

def get_board_markup(game_id, board, size):
    markup = InlineKeyboardMarkup(row_width=size)
    btns = [InlineKeyboardButton(board[i] if board[i] != " " else "⬜️", callback_data=f"st:{game_id}:{i}") for i in range(size*size)]
    return markup.add(*btns)

async def get_u(user_id, name="Користувач"):
    u = await users_col.find_one({"_id": user_id})
    if not u:
        u = {"_id": user_id, "nickname": name, "balance": 0.0, "invited_by": None, "referrals_count": 0, "total_deposited": 0.0}
        await users_col.insert_one(u)
    return u

def check_win(b, s):
    lines = []
    for i in range(s):
        lines.append(list(range(i*s, (i+1)*s)))
        lines.append(list(range(i, s*s, s)))
    if s == 3: lines.extend([[0, 4, 8], [2, 4, 6]])
    else: lines.extend([[0, 5, 10, 15], [3, 6, 9, 12]])
    for r in lines:
        if all(b[r[i]] == b[r[0]] != " " for i in range(len(r))): return b[r[0]]
    return "draw" if " " not in b else None

# --- СТАРТ ТА ВХІД ---
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
                await users_col.update_one({"_id": uid}, {"$inc": {"balance": -round(g['bet'], 2)}})
                opp_m = await m.answer("🎮 Гра почалася!", reply_markup=get_board_markup(gid, g['board'], g['size']))
                await games_col.update_one({"game_id": gid}, {"$set": {
                    "opponent_id": uid, "status": "playing", "turn": g['creator_id'], 
                    "opponent_msg_id": opp_m.message_id, "last_move_time": time.time()
                }})
                await bot.edit_message_text("🎮 Гра почалася! Твій хід!", g['creator_id'], g['creator_msg_id'], reply_markup=get_board_markup(gid, g['board'], g['size']))
                return
        elif args.startswith("ref_"):
            ref_id = int(args.split("_")[1])
            if ref_id != uid and not u.get("invited_by"):
                await users_col.update_one({"_id": uid}, {"$set": {"invited_by": ref_id}})
                await users_col.update_one({"_id": ref_id}, {"$inc": {"referrals_count": 1}})

    await m.answer("Головне меню:", reply_markup=main_menu(uid))

# --- ЛОГІКА ХОДУ ТА ТАЙМЕРА ---
@dp.callback_query_handler(lambda c: c.data.startswith("st:"), state="*")
async def game_move(c: types.CallbackQuery):
    _, gid, idx = c.data.split(":"); idx = int(idx)
    g = await games_col.find_one({"game_id": gid})
    if not g or g['status'] != "playing": return

    now = time.time()
    # Перевірка таймера (якщо суперник прострочив 40 сек)
    if g['turn'] != c.from_user.id:
        if now - g['last_move_time'] > MOVE_TIMEOUT:
            await games_col.update_one({"game_id": gid}, {"$set": {"status": "finished"}})
            win_sum = round(g['bet'] * 2, 2)
            await users_col.update_one({"_id": c.from_user.id}, {"$inc": {"balance": win_sum}})
            msg = "🏆 Суперник не походив вчасно! Вам зараховано перемогу!"
            await bot.send_message(g['creator_id'], msg)
            await bot.send_message(g['opponent_id'], msg)
            return
        else:
            return await c.answer("Зараз не ваш хід!", show_alert=True)

    # Звичайний хід
    char = "❌" if c.from_user.id == g['creator_id'] else "⭕️"
    new_board = list(g['board']); new_board[idx] = char
    next_p = g['opponent_id'] if c.from_user.id == g['creator_id'] else g['creator_id']
    
    await games_col.update_one({"game_id": gid}, {"$set": {"board": new_board, "turn": next_p, "last_move_time": now}})
    
    win = check_win(new_board, g['size'])
    kb = get_board_markup(gid, new_board, g['size'])

    if win:
        await games_col.update_one({"game_id": gid}, {"$set": {"status": "finished"}})
        win_sum = round(g['bet'] * 2, 2)
        if win == "draw":
            await users_col.update_many({"_id": {"$in": [g['creator_id'], g['opponent_id']]}}, {"$inc": {"balance": round(g['bet'], 2)}})
            res_c = res_o = "🤝 Нічия! Ставки повернуті."
        else:
            winner = g['creator_id'] if win == "❌" else g['opponent_id']
            await users_col.update_one({"_id": winner}, {"$inc": {"balance": win_sum}})
            res_c = f"🎉 Перемога! +{win_sum} 💎" if winner == g['creator_id'] else "❌ Програш."
            res_o = f"🎉 Перемога! +{win_sum} 💎" if winner == g['opponent_id'] else "❌ Програш."
        await bot.edit_message_text(res_c, g['creator_id'], g['creator_msg_id'], reply_markup=kb)
        await bot.edit_message_text(res_o, g['opponent_id'], g['opponent_msg_id'], reply_markup=kb)
    else:
        await bot.edit_message_text("⏳ Очікуйте...", c.from_user.id, c.message.message_id, reply_markup=kb)
        target = g['creator_msg_id'] if next_p == g['creator_id'] else g['opponent_msg_id']
        await bot.edit_message_text(f"🔔 Твій хід! (Залишилось {MOVE_TIMEOUT} сек)", next_p, target, reply_markup=kb)

# --- МЕНЮ: ПРОФІЛЬ, БАЛАНС, РЕФЕРАЛКА, ІГРИ ---
@dp.message_handler(lambda m: m.text == "👤 Профіль", state="*")
async def profile(m: types.Message):
    u = await get_u(m.from_user.id)
    await m.answer(f"👤 **Профіль**\n\n🆔 Ваш ID: `{m.from_user.id}`\n💰 Баланс: {round(u['balance'], 2)} 💎\n🤝 Рефералів: {u.get('referrals_count', 0)}", parse_mode="Markdown")

@dp.message_handler(lambda m: m.text == "💎 Баланс", state="*")
async def balance(m: types.Message):
    u = await get_u(m.from_user.id)
    kb = InlineKeyboardMarkup().add(InlineKeyboardButton("💳 Поповнити", callback_data="dep"), InlineKeyboardButton("📤 Вивести", callback_data="withdr"))
    await m.answer(f"💰 Ваш баланс: {round(u['balance'], 2)} 💎", reply_markup=kb)

@dp.message_handler(lambda m: m.text == "🤝 Рефералка", state="*")
async def referral(m: types.Message):
    bot_u = (await bot.get_me()).username
    await m.answer(f"🤝 **Реферальна система**\n\nЗапрошуйте друзів та отримуйте бонуси!\n\nВаше посилання:\n`https://t.me/{bot_u}?start=ref_{m.from_user.id}`", parse_mode="Markdown")

@dp.message_handler(lambda m: m.text == "🎮 Ігри", state="*")
async def games_menu(m: types.Message):
    await GameState.wait_game_type.set()
    await m.answer("Оберіть гру:", reply_markup=ReplyKeyboardMarkup(resize_keyboard=True).add("❌ Хрестики нолики", "❌ Скасувати"))

# --- ОБРОБНИКИ СТВОРЕННЯ ГРИ ---
@dp.message_handler(state=GameState.wait_game_type)
async def set_type(m: types.Message, state: FSMContext):
    if m.text == "❌ Хрестики нолики":
        await GameState.wait_size.set()
        await m.answer("Оберіть розмір поля:", reply_markup=ReplyKeyboardMarkup(resize_keyboard=True).add("3х3", "4х4", "❌ Скасувати"))
    else: await state.finish(); await m.answer("Скасовано", reply_markup=main_menu(m.from_user.id))

@dp.message_handler(state=GameState.wait_size)
async def set_size(m: types.Message, state: FSMContext):
    if "3" in m.text or "4" in m.text:
        await state.update_data(s=3 if "3" in m.text else 4)
        await GameState.wait_bet.set()
        await m.answer("Введіть ставку 💎:", reply_markup=cancel_kb())

@dp.message_handler(state=GameState.wait_bet)
async def set_bet(m: types.Message, state: FSMContext):
    try:
        bet = round(float(m.text.replace(",", ".")), 2)
        u = await get_u(m.from_user.id)
        if u['balance'] < bet: return await m.answer("❌ Недостатньо коштів!")
        d = await state.get_data(); gid = str(uuid.uuid4())[:8]
        bot_u = (await bot.get_me()).username
        msg = await m.answer(f"⏳ Очікуємо суперника...\nСтавка: {bet} 💎\n\n`https://t.me/{bot_u}?start=game_{gid}`", reply_markup=main_menu(m.from_user.id), parse_mode="Markdown")
        await users_col.update_one({"_id": m.from_user.id}, {"$inc": {"balance": -bet}})
        await games_col.insert_one({"game_id": gid, "creator_id": m.from_user.id, "bet": bet, "size": d['s'], "board": [" "]*(d['s']**2), "status": "waiting", "creator_msg_id": msg.message_id})
        await state.finish()
    except: await m.answer("Введіть число!")

# --- ПОПОВНЕННЯ / ВИВЕДЕННЯ (ТАК САМО ЯК РАНІШЕ) ---
@dp.callback_query_handler(lambda c: c.data == "dep", state="*")
async def dep_start(c: types.CallbackQuery):
    await DepositState.wait_amount.set()
    await bot.send_message(c.from_user.id, "Сума поповнення (мін 0.50):", reply_markup=cancel_kb())

@dp.message_handler(state=DepositState.wait_amount)
async def dep_amt(m: types.Message, state: FSMContext):
    try:
        a = float(m.text)
        await state.update_data(a=a)
        await m.answer(f"Оплатіть на карту `5355 2800 2890 2177` та надішліть фото квитанції.")
        await DepositState.wait_receipt.set()
    except: await m.answer("Число!")

@dp.message_handler(state=DepositState.wait_receipt, content_types=['photo'])
async def dep_fin(m: types.Message, state: FSMContext):
    d = await state.get_data()
    kb = InlineKeyboardMarkup().add(InlineKeyboardButton("✅ Ок", callback_data=f"adm_ok:{m.from_user.id}:{d['a']}"))
    await bot.send_photo(ADMIN_ID, m.photo[-1].file_id, caption=f"Депозит {d['a']} від {m.from_user.id}", reply_markup=kb)
    await m.answer("Надіслано адміну!", reply_markup=main_menu(m.from_user.id))
    await state.finish()

@dp.callback_query_handler(lambda c: c.data.startswith("adm_ok:"), state="*")
async def adm_confirm(c: types.CallbackQuery):
    _, uid, amt = c.data.split(":")
    await users_col.update_one({"_id": int(uid)}, {"$inc": {"balance": float(amt), "total_deposited": float(amt)}})
    await bot.send_message(int(uid), f"Баланс поповнено на {amt} 💎")
    await c.message.delete()

@dp.message_handler(lambda m: m.text == "❌ Скасувати", state="*")
async def cancel(m: types.Message, state: FSMContext):
    await state.finish()
    await m.answer("Скасовано", reply_markup=main_menu(m.from_user.id))

if __name__ == '__main__':
    executor.start_polling(dp, skip_updates=True)
