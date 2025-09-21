# © 2025 Kaustav Ray. All rights reserved.
# Licensed under the MIT License.

import logging
from bson.objectid import ObjectId
from pymongo import MongoClient
from pymongo.errors import PyMongoError
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)
from telegram.error import TelegramError
from fuzzywuzzy import fuzz

# ========================
# CONFIG
# ========================
BOT_TOKEN = "8410215954:AAE0icLhQeXs4aIU0pA_wrhMbOOziPQLx24"  # Bot Token
DB_CHANNEL = -1002975831610  # Database channel
LOG_CHANNEL = -1002988891392  # Channel to log user queries
JOIN_CHECK_CHANNEL = -1002692055617  # Channel users must join to use the bot
ADMINS = [6705618257]        # Admin IDs

PROMOTIONAL_LINKS = [
    "Join our main channel: @filestore4u",
    "Our backup channel: @freemovie5u",
    "For latest movies: @latestmovies"
]

# A list of MongoDB URIs to use. Add as many as you need.
# The bot will try them in order if a connection or insert fails.
MONGO_URIS = [
    "mongodb+srv://bf44tb5_db_user:RhyeHAHsTJeuBPNg@cluster0.lgao3zu.mongodb.net/?retryWrites=true&w=majority&appName=Cluster0",
    "mongodb+srv://<REPLACE_WITH_YOUR_USER>:<REPLACE_WITH_YOUR_PASSWORD>@<REPLACE_WITH_YOUR_CLUSTER>.mongodb.net/?retryWrites=true&w=majority&appName=<REPLACE_WITH_YOUR_APPNAME>",
]
current_uri_index = 0

mongo_client = None
db = None
files_col = None

# Logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)


# ========================
# HELPERS
# ========================

def escape_markdown(text: str) -> str:
    """Helper function to escape special characters in Markdown V2."""
    escape_chars = r'_*[]()~`>#+-=|{}.!'
    return "".join('\\' + char if char in escape_chars else char for char in text)

def format_filename_for_display(filename: str) -> str:
    """Splits a long filename into two lines for better display."""
    if len(filename) < 40:
        return filename
    
    mid = len(filename) // 2
    split_point = -1
    
    # Try to find a space near the midpoint
    for i in range(mid, 0, -1):
        if filename[i] == ' ':
            split_point = i
            break
    
    if split_point == -1:
        for i in range(mid, len(filename)):
            if filename[i] == ' ':
                split_point = i
                break
    
    if split_point != -1:
        return filename[:split_point] + '\n' + filename[split_point+1:]
    else:
        # Fallback if no space is found (e.g., a single long word)
        return filename[:mid] + '\n' + filename[mid:]

async def check_member_status(user_id, context: ContextTypes.DEFAULT_TYPE):
    """Check if the user is a member of the required channel."""
    try:
        member = await context.bot.get_chat_member(chat_id=JOIN_CHECK_CHANNEL, user_id=user_id)
        if member.status in ["member", "administrator", "creator"]:
            return True
        else:
            return False
    except TelegramError as e:
        logger.error(f"Error checking member status for user {user_id}: {e}")
        return False

def connect_to_mongo():
    """Connect to the MongoDB URI at the current index."""
    global mongo_client, db, files_col
    try:
        uri = MONGO_URIS[current_uri_index]
        mongo_client = MongoClient(uri)
        db = mongo_client["telegram_files"]
        files_col = db["files"]
        logger.info(f"Successfully connected to MongoDB at index {current_uri_index}.")
        return True
    except (PyMongoError, IndexError) as e:
        logger.error(f"Failed to connect to MongoDB at index {current_uri_index}: {e}")
        return False


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

    # Get filename from caption, then from file_name, replacing underscores, dots, and hyphens with spaces
    # Otherwise, use a default value
    if update.message.caption:
        raw_name = update.message.caption
    else:
        raw_name = getattr(file, "file_name", None) or getattr(file, "title", None) or file.file_unique_id

    clean_name = raw_name.replace("_", " ").replace(".", " ").replace("-", " ") if raw_name else "Unknown"
    
    global current_uri_index, files_col
    
    saved = False
    while not saved and current_uri_index < len(MONGO_URIS):
        try:
            # Try to save metadata with the current client
            files_col.insert_one({
                "file_name": clean_name,
                "file_id": forwarded.message_id,
                "channel_id": forwarded.chat.id,
            })
            await update.message.reply_text(f"✅ Saved: {clean_name}")
            saved = True
        except Exception as e:
            logger.error(f"Error saving file with URI #{current_uri_index + 1}: {e}")
            current_uri_index += 1
            if current_uri_index < len(MONGO_URIS):
                await update.message.reply_text(
                    f"⚠️ Database connection failed. Attempting to switch to URI #{current_uri_index + 1}..."
                )
                if not connect_to_mongo():
                    # If connection to the new URI also fails, the loop will continue to the next one
                    await update.message.reply_text("❌ Failed to connect to the next database.")
            else:
                await update.message.reply_text("❌ Failed to save file on all available databases.")

    if not saved:
        logger.error("All MongoDB URIs have been tried and failed.")


async def search_files(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Search DB and show results, sorted by relevance"""
    if not await check_member_status(update.effective_user.id, context):
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("Join Channel", url="https://t.me/filestore4u")]])
        await update.message.reply_text("❌ You must join our channel to use this bot!", reply_markup=keyboard)
        return

    raw_query = update.message.text.strip()
    query = raw_query.replace("_", " ").replace(".", " ").replace("-", " ")

    # Log the user's query
    user = update.effective_user
    log_text = f"🔍 User: {user.full_name} | @{user.username} | ID: {user.id}\nQuery: {raw_query}"
    try:
        await context.bot.send_message(LOG_CHANNEL, text=log_text)
    except Exception as e:
        logger.error(f"Failed to log query to channel: {e}")

    # Find ALL files. This ensures the fuzzy search is comprehensive.
    results = list(files_col.find({}))

    if not results:
        await update.message.reply_text("❌ No files found.")
        return

    # Calculate a similarity score for each result and sort
    sorted_results = sorted(
        results,
        key=lambda x: fuzz.ratio(query.lower(), x['file_name'].lower()),
        reverse=True
    )

    # Filter out results with a low score and show only the top 50
    final_results = [r for r in sorted_results if fuzz.partial_ratio(query.lower(), r['file_name'].lower()) > 60][:50]
    
    if not final_results:
        await update.message.reply_text("❌ No relevant files found.")
        return

    await send_results_page(update.effective_chat.id, final_results, 0, context, raw_query)


async def send_results_page(chat_id, results, page, context: ContextTypes.DEFAULT_TYPE, query: str):
    start, end = page * 10, (page + 1) * 10
    page_results = results[start:end]

    # Escape the query string for Markdown
    escaped_query = escape_markdown(query)
    text = f"🔎 Results for: *{escaped_query}*\n\n"
    buttons = []
    for idx, file in enumerate(page_results, start=start + 1):
        # Format the filename first, then escape it for Markdown
        two_line_name = format_filename_for_display(file['file_name'])
        escaped_file_name = escape_markdown(two_line_name)
        
        text += f"**{idx}.** {escaped_file_name}\n"
        buttons.append(
            [InlineKeyboardButton(file["file_name"][:40], callback_data=f"get_{file['_id']}")]
        )
    
    # Add the promotional text at the end
    text += "\n\nKaustav Ray                                                                                                      Join here: @filestore4u     @freemovie5u"

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
    if not await check_member_status(update.effective_user.id, context):
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("Join Channel", url="https://t.me/filestore4u")]])
        await update.callback_query.message.reply_text("❌ You must join our channel to use this bot!", reply_markup=keyboard)
        return

    query = update.callback_query
    await query.answer()
    data = query.data

    if data.startswith("get_"):
        file_data = files_col.find_one({"_id": ObjectId(data.split("_", 1)[1])})
        if file_data:
            # Send file to user's private chat
            await context.bot.copy_message(
                chat_id=query.from_user.id,
                from_chat_id=file_data["channel_id"],
                message_id=file_data["file_id"],
            )
            
            # Send promotional links to user's private chat
            for promo_text in PROMOTIONAL_LINKS:
                await context.bot.send_message(
                    chat_id=query.from_user.id,
                    text=promo_text
                )
            
            # Send confirmation message to the original chat
            await query.message.reply_text("✅ I have sent the file and a few other links to you in a private message.")
        else:
            await query.message.reply_text("❌ File not found.")

    elif data.startswith("page_"):
        _, page_str, search_query = data.split("_", 2)
        page = int(page_str)
        # Re-run the broader search and sorting logic to get the correct results
        all_results = list(files_col.find({}))
        sorted_results = sorted(
            all_results,
            key=lambda x: fuzz.ratio(search_query.lower().replace("_", " ").replace(".", " ").replace("-", " "), x['file_name'].lower()),
            reverse=True
        )
        final_results = [r for r in sorted_results if fuzz.partial_ratio(search_query.lower().replace("_", " ").replace(".", " ").replace("-", " "), r['file_name'].lower()) > 60][:50]

        await query.message.delete()
        await send_results_page(query.message.chat.id, final_results, page, context, search_query)

    elif data.startswith("sendall_"):
        _, page_str, search_query = data.split("_", 2)
        page = int(page_str)
        # Re-run the broader search and sorting logic
        all_results = list(files_col.find({}))
        sorted_results = sorted(
            all_results,
            key=lambda x: fuzz.ratio(search_query.lower().replace("_", " ").replace(".", " ").replace("-", " "), x['file_name'].lower()),
            reverse=True
        )
        final_results = [r for r in sorted_results if fuzz.partial_ratio(search_query.lower().replace("_", " ").replace(".", " ").replace("-", " "), r['file_name'].lower()) > 60][:50]
        
        for file in final_results[page * 10:(page + 1) * 10]:
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
    if not connect_to_mongo():
        logger.critical("Failed to connect to the initial MongoDB URI. Exiting.")
        return

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.Document.ALL | filters.VIDEO | filters.AUDIO, save_file))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, search_files))
    app.add_handler(CallbackQueryHandler(button_handler))

    logger.info("Bot started...")
    app.run_polling(poll_interval=1, timeout=10, drop_pending_updates=True)


if __name__ == "__main__":
    main()
