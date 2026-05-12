import os, uuid, logging, asyncio
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    ReplyKeyboardMarkup, KeyboardButton, 
    InlineKeyboardMarkup, InlineKeyboardButton
)
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

# --- НАЛАШТУВАННЯ ---
load_dotenv()
API_TOKEN = os.getenv("BOT_TOKEN") 
MONGO_URL = os.getenv("MONGO_URL")
ADMIN_ID = int(os.getenv("ADMIN_ID"))
CURRATE = 44.50 

logging.basicConfig(level=logging.INFO)
bot = Bot(token=API_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

cluster = AsyncIOMotorClient(MONGO_URL)
db = cluster["game_bot_db"] 
users_col = db["users"]
games_col = db["games"]
finance_col = db["finance"]
admin_col = db["admin_data"]

# --- СТАНИ ---
class GameStates(StatesGroup):
    wait_rounds = State()
    wait_bet = State()

class FinanceStates(StatesGroup):
    wait_dep_amount = State()
    wait_receipt = State()

# --- КЛАВІАТУРИ ---
def main_kb():
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="👤 Профіль"), KeyboardButton(text="🎮 Games")],
        [KeyboardButton(text="💎 Баланс"), KeyboardButton(text="🤝 Рефералка")],
        [KeyboardButton(text="⚙️ Налаштування")]
    ], resize_keyboard=True)

def cancel_kb():
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="❌ Скасувати")]], resize_keyboard=True)

async def get_u(uid, ref_id=None):
    u = await users_col.find_one({"_id": uid})
    if not u:
        u = {"_id": uid, "balance": 0.0, "referals_count": 0, "turnover": 0.0, "games_played": 0, "referrer": ref_id}
        await users_col.insert_one(u)
        if ref_id and ref_id != uid:
            await users_col.update_one({"_id": ref_id}, {"$inc": {"referals_count": 1}})
    return u

# --- ОБРОБКА КОМАНДИ /START (ВИПРАВЛЕНО) ---
@dp.message(Command("start"))
async def cmd_start(m: types.Message, state: FSMContext):
    await state.clear()
    args = m.text.split()
    user_id = m.from_user.id
    
    # Реєстрація або отримання даних
    ref_id = int(args[1]) if len(args) > 1 and args[1].isdigit() else None
    u = await get_u(user_id, ref_id)
    
    # Якщо перейшли за посиланням на гру
    if len(args) > 1 and args[1].startswith("game_"):
        gid = args[1].replace("game_", "")
        g = await games_col.find_one({"game_id": gid, "status": "waiting"})
        
        if not g:
            return await m.answer("❌ Гра не знайдена або вже почалася.", reply_markup=main_kb())
        
        if g['creator_id'] == user_id:
            return await m.answer("❌ Це твоє власне посилання.", reply_markup=main_kb())
            
        if u['balance'] < g['bet']:
            return await m.answer(f"❌ Треба `{g['bet']} 💎` для входу. Поповни баланс!", reply_markup=main_kb())
        
        # Списання та старт гри
        await users_col.update_many({"_id": {"$in": [g['creator_id'], user_id]}}, {"$inc": {"balance": -g['bet']}})
        await games_col.update_one({"game_id": gid}, {"$set": {"opponent_id": user_id, "status": "playing", "turn": g['creator_id']}})
        
        await bot.send_message(g['creator_id'], f"🎮 Гравець приєднався! Твій хід — кидай `{g['type']}`")
        return await m.answer(f"🎮 Ти у грі! Очікуй хід суперника `{g['type']}`", reply_markup=main_kb())

    await m.answer("💎 Ласкаво просимо до Different Games!", reply_markup=main_kb())

# --- ІГРИ (GAMES) ---
@dp.message(F.text == "🎮 Games")
async def games_menu(m: types.Message):
    kb = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="🎳 Боулінг"), KeyboardButton(text="🎲 Кубик")], [KeyboardButton(text="❌ Скасувати")]], resize_keyboard=True)
    await m.answer("Оберіть гру:", reply_markup=kb)

@dp.message(F.text.in_(["🎳 Боулінг", "🎲 Кубик"]))
async def g_mode(m: types.Message, state: FSMContext):
    await state.update_data(type="🎳" if "🎳" in m.text else "🎲")
    kb = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="1"), KeyboardButton(text="5")], [KeyboardButton(text="❌ Скасувати")]], resize_keyboard=True)
    await m.answer("Кількість раундів:", reply_markup=kb)
    await state.set_state(GameStates.wait_rounds)

@dp.message(GameStates.wait_rounds, F.text.in_(["1", "5"]))
async def g_rounds(m: types.Message, state: FSMContext):
    await state.update_data(r=int(m.text))
    await m.answer("Ставка (💎):", reply_markup=cancel_kb())
    await state.set_state(GameStates.wait_bet)

@dp.message(GameStates.wait_bet)
async def g_create(m: types.Message, state: FSMContext):
    if m.text == "❌ Скасувати":
        await state.clear()
        return await m.answer("Скасовано.", reply_markup=main_kb())
    try:
        bet = float(m.text.replace(",", "."))
        u = await get_u(m.from_user.id)
        if u['balance'] < bet: return await m.answer("❌ Мало грошей на балансі!")
        
        d = await state.get_data()
        gid = str(uuid.uuid4())[:8]
        await games_col.insert_one({
            "game_id": gid, "creator_id": m.from_user.id, "status": "waiting", 
            "type": d['type'], "bet": bet, "rounds_total": d['r'], 
            "c_score": [], "o_score": []
        })
        me = await bot.get_me()
        await m.answer(f"✅ Створено! Кидай посилання другу:\n`https://t.me/{me.username}?start=game_{gid}`", parse_mode="Markdown", reply_markup=main_kb())
        await state.clear()
    except: await m.answer("Введіть число!")

# --- ЛОГІКА ХОДІВ (DICE) ---
@dp.message(F.dice)
async def dice_handler(m: types.Message):
    g = await games_col.find_one({"status": "playing", "turn": m.from_user.id})
    if not g or m.dice.emoji != g['type']: return
    
    is_c = (m.from_user.id == g['creator_id'])
    f = "c_score" if is_c else "o_score"
    opp = g['opponent_id'] if is_c else g['creator_id']
    
    await games_col.update_one({"game_id": g['game_id']}, {"$push": {f: m.dice.value}})
    await m.forward(opp)
    
    upd = await games_col.find_one({"game_id": g['game_id']})
    if len(upd['c_score']) == upd['rounds_total'] and len(upd['o_score']) == upd['rounds_total']:
        # Визначення переможця
        s1, s2 = sum(upd['c_score']), sum(upd['o_score'])
        if s1 == s2:
            await users_col.update_many({"_id": {"$in": [upd['creator_id'], upd['opponent_id']]}}, {"$inc": {"balance": g['bet']}})
            for uid in [upd['creator_id'], upd['opponent_id']]: await bot.send_message(uid, "🤝 Нічия! Кошти повернуто.")
        else:
            win_id = upd['creator_id'] if s1 > s2 else upd['opponent_id']
            lose_id = upd['opponent_id'] if s1 > s2 else upd['creator_id']
            win_sum = g['bet'] * 1.98
            await users_col.update_one({"_id": win_id}, {"$inc": {"balance": win_sum, "turnover": g['bet']}})
            await bot.send_message(win_id, f"🏆 Перемога! Виграш: `{win_sum:.2f} 💎`", parse_mode="Markdown")
            await bot.send_message(lose_id, "❌ Ти програв.")
        await games_col.update_one({"game_id": g['game_id']}, {"$set": {"status": "finished"}})
    else:
        await games_col.update_one({"game_id": g['game_id']}, {"$set": {"turn": opp}})
        await bot.send_message(opp, f"Твій хід! Кидай {g['type']}")

# --- ПРОФІЛЬ ТА ФІНАНСИ ---
@dp.message(F.text == "👤 Профіль")
async def profile_cmd(m: types.Message):
    u = await get_u(m.from_user.id)
    await m.answer(f"👤 **Профіль**\n🆔 ID: `{u['_id']}`\n💰 Баланс: `{u['balance']:.2f} 💎`\n📈 Оборот: `{u['turnover']:.2f}`\n👥 Рефералів: {u['referals_count']}", parse_mode="Markdown")

@dp.message(F.text == "🤝 Рефералка")
async def referal_cmd(m: types.Message):
    me = await bot.get_me()
    await m.answer(f"🤝 Посилання для друзів:\n`https://t.me/{me.username}?start={m.from_user.id}`", parse_mode="Markdown")

@dp.message(F.text == "💎 Баланс")
async def balance_cmd(m: types.Message):
    u = await get_u(m.from_user.id)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="📥 Поповнити", callback_data="dep")]])
    await m.answer(f"💰 Баланс: `{u['balance']:.2f} 💎`", reply_markup=kb, parse_mode="Markdown")

@dp.callback_query(F.data == "dep")
async def dep_start(cb: types.CallbackQuery, state: FSMContext):
    await state.set_state(FinanceStates.wait_dep_amount)
    await cb.message.answer("💎 Сума поповнення (💎):", reply_markup=cancel_kb())

@dp.message(FinanceStates.wait_dep_amount)
async def dep_amt(m: types.Message, state: FSMContext):
    try:
        amt = float(m.text.replace(",", "."))
        total = (amt * CURRATE) * 1.05
        await state.update_data(amt=amt)
        await m.answer(f"До оплати: `{total:.2f} ГРН`\nРеквізити: `5355 2800 2890 2177`\n\n**Надішліть квитанцію!**", parse_mode="Markdown")
        await state.set_state(FinanceStates.wait_receipt)
    except: await m.answer("Введіть число!")

@dp.message(FinanceStates.wait_receipt, F.photo)
async def dep_receipt(m: types.Message, state: FSMContext):
    d = await state.get_data()
    tid = str(uuid.uuid4())[:8]
    await finance_col.insert_one({"_id": tid, "uid": m.from_user.id, "amt": d['amt']})
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✅", callback_data=f"f_ok_{tid}"), InlineKeyboardButton(text="❌", callback_data=f"f_no_{tid}")]])
    await bot.send_photo(ADMIN_ID, m.photo[-1].file_id, caption=f"📥 Чек на {d['amt']} від {m.from_user.id}", reply_markup=kb)
    await m.answer("✅ Чек відправлено!", reply_markup=main_kb())
    await state.clear()

# --- АДМІН-КНОПКИ ✅ ТА ❌ (ВИПРАВЛЕНО) ---
@dp.callback_query(F.data.startswith("f_ok_"))
async def admin_approve(cb: types.CallbackQuery):
    if cb.from_user.id != ADMIN_ID: return
    tid = cb.data[5:]; f = await finance_col.find_one({"_id": tid})
    if f:
        await users_col.update_one({"_id": f['uid']}, {"$inc": {"balance": f['amt']}})
        await bot.send_message(f['uid'], f"✅ Нараховано `{f['amt']} 💎`!", parse_mode="Markdown")
        await cb.message.delete(); await finance_col.delete_one({"_id": tid})

@dp.callback_query(F.data.startswith("f_no_"))
async def admin_reject(cb: types.CallbackQuery):
    if cb.from_user.id != ADMIN_ID: return
    tid = cb.data[5:]; f = await finance_col.find_one({"_id": tid})
    if f:
        await bot.send_message(f['uid'], "❌ Квитанцію відхилено.")
        await cb.message.delete(); await finance_col.delete_one({"_id": tid})

@dp.message(F.text == "⚙️ Налаштування")
async def settings(m: types.Message):
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🆘 Support", url="https://t.me/vex0o0")]])
    if m.from_user.id == ADMIN_ID:
        kb.inline_keyboard.append([InlineKeyboardButton(text="👨‍💻 Admin", callback_data="admin_panel")])
    await m.answer("Налаштування:", reply_markup=kb)

@dp.callback_query(F.data == "admin_panel")
async def admin_view(cb: types.CallbackQuery):
    if cb.from_user.id != ADMIN_ID: return
    count = await users_col.count_documents({})
    await cb.message.answer(f"📊 Юзерів у базі: {count}")

@dp.message(F.text == "❌ Скасувати")
async def global_cancel(m: types.Message, state: FSMContext):
    await state.clear(); await m.answer("Скасовано", reply_markup=main_kb())

async def main(): await dp.start_polling(bot)
if __name__ == "__main__": asyncio.run(main())
