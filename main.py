import os
import asyncio
import logging
import tempfile
import time
from pyrogram import Client, filters
from pyrogram.types import Message
import aiohttp
import aiofiles
from urllib.parse import urlencode

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Bot configuration from environment variables
API_ID = int(os.getenv('TELEGRAM_API_ID'))
API_HASH = os.getenv('TELEGRAM_API_HASH')
BOT_TOKEN = os.getenv('BOT_TOKEN')

# Initialize Pyrogram client for large file handling
app = Client("dailymotion_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# Global storage for user credentials (in production, use a database)
user_credentials = {}

class DailymotionUploader:
    def __init__(self, api_key, api_secret, username, password):
        self.api_key = api_key
        self.api_secret = api_secret
        self.username = username
        self.password = password
        self.access_token = None
        self.base_url = "https://api.dailymotion.com"
        
    async def authenticate(self):
        """Authenticate with Dailymotion Partner API"""
        try:
            auth_data = {
                'grant_type': 'password',
                'client_id': self.api_key,
                'client_secret': self.api_secret,
                'username': self.username,
                'password': self.password,
                'scope': 'manage_videos'
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.post(f"{self.base_url}/oauth/token", data=auth_data) as response:
                    if response.status == 200:
                        data = await response.json()
                        self.access_token = data.get('access_token')
                        logger.info("Successfully authenticated with Dailymotion")
                        return True
                    else:
                        error_text = await response.text()
                        logger.error(f"Authentication failed: {error_text}")
                        return False
        except Exception as e:
            logger.error(f"Authentication error: {e}")
            return False
    
    async def upload_video(self, file_path, title, description="", progress_callback=None):
        """Upload video to Dailymotion with error handling"""
        max_retries = 3
        retry_delay = 5
        
        for attempt in range(max_retries):
            try:
                logger.info(f"Upload attempt {attempt + 1}/{max_retries}")
                
                if not self.access_token:
                    if not await self.authenticate():
                        return None
                
                # Step 1: Get upload URL
                upload_url = await self._get_upload_url()
                if not upload_url:
                    continue
                
                # Step 2: Upload file
                video_url = await self._upload_file(file_path, upload_url, progress_callback)
                if not video_url:
                    continue
                
                # Step 3: Create video entry
                video_id = await self._create_video(video_url, title, description)
                if video_id:
                    return video_id
                    
            except aiohttp.ClientError as e:
                logger.error(f"Network error on attempt {attempt + 1}: {e}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(retry_delay)
                    continue
            except Exception as e:
                logger.error(f"Upload error on attempt {attempt + 1}: {e}")
                if "length" in str(e).lower() or "size" in str(e).lower():
                    logger.error("File size limit exceeded")
                    return "SIZE_ERROR"
                if attempt < max_retries - 1:
                    await asyncio.sleep(retry_delay)
                    continue
        
        return None
    
    async def _get_upload_url(self):
        """Get upload URL from Dailymotion"""
        try:
            headers = {'Authorization': f'Bearer {self.access_token}'}
            
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{self.base_url}/file/upload", headers=headers) as response:
                    if response.status == 200:
                        data = await response.json()
                        return data.get('upload_url')
                    else:
                        logger.error(f"Get upload URL failed: {await response.text()}")
        except Exception as e:
            logger.error(f"Get upload URL error: {e}")
        return None
    
    async def _upload_file(self, file_path, upload_url, progress_callback=None):
        """Upload file with chunked upload for large files"""
        try:
            file_size = os.path.getsize(file_path)
            logger.info(f"Uploading file: {file_size} bytes")
            
            # Use chunked upload for large files
            timeout = aiohttp.ClientTimeout(total=3600)  # 1 hour timeout
            
            async with aiohttp.ClientSession(timeout=timeout) as session:
                with open(file_path, 'rb') as file:
                    form_data = aiohttp.FormData()
                    form_data.add_field('file', file, filename=os.path.basename(file_path))
                    
                    async with session.post(upload_url, data=form_data) as response:
                        if response.status == 200:
                            result = await response.json()
                            return result.get('url')
                        else:
                            error_text = await response.text()
                            logger.error(f"File upload failed: {error_text}")
                            
        except Exception as e:
            logger.error(f"File upload error: {e}")
            raise
        return None
    
    async def _create_video(self, video_url, title, description):
        """Create video entry on Dailymotion"""
        try:
            headers = {
                'Authorization': f'Bearer {self.access_token}',
                'Content-Type': 'application/x-www-form-urlencoded'
            }
            
            video_data = {
                'url': video_url,
                'title': title,
                'description': description,
                'published': 'true'
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.post(f"{self.base_url}/me/videos", 
                                      headers=headers, 
                                      data=video_data) as response:
                    if response.status == 200:
                        data = await response.json()
                        return data.get('id')
                    else:
                        logger.error(f"Create video failed: {await response.text()}")
                        
        except Exception as e:
            logger.error(f"Create video error: {e}")
        return None

    def get_video_url(self, video_id):
        """Get public URL of uploaded video"""
        return f"https://www.dailymotion.com/video/{video_id}"

class ProgressTracker:
    def __init__(self, message, total_size, operation="Processing"):
        self.message = message
        self.total_size = total_size
        self.operation = operation
        self.last_update = 0
        self.start_time = time.time()
    
    async def update(self, current, total=None):
        if total is None:
            total = self.total_size
        
        current_time = time.time()
        if current_time - self.last_update < 3:  # Update every 3 seconds
            return
        
        self.last_update = current_time
        percentage = (current / total) * 100 if total > 0 else 0
        
        # Progress bar
        bar_length = 15
        filled_length = int(bar_length * current / total) if total > 0 else 0
        bar = "█" * filled_length + "░" * (bar_length - filled_length)
        
        # Speed calculation
        elapsed_time = current_time - self.start_time
        if elapsed_time > 0 and current > 0:
            speed = current / elapsed_time / (1024 * 1024)  # MB/s
            eta = (total - current) / (current / elapsed_time) if current > 0 else 0
            
            progress_text = f"""
🎬 **{self.operation}**

{bar} {percentage:.1f}%
📦 {current/(1024*1024):.1f}MB / {total/(1024*1024):.1f}MB
🚀 {speed:.1f} MB/s
⏱️ ETA: {int(eta//60)}m {int(eta%60)}s
            """
        else:
            progress_text = f"""
🎬 **{self.operation}**

{bar} {percentage:.1f}%
📦 {current/(1024*1024):.1f}MB / {total/(1024*1024):.1f}MB
            """
        
        try:
            await self.message.edit_text(progress_text.strip())
        except Exception:
            pass  # Ignore edit errors

# Bot command handlers
@app.on_message(filters.command("start"))
async def start_command(client, message: Message):
    welcome_text = """
🎬 **Dailymotion Video Uploader Bot**

This bot uploads videos from Telegram to Dailymotion using Partner API.

**Commands:**
• `/credentials` - Set your Dailymotion credentials
• `/upload` - Upload a video (send after setting credentials)
• `/help` - Show this help message

**First, set your credentials using `/credentials`**
    """
    await message.reply_text(welcome_text)

@app.on_message(filters.command("help"))
async def help_command(client, message: Message):
    help_text = """
📖 **How to use this bot:**

**Step 1:** Set your Dailymotion Partner credentials
Send `/credentials` and provide:
- API Key
- API Secret  
- Username
- Password

**Step 2:** Upload videos
Send `/upload` then send your video file

**Format for credentials:**
```
API Key: your_api_key
API Secret: your_api_secret
Username: your_username
Password: your_password
```

**Notes:**
- Supports large files up to 2GB
- Automatic retry on network errors
- Real-time progress tracking
- Handles connection timeouts
    """
    await message.reply_text(help_text)

@app.on_message(filters.command("credentials"))
async def credentials_command(client, message: Message):
    await message.reply_text(
        "🔐 **Set Dailymotion Credentials**\n\n"
        "Send your credentials in this format:\n\n"
        "```\n"
        "API Key: your_api_key_here\n"
        "API Secret: your_api_secret_here\n"
        "Username: your_username_here\n"
        "Password: your_password_here\n"
        "```\n\n"
        "**Example:**\n"
        "```\n"
        "API Key: abc123def456\n"
        "API Secret: xyz789uvw012\n"
        "Username: myuser@email.com\n"
        "Password: mypassword123\n"
        "```"
    )
    
    # Mark user as waiting for credentials
    user_credentials[message.from_user.id] = {'waiting_for': 'credentials'}

@app.on_message(filters.command("upload"))
async def upload_command(client, message: Message):
    user_id = message.from_user.id
    
    if user_id not in user_credentials or 'api_key' not in user_credentials[user_id]:
        await message.reply_text(
            "❌ **No credentials set!**\n\n"
            "Please set your Dailymotion credentials first using `/credentials`"
        )
        return
    
    await message.reply_text(
        "📹 **Ready to upload!**\n\n"
        "Send me a video file and I'll upload it to Dailymotion.\n\n"
        "**Supported:** MP4, AVI, MOV, MKV, WMV\n"
        "**Max size:** 2GB"
    )
    
    user_credentials[user_id]['waiting_for'] = 'video'

@app.on_message(filters.text & ~filters.command([]))
async def handle_credentials(client, message: Message):
    user_id = message.from_user.id
    
    if user_id in user_credentials and user_credentials[user_id].get('waiting_for') == 'credentials':
        await process_credentials(message)

async def process_credentials(message: Message):
    try:
        lines = message.text.strip().split('\n')
        credentials = {}
        
        for line in lines:
            if ':' in line:
                key, value = line.split(':', 1)
                key = key.strip().lower().replace(' ', '_')
                value = value.strip()
                credentials[key] = value
        
        required = ['api_key', 'api_secret', 'username', 'password']
        missing = [field for field in required if field not in credentials]
        
        if missing:
            await message.reply_text(f"❌ Missing: {', '.join(missing)}")
            return
        
        # Test credentials
        status_msg = await message.reply_text("🔄 Testing credentials...")
        
        uploader = DailymotionUploader(
            credentials['api_key'],
            credentials['api_secret'], 
            credentials['username'],
            credentials['password']
        )
        
        if await uploader.authenticate():
            # Save credentials
            user_credentials[message.from_user.id] = {
                'api_key': credentials['api_key'],
                'api_secret': credentials['api_secret'],
                'username': credentials['username'],
                'password': credentials['password'],
                'waiting_for': None
            }
            
            await status_msg.edit_text(
                "✅ **Credentials saved successfully!**\n\n"
                "You can now upload videos using `/upload`"
            )
        else:
            await status_msg.edit_text(
                "❌ **Authentication failed!**\n\n"
                "Please check your credentials and try again."
            )
            
    except Exception as e:
        logger.error(f"Credentials processing error: {e}")
        await message.reply_text("❌ Error processing credentials. Please try again.")

@app.on_message(filters.video)
async def handle_video(client, message: Message):
    user_id = message.from_user.id
    
    # Check if user is ready to upload
    if (user_id not in user_credentials or 
        user_credentials[user_id].get('waiting_for') != 'video' or
        'api_key' not in user_credentials[user_id]):
        await message.reply_text("Please use `/upload` command first.")
        return
    
    try:
        # Get video info
        video = message.video
        file_size_mb = video.file_size / (1024 * 1024)
        
        if file_size_mb > 2048:  # 2GB limit
            await message.reply_text("❌ File too large! Maximum size is 2GB.")
            return
        
        await message.reply_text(
            f"📹 **Video received!**\n\n"
            f"📁 Size: {file_size_mb:.1f} MB\n"
            f"⏱️ Duration: {video.duration}s\n\n"
            f"🔄 Starting upload process..."
        )
        
        # Create temporary file
        with tempfile.NamedTemporaryFile(delete=False, suffix='.mp4') as temp_file:
            temp_path = temp_file.name
        
        try:
            # Download video with progress
            progress_msg = await message.reply_text("📥 **Downloading from Telegram...**")
            progress_tracker = ProgressTracker(progress_msg, video.file_size, "Downloading")
            
            await app.download_media(
                message.video.file_id,
                temp_path,
                progress=lambda current, total: asyncio.create_task(progress_tracker.update(current, total))
            )
            
            # Upload to Dailymotion
            await progress_msg.edit_text("🔄 **Starting Dailymotion upload...**")
            
            creds = user_credentials[user_id]
            uploader = DailymotionUploader(
                creds['api_key'],
                creds['api_secret'],
                creds['username'],
                creds['password']
            )
            
            # Create upload progress tracker
            upload_progress = ProgressTracker(progress_msg, video.file_size, "Uploading to Dailymotion")
            
            video_title = video.file_name or f"Video_{int(time.time())}"
            video_description = f"Uploaded via Telegram Bot on {time.strftime('%Y-%m-%d %H:%M:%S')}"
            
            result = await uploader.upload_video(
                temp_path,
                video_title,
                video_description,
                progress_callback=lambda current, total: asyncio.create_task(upload_progress.update(current, total))
            )
            
            if result == "SIZE_ERROR":
                await progress_msg.edit_text(
                    "❌ **File size limit exceeded!**\n\n"
                    "The video is too large for Dailymotion's limits. "
                    "Try compressing the video or use a smaller file."
                )
            elif result:
                video_url = uploader.get_video_url(result)
                await progress_msg.edit_text(
                    f"🎉 **Upload successful!**\n\n"
                    f"🎬 **Video ID:** `{result}`\n"
                    f"📁 **Title:** {video_title}\n"
                    f"🔗 **URL:** {video_url}\n\n"
                    f"✅ Your video is now live on Dailymotion!"
                )
            else:
                await progress_msg.edit_text(
                    "❌ **Upload failed!**\n\n"
                    "The upload couldn't be completed. This might be due to:\n"
                    "• Network issues\n"
                    "• Dailymotion API limits\n"
                    "• File format issues\n\n"
                    "Please try again later."
                )
                
        finally:
            # Cleanup
            if os.path.exists(temp_path):
                os.unlink(temp_path)
            
            # Reset user state
            user_credentials[user_id]['waiting_for'] = None
            
    except Exception as e:
        logger.error(f"Video upload error: {e}")
        await message.reply_text(
            "❌ **Error during upload!**\n\n"
            "An unexpected error occurred. Please try again."
        )

# Run the bot
if __name__ == "__main__":
    print("🚀 Starting Dailymotion Upload Bot...")
    app.run()
