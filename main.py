import os, uuid, logging, asyncio
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove, 
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
stats_col = db["stats"]

# --- СТАНИ ---
class GameStates(StatesGroup):
    wait_rounds = State()
    wait_bet = State()

class FinanceStates(StatesGroup):
    wait_dep_amount = State()
    wait_receipt = State()
    wait_withdraw_amount = State()
    wait_withdraw_details = State()

# --- КЛАВІАТУРИ ---
def main_kb():
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="👤 Профіль"), KeyboardButton(text="🎮 Games")],
        [KeyboardButton(text="💎 Баланс"), KeyboardButton(text="🤝 Рефералка")],
        [KeyboardButton(text="⚙️ Налаштування")]
    ], resize_keyboard=True)

def cancel_kb():
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="❌ Скасувати")]], resize_keyboard=True)

def rounds_kb():
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="1 раунд"), KeyboardButton(text="5 раундів")],
        [KeyboardButton(text="⬅️ Назад")]
    ], resize_keyboard=True)

# --- ДОПОМІЖНІ ФУНКЦІЇ ---
async def get_u(uid, ref_id=None):
    u = await users_col.find_one({"_id": uid})
    if not u:
        u = {
            "_id": uid, "balance": 0.0, "referals_count": 0, 
            "turnover": 0.0, "games_played": 0, "referrer": ref_id,
            "is_active_ref": False
        }
        await users_col.insert_one(u)
    return u

async def add_admin_profit(amount):
    await stats_col.update_one({"_id": "global"}, {"$inc": {"admin_profit": amount}}, upsert=True)

# --- ГЛОБАЛЬНЕ СКАСУВАННЯ ---
@dp.message(F.text == "❌ Скасувати")
async def global_cancel(m: types.Message, state: FSMContext):
    await state.clear()
    await m.answer("Дію скасовано ↩️", reply_markup=main_kb())

# --- ПРОФІЛЬ ---
@dp.message(F.text == "👤 Профіль")
async def profile_cmd(m: types.Message, state: FSMContext):
    await state.clear()
    u = await get_u(m.from_user.id)
    text = (f"👤 **Профіль:**\n\n"
            f"🆔 Id: `{m.from_user.id}`\n"
            f"💰 Баланс: `{u.get('balance', 0.0):.2f}` 💎\n\n"
            f"Запрошених гравців: `{u.get('referals_count', 0)}`👤\n"
            f"Оборот: `{u.get('turnover', 0.0):.2f}` 💎\n"
            f"Зіграно ігор: `{u.get('games_played', 0)}` 🎳")
    await m.answer(text, parse_mode="Markdown", reply_markup=main_kb())

# --- СИСТЕМА ПОПОВНЕННЯ ---
@dp.callback_query(F.data == "dep")
async def start_deposit(cb: types.CallbackQuery, state: FSMContext):
    await state.set_state(FinanceStates.wait_dep_amount)
    await cb.message.answer("Введіть суму поповнення (мінімум 0.50 💎):", reply_markup=cancel_kb())

@dp.message(FinanceStates.wait_dep_amount)
async def dep_amount(m: types.Message, state: FSMContext):
    try:
        amt = float(m.text.replace(",", "."))
        if amt < 0.50: return await m.answer("❌ Мінімальна сума — 0.50 💎")
        await state.update_data(amt=amt)
        total_uah = (amt * CURRATE) * 1.05
        text = (f"Курс: {CURRATE:.2f}\nДо оплати: {total_uah:.2f} ГРН ({amt} 💎 + 5%)\n\n"
                f"Карта: `5355 2800 2890 2177`\n\n**(Обов’язково сюди кидайте квитанцію)**")
        await m.answer(text, parse_mode="Markdown", reply_markup=cancel_kb())
        await state.set_state(FinanceStates.wait_receipt)
    except: await m.answer("Введіть число!")

@dp.message(FinanceStates.wait_receipt, F.photo | F.document)
async def dep_receipt(m: types.Message, state: FSMContext):
    data = await state.get_data(); amt = data['amt']; uid = m.from_user.id
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Схвалити", callback_data=f"adm_ok_{uid}_{amt}"),
        InlineKeyboardButton(text="❌ Відхилити", callback_data=f"adm_no_{uid}")
    ]])
    await bot.send_message(ADMIN_ID, f"🔔 Чек на `{amt} 💎` від `{uid}`")
    if m.photo: await bot.send_photo(ADMIN_ID, m.photo[-1].file_id, reply_markup=kb)
    else: await bot.send_document(ADMIN_ID, m.document.file_id, reply_markup=kb)
    await m.answer("⏳ Чек надіслано адміну.", reply_markup=main_kb())
    await state.clear()

# --- СИСТЕМА ВИВЕДЕННЯ ---
@dp.callback_query(F.data == "with")
async def start_withdraw(cb: types.CallbackQuery, state: FSMContext):
    await state.set_state(FinanceStates.wait_withdraw_amount)
    await cb.message.answer("Введіть суму для виводу (мінімум 4.5 💎):", reply_markup=cancel_kb())

@dp.message(FinanceStates.wait_withdraw_amount)
async def withdraw_amount(m: types.Message, state: FSMContext):
    try:
        amt = float(m.text.replace(",", "."))
        u = await get_u(m.from_user.id)
        if amt < 4.5: return await m.answer("❌ Мінімум 4.5 💎", reply_markup=cancel_kb())
        if u.get('balance', 0) < amt: return await m.answer("❌ Недостатньо балансу!", reply_markup=cancel_kb())
        await state.update_data(w_amt=amt)
        await m.answer(f"Сума для виводу: {amt}\nВаші дані: **ІПН IBAN ПІБ**\n\nВведіть дані одним повідомленням:", reply_markup=cancel_kb())
        await state.set_state(FinanceStates.wait_withdraw_details)
    except: await m.answer("Введіть число")

@dp.message(FinanceStates.wait_withdraw_details)
async def withdraw_final(m: types.Message, state: FSMContext):
    data = await state.get_data(); amt = data['w_amt']
    user_nick = f"@{m.from_user.username}" if m.from_user.username else "без ніку"
    await users_col.update_one({"_id": m.from_user.id}, {"$inc": {"balance": -amt}})
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Вивід виконано ✅", callback_data=f"wd_ok_{m.from_user.id}_{amt}")],
        [InlineKeyboardButton(text="Вивід відхилено ❌", callback_data=f"wd_no_{m.from_user.id}_{amt}")]
    ])
    admin_msg = (f"📤 **ЗАЯВКА НА ВИВІД**\n\n👤 Гравець: {user_nick} (ID: `{m.from_user.id}`)\n💰 Сума: `{amt} 💎`\n📝 Дані: {m.text}")
    await bot.send_message(ADMIN_ID, admin_msg, parse_mode="Markdown", reply_markup=kb)
    await m.answer("✅ Заявка надіслана.", reply_markup=main_kb())
    await state.clear()

# --- ОБРОБКА АДМІН-КНОПОК ---
@dp.callback_query(F.data.startswith("adm_"))
async def admin_deposit_confirm(cb: types.CallbackQuery):
    _, action, uid, amt = cb.data.split("_")
    uid, amt = int(uid), float(amt)
    if action == "ok":
        u = await get_u(uid)
        # АКТИВАЦІЯ РЕФЕРАЛА ПІСЛЯ ДОНАТУ
        if not u.get("is_active_ref") and u.get("referrer"):
            await users_col.update_one({"_id": u["referrer"]}, {"$inc": {"referals_count": 1}})
            await users_col.update_one({"_id": uid}, {"$set": {"is_active_ref": True}})
            try: await bot.send_message(u["referrer"], "🤝 Ваш реферал зробив донат! Тепер ви отримуєте бонуси.")
            except: pass
        await users_col.update_one({"_id": uid}, {"$inc": {"balance": amt}})
        await bot.send_message(uid, f"✅ Баланс поповнено на {amt} 💎!")
        await cb.message.answer("Схвалено ✅")
    else:
        await bot.send_message(uid, "❌ Поповнення відхилено."); await cb.message.answer("Відхилено ❌")

@dp.callback_query(F.data.startswith("wd_"))
async def admin_withdraw_confirm(cb: types.CallbackQuery):
    _, action, uid, amt = cb.data.split("_")
    uid, amt = int(uid), float(amt)
    if action == "ok":
        await bot.send_message(uid, f"✅ Ваш вивід {amt} 💎 виконано!"); await cb.message.edit_text("Виконано ✅")
    else:
        await users_col.update_one({"_id": uid}, {"$inc": {"balance": amt}})
        await bot.send_message(uid, f"❌ Вивід відхилено. Кошти повернуто."); await cb.message.edit_text("Відхилено ❌")

# --- ІГРОВА ЛОГІКА ---
@dp.message(F.text == "🎮 Games")
async def games_menu(m: types.Message, state: FSMContext):
    await state.clear()
    kb = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="Гра Боулінг 🎳"), KeyboardButton(text="Гра Кубик 🎲")], [KeyboardButton(text="⬅️ Назад")]], resize_keyboard=True)
    await m.answer("Оберіть гру:", reply_markup=kb)

async def join_game_logic(m, gid):
    g = await games_col.find_one({"game_id": gid, "status": "waiting"})
    if not g or g['creator_id'] == m.from_user.id: return await m.answer("❌ Гра недоступна")
    u2 = await get_u(m.from_user.id)
    if u2.get('balance', 0) < g['bet']: return await m.answer("❌ Мало коштів")
    await users_col.update_many({"_id": {"$in": [g['creator_id'], m.from_user.id]}}, {"$inc": {"balance": -g['bet'], "games_played": 1}})
    await games_col.update_one({"game_id": gid}, {"$set": {"opponent_id": m.from_user.id, "status": "playing", "turn": g['creator_id']}})
    await bot.send_message(g['creator_id'], f"Гру розпочато! Кидайте {g['type']}")
    await m.answer(f"Гру розпочато! Твій суперник кидає першим...")

@dp.message(F.dice)
async def dice_handler(m: types.Message):
    g = await games_col.find_one({"status": "playing", "$or": [{"creator_id": m.from_user.id}, {"opponent_id": m.from_user.id}]})
    if not g or g['turn'] != m.from_user.id or m.dice.emoji != g['type']: return
    is_c = (m.from_user.id == g['creator_id'])
    await games_col.update_one({"game_id": g['game_id']}, {"$push": {"c_score" if is_c else "o_score": m.dice.value}, "$set": {"turn": g['opponent_id'] if is_c else g['creator_id']}})
    upd = await games_col.find_one({"game_id": g['game_id']})
    if len(upd['c_score']) == upd['rounds_total'] and len(upd['o_score']) == upd['rounds_total']:
        await finish_game(upd, sum(upd['c_score']), sum(upd['o_score']))
    else: await bot.send_message(upd['turn'], f"Твій хід! Супернику випало {m.dice.value}")

async def finish_game(g, s1, s2):
    win_sum, ref_sh = g['bet'] * 1.98, g['bet'] * 0.01
    if s1 == s2:
        for uid in [g['creator_id'], g['opponent_id']]:
            await users_col.update_one({"_id": uid}, {"$inc": {"balance": g['bet']}})
            await bot.send_message(uid, "Нічия! Баланс повернуто.")
    else:
        win_id = g['creator_id'] if s1 > s2 else g['opponent_id']
        lose_id = g['opponent_id'] if s1 > s2 else g['creator_id']
        await users_col.update_one({"_id": win_id}, {"$inc": {"balance": win_sum, "turnover": g['bet']}})
        u_win = await users_col.find_one({"_id": win_id})
        if u_win.get("referrer"):
            ref_data = await users_col.find_one({"_id": win_id})
            if ref_data.get("is_active_ref"):
                await users_col.update_one({"_id": u_win["referrer"]}, {"$inc": {"balance": ref_sh}})
                await add_admin_profit(g['bet'] * 0.01)
            else: await add_admin_profit(g['bet'] * 0.02)
        else: await add_admin_profit(g['bet'] * 0.02)
        await bot.send_message(win_id, f"🏆 Перемога! +{win_sum:.2f} 💎"); await bot.send_message(lose_id, "❌ Програш.")
    await games_col.update_one({"game_id": g['game_id']}, {"$set": {"status": "finished"}})

# --- МЕНЮ ТА АДМІНКА ---
@dp.message(F.text == "⚙️ Налаштування")
async def settings_cmd(m: types.Message, state: FSMContext):
    await state.clear()
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🆘 Підтримка", url="https://t.me/vex0o0")]])
    if m.from_user.id == ADMIN_ID: kb.inline_keyboard.append([InlineKeyboardButton(text="👨‍💻 Адмін Панель", callback_data="admin_panel")])
    await m.answer("⚙️ Налаштування:", reply_markup=kb)

@dp.callback_query(F.data == "admin_panel")
async def admin_panel_cb(cb: types.CallbackQuery):
    stats = await stats_col.find_one({"_id": "global"})
    profit = stats.get("admin_profit", 0) if stats else 0
    t_turn = await users_col.find().sort("turnover", -1).limit(10).to_list(10)
    l_turn = "\n".join([f"{i+1}. `{u['_id']}` - {u.get('turnover',0):.2f}" for i, u in enumerate(t_turn)])
    t_refs = await users_col.find().sort("referals_count", -1).limit(10).to_list(10)
    l_refs = "\n".join([f"{i+1}. `{u['_id']}` - {u.get('referals_count',0)}👤" for i, u in enumerate(t_refs)])
    await cb.message.answer(f"👨‍💻 **Адмін Панель**\nДохід: `{profit:.4f}` 💎\n\n🏆 **Топ Оборот:**\n{l_turn}\n\n👥 **Топ Реферали:**\n{l_refs}", parse_mode="Markdown")

@dp.message(F.text == "🤝 Рефералка")
async def referral_cmd(m: types.Message, state: FSMContext):
    await state.clear()
    u = await get_u(m.from_user.id); me = await bot.get_me()
    text = (f"🤝 **Рефералка**\n\nОтримуйте 1% від ігор друзів!\nЗараховується після донату.\n\n"
            f"Запрошено: `{u.get('referals_count', 0)}`👤\n"
            f"Посилання: `https://t.me/{me.username}?start={m.from_user.id}`")
    await m.answer(text, parse_mode="Markdown")

@dp.message(F.text == "💎 Баланс")
async def balance_cmd(m: types.Message, state: FSMContext):
    await state.clear()
    u = await get_u(m.from_user.id)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="📥 Поповнити", callback_data="dep"), InlineKeyboardButton(text="📤 Вивести", callback_data="with")]])
    await m.answer(f"💰 Баланс: `{u.get('balance',0.0):.2f}` 💎", reply_markup=kb, parse_mode="Markdown")

@dp.message(Command("start"))
async def start_handler(m: types.Message, state: FSMContext):
    await state.clear(); args = m.text.split()
    ref_id = int(args[1]) if len(args) > 1 and args[1].isdigit() else None
    await get_u(m.from_user.id, ref_id)
    if len(args) > 1 and args[1].startswith("game_"):
        await join_game_logic(m, args[1].replace("game_", ""))
    await m.answer("💎 Вітаємо у Different Games!", reply_markup=main_kb())

async def main(): await dp.start_polling(bot)
if __name__ == "__main__": asyncio.run(main())
