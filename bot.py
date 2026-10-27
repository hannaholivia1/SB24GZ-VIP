# ============ HIDE TOKEN FROM LOGS ============
import logging
logging.getLogger("httpx").setLevel(logging.WARNING)

import os
import re
import random
import asyncio
import threading
from html import escape
from flask import Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    filters, ContextTypes
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("No BOT_TOKEN set!")

# ============ RIDDLES: (question, [accepted answers]) ============
RIDDLES = [
    ("I have hands but I cannot clap. What am I?", ["clock", "watch"]),
    ("I have keys but no locks, and space but no room. What am I?", ["keyboard"]),
    ("What gets wetter the more it dries?", ["towel"]),
    ("The more of me you take, the more you leave behind. What am I?", ["footsteps", "steps", "footstep"]),
    ("What can you catch but never throw?", ["cold"]),
    ("What has a neck but no head?", ["bottle"]),
    ("What goes up but never comes down?", ["age"]),
    ("I am tall when I am young and short when I am old. What am I?", ["candle"]),
    ("What has many teeth but cannot bite?", ["comb"]),
    ("What runs but never walks, and has a bed but never sleeps?", ["river"]),
    ("What has one eye but cannot see?", ["needle"]),
    ("What can travel around the world while staying in one corner?", ["stamp"]),
    ("What belongs to you but other people use it more than you do?", ["name"]),
    ("What comes down but never goes up?", ["rain"]),
    ("What is full of holes but still holds water?", ["sponge"]),
    ("What building has the most stories?", ["library"]),
    ("What has words but never speaks?", ["book"]),
    ("What begins with T, ends with T, and has T in it?", ["teapot"]),
    ("What can fill a room but takes up no space?", ["light"]),
    ("The more of me there is, the less you see. What am I?", ["darkness", "dark"]),
    ("Which month has 28 days?", ["all", "all of them", "every month", "all months"]),
    ("What is always in front of you but cannot be seen?", ["future", "the future"]),
    ("What is easy to lift but hard to throw?", ["feather"]),
    ("What has a thumb and four fingers but is not alive?", ["glove"]),
    ("What has a ring but no finger?", ["phone", "telephone", "bell"]),
]

# ============ RANKS (ascending) ============
RANKS = [
    (0, "🥉 Bronze"),
    (30, "🥈 Silver"),
    (80, "🥇 Gold"),
    (150, "💎 VIP"),
]

# ============ IN-MEMORY STATE ============
games = {}    # user_id -> {"idx", "attempts", "hint"}
points = {}   # user_id -> int
solved = {}   # user_id -> set of riddle indexes
names = {}    # user_id -> first name

# ============ FLASK - KEEPS HOST ALIVE ============
flask_app = Flask(__name__)

@flask_app.route("/")
def health_check():
    return "SB24GZ VIP Riddle bot is running!", 200

# ============ HELPERS ============
def norm(text):
    text = re.sub(r"[^a-z0-9 ]", "", text.lower()).strip()
    for article in ("a ", "an ", "the "):
        if text.startswith(article):
            text = text[len(article):]
    return text.strip()

def get_rank(p):
    label = RANKS[0][1]
    for need, name in RANKS:
        if p >= need:
            label = name
    return label

def next_rank(p):
    for need, name in RANKS:
        if p < need:
            return need - p, name
    return 0, None

def menu_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🧩 Play a Riddle", callback_data="play")],
        [InlineKeyboardButton("📊 My Rank", callback_data="score"),
         InlineKeyboardButton("🏆 Top Players", callback_data="top")],
    ])

def game_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💡 Hint", callback_data="hint"),
         InlineKeyboardButton("⏭ Skip", callback_data="skip")],
        [InlineKeyboardButton("📊 My Rank", callback_data="score"),
         InlineKeyboardButton("🏆 Top Players", callback_data="top")],
    ])

def remember(user):
    names[user.id] = user.first_name or "Player"

def new_riddle(user_id):
    done = solved.setdefault(user_id, set())
    pool = [i for i in range(len(RIDDLES)) if i not in done]
    if not pool:
        done.clear()
        pool = list(range(len(RIDDLES)))
    idx = random.choice(pool)
    games[user_id] = {"idx": idx, "attempts": 0, "hint": False}
    return idx

def riddle_text(user_id):
    g = games[user_id]
    question = RIDDLES[g["idx"]][0]
    p = points.get(user_id, 0)
    return (
        "🧩 <b>Riddle</b>\n\n"
        f"{escape(question)}\n\n"
        f"⭐ Points: {p}  |  Rank: {get_rank(p)}\n\n"
        "Type your answer below 👇"
    )

# ---- core actions: each returns (text, keyboard) ----
def do_play(user):
    remember(user)
    new_riddle(user.id)
    return riddle_text(user.id), game_keyboard()

def do_hint(user_id):
    if user_id not in games:
        return "No active riddle yet. Tap Play to get one!", menu_keyboard()
    g = games[user_id]
    g["hint"] = True
    answer = RIDDLES[g["idx"]][1][0]
    text = (
        "💡 <b>Hint</b>\n\n"
        f"The answer starts with <b>{escape(answer[0].upper())}</b> "
        f"and has <b>{len(answer)}</b> letters.\n\n"
        "Using a hint lowers the reward to 5 points."
    )
    return text, game_keyboard()

def do_skip(user_id):
    if user_id not in games:
        return "No active riddle yet. Tap Play to get one!", menu_keyboard()
    g = games.pop(user_id)
    solved.setdefault(user_id, set()).add(g["idx"])
    answer = RIDDLES[g["idx"]][1][0]
    text = (
        "⏭ <b>Skipped</b>\n\n"
        f"The answer was: <b>{escape(answer.upper())}</b>\n\n"
        "Ready for another one?"
    )
    return text, menu_keyboard()

def do_score(user):
    remember(user)
    p = points.get(user.id, 0)
    need, nxt = next_rank(p)
    if nxt:
        progress = f"Next rank: {nxt} in {need} more points"
    else:
        progress = "You reached the top rank. Amazing!"
    text = (
        "📊 <b>Your Rank</b>\n\n"
        f"⭐ Points: {p}\n"
        f"🎖 Rank: {get_rank(p)}\n"
        f"📈 {progress}\n\n"
        "Ranks: Bronze → Silver → Gold → VIP"
    )
    return text, menu_keyboard()

def do_top():
    ranked = sorted(points.items(), key=lambda kv: kv[1], reverse=True)[:5]
    ranked = [(uid, p) for uid, p in ranked if p > 0]
    if not ranked:
        return "🏆 <b>Top Players</b>\n\nNo scores yet. Be the first!", menu_keyboard()
    medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"]
    lines = []
    for i, (uid, p) in enumerate(ranked):
        lines.append(f"{medals[i]} {escape(names.get(uid, 'Player'))} - {p} pts ({get_rank(p)})")
    return "🏆 <b>Top Players</b>\n\n" + "\n".join(lines), menu_keyboard()

async def send(update, text, markup):
    await update.message.reply_text(text, parse_mode="HTML", reply_markup=markup)

# ============ COMMANDS ============
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    remember(user)
    text = (
        f"👋 Hello {escape(user.first_name or 'friend')}!\n\n"
        "Welcome to <b>SB24GZ VIP</b> 🌟\n\n"
        "Solve riddles, earn points and climb the ranks:\n"
        "🥉 Bronze → 🥈 Silver → 🥇 Gold → 💎 VIP\n\n"
        "It is 100% free. Tap the button to play 👇"
    )
    await send(update, text, menu_keyboard())

async def play_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text, markup = do_play(update.effective_user)
    await send(update, text, markup)

async def hint_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text, markup = do_hint(update.effective_user.id)
    await send(update, text, markup)

async def skip_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text, markup = do_skip(update.effective_user.id)
    await send(update, text, markup)

async def score_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text, markup = do_score(update.effective_user)
    await send(update, text, markup)

async def top_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text, markup = do_top()
    await send(update, text, markup)

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "📚 <b>How to play</b>\n\n"
        "1. Tap /play to get a riddle\n"
        "2. Type your answer\n"
        "3. Correct answer = +10 points (+5 if you used a hint)\n"
        "4. Reach 30 points for Silver, 80 for Gold, 150 for VIP\n\n"
        "<b>Commands</b>\n"
        "/play - New riddle\n"
        "/hint - Get a hint\n"
        "/skip - Skip this riddle\n"
        "/score - Your points and rank\n"
        "/top - Top players\n"
        "/help - Show this message"
    )
    await send(update, text, menu_keyboard())

# ============ ANSWER HANDLER ============
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    remember(user)
    uid = user.id

    if uid not in games:
        await send(update, "No active riddle yet. Tap Play to get one!", menu_keyboard())
        return

    g = games[uid]
    accepted = [norm(a) for a in RIDDLES[g["idx"]][1]]

    if norm(update.message.text) in accepted:
        earned = 5 if g["hint"] else 10
        before = get_rank(points.get(uid, 0))
        points[uid] = points.get(uid, 0) + earned
        after = get_rank(points[uid])
        solved.setdefault(uid, set()).add(g["idx"])
        answer = RIDDLES[g["idx"]][1][0]
        del games[uid]

        text = (
            "✅ <b>Correct!</b>\n\n"
            f"The answer is <b>{escape(answer.upper())}</b>\n"
            f"+{earned} points\n"
            f"⭐ Total: {points[uid]}  |  Rank: {after}"
        )
        if after != before:
            text += f"\n\n🎉 <b>Rank up!</b> You are now {after}"
        await send(update, text, menu_keyboard())
    else:
        g["attempts"] += 1
        text = "❌ <b>Not quite.</b> Try again!"
        if g["attempts"] >= 2 and not g["hint"]:
            text += "\n\nStuck? Tap Hint for a clue."
        await send(update, text, game_keyboard())

# ============ BUTTONS ============
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = query.from_user
    data = query.data

    if data == "play":
        text, markup = do_play(user)
    elif data == "hint":
        text, markup = do_hint(user.id)
    elif data == "skip":
        text, markup = do_skip(user.id)
    elif data == "score":
        text, markup = do_score(user)
    elif data == "top":
        text, markup = do_top()
    else:
        return

    await query.message.reply_text(text, parse_mode="HTML", reply_markup=markup)

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Update {update} caused error {context.error}")

# ============ BOT STARTUP ============
async def run_bot_async():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("play", play_cmd))
    app.add_handler(CommandHandler("hint", hint_cmd))
    app.add_handler(CommandHandler("skip", skip_cmd))
    app.add_handler(CommandHandler("score", score_cmd))
    app.add_handler(CommandHandler("top", top_cmd))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_error_handler(error_handler)

    await app.initialize()
    await app.start()
    await app.updater.start_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True
    )
    logger.info("SB24GZ VIP Riddle bot is polling and ready!")
    while True:
        await asyncio.sleep(1)

def run_bot():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(run_bot_async())

bot_thread = threading.Thread(target=run_bot, daemon=True)
bot_thread.start()
