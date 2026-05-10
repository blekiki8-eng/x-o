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

logging.basicConfig(level=logging.INFO)
storage = MemoryStorage()
bot = Bot(token=API_TOKEN)
dp = Dispatcher(bot, storage=storage)

cluster = AsyncIOMotorClient(MONGO_URL)
db = cluster["game_bot_db"]
users_col = db["users"]
games_col = db["games"]

# --- КЛАВІАТУРИ ---
def main_menu(user_id):
    markup = ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add(KeyboardButton("👤 Профіль"), KeyboardButton("🎮 Ігри"))
    markup.add(KeyboardButton("💎 Баланс"), KeyboardButton("🤝 Рефералка"))
    if user_id == ADMIN_ID: markup.add(KeyboardButton("🛡 Панель адміна"))
    return markup

def get_board_markup(game_id, board, size):
    markup = InlineKeyboardMarkup(row_width=size)
    btns = [InlineKeyboardButton(board[i] if board[i] != " " else "⬜️", callback_data=f"st:{game_id}:{i}") for i in range(size*size)]
    return markup.add(*btns)

# --- ЛОГІКА ХОДУ (БЕЗ СТВОРЕННЯ НОВИХ ПОВІДОМЛЕНЬ) ---
@dp.callback_query_handler(lambda c: c.data.startswith("st:"), state="*")
async def game_move(c: types.CallbackQuery):
    _, gid, idx = c.data.split(":"); idx = int(idx)
    g = await games_col.find_one({"game_id": gid})
    
    if not g or g['status'] != "playing":
        return await c.answer("Гра вже завершена.")
    
    if c.from_user.id != g['turn']:
        return await c.answer("⏳ Зараз не твій хід!", show_alert=True)
        
    if g['board'][idx] != " ":
        return await c.answer("Клітинка зайнята!")

    char = "❌" if c.from_user.id == g['creator_id'] else "⭕️"
    new_board = list(g['board'])
    new_board[idx] = char
    
    # Визначаємо ID повідомлень обох гравців, щоб редагувати їх
    # Для цього нам треба зберігати message_id при старті гри (додано в логіку нижче)
    
    next_p = g['opponent_id'] if c.from_user.id == g['creator_id'] else g['creator_id']
    await games_col.update_one({"game_id": gid}, {"$set": {"board": new_board, "turn": next_p}})
    
    win = check_win(new_board, g['size'])
    kb = get_board_markup(gid, new_board, g['size'])

    if win:
        await games_col.update_one({"game_id": gid}, {"$set": {"status": "finished"}})
        winner_id = None
        if win == "draw":
            await users_col.update_many({"_id": {"$in": [g['creator_id'], g['opponent_id']]}}, {"$inc": {"balance": float(g['bet'])}})
            res_creator = res_opponent = "🤝 Нічия! Ставки повернуті."
        else:
            winner_id = g['creator_id'] if win == "❌" else g['opponent_id']
            loser_id = g['opponent_id'] if win == "❌" else g['creator_id']
            win_sum = float(g['bet']) * 2
            await users_col.update_one({"_id": winner_id}, {"$inc": {"balance": win_sum}})
            
            # Реф-бонус
            win_u = await users_col.find_one({"_id": winner_id})
            if win_u.get('invited_by'):
                bonus = round(win_sum * 0.0001, 6)
                await users_col.update_one({"_id": win_u['invited_by']}, {"$inc": {"balance": bonus}})
                try: await bot.send_message(win_u['invited_by'], f"📈 реферальний бонус +{bonus} 💎")
                except: pass

            res_winner = f"🎉 Вітаю з перемогою! На баланс нараховано +{win_sum} 💎"
            res_loser = "Нажаль ви програли( на наступний раз повезе!"
            
            res_creator = res_winner if winner_id == g['creator_id'] else res_loser
            res_opponent = res_winner if winner_id == g['opponent_id'] else res_loser

        # Оновлюємо фінальні повідомлення
        try:
            await bot.edit_message_text(res_creator, g['creator_id'], g['creator_msg_id'], reply_markup=kb)
            await bot.edit_message_text(res_opponent, g['opponent_id'], g['opponent_msg_id'], reply_markup=kb)
        except: pass
    else:
        # ОСНОВНИЙ МОМЕНТ: Редагуємо повідомлення в обох гравців замість send_message
        try:
            # Тому хто походив, пишемо "Хід суперника"
            await bot.edit_message_text("⏳ Хід суперника...", c.from_user.id, c.message.message_id, reply_markup=kb)
            # Супернику оновлюємо поле і пишемо "Твій хід"
            await bot.edit_message_text("🔔 Твій хід!", next_p, g['creator_msg_id'] if next_p == g['creator_id'] else g['opponent_msg_id'], reply_markup=kb)
        except:
            pass

# --- ВХІД У ГРУ (ЗБЕРЕЖЕННЯ ID ПОВІДОМЛЕНЬ) ---
@dp.message_handler(commands=['start'], state="*")
async def start_cmd(m: types.Message, state: FSMContext):
    args = m.get_args()
    if args and args.startswith("game_"):
        gid = args.replace("game_", "")
        g = await games_col.find_one({"game_id": gid, "status": "waiting"})
        if g and g['creator_id'] != m.from_user.id:
            u = await users_col.find_one({"_id": m.from_user.id})
            if u['balance'] >= g['bet']:
                await users_col.update_one({"_id": m.from_user.id}, {"$inc": {"balance": -float(g['bet'])}})
                
                # Відправляємо повідомлення опоненту і зберігаємо його ID
                opp_msg = await m.answer("🎮 Гра почалася! Очікуй ходу суперника.", reply_markup=get_board_markup(gid, g['board'], g['size']))
                
                await games_col.update_one({"game_id": gid}, {
                    "$set": {
                        "opponent_id": m.from_user.id, 
                        "status": "playing", 
                        "turn": g['creator_id'],
                        "opponent_msg_id": opp_msg.message_id
                    }
                })
                # Оновлюємо повідомлення творцю
                await bot.edit_message_text("🎮 Гра почалася! Твій хід (❌)", g['creator_id'], g['creator_msg_id'], reply_markup=get_board_markup(gid, g['board'], g['size']))
                return
    # ... інша логіка старту ...

# --- СТВОРЕННЯ ГРИ ---
@dp.message_handler(state=GameState.wait_bet)
async def game_bet(m: types.Message, state: FSMContext):
    try:
        bet = float(m.text.replace(",", "."))
        gid = str(uuid.uuid4())[:8]
        d = await state.get_data()
        
        # Створюємо повідомлення-заглушку, яке потім будемо редагувати
        msg = await m.answer(f"⏳ Очікуйте суперника...\nСтавка: {bet} 💎\nПосилання: `https://t.me/{(await bot.get_me()).username}?start=game_{gid}`", parse_mode="Markdown")
        
        await games_col.insert_one({
            "game_id": gid, "creator_id": m.from_user.id, "bet": bet, "size": d['s'], 
            "board": [" "]*(d['s']**2), "status": "waiting", "creator_msg_id": msg.message_id
        })
        await state.finish()
    except: pass

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

# (Всі інші функції як у попередньому коді: Баланс, Профіль, Адмінка)
