import os, uuid, logging, asyncio
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

# --- КОМАНДИ ---
@dp.message_handler(commands=['start'], state="*")
async def start_cmd(m: types.Message, state: FSMContext):
    await state.finish()
    args = m.get_args()
    uid = m.from_user.id
    u = await get_u(uid, m.from_user.full_name)

    if args and args.startswith("game_"):
        gid = args.replace("game_", "")
        g = await games_col.find_one({"game_id": gid, "status": "waiting"})
        if g:
            if g['creator_id'] == uid: 
                return await m.answer("⏳ Ви вже створили цю гру. Очікуйте суперника.")
            if u['balance'] < g['bet']: 
                return await m.answer("❌ Недостатньо коштів!")
            
            await users_col.update_one({"_id": uid}, {"$inc": {"balance": -round(g['bet'], 2)}})
            opp_msg = await m.answer("🎮 Гра почалася! Очікуйте ходу суперника.", reply_markup=get_board_markup(gid, g['board'], g['size']))
            
            await games_col.update_one({"game_id": gid}, {
                "$set": {"opponent_id": uid, "status": "playing", "turn": g['creator_id'], "opponent_msg_id": opp_msg.message_id}
            })
            # Оновлюємо гру у ТОГО, ХТО СТВОРИВ
            await bot.edit_message_text(chat_id=g['creator_id'], message_id=g['creator_msg_id'], text="🎮 Гра почалася! Твій хід (❌)", reply_markup=get_board_markup(gid, g['board'], g['size']))
            return

    await m.answer("Вітаємо у Different Games!", reply_markup=main_menu(uid))

@dp.message_handler(lambda m: m.text == "❌ Скасувати", state="*")
async def cancel_all(m: types.Message, state: FSMContext):
    await state.finish()
    await m.answer("Дію скасовано.", reply_markup=main_menu(m.from_user.id))

# --- ЛОГІКА ГРИ ---
@dp.callback_query_handler(lambda c: c.data.startswith("st:"), state="*")
async def game_move(c: types.CallbackQuery):
    _, gid, idx = c.data.split(":"); idx = int(idx)
    g = await games_col.find_one({"game_id": gid})
    if not g or g['status'] != "playing" or c.from_user.id != g['turn']: return
    
    char = "❌" if c.from_user.id == g['creator_id'] else "⭕️"
    new_board = list(g['board']); new_board[idx] = char
    next_p = g['opponent_id'] if c.from_user.id == g['creator_id'] else g['creator_id']
    await games_col.update_one({"game_id": gid}, {"$set": {"board": new_board, "turn": next_p}})
    
    win = check_win(new_board, g['size'])
    kb = get_board_markup(gid, new_board, g['size'])

    if win:
        await games_col.update_one({"game_id": gid}, {"$set": {"status": "finished"}})
        if win == "draw":
            await users_col.update_many({"_id": {"$in": [g['creator_id'], g['opponent_id']]}}, {"$inc": {"balance": round(g['bet'], 2)}})
            res_c = res_o = "🤝 Нічия! Ставки повернуті."
        else:
            winner_id = g['creator_id'] if win == "❌" else g['opponent_id']
            win_sum = round(g['bet'] * 2, 2)
            await users_col.update_one({"_id": winner_id}, {"$inc": {"balance": win_sum}})
            res_c = f"🎉 Перемога! +{win_sum} 💎" if winner_id == g['creator_id'] else "Нажаль ви програли("
            res_o = f"🎉 Перемога! +{win_sum} 💎" if winner_id == g['opponent_id'] else "Нажаль ви програли("

        await bot.edit_message_text(res_c, g['creator_id'], g['creator_msg_id'], reply_markup=kb)
        await bot.edit_message_text(res_o, g['opponent_id'], g['opponent_msg_id'], reply_markup=kb)
    else:
        try:
            await bot.edit_message_text("⏳ Хід суперника...", c.from_user.id, c.message.message_id, reply_markup=kb)
            target_msg = g['creator_msg_id'] if next_p == g['creator_id'] else g['opponent_msg_id']
            await bot.edit_message_text("🔔 Твій хід!", next_p, target_msg, reply_markup=kb)
        except: pass

# --- ПРОФІЛЬ ТА БАЛАНС ---
@dp.message_handler(lambda m: m.text == "👤 Профіль", state="*")
async def profile(m: types.Message):
    u = await get_u(m.from_user.id)
    await m.answer(f"👤 Профіль: {m.from_user.full_name}\n🆔 ID: `{m.from_user.id}`\n💰 Баланс: {round(u['balance'], 2)} 💎\n📥 Поповнено: {round(u.get('total_deposited', 0.0), 2)} 💎", parse_mode="Markdown")

@dp.message_handler(lambda m: m.text == "💎 Баланс", state="*")
async def balance_menu(m: types.Message):
    u = await get_u(m.from_user.id)
    kb = InlineKeyboardMarkup().add(InlineKeyboardButton("💳 Поповнити", callback_data="dep"), InlineKeyboardButton("📤 Вивести", callback_data="withdr"))
    await m.answer(f"💰 Баланс: {round(u['balance'], 2)} 💎", reply_markup=kb)

# --- ПОПОВНЕННЯ (НОРМАЛЬНА ЗАЯВКА) ---
@dp.callback_query_handler(lambda c: c.data == "dep")
async def dep_1(c: types.CallbackQuery):
    await DepositState.wait_amount.set()
    await bot.send_message(c.from_user.id, "Введіть суму поповнення (мін. 0.50 💎):", reply_markup=cancel_kb())

@dp.message_handler(state=DepositState.wait_amount)
async def dep_2(m: types.Message, state: FSMContext):
    try:
        amt = float(m.text.replace(",", "."))
        if amt < 0.50: return await m.answer("❌ Мінімальна сума 0.50")
        to_pay = round((amt * RATE) * 1.05, 2)
        await state.update_data(a=amt)
        await m.answer(f"💎 Сума: {amt}\n💵 До оплати: {to_pay} грн\n💳 Картка: `5355 2800 2890 2177`\n\nПісля оплати надішліть СКРІНШОТ (фото) квитанції:", parse_mode="Markdown")
        await DepositState.wait_receipt.set()
    except: await m.answer("Введіть число!")

@dp.message_handler(state=DepositState.wait_receipt, content_types=['photo'])
async def dep_3(m: types.Message, state: FSMContext):
    data = await state.get_data()
    kb = InlineKeyboardMarkup().add(InlineKeyboardButton("✅ Підтвердити", callback_data=f"adm_ok:{m.from_user.id}:{data['a']}"), InlineKeyboardButton("❌ Відхилити", callback_data=f"adm_no:{m.from_user.id}"))
    await bot.send_photo(ADMIN_ID, m.photo[-1].file_id, caption=f"📥 Заявка на {data['a']} 💎\nID: {m.from_user.id}", reply_markup=kb)
    await m.answer("✅ Заявку надіслано адміну. Очікуйте зарахування.", reply_markup=main_menu(m.from_user.id))
    await state.finish()

# --- ВИВЕДЕННЯ (МІН. 4) ---
@dp.callback_query_handler(lambda c: c.data == "withdr")
async def wit_1(c: types.CallbackQuery):
    await WithdrawState.wait_amount.set()
    await bot.send_message(c.from_user.id, "Введіть суму виведення (мін. 4.0 💎):", reply_markup=cancel_kb())

@dp.message_handler(state=WithdrawState.wait_amount)
async def wit_2(m: types.Message, state: FSMContext):
    try:
        amt = float(m.text.replace(",", "."))
        u = await get_u(m.from_user.id)
        if amt < 4.0: return await m.answer("❌ Мінімум 4.0")
        if u['balance'] < amt: return await m.answer("❌ Недостатньо коштів!")
        await state.update_data(a=amt)
        await m.answer("Введіть номер вашої картки:")
        await WithdrawState.wait_data.set()
    except: await m.answer("Введіть число!")

@dp.message_handler(state=WithdrawState.wait_data)
async def wit_3(m: types.Message, state: FSMContext):
    data = await state.get_data()
    await users_col.update_one({"_id": m.from_user.id}, {"$inc": {"balance": -round(data['a'], 2)}})
    await bot.send_message(ADMIN_ID, f"📤 ВИВІД: {data['a']} 💎\nКартка: {m.text}\nID: {m.from_user.id}")
    await m.answer("✅ Заявку на вивід прийнято!", reply_markup=main_menu(m.from_user.id))
    await state.finish()

# --- СТВОРЕННЯ ГРИ ---
@dp.message_handler(lambda m: m.text == "🎮 Ігри", state="*")
async def game_init(m: types.Message):
    await GameState.wait_game_type.set()
    await m.answer("Оберіть гру:", reply_markup=ReplyKeyboardMarkup(resize_keyboard=True).add("❌ Хрестики нолики", "❌ Скасувати"))

@dp.message_handler(state=GameState.wait_game_type)
async def game_type(m: types.Message, state: FSMContext):
    if m.text == "❌ Хрестики нолики":
        await GameState.wait_size.set()
        await m.answer("Поле:", reply_markup=ReplyKeyboardMarkup(resize_keyboard=True).add("3х3", "4х4", "❌ Скасувати"))

@dp.message_handler(state=GameState.wait_size)
async def game_size(m: types.Message, state: FSMContext):
    await state.update_data(s=3 if "3" in m.text else 4)
    await GameState.wait_bet.set()
    await m.answer("Ставка 💎:", reply_markup=cancel_kb())

@dp.message_handler(state=GameState.wait_bet)
async def game_final(m: types.Message, state: FSMContext):
    try:
        bet = round(float(m.text.replace(",", ".")), 2)
        u = await get_u(m.from_user.id)
        if u['balance'] < bet: return await m.answer("❌ Недостатньо коштів!")
        
        d = await state.get_data(); gid = str(uuid.uuid4())[:8]
        bot_u = (await bot.get_me()).username
        msg = await m.answer(f"Перешліть це другу!\n\nГра: Хрестики Нолики\nСтавка: {bet} 💎 виграш: {round(bet*2, 2)} 💎\nПосилання: https://t.me/{bot_u}?start=game_{gid}", reply_markup=main_menu(m.from_user.id))
        
        await users_col.update_one({"_id": m.from_user.id}, {"$inc": {"balance": -bet}})
        await games_col.insert_one({
            "game_id": gid, "creator_id": m.from_user.id, "bet": bet, "size": d['s'], 
            "board": [" "]*(d['s']**2), "status": "waiting", "creator_msg_id": msg.message_id
        })
        await state.finish()
    except: await m.answer("Введіть число!")

# --- РЕФЕРАЛКА ТА АДМІНКА ---
@dp.message_handler(lambda m: m.text == "🤝 Рефералка")
async def ref_sys(m: types.Message):
    bot_u = (await bot.get_me()).username
    await m.answer(f"Ваше посилання: `https://t.me/{bot_u}?start=ref_{m.from_user.id}`", parse_mode="Markdown")

@dp.callback_query_handler(lambda c: c.data.startswith("adm_"), state="*")
async def adm_proc(c: types.CallbackQuery):
    if c.from_user.id != ADMIN_ID: return
    p = c.data.split(":"); uid = int(p[1])
    if "ok" in p[0]:
        amt = float(p[2])
        await users_col.update_one({"_id": uid}, {"$inc": {"balance": amt, "total_deposited": amt}})
        await bot.send_message(uid, f"✅ Баланс поповнено на {amt} 💎")
    await c.message.delete()

@dp.message_handler(lambda m: m.text == "🛡 Панель адміна")
async def adm_pan(m: types.Message):
    if m.from_user.id != ADMIN_ID: return
    users = await users_col.find().limit(10).to_list(length=10)
    kb = InlineKeyboardMarkup()
    for u in users: kb.add(InlineKeyboardButton(f"{u['_id']}", callback_data=f"info:{u['_id']}"))
    await m.answer("Гравці:", reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data.startswith("info:"))
async def adm_inf(c: types.CallbackQuery):
    u = await users_col.find_one({"_id": int(c.data.split(":")[1])})
    await bot.send_message(c.from_user.id, f"Баланс: {u['balance']} 💎\nПоповнено: {u.get('total_deposited', 0.0)} 💎")

if __name__ == '__main__':
    executor.start_polling(dp, skip_updates=True)
