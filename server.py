import os, io, uuid, base64, json, logging, sqlite3, threading, time, random, asyncio
from datetime import datetime, timedelta
from flask import Flask, request, jsonify
import requests
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ChatPermissions
from telegram.constants import ChatMemberStatus
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes, MessageHandler, filters
import edge_tts

# -------------------- Configuration --------------------
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "8910769488:AAG7effUIZqoK0vVLJ_zRAVJ7K4ifgMX4AY")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "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")
ADMIN_USER_ID = int(os.environ.get("ADMIN_USER_ID", "8391932958"))  # سازنده
DATABASE = "bot_data.db"

BOT_ACTIVE = True
AI_ENABLED = True
REACTIONS_ENABLED = True

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)

# -------------------- Database --------------------
def get_db():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    conn.execute("CREATE TABLE IF NOT EXISTS learned (keyword TEXT PRIMARY KEY, response TEXT)")
    conn.execute("CREATE TABLE IF NOT EXISTS groups (chat_id INTEGER PRIMARY KEY, title TEXT, welcome_text TEXT, rules_text TEXT, antispam INTEGER DEFAULT 0, antilink INTEGER DEFAULT 0, slow_mode INTEGER DEFAULT 0, locked INTEGER DEFAULT 0)")
    conn.execute("CREATE TABLE IF NOT EXISTS warnings (user_id INTEGER, chat_id INTEGER, reason TEXT, warned_by INTEGER, timestamp TEXT)")
    conn.commit()
    conn.close()

# -------------------- Helpers --------------------
def send_telegram_message(chat_id, text, reply_markup=None, parse_mode="HTML"):
    if not TOKEN: return
    try:
        requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage",
                      json={"chat_id": chat_id, "text": text, "parse_mode": parse_mode, "reply_markup": reply_markup},
                      timeout=10)
    except Exception as e:
        logging.error(f"Send message failed: {e}")

async def is_user_admin(chat_id, user_id, context: ContextTypes.DEFAULT_TYPE):
    try:
        member = await context.bot.get_chat_member(chat_id, user_id)
        return member.status in [ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER]
    except:
        return False

async def is_user_creator(user_id):
    return user_id == ADMIN_USER_ID

def get_main_menu_keyboard(is_creator=False, is_admin=False):
    keyboard = []
    if is_creator:
        keyboard.append([InlineKeyboardButton("⚙️ پنل سازنده", callback_data="creator_panel")])
    if is_admin:
        keyboard.append([InlineKeyboardButton("🛡️ مدیریت گروه", callback_data="admin_panel")])
    keyboard.extend([
        [InlineKeyboardButton("🧠 یادگیری کلمات", callback_data="learn_menu")],
        [InlineKeyboardButton("🎲 سرگرمی", callback_data="fun_menu")],
        [InlineKeyboardButton("ℹ️ راهنما", callback_data="help")]
    ])
    return InlineKeyboardMarkup(keyboard)

# -------------------- AI Helpers --------------------
def ask_groq(prompt):
    if not GROQ_API_KEY: return "⚠️ AI کلید تنظیم نشده است."
    try:
        url = "https://api.groq.com/openai/v1/chat/completions"
        headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
        payload = {
            "model": "llama-3.3-70b-versatile",
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.9,
            "max_tokens": 1000
        }
        resp = requests.post(url, json=payload, headers=headers, timeout=20)
        if resp.status_code == 200:
            return resp.json()['choices'][0]['message']['content'].strip()
        return "❌ هوش مصنوعی در دسترس نیست."
    except:
        return "❌ خطا در ارتباط با AI."

def transcribe_audio(file_bytes):
    if not GROQ_API_KEY: return None
    try:
        url = "https://api.groq.com/openai/v1/audio/transcriptions"
        headers = {"Authorization": f"Bearer {GROQ_API_KEY}"}
        files = {'file': ('voice.ogg', io.BytesIO(file_bytes), 'audio/ogg'), 'model': (None, 'whisper-large-v3'), 'language': (None, 'fa')}
        resp = requests.post(url, headers=headers, files=files, timeout=30)
        return resp.json().get('text', '').strip() if resp.status_code == 200 else None
    except:
        return None

async def text_to_speech_async(text):
    try:
        communicate = edge_tts.Communicate(text=text, voice="fa-IR-FaridNeural")
        mp3_data = io.BytesIO()
        async for chunk in communicate.stream():
            if chunk["type"] == "audio": mp3_data.write(chunk["data"])
        mp3_data.seek(0)
        return mp3_data.getvalue()
    except:
        return None

# -------------------- Bot Handlers --------------------
REACTIONS = ["🫩", "😡", "😨", "😐", "😱", "🤙🏽", "🤣"]
STICKERS = ["CAACAgIAAxkBAAE", "CAACAgIAAxkBAAE"]  # replace with real sticker file_ids
GIFS = ["CgACAgQAAxkBAAE", "CgACAgQAAxkBAAE"]  # replace with real gif file_ids

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not BOT_ACTIVE:
        await update.message.reply_text("❌ ربات غیرفعال است.")
        return
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    is_creator = await is_user_creator(user_id)
    is_admin = False
    if update.effective_chat.type != 'private':
        is_admin = await is_user_admin(chat_id, user_id, context)
    await update.message.reply_text(
        "👋 سلام! من ربات هوشمند شما هستم. از دکمه‌های زیر استفاده کنید:",
        reply_markup=get_main_menu_keyboard(is_creator, is_admin)
    )

# -------------------- Callback Handler --------------------
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global BOT_ACTIVE, AI_ENABLED, REACTIONS_ENABLED
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = query.from_user.id
    chat_id = query.message.chat.id if query.message else None

    # Helper to refresh menu after action
    async def refresh_menu():
        is_creator = await is_user_creator(user_id)
        is_admin = False
        if chat_id and query.message.chat.type != 'private':
            is_admin = await is_user_admin(chat_id, user_id, context)
        await query.edit_message_text("👋 سلام! من ربات هوشمند شما هستم. از دکمه‌های زیر استفاده کنید:",
                                      reply_markup=get_main_menu_keyboard(is_creator, is_admin))

    # ==================== Creator Panel ====================
    if data == "creator_panel":
        if not await is_user_creator(user_id): return await query.answer("⛔ فقط سازنده", show_alert=True)
        keyboard = [
            [InlineKeyboardButton(f"ربات {'✅' if BOT_ACTIVE else '❌'}", callback_data="toggle_bot"),
             InlineKeyboardButton(f"AI {'✅' if AI_ENABLED else '❌'}", callback_data="toggle_ai")],
            [InlineKeyboardButton(f"ری‌اکشن {'✅' if REACTIONS_ENABLED else '❌'}", callback_data="toggle_reactions")],
            [InlineKeyboardButton("📢 ارسال همگانی", callback_data="broadcast_prompt"),
             InlineKeyboardButton("📊 آمار", callback_data="show_stats")],
            [InlineKeyboardButton("🔙 بازگشت", callback_data="main_menu")]
        ]
        await query.edit_message_text("🔧 پنل سازنده:", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data == "toggle_bot":
        BOT_ACTIVE = not BOT_ACTIVE
        await button_handler(update, context)  # re-show panel
    elif data == "toggle_ai":
        AI_ENABLED = not AI_ENABLED
        await button_handler(update, context)
    elif data == "toggle_reactions":
        REACTIONS_ENABLED = not REACTIONS_ENABLED
        await button_handler(update, context)
    elif data == "broadcast_prompt":
        context.user_data['awaiting_broadcast'] = True
        await query.edit_message_text("📢 متن پیام همگانی را ارسال کنید:")
    elif data == "show_stats":
        conn = get_db(); groups_cnt = conn.execute("SELECT COUNT(*) FROM groups").fetchone()[0]
        learned_cnt = conn.execute("SELECT COUNT(*) FROM learned").fetchone()[0]; conn.close()
        await query.edit_message_text(f"📊 آمار:\n👥 گروه‌ها: {groups_cnt}\n🧠 کلمات: {learned_cnt}",
                                      reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙", callback_data="creator_panel")]]))

    # ==================== Admin Panel ====================
    elif data == "admin_panel":
        if chat_id and not await is_user_admin(chat_id, user_id, context):
            return await query.answer("⛔ فقط ادمین گروه", show_alert=True)
        keyboard = [
            [InlineKeyboardButton("🚫 بن", callback_data="admin_ban"),
             InlineKeyboardButton("🔇 میوت", callback_data="admin_mute")],
            [InlineKeyboardButton("🔊 آنمیوت", callback_data="admin_unmute"),
             InlineKeyboardButton("👢 کیک", callback_data="admin_kick")],
            [InlineKeyboardButton("📜 قوانین", callback_data="admin_rules_prompt"),
             InlineKeyboardButton("👋 خوش‌آمدگویی", callback_data="admin_welcome_prompt")],
            [InlineKeyboardButton("🔙 بازگشت", callback_data="main_menu")]
        ]
        await query.edit_message_text("🛡️ مدیریت گروه:", reply_markup=InlineKeyboardMarkup(keyboard))
    elif data.startswith("admin_"):
        action = data.replace("admin_", "")
        if action == "ban" or action == "mute" or action == "unmute" or action == "kick":
            context.user_data['admin_action'] = action
            await query.edit_message_text("👤 لطفاً روی پیام کاربر مورد نظر ریپلای کنید و /execute را بفرستید.")
        elif action == "rules_prompt":
            context.user_data['awaiting_rules'] = True
            await query.edit_message_text("📜 متن قوانین جدید را ارسال کنید:")
        elif action == "welcome_prompt":
            context.user_data['awaiting_welcome'] = True
            await query.edit_message_text("👋 متن پیام خوش‌آمدگویی را ارسال کنید:")

    # ==================== Learn Menu ====================
    elif data == "learn_menu":
        keyboard = [
            [InlineKeyboardButton("➕ افزودن", callback_data="learn_add"),
             InlineKeyboardButton("➖ حذف", callback_data="learn_remove")],
            [InlineKeyboardButton("📚 لیست", callback_data="learn_list")],
            [InlineKeyboardButton("🔙 بازگشت", callback_data="main_menu")]
        ]
        await query.edit_message_text("🧠 مدیریت یادگیری:", reply_markup=InlineKeyboardMarkup(keyboard))
    elif data == "learn_add":
        context.user_data['awaiting_learn'] = True
        await query.edit_message_text("📝 کلمه و پاسخ را با فرمت <code>کلمه|پاسخ</code> ارسال کنید:", parse_mode="HTML")
    elif data == "learn_remove":
        context.user_data['awaiting_unlearn'] = True
        await query.edit_message_text("🗑 کلمه‌ای که می‌خواهید حذف شود را ارسال کنید:")
    elif data == "learn_list":
        conn = get_db(); rows = conn.execute("SELECT keyword, response FROM learned ORDER BY keyword").fetchall(); conn.close()
        if not rows:
            await query.edit_message_text("📭 لیست خالی است.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙", callback_data="learn_menu")]]))
        else:
            text = "📚 کلمات یادگرفته:\n\n" + "\n".join(f"• <b>{r['keyword']}</b> → {r['response']}" for r in rows)
            await query.edit_message_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙", callback_data="learn_menu")]]))

    # ==================== Fun Menu ====================
    elif data == "fun_menu":
        keyboard = [
            [InlineKeyboardButton("😂 جوک", callback_data="fun_joke"),
             InlineKeyboardButton("📚 دانستنی", callback_data="fun_fact")],
            [InlineKeyboardButton("🎲 تاس", callback_data="fun_dice"),
             InlineKeyboardButton("🔮 8ball", callback_data="fun_8ball")],
            [InlineKeyboardButton("🔙 بازگشت", callback_data="main_menu")]
        ]
        await query.edit_message_text("🎲 سرگرمی:", reply_markup=InlineKeyboardMarkup(keyboard))
    elif data == "fun_joke":
        jokes = ["چرا برنامه‌نویس‌ها تاریکی رو دوست دارن؟ چون light mode چشمشون رو اذیت می‌کنه! 😂", "به سگی گفتم بشین، نشست. فهمیدم تلگرام نیست که ignore کنه.", "زندگی بدون وای‌فای یعنی جهنم. جدی میگم."]
        await query.edit_message_text(random.choice(jokes), reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔄 یکی دیگه", callback_data="fun_joke"), InlineKeyboardButton("🔙", callback_data="fun_menu")]]))
    elif data == "fun_fact":
        facts = ["آیا می‌دانستید قلب میگو در سرش قرار دارد؟", "هر ثانیه ۱۰۰ صاعقه در جهان رخ می‌دهد.", "زبان گربه‌ها اثر ضدعفونی‌کننده دارد."]
        await query.edit_message_text(f"📚 {random.choice(facts)}", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔄 یکی دیگه", callback_data="fun_fact"), InlineKeyboardButton("🔙", callback_data="fun_menu")]]))
    elif data == "fun_dice":
        await query.edit_message_text(f"🎲 عدد: {random.randint(1,6)}", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔄 دوباره", callback_data="fun_dice"), InlineKeyboardButton("🔙", callback_data="fun_menu")]]))
    elif data == "fun_8ball":
        answers = ["قطعاً", "شک نکن", "آره", "نه", "پرسش مبهم است", "بعداً بپرس", "خواب دیدی"]
        await query.edit_message_text(f"🔮 {random.choice(answers)}", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔄 دوباره", callback_data="fun_8ball"), InlineKeyboardButton("🔙", callback_data="fun_menu")]]))

    elif data == "help":
        help_text = ("🤖 <b>راهنمای ربات:</b>\n"
                     "/start - نمایش منو\n"
                     "/learn &lt;کلمه&gt; &lt;پاسخ&gt; - آموزش کلمه\n"
                     "/unlearn &lt;کلمه&gt; - حذف کلمه\n"
                     "/wordlist - لیست کلمات\n"
                     "دکمه‌های شیشه‌ای هم برای مدیریت گروه، یادگیری و سرگرمی در دسترس هستند.")
        await query.edit_message_text(help_text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙", callback_data="main_menu")]]))

    elif data == "main_menu":
        await refresh_menu()

# -------------------- Message Handler --------------------
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not BOT_ACTIVE: return
    msg = update.message
    chat_id = msg.chat.id
    user_id = msg.from_user.id
    text = msg.text or ""

    # Broadcast state
    if context.user_data.get('awaiting_broadcast') and await is_user_creator(user_id):
        context.user_data['awaiting_broadcast'] = False
        conn = get_db(); groups = conn.execute("SELECT chat_id FROM groups").fetchall(); conn.close()
        for g in groups:
            try: await context.bot.send_message(g['chat_id'], text)
            except: pass
        await msg.reply_text("✅ پیام همگانی ارسال شد.")
        return

    # Group settings prompts
    if context.user_data.get('awaiting_welcome') and await is_user_admin(chat_id, user_id, context):
        conn = get_db(); conn.execute("INSERT OR REPLACE INTO groups (chat_id, welcome_text) VALUES (?, ?)", (chat_id, text)); conn.commit(); conn.close()
        context.user_data['awaiting_welcome'] = False
        await msg.reply_text("✅ متن خوش‌آمدگویی تنظیم شد.")
        return
    if context.user_data.get('awaiting_rules') and await is_user_admin(chat_id, user_id, context):
        conn = get_db(); conn.execute("INSERT OR REPLACE INTO groups (chat_id, rules_text) VALUES (?, ?)", (chat_id, text)); conn.commit(); conn.close()
        context.user_data['awaiting_rules'] = False
        await msg.reply_text("✅ قوانین تنظیم شد.")
        return
    if context.user_data.get('admin_action') and await is_user_admin(chat_id, user_id, context):
        action = context.user_data.pop('admin_action')
        if msg.reply_to_message:
            target = msg.reply_to_message.from_user
            try:
                if action == 'ban': await context.bot.ban_chat_member(chat_id, target.id)
                elif action == 'mute': await context.bot.restrict_chat_member(chat_id, target.id, permissions=ChatPermissions(can_send_messages=False))
                elif action == 'unmute': await context.bot.restrict_chat_member(chat_id, target.id, permissions=ChatPermissions(can_send_messages=True, can_send_media_messages=True, can_send_other_messages=True))
                elif action == 'kick':
                    await context.bot.ban_chat_member(chat_id, target.id)
                    await context.bot.unban_chat_member(chat_id, target.id)
                await msg.reply_text(f"✅ عملیات {action} با موفقیت انجام شد.")
            except Exception as e:
                await msg.reply_text(f"❌ خطا: {e}")
        else:
            await msg.reply_text("❌ لطفاً روی پیام کاربر ریپلای کنید.")
        return

    # Learn/Unlearn via text
    if context.user_data.get('awaiting_learn'):
        parts = text.split('|')
        if len(parts) == 2:
            keyword, response = parts[0].strip().lower(), parts[1].strip()
            conn = get_db(); conn.execute("INSERT OR REPLACE INTO learned (keyword, response) VALUES (?, ?)", (keyword, response)); conn.commit(); conn.close()
            context.user_data['awaiting_learn'] = False
            await msg.reply_text(f"✅ یاد گرفتم: «{keyword}» → {response}")
        else:
            await msg.reply_text("❌ فرمت اشتباه. مثال: سلام|علیک")
        return
    if context.user_data.get('awaiting_unlearn'):
        keyword = text.strip().lower()
        conn = get_db(); conn.execute("DELETE FROM learned WHERE keyword=?", (keyword,)); conn.commit(); conn.close()
        context.user_data['awaiting_unlearn'] = False
        await msg.reply_text(f"✅ کلمه «{keyword}» حذف شد.")
        return

    # Auto reactions
    if REACTIONS_ENABLED and random.random() < 0.3:
        try: await msg.set_reaction(reaction=[random.choice(REACTIONS)], is_big=False)
        except: pass

    # Sticker/GIF reply
    if msg.sticker and STICKERS:
        await msg.reply_sticker(sticker=random.choice(STICKERS))
    elif msg.animation and GIFS:
        await msg.reply_animation(animation=random.choice(GIFS))

    # AI or learned reply
    if msg.reply_to_message and msg.reply_to_message.from_user.id == context.bot.id:
        if AI_ENABLED:
            thinking = await msg.reply_text("🤔 ...")
            answer = ask_groq(text)
            await thinking.edit_text(answer)
        return

    text_lower = text.lower()
    conn = get_db(); rows = conn.execute("SELECT keyword, response FROM learned").fetchall(); conn.close()
    for row in rows:
        if row['keyword'] in text_lower:
            await msg.reply_text(row['response'])
            return

    if msg.chat.type == 'private' and AI_ENABLED:
        thinking = await msg.reply_text("🤔 ...")
        answer = ask_groq(text)
        await thinking.edit_text(answer)

# -------------------- Voice Handler --------------------
async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not AI_ENABLED: return
    msg = update.message
    voice = msg.voice
    file = await voice.get_file()
    file_bytes = await file.download_as_bytearray()
    transcript = transcribe_audio(file_bytes)
    if not transcript:
        await msg.reply_text("❌ تشخیص گفتار ناموفق.")
        return
    thinking = await msg.reply_text("🤔 ...")
    answer = ask_groq(transcript)
    audio = await text_to_speech_async(answer)
    if audio:
        await msg.reply_voice(voice=io.BytesIO(audio))
        await thinking.delete()
    else:
        await thinking.edit_text(answer)

# -------------------- Main --------------------
async def main():
    init_db()
    application = Application.builder().token(TOKEN).connect_timeout(30).read_timeout(30).write_timeout(30).build()
    application.add_handler(CommandHandler("start", start))
    # Optional direct commands for convenience
    application.add_handler(CommandHandler("learn", learn_command))
    application.add_handler(CommandHandler("unlearn", unlearn_command))
    application.add_handler(CommandHandler("wordlist", wordlist_command))
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_handler(MessageHandler(filters.VOICE, handle_voice))
    # Sticker and animation handlers will be caught by handle_message
    await application.run_polling()

def run_flask():
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)

if __name__ == '__main__':
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    asyncio.run(main())
