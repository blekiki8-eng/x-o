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

# --- СТАНИ ---
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

# --- ГЛОБАЛЬНЕ СКАСУВАННЯ ---
@dp.message_handler(lambda m: m.text == "❌ Скасувати", state="*")
async def global_cancel(m: types.Message, state: FSMContext):
    await state.finish()
    await m.answer("Дію скасовано.", reply_markup=main_menu(m.from_user.id))

# --- ДОПОМІЖНІ ФУНКЦІЇ ---
async def get_u(m):
    uid = m.from_user.id
    u = await users_col.find_one({"_id": uid})
    if not u:
        u = {
            "_id": uid, 
            "nickname": m.from_user.full_name, 
            "balance": 0.0, 
            "invited_by": None,
            "referrals_count": 0,
            "total_deposited": 0.0
        }
        await users_col.insert_one(u)
    return u

def check_win(b, s):
    lines = []
    for i in range(s):
        lines.append(list(range(i*s, (i+1)*s))) # Горизонталі
        lines.append(list(range(i, s*s, s)))     # Вертикалі
    if s == 3: lines.extend([[0, 4, 8], [2, 4, 6]])
    else: lines.extend([[0, 5, 10, 15], [3, 6, 9, 12]])
    for r in lines:
        if all(b[r[i]] == b[r[0]] != " " for i in range(len(r))): return b[r[0]]
    return "draw" if " " not in b else None

# --- КОМАНДА СТАРТ (РЕФЕРАЛКА ТА ГРА) ---
@dp.message_handler(commands=['start'], state="*")
async def start(m: types.Message, state: FSMContext):
    await state.finish()
    args = m.get_args()
    u_exists = await users_col.find_one({"_id": m.from_user.id})
    
    if not u_exists and args and args.startswith("ref_"):
        ref_id = int(args.replace("ref_", ""))
        if ref_id != m.from_user.id:
            await users_col.update_one({"_id": ref_id}, {"$inc": {"referrals_count": 1}})
            await users_col.insert_one({
                "_id": m.from_user.id, "nickname": m.from_user.full_name, "balance": 0.0, 
                "invited_by": ref_id, "referrals_count": 0, "total_deposited": 0.0
            })

    if args and args.startswith("game_"):
        gid = args.replace("game_", "")
        g = await games_col.find_one({"game_id": gid, "status": "waiting"})
        if g:
            u = await get_u(m)
            if u['balance'] < g['bet']:
                await users_col.update_one({"_id": g['creator_id']}, {"$inc": {"balance": g['bet']}})
                await games_col.delete_one({"game_id": gid})
                await bot.send_message(g['creator_id'], "❌ У суперника мало коштів, ставку повернуто.")
                return await m.answer("❌ Недостатньо 💎")
            await users_col.update_one({"_id": m.from_user.id}, {"$inc": {"balance": -g['bet']}})
            await games_col.update_one({"game_id": gid}, {"$set": {"opponent_id": m.from_user.id, "status": "playing", "turn": g['creator_id']}})
            kb = get_board_markup(gid, g['board'], g['size'])
            await bot.send_message(g['creator_id'], "🎮 Гра почалася! Твій хід (❌)", reply_markup=kb)
            await m.answer("🎮 Гра почалася! Твій символ ⭕️. Очікуй ходу.", reply_markup=kb)
            return

    await m.answer("Вітаємо у боті!", reply_markup=main_menu(m.from_user.id))

# --- ПРОФІЛЬ ---
@dp.message_handler(lambda m: m.text == "👤 Профіль", state="*")
async def profile(m: types.Message, state: FSMContext):
    await state.finish()
    u = await get_u(m)
    text = (f"🆔Id: `{m.from_user.id}`\n"
            f"💰Баланс: {round(u['balance'], 4)} 💎\n\n"
            f"Запрошено друзів: {u.get('referrals_count', 0)}\n"
            f"Всього поповнено: {u.get('total_deposited', 0)} 💎")
    await m.answer(text, parse_mode="Markdown")

# --- РЕФЕРАЛКА ---
@dp.message_handler(lambda m: m.text == "🤝 Рефералка", state="*")
async def referral(m: types.Message, state: FSMContext):
    await state.finish()
    bot_un = (await bot.get_me()).username
    link = f"https://t.me/{bot_un}?start=ref_{m.from_user.id}"
    text = (f"Вітаю це реферальна система\n"
            f"Ваше посилання: `{link}`\n\n"
            f"Система діє так:\n"
            f"Якщо ви запросили свого друга в гру то ваш реферал коли виграє то ви получаєте на свій баланс 0.01%. Чим більше виграш тим більше на балансі!")
    await m.answer(text, parse_mode="Markdown")

# --- ПОПОВНЕННЯ ---
@dp.callback_query_handler(lambda c: c.data == "dep", state="*")
async def dep_start(c: types.CallbackQuery):
    await DepositState.wait_amount.set()
    await bot.send_message(c.from_user.id, "Введіть суму поповнення\nМінімум 0.50!", reply_markup=cancel_kb())

@dp.message_handler(state=DepositState.wait_amount)
async def dep_amt(m: types.Message, state: FSMContext):
    try:
        amt = float(m.text.replace(",", "."))
        if amt < 0.50: return await m.answer("❌ Мінімум 0.50!")
        to_pay = round((amt * RATE) * 1.05, 2)
        await state.update_data(amt=amt)
        text = (f"Заявка на поповнення балансу:\nКурс: {RATE}\nДо оплати: {to_pay}грн (+5%)\n\n"
                f"`5355 2800 2890 2177`\n\n(Для підтвердження оплати обовʼязково кидайте сюди квитанцію)")
        await m.answer(text, parse_mode="Markdown")
        await DepositState.wait_receipt.set()
    except: await m.answer("Введіть число!")

@dp.message_handler(state=DepositState.wait_receipt, content_types=['photo'])
async def dep_rec(m: types.Message, state: FSMContext):
    data = await state.get_data()
    kb = InlineKeyboardMarkup().add(InlineKeyboardButton("✅ Схвалити", callback_data=f"adm_d_ok:{m.from_user.id}:{data['amt']}"), InlineKeyboardButton("❌ Відхилити", callback_data=f"adm_d_no:{m.from_user.id}"))
    await bot.send_photo(ADMIN_ID, m.photo[-1].file_id, caption=f"💎 {data['amt']} від {m.from_user.id}", reply_markup=kb)
    await m.answer("✅ Квитанцію надіслано!", reply_markup=main_menu(m.from_user.id))
    await state.finish()

# --- ВИВЕДЕННЯ ---
@dp.callback_query_handler(lambda c: c.data == "withdr", state="*")
async def wit_start(c: types.CallbackQuery):
    await WithdrawState.wait_amount.set()
    await bot.send_message(c.from_user.id, "Ви бажаєте вивести свої кошти, будь ласка введіть сумму для виведення:\n\n(Мінімалка 4💎)", reply_markup=cancel_kb())

@dp.message_handler(state=WithdrawState.wait_amount)
async def wit_amt(m: types.Message, state: FSMContext):
    try:
        amt = float(m.text.replace(",", "."))
        u = await get_u(m)
        if amt < 4.0: return await m.answer("❌ Мінімалка 4💎")
        if u['balance'] < amt: return await m.answer("❌ Недостатньо коштів!")
        await state.update_data(amt=amt)
        await m.answer(f"Сумма для виводу: {amt} 💎\n\nНапишіть IBAN, ІПН, ПІБ:")
        await WithdrawState.wait_data.set()
    except: await m.answer("Введіть число!")

@dp.message_handler(state=WithdrawState.wait_data)
async def wit_data(m: types.Message, state: FSMContext):
    data = await state.get_data()
    await users_col.update_one({"_id": m.from_user.id}, {"$inc": {"balance": -data['amt']}})
    await bot.send_message(ADMIN_ID, f"📤 ЗАЯВКА НА ВИВІД\nСума: {data['amt']} 💎\nДані: {m.text}\nID: {m.from_user.id}")
    await m.answer("✅ Заявка надіслана адміну!", reply_markup=main_menu(m.from_user.id))
    await state.finish()

# --- ЛОГІКА ХОДУ ТА ВІДРАХУВАННЯ 0.01% ---
@dp.callback_query_handler(lambda c: c.data.startswith("st:"), state="*")
async def handle_move(c: types.CallbackQuery):
    _, gid, idx = c.data.split(":"); idx = int(idx)
    g = await games_col.find_one({"game_id": gid})
    if not g or g['status'] != "playing" or c.from_user.id != g['turn']: return
    if g['board'][idx] != " ": return

    char = "❌" if c.from_user.id == g['creator_id'] else "⭕️"
    new_board = list(g['board']); new_board[idx] = char
    next_p = g['opponent_id'] if c.from_user.id == g['creator_id'] else g['creator_id']
    await games_col.update_one({"game_id": gid}, {"$set": {"board": new_board, "turn": next_p}})
    
    win = check_win(new_board, g['size'])
    if win:
        await games_col.update_one({"game_id": gid}, {"$set": {"status": "finished"}})
        if win == "draw":
            await users_col.update_many({"_id": {"$in": [g['creator_id'], g['opponent_id']]}}, {"$inc": {"balance": g['bet']}})
            res = "🤝 Нічия! Ставки повернуті."
        else:
            winner_id = g['creator_id'] if win == "❌" else g['opponent_id']
            win_sum = g['bet'] * 2
            await users_col.update_one({"_id": winner_id}, {"$inc": {"balance": win_sum}})
            
            # РЕФЕРАЛЬНИЙ БОНУС 0.01%
            winner_data = await users_col.find_one({"_id": winner_id})
            if winner_data.get('invited_by'):
                bonus = win_sum * 0.0001
                await users_col.update_one({"_id": winner_data['invited_by']}, {"$inc": {"balance": bonus}})
                await bot.send_message(winner_data['invited_by'], f"📈 Реф-бонус: +{round(bonus, 6)} 💎")
            res = f"🎉 Переміг {win}! Виграш: {win_sum} 💎"
        
        for p_id in [g['creator_id'], g['opponent_id']]:
            await bot.send_message(p_id, res, reply_markup=get_board_markup(gid, new_board, g['size']))
    else:
        kb = get_board_markup(gid, new_board, g['size'])
        await c.message.edit_text("⏳ Очікуємо хід...", reply_markup=kb)
        await bot.send_message(next_p, "🔔 Твій хід!", reply_markup=kb)

# --- БАЛАНС / АДМІНКА / ІГРИ (СТАНДАРТ) ---
@dp.message_handler(lambda m: m.text == "💎 Баланс", state="*")
async def bal_menu(m: types.Message, state: FSMContext):
    await state.finish()
    u = await get_u(m)
    kb = InlineKeyboardMarkup().add(InlineKeyboardButton("💳 Поповнити", callback_data="dep"), InlineKeyboardButton("📤 Вивести", callback_data="withdr"))
    await m.answer(f"💰 Ваш баланс: {round(u['balance'], 2)} 💎", reply_markup=kb)

@dp.message_handler(lambda m: m.text == "🎮 Ігри", state="*")
async def game_start(m: types.Message):
    await GameState.wait_game_type.set()
    await m.answer("Оберіть гру:", reply_markup=ReplyKeyboardMarkup(resize_keyboard=True).add("❌ Хрестики нолики", "❌ Скасувати"))

@dp.message_handler(state=GameState.wait_game_type)
async def game_type(m: types.Message, state: FSMContext):
    if m.text == "❌ Хрестики нолики":
        await GameState.wait_size.set()
        await m.answer("Розмір поля:", reply_markup=ReplyKeyboardMarkup(resize_keyboard=True).add("3х3", "4х4", "❌ Скасувати"))
    else: await state.finish(); await m.answer("Меню", reply_markup=main_menu(m.from_user.id))

@dp.message_handler(state=GameState.wait_size)
async def game_size(m: types.Message, state: FSMContext):
    if m.text in ["3х3", "4х4"]:
        await state.update_data(s=3 if "3" in m.text else 4)
        await GameState.wait_bet.set()
        await m.answer("Введіть ставку 💎:", reply_markup=cancel_kb())
    else: await state.finish(); await m.answer("Меню", reply_markup=main_menu(m.from_user.id))

@dp.message_handler(state=GameState.wait_bet)
async def game_bet(m: types.Message, state: FSMContext):
    try:
        bet = float(m.text.replace(",", "."))
        u = await get_u(m)
        if u['balance'] < bet: return await m.answer("❌ Мало коштів")
        d = await state.get_data(); gid = str(uuid.uuid4())[:8]
        await users_col.update_one({"_id": m.from_user.id}, {"$inc": {"balance": -bet}})
        await games_col.insert_one({"game_id": gid, "creator_id": m.from_user.id, "bet": bet, "size": d['s'], "board": [" "]*(d['s']**2), "status": "waiting"})
        link = f"https://t.me/{(await bot.get_me()).username}?start=game_{gid}"
        await m.answer(f"✅ Гра створена!\n`{link}`", reply_markup=main_menu(m.from_user.id))
        await state.finish()
    except: await m.answer("Число!")

@dp.callback_query_handler(lambda c: c.data.startswith("adm_d_"), state="*")
async def adm_dep_res(c: types.CallbackQuery):
    if c.from_user.id != ADMIN_ID: return
    p = c.data.split(":"); uid = int(p[1])
    if "ok" in p[0]:
        amt = float(p[2])
        await users_col.update_one({"_id": uid}, {"$inc": {"balance": amt, "total_deposited": amt}})
        await bot.send_message(uid, f"✅ Баланс поповнено на {amt} 💎!")
    else: await bot.send_message(uid, "❌ Депозит відхилено.")
    await c.message.delete()

if __name__ == '__main__':
    executor.start_polling(dp, skip_updates=True)
