# © 2025 Kaustav Ray. All rights reserved.
# Licensed under the MIT License.

import logging
import asyncio
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
import math
import re
import io

# ========================
# CONFIG
# ========================
BOT_TOKEN = "8410215954:AAE0icLhQeXs4aIU0pA_wrhMbOOziPQLx24"  # Bot Token
DB_CHANNEL = -1002975831610  # Database channel
LOG_CHANNEL = -1002988891392  # Channel to log user queries
# NEW: Reverted to the correct channel IDs
JOIN_CHECK_CHANNEL = [-1002692055617, -1002551875503, -1002839913869]
ADMINS = [6705618257]        # Admin IDs

# NEW: Updated to match the new channels you provided
PROMOTIONAL_LINKS = [
    "Credit to Prince Kaustav Ray",
    "Join our main channel: @filestore4u",
    "Join our channel: @code_boost",
    "Join our channel: @krbook_official"
]

# A list of MongoDB URIs to use. Add as many as you need.
# The bot will try them in order if a connection or insert fails.
MONGO_URIS = [
    "mongodb+srv://bf44tb5_db_user:RhyeHAHsTJeuBPNg@cluster0.lgao3zu.mongodb.net/?retryWrites=true&w=majority&appName=Cluster0",
    "mongodb+srv://28c2kqa_db_user:IL51mem7W6g37mA5@cluster0.np0ffl0.mongodb.net/?retryWrites=true&w=majority&appName=Cluster0",
    "mongodb+srv://l6yml41j_db_user:2m5HFR6CTdSb46ck@cluster0.nztdqdr.mongodb.net/?retryWrites=true&w=majority&appName=Cluster0",
    "mongodb+srv://7afcwd6_db_user:sOthaH9f53BDRBoj@cluster0.m9d2zcy.mongodb.net/?retryWrites=true&w=majority&appName=Cluster0",
    "mongodb+srv://x05bq9p_db_user:gspcMp5M0NQnu9zt@cluster0.bhxd7dp.mongodb.net/?retryWrites=true&w=majority&appName=Cluster0",
    "mongodb+srv://fxexlqy_db_user:O5HiYEZee2pyUyGK@cluster0.ugozkfc.mongodb.net/?retryWrites=true&w=majority&appName=Cluster0",
    "mongodb+srv://vnitm0p_db_user:rz1Szy1U9fwJMkis@cluster0.apaqaef.mongodb.net/?retryWrites=true&w=majority&appName=Cluster0",
    "mongodb+srv://mezojs2_db_user:gvT09wd648MfGP5W@cluster0.c5hejzo.mongodb.net/?retryWrites=true&w=majority&appName=Cluster0",
]
current_uri_index = 0

mongo_client = None
db = None
files_col = None
users_col = None
banned_users_col = None


# Logging setup with an in-memory buffer for the /log command
log_stream = io.StringIO()
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO, stream=log_stream
)
logger = logging.getLogger(__name__)


# ========================
# HELPERS
# ========================

def escape_markdown(text: str) -> str:
    """Helper function to escape special characters in Markdown V2."""
    escape_chars = r'_*[]()~`>#+-=|{}.!'
    return "".join('\\' + char if char in escape_chars else char for char in text)

def format_size(size_in_bytes: int) -> str:
    """Converts a size in bytes to a human-readable format."""
    if size_in_bytes is None:
        return "N/A"
    
    if size_in_bytes == 0:
        return "0 B"
    
    size_name = ("B", "KB", "MB", "GB", "TB", "PB", "EB", "ZB", "YB")
    i = int(math.floor(math.log(size_in_bytes, 1024)))
    p = math.pow(1024, i)
    s = round(size_in_bytes / p, 2)
    return f"{s} {size_name[i]}"


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
    """Check if the user is a member of ALL required channels."""
    for channel_id in JOIN_CHECK_CHANNEL:
        try:
            member = await context.bot.get_chat_member(chat_id=channel_id, user_id=user_id)
            if member.status not in ["member", "administrator", "creator"]:
                return False
        except TelegramError as e:
            logger.error(f"Error checking member status for user {user_id} in channel {channel_id}: {e}")
            return False
            
    return True

async def is_banned(user_id):
    """Check if the user is banned."""
    if banned_users_col is not None:
        return banned_users_col.find_one({"_id": user_id}) is not None
    return False

def connect_to_mongo():
    """Connect to the MongoDB URI at the current index."""
    global mongo_client, db, files_col, users_col, banned_users_col
    try:
        uri = MONGO_URIS[current_uri_index]
        # Set serverSelectionTimeoutMS to 5 seconds to fail fast if the connection is dead
        mongo_client = MongoClient(uri, serverSelectionTimeoutMS=5000)
        # The ismaster command is cheap and does not require auth.
        # It forces the client to check the connection.
        mongo_client.admin.command('ismaster')
        
        db = mongo_client["telegram_files"]
        files_col = db["files"]
        users_col = db["users"]
        banned_users_col = db["banned_users"]
        logger.info(f"Successfully connected to MongoDB at index {current_uri_index}.")
        return True
    except (PyMongoError, IndexError) as e:
        logger.error(f"Failed to connect to MongoDB at index {current_uri_index}: {e}")
        return False

async def save_user_info(user: Update.effective_user):
    """Saves user information to the database if not already present."""
    if users_col is not None:
        try:
            users_col.update_one(
                {"_id": user.id},
                {
                    "$set": {
                        "first_name": user.first_name,
                        "last_name": user.last_name,
                        "username": user.username,
                    }
                },
                upsert=True
            )
        except Exception as e:
            logger.error(f"Error saving user info for {user.id}: {e}")


# ========================
# TASK FUNCTIONS (FOR BACKGROUND EXECUTION)
# ========================

async def send_file_task(query, context, file_data):
    """Background task to send a single file to the user's private chat and auto-delete it."""
    user_id = query.from_user.id
    try:
        sent_message = await context.bot.copy_message(
            chat_id=user_id, # Ensure it's sent to the user's private chat
            from_chat_id=file_data["channel_id"],
            message_id=file_data["file_id"],
        )
        
        # Send promotional links
        for promo_text in PROMOTIONAL_LINKS:
            await context.bot.send_message(
                chat_id=user_id,
                text=promo_text
            )
        
        # If the message was sent successfully, wait and then delete it
        if sent_message:
            # Notify the user in the original chat (can be group/private)
            await query.message.reply_text("✅ I have sent the file to you in a private message. The file will be deleted automatically in 5 minutes.")
            
            # Wait for 5 minutes
            await asyncio.sleep(5 * 60)
            
            # Delete the message
            await context.bot.delete_message(
                chat_id=user_id,
                message_id=sent_message.message_id
            )
            logger.info(f"Deleted message {sent_message.message_id} from chat {user_id}.")
        
    except TelegramError as e:
        logger.error(f"Failed to send file to user {user_id}: {e}")
        # Only reply in the chat where the button was clicked (group/private)
        await query.message.reply_text("❌ File not found or could not be sent. Please ensure the bot is not blocked in your private chat.")
    except Exception as e:
        logger.error(f"An unexpected error occurred while sending the file: {e}")
        await query.message.reply_text("❌ An unexpected error occurred. Please try again later.")


async def send_all_files_task(query, context, file_list):
    """Background task to send multiple files to the user's private chat and auto-delete them."""
    user_id = query.from_user.id
    
    # Send all files
    sent_messages = []
    try:
        for file in file_list:
            # Ensure it's sent to the user's private chat
            sent_message = await context.bot.copy_message(
                chat_id=user_id,
                from_chat_id=file["channel_id"],
                message_id=file["file_id"],
            )
            sent_messages.append(sent_message.message_id)
            # Add a small delay between sending files to avoid rate limits
            await asyncio.sleep(0.5)

        # Send promotional links to user's private chat
        for promo_text in PROMOTIONAL_LINKS:
            await context.bot.send_message(
                chat_id=user_id,
                text=promo_text
            )
            await asyncio.sleep(0.1) # small delay for promo links

        # Send final confirmation message to the chat where the button was clicked
        await query.message.reply_text(
            "✅ I have sent all files to you in a private message. The files will be deleted automatically in 5 minutes."
        )

        # Wait for 5 minutes
        await asyncio.sleep(5 * 60)

        # Delete all sent messages
        for message_id in sent_messages:
            try:
                await context.bot.delete_message(
                    chat_id=user_id,
                    message_id=message_id
                )
                logger.info(f"Deleted message {message_id} from chat {user_id}.")
            except TelegramError as e:
                logger.warning(f"Failed to delete message {message_id} for user {user_id}: {e}")

    except TelegramError as e:
        logger.error(f"Failed to send one or more files to user {user_id}: {e}")
        await query.message.reply_text("❌ One or more files could not be sent. Please ensure the bot is not blocked in your private chat.")
    except Exception as e:
        logger.error(f"An unexpected error occurred while sending all files: {e}")
        await query.message.reply_text("❌ An unexpected error occurred. Please try again later.")

# ========================
# COMMAND HANDLERS
# ========================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await is_banned(update.effective_user.id):
        await update.message.reply_text("❌ You are banned from using this bot.")
        return
    await save_user_info(update.effective_user)
    await update.message.reply_text("👋 Send me a movie name to search.")


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Shows the help message and available commands."""
    if await is_banned(update.effective_user.id):
        await update.message.reply_text("❌ You are banned from using this bot.")
        return
    help_message = (
        "Hello! I am a file search bot. Here's how to use me:\n\n"
        "**User Commands:**\n"
        "🎬 Send me a movie name to search for files.\n"
        "  - Example: `The Matrix`\n"
        "ℹ️ `/info`: Get information about this bot.\n"
        "❓ `/help`: Show this help message.\n\n"
        "**Admin Commands:**\n"
        "⬆️ Send me a file with a caption to upload it.\n"
        "  - The file will be saved to the database and is searchable.\n"
        "⬆️ You can also send a file directly in the database channel to index it.\n"
        "📢 `/broadcast <message>`: Send a message to all users.\n"
        "👥 `/total_users`: Get the total number of users.\n"
        "🗃️ `/total_files`: Get the total number of files.\n"
        "📊 `/stats`: Get bot statistics (total users, total files, and DB status).\n"
        "🗑️ `/deletefile <db_id>`: Delete a file from the database.\n"
        "  - Use `/findfile <filename>` to get the ID first.\n"
        "📁 `/findfile <filename>`: Find a file by name and get its ID.\n"
        "🗑️ `/deleteall`: Delete all files from the database.\n"
        "🔨 `/ban <user_id>`: Ban a user from the bot.\n"
        "✅ `/unban <user_id>`: Unban a user.\n"
        "📝 `/log`: Show recent error logs.\n"
    )
    await update.message.reply_text(help_message, parse_mode="Markdown")


async def info_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Shows information about the bot."""
    if await is_banned(update.effective_user.id):
        await update.message.reply_text("❌ You are banned from using this bot.")
        return
    info_message = (
        "**About this Bot**\n\n"
        "This bot helps you find and share files on Telegram.\n"
        "• Developed by Kaustav Ray."
    )
    await update.message.reply_text(info_message, parse_mode="Markdown")


async def log_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin command to show recent error logs."""
    user_id = update.effective_user.id
    if user_id not in ADMINS:
        await update.message.reply_text("❌ You do not have permission to use this command.")
        return
    
    # Retrieve all logs from the in-memory stream
    log_stream.seek(0)
    logs = log_stream.readlines()
    
    # Filter for ERROR and CRITICAL logs and get the last 20
    error_logs = [log.strip() for log in logs if "ERROR" in log or "CRITICAL" in log]
    recent_errors = error_logs[-20:]

    if not recent_errors:
        await update.message.reply_text("✅ No recent errors found in the logs.")
    else:
        log_text = "```\nRecent Error Logs:\n\n" + "\n".join(recent_errors) + "\n```"
        await update.message.reply_text(log_text, parse_mode="MarkdownV2")
    
    # Clear the log buffer to prevent it from growing too large
    log_stream.seek(0)
    log_stream.truncate(0)


async def total_users_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin command to get the total number of users."""
    user_id = update.effective_user.id
    if user_id not in ADMINS:
        await update.message.reply_text("❌ You do not have permission to use this command.")
        return
    
    if users_col is None:
        await update.message.reply_text("❌ Database not connected.")
        return

    try:
        user_count = users_col.count_documents({})
        await update.message.reply_text(f"📊 **Total Users:** {user_count}")
    except Exception as e:
        logger.error(f"Error getting user count: {e}")
        await update.message.reply_text("❌ Failed to retrieve user count. Please check the database connection.")


async def total_files_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin command to get the total number of files."""
    user_id = update.effective_user.id
    if user_id not in ADMINS:
        await update.message.reply_text("❌ You do not have permission to use this command.")
        return
    
    if files_col is None:
        await update.message.reply_text("❌ Database not connected.")
        return

    try:
        file_count = files_col.count_documents({})
        await update.message.reply_text(f"🗃️ **Total Files:** {file_count}")
    except Exception as e:
        logger.error(f"Error getting file count: {e}")
        await update.message.reply_text("❌ Failed to retrieve file count. Please check the database connection.")


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin command to get bot statistics, including per-URI file counts."""
    user_id = update.effective_user.id
    if user_id not in ADMINS:
        await update.message.reply_text("❌ You do not have permission to use this command.")
        return

    await update.message.reply_text("🔄 Collecting statistics, please wait...")

    user_count = 0
    total_file_count = 0
    uri_stats = {}

    try:
        # 1. Get Total Users (from the currently connected DB)
        if users_col is not None:
            user_count = users_col.count_documents({})
        
        # 2. Get Total Files (from the currently connected DB)
        if files_col is not None:
            total_file_count = files_col.count_documents({})

        # 3. Get File Counts per URI
        for idx, uri in enumerate(MONGO_URIS):
            temp_client = None
            try:
                # Temporarily connect to each URI
                temp_client = MongoClient(uri, serverSelectionTimeoutMS=5000)
                temp_client.admin.command('ismaster')
                temp_db = temp_client["telegram_files"]
                temp_files_col = temp_db["files"]
                # Use estimated_document_count for fast approximation
                file_count = temp_files_col.estimated_document_count() 
                uri_stats[idx] = f"✅ {file_count} files"
            except Exception as e:
                logger.warning(f"Failed to connect or get file count for URI #{idx + 1}: {e}")
                uri_stats[idx] = "❌ Failed to connect/read"
            finally:
                if temp_client:
                    temp_client.close()

        # 4. Format the output message
        stats_message = (
            f"📊 **Bot Statistics**\n"
            f"  • Total Users: {user_count}\n"
            f"  • Total Files (Current DB): {total_file_count}\n"
            f"  • **Total MongoDB URIs:** {len(MONGO_URIS)}\n"
            f"  • **Current Active URI:** #{current_uri_index + 1}\n\n"
            f"**File Count per URI:**\n"
        )
        for idx, status in uri_stats.items():
            stats_message += f"  • URI #{idx + 1}: {status}\n"

        await update.message.reply_text(stats_message, parse_mode="Markdown")
        
    except Exception as e:
        logger.error(f"Error getting bot stats: {e}")
        await update.message.reply_text("❌ Failed to retrieve statistics. Please check the database connection.")


async def delete_file_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin command to delete a file by its MongoDB ID."""
    user_id = update.effective_user.id
    if user_id not in ADMINS:
        await update.message.reply_text("❌ You do not have permission to use this command.")
        return
    
    if files_col is None:
        await update.message.reply_text("❌ Database not connected.")
        return

    if not context.args:
        await update.message.reply_text("Usage: /deletefile <MongoDB_ID>\nTip: Use /findfile <filename> to get the ID.")
        return
    
    try:
        file_id = context.args[0]
        # NOTE: This only deletes from the *current* active database.
        result = files_col.delete_one({"_id": ObjectId(file_id)})
        
        if result.deleted_count == 1:
            await update.message.reply_text(f"✅ File with ID `{file_id}` has been deleted from the database.")
        else:
            await update.message.reply_text(f"❌ File with ID `{file_id}` not found in the database.")
    except Exception as e:
        logger.error(f"Error deleting file: {e}")
        await update.message.reply_text("❌ Invalid ID or an error occurred. Please provide a valid MongoDB ID.")


async def find_file_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin command to find a file by its name and show its ID. Searches ALL URIs."""
    user_id = update.effective_user.id
    if user_id not in ADMINS:
        await update.message.reply_text("❌ You do not have permission to use this command.")
        return
    
    if not context.args:
        await update.message.reply_text("Usage: /findfile <filename>")
        return

    query_filename = " ".join(context.args)
    all_results = []
    
    await update.message.reply_text(f"🔎 Searching all {len(MONGO_URIS)} databases for `{query_filename}`...")

    # Iterate through all URIs
    for idx, uri in enumerate(MONGO_URIS):
        temp_client = None
        try:
            temp_client = MongoClient(uri, serverSelectionTimeoutMS=5000)
            temp_client.admin.command('ismaster')
            temp_db = temp_client["telegram_files"]
            temp_files_col = temp_db["files"]
            
            # Use regex for case-insensitive search
            results = list(temp_files_col.find({"file_name": {"$regex": query_filename, "$options": "i"}}))
            all_results.extend(results)
            logger.info(f"Found {len(results)} files in URI #{idx + 1}")
        except Exception as e:
            logger.error(f"Error finding file on URI #{idx + 1}: {e}")
        finally:
            if temp_client:
                temp_client.close()


    if not all_results:
        await update.message.reply_text(f"❌ No files found with the name `{query_filename}` in any database.")
        return

    response_text = f"📁 Found {len(all_results)} files matching `{query_filename}` across all databases:\n\n"
    for idx, file in enumerate(all_results):
        response_text += f"{idx + 1}. *{escape_markdown(file['file_name'])}*\n  `ID: {file['_id']}`\n\n"
    
    response_text += "Copy the ID of the file you want to delete and use the command:\n`/deletefile <ID>`\n\nNote: `/deletefile` only works on the currently *active* database. If the file is not found, you may need to manually update the `current_uri_index` and restart."
    
    await update.message.reply_text(response_text, parse_mode="Markdown")


async def delete_all_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin command to delete all files from the database."""
    user_id = update.effective_user.id
    if user_id not in ADMINS:
        await update.message.reply_text("❌ You do not have permission to use this command.")
        return
    
    if files_col is None:
        await update.message.reply_text("❌ Database not connected.")
        return

    try:
        # NOTE: This only deletes from the *current* active database.
        result = files_col.delete_many({})
        await update.message.reply_text(f"✅ Deleted {result.deleted_count} files from the **current** database.")
    except Exception as e:
        logger.error(f"Error deleting all files: {e}")
        await update.message.reply_text("❌ An error occurred while trying to delete all files from the current database.")


async def ban_user_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin command to ban a user by their user ID."""
    user_id = update.effective_user.id
    if user_id not in ADMINS:
        await update.message.reply_text("❌ You do not have permission to use this command.")
        return
    
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("Usage: /ban <user_id>")
        return
    
    user_to_ban_id = int(context.args[0])
    if user_to_ban_id in ADMINS:
        await update.message.reply_text("❌ Cannot ban an admin.")
        return

    if banned_users_col is None:
        await update.message.reply_text("❌ Database not connected.")
        return

    try:
        banned_users_col.update_one(
            {"_id": user_to_ban_id},
            {"$set": {"_id": user_to_ban_id}},
            upsert=True
        )
        await update.message.reply_text(f"🔨 User `{user_to_ban_id}` has been banned.")
    except Exception as e:
        logger.error(f"Error banning user: {e}")
        await update.message.reply_text("❌ An error occurred while trying to ban the user.")


async def unban_user_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin command to unban a user by their user ID."""
    user_id = update.effective_user.id
    if user_id not in ADMINS:
        await update.message.reply_text("❌ You do not have permission to use this command.")
        return
    
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("Usage: /unban <user_id>")
        return
    
    user_to_unban_id = int(context.args[0])

    if banned_users_col is None:
        await update.message.reply_text("❌ Database not connected.")
        return

    try:
        result = banned_users_col.delete_one({"_id": user_to_unban_id})
        
        if result.deleted_count == 1:
            await update.message.reply_text(f"✅ User `{user_to_unban_id}` has been unbanned.")
        else:
            await update.message.reply_text(f"❌ User `{user_to_unban_id}` was not found in the banned list.")
    except Exception as e:
        logger.error(f"Error unbanning user: {e}")
        await update.message.reply_text("❌ An error occurred while trying to unban the user.")


async def broadcast_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Broadcasts a message to all users in the database.
    Usage: /broadcast <message>
    """
    user_id = update.effective_user.id
    if user_id not in ADMINS:
        await update.message.reply_text("❌ You do not have permission to use this command.")
        return

    if not context.args:
        await update.message.reply_text("Usage: /broadcast <message>")
        return

    broadcast_text = " ".join(context.args)
    
    # NOTE: This only broadcasts to users in the *current* active database's users_col.
    # To broadcast to ALL users, you'd need to query all URIs for user IDs.
    users_cursor = users_col.find({}, {"_id": 1})
    user_ids = [user["_id"] for user in users_cursor]
    sent_count = 0
    failed_count = 0

    await update.message.reply_text(f"🚀 Starting broadcast to {len(user_ids)} users...")

    for uid in user_ids:
        try:
            await context.bot.send_message(chat_id=uid, text=broadcast_text)
            sent_count += 1
            await asyncio.sleep(0.1)
        except TelegramError as e:
            failed_count += 1
            logger.error(f"Failed to send broadcast to user {uid}: {e}")
        except Exception as e:
            failed_count += 1
            logger.error(f"Unknown error sending broadcast to user {uid}: {e}")
    
    await update.message.reply_text(f"✅ Broadcast complete!\n\nSent to: {sent_count}\nFailed: {failed_count}")


# ========================
# FILE/SEARCH HANDLERS
# ========================

async def save_file_from_pm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin sends file to bot -> save to channel + DB"""
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
    temp_uri_index = current_uri_index
    # Start the loop from the current active index and wrap around to try all
    for i in range(len(MONGO_URIS)):
        idx = (temp_uri_index + i) % len(MONGO_URIS)
        uri_to_try = MONGO_URIS[idx]
        
        temp_client = None
        try:
            # If we're not on the currently connected URI, we need a new client
            if idx != current_uri_index or files_col is None:
                temp_client = MongoClient(uri_to_try, serverSelectionTimeoutMS=5000)
                temp_client.admin.command('ismaster')
                temp_db = temp_client["telegram_files"]
                temp_files_col = temp_db["files"]
            else:
                # Use the global client
                temp_files_col = files_col

            # Try to save metadata
            temp_files_col.insert_one({
                "file_name": clean_name,
                "file_id": forwarded.message_id,
                "channel_id": forwarded.chat.id,
                "file_size": file.file_size, 
            })
            
            # If successful, set the current global index to this one
            if idx != current_uri_index:
                global mongo_client, db, users_col, banned_users_col
                # Close old client if needed
                if mongo_client:
                    mongo_client.close()
                
                # Update global connection pointers
                mongo_client = temp_client
                db = temp_db
                files_col = temp_files_col
                current_uri_index = idx
                logger.info(f"Switched active MongoDB connection to index {current_uri_index}.")
            
            await update.message.reply_text(f"✅ Saved to DB #{idx + 1}: {clean_name}")
            saved = True
            break
        except Exception as e:
            logger.error(f"Error saving file with URI #{idx + 1}: {e}")
            if idx == current_uri_index and len(MONGO_URIS) > 1:
                 await update.message.reply_text(f"⚠️ Primary DB failed. Trying next available URI...")
        finally:
            if temp_client and idx != current_uri_index:
                temp_client.close()


    if not saved:
        logger.error("All MongoDB URIs have been tried and failed.")
        await update.message.reply_text("❌ Failed to save file on all available databases.")


async def save_file_from_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin sends file directly to channel -> save to DB"""
    user_id = update.message.from_user.id
    chat_id = update.message.chat.id

    # Only process files from admins in the database channel
    if chat_id != DB_CHANNEL or user_id not in ADMINS:
        return

    file = update.message.document or update.message.video or update.message.audio
    if not file:
        return

    # Get filename from caption, then from file_name, replacing underscores, dots, and hyphens with spaces
    # Otherwise, use a default value
    if update.message.caption:
        raw_name = update.message.caption
    else:
        raw_name = getattr(file, "file_name", None) or getattr(file, "title", None) or file.file_unique_id
    
    clean_name = raw_name.replace("_", " ").replace(".", " ").replace("-", " ") if raw_name else "Unknown"

    global current_uri_index, files_col
    
    saved = False
    temp_uri_index = current_uri_index
    
    for i in range(len(MONGO_URIS)):
        idx = (temp_uri_index + i) % len(MONGO_URIS)
        uri_to_try = MONGO_URIS[idx]
        
        temp_client = None
        try:
            if idx != current_uri_index or files_col is None:
                temp_client = MongoClient(uri_to_try, serverSelectionTimeoutMS=5000)
                temp_client.admin.command('ismaster')
                temp_db = temp_client["telegram_files"]
                temp_files_col = temp_db["files"]
            else:
                temp_files_col = files_col

            # Try to save metadata
            temp_files_col.insert_one({
                "file_name": clean_name,
                "file_id": update.message.message_id,
                "channel_id": chat_id,
                "file_size": file.file_size, 
            })
            
            # If successful, set the current global index to this one
            if idx != current_uri_index:
                global mongo_client, db, users_col, banned_users_col
                if mongo_client:
                    mongo_client.close()
                
                mongo_client = temp_client
                db = temp_db
                files_col = temp_files_col
                current_uri_index = idx
                logger.info(f"Switched active MongoDB connection to index {current_uri_index}.")
            
            # Send **INSTANT** success notification to the admin
            try:
                await context.bot.send_message(
                    user_id, 
                    f"✅ File **`{escape_markdown(clean_name)}`** has been indexed successfully from the database channel to DB #{idx + 1}.",
                    parse_mode="MarkdownV2"
                )
            except TelegramError as e:
                logger.error(f"Failed to send notification to admin {user_id}: {e}")
            saved = True
            break
            
        except Exception as e:
            logger.error(f"Error saving file from channel with URI #{idx + 1}: {e}")
            if idx == current_uri_index and len(MONGO_URIS) > 1:
                try:
                    await context.bot.send_message(user_id, "⚠️ Primary DB failed. Trying next available URI...")
                except TelegramError:
                    pass
        finally:
            if temp_client and idx != current_uri_index:
                temp_client.close()

    if not saved:
        logger.error("All MongoDB URIs have been tried and failed.")
        try:
            await context.bot.send_message(user_id, "❌ Failed to save file on all available databases.")
        except TelegramError:
            pass


async def search_files(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Search ALL URIs and show results, sorted by relevance"""
    
    if await is_banned(update.effective_user.id):
        await update.message.reply_text("❌ You are banned from using this bot.")
        return
    
    await save_user_info(update.effective_user)
    if not await check_member_status(update.effective_user.id, context):
        # NEW: Updated to show buttons for all channels
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("Join Channel: @filestore4u", url="https://t.me/filestore4u")],
            [InlineKeyboardButton("Join Channel: @code_boost", url="https://t.me/code_boost")],
            [InlineKeyboardButton("Join Channel: @krbook_official", url="https://t.me/krbook_official")]
        ])
        await update.message.reply_text("❌ You must join ALL our channels to use this bot!", reply_markup=keyboard)
        return

    # Send instant feedback
    await update.message.reply_text(f"🔍 Searching all {len(MONGO_URIS)} databases...")

    raw_query = update.message.text.strip()
    # Normalize query for better fuzzy search
    normalized_query = raw_query.replace("_", " ").replace(".", " ").replace("-", " ").strip()

    # Log the user's query
    user = update.effective_user
    log_text = f"🔍 User: {user.full_name} | @{user.username} | ID: {user.id}\nQuery: {raw_query}"
    try:
        await context.bot.send_message(LOG_CHANNEL, text=log_text)
    except Exception as e:
        logger.error(f"Failed to log query to channel: {e}")

    # Use a more flexible regex that finds words in any order
    words = normalized_query.split()
    regex_pattern = re.compile(".*".join(map(re.escape, words)), re.IGNORECASE)
    
    preliminary_results = []
    
    # --- NEW LOGIC: Iterate over ALL URIs for search ---
    for idx, uri in enumerate(MONGO_URIS):
        temp_client = None
        try:
            # Temporarily connect to each URI
            temp_client = MongoClient(uri, serverSelectionTimeoutMS=5000)
            temp_client.admin.command('ismaster')
            temp_db = temp_client["telegram_files"]
            temp_files_col = temp_db["files"]
            
            # Query the database
            results = list(temp_files_col.find({"file_name": {"$regex": regex_pattern}}))
            preliminary_results.extend(results)
            logger.info(f"Found {len(results)} matches in URI #{idx + 1}.")
            
        except Exception as e:
            logger.error(f"MongoDB search query failed on URI #{idx + 1}: {e}")
        finally:
            if temp_client:
                temp_client.close()
    # --- END NEW LOGIC ---

    if not preliminary_results:
        await update.message.reply_text("❌ No relevant files found. For your query contact @kaustavhibot")
        return

    # Now, perform a more accurate fuzzy search on this aggregated list
    results_with_score = []
    # Use a set to track file_id + channel_id tuples to ensure no duplicates from different DBs
    unique_files = set() 
    
    for file in preliminary_results:
        file_key = (file.get('file_id'), file.get('channel_id'))
        if file_key in unique_files:
            continue
            
        score = fuzz.token_set_ratio(normalized_query, file['file_name'])
        if score > 40:
            results_with_score.append((file, score))
            unique_files.add(file_key)
    
    # Sort the results by score in descending order
    sorted_results = sorted(results_with_score, key=lambda x: x[1], reverse=True)

    # Extract the file documents from the sorted list and limit to the top 50
    final_results = [result[0] for result in sorted_results[:50]]
    
    if not final_results:
        await update.message.reply_text("❌ No relevant files found. For your query contact @kaustavhibot")
        return

    await send_results_page(update.effective_chat.id, final_results, 0, context, raw_query)


async def send_results_page(chat_id, results, page, context: ContextTypes.DEFAULT_TYPE, query: str):
    start, end = page * 10, (page + 1) * 10
    page_results = results[start:end]

    # Escape the query string for Markdown
    escaped_query = escape_markdown(query)
    text = f"🔎 Results for: *{escaped_query}*"
    buttons = []
    for idx, file in enumerate(page_results, start=start + 1):
        # Format the filename first, then escape it for Markdown
        two_line_name = format_filename_for_display(file['file_name'])
        
        file_size = format_size(file.get("file_size"))
        
        # We need the full file object ID for retrieval
        file_obj_id = str(file['_id'])
        
        button_text = f"[{file_size}] {file['file_name'][:40]}"
        buttons.append(
            [InlineKeyboardButton(button_text, callback_data=f"get_{file_obj_id}")]
        )
    
    # Add the promotional text at the end
    text += "\n\nKaustav Ray                                                                                                      Join here: @filestore4u     @freemovie5u"

    nav_buttons = []
    # We pass the full search results list along in context.user_data to prevent re-querying all URIs for pagination
    # For simplicity here, we'll store the results temporarily. The full solution requires a dedicated caching mechanism.
    # Storing the results in context.user_data will be lost on bot restart, but works for the current user session.
    # The current code relies on the search query, which is acceptable for this code structure.
    
    if page > 0:
        # The query string in the callback needs to be properly URL-encoded if it contains special characters
        # But for this bot's simple query, passing the raw query should be fine.
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
    # Provide an immediate answer to the callback query to clear the loading state on the button
    query = update.callback_query
    await query.answer()

    if await is_banned(update.effective_user.id):
        await query.message.reply_text("❌ You are banned from using this bot.")
        return

    await save_user_info(update.effective_user)
    if not await check_member_status(update.effective_user.id, context):
        # NEW: Updated to show buttons for all channels
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("Join Channel: @filestore4u", url="https://t.me/filestore4u")],
            [InlineKeyboardButton("Join Channel: @code_boost", url="https://t.me/code_boost")],
            [InlineKeyboardButton("Join Channel: @krbook_official", url="https://tme/krbook_official")]
        ])
        await query.message.reply_text("❌ You must join ALL our channels to use this bot!", reply_markup=keyboard)
        return

    data = query.data

    if data.startswith("get_"):
        # Send a message to the user to confirm the request is being processed
        await query.message.reply_text("⌛ Processing your request, please wait...")
        
        file_id_str = data.split("_", 1)[1]
        file_data = None
        
        # --- NEW LOGIC: Check ALL URIs to find the file by its MongoDB ID ---
        for uri in MONGO_URIS:
            temp_client = None
            try:
                temp_client = MongoClient(uri, serverSelectionTimeoutMS=5000)
                temp_client.admin.command('ismaster')
                temp_db = temp_client["telegram_files"]
                temp_files_col = temp_db["files"]
                
                file_data = temp_files_col.find_one({"_id": ObjectId(file_id_str)})
                if file_data:
                    logger.info(f"File {file_id_str} found for retrieval in URI: {uri}")
                    break # Found the file, stop searching
            except Exception as e:
                logger.error(f"Error checking file ID {file_id_str} in URI {uri}: {e}")
            finally:
                if temp_client:
                    temp_client.close()
        # --- END NEW LOGIC ---

        if file_data:
            # Schedule the slow `send_file_task` to run in the background.
            asyncio.create_task(send_file_task(query, context, file_data))
        else:
            await query.message.reply_text("❌ File not found. The file may have been deleted or the database is inaccessible.")

    elif data.startswith("page_") or data.startswith("sendall_"):
        
        if data.startswith("sendall_"):
            await query.message.reply_text("📨 Sending all files in the current list. Please check your private chat with me. This may take a moment...")

        _, page_str, search_query = data.split("_", 2)
        page = int(page_str)
        
        # Re-run the search logic for pagination/sendall
        normalized_query = search_query.replace("_", " ").replace(".", " ").replace("-", " ").strip()
        
        # Use a more flexible regex that finds words in any order
        words = normalized_query.split()
        regex_pattern = re.compile(".*".join(map(re.escape, words)), re.IGNORECASE)
        
        preliminary_results = []
        
        # --- Rerun Search Logic: Iterate over ALL URIs for search ---
        for idx, uri in enumerate(MONGO_URIS):
            temp_client = None
            try:
                temp_client = MongoClient(uri, serverSelectionTimeoutMS=5000)
                temp_client.admin.command('ismaster')
                temp_db = temp_client["telegram_files"]
                temp_files_col = temp_db["files"]
                
                results = list(temp_files_col.find({"file_name": {"$regex": regex_pattern}}))
                preliminary_results.extend(results)
            except Exception as e:
                logger.error(f"MongoDB search query failed on URI #{idx + 1}: {e}")
            finally:
                if temp_client:
                    temp_client.close()
        # --- End Rerun Search Logic ---
        
        # Now, perform a more accurate fuzzy search on this aggregated list
        results_with_score = []
        unique_files = set() 
        for file in preliminary_results:
            file_key = (file.get('file_id'), file.get('channel_id'))
            if file_key in unique_files:
                continue
            
            score = fuzz.token_set_ratio(normalized_query, file['file_name'])
            if score > 40:
                results_with_score.append((file, score))
                unique_files.add(file_key)
        
        sorted_results = sorted(results_with_score, key=lambda x: x[1], reverse=True)
        final_results = [result[0] for result in sorted_results[:50]]
        
        if not final_results:
            await query.message.reply_text("❌ No relevant files found. For your query contact @kaustavhibot")
            return
        
        if data.startswith("page_"):
            # Edit the existing message to show the new page
            await query.message.delete()
            await send_results_page(query.message.chat.id, final_results, page, context, search_query)
        
        elif data.startswith("sendall_"):
            # Get the files only for the current page
            files_to_send = final_results[page * 10:(page + 1) * 10]
            
            if not files_to_send:
                await query.message.reply_text("❌ No files found on this page to send.")
                return

            # Schedule the batch sending task
            asyncio.create_task(send_all_files_task(query, context, files_to_send))


# ========================
# MAIN
# ========================

def main():
    if not connect_to_mongo():
        logger.critical("Failed to connect to the initial MongoDB URI. Exiting.")
        return

    app = Application.builder().token(BOT_TOKEN).build()

    # Command Handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("info", info_command))
    app.add_handler(CommandHandler("log", log_command))
    app.add_handler(CommandHandler("total_users", total_users_command))
    app.add_handler(CommandHandler("total_files", total_files_command))
    app.add_handler(CommandHandler("stats", stats_command)) # UPDATED handler
    app.add_handler(CommandHandler("deletefile", delete_file_command))
    app.add_handler(CommandHandler("findfile", find_file_command))
    app.add_handler(CommandHandler("deleteall", delete_all_command))
    app.add_handler(CommandHandler("ban", ban_user_command))
    app.add_handler(CommandHandler("unban", unban_user_command))
    app.add_handler(CommandHandler("broadcast", broadcast_message))

    # File and Message Handlers
    # Admin file upload via PM
    app.add_handler(MessageHandler(
        (filters.Document.ALL | filters.VIDEO | filters.AUDIO) & filters.ChatType.PRIVATE,
        save_file_from_pm
    ))
    
    # Admin file indexing via DB Channel
    app.add_handler(MessageHandler(
        (filters.Document.ALL | filters.VIDEO | filters.AUDIO) & filters.Chat(chat_id=DB_CHANNEL),
        save_file_from_channel
    ))

    # Text Search Handler
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, search_files))
    
    # Callback Query Handler (for buttons)
    app.add_handler(CallbackQueryHandler(button_handler))

    logger.info("Bot started...")
    # Start the bot
    app.run_polling(poll_interval=1, timeout=10, drop_pending_updates=True)


if __name__ == "__main__":
    main()
