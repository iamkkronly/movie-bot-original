# © 2025 Kaustav Ray. All rights reserved.
# Licensed under the MIT License.

import logging
from bson.objectid import ObjectId
from pymongo import MongoClient
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# ========================
# CONFIG
# ========================
BOT_TOKEN = "8410215954:AAE0icLhQeXs4aIU0pA_wrhMbOOziPQLx24"  # Bot Token
DB_CHANNEL = -1002975831610  # Database channel
ADMINS = [6705618257]        # Admin IDs

MONGO_URI = (
    "mongodb+srv://bf44tb5_db_user:RhyeHAHsTJeuBPNg@cluster0.lgao3zu.mongodb.net/"
    "?retryWrites=true&w=majority&appName=Cluster0"
)
mongo_client = MongoClient(MONGO_URI)
db = mongo_client["telegram_files"]
files_col = db["files"]

# Logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)


# ========================
# HANDLERS
# ========================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👋 Send me a movie name to search.")


async def save_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin sends file -> save to channel + DB"""
    user_id = update.message.from_user.id
    if user_id not in ADMINS:
        return

    file = update.message.document or update.message.video or update.message.audio
    if not file:
        return

    # Forward to database channel
    forwarded = await update.message.forward(DB_CHANNEL)

    # Clean filename (replace "_" with space)
    raw_name = getattr(file, "file_name", None) or getattr(file, "title", None) or file.file_unique_id
    clean_name = raw_name.replace("_", " ") if raw_name else "Unknown"

    # Save metadata
    files_col.insert_one({
        "file_name": clean_name,
        "file_id": forwarded.message_id,
        "channel_id": forwarded.chat.id,
    })

    await update.message.reply_text(f"✅ Saved: {clean_name}")


async def search_files(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Search DB and show results"""
    query = update.message.text.strip().replace("_", " ")
    results = list(files_col.find({"file_name": {"$regex": query, "$options": "i"}}).limit(50))
    if not results:
        await update.message.reply_text("❌ No files found.")
        return
    await send_results_page(update.effective_chat.id, results, 0, context, query)


async def send_results_page(chat_id, results, page, context: ContextTypes.DEFAULT_TYPE, query: str):
    start, end = page * 10, (page + 1) * 10
    page_results = results[start:end]

    text = f"🔎 Results for: *{query}*\n\n"
    buttons = []
    for idx, file in enumerate(page_results, start=start + 1):
        text += f"**{idx}.** {file['file_name']}\n"
        buttons.append(
            [InlineKeyboardButton(file["file_name"][:40], callback_data=f"get_{file['_id']}")]
        )

    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"page_{page-1}_{query}"))
    if end < len(results):
        nav_buttons.append(InlineKeyboardButton("Next ➡️", callback_data=f"page_{page+1}_{query}"))

    if nav_buttons:
        buttons.append(nav_buttons)

    buttons.append([InlineKeyboardButton("📨 Send All Files", callback_data=f"sendall_{page}_{query}")])

    await context.bot.send_message(
        chat_id, text or "No results.", reply_markup=InlineKeyboardMarkup(buttons), parse_mode="Markdown"
    )


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle button clicks"""
    query = update.callback_query
    await query.answer()
    data = query.data

    if data.startswith("get_"):
        file_data = files_col.find_one({"_id": ObjectId(data.split("_", 1)[1])})
        if file_data:
            await context.bot.copy_message(
                chat_id=query.message.chat.id,
                from_chat_id=file_data["channel_id"],
                message_id=file_data["file_id"],
            )
        else:
            await query.message.reply_text("❌ File not found.")

    elif data.startswith("page_"):
        _, page_str, search_query = data.split("_", 2)
        page = int(page_str)
        results = list(files_col.find({"file_name": {"$regex": search_query, "$options": "i"}}).limit(50))
        await query.message.delete()
        await send_results_page(query.message.chat.id, results, page, context, search_query)

    elif data.startswith("sendall_"):
        _, page_str, search_query = data.split("_", 2)
        page = int(page_str)
        results = list(files_col.find({"file_name": {"$regex": search_query, "$options": "i"}}).limit(50))
        for file in results[page * 10:(page + 1) * 10]:
            await context.bot.copy_message(
                chat_id=query.message.chat.id,
                from_chat_id=file["channel_id"],
                message_id=file["file_id"],
            )
        await query.message.reply_text("📨 Sent all files!")


# ========================
# MAIN
# ========================

def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.Document.ALL | filters.VIDEO | filters.AUDIO, save_file))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, search_files))
    app.add_handler(CallbackQueryHandler(button_handler))

    logger.info("Bot started...")
    app.run_polling(poll_interval=1, timeout=10, drop_pending_updates=True)


if __name__ == "__main__":
    main()
