import os
import logging
from telegram import Update
from telegram.ext import Application, ContextTypes, MessageHandler, filters, CommandHandler
import aiohttp
import asyncio
from aiohttp import web
import humanize
import traceback
from telegram.error import TelegramError
import re
from typing import Set, Dict, List, Tuple
import time
import aiofiles
from collections import deque

# Configure logging with more detail
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.DEBUG
)
logger = logging.getLogger(__name__)

# Bot Configuration
TOKEN = os.environ.get("BOT_TOKEN", "7895976352:AAHK5RhzlYzktFOyZazhS0rc4xHI9mt8ijQ")
PORT = int(os.environ.get("PORT", "8443"))
RENDER_EXTERNAL_URL = os.environ.get("RENDER_EXTERNAL_URL")

# File size configurations
MAX_FILE_SIZE = 500 * 1024 * 1024  # 500MB
CHUNK_SIZE = 1024 * 1024  # 1MB chunks for faster downloads
PROCESSING_TIMEOUT = 600  # 10 minutes for overall processing

# Create storage directory
STORAGE_DIR = "credentials_storage"
os.makedirs(STORAGE_DIR, exist_ok=True)

# Initialize bot application
application = Application.builder().token(TOKEN).build()

# Processing queue system
class ProcessingQueue:
    def __init__(self):
        self.queue = deque()
        self.currently_processing = None
        self.user_queues = {}  # Track files per user
        
    def add_file(self, chat_id: int, file_info: dict):
        """Add a file to the processing queue"""
        if chat_id not in self.user_queues:
            self.user_queues[chat_id] = []
        
        self.user_queues[chat_id].append(file_info)
        self.queue.append((chat_id, file_info))
        
    def get_next(self):
        """Get the next file to process"""
        if self.queue:
            return self.queue.popleft()
        return None
        
    def is_processing(self) -> bool:
        """Check if currently processing a file"""
        return self.currently_processing is not None
        
    def start_processing(self, chat_id: int, file_info: dict):
        """Mark a file as currently being processed"""
        self.currently_processing = (chat_id, file_info)
        
    def finish_processing(self):
        """Mark current processing as finished"""
        if self.currently_processing:
            chat_id, file_info = self.currently_processing
            if chat_id in self.user_queues:
                try:
                    self.user_queues[chat_id].remove(file_info)
                    if not self.user_queues[chat_id]:
                        del self.user_queues[chat_id]
                except ValueError:
                    pass
        self.currently_processing = None
        
    def get_queue_position(self, chat_id: int, file_name: str) -> int:
        """Get position of a file in the queue"""
        for i, (q_chat_id, file_info) in enumerate(self.queue):
            if q_chat_id == chat_id and file_info['name'] == file_name:
                return i + 1
        return -1
        
    def get_queue_status(self) -> dict:
        """Get overall queue status"""
        total_files = len(self.queue)
        users_waiting = len(self.user_queues)
        
        current_file = None
        if self.currently_processing:
            _, file_info = self.currently_processing
            current_file = file_info['name']
            
        return {
            'total_files': total_files,
            'users_waiting': users_waiting,
            'current_file': current_file,
            'is_processing': self.is_processing()
        }
        
    def get_user_files(self, chat_id: int) -> List[dict]:
        """Get files in queue for a specific user"""
        return self.user_queues.get(chat_id, [])
        
    def clear_user_files(self, chat_id: int):
        """Remove all files for a user from the queue"""
        if chat_id in self.user_queues:
            # Remove from main queue
            self.queue = deque([(c_id, f_info) for c_id, f_info in self.queue if c_id != chat_id])
            # Clear user queue
            del self.user_queues[chat_id]

# Initialize processing queue
processing_queue = ProcessingQueue()

def is_valid_email_domain(email: str) -> bool:
    """Check if the email domain is Gmail, Hotmail, or iCloud."""
    email = email.lower()
    valid_domains = [
        '@gmail.com',
        '@hotmail.com',
        '@hotmail.co.uk',
        '@hotmail.fr',
        '@hotmail.it',
        '@hotmail.es',
        '@hotmail.de',
        '@icloud.com'
    ]
    return any(email.endswith(domain) for domain in valid_domains)

def normalize_email(email: str) -> str:
    """Normalize email address for comparison."""
    email = email.lower()
    if '@gmail.com' in email:
        username, domain = email.split('@')
        username = username.replace('.', '')
        username = username.split('+')[0]
        return f"{username}@{domain}"
    return email

async def extract_and_save_credentials(text: str, output_file: str, processing_message=None) -> Tuple[List[str], str]:
    """Extract credentials and save them to a file."""
    email_pattern = r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}'
    password_pattern = r'^(?=.*[a-z])(?=.*[A-Z])(?=.*\d)[a-zA-Z0-9]{6,12}$'
    
    valid_credentials = []
    seen_emails = set()
    seen_passwords = set()
    
    try:
        if processing_message:
            await processing_message.edit_text("🔍 Scanning for emails and passwords...")
            
        emails = re.findall(email_pattern, text)
        words = re.findall(r'\b\w+\b', text)
        passwords = [w for w in words if re.match(password_pattern, w)]
        
        if processing_message:
            await processing_message.edit_text(
                f"📊 Found {len(emails)} emails and {len(passwords)} potential passwords\n"
                f"🔄 Matching credentials..."
            )
        
        for email in emails:
            if not is_valid_email_domain(email):
                continue
                
            norm_email = normalize_email(email)
            if norm_email in seen_emails:
                continue
            
            for password in passwords:
                if password not in seen_passwords:
                    valid_credentials.append(f"{email}:{password}")
                    seen_emails.add(norm_email)
                    seen_passwords.add(password)
                    break
        
        if valid_credentials:
            if processing_message:
                await processing_message.edit_text(f"💾 Saving {len(valid_credentials)} credentials...")
            async with aiofiles.open(output_file, 'w', encoding='utf-8') as f:
                await f.write("\n".join(valid_credentials))
        
        return valid_credentials, output_file
    except Exception as e:
        logger.error(f"Error in extract_credentials: {str(e)}")
        raise

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send a message when the command /start is issued."""
    user_name = update.message.from_user.first_name
    greeting_message = (
        f"👋 Hello {user_name}!\n\n"
        "Welcome to the Credential Extractor Bot! 🤖\n\n"
        "📄 Send me text files to extract credentials\n"
        "🔑 Supported formats: .txt, .csv, .log, .md\n\n"
        "Supported email domains:\n"
        "- Gmail (@gmail.com)\n"
        "- Hotmail (@hotmail.com, etc.)\n"
        "- iCloud (@icloud.com)\n\n"
        "Password requirements:\n"
        "- 6-12 characters\n"
        "- At least 1 uppercase, lowercase, and number\n"
        "- No special characters or spaces\n\n"
        "Commands:\n"
        "/queue - Check processing queue\n"
        "/cancel - Cancel your files in queue\n\n"
        "Send me a file to get started!"
    )
    await update.message.reply_text(greeting_message)

async def show_queue_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show current queue status."""
    chat_id = update.effective_chat.id
    status = processing_queue.get_queue_status()
    user_files = processing_queue.get_user_files(chat_id)
    
    message = "📊 Processing Queue Status\n\n"
    
    if status['is_processing']:
        message += f"🔄 Currently processing: {status['current_file']}\n\n"
    else:
        message += "💤 No file currently being processed\n\n"
    
    message += f"📋 Total files in queue: {status['total_files']}\n"
    message += f"👥 Users waiting: {status['users_waiting']}\n\n"
    
    if user_files:
        message += f"📁 Your files in queue ({len(user_files)}):\n"
        for i, file_info in enumerate(user_files, 1):
            position = processing_queue.get_queue_position(chat_id, file_info['name'])
            message += f"  {i}. {file_info['name']} (Position: {position})\n"
    else:
        message += "✅ You have no files in the queue\n"
    
    message += f"\nUse /cancel to remove your files from queue"
    
    await update.message.reply_text(message)

async def cancel_user_files(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancel all files for a user in the queue."""
    chat_id = update.effective_chat.id
    user_files = processing_queue.get_user_files(chat_id)
    
    if not user_files:
        await update.message.reply_text("ℹ️ You have no files in the processing queue.")
        return
    
    file_count = len(user_files)
    processing_queue.clear_user_files(chat_id)
    
    await update.message.reply_text(
        f"✅ Cancelled {file_count} file(s) from the processing queue.\n"
        f"You can send new files anytime!"
    )

def format_size(size_bytes):
    """Format bytes to human readable size."""
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size_bytes < 1024:
            return f"{size_bytes:.2f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.2f} TB"

async def download_file_with_progress(file_info, processing_message):
    """Download file with progress updates."""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(file_info.file_path) as response:
                if response.status != 200:
                    raise Exception(f"Download failed with status {response.status}")
                
                total_size = int(response.headers.get('content-length', 0))
                downloaded = 0
                content = b""
                start_time = time.time()
                
                async for chunk in response.content.iter_chunked(CHUNK_SIZE):
                    content += chunk
                    downloaded += len(chunk)
                    
                    # Update progress every MB or 2 seconds
                    if downloaded % CHUNK_SIZE == 0 or time.time() - start_time > 2:
                        progress = (downloaded / total_size * 100) if total_size > 0 else 0
                        try:
                            await processing_message.edit_text(
                                f"📥 Downloading... {progress:.1f}%\n"
                                f"📊 {format_size(downloaded)} / {format_size(total_size)}"
                            )
                        except Exception:
                            pass  # Ignore rate limit errors
                        start_time = time.time()
                
                return content
                
    except Exception as e:
        logger.error(f"Download error: {e}")
        raise

async def process_single_file():
    """Process the next file in the queue."""
    if processing_queue.is_processing():
        return
    
    next_item = processing_queue.get_next()
    if not next_item:
        return
    
    chat_id, file_info = next_item
    processing_queue.start_processing(chat_id, file_info)
    
    processing_message = None
    try:
        # Send initial processing message
        processing_message = await application.bot.send_message(
            chat_id,
            f"🔄 Processing your file: {file_info['name']}\n"
            f"📊 Initializing download..."
        )
        
        # Get file info from Telegram
        telegram_file = await application.bot.get_file(file_info['file_id'])
        
        # Download file with progress
        await processing_message.edit_text(
            f"📥 Downloading: {file_info['name']}\n"
            f"Please wait..."
        )
        
        content = await download_file_with_progress(telegram_file, processing_message)
        
        # Process content
        await processing_message.edit_text(
            f"🔍 Processing: {file_info['name']}\n"
            f"Extracting credentials..."
        )
        
        # Decode content
        text_content = content.decode('utf-8', errors='ignore')
        
        # Create output file
        output_file = os.path.join(STORAGE_DIR, f"credentials_{int(time.time())}.txt")
        
        # Extract credentials
        credentials, saved_file = await extract_and_save_credentials(
            text_content, output_file, processing_message
        )
        
        if credentials:
            # Send results
            await application.bot.send_document(
                chat_id,
                document=open(saved_file, 'rb'),
                filename=f"extracted_credentials_{len(credentials)}.txt",
                caption=(
                    f"✅ Processing Complete!\n"
                    f"📁 File: {file_info['name']}\n"
                    f"🔐 Found: {len(credentials)} credential pairs\n"
                    f"⏱️ Processing time: {time.time() - file_info['start_time']:.1f}s"
                )
            )
            
            # Update processing message
            await processing_message.edit_text(
                f"✅ Successfully processed: {file_info['name']}\n"
                f"🔐 Found {len(credentials)} credentials\n"
                f"📤 Results file sent above!"
            )
        else:
            await processing_message.edit_text(
                f"❌ No valid credentials found in: {file_info['name']}\n"
                f"Please try another file."
            )
        
        # Clean up output file
        try:
            if os.path.exists(saved_file):
                os.remove(saved_file)
        except Exception as e:
            logger.error(f"Error deleting file: {e}")
            
    except Exception as e:
        logger.error(f"Error processing file: {e}")
        if processing_message:
            await processing_message.edit_text(
                f"❌ Error processing: {file_info['name']}\n"
                f"Error: {str(e)}\n"
                f"Please try again."
            )
    finally:
        processing_queue.finish_processing()
        
        # Process next file if available
        asyncio.create_task(process_single_file())

def is_supported_file(filename: str) -> bool:
    """Check if file format is supported."""
    return filename.lower().endswith(('.txt', '.csv', '.log', '.md'))

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle incoming messages."""
    message = update.message
    chat_id = message.chat_id
    
    # Handle text messages
    if message.text and not message.text.startswith('/'):
        processing_message = await message.reply_text("🔍 Processing text message...")
        
        try:
            output_file = os.path.join(STORAGE_DIR, f"text_credentials_{int(time.time())}.txt")
            credentials, saved_file = await extract_and_save_credentials(
                message.text, output_file, processing_message
            )
            
            if credentials:
                await message.reply_document(
                    document=open(saved_file, 'rb'),
                    filename=f"text_credentials_{len(credentials)}.txt",
                    caption=f"✅ Found {len(credentials)} credential pairs from your text"
                )
                await processing_message.edit_text(
                    f"✅ Text processed successfully!\n"
                    f"🔐 Found {len(credentials)} credentials"
                )
            else:
                await processing_message.edit_text("❌ No valid credentials found in your text")
                
            # Clean up
            try:
                if os.path.exists(saved_file):
                    os.remove(saved_file)
            except Exception as e:
                logger.error(f"Error deleting file: {e}")
                
        except Exception as e:
            logger.error(f"Error processing text: {e}")
            await processing_message.edit_text(f"❌ Error processing text: {str(e)}")
        return
    
    # Handle file uploads
    if message.document:
        file = message.document
        
        # Check file format
        if not is_supported_file(file.file_name):
            await message.reply_text(
                f"❌ Unsupported file format: {file.file_name}\n\n"
                f"📝 Supported formats:\n"
                f"- .txt (Text files)\n"
                f"- .csv (CSV files)\n"
                f"- .log (Log files)\n"
                f"- .md (Markdown files)"
            )
            return
        
        # Check file size
        if file.file_size > MAX_FILE_SIZE:
            await message.reply_text(
                f"❌ File too large!\n"
                f"Maximum size: {format_size(MAX_FILE_SIZE)}\n"
                f"Your file: {format_size(file.file_size)}"
            )
            return
        
        # Add to queue
        file_info = {
            'file_id': file.file_id,
            'name': file.file_name,
            'size': file.file_size,
            'start_time': time.time()
        }
        
        processing_queue.add_file(chat_id, file_info)
        
        # Get queue position
        position = processing_queue.get_queue_position(chat_id, file.file_name)
        status = processing_queue.get_queue_status()
        
        if position == 1 and not status['is_processing']:
            await message.reply_text(
                f"✅ File added to queue: {file.file_name}\n"
                f"🔄 Processing will start immediately..."
            )
        else:
            await message.reply_text(
                f"✅ File added to queue: {file.file_name}\n"
                f"📊 Position in queue: {position}\n"
                f"⏳ Estimated wait time: ~{(position-1) * 30} seconds\n\n"
                f"Use /queue to check status or /cancel to remove from queue"
            )
        
        # Start processing if not already running
        asyncio.create_task(process_single_file())

async def webhook_handler(request):
    """Handle incoming webhook updates."""
    try:
        update = Update.de_json(await request.json(), application.bot)
        await application.process_update(update)
        return web.Response(status=200)
    except Exception as e:
        logger.error(f"Error processing update: {e}")
        return web.Response(status=500)

async def setup_webhook():
    """Setup the webhook for Render deployment."""
    try:
        if not RENDER_EXTERNAL_URL:
            raise ValueError("RENDER_EXTERNAL_URL environment variable is not set")
            
        webhook_url = f"{RENDER_EXTERNAL_URL}/{TOKEN}"
        await application.bot.set_webhook(webhook_url)
        logger.info(f"Webhook set to: {webhook_url}")
        
        return PORT
    except Exception as e:
        logger.error(f"Error setting up webhook: {e}")
        raise

async def main():
    """Main function to run the bot."""
    try:
        # Add handlers
        application.add_handler(CommandHandler("start", start))
        application.add_handler(CommandHandler("queue", show_queue_status))
        application.add_handler(CommandHandler("cancel", cancel_user_files))
        
        # Add message handler
        application.add_handler(MessageHandler(
            (filters.ATTACHMENT | filters.TEXT) & 
            ~filters.COMMAND, 
            handle_message
        ))
        
        # Initialize the application
        await application.initialize()
        
        # Setup webhook
        port = await setup_webhook()
        
        # Setup aiohttp web app
        app = web.Application()
        app.router.add_post(f"/{TOKEN}", webhook_handler)
        
        # Start the server
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, '0.0.0.0', port)
        
        print("=== Credential Extractor Bot Started ===")
        print(f"✅ Webhook configured on port {port}")
        print("🤖 Bot is ready to process files!")
        print("📝 Single file processing mode enabled")
        print("⚡ Queue system active")
        print("=====================================")
        
        await site.start()
        
        # Keep the bot running
        while True:
            await asyncio.sleep(3600)
            
    except Exception as e:
        logger.error(f"Error in main: {e}")
        raise

if __name__ == '__main__':
    asyncio.run(main())
