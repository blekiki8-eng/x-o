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

# --- КОМАНДИ ТА ВХІД У ГРУ ---
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
                return await m.answer("⏳ Ви творець цієї гри. Очікуйте друга.")
            if u['balance'] < g['bet']: 
                return await m.answer("❌ Недостатньо коштів!")
            
            # Знімаємо ставку з другого гравця
            await users_col.update_one({"_id": uid}, {"$inc": {"balance": -round(g['bet'], 2)}})
            
            # Надсилаємо поле другому гравцю і зберігаємо його ID повідомлення
            opp_msg = await m.answer("🎮 Гра почалася! Твій символ ⭕️. Очікуй ходу суперника.", 
                                     reply_markup=get_board_markup(gid, g['board'], g['size']))
            
            await games_col.update_one({"game_id": gid}, {
                "$set": {
                    "opponent_id": uid, 
                    "status": "playing", 
                    "turn": g['creator_id'], 
                    "opponent_msg_id": opp_msg.message_id
                }
            })

            # ОНОВЛЕННЯ У ТВОРЦЯ (КРИТИЧНО)
            try:
                await bot.edit_message_text(
                    chat_id=g['creator_id'], 
                    message_id=g['creator_msg_id'], 
                    text=f"🎮 Гра почалася! Твій хід (❌)\nСтавка: {g['bet']} 💎",
                    reply_markup=get_board_markup(gid, g['board'], g['size'])
                )
            except Exception as e:
                # Якщо повідомлення не знайдено, шлемо нове
                new_m = await bot.send_message(g['creator_id'], "🎮 Гра почалася! Твій хід (❌)", 
                                               reply_markup=get_board_markup(gid, g['board'], g['size']))
                await games_col.update_one({"game_id": gid}, {"$set": {"creator_msg_id": new_m.message_id}})
            return

    await m.answer("Вітаємо у Different Games!", reply_markup=main_menu(uid))

# --- ЛОГІКА ХОДІВ (БЕЗ ДУБЛІВ) ---
@dp.callback_query_handler(lambda c: c.data.startswith("st:"), state="*")
async def game_move(c: types.CallbackQuery):
    _, gid, idx = c.data.split(":"); idx = int(idx)
    g = await games_col.find_one({"game_id": gid})
    
    if not g or g['status'] != "playing" or c.from_user.id != g['turn']:
        return await c.answer("Не твій хід!")
    
    char = "❌" if c.from_user.id == g['creator_id'] else "⭕️"
    new_board = list(g['board'])
    new_board[idx] = char
    next_p = g['opponent_id'] if c.from_user.id == g['creator_id'] else g['creator_id']
    
    await games_col.update_one({"game_id": gid}, {"$set": {"board": new_board, "turn": next_p}})
    
    win = check_win(new_board, g['size'])
    kb = get_board_markup(gid, new_board, g['size'])

    if win:
        await games_col.update_one({"game_id": gid}, {"$set": {"status": "finished"}})
        win_sum = round(g['bet'] * 2, 2)
        
        if win == "draw":
            await users_col.update_many({"_id": {"$in": [g['creator_id'], g['opponent_id']]}}, {"$inc": {"balance": round(g['bet'], 2)}})
            res_c = res_o = "🤝 Нічия! Ставки повернуті."
        else:
            winner_id = g['creator_id'] if win == "❌" else g['opponent_id']
            await users_col.update_one({"_id": winner_id}, {"$inc": {"balance": win_sum}})
            res_c = f"🎉 Перемога! +{win_sum} 💎" if winner_id == g['creator_id'] else "❌ Ви програли."
            res_o = f"🎉 Перемога! +{win_sum} 💎" if winner_id == g['opponent_id'] else "❌ Ви програли."

        await bot.edit_message_text(res_c, g['creator_id'], g['creator_msg_id'], reply_markup=kb)
        await bot.edit_message_text(res_o, g['opponent_id'], g['opponent_msg_id'], reply_markup=kb)
    else:
        # Той хто походив бачить очікування
        await bot.edit_message_text("⏳ Хід суперника...", c.from_user.id, c.message.message_id, reply_markup=kb)
        # Суперник бачить сигнал до дії
        target_msg = g['creator_msg_id'] if next_p == g['creator_id'] else g['opponent_msg_id']
        await bot.edit_message_text("🔔 Твій хід!", next_p, target_msg, reply_markup=kb)

# --- ПРОФІЛЬ ---
@dp.message_handler(lambda m: m.text == "👤 Профіль", state="*")
async def profile(m: types.Message):
    u = await get_u(m.from_user.id)
    await m.answer(f"👤 Профіль: {m.from_user.full_name}\n💰 Баланс: {round(u['balance'], 2)} 💎\n📥 Поповнено: {round(u.get('total_deposited', 0.0), 2)} 💎")

# --- СТВОРЕННЯ ГРИ ---
@dp.message_handler(state=GameState.wait_bet)
async def game_final(m: types.Message, state: FSMContext):
    try:
        bet = round(float(m.text.replace(",", ".")), 2)
        u = await get_u(m.from_user.id)
        if u['balance'] < bet: return await m.answer("❌ Недостатньо коштів!")
        
        d = await state.get_data(); gid = str(uuid.uuid4())[:8]
        bot_u = (await bot.get_me()).username
        
        # Творець отримує повідомлення і бот ПАМ'ЯТАЄ його ID
        msg = await m.answer(f"⏳ Очікуйте суперника...\nСтавка: {bet} 💎\n\nПосилання: `https://t.me/{bot_u}?start=game_{gid}`", 
                             parse_mode="Markdown", reply_markup=main_menu(m.from_user.id))
        
        await users_col.update_one({"_id": m.from_user.id}, {"$inc": {"balance": -bet}})
        await games_col.insert_one({
            "game_id": gid, "creator_id": m.from_user.id, "bet": bet, "size": d['s'], 
            "board": [" "]*(d['s']**2), "status": "waiting", "creator_msg_id": msg.message_id
        })
        await state.finish()
    except: await m.answer("Введіть число!")

# --- ПОПОВНЕННЯ (НОРМАЛЬНА ЗАЯВКА) ---
@dp.callback_query_handler(lambda c: c.data == "dep")
async def dep_1(c: types.CallbackQuery):
    await DepositState.wait_amount.set()
    await bot.send_message(c.from_user.id, "Введіть суму поповнення (мін. 0.50 💎):", reply_markup=cancel_kb())

@dp.message_handler(state=DepositState.wait_amount)
async def dep_2(m: types.Message, state: FSMContext):
    try:
        amt = float(m.text.replace(",", "."))
        if amt < 0.50: return await m.answer("❌ Мінімум 0.50")
        to_pay = round((amt * RATE) * 1.05, 2)
        await state.update_data(a=amt)
        await m.answer(f"💎 Сума: {amt}\n💵 До оплати: {to_pay} грн\n💳 Картка: `5355 2800 2890 2177`\n\nНадішліть ФОТО квитанції:", parse_mode="Markdown")
        await DepositState.wait_receipt.set()
    except: await m.answer("Введіть число!")

@dp.message_handler(state=DepositState.wait_receipt, content_types=['photo'])
async def dep_3(m: types.Message, state: FSMContext):
    data = await state.get_data()
    kb = InlineKeyboardMarkup().add(InlineKeyboardButton("✅ Ок", callback_data=f"adm_ok:{m.from_user.id}:{data['a']}"), 
                                   InlineKeyboardButton("❌ Ні", callback_data=f"adm_no:{m.from_user.id}"))
    await bot.send_photo(ADMIN_ID, m.photo[-1].file_id, caption=f"📥 Поповнення {data['a']} 💎 від {m.from_user.id}", reply_markup=kb)
    await m.answer("✅ Заявку надіслано адміну.", reply_markup=main_menu(m.from_user.id))
    await state.finish()

# (Решта коду для ігор, виводів, адмінки — така ж як раніше)
if __name__ == '__main__':
    executor.start_polling(dp, skip_updates=True)
