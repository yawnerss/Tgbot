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
LARGE_FILE_THRESHOLD = 50 * 1024 * 1024  # 50MB threshold
CONCURRENT_CHUNKS = 4  # Number of concurrent chunk downloads
PROCESSING_TIMEOUT = 600  # 10 minutes for overall processing

# Create storage directory
STORAGE_DIR = "credentials_storage"
os.makedirs(STORAGE_DIR, exist_ok=True)

# Dictionary to track bulk processing state
bulk_processing = {}

# Dictionary to track processing status
processing_status = {}

# Add conversion mode tracking
conversion_mode = {}

# Add file processing tracking
processing_files = {}

# Initialize bot application
application = Application.builder().token(TOKEN).build()

# Add enhanced processing tracking
class ProcessingTracker:
    def __init__(self):
        self.processing_files = {}
        self.active_tasks = {}
        self.stop_requested = {}
    
    def is_processing(self, file_id: str) -> bool:
        return file_id in self.processing_files
        
    def start_processing(self, file_id: str, chat_id: int):
        self.processing_files[file_id] = True
        if chat_id not in self.active_tasks:
            self.active_tasks[chat_id] = set()
        self.active_tasks[chat_id].add(file_id)
        self.stop_requested[chat_id] = False
        
    def stop_processing(self, chat_id: int):
        self.stop_requested[chat_id] = True
        
    def should_stop(self, chat_id: int) -> bool:
        return self.stop_requested.get(chat_id, False)
        
    def finish_processing(self, file_id: str, chat_id: int):
        if file_id in self.processing_files:
            del self.processing_files[file_id]
        if chat_id in self.active_tasks:
            self.active_tasks[chat_id].discard(file_id)
            if not self.active_tasks[chat_id]:
                del self.active_tasks[chat_id]
                if chat_id in self.stop_requested:
                    del self.stop_requested[chat_id]
                    
    def get_active_tasks(self, chat_id: int) -> int:
        return len(self.active_tasks.get(chat_id, set()))

# Initialize processing tracker
processing_tracker = ProcessingTracker()

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
    # Convert to lowercase
    email = email.lower()
    # Handle Gmail dots and plus addressing
    if '@gmail.com' in email:
        username, domain = email.split('@')
        # Remove dots from username
        username = username.replace('.', '')
        # Remove everything after + in username
        username = username.split('+')[0]
        return f"{username}@{domain}"
    return email

async def extract_and_save_credentials(text: str, output_file: str, processing_message = None) -> Tuple[List[str], str]:
    """Extract credentials and save them to a file."""
    # Email pattern - modified to be more strict about domains
    email_pattern = r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}'
    
    # Password pattern (6-12 chars, at least 1 uppercase, 1 lowercase, 1 number, no special chars or spaces)
    password_pattern = r'^(?=.*[a-z])(?=.*[A-Z])(?=.*\d)[a-zA-Z0-9]{6,12}$'
    
    valid_credentials = []
    seen_emails = set()
    seen_passwords = set()
    total_emails = 0
    valid_emails = 0
    total_passwords = 0
    valid_passwords = 0
    
    try:
        # Find all emails
        if processing_message:
            await processing_message.edit_text("🔍 Scanning for emails...")
        emails = re.findall(email_pattern, text)
        total_emails = len(emails)
        
        # Find potential passwords
        if processing_message:
            await processing_message.edit_text("🔑 Scanning for passwords...")
        words = re.findall(r'\b\w+\b', text)
        passwords = [w for w in words if re.match(password_pattern, w)]
        total_passwords = len(passwords)
        
        if processing_message:
            await processing_message.edit_text(
                f"📊 Found:\n"
                f"📧 {total_emails} potential emails\n"
                f"🔑 {total_passwords} potential passwords\n\n"
                f"🔄 Starting credential matching..."
            )
        
        # Process in smaller batches to manage memory
        batch_size = 1000
        for i in range(0, len(emails), batch_size):
            email_batch = emails[i:i + batch_size]
            
            for email in email_batch:
                if not is_valid_email_domain(email):
                    continue
                    
                norm_email = normalize_email(email)
                if norm_email in seen_emails:
                    continue
                
                valid_emails += 1
                for password in passwords:
                    if password not in seen_passwords:
                        valid_credentials.append(f"{email}:{password}")
                        seen_emails.add(norm_email)
                        seen_passwords.add(password)
                        valid_passwords += 1
                        break
            
            # Update progress every batch
            if processing_message and i + batch_size < len(emails):
                progress = ((i + batch_size) / len(emails)) * 100
                await processing_message.edit_text(
                    f"🔄 Matching Progress: {progress:.1f}%\n\n"
                    f"📊 Current Stats:\n"
                    f"📧 Valid Emails: {valid_emails}/{total_emails}\n"
                    f"🔑 Used Passwords: {valid_passwords}/{total_passwords}\n"
                    f"✅ Matches Found: {len(valid_credentials)}"
                )
        
        # Save credentials to file
        if valid_credentials:
            if processing_message:
                await processing_message.edit_text(
                    f"💾 Saving credentials...\n\n"
                    f"📊 Final Stats:\n"
                    f"📧 Valid Emails: {valid_emails}/{total_emails}\n"
                    f"🔑 Used Passwords: {valid_passwords}/{total_passwords}\n"
                    f"✅ Total Matches: {len(valid_credentials)}"
                )
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
        "Welcome to the File Downloader Bot! 🤖\n\n"
        "I can help you:\n"
        "📄 Process files and extract credentials\n"
        "🔑 Extract credentials from text\n"
        "📝 Process text and links\n\n"
        "Supported email domains:\n"
        "- Gmail (@gmail.com)\n"
        "- Hotmail (@hotmail.com, etc.)\n"
        "- iCloud (@icloud.com)\n\n"
        "Password requirements:\n"
        "- 6-12 characters\n"
        "- At least 1 uppercase letter\n"
        "- At least 1 lowercase letter\n"
        "- At least 1 number\n"
        "- No special characters\n"
        "- No spaces\n\n"
        "Send me any file or text to process!"
    )
    await update.message.reply_text(greeting_message)

async def process_text_message(message, context):
    """Process a text message."""
    processing_message = await message.reply_text("🔍 Processing text...")
    
    try:
        async with asyncio.timeout(PROCESSING_TIMEOUT):
            # Create unique output file for this message
            output_file = os.path.join(STORAGE_DIR, f"credentials_{int(time.time())}.txt")
            
            # Process text and extract credentials
            credentials, saved_file = await extract_and_save_credentials(message.text, output_file, processing_message)
            
            if credentials:
                try:
                    # Send only the file, not the credentials in chat
                    await message.reply_document(
                        document=open(saved_file, 'rb'),
                        filename=os.path.basename(saved_file),
                        caption=f"✅ Found {len(credentials)} credential pairs"
                    )
                    
                    # Update processing message
                    await processing_message.edit_text(
                        f"✅ Successfully processed:\n"
                        f"📧 Found {len(credentials)} credential pairs\n"
                        f"💾 File sent successfully"
                    )
                finally:
                    # Delete the file after sending
                    try:
                        os.remove(saved_file)
                    except Exception as e:
                        logger.error(f"Error deleting file {saved_file}: {e}")
            else:
                await processing_message.edit_text("❌ No valid credentials found")
                
    except TimeoutError:
        await processing_message.edit_text("⏱️ Processing timed out. Please try with a smaller file or text.")
    except Exception as e:
        logger.error(f"Error processing message: {str(e)}")
        await processing_message.edit_text(f"❌ Error: {str(e)}")

def format_size(size_bytes):
    """Format bytes to human readable size."""
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size_bytes < 1024:
            return f"{size_bytes:.2f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.2f} TB"

async def update_progress(message, current, total, start_time):
    """Update progress message with download status."""
    now = time.time()
    elapsed = now - start_time
    speed = current / elapsed if elapsed > 0 else 0
    progress = (current / total) * 100 if total > 0 else 0
    
    # Create progress bar
    bar_length = 20
    filled_length = int(bar_length * progress / 100)
    bar = '█' * filled_length + '░' * (bar_length - filled_length)
    
    status_text = (
        f"📥 Downloading...\n"
        f"Progress: {progress:.1f}%\n"
        f"[{bar}]\n"
        f"Speed: {format_size(speed)}/s\n"
        f"Downloaded: {format_size(current)} / {format_size(total)}"
    )
    
    try:
        await message.edit_text(status_text)
    except Exception:
        pass  # Ignore errors from too many updates

async def download_chunk(session, url, start, end, chunk_number):
    """Download a specific chunk of the file."""
    headers = {'Range': f'bytes={start}-{end}'}
    async with session.get(url, headers=headers) as response:
        return await response.read(), chunk_number

async def download_file_in_chunks(session, url, file_size, processing_message, start_time):
    """Download file in parallel chunks for better performance."""
    chunk_size = file_size // CONCURRENT_CHUNKS
    chunks = []
    tasks = []
    
    for i in range(CONCURRENT_CHUNKS):
        start = i * chunk_size
        end = start + chunk_size - 1 if i < CONCURRENT_CHUNKS - 1 else file_size - 1
        task = asyncio.create_task(download_chunk(session, url, start, end, i))
        tasks.append(task)
    
    total_downloaded = 0
    last_update = 0
    
    # Process chunks as they complete
    for completed_task in asyncio.as_completed(tasks):
        chunk_data, chunk_number = await completed_task
        chunks.append((chunk_number, chunk_data))
        total_downloaded += len(chunk_data)
        
        # Update progress
        now = time.time()
        if now - last_update >= 0.5:
            try:
                await update_progress(processing_message, total_downloaded, file_size, start_time)
                last_update = now
            except Exception as e:
                logger.error(f"Error updating progress: {e}")
    
    # Sort chunks by their number and combine
    chunks.sort(key=lambda x: x[0])
    return b''.join(chunk[1] for chunk in chunks)

async def process_file_message(message, context):
    """Process a file message."""
    file = None
    file_size = 0
    chat_id = message.chat_id
    
    try:
        # Check for different types of files
        if message.document:
            file = message.document
            file_size = message.document.file_size
            
            # Check if file is already being processed
            file_id = file.file_id
            if processing_tracker.is_processing(file_id):
                await message.reply_text(
                    "⏳ This file is already being processed.\n"
                    "Use /stop to cancel current processing."
                )
                return
                
            # Mark file as being processed
            processing_tracker.start_processing(file_id, chat_id)
            
        elif message.photo:
            file = message.photo[-1]
            file_size = file.file_size
        elif message.video:
            file = message.video
            file_size = message.video.file_size
        elif message.audio:
            file = message.audio
            file_size = message.audio.file_size
        
        try:
            if not file:
                await message.reply_text("❌ Please send a file or text message!")
                return

            # Get file details
            file_id = file.file_id
            file_name = getattr(file, 'file_name', f"file_{file.file_unique_id}")
            
            # Check file size
            if file_size > MAX_FILE_SIZE:
                await message.reply_text(
                    f"❌ File too large! Maximum size is {format_size(MAX_FILE_SIZE)}\n"
                    f"Current file size: {format_size(file_size)}"
                )
                return
                
            processing_message = await message.reply_text(
                f"🔍 Initializing download...\n"
                f"📦 File size: {format_size(file_size)}\n"
                f"⚡ Using optimized parallel download"
            )
            start_time = time.time()

            try:
                async with asyncio.timeout(PROCESSING_TIMEOUT):
                    # Check for stop request before starting download
                    if processing_tracker.should_stop(chat_id):
                        await processing_message.edit_text("⚠️ Processing cancelled by user.")
                        return
                        
                    file_info = await context.bot.get_file(file_id)
                    
                    async with aiohttp.ClientSession() as session:
                        # Use parallel downloading for large files
                        if file_size > LARGE_FILE_THRESHOLD:
                            content = await download_file_in_chunks(
                                session, 
                                file_info.file_path, 
                                file_size,
                                processing_message,
                                start_time
                            )
                        else:
                            # Use simple download for smaller files
                            async with session.get(file_info.file_path) as response:
                                if response.status != 200:
                                    await processing_message.edit_text(f"❌ Download failed with status {response.status}")
                                    return
                                content = await response.read()
                        
                        # Check for stop request after download
                        if processing_tracker.should_stop(chat_id):
                            await processing_message.edit_text("⚠️ Processing cancelled by user.")
                            return
                            
                        await processing_message.edit_text(
                            "📥 Download complete!\n"
                            f"⏱️ Time taken: {time.time() - start_time:.1f}s\n"
                            "🔍 Processing file..."
                        )
                        
                        if file_name.endswith(('.txt', '.csv', '.log', '.md')):
                            try:
                                text_content = content.decode('utf-8', errors='ignore')
                                content = None  # Free memory
                                
                                # Check for stop request before processing
                                if processing_tracker.should_stop(chat_id):
                                    await processing_message.edit_text("⚠️ Processing cancelled by user.")
                                    return
                                
                                # Create unique output file
                                output_file = os.path.join(STORAGE_DIR, f"credentials_{int(time.time())}.txt")
                                
                                # Extract credentials without intermediate progress messages
                                credentials, saved_file = await extract_and_save_credentials(text_content, output_file)
                                text_content = None  # Free memory
                                
                                # Final stop check before sending results
                                if processing_tracker.should_stop(chat_id):
                                    await processing_message.edit_text("⚠️ Processing cancelled by user.")
                                    if os.path.exists(saved_file):
                                        os.remove(saved_file)
                                    return
                                
                                if credentials:
                                    try:
                                        # Send results file with complete information
                                        await message.reply_document(
                                            document=open(saved_file, 'rb'),
                                            filename=f"extracted_credentials_{len(credentials)}.txt",
                                            caption=(
                                                f"✅ Processing Complete!\n"
                                                f"📊 Found {len(credentials)} unique credentials\n"
                                                f"⏱️ Total time: {time.time() - start_time:.1f}s\n"
                                                f"📦 Original size: {format_size(file_size)}\n"
                                                f"📁 From: {file_name}"
                                            )
                                        )
                                        
                                        # Delete the processing status message
                                        try:
                                            await processing_message.delete()
                                        except Exception:
                                            pass
                                        
                                    except Exception as e:
                                        logger.error(f"Error sending file: {e}")
                                        await processing_message.edit_text(f"❌ Error sending file: {str(e)}")
                                    finally:
                                        # Delete the temporary file
                                        try:
                                            if os.path.exists(saved_file):
                                                os.remove(saved_file)
                                        except Exception as e:
                                            logger.error(f"Error deleting file {saved_file}: {e}")
                                else:
                                    await processing_message.edit_text(
                                        "❌ No valid credentials found\n"
                                        f"⏱️ Time taken: {time.time() - start_time:.1f}s"
                                    )
                                    
                            except Exception as e:
                                logger.error(f"Error processing text content: {e}")
                                await processing_message.edit_text(f"❌ Error processing text content: {str(e)}")
                        else:
                            await processing_message.edit_text(
                                "❌ Not a text file. Please send text files only."
                            )

            except asyncio.TimeoutError:
                await processing_message.edit_text(
                    "⏱️ Processing timed out.\n"
                    "Please try with a smaller file."
                )
            except Exception as e:
                logger.error(f"Error processing file: {str(e)}")
                await processing_message.edit_text(
                    f"❌ Error processing file:\n"
                    f"Error: {str(e)}\n"
                    "Please try again."
                )
        finally:
            # Remove file from processing tracking
            if file and file.file_id:
                processing_tracker.finish_processing(file.file_id, chat_id)
                
    except Exception as e:
        logger.error(f"Unexpected error: {str(e)}")
        await message.reply_text(f"❌ An unexpected error occurred: {str(e)}")

async def stop_processing(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Force stop all processing for the chat."""
    chat_id = update.effective_chat.id
    
    # Stop all processing for this chat
    processing_tracker.stop_processing(chat_id)
    
    active_tasks = processing_tracker.get_active_tasks(chat_id)
    if active_tasks > 0:
        msg = await update.message.reply_text(
            f"⚠️ Force stopping {active_tasks} active processing tasks...\n"
            "Please wait while current operations complete safely."
        )
        
        # Wait for up to 5 seconds for tasks to clean up
        for _ in range(10):
            if processing_tracker.get_active_tasks(chat_id) == 0:
                await msg.edit_text("✅ All processing tasks stopped successfully!")
                return
            await asyncio.sleep(0.5)
            
        await msg.edit_text(
            "⚠️ Some tasks are taking longer to stop.\n"
            "They will be terminated as soon as possible."
        )
    else:
        await update.message.reply_text("ℹ️ No active processing tasks to stop.")
    
    # Reset any ongoing modes
    if chat_id in conversion_mode:
        conversion_mode[chat_id]['active'] = False
    if chat_id in bulk_processing:
        bulk_processing[chat_id]['active'] = False
    if chat_id in processing_status:
        processing_status[chat_id]['stop_requested'] = True

async def start_bulk(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start bulk processing mode."""
    chat_info = await get_chat_info(update)
    chat_id, chat_type, message = chat_info
    if not chat_id:
        return
        
    # For channels, verify admin status
    if chat_type == 'channel':
        if not message.author_signature:  # If no signature, we can't verify admin
            await message.reply_text("❌ Channel posts must be signed by an admin.")
            return
    
    # Initialize bulk processing state
    bulk_processing[chat_id] = {
        'active': True,
        'files': [],
        'pending_files': [],
        'processing': False,
        'credentials': set(),
        'start_time': time.time(),
        'message': await message.reply_text(
            f"🔄 Bulk processing mode activated!\n\n"
            f"Send me multiple files to process. All credentials will be combined into one file.\n"
            f"When you're done, send /done to finish processing.\n"
            f"You can use /stop to halt processing.\n\n"
            f"Chat Type: {chat_type.capitalize()}"
        )
    }
    
    # Initialize processing status
    processing_status[chat_id] = {
        'stop_requested': False
    }

async def show_loading_status(message, current_step, total_steps, description):
    """Show a loading bar with current progress."""
    bar_length = 20
    filled_length = int(bar_length * current_step / total_steps)
    bar = '█' * filled_length + '░' * (bar_length - filled_length)
    loading_text = (
        f"⏳ {description}\n"
        f"[{bar}] {int(current_step/total_steps*100)}%"
    )
    await message.edit_text(loading_text)

async def auto_finish_bulk(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Automatically finish bulk processing and send results."""
    chat_info = await get_chat_info(update)
    chat_id, chat_type, message = chat_info
    if not chat_id:
        return
        
    # For channels, verify admin status
    if chat_type == 'channel':
        if not message.author_signature:
            await message.reply_text("❌ Only channel admins can finish processing.")
            return
    
    try:
        if chat_id not in bulk_processing:
            await message.reply_text("❌ No active bulk processing session. Start one with /bulk")
            return
            
        state = bulk_processing[chat_id]
        if not state['active']:
            await message.reply_text("❌ Bulk processing session is not active. Start one with /bulk")
            return
        
        # Create final output file
        output_file = os.path.join(STORAGE_DIR, f"bulk_credentials_{int(time.time())}.txt")
        
        try:
            # Save all unique credentials
            async with aiofiles.open(output_file, 'w', encoding='utf-8') as f:
                await f.write("\n".join(sorted(state['credentials'])))
            
            # Send completion message first
            completion_msg = await state['message'].reply_text(
                f"✅ Bulk Processing Complete!\n"
                f"📊 Found {len(state['credentials'])} unique credential pairs\n"
                f"📁 Processed {len(state['files'])} files\n"
                f"⏱️ Total time: {int(time.time() - state['start_time'])}s\n\n"
                f"📤 Sending results file..."
            )
            
            # Send the file
            await state['message'].reply_document(
                document=open(output_file, 'rb'),
                filename=f"credentials_{len(state['credentials'])}_{int(time.time())}.txt",
                caption=(
                    f"✅ Results File\n"
                    f"📊 {len(state['credentials'])} unique credentials\n"
                    f"📁 From {len(state['files'])} files"
                )
            )
            
            # Update completion message
            await completion_msg.edit_text(
                f"✅ Bulk Processing Complete!\n\n"
                f"📊 Summary:\n"
                f"📁 Processed files: {len(state['files'])}\n"
                f"🔐 Total unique credentials: {len(state['credentials'])}\n"
                f"⏱️ Processing time: {(time.time() - state['start_time']):.1f} seconds\n\n"
                f"✨ Results file has been sent above."
            )
            
        except Exception as e:
            logger.error(f"Error in auto_finish_bulk: {str(e)}")
            await state['message'].reply_text(
                f"❌ Error sending results: {str(e)}\n"
                f"Use /done to try again."
            )
        finally:
            # Cleanup
            try:
                if os.path.exists(output_file):
                    os.remove(output_file)
            except Exception as e:
                logger.error(f"Error deleting output file: {e}")
            
    except Exception as e:
        logger.error(f"Error in auto_finish_bulk: {str(e)}")
    finally:
        # Safely cleanup states
        if chat_id in bulk_processing:
            bulk_processing[chat_id]['active'] = False
            bulk_processing[chat_id]['processing'] = False
            bulk_processing[chat_id]['pending_files'] = []
        if chat_id in processing_status:
            processing_status[chat_id]['stop_requested'] = False

async def show_bulk_status(chat_id: int, context: ContextTypes.DEFAULT_TYPE):
    """Show current bulk processing status."""
    if chat_id not in bulk_processing:
        return
        
    state = bulk_processing[chat_id]
    current_file = "None"
    if state['processing'] and state['pending_files']:
        current_file = state['pending_files'][0]['name']
        
    # Calculate processing speed
    elapsed_time = time.time() - state['start_time']
    files_per_minute = len(state['files']) / (elapsed_time / 60) if elapsed_time > 0 else 0
    
    # Create progress bar for queue
    total_files = len(state['files']) + len(state['pending_files'])
    if total_files > 0:
        progress = len(state['files']) / total_files
        bar_length = 20
        filled_length = int(bar_length * progress)
        bar = '█' * filled_length + '░' * (bar_length - filled_length)
    else:
        bar = '░' * 20
        progress = 0
    
    status_message = (
        f"📊 Bulk Processing Status\n\n"
        f"Progress: [{bar}] {progress*100:.1f}%\n"
        f"🔄 Active: {'Yes' if state['active'] else 'No'}\n"
        f"📂 Current File: {current_file}\n"
        f"📋 Files in Queue: {len(state['pending_files'])}\n"
        f"✅ Processed Files: {len(state['files'])}\n"
        f"🔐 Unique Credentials: {len(state['credentials'])}\n"
        f"⚡ Speed: {files_per_minute:.1f} files/minute\n"
        f"⏱️ Running Time: {int(elapsed_time)}s\n\n"
        f"Commands:\n"
        f"/bulk - Start new bulk session\n"
        f"/stop - Stop processing\n"
        f"/done - Finish and get results"
    )
    
    try:
        await state['message'].edit_text(status_message)
    except Exception as e:
        logger.error(f"Error updating status message: {e}")

async def process_bulk_file(message, context, file_content: str) -> int:
    """Process a single file in bulk mode and return credential count."""
    chat_id = message.chat_id
    state = bulk_processing[chat_id]
    
    # Check if processing should stop
    if processing_status[chat_id]['stop_requested']:
        await state['message'].edit_text("⛔ Processing stopped by user request.")
        return 0
    
    # Create temporary output file
    temp_file = os.path.join(STORAGE_DIR, f"temp_{int(time.time())}.txt")
    
    try:
        # Extract credentials
        credentials, _ = await extract_and_save_credentials(file_content, temp_file, state['message'])
        
        # Add new credentials to the set
        if credentials:
            original_count = len(state['credentials'])
            state['credentials'].update(credentials)
            new_unique = len(state['credentials']) - original_count
            
            # Update status with duplicate information
            await state['message'].edit_text(
                f"📊 File Results:\n"
                f"✨ New unique credentials: {new_unique}\n"
                f"🔄 Duplicates skipped: {len(credentials) - new_unique}\n"
                f"📈 Total unique so far: {len(state['credentials'])}\n"
                f"📋 Remaining files: {len(state['pending_files'])}"
            )
        
        return len(credentials)
    finally:
        # Cleanup temporary file
        try:
            if os.path.exists(temp_file):
                os.remove(temp_file)
        except Exception as e:
            logger.error(f"Error deleting temporary file: {e}")

async def process_pending_files(chat_id: int, context: ContextTypes.DEFAULT_TYPE):
    """Process pending files sequentially."""
    if chat_id not in bulk_processing:
        return
        
    state = bulk_processing[chat_id]
    
    if state['processing'] or not state['active']:
        return
    
    state['processing'] = True
    
    try:
        while state['pending_files'] and state['active'] and not processing_status.get(chat_id, {}).get('stop_requested', False):
            try:
                # Safely get the first file info
                if not state['pending_files']:  # Double check in case list became empty
                    break
                    
                file_info = state['pending_files'][0]  # Peek at first file without removing
                
                # Create loading animation for current file
                loading_chars = ['⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧', '⠇', '⠏']
                loading_idx = 0
                
                # Show initial status
                await state['message'].edit_text(
                    f"{loading_chars[loading_idx]} Processing: {file_info['name']}\n"
                    f"Please wait..."
                )
                
                file_obj = await context.bot.get_file(file_info['file_id'])
                async with aiohttp.ClientSession() as session:
                    async with session.get(file_obj.file_path) as response:
                        if response.status != 200:
                            raise Exception("Download failed")
                        content = await response.text()
                        
                        # Update loading animation while processing
                        loading_idx = (loading_idx + 1) % len(loading_chars)
                        await state['message'].edit_text(
                            f"{loading_chars[loading_idx]} Processing: {file_info['name']}\n"
                            f"Extracting credentials..."
                        )
                        
                        await process_bulk_file(file_info['message'], context, content)
                        state['files'].append(file_info['name'])
                        
                        # Safely remove the processed file
                        if state['pending_files']:  # Check again before removing
                            state['pending_files'].pop(0)  # Remove only after successful processing
                
            except IndexError:
                logger.error("Attempted to process empty pending files list")
                break
            except Exception as e:
                logger.error(f"Error processing file {file_info['name'] if 'file_info' in locals() else 'unknown'}: {e}")
                # Safely remove failed file
                if state['pending_files']:  # Check before removing
                    state['pending_files'].pop(0)
                await state['message'].edit_text(
                    f"❌ Error processing file: {str(e)}\n"
                    f"Continuing with next file..."
                )
                await asyncio.sleep(2)  # Show error message briefly
            
            # Update status after each file
            await show_bulk_status(chat_id, context)
            
            # Small delay between files
            await asyncio.sleep(1)
        
        # Final status update and auto-finish if complete
        if state['active']:
            await show_bulk_status(chat_id, context)
            
            # If all files are processed and we have credentials, automatically finish
            if not state['pending_files'] and len(state['files']) > 0 and len(state['credentials']) > 0:
                await auto_finish_bulk(message, context)
            
    finally:
        state['processing'] = False

async def get_chat_info(update: Update) -> tuple:
    """Get chat ID and type from update."""
    try:
        if update.channel_post:
            return update.channel_post.chat_id, 'channel', update.channel_post
        elif update.message:
            chat_type = update.message.chat.type
            return update.message.chat_id, chat_type, update.message
        return None, None, None
    except Exception as e:
        logger.error(f"Error getting chat info: {str(e)}")
        return None, None, None

async def is_admin(chat_id: int, user_id: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Check if user is an admin in the channel."""
    try:
        chat_member = await context.bot.get_chat_member(chat_id, user_id)
        return chat_member.status in ['creator', 'administrator']
    except Exception:
        return False

async def start_conversion(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start file conversion mode."""
    chat_info = await get_chat_info(update)
    chat_id, chat_type, message = chat_info
    if not chat_id:
        return
    
    # Initialize conversion mode for any chat type
    conversion_mode[chat_id] = {
        'active': True,
        'start_time': time.time(),
        'chat_type': chat_type
    }
    
    await message.reply_text(
        f"✨ File Conversion Mode Activated in {chat_type} chat!\n\n"
        "📤 Send files to extract credentials.\n"
        "📝 Supported formats: .txt, .csv, .log, .md\n\n"
        "❌ Send /stop to exit conversion mode"
    )

def is_password_file(filename):
    """Check if the file is a potential password file."""
    # List of common password file patterns
    patterns = [
        r'pass(?:word)?s?\.txt$',  # password.txt, passwords.txt
        r'Pass(?:word)?s?\.txt$',  # Password.txt, Passwords.txt
        r'all[_\s-]*pass(?:word)?s?\.txt$',
        r'pass(?:word)?s?[_\s-]*all\.txt$',
        r'(?:combo|combolist)[_\s-]*all\.txt$',
        r'credentials?[_\s-]*all\.txt$',
        r'full[_\s-]*pass(?:word)?s?\.txt$',
        r'pass(?:word)?s?[_\s-]*dump\.txt$',
        r'dump[_\s-]*pass(?:word)?s?\.txt$',
        r'.*\.txt$'  # Include all .txt files as potential sources
    ]
    
    filename_lower = filename.lower()
    return any(re.match(pattern, filename_lower) for pattern in patterns)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle incoming messages from all chat types."""
    chat_info = await get_chat_info(update)
    chat_id, chat_type, message = chat_info
    if not chat_id:
        return

    # Check if in conversion mode first (for all chat types)
    if chat_id in conversion_mode and conversion_mode[chat_id]['active']:
        if message.document:
            file = message.document
            # Check if it's a password file or supported format
            if not (file.file_name.lower().endswith(('.txt', '.csv', '.log', '.md')) or 
                   is_password_file(file.file_name)):
                error_msg = await message.reply_text(
                    "❌ Unsupported file format!\n\n"
                    "📝 Supported formats:\n"
                    "- .txt (Text files)\n"
                    "- .csv (CSV files)\n"
                    "- .log (Log files)\n"
                    "- .md (Markdown files)\n"
                    "- password.txt/Passwords.txt"
                )
                return
                
            # Show processing message with chat context
            sender_name = ""
            if message.from_user:
                sender_name = f" (by {message.from_user.first_name})"
            elif message.author_signature:
                sender_name = f" (by {message.author_signature})"
            
            status_msg = await message.reply_text(
                f"🔄 Processing file in {chat_type} chat{sender_name}...\n"
                f"📁 File: {file.file_name}\n"
                "⏳ Please wait while I extract credentials."
            )
            
            try:
                file_info = await context.bot.get_file(file.file_id)
                async with aiohttp.ClientSession() as session:
                    async with session.get(file_info.file_path) as response:
                        if response.status != 200:
                            raise Exception("Download failed")
                        content = await response.text()
                        
                        # Create temporary output file
                        output_file = os.path.join(STORAGE_DIR, f"cv_credentials_{int(time.time())}.txt")
                        
                        # Extract credentials
                        credentials, saved_file = await extract_and_save_credentials(content, output_file, status_msg)
                        
                        if credentials:
                            # Send the results file
                            await message.reply_document(
                                document=open(saved_file, 'rb'),
                                filename=f"extracted_credentials_{len(credentials)}.txt",
                                caption=(
                                    f"✅ Conversion Complete!\n"
                                    f"💬 Chat Type: {chat_type}\n"
                                    f"📊 Found {len(credentials)} credential pairs\n"
                                    f"📁 Original file: {file.file_name}\n"
                                    f"⏱️ Time: {int(time.time() - conversion_mode[chat_id]['start_time'])}s"
                                )
                            )
                            
                            # Update status message
                            await status_msg.edit_text(
                                f"✅ File processed successfully!\n\n"
                                f"📊 Results:\n"
                                f"🔐 Credentials found: {len(credentials)}\n"
                                f"📁 Results file sent above.\n"
                                f"📤 Send another file or use /stop to exit conversion mode."
                            )
                        else:
                            await status_msg.edit_text(
                                f"❌ No valid credentials found in file: {file.file_name}\n"
                                f"📤 Send another file or use /stop to exit conversion mode."
                            )
                            
                        # Cleanup
                        try:
                            os.remove(saved_file)
                        except Exception as e:
                            logger.error(f"Error deleting file: {e}")
                            
            except Exception as e:
                logger.error(f"Error processing file: {e}")
                await status_msg.edit_text(
                    f"❌ Error processing file: {str(e)}\n"
                    f"📤 Please try again or use /stop to exit conversion mode."
                )
            return
                
        elif message.text and not message.text.startswith('/'):
            await message.reply_text(
                "📤 Please send a file to convert.\n"
                "❓ Use /stop to exit conversion mode."
            )
            return

    # Process files directly (when not in conversion mode)
    if message.document:
        file = message.document
        # Check if it's a password file or supported format
        if not (file.file_name.lower().endswith(('.txt', '.csv', '.log', '.md')) or 
               is_password_file(file.file_name)):
            await message.reply_text(
                "❌ Unsupported file format!\n\n"
                "📝 Supported formats:\n"
                "- .txt (Text files)\n"
                "- .csv (CSV files)\n"
                "- .log (Log files)\n"
                "- .md (Markdown files)\n"
                "- password.txt/Passwords.txt"
            )
            return
            
        # Get sender info based on chat type
        sender_name = ""
        if message.from_user:
            sender_name = f" (by {message.from_user.first_name})"
        elif message.author_signature:
            sender_name = f" (by {message.author_signature})"
        
        # Show processing message
        status_msg = await message.reply_text(
            f"🔄 Processing file in {chat_type} chat{sender_name}...\n"
            "⏳ Please wait while I extract credentials."
        )
        
        try:
            # Download and process the file
            file_info = await context.bot.get_file(file.file_id)
            async with aiohttp.ClientSession() as session:
                async with session.get(file_info.file_path) as response:
                    if response.status != 200:
                        raise Exception("Download failed")
                    content = await response.text()
                    
                    # Create temporary output file
                    output_file = os.path.join(STORAGE_DIR, f"chat_credentials_{int(time.time())}.txt")
                    
                    # Extract credentials
                    credentials, saved_file = await extract_and_save_credentials(content, output_file, status_msg)
                    
                    if credentials:
                        # Send the results file
                        await message.reply_document(
                            document=open(saved_file, 'rb'),
                            filename=f"extracted_credentials_{len(credentials)}.txt",
                            caption=(
                                f"✅ File Processed!\n"
                                f"💬 Chat Type: {chat_type}\n"
                                f"📊 Found {len(credentials)} unique credentials\n"
                                f"📁 Original file: {file.file_name}"
                            )
                        )
                        
                        # Update status message
                        await status_msg.edit_text(
                            f"✅ File processed successfully!\n\n"
                            f"📊 Results:\n"
                            f"🔐 Credentials found: {len(credentials)}\n"
                            f"📁 Results file sent above."
                        )
                    else:
                        await status_msg.edit_text(
                            f"❌ No valid credentials found in file: {file.file_name}"
                        )
                        
                    # Cleanup
                    try:
                        os.remove(saved_file)
                    except Exception as e:
                        logger.error(f"Error deleting file: {e}")
                        
        except Exception as e:
            logger.error(f"Error processing file: {e}")
            await status_msg.edit_text(f"❌ Error processing file: {str(e)}")
        return

    # Check if in bulk processing mode
    elif chat_id in bulk_processing and bulk_processing[chat_id]['active']:
        if message.text and not message.text.startswith('/'):
            state = bulk_processing[chat_id]
            if not state['processing']:
                loading_msg = await message.reply_text("⏳ Processing text message...")
                await process_bulk_file(message, context, message.text)
                state['files'].append('text message')
                await loading_msg.edit_text("✅ Text message processed!")
                await show_bulk_status(chat_id, context)
            else:
                await message.reply_text(
                    "⏳ Please wait for current file to finish processing...\n"
                    "Use /stop to halt processing or /done to finish."
                )
                
        elif message.document:
            file = message.document
            if not file.file_name.endswith(('.txt', '.csv', '.log', '.md')):
                await message.reply_text("❌ Please send only text files.")
                return
            
            state = bulk_processing[chat_id]
            state['pending_files'].append({
                'file_id': file.file_id,
                'name': file.file_name,
                'message': message
            })
            
            loading_msg = await message.reply_text(
                f"📋 File queued: {file.file_name}\n"
                f"⏳ Position in queue: {len(state['pending_files'])}\n"
                f"Please wait..."
            )
            
            await show_bulk_status(chat_id, context)
            
            asyncio.create_task(process_pending_files(chat_id, context))
    
    # Clean up processing message for channel posts
    if chat_type == 'channel' and 'processing_msg' in locals():
        try:
            await processing_msg.delete()
        except Exception:
            pass

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
        application.add_handler(CommandHandler("bulk", start_bulk))
        application.add_handler(CommandHandler("done", auto_finish_bulk))
        application.add_handler(CommandHandler("stop", stop_processing))
        application.add_handler(CommandHandler("cv", start_conversion))
        
        # Add handler for both private messages and channel posts
        application.add_handler(MessageHandler(
            (filters.ATTACHMENT | filters.PHOTO | filters.TEXT) & 
            (filters.ChatType.PRIVATE | filters.ChatType.CHANNEL) & 
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
        
        print("=== Bot is starting ===")
        print(f"1. Webhook has been set up on port {port}")
        print("2. Go to Telegram and start chatting with the bot!")
        print("3. Send /start command to begin")
        print("4. Send /bulk to start bulk processing")
        print("======================")
        
        await site.start()
        
        # Keep the bot running
        while True:
            await asyncio.sleep(3600)
            
    except Exception as e:
        logger.error(f"Error in main: {e}")
        raise

if __name__ == '__main__':
    asyncio.run(main()) 
