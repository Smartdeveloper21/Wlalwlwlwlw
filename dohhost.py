import os
import asyncio
import nest_asyncio
from telethon import TelegramClient, events
from telethon.sessions import MemorySession

nest_asyncio.apply()

# --- CONFIGURATION ---
API_ID = 30916259
API_HASH = "a01730fb9ca58d12d51f4b7c9c732eda"
BOT_TOKEN = "8647748004:AAFwN7zl5Nu_RprlKbVOGMKMHA5-8O4taSc"

# Using MemorySession for Colab stability
client = TelegramClient(MemorySession(), API_ID, API_HASH)

user_states = {}

@client.on(events.NewMessage(pattern="/start"))
async def start(event):
    await event.respond("Hi! I am active. Send me a file and then reply to it with a new name.")

@client.on(events.NewMessage(func=lambda e: e.document))
async def handle_document(event):
    sender_id = event.sender_id
    file_name = event.document.attributes[0].file_name if event.document.attributes else "file"
    ext = os.path.splitext(file_name)[1]
    user_states[sender_id] = {"file": event.document, "ext": ext}
    await event.respond(f"Received: `{file_name}`. Reply to this file with the new name (without extension).")

@client.on(events.NewMessage(func=lambda e: e.is_reply))
async def handle_rename(event):
    sender_id = event.sender_id
    if sender_id not in user_states: return
    
    state = user_states.pop(sender_id)
    new_name = event.text.strip() + state["ext"]
    status = await event.respond(f"🔄 Renaming to `{new_name}`...")
    
    try:
        path = await client.download_media(state["file"], file=new_name)
        await client.send_file(event.chat_id, path, caption=f"✅ Renamed: `{new_name}`")
        if os.path.exists(path): os.remove(path)
    except Exception as e:
        await event.respond(f"Error: {str(e)}")
    finally:
        await status.delete()

async def run_bot():
    if not client.is_connected():
        await client.start(bot_token=BOT_TOKEN)
    print("Bot is now active and running!")
    await client.run_until_disconnected()

# Using the loop to run the bot and block the cell so output is visible
if __name__ == '__main__':
    loop = asyncio.get_event_loop()
    try:
        loop.run_until_complete(run_bot())
    except Exception as e:
        print(f"Bot stopped: {e}")
