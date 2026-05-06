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

MOVE_TIMEOUT, EXCHANGE_RATE, BONUS_PERCENT = 40, 44.50, 0.05
MIN_DEPOSIT, MIN_WITHDRAW, MIN_BET = 0.50, 4.0, 0.05

logging.basicConfig(level=logging.INFO)
storage = MemoryStorage()
bot = Bot(token=API_TOKEN)
dp = Dispatcher(bot, storage=storage)

cluster = AsyncIOMotorClient(MONGO_URL)
db = cluster["fishcash_game"]
users_col, games_col = db["users"], db["games"]

class DepositState(StatesGroup):
    wait_amount = State()
    wait_receipt = State()

class WithdrawState(StatesGroup):
    wait_amount = State()
    wait_details = State()

class GameState(StatesGroup):
    wait_bet = State()

def main_menu():
    markup = ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add(KeyboardButton("👤 Профіль"), KeyboardButton("🎮 Ігри"))
    markup.add(KeyboardButton("💎 Баланс"))
    return markup

def cancel_keyboard():
    return ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True).add("❌ Скасувати")

def get_board_markup(game_id, board):
    markup = InlineKeyboardMarkup(row_width=3)
    btns = [InlineKeyboardButton(board[i] if board[i] != " " else "⬜️", callback_data=f"ticstep_{game_id}_{i}") for i in range(9)]
    return markup.add(*btns)

def check_winner(b):
    win_coords = [(0,1,2), (3,4,5), (6,7,8), (0,3,6), (1,4,7), (2,5,8), (0,4,8), (2,4,6)]
    for r in win_coords:
        if b[r[0]] == b[r[1]] == b[r[2]] != " ": return b[r[0]]
    return "draw" if " " not in b else None

async def update_game_messages(game, board, winner=None):
    markup = get_board_markup(game['game_id'], board)
    players = [
        {'id': game['creator_id'], 'mid': game.get('creator_msg_id')},
        {'id': game['opponent_id'], 'mid': game.get('opponent_msg_id')}
    ]
    for p in players:
        if not p['id'] or not p['mid']: continue
        if winner:
            if winner == "draw": text = "🤝 **Нічия!** Кошти повернуто."
            else:
                is_win = (winner == "X" and p['id'] == game['creator_id']) or (winner == "O" and p['id'] == game['opponent_id'])
                text = "🏁 **Ви виграли!**" if is_win else "❌ **Ви програли.**"
        else:
            text = "🎮 **Твій хід!**" if p['id'] == game['turn'] else "⏳ **Хід суперника...**"

        try:
            await bot.edit_message_text(text, p['id'], p['mid'], reply_markup=markup, parse_mode="Markdown")
        except: pass

@dp.message_handler(state="*", text="❌ Скасувати")
async def cancel_action(m: types.Message, state: FSMContext):
    await state.finish()
    await m.answer("Дію скасовано.", reply_markup=main_menu())

@dp.message_handler(commands=['start'], state="*")
async def start_cmd(m: types.Message, state: FSMContext):
    await state.finish()
    if not await users_col.find_one({"_id": m.from_user.id}):
        await users_col.insert_one({"_id": m.from_user.id, "balance": 0.0})
    
    args = m.get_args()
    if args and args.startswith("game_"):
        gid = args.split("_")[1]
        g = await games_col.find_one({"game_id": gid, "status": "waiting"})
        
        if g:
            if g['creator_id'] == m.from_user.id:
                return await m.answer("❌ Ви не можете грати самі з собою.")
            
            u = await users_col.find_one({"_id": m.from_user.id})
            if u['balance'] >= g['bet']:
                await users_col.update_one({"_id": m.from_user.id}, {"$inc": {"balance": -g['bet']}})
                
                # Повідомлення про початок гри
                await m.answer("Суперник зайшов, Гра почалась ✅")
                try: await bot.send_message(g['creator_id'], "Суперник зайшов, Гра почалась ✅")
                except: pass
                
                mo = await m.answer("⏳ Завантаження поля...", reply_markup=get_board_markup(gid, g['board']))
                
                upd = {
                    "opponent_id": m.from_user.id, 
                    "opponent_msg_id": mo.message_id, 
                    "status": "playing", 
                    "turn": g['creator_id'], 
                    "last_move_time": time.time()
                }
                await games_col.update_one({"game_id": gid}, {"$set": upd})
                g.update(upd)
                await update_game_messages(g, g['board'])
                return
            else: return await m.answer("❌ Недостатньо балансу.")
            
    await m.answer("Головне меню", reply_markup=main_menu())

@dp.message_handler(lambda m: m.text == "👤 Профіль", state="*")
async def profile_view(m: types.Message):
    u = await users_col.find_one({"_id": m.from_user.id})
    await m.answer(f"👤 **Профіль**\n🆔 ID: `{m.from_user.id}`\n💰 Баланс: {round(u['balance'], 2)} 💎", parse_mode="Markdown")

@dp.message_handler(lambda m: m.text == "💎 Баланс", state="*")
async def balance_view(m: types.Message):
    u = await users_col.find_one({"_id": m.from_user.id})
    kb = InlineKeyboardMarkup().add(
        InlineKeyboardButton("💳 Поповнити", callback_data="deposit"),
        InlineKeyboardButton("📤 Вивести", callback_data="withdraw_start")
    )
    await m.answer(f"💎 Баланс: **{round(u['balance'], 2)}**", reply_markup=kb, parse_mode="Markdown")

@dp.message_handler(lambda m: m.text == "🎮 Ігри", state="*")
async def games_menu(m: types.Message):
    kb = InlineKeyboardMarkup().add(InlineKeyboardButton("❌ Хрестики-нолики ⭕️", callback_data="tic_info"))
    await m.answer("Оберіть гру:", reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data == "tic_info", state="*")
async def tic_info(c: types.CallbackQuery):
    kb = InlineKeyboardMarkup().add(InlineKeyboardButton("🟩 Створити матч", callback_data="tic_create"))
    await c.message.edit_text("🕹 **Хрестики-нолики**", reply_markup=kb, parse_mode="Markdown")

@dp.callback_query_handler(lambda c: c.data == "tic_create", state="*")
async def tic_create_req(c: types.CallbackQuery):
    await bot.send_message(c.from_user.id, f"💰 Введіть ставку (мін. {MIN_BET} 💎):", reply_markup=cancel_keyboard())
    await GameState.wait_bet.set()
    await c.answer()

@dp.message_handler(state=GameState.wait_bet)
async def tic_set_bet(m: types.Message, state: FSMContext):
    try:
        bet = float(m.text.replace(',', '.'))
        u = await users_col.find_one({"_id": m.from_user.id})
        if bet < MIN_BET:
            return await m.answer(f"❌ Мінімальна ставка {MIN_BET}")
        if u['balance'] < bet:
            return await m.answer("❌ Недостатньо коштів!")
        
        gid = str(uuid.uuid4())[:8]
        await users_col.update_one({"_id": m.from_user.id}, {"$inc": {"balance": -bet}})
        
        # Стан завершуємо ВІДРАЗУ, щоб не було помилок "введіть число"
        await state.finish()

        msg = await m.answer("⏳ Чекаємо суперника...", reply_markup=main_menu())
        
        link_text = (
            f"🎮 **Гра: Хрестики-нолики**\n"
            f"💰 **Ставка: {bet} 💎**\n\n"
            f"Приєднуйся за посиланням:\n"
            f"https://t.me/{(await bot.get_me()).username}?start=game_{gid}"
        )
        await m.answer(link_text, disable_web_page_preview=True)
        
        await games_col.insert_one({
            "game_id": gid, "creator_id": m.from_user.id, "opponent_id": None, 
            "bet": bet, "board": [" "]*9, "status": "waiting", "turn": None, 
            "creator_msg_id": msg.message_id, "last_move_time": time.time()
        })
    except ValueError:
        await m.answer("❌ Введіть коректне число.")

@dp.callback_query_handler(lambda c: c.data == "withdraw_start", state="*")
async def withdraw_init(c: types.CallbackQuery, state: FSMContext):
    await state.finish()
    await bot.send_message(c.from_user.id, "📤 Введіть суму для виведення:", reply_markup=cancel_keyboard())
    await WithdrawState.wait_amount.set()
    await c.answer()

@dp.message_handler(state=WithdrawState.wait_amount)
async def withdraw_amount(m: types.Message, state: FSMContext):
    try:
        amt = float(m.text.replace(',', '.'))
        u = await users_col.find_one({"_id": m.from_user.id})
        if u['balance'] < amt: return await m.answer("❌ Недостатньо 💎")
        await state.update_data(wa=amt)
        await m.answer("Введіть реквізити:", reply_markup=cancel_keyboard())
        await WithdrawState.wait_details.set()
    except: await m.answer("❌ Введіть число!")

@dp.message_handler(state=WithdrawState.wait_details)
async def withdraw_final(m: types.Message, state: FSMContext):
    d = await state.get_data()
    await users_col.update_one({"_id": m.from_user.id}, {"$inc": {"balance": -d['wa']}})
    await bot.send_message(ADMIN_ID, f"📤 Вивід: {d['wa']} 💎\nID: {m.from_user.id}\nДані: {m.text}")
    await m.answer("✅ Заявка на виведення прийнята!", reply_markup=main_menu())
    await state.finish()

@dp.callback_query_handler(lambda c: c.data == "deposit", state="*")
async def deposit_start(c: types.CallbackQuery, state: FSMContext):
    await state.finish()
    await bot.send_message(c.from_user.id, f"💰 Сума в 💎 (мін. {MIN_DEPOSIT}):", reply_markup=cancel_keyboard())
    await DepositState.wait_amount.set()
    await c.answer()

@dp.message_handler(state=DepositState.wait_amount)
async def deposit_amount(m: types.Message, state: FSMContext):
    try:
        amt = float(m.text.replace(',', '.'))
        if amt < MIN_DEPOSIT: return await m.answer(f"❌ Мінімум {MIN_DEPOSIT}")
        total_uah = (amt * EXCHANGE_RATE) * (1 + BONUS_PERCENT)
        await state.update_data(deposit_amt=amt)
        cap = (f"📄 **Заявка**\n💎: {amt}\n💳 **До сплати: {round(total_uah, 2)} ₴ (+5%)**\n\n"
               f"Карта: `5355 2800 2890 2177`\nНадішліть фото чека:")
        await m.answer(cap, parse_mode="Markdown", reply_markup=cancel_keyboard())
        await DepositState.wait_receipt.set()
    except: await m.answer("❌ Введіть число!")

@dp.message_handler(state=DepositState.wait_receipt, content_types=['photo'])
async def deposit_receipt(m: types.Message, state: FSMContext):
    data = await state.get_data()
    kb = InlineKeyboardMarkup().add(InlineKeyboardButton("✅", callback_data=f"ok_{m.from_user.id}_{data['deposit_amt']}"), InlineKeyboardButton("❌", callback_data=f"no_{m.from_user.id}"))
    await bot.send_photo(ADMIN_ID, m.photo[-1].file_id, caption=f"Чек від {m.from_user.id}\n💎: {data['deposit_amt']}", reply_markup=kb)
    await m.answer("✅ Чек надіслано!", reply_markup=main_menu())
    await state.finish()

@dp.callback_query_handler(lambda c: c.data.startswith(('ok_', 'no_')), state="*")
async def admin_verify(c: types.CallbackQuery):
    if c.from_user.id != ADMIN_ID: return
    p = c.data.split("_")
    if p[0] == "ok":
        await users_col.update_one({"_id": int(p[1])}, {"$inc": {"balance": float(p[2])}})
        try: await bot.send_message(int(p[1]), "✅ Баланс поповнено!")
        except: pass
    await c.message.edit_caption("✅ Готово")
    await c.answer()

@dp.callback_query_handler(lambda c: c.data.startswith('ticstep_'), state="*")
async def tic_step(c: types.CallbackQuery):
    _, gid, idx = c.data.split("_")
    g = await games_col.find_one({"game_id": gid})
    if not g or g['status'] != "playing" or c.from_user.id != g['turn'] or g['board'][int(idx)] != " ":
        return await c.answer("Не ваш хід!")
    
    nb = list(g['board'])
    nb[int(idx)] = "X" if c.from_user.id == g['creator_id'] else "O"
    win = check_winner(nb)
    nxt = g['opponent_id'] if c.from_user.id == g['creator_id'] else g['creator_id']
    
    await games_col.update_one({"game_id": gid}, {"$set": {"board": nb, "turn": nxt, "last_move_time": time.time()}})
    
    if win:
        await games_col.update_one({"game_id": gid}, {"$set": {"status": "finished"}})
        if win != "draw":
            wid = g['creator_id'] if win == "X" else g['opponent_id']
            await users_col.update_one({"_id": wid}, {"$inc": {"balance": g['bet']*2}})
        else:
            await users_col.update_many({"_id": {"$in": [g['creator_id'], g['opponent_id']]}}, {"$inc": {"balance": g['bet']}})
    
    g['board'], g['turn'] = nb, nxt
    await update_game_messages(g, nb, win)
    await c.answer()

async def check_timeouts():
    while True:
        try:
            async for game in games_col.find({"status": "playing"}):
                if time.time() - game.get('last_move_time', 0) > MOVE_TIMEOUT:
                    win_id = game['opponent_id'] if game['turn'] == game['creator_id'] else game['creator_id']
                    await games_col.update_one({"game_id": game['game_id']}, {"$set": {"status": "finished"}})
                    await users_col.update_one({"_id": win_id}, {"$inc": {"balance": game['bet'] * 2}})
                    await update_game_messages(game, game['board'], winner="X" if win_id == game['creator_id'] else "O")
        except: pass
        await asyncio.sleep(5)

if __name__ == '__main__':
    loop = asyncio.get_event_loop()
    loop.create_task(check_timeouts())
    executor.start_polling(dp, skip_updates=True)
