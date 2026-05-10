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

# --- ДОПОМІЖНІ ФУНКЦІЇ ---
async def get_u(m):
    uid = m.from_user.id
    u = await users_col.find_one({"_id": uid})
    if not u:
        u = {"_id": uid, "nickname": m.from_user.full_name, "balance": 0.0, "invited_by": None, "referrals_count": 0, "total_deposited": 0.0}
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

# --- СТАРТ ТА СКАСУВАННЯ ---
@dp.message_handler(commands=['start'], state="*")
async def start_cmd(m: types.Message, state: FSMContext):
    await state.finish()
    args = m.get_args()
    uid = m.from_user.id
    u = await users_col.find_one({"_id": uid})

    if not u:
        inviter = None
        if args and args.startswith("ref_"):
            try:
                inviter = int(args.replace("ref_", ""))
                if inviter != uid:
                    await users_col.update_one({"_id": inviter}, {"$inc": {"referrals_count": 1}})
            except: pass
        u = {"_id": uid, "nickname": m.from_user.full_name, "balance": 0.0, "invited_by": inviter, "referrals_count": 0, "total_deposited": 0.0}
        await users_col.insert_one(u)

    if args and args.startswith("game_"):
        g = await games_col.find_one({"game_id": args.replace("game_", ""), "status": "waiting"})
        if g:
            if g['creator_id'] == uid: return await m.answer("⏳ Очікуйте суперника.")
            if u['balance'] < g['bet']: return await m.answer("❌ Недостатньо 💎")
            
            await users_col.update_one({"_id": uid}, {"$inc": {"balance": -g['bet']}})
            await games_col.update_one({"game_id": g['game_id']}, {"$set": {"opponent_id": uid, "status": "playing", "turn": g['creator_id']}})
            kb = get_board_markup(g['game_id'], g['board'], g['size'])
            await bot.send_message(g['creator_id'], "🎮 Гра почалася! Твій хід (❌)", reply_markup=kb)
            await m.answer("🎮 Гра почалася! Хід суперника (ти ⭕️)", reply_markup=kb)
            return

    await m.answer(f"Вітаємо, {m.from_user.first_name}!", reply_markup=main_menu(uid))

@dp.message_handler(lambda m: m.text == "❌ Скасувати", state="*")
async def cancel_all(m: types.Message, state: FSMContext):
    await state.finish()
    await m.answer("Дію скасовано.", reply_markup=main_menu(m.from_user.id))

# --- ПРОФІЛЬ ТА РЕФЕРАЛКА ---
@dp.message_handler(lambda m: m.text == "👤 Профіль", state="*")
async def profile_h(m: types.Message, state: FSMContext):
    await state.finish()
    u = await get_u(m)
    await m.answer(f"🆔Id: `{m.from_user.id}`\n💰Баланс: {round(u['balance'], 4)} 💎\n\nЗапрошено друзів: {u.get('referrals_count', 0)}\nВсього поповнено: {u.get('total_deposited', 0)} 💎", parse_mode="Markdown")

@dp.message_handler(lambda m: m.text == "🤝 Рефералка", state="*")
async def refer_h(m: types.Message, state: FSMContext):
    await state.finish()
    bot_un = (await bot.get_me()).username
    link = f"https://t.me/{bot_un}?start=ref_{m.from_user.id}"
    await m.answer(f"Вітаю це реферальна система\nВаше посилання: `{link}`\n\nСистема діє так: при виграші вашого реферала ви отримуєте 0.01% на баланс!", parse_mode="Markdown")

# --- ПОПОВНЕННЯ ТА ВИВЕДЕННЯ ---
@dp.message_handler(lambda m: m.text == "💎 Баланс", state="*")
async def bal_h(m: types.Message, state: FSMContext):
    await state.finish()
    u = await get_u(m)
    kb = InlineKeyboardMarkup().add(InlineKeyboardButton("💳 Поповнити", callback_data="dep"), InlineKeyboardButton("📤 Вивести", callback_data="withdr"))
    await m.answer(f"💰 Баланс: {round(u['balance'], 2)} 💎", reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data == "dep", state="*")
async def dep_1(c: types.CallbackQuery):
    await DepositState.wait_amount.set()
    await bot.send_message(c.from_user.id, "Введіть суму поповнення\nМінімум 0.50!", reply_markup=cancel_kb())

@dp.message_handler(state=DepositState.wait_amount)
async def dep_2(m: types.Message, state: FSMContext):
    try:
        amt = float(m.text.replace(",", "."))
        if amt < 0.50: return await m.answer("❌ Мінімум 0.50!")
        to_pay = round((amt * RATE) * 1.05, 2)
        await state.update_data(a=amt)
        await m.answer(f"Заявка на поповнення балансу:\nКурс: {RATE}\nДо оплати: {to_pay}грн (+5%)\n\n`5355 2800 2890 2177`\n\n(Для підтвердження оплати обовʼязково кидайте сюди квитанцію)", parse_mode="Markdown")
        await DepositState.wait_receipt.set()
    except: await m.answer("Введіть число!")

@dp.message_handler(state=DepositState.wait_receipt, content_types=['photo'])
async def dep_3(m: types.Message, state: FSMContext):
    data = await state.get_data()
    kb = InlineKeyboardMarkup().add(InlineKeyboardButton("✅ Схвалити", callback_data=f"adm_ok:{m.from_user.id}:{data['a']}"), InlineKeyboardButton("❌ Відхилити", callback_data=f"adm_no:{m.from_user.id}"))
    await bot.send_photo(ADMIN_ID, m.photo[-1].file_id, caption=f"💎 {data['a']} від {m.from_user.id}", reply_markup=kb)
    await m.answer("✅ Надіслано!", reply_markup=main_menu(m.from_user.id))
    await state.finish()

@dp.callback_query_handler(lambda c: c.data == "withdr", state="*")
async def wit_1(c: types.CallbackQuery):
    await WithdrawState.wait_amount.set()
    await bot.send_message(c.from_user.id, "Ви бажаєте вивести свої кошти, будь ласка введіть сумму для виведення:\n\n(Мінімалка 4💎)", reply_markup=cancel_kb())

@dp.message_handler(state=WithdrawState.wait_amount)
async def wit_2(m: types.Message, state: FSMContext):
    try:
        amt = float(m.text.replace(",", "."))
        u = await get_u(m)
        if amt < 4.0: return await m.answer("❌ Мінімалка 4💎")
        if u['balance'] < amt: return await m.answer("❌ Недостатньо 💎")
        await state.update_data(a=amt)
        await m.answer(f"Сумма для виводу: {amt} 💎\n\nНапишіть IBAN, ІПН, ПІБ:")
        await WithdrawState.wait_data.set()
    except: await m.answer("Число!")

@dp.message_handler(state=WithdrawState.wait_data)
async def wit_3(m: types.Message, state: FSMContext):
    data = await state.get_data()
    await users_col.update_one({"_id": m.from_user.id}, {"$inc": {"balance": -data['a']}})
    await bot.send_message(ADMIN_ID, f"📤 ВИВІД {data['a']} 💎\nДані: {m.text}\nID: {m.from_user.id}")
    await m.answer("✅ Заявка надіслана!", reply_markup=main_menu(m.from_user.id))
    await state.finish()

# --- АДМІН-ПАНЕЛЬ ---
@dp.message_handler(lambda m: m.text == "🛡 Панель адміна", state="*")
async def adm_p(m: types.Message):
    if m.from_user.id != ADMIN_ID: return
    kb = InlineKeyboardMarkup().add(InlineKeyboardButton("👥 Список гравців", callback_data="adm_list"))
    await m.answer("🛡 Керування:", reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data == "adm_list", state="*")
async def adm_list(c: types.CallbackQuery):
    users = await users_col.find().limit(30).to_list(length=30)
    kb = InlineKeyboardMarkup(row_width=1)
    for u in users: kb.add(InlineKeyboardButton(f"{u['nickname']} ({u['_id']})", callback_data=f"info:{u['_id']}"))
    await bot.send_message(c.from_user.id, "Оберіть гравця:", reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data.startswith("info:"), state="*")
async def adm_info(c: types.CallbackQuery):
    u = await users_col.find_one({"_id": int(c.data.split(":")[1])})
    text = (f"📊 Гравець: {u['nickname']}\nID: `{u['_id']}`\nБаланс: {u['balance']} 💎\n"
            f"Поповнив: {u['total_deposited']} 💎\nЗапросив: {u['referrals_count']}")
    await bot.send_message(c.from_user.id, text, parse_mode="Markdown")

# --- ГРА (ОПТИМІЗОВАНА) ---
@dp.callback_query_handler(lambda c: c.data.startswith("st:"), state="*")
async def game_move(c: types.CallbackQuery):
    _, gid, idx = c.data.split(":"); idx = int(idx)
    g = await games_col.find_one({"game_id": gid})
    if not g or g['status'] != "playing" or c.from_user.id != g['turn']: return
    if g['board'][idx] != " ": return

    char = "❌" if c.from_user.id == g['creator_id'] else "⭕️"
    new_board = list(g['board']); new_board[idx] = char
    next_p = g['opponent_id'] if c.from_user.id == g['creator_id'] else g['creator_id']
    await games_col.update_one({"game_id": gid}, {"$set": {"board": new_board, "turn": next_p}})
    
    win = check_win(new_board, g['size'])
    kb = get_board_markup(gid, new_board, g['size'])

    if win:
        await games_col.update_one({"game_id": gid}, {"$set": {"status": "finished"}})
        if win == "draw":
            await users_col.update_many({"_id": {"$in": [g['creator_id'], g['opponent_id']]}}, {"$inc": {"balance": g['bet']}})
            await c.message.edit_text("🤝 Нічия! Ставки повернуті.", reply_markup=kb)
            await bot.send_message(next_p, "🤝 Нічия! Ставки повернуті.", reply_markup=kb)
        else:
            winner_id = g['creator_id'] if win == "❌" else g['opponent_id']
            loser_id = g['opponent_id'] if win == "❌" else g['creator_id']
            win_sum = g['bet'] * 2
            await users_col.update_one({"_id": winner_id}, {"$inc": {"balance": win_sum}})
            
            # Реф-бонус 0.01%
            win_u = await users_col.find_one({"_id": winner_id})
            if win_u.get('invited_by'):
                bonus = round(win_sum * 0.0001, 5)
                await users_col.update_one({"_id": win_u['invited_by']}, {"$inc": {"balance": bonus}})
                await bot.send_message(win_u['invited_by'], f"📈 реферальний бонус +{bonus} 💎")

            if c.from_user.id == winner_id:
                await c.message.edit_text(f"Вітаю з перемогою! На баланс нараховано +{win_sum} 💎", reply_markup=kb)
                await bot.send_message(loser_id, "Нажаль ви програли( на наступний раз повезе!", reply_markup=kb)
            else:
                await c.message.edit_text("Нажаль ви програли( на наступний раз повезе!", reply_markup=kb)
                await bot.send_message(winner_id, f"Вітаю з перемогою! На баланс нараховано +{win_sum} 💎", reply_markup=kb)
    else:
        try: await c.message.edit_text("⏳ Очікуємо хід суперника...", reply_markup=kb)
        except: pass
        await bot.send_message(next_p, "🔔 Твій хід!", reply_markup=kb)

# --- СТВОРЕННЯ ГРИ ---
@dp.message_handler(lambda m: m.text == "🎮 Ігри", state="*")
async def game_1(m: types.Message):
    await GameState.wait_game_type.set()
    await m.answer("Оберіть гру:", reply_markup=ReplyKeyboardMarkup(resize_keyboard=True).add("❌ Хрестики нолики", "❌ Скасувати"))

@dp.message_handler(state=GameState.wait_game_type)
async def game_2(m: types.Message, state: FSMContext):
    if m.text == "❌ Хрестики нолики":
        await GameState.wait_size.set()
        await m.answer("Розмір поля:", reply_markup=ReplyKeyboardMarkup(resize_keyboard=True).add("3х3", "4х4", "❌ Скасувати"))
    else: await state.finish(); await m.answer("Меню", reply_markup=main_menu(m.from_user.id))

@dp.message_handler(state=GameState.wait_size)
async def game_3(m: types.Message, state: FSMContext):
    if m.text in ["3х3", "4х4"]:
        await state.update_data(s=3 if "3" in m.text else 4)
        await GameState.wait_bet.set()
        await m.answer("Введіть ставку 💎:", reply_markup=cancel_kb())
    else: await state.finish(); await m.answer("Меню", reply_markup=main_menu(m.from_user.id))

@dp.message_handler(state=GameState.wait_bet)
async def game_4(m: types.Message, state: FSMContext):
    try:
        bet = float(m.text.replace(",", "."))
        u = await get_u(m)
        if u['balance'] < bet: return await m.answer("❌ Мало коштів")
        d = await state.get_data(); gid = str(uuid.uuid4())[:8]
        await users_col.update_one({"_id": m.from_user.id}, {"$inc": {"balance": -bet}})
        await games_col.insert_one({"game_id": gid, "creator_id": m.from_user.id, "bet": bet, "size": d['s'], "board": [" "]*(d['s']**2), "status": "waiting"})
        link = f"https://t.me/{(await bot.get_me()).username}?start=game_{gid}"
        await m.answer(f"✅ Гра створена!\n`{link}`", reply_markup=main_menu(m.from_user.id), parse_mode="Markdown")
        await state.finish()
    except: await m.answer("Число!")

@dp.callback_query_handler(lambda c: c.data.startswith("adm_"), state="*")
async def adm_call(c: types.CallbackQuery):
    if c.from_user.id != ADMIN_ID: return
    p = c.data.split(":"); uid = int(p[1])
    if "ok" in p[0]:
        amt = float(p[2])
        await users_col.update_one({"_id": uid}, {"$inc": {"balance": amt, "total_deposited": amt}})
        await bot.send_message(uid, f"✅ Баланс поповнено на {amt} 💎!")
    else: await bot.send_message(uid, "❌ Поповнення відхилено.")
    await c.message.delete()

if __name__ == '__main__':
    executor.start_polling(dp, skip_updates=True)
