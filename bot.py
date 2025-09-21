# © 2025 Kaustav Ray. All rights reserved.
# Licensed under the MIT License.

import logging
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from pymongo import MongoClient
from bson.objectid import ObjectId

# Enable logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# ==========================
# CONFIGURATION
# ==========================
BOT_TOKEN = "8410215954:AAE0icLhQeXs4aIU0pA_wrhMbOOziPQLx24"

DB_CHANNEL = -1002975831610  # Database channel ID
ADMINS = [6280045392, 6705618257]  # Admin IDs

MONGO_URI = (
    "mongodb+srv://bf44tb5_db_user:RhyeHAHsTJeuBPNg@cluster0.lgao3zu.mongodb.net/"
    "?retryWrites=true&w=majority&appName=Cluster0"
)
mongo_client = MongoClient(MONGO_URI)
db = mongo_client["telegram_files"]
files_col = db["files"]

# Initialize bot
app = Client("file_index_bot", bot_token=BOT_TOKEN)


# ==========================
# HANDLERS
# ==========================

# Save incoming files from admins
@app.on_message(filters.user(ADMINS) & (filters.document | filters.video | filters.audio))
async def save_file(client, message):
    try:
        file = message.document or message.video or message.audio
        if not file:
            return

        forwarded = await message.forward(DB_CHANNEL)

        file_data = {
            "file_name": file.file_name,
            "file_id": forwarded.id,
            "channel_id": forwarded.chat.id,
        }
        files_col.insert_one(file_data)

        await message.reply_text(f"✅ File saved: **{file.file_name}**")
    except Exception as e:
        logger.error(f"Error saving file: {e}")
        await message.reply_text("❌ Failed to save file.")


# Search for files
@app.on_message(filters.private & filters.text)
async def search_files(client, message):
    query = message.text.strip()
    results = list(files_col.find({"file_name": {"$regex": query, "$options": "i"}}).limit(50))

    if not results:
        await message.reply_text("❌ No files found.")
        return

    await send_results_page(message.chat.id, results, page=0)


async def send_results_page(chat_id, results, page):
    start, end = page * 10, (page + 1) * 10
    page_results = results[start:end]

    text = ""
    buttons = []
    for idx, file in enumerate(page_results, start=start + 1):
        text += f"**{idx}.** {file['file_name']}\n"
        buttons.append(
            [InlineKeyboardButton(file["file_name"][:40], callback_data=f"get_{file['_id']}")]
        )

    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"page_{page-1}"))
    if end < len(results):
        nav_buttons.append(InlineKeyboardButton("Next ➡️", callback_data=f"page_{page+1}"))

    if nav_buttons:
        buttons.append(nav_buttons)

    buttons.append([InlineKeyboardButton("📨 Send All Files", callback_data=f"sendall_{page}")])

    await app.send_message(chat_id, text or "No results found.",
                           reply_markup=InlineKeyboardMarkup(buttons))


# Handle button clicks
@app.on_callback_query()
async def callback_handler(client, cq: CallbackQuery):
    data = cq.data

    if data.startswith("get_"):
        file_id = data.split("_", 1)[1]
        file_data = files_col.find_one({"_id": ObjectId(file_id)})
        if file_data:
            try:
                await app.copy_message(cq.message.chat.id, file_data["channel_id"], file_data["file_id"])
            except Exception as e:
                logger.error(f"Error sending file: {e}")
                await cq.answer("❌ Failed to fetch file.")
        else:
            await cq.answer("❌ File not found.")

    elif data.startswith("page_"):
        page = int(data.split("_", 1)[1])
        query = cq.message.text.split("\n")[0].strip()
        results = list(files_col.find({"file_name": {"$regex": query, "$options": "i"}}).limit(50))
        await cq.message.delete()
        await send_results_page(cq.message.chat.id, results, page)

    elif data.startswith("sendall_"):
        page = int(data.split("_", 1)[1])
        query = cq.message.text.split("\n")[0].strip()
        results = list(files_col.find({"file_name": {"$regex": query, "$options": "i"}}).limit(50))
        start, end = page * 10, (page + 1) * 10
        for file in results[start:end]:
            try:
                await app.copy_message(cq.message.chat.id, file["channel_id"], file["file_id"])
            except Exception as e:
                logger.error(f"Error sending all files: {e}")
        await cq.answer("📨 Sent all files!")


# ==========================
# RUN BOT
# ==========================
if __name__ == "__main__":
    logger.info("Bot started...")
    app.run()
