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

RATE = 44.50  # Курс 1 💎 = 44.50 грн
logging.basicConfig(level=logging.INFO)
storage = MemoryStorage()
bot = Bot(token=API_TOKEN)
dp = Dispatcher(bot, storage=storage)

# Підключення до БД
cluster = AsyncIOMotorClient(MONGO_URL)
db = cluster["game_bot_db"]
users_col = db["users"]
games_col = db["games"]

# --- СТАНИ FSM ---
class DepositState(StatesGroup):
    wait_amount = State()
    wait_receipt = State()

class GameState(StatesGroup):
    wait_size = State()
    wait_bet = State()

# --- КЛАВІАТУРИ ---
def main_menu(user_id):
    markup = ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add(KeyboardButton("👤 Профіль"), KeyboardButton("🎮 Ігри"))
    markup.add(KeyboardButton("💎 Баланс"))
    if user_id == ADMIN_ID:
        markup.add(KeyboardButton("🛡 Панель адміна"))
    return markup

def cancel_kb():
    return ReplyKeyboardMarkup(resize_keyboard=True).add(KeyboardButton("❌ Скасувати"))

# --- ДОПОМІЖНІ ФУНКЦІЇ ---
async def get_u(m):
    uid = m.from_user.id
    u = await users_col.find_one({"_id": uid})
    if not u:
        u = {"_id": uid, "nickname": m.from_user.full_name, "balance": 0.0, "banned": False}
        await users_col.insert_one(u)
    return u

def get_board_markup(game_id, board, size):
    markup = InlineKeyboardMarkup(row_width=size)
    btns = [InlineKeyboardButton(board[i] if board[i] != " " else "⬜️", callback_data=f"st:{game_id}:{i}") for i in range(size*size)]
    return markup.add(*btns)

def check_win(b, s):
    lines = []
    for i in range(s):
        lines.append(list(range(i*s, (i+1)*s)))
        lines.append(list(range(i, s*s, s)))
    lines.append(list(range(0, s*s, s+1)))
    lines.append(list(range(s-1, s*s-1, s-1)))
    for r in lines:
        if all(b[r[i]] == b[r[0]] != " " for i in range(s)): return b[r[0]]
    return "draw" if " " not in b else None

# --- ОБРОБНИКИ КОМАНД ---
@dp.message_handler(commands=['start'], state="*")
async def start(m: types.Message, state: FSMContext):
    await state.finish()
    u = await get_u(m)
    
    args = m.get_args()
    if args and args.startswith("game_"):
        gid = args.split("_")[1]
        g = await games_col.find_one({"game_id": gid, "status": "waiting"})
        if g and g['creator_id'] != m.from_user.id:
            if u['balance'] >= g['bet']:
                await users_col.update_one({"_id": m.from_user.id}, {"$inc": {"balance": -g['bet']}})
                m_op = await m.answer("Приєднуємось...", reply_markup=get_board_markup(gid, g['board'], g['size']))
                m_cr = await bot.send_message(g['creator_id'], "Суперник знайдений!", reply_markup=get_board_markup(gid, g['board'], g['size']))
                await games_col.update_one({"game_id": gid}, {"$set": {"opponent_id": m.from_user.id, "opponent_msg_id": m_op.message_id, "creator_msg_id": m_cr.message_id, "status": "playing", "turn": g['creator_id']}})
                return
    await m.answer(f"Привіт, {u['nickname']}!", reply_markup=main_menu(m.from_user.id))

@dp.message_handler(lambda m: m.text == "❌ Скасувати", state="*")
async def cancel_handler(m: types.Message, state: FSMContext):
    await state.finish()
    await m.answer("❌ Дію скасовано.", reply_markup=main_menu(m.from_user.id))

@dp.message_handler(lambda m: m.text == "👤 Профіль", state="*")
async def profile(m: types.Message):
    u = await get_u(m)
    await m.answer(f"👤 **Твій профіль**\n\n📝 Нік: {u.get('nickname')}\n🆔 ID: `{m.from_user.id}`\n💰 Баланс: {round(u.get('balance', 0.0), 2)} 💎", parse_mode="Markdown")

# --- БАЛАНС ТА ПОПОВНЕННЯ ---
@dp.message_handler(lambda m: m.text == "💎 Баланс", state="*")
async def balance_menu(m: types.Message):
    u = await get_u(m)
    kb = InlineKeyboardMarkup().add(InlineKeyboardButton("💳 Поповнити", callback_data="dep"))
    await m.answer(f"Твій баланс: {round(u.get('balance', 0.0), 2)} 💎", reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data == "dep", state="*")
async def dep_start(c: types.CallbackQuery):
    await DepositState.wait_amount.set()
    text = "Введіть сумму в 💎\n*💎-$\n(Мінімум 0.50!)"
    await bot.send_message(c.from_user.id, text, reply_markup=cancel_kb())

@dp.message_handler(state=DepositState.wait_amount)
async def dep_amt(m: types.Message, state: FSMContext):
    try:
        amt = float(m.text.replace(",", "."))
        if amt < 0.50: return await m.answer("❌ Мінімальна сума — 0.50 💎")
        
        uah = round((amt * RATE) * 1.05, 2)
        await state.update_data(diamonds=amt)
        
        text = (
            "Заявка на поповнення балансу:\n"
            f"1💎= {RATE}₴\n"
            f"До оплати: {uah}₴ [+5%]\n\n"
            "`5355 2800 2890 2177`\n\n"
            "(Для підтвердження оплати обовʼязково скиньте квитанцію)"
        )
        await m.answer(text, parse_mode="Markdown", reply_markup=cancel_kb())
        await DepositState.wait_receipt.set()
    except: await m.answer("❌ Введіть число!")

@dp.message_handler(state=DepositState.wait_receipt, content_types=['photo'])
async def dep_rec(m: types.Message, state: FSMContext):
    data = await state.get_data()
    kb = InlineKeyboardMarkup().add(
        InlineKeyboardButton("✅ Схвалити", callback_data=f"ok:{m.from_user.id}:{data['diamonds']}"),
        InlineKeyboardButton("❌ Відхилити", callback_data=f"no:{m.from_user.id}")
    )
    await bot.send_photo(ADMIN_ID, m.photo[-1].file_id, caption=f"💰 Депозит {data['diamonds']} 💎\nВід: {m.from_user.full_name}", reply_markup=kb)
    await m.answer("✅ Очікуйте (до 60хв.)", reply_markup=main_menu(m.from_user.id))
    await state.finish()

# --- АДМІН-ПАНЕЛЬ ---
@dp.message_handler(lambda m: m.text == "🛡 Панель адміна", state="*")
async def admin_panel(m: types.Message):
    if m.from_user.id != ADMIN_ID: return
    kb = InlineKeyboardMarkup().add(InlineKeyboardButton("👥 Список гравців", callback_data="alist"))
    await m.answer("🛡 Адмін-панель", reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data == "alist", state="*")
async def admin_list(c: types.CallbackQuery):
    users = await users_col.find().to_list(100)
    kb = InlineKeyboardMarkup(row_width=1)
    for u in users:
        name = u.get('nickname', 'Гість')[:15]
        kb.add(InlineKeyboardButton(f"{name} | {u.get('balance', 0)} 💎", callback_data=f"u:{u['_id']}"))
    await c.message.edit_text("Список гравців:", reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data.startswith(("u:", "res:", "ban:", "ok:", "no:")), state="*")
async def admin_actions(c: types.CallbackQuery):
    if c.from_user.id != ADMIN_ID: return
    p = c.data.split(":")
    act, uid = p[0], int(p[1])
    
    if act == "u":
        u = await users_col.find_one({"_id": uid})
        kb = InlineKeyboardMarkup(row_width=1).add(
            InlineKeyboardButton("💰 Обнулити баланс", callback_data=f"res:{uid}"),
            InlineKeyboardButton("🚫 Бан / Розбан", callback_data=f"ban:{uid}"),
            InlineKeyboardButton("⬅️ Назад", callback_data="alist")
        )
        await c.message.edit_text(f"Гравець: {u['nickname']}\nБаланс: {u['balance']} 💎", reply_markup=kb)
    elif act == "res":
        await users_col.update_one({"_id": uid}, {"$set": {"balance": 0.0}})
        await c.answer("Обнулено")
        await admin_list(c)
    elif act == "ok":
        amt = float(p[2])
        await users_col.update_one({"_id": uid}, {"$inc": {"balance": amt}})
        await bot.send_message(uid, f"✅ Баланс поповнено на {amt} 💎!")
        await c.message.delete()
    elif act == "no":
        await bot.send_message(uid, "❌ Заявку на поповнення відхилено.")
        await c.message.delete()

# --- ІГРИ (РЕЖИМ -> СТАВКА) ---
@dp.message_handler(lambda m: m.text == "🎮 Ігри", state="*")
async def games_menu(m: types.Message):
    kb = InlineKeyboardMarkup().add(InlineKeyboardButton("❌ Хрестики-Нолики ⭕️", callback_data="gtic"))
    await m.answer("Оберіть гру:", reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data == "gtic", state="*")
async def tic_mode(c: types.CallbackQuery):
    kb = InlineKeyboardMarkup().add(
        InlineKeyboardButton("3x3", callback_data="sz:3"),
        InlineKeyboardButton("4x4", callback_data="sz:4")
    )
    await c.message.edit_text("Оберіть режим гри:", reply_markup=kb)
    await GameState.wait_size.set()

@dp.callback_query_handler(state=GameState.wait_size)
async def tic_size_selected(c: types.CallbackQuery, state: FSMContext):
    size = int(c.data.split(":")[1])
    await state.update_data(sz=size)
    await bot.send_message(c.from_user.id, f"Режим {size}x{size}. Введіть ставку 💎:", reply_markup=cancel_kb())
    await GameState.wait_bet.set()

@dp.message_handler(state=GameState.wait_bet)
async def tic_create_final(m: types.Message, state: FSMContext):
    if m.text == "❌ Скасувати":
        await state.finish()
        return await m.answer("Скасовано", reply_markup=main_menu(m.from_user.id))
    try:
        bet = float(m.text.replace(",", "."))
        u = await get_u(m)
        if u['balance'] < bet: return await m.answer("❌ Недостатньо балансу!")
        
        data = await state.get_data()
        size = data['sz']
        gid = str(uuid.uuid4())[:8]
        
        await users_col.update_one({"_id": m.from_user.id}, {"$inc": {"balance": -bet}})
        await games_col.insert_one({"game_id": gid, "creator_id": m.from_user.id, "bet": bet, "size": size, "board": [" "]*(size*size), "status": "waiting"})
        
        link = f"https://t.me/{(await bot.get_me()).username}?start=game_{gid}"
        await m.answer(f"🎮 Гра {size}x{size} створена!\nСтавка: {bet} 💎\n\nПосилання для друга:\n{link}", reply_markup=main_menu(m.from_user.id))
        await state.finish()
    except: await m.answer("❌ Введіть число!")

if __name__ == '__main__':
    executor.start_polling(dp, skip_updates=True)
