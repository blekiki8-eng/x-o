import os, uuid, logging, asyncio, time
from aiogram import Bot, Dispatcher, executor, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

# --- КОНФІГУРАЦІЯ ---
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

class GameState(StatesGroup):
    wait_game_type = State()
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

def games_choice_kb():
    return ReplyKeyboardMarkup(resize_keyboard=True).add(KeyboardButton("❌ Хрестики нолики"), KeyboardButton("❌ Скасувати"))

def modes_choice_kb():
    return ReplyKeyboardMarkup(resize_keyboard=True).add(KeyboardButton("3х3"), KeyboardButton("4х4")).add(KeyboardButton("❌ Скасувати"))

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
        u = {"_id": uid, "nickname": m.from_user.full_name, "balance": 0.0, "banned": False}
        await users_col.insert_one(u)
    return u

def check_win(b, s):
    lines = []
    for i in range(s):
        lines.append(list(range(i*s, (i+1)*s))) # Горизонталі
        lines.append(list(range(i, s*s, s)))     # Вертикалі
    lines.append(list(range(0, s*s, s+1)))       # Діагоналі
    lines.append(list(range(s-1, s*s-1, s-1)))
    for r in lines:
        if all(b[r[i]] == b[r[0]] != " " for i in range(s)): return b[r[0]]
    return "draw" if " " not in b else None

# --- ВХІД В ГРУ (ПОСИЛАННЯ) ---
@dp.message_handler(commands=['start'], state="*")
async def start(m: types.Message, state: FSMContext):
    await state.finish()
    u = await get_u(m)
    
    args = m.get_args()
    if args and args.startswith("game_"):
        gid = args.replace("game_", "")
        g = await games_col.find_one({"game_id": gid, "status": "waiting"})
        
        if not g:
            return await m.answer("❌ Гра не знайдена або вже почалася.", reply_markup=main_menu(m.from_user.id))
        
        if g['creator_id'] == m.from_user.id:
            return await m.answer("⏳ Це ваша гра. Очікуйте суперника.")

        if u['balance'] < g['bet']:
            await users_col.update_one({"_id": g['creator_id']}, {"$inc": {"balance": g['bet']}})
            await games_col.delete_one({"game_id": gid})
            await bot.send_message(g['creator_id'], f"❌ У суперника не вистачає коштів, кошти повернуті на баланс ({g['bet']} 💎).")
            return await m.answer("❌ У вас недостатньо коштів для гри!")

        await users_col.update_one({"_id": m.from_user.id}, {"$inc": {"balance": -g['bet']}})
        board_markup = get_board_markup(gid, g['board'], g['size'])
        
        await bot.send_message(g['creator_id'], f"🎮 Суперник приєднався! Ваш хід! Ви граєте за: ❌", reply_markup=board_markup)
        await m.answer(f"🎮 Гра почалася! Очікуйте ходу суперника. Ви граєте за: ⭕️", reply_markup=board_markup)
        
        await games_col.update_one({"game_id": gid}, {"$set": {"opponent_id": m.from_user.id, "status": "playing", "turn": g['creator_id']}})
        return

    await m.answer(f"Привіт, {u['nickname']}!", reply_markup=main_menu(m.from_user.id))

# --- ЛОГІКА ХОДУ ---
@dp.callback_query_handler(lambda c: c.data.startswith("st:"), state="*")
async def game_step(c: types.CallbackQuery):
    _, gid, idx = c.data.split(":")
    idx = int(idx)
    g = await games_col.find_one({"game_id": gid})
    
    if not g or g['status'] != "playing": return
    if c.from_user.id != g['turn']: return await c.answer("⏳ Очікуйте свого ходу!", show_alert=True)
    if g['board'][idx] != " ": return await c.answer("❌ Ця клітинка вже зайнята!")

    char = "❌" if c.from_user.id == g['creator_id'] else "⭕️"
    new_board = list(g['board'])
    new_board[idx] = char
    next_turn = g['opponent_id'] if c.from_user.id == g['creator_id'] else g['creator_id']
    
    await games_col.update_one({"game_id": gid}, {"$set": {"board": new_board, "turn": next_turn}})
    
    win = check_win(new_board, g['size'])
    if win:
        await games_col.update_one({"game_id": gid}, {"$set": {"status": "finished"}})
        if win == "draw":
            res = "🤝 Нічия! Ставки повернуті."
            await users_col.update_one({"_id": g['creator_id']}, {"$inc": {"balance": g['bet']}})
            await users_col.update_one({"_id": g['opponent_id']}, {"$inc": {"balance": g['bet']}})
        else:
            winner = g['creator_id'] if win == "❌" else g['opponent_id']
            res = f"🎉 Переміг {win}! Виграш: {g['bet'] * 2} 💎"
            await users_col.update_one({"_id": winner}, {"$inc": {"balance": g['bet'] * 2}})
        
        await bot.send_message(g['creator_id'], res, reply_markup=get_board_markup(gid, new_board, g['size']))
        await bot.send_message(g['opponent_id'], res, reply_markup=get_board_markup(gid, new_board, g['size']))
    else:
        await bot.send_message(next_turn, "🔔 Ваш хід!", reply_markup=get_board_markup(gid, new_board, g['size']))
        await c.message.edit_text("⏳ Хід суперника...", reply_markup=get_board_markup(gid, new_board, g['size']))

# --- ПРОФІЛЬ ---
@dp.message_handler(lambda m: m.text == "👤 Профіль", state="*")
async def profile(m: types.Message):
    u = await get_u(m)
    text = f"👤 **Твій профіль**\n\n📝 Нік: {u.get('nickname')}\n🆔 ID: `{m.from_user.id}`\n💰 Баланс: {round(u.get('balance', 0.0), 2)} 💎"
    await m.answer(text, parse_mode="Markdown")

# --- БАЛАНС ТА АДМІНКА ПОПОВНЕНЬ ---
@dp.message_handler(lambda m: m.text == "💎 Баланс", state="*")
async def bal_m(m: types.Message):
    u = await get_u(m)
    kb = InlineKeyboardMarkup().add(InlineKeyboardButton("💳 Поповнити", callback_data="dep"))
    await m.answer(f"💰 Баланс: {round(u.get('balance', 0.0), 2)} 💎", reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data == "dep", state="*")
async def dep_st(c: types.CallbackQuery):
    await DepositState.wait_amount.set()
    await bot.send_message(c.from_user.id, "Введіть сумму в 💎\n*💎-$\n(Мінімум 0.50!)", reply_markup=cancel_kb())

@dp.message_handler(state=DepositState.wait_amount)
async def dep_amt(m: types.Message, state: FSMContext):
    if m.text == "❌ Скасувати":
        await state.finish()
        return await m.answer("Скасовано", reply_markup=main_menu(m.from_user.id))
    try:
        amt = float(m.text.replace(",", "."))
        if amt < 0.50: return await m.answer("❌ Мінімум 0.50!")
        uah = round((amt * RATE) * 1.05, 2)
        await state.update_data(d=amt)
        txt = f"Заявка на поповнення:\n1💎= {RATE}₴\nДо оплати: {uah}₴ [+5%]\n\n`5355 2800 2890 2177`"
        await m.answer(txt, parse_mode="Markdown", reply_markup=cancel_kb())
        await DepositState.wait_receipt.set()
    except: await m.answer("❌ Введіть число!")

@dp.message_handler(state=DepositState.wait_receipt, content_types=['photo'])
async def dep_rec(m: types.Message, state: FSMContext):
    data = await state.get_data()
    kb = InlineKeyboardMarkup().add(InlineKeyboardButton("✅", callback_data=f"payok:{m.from_user.id}:{data['d']}"), InlineKeyboardButton("❌", callback_data=f"payno:{m.from_user.id}"))
    await bot.send_photo(ADMIN_ID, m.photo[-1].file_id, caption=f"💎 {data['d']} від {m.from_user.full_name}", reply_markup=kb)
    await m.answer("✅ Очікуйте (до 60хв.)", reply_markup=main_menu(m.from_user.id))
    await state.finish()

@dp.callback_query_handler(lambda c: c.data.startswith(("payok:", "payno:")), state="*")
async def adm_pay(c: types.CallbackQuery):
    if c.from_user.id != ADMIN_ID: return
    p = c.data.split(":")
    uid, act = int(p[1]), p[0]
    if act == "payok":
        await users_col.update_one({"_id": uid}, {"$inc": {"balance": float(p[2])}})
        await bot.send_message(uid, "✅ Баланс поповнено!")
    await c.message.delete()

# --- СТВОРЕННЯ ГРИ (НИЖНЯ ПАНЕЛЬ) ---
@dp.message_handler(lambda m: m.text == "🎮 Ігри", state="*")
async def games_menu(m: types.Message):
    await GameState.wait_game_type.set()
    await m.answer("Оберіть гру:", reply_markup=games_choice_kb())

@dp.message_handler(state=GameState.wait_game_type)
async def game_type(m: types.Message, state: FSMContext):
    if m.text == "❌ Хрестики нолики":
        await GameState.wait_size.set()
        await m.answer("Оберіть режим:", reply_markup=modes_choice_kb())
    elif m.text == "❌ Скасувати":
        await state.finish()
        await m.answer("Головне меню", reply_markup=main_menu(m.from_user.id))

@dp.message_handler(state=GameState.wait_size)
async def game_size(m: types.Message, state: FSMContext):
    if m.text in ["3х3", "4х4"]:
        await state.update_data(sz=3 if "3" in m.text else 4)
        await GameState.wait_bet.set()
        await m.answer(f"Введіть ставку 💎:", reply_markup=cancel_kb())
    elif m.text == "❌ Скасувати":
        await state.finish()
        await m.answer("Скасовано", reply_markup=main_menu(m.from_user.id))

@dp.message_handler(state=GameState.wait_bet)
async def game_bet(m: types.Message, state: FSMContext):
    try:
        bet = float(m.text)
        u = await get_u(m)
        if u['balance'] < bet: return await m.answer("❌ Недостатньо 💎")
        data = await state.get_data()
        gid = str(uuid.uuid4())[:8]
        await users_col.update_one({"_id": m.from_user.id}, {"$inc": {"balance": -bet}})
        await games_col.insert_one({"game_id": gid, "creator_id": m.from_user.id, "bet": bet, "size": data['sz'], "board": [" "]*(data['sz']**2), "status": "waiting"})
        link = f"https://t.me/{(await bot.get_me()).username}?start=game_{gid}"
        await m.answer(f"Гра створена!\n`{link}`", parse_mode="Markdown", reply_markup=main_menu(m.from_user.id))
        await state.finish()
    except: await m.answer("❌ Введіть число!")

if __name__ == '__main__':
    executor.start_polling(dp, skip_updates=True)
