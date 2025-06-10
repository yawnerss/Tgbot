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
from typing import Set, Dict, List, Tuple, Optional
import time
import aiofiles
from collections import deque
import tempfile
from concurrent.futures import ThreadPoolExecutor
import weakref

# Configure logging with more detail
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO  # Changed to INFO for better performance
)
logger = logging.getLogger(__name__)

# Bot Configuration
TOKEN = os.environ.get("BOT_TOKEN", "7895976352:AAHK5RhzlYzktFOyZazhS0rc4xHI9mt8ijQ")
PORT = int(os.environ.get("PORT", "8443"))
RENDER_EXTERNAL_URL = os.environ.get("RENDER_EXTERNAL_URL")

# Enhanced file size configurations
MAX_FILE_SIZE = 500 * 1024 * 1024  # 500MB
CHUNK_SIZE = 2 * 1024 * 1024  # 2MB chunks for faster downloads
PROCESSING_TIMEOUT = 900  # 15 minutes for overall processing
MAX_CONCURRENT_DOWNLOADS = 3  # Process up to 3 files simultaneously
PROGRESS_UPDATE_INTERVAL = 1  # Update progress every 1 second

# Create storage directory
STORAGE_DIR = "credentials_storage"
os.makedirs(STORAGE_DIR, exist_ok=True)

# Initialize bot application
application = Application.builder().token(TOKEN).build()

# Thread pool for CPU-intensive tasks
thread_pool = ThreadPoolExecutor(max_workers=4)

# Enhanced Processing Queue System
class EnhancedProcessingQueue:
    def __init__(self):
        self.queue = deque()
        self.currently_processing = {}  # Support multiple concurrent processing
        self.user_queues = {}
        self.processing_lock = asyncio.Lock()
        self.max_concurrent = MAX_CONCURRENT_DOWNLOADS
        
    def add_file(self, chat_id: int, file_info: dict):
        """Add a file to the processing queue"""
        if chat_id not in self.user_queues:
            self.user_queues[chat_id] = []
        
        file_info['queue_time'] = time.time()
        self.user_queues[chat_id].append(file_info)
        self.queue.append((chat_id, file_info))
        
    def get_next(self) -> Optional[Tuple[int, dict]]:
        """Get the next file to process"""
        if self.queue and len(self.currently_processing) < self.max_concurrent:
            return self.queue.popleft()
        return None
        
    def can_process_more(self) -> bool:
        """Check if we can process more files"""
        return len(self.currently_processing) < self.max_concurrent
        
    def start_processing(self, chat_id: int, file_info: dict):
        """Mark a file as currently being processed"""
        process_id = f"{chat_id}_{file_info['file_id']}"
        self.currently_processing[process_id] = {
            'chat_id': chat_id,
            'file_info': file_info,
            'start_time': time.time()
        }
        
    def finish_processing(self, chat_id: int, file_info: dict):
        """Mark current processing as finished"""
        process_id = f"{chat_id}_{file_info['file_id']}"
        if process_id in self.currently_processing:
            del self.currently_processing[process_id]
            
        if chat_id in self.user_queues:
            try:
                self.user_queues[chat_id].remove(file_info)
                if not self.user_queues[chat_id]:
                    del self.user_queues[chat_id]
            except ValueError:
                pass
        
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
        currently_processing_count = len(self.currently_processing)
        
        processing_files = []
        for process_info in self.currently_processing.values():
            processing_files.append(process_info['file_info']['name'])
            
        return {
            'total_files': total_files,
            'users_waiting': users_waiting,
            'currently_processing_count': currently_processing_count,
            'processing_files': processing_files,
            'can_process_more': self.can_process_more()
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
            
        # Cancel currently processing files for this user
        to_remove = []
        for process_id, process_info in self.currently_processing.items():
            if process_info['chat_id'] == chat_id:
                to_remove.append(process_id)
        
        for process_id in to_remove:
            del self.currently_processing[process_id]

# Initialize enhanced processing queue
processing_queue = EnhancedProcessingQueue()

# Enhanced HTTP session with connection pooling
class HTTPSessionManager:
    def __init__(self):
        self._session = None
        self._connector = None
        
    async def get_session(self):
        if self._session is None or self._session.closed:
            # Configure connector for better performance
            self._connector = aiohttp.TCPConnector(
                limit=20,  # Total connection pool size
                limit_per_host=10,  # Connections per host
                ttl_dns_cache=300,  # DNS cache TTL
                use_dns_cache=True,
                keepalive_timeout=60,
                enable_cleanup_closed=True
            )
            
            # Configure timeout
            timeout = aiohttp.ClientTimeout(
                total=PROCESSING_TIMEOUT,
                connect=30,
                sock_read=60
            )
            
            self._session = aiohttp.ClientSession(
                connector=self._connector,
                timeout=timeout,
                headers={'User-Agent': 'TelegramBot/1.0'}
            )
        
        return self._session
        
    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()
        if self._connector:
            await self._connector.close()

# Global session manager
session_manager = HTTPSessionManager()

def is_valid_email_domain(email: str) -> bool:
    """Check if the email domain is valid."""
    email = email.lower()
    valid_domains = [
        '@yandex.ru', '@rambler.ru', '@mail.ru',
        '@gmail.com', '@hotmail.com', '@hotmail.co.uk',
        '@hotmail.fr', '@hotmail.it', '@hotmail.es',
        '@hotmail.de', '@icloud.com', '@outlook.com',
        '@live.com', '@yahoo.com', '@protonmail.com'
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

async def extract_and_save_credentials_optimized(text: str, output_file: str, processing_message=None) -> Tuple[List[str], str]:
    """Optimized credential extraction with better performance."""
    
    def extract_credentials_sync(text_content):
        """Synchronous credential extraction for thread pool"""
        email_pattern = r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}'
        password_pattern = r'^(?=.*[a-z])(?=.*[A-Z])(?=.*\d)[a-zA-Z0-9@#$%^&*!]{6,20}$'
        
        valid_credentials = []
        seen_emails = set()
        seen_passwords = set()
        
        # Extract emails and passwords
        emails = re.findall(email_pattern, text_content)
        words = re.findall(r'\b\w+\b', text_content)
        passwords = [w for w in words if re.match(password_pattern, w)]
        
        # Match credentials
        for email in emails:
            if not is_valid_email_domain(email):
                continue
                
            norm_email = normalize_email(email)
            if norm_email in seen_emails:
                continue
            
            for password in passwords:
                if password not in seen_passwords and len(password) >= 6:
                    valid_credentials.append(f"{email}:{password}")
                    seen_emails.add(norm_email)
                    seen_passwords.add(password)
                    break
        
        return valid_credentials, len(emails), len(passwords)
    
    try:
        if processing_message:
            await processing_message.edit_text("🔍 Analyzing file content...")
        
        # Run extraction in thread pool for better performance
        loop = asyncio.get_event_loop()
        valid_credentials, email_count, password_count = await loop.run_in_executor(
            thread_pool, extract_credentials_sync, text
        )
        
        if processing_message:
            await processing_message.edit_text(
                f"📊 Analysis complete!\n"
                f"📧 Found {email_count} emails\n"
                f"🔐 Found {password_count} passwords\n"
                f"✅ Matched {len(valid_credentials)} credential pairs"
            )
        
        if valid_credentials:
            async with aiofiles.open(output_file, 'w', encoding='utf-8') as f:
                await f.write("\n".join(valid_credentials))
        
        return valid_credentials, output_file
        
    except Exception as e:
        logger.error(f"Error in extract_credentials_optimized: {str(e)}")
        raise

def format_size(size_bytes):
    """Format bytes to human readable size."""
    if size_bytes == 0:
        return "0 B"
    
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} TB"

def calculate_speed(bytes_downloaded, elapsed_time):
    """Calculate download speed."""
    if elapsed_time <= 0:
        return "0 B/s"
    
    speed = bytes_downloaded / elapsed_time
    return f"{format_size(speed)}/s"

async def download_file_with_enhanced_progress(file_info, processing_message):
    """Enhanced file download with better progress tracking and speed."""
    session = await session_manager.get_session()
    
    try:
        async with session.get(file_info.file_path) as response:
            if response.status != 200:
                raise Exception(f"Download failed with status {response.status}")
            
            total_size = int(response.headers.get('content-length', 0))
            downloaded = 0
            start_time = time.time()
            last_update = start_time
            
            # Use temporary file for large downloads
            with tempfile.NamedTemporaryFile(delete=False) as temp_file:
                temp_path = temp_file.name
                
                async for chunk in response.content.iter_chunked(CHUNK_SIZE):
                    temp_file.write(chunk)
                    downloaded += len(chunk)
                    current_time = time.time()
                    
                    # Update progress more frequently for better UX
                    if current_time - last_update >= PROGRESS_UPDATE_INTERVAL:
                        progress = (downloaded / total_size * 100) if total_size > 0 else 0
                        elapsed = current_time - start_time
                        speed = calculate_speed(downloaded, elapsed)
                        eta = ((total_size - downloaded) / (downloaded / elapsed)) if downloaded > 0 and elapsed > 0 else 0
                        
                        progress_bar = "█" * int(progress // 5) + "░" * (20 - int(progress // 5))
                        
                        try:
                            await processing_message.edit_text(
                                f"📥 Downloading: {file_info['name']}\n"
                                f"[{progress_bar}] {progress:.1f}%\n"
                                f"📊 {format_size(downloaded)} / {format_size(total_size)}\n"
                                f"⚡ Speed: {speed}\n"
                                f"⏱️ ETA: {int(eta)}s" if eta > 0 else f"⏱️ ETA: calculating..."
                            )
                        except Exception:
                            pass  # Ignore rate limit errors
                        
                        last_update = current_time
                
                # Read the complete file
                temp_file.seek(0)
                content = temp_file.read()
                
            # Clean up temp file
            try:
                os.unlink(temp_path)
            except Exception:
                pass
                
            return content
                
    except Exception as e:
        logger.error(f"Enhanced download error: {e}")
        raise

async def process_single_file():
    """Enhanced single file processing with better error handling."""
    async with processing_queue.processing_lock:
        if not processing_queue.can_process_more():
            return
        
        next_item = processing_queue.get_next()
        if not next_item:
            return
    
    chat_id, file_info = next_item
    processing_queue.start_processing(chat_id, file_info)
    
    processing_message = None
    temp_files = []
    
    try:
        # Send initial processing message
        queue_time = time.time() - file_info.get('queue_time', time.time())
        processing_message = await application.bot.send_message(
            chat_id,
            f"🚀 Processing started: {file_info['name']}\n"
            f"📊 File size: {format_size(file_info['size'])}\n"
            f"⏱️ Queue time: {queue_time:.1f}s\n"
            f"🔄 Initializing enhanced download..."
        )
        
        # Get file info from Telegram
        telegram_file = await application.bot.get_file(file_info['file_id'])
        
        # Enhanced download with progress
        await processing_message.edit_text(
            f"📥 Starting download: {file_info['name']}\n"
            f"📊 Size: {format_size(file_info['size'])}\n"
            f"⚡ Using optimized download..."
        )
        
        download_start = time.time()
        content = await download_file_with_enhanced_progress(telegram_file, processing_message)
        download_time = time.time() - download_start
        
        # Process content
        await processing_message.edit_text(
            f"✅ Download complete: {file_info['name']}\n"
            f"⏱️ Download time: {download_time:.1f}s\n"
            f"⚡ Speed: {calculate_speed(len(content), download_time)}\n"
            f"🔍 Processing content..."
        )
        
        # Decode content efficiently
        try:
            text_content = content.decode('utf-8')
        except UnicodeDecodeError:
            try:
                text_content = content.decode('latin-1')
            except UnicodeDecodeError:
                text_content = content.decode('utf-8', errors='ignore')
        
        # Create output file
        output_file = os.path.join(STORAGE_DIR, f"credentials_{int(time.time())}_{chat_id}.txt")
        temp_files.append(output_file)
        
        # Extract credentials with optimization
        processing_start = time.time()
        credentials, saved_file = await extract_and_save_credentials_optimized(
            text_content, output_file, processing_message
        )
        processing_time = time.time() - processing_start
        total_time = time.time() - file_info.get('queue_time', time.time())
        
        if credentials:
            # Send results with enhanced stats
            with open(saved_file, 'rb') as f:
                await application.bot.send_document(
                    chat_id,
                    document=f,
                    filename=f"extracted_credentials_{len(credentials)}.txt",
                    caption=(
                        f"✅ Processing Complete!\n"
                        f"📁 File: {file_info['name']}\n"
                        f"🔐 Found: {len(credentials)} credential pairs\n"
                        f"📊 File size: {format_size(file_info['size'])}\n"
                        f"⏱️ Download: {download_time:.1f}s\n"
                        f"🔍 Processing: {processing_time:.1f}s\n"
                        f"⚡ Total time: {total_time:.1f}s\n"
                        f"🚀 Avg speed: {calculate_speed(file_info['size'], total_time)}"
                    )
                )
            
            # Update processing message
            await processing_message.edit_text(
                f"🎉 Successfully processed: {file_info['name']}\n"
                f"🔐 Found {len(credentials)} credentials\n"
                f"⚡ Total time: {total_time:.1f}s\n"
                f"📤 Results sent above!"
            )
        else:
            await processing_message.edit_text(
                f"❌ No valid credentials found in: {file_info['name']}\n"
                f"⏱️ Processing time: {total_time:.1f}s\n"
                f"💡 Try a different file format or check content."
            )
            
    except Exception as e:
        logger.error(f"Error processing file {file_info.get('name', 'unknown')}: {e}")
        if processing_message:
            try:
                await processing_message.edit_text(
                    f"❌ Error processing: {file_info.get('name', 'unknown')}\n"
                    f"Error: {str(e)[:100]}...\n"
                    f"🔄 Please try again or contact support."
                )
            except Exception:
                pass
    finally:
        # Clean up
        processing_queue.finish_processing(chat_id, file_info)
        
        # Clean up temp files
        for temp_file in temp_files:
            try:
                if os.path.exists(temp_file):
                    os.remove(temp_file)
            except Exception as e:
                logger.error(f"Error deleting temp file: {e}")
        
        # Process next files if available
        for _ in range(MAX_CONCURRENT_DOWNLOADS):
            asyncio.create_task(process_single_file())

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Enhanced start command with better information."""
    user_name = update.message.from_user.first_name
    greeting_message = (
        f"👋 Hello {user_name}!\n\n"
        "<b>🚀 Enhanced Credential Extractor Bot v2.0</b> 🤖\n\n"
        "<b>✨ New Features:</b>\n"
        f"📊 Up to {MAX_FILE_SIZE // (1024*1024)}MB file support\n"
        f"⚡ {MAX_CONCURRENT_DOWNLOADS}x faster concurrent processing\n"
        f"📈 Real-time progress tracking\n"
        f"🔄 Enhanced queue system\n\n"
        "<b>📄 Supported formats:</b> .txt, .csv, .log, .md\n\n"
        "<b>🌐 Supported email domains:</b>\n"
        "• Gmail, Hotmail, Outlook, Live\n"
        "• Yahoo, iCloud, ProtonMail\n"
        "• Yandex, Mail.ru, Rambler\n\n"
        "<b>🔐 Password requirements:</b>\n"
        "• 6-20 characters\n"
        "• Mixed case + numbers\n"
        "• Special characters allowed\n\n"
        "<b>🎮 Commands:</b>\n"
        "/queue - Check processing status\n"
        "/cancel - Cancel your files\n"
        "/stats - View performance stats\n\n"
        "📤 Send me a file to get started!"
    )
    await update.message.reply_text(greeting_message, parse_mode='HTML')

async def show_enhanced_queue_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Enhanced queue status with more details."""
    chat_id = update.effective_chat.id
    status = processing_queue.get_queue_status()
    user_files = processing_queue.get_user_files(chat_id)
    
    message = "📊 <b>Enhanced Processing Queue Status</b>\n\n"
    
    if status['currently_processing_count'] > 0:
        message += f"🔄 <b>Currently processing:</b> {status['currently_processing_count']} files\n"
        for i, file_name in enumerate(status['processing_files'][:3], 1):
            message += f"  {i}. {file_name[:30]}...\n"
        if len(status['processing_files']) > 3:
            message += f"  ... and {len(status['processing_files']) - 3} more\n"
        message += "\n"
    else:
        message += "💤 No files currently being processed\n\n"
    
    message += f"📋 <b>Queue stats:</b>\n"
    message += f"• Files waiting: {status['total_files']}\n"
    message += f"• Users in queue: {status['users_waiting']}\n"
    message += f"• Processing slots: {status['currently_processing_count']}/{MAX_CONCURRENT_DOWNLOADS}\n\n"
    
    if user_files:
        message += f"📁 <b>Your files ({len(user_files)}):</b>\n"
        for i, file_info in enumerate(user_files[:5], 1):
            position = processing_queue.get_queue_position(chat_id, file_info['name'])
            wait_time = time.time() - file_info.get('queue_time', time.time())
            message += f"  {i}. {file_info['name'][:25]}... (#{position}, {wait_time:.0f}s)\n"
        if len(user_files) > 5:
            message += f"  ... and {len(user_files) - 5} more files\n"
    else:
        message += "✅ You have no files in the queue\n"
    
    message += f"\n💡 Use /cancel to remove your files"
    
    await update.message.reply_text(message, parse_mode='HTML')

async def cancel_user_files(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Enhanced cancel command."""
    chat_id = update.effective_chat.id
    user_files = processing_queue.get_user_files(chat_id)
    
    if not user_files:
        await update.message.reply_text("ℹ️ You have no files in the processing queue.")
        return
    
    file_count = len(user_files)
    processing_queue.clear_user_files(chat_id)
    
    await update.message.reply_text(
        f"✅ <b>Cancelled {file_count} file(s)</b>\n"
        f"🔄 Processing slots freed up\n"
        f"📤 You can send new files anytime!",
        parse_mode='HTML'
    )

def is_supported_file(filename: str) -> bool:
    """Check if file format is supported."""
    supported_extensions = ('.txt', '.csv', '.log', '.md', '.json', '.xml')
    return filename.lower().endswith(supported_extensions)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Enhanced message handler with better file processing."""
    message = update.message
    chat_id = message.chat_id
    
    # Handle text messages
    if message.text and not message.text.startswith('/'):
        processing_message = await message.reply_text("🔍 Processing text message...")
        
        try:
            output_file = os.path.join(STORAGE_DIR, f"text_credentials_{int(time.time())}_{chat_id}.txt")
            credentials, saved_file = await extract_and_save_credentials_optimized(
                message.text, output_file, processing_message
            )
            
            if credentials:
                with open(saved_file, 'rb') as f:
                    await message.reply_document(
                        document=f,
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
        await message.reply_text(
            f"❌ <b>Unsupported file format:</b> {file.file_name}\n\n"
            f"📝 <b>Supported formats:</b>\n"
            f"• .txt (Text files)\n"
            f"• .csv (CSV files)\n"
            f"• .log (Log files)\n"
            f"• .md (Markdown files)\n"
            f"• .json (JSON files)\n"
            f"• .xml (XML files)",
            parse_mode='HTML'
        )
        
        # Check file size
        if file.file_size > MAX_FILE_SIZE:
            await message.reply_text(
                f"❌ <b>File too large!</b>\n"
                f"📊 Maximum size: {format_size(MAX_FILE_SIZE)}\n"
                f"📁 Your file: {format_size(file.file_size)}\n\n"
                f"💡 Try splitting the file into smaller parts.",
                parse_mode='HTML'
            )
        
        # Add to queue
        file_info = {
            'file_id': file.file_id,
            'name': file.file_name,
            'size': file.file_size,
            'start_time': time.time()
        }
        
        processing_queue.add_file(chat_id, file_info)
        
        # Get queue position and status
        position = processing_queue.get_queue_position(chat_id, file.file_name)
        status = processing_queue.get_queue_status()
        
        if position <= MAX_CONCURRENT_DOWNLOADS and status['can_process_more']:
            await message.reply_text(
                f"✅ <b>File queued:</b> {file.file_name}\n"
                f"📊 Size: {format_size(file.file_size)}\n"
                f"🚀 Processing will start immediately...\n"
                f"⚡ Enhanced speed mode active!",
                parse_mode='HTML'
            )
        else:
            estimated_wait = max(0, (position - MAX_CONCURRENT_DOWNLOADS) * 45)  # 45s per file estimate
            await message.reply_text(
                f"✅ <b>File added to queue:</b> {file.file_name}\n"
                f"📊 Size: {format_size(file.file_size)}\n"
                f"📍 Position: #{position}\n"
                f"⏳ Estimated wait: ~{estimated_wait}s\n"
                f"🔄 {status['currently_processing_count']}/{MAX_CONCURRENT_DOWNLOADS} slots active\n\n"
                f"💡 Use /queue for status or /cancel to remove",
                parse_mode='HTML'
            )
        
        # Start processing (multiple files can be processed concurrently)
        for _ in range(MAX_CONCURRENT_DOWNLOADS):
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

async def cleanup_on_shutdown():
    """Cleanup resources on shutdown."""
    try:
        await session_manager.close()
        thread_pool.shutdown(wait=True)
        logger.info("Cleanup completed")
    except Exception as e:
        logger.error(f"Error during cleanup: {e}")

async def main():
    """Enhanced main function with better resource management."""
    try:
        # Add handlers
        application.add_handler(CommandHandler("start", start))
        application.add_handler(CommandHandler("queue", show_enhanced_queue_status))
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
        
        print("=== Enhanced Credential Extractor Bot v2.0 ===")
        print(f"✅ Webhook configured on port {port}")
        print(f"🚀 Enhanced processing: {MAX_CONCURRENT_DOWNLOADS} concurrent files")
        print(f"📊 Max file size: {format_size(MAX_FILE_SIZE)}")
        print(f"⚡ Chunk size: {format_size(CHUNK_SIZE)}")
        print(f"🔄 Queue system: Active with progress tracking")
        print(f"🧵 Thread pool: {thread_pool._max_workers} workers")
        print("🤖 Bot is ready for high-performance processing!")
        print("=" * 50)
        
        await site.start()
        
        # Keep the bot running
        try:
            while True:
                await asyncio.sleep(3600)
        except KeyboardInterrupt:
            logger.info("Received shutdown signal")
        finally:
            await cleanup_on_shutdown()
            
    except Exception as e:
        logger.error(f"Error in main: {e}")
        await cleanup_on_shutdown()
        raise

if __name__ == '__main__':
    asyncio.run(main())
