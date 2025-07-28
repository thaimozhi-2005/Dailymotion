import os
import asyncio
import aiohttp
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from pyrogram import Client as PyrogramClient
import logging
import tempfile
import json
import time
from collections import deque
from aiohttp import web
import signal
import sys
import shutil

# Configure logging for Render
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

class DownloadSpeedMonitor:
    def __init__(self, status_message, filename: str, total_size_mb: float):
        self.status_message = status_message
        self.filename = filename
        self.total_size_mb = total_size_mb
        self.start_time = time.time()
        self.last_update_time = self.start_time
        self.speed_history = deque(maxlen=10)
        self.last_bytes = 0
        self.update_interval = 2.0
        
    def calculate_speeds(self, current_bytes: int, current_time: float) -> tuple[float, float, float]:
        time_diff = max(current_time - self.last_update_time, 0.1)
        bytes_diff = max(current_bytes - self.last_bytes, 0)
        current_speed_bps = bytes_diff / time_diff
        current_speed_mbps = current_speed_bps / (1024 * 1024)
        
        if current_speed_mbps > 0:
            self.speed_history.append(current_speed_mbps)
        
        total_time = max(current_time - self.start_time, 0.1)
        avg_speed_mbps = (current_bytes / (1024 * 1024)) / total_time
        peak_speed_mbps = max(self.speed_history) if self.speed_history else 0
        
        return max(current_speed_mbps, 0), max(avg_speed_mbps, 0), max(peak_speed_mbps, 0)
    
    def format_speed_bar(self, current_speed: float, peak_speed: float) -> str:
        ratio = min(current_speed / peak_speed, 1.0) if peak_speed > 0 else 0
        filled_bars = int(ratio * 10)
        empty_bars = 10 - filled_bars
        return "▰" * filled_bars + "▱" * empty_bars
    
    def estimate_remaining_time(self, current_bytes: int, total_bytes: int, avg_speed_bps: float) -> str:
        if total_bytes <= current_bytes:
            return "Complete"
        remaining_bytes = total_bytes - current_bytes
        eta_seconds = remaining_bytes / max(avg_speed_bps, 0.1)
        if eta_seconds > 3600:
            hours = int(eta_seconds // 3600)
            minutes = int((eta_seconds % 3600) // 60)
            return f"{hours}h {minutes}m"
        elif eta_seconds > 60:
            minutes = int(eta_seconds // 60)
            seconds = int(eta_seconds % 60)
            return f"{minutes}m {seconds}s"
        else:
            return f"{int(eta_seconds)}s"
    
    async def update_progress(self, current_bytes: int, total_bytes: int):
        current_time = time.time()
        if current_time - self.last_update_time < self.update_interval:
            return
        current_speed, avg_speed, peak_speed = self.calculate_speeds(current_bytes, current_time)
        progress_percent = min((current_bytes / max(total_bytes, 1)) * 100, 100)
        current_mb = current_bytes / (1024 * 1024)
        speed_bar = self.format_speed_bar(current_speed, peak_speed)
        eta = self.estimate_remaining_time(current_bytes, total_bytes, avg_speed * 1024 * 1024)
        progress_bars = int(min(progress_percent / 5, 20))
        progress_text = (
            f"📥 **Downloading {self.filename}**\n\n"
            f"📊 **Progress:** {current_mb:.1f}MB / {self.total_size_mb:.1f}MB\n"
            f"📈 **{progress_percent:.1f}%** {'█' * progress_bars}{'░' * (20-progress_bars)}\n\n"
            f"⚡ **Current Speed:** {current_speed:.2f} MB/s\n"
            f"📊 **Average Speed:** {avg_speed:.2f} MB/s\n"
            f"🔝 **Peak Speed:** {peak_speed:.2f} MB/s\n"
            f"📶 **Speed Graph:** {speed_bar}\n\n"
            f"⏱️ **ETA:** {eta}\n"
            f"🕐 **Elapsed:** {int(current_time - self.start_time)}s"
        )
        await self.status_message.edit_text(progress_text, parse_mode='Markdown')
        self.last_update_time = current_time
        self.last_bytes = current_bytes
    
    async def finish(self, final_bytes: int):
        final_time = time.time()
        total_time = max(final_time - self.start_time, 0.1)
        final_mb = final_bytes / (1024 * 1024)
        avg_speed = final_mb / total_time
        peak_speed = max(self.speed_history) if self.speed_history else 0
        efficiency = (avg_speed / peak_speed * 100) if peak_speed > 0 else 0
        final_text = (
            f"✅ **Download Complete!**\n\n"
            f"📁 **File:** {self.filename}\n"
            f"📊 **Size:** {final_mb:.2f} MB\n"
            f"⏱️ **Time:** {total_time:.1f}s\n\n"
            f"📈 **Statistics:**\n"
            f"• Average Speed: {avg_speed:.2f} MB/s\n"
            f"• Peak Speed: {peak_speed:.2f} MB/s\n"
            f"• Efficiency: {efficiency:.1f}%"
        )
        await self.status_message.edit_text(final_text, parse_mode='Markdown')

class DailymotionUploader:
    def __init__(self):
        self.access_token = None
        self.api_key = None
        self.api_secret = None
        self.username = None
        self.password = None
        self.base_url = "https://api.dailymotion.com/oauth"
        self.api_url = "https://api.dailymotion.com"
    
    async def get_access_token(self):
        if self.access_token:
            return self.access_token
        auth_data = {
            'grant_type': 'password',
            'client_id': self.api_key,
            'client_secret': self.api_secret,
            'username': self.username,
            'password': self.password,
            'scope': 'manage_videos'
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(f"{self.base_url}/token", data=auth_data) as response:
                if response.status == 200:
                    data = await response.json()
                    self.access_token = data.get('access_token')
                    logger.info("Successfully obtained access token")
                    return self.access_token
                logger.error(f"Token error: {response.status} - {await response.text()}")
                return None
    
    async def get_upload_url(self):
        token = await self.get_access_token()
        if not token:
            return None, None
        headers = {'Authorization': f'Bearer {token}'}
        upload_endpoints = [f"{self.api_url}/file/upload", f"{self.api_url}/upload"]
        async with aiohttp.ClientSession() as session:
            for endpoint in upload_endpoints:
                try:
                    async with session.get(endpoint, headers=headers) as response:
                        if response.status == 200:
                            data = await response.json()
                            return data.get('upload_url'), data.get('progress_url')
                except Exception as e:
                    logger.error(f"Error with endpoint {endpoint}: {e}")
            return None, None
    
    async def upload_video_file(self, file_path: str):
        upload_url, _ = await self.get_upload_url()
        if not upload_url:
            return None
        async with aiohttp.ClientSession() as session:
            with open(file_path, 'rb') as f:
                data = aiohttp.FormData()
                data.add_field('file', f, filename=os.path.basename(file_path))
                async with session.post(upload_url, data=data) as response:
                    if response.status == 200:
                        result = await response.json()
                        return result.get('url') or result.get('file_url')
                    logger.error(f"Upload failed: {response.status} - {await response.text()}")
                    return None
    
    async def create_video(self, file_url: str, title: str, description="", tags=None):
        token = await self.get_access_token()
        if not token:
            return None
        video_data = {
            'url': file_url,
            'title': title,
            'description': description,
            'tags': ','.join(tags) if tags else '',
            'published': 'true'
        }
        headers = {'Authorization': f'Bearer {token}'}
        video_endpoints = [f"{self.api_url}/me/videos", f"{self.api_url}/videos"]
        async with aiohttp.ClientSession() as session:
            for endpoint in video_endpoints:
                try:
                    async with session.post(endpoint, headers=headers, data=video_data) as response:
                        if response.status in [200, 201]:
                            data = await response.json()
                            return data.get('id')
                        elif response.status == 403:
                            error_data = await response.json()
                            error_msg = error_data.get('error', {}).get('message', 'Unknown error')
                            if 'duration' in error_msg.lower():
                                raise Exception("video_duration_exceeded")
                            elif any(word in error_msg.lower() for word in ['quota', 'limit']):
                                raise Exception("quota_exceeded")
                            else:
                                raise Exception("access_forbidden")
                except Exception as e:
                    logger.error(f"Error with video endpoint {endpoint}: {e}")
            return None

class TelegramDailymotionBot:
    def __init__(self, telegram_token: str, api_id: int, api_hash: str, session_name: str = "bot_session"):
        self.telegram_token = telegram_token
        self.api_id = api_id
        self.api_hash = api_hash
        self.session_name = session_name
        self.app = Application.builder().token(telegram_token).build()
        self.pyrogram_client = PyrogramClient(session_name, api_id=api_id, api_hash=api_hash, bot_token=telegram_token)
        self.uploader = DailymotionUploader()
        self.user_sessions = {}
        self.temp_dir = tempfile.mkdtemp()
        self.session_file = os.path.join(self.temp_dir, 'sessions.json')
        self.running = False
        self.load_sessions()
        self.setup_handlers()
    
    def load_sessions(self):
        try:
            if os.path.exists(self.session_file):
                with open(self.session_file, 'r') as f:
                    self.user_sessions = json.load(f)
                logger.info("Loaded user sessions from file")
                for user_id, session in self.user_sessions.items():
                    if all(k in session for k in ['api_key', 'api_secret', 'username', 'password']):
                        self.uploader.api_key = session['api_key']
                        self.uploader.api_secret = session['api_secret']
                        self.uploader.username = session['username']
                        self.uploader.password = session['password']
                        self.uploader.get_access_token()
        except Exception as e:
            logger.error(f"Error loading sessions: {e}")
            self.user_sessions = {}
    
    def save_sessions(self):
        try:
            sessions_copy = {user_id: {k: v for k, v in session.items() if k not in ['video_message']} for user_id, session in self.user_sessions.items()}
            with open(self.session_file, 'w') as f:
                json.dump(sessions_copy, f)
            logger.info("Saved user sessions to file")
        except Exception as e:
            logger.error(f"Error saving sessions: {e}")
    
    def setup_handlers(self):
        self.app.add_handler(CommandHandler("start", self.start_command))
        self.app.add_handler(CommandHandler("help", self.help_command))
        self.app.add_handler(CommandHandler("test", self.test_command))
        self.app.add_handler(CommandHandler("force", self.force_upload_command))
        self.app.add_handler(MessageHandler(filters.VIDEO, self.handle_video))
        self.app.add_handler(MessageHandler(filters.Document.ALL, self.handle_document))
        self.app.add_handler(MessageHandler(~filters.COMMAND, self.handle_message))
    
    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        if user_id not in self.user_sessions:
            self.user_sessions[user_id] = {'step': 'waiting_api_key', 'channels': {}}
        welcome_msg = """
🎬 **Dailymotion Video Upload Bot**

First, provide your Dailymotion API credentials:
📝 Send your **API Key**:
        """
        await update.message.reply_text(welcome_msg)
        self.save_sessions()
    
    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        help_text = """
🆘 **Help - How to use this bot:**

1️⃣ /start - Begin setup
2️⃣ Provide Dailymotion API credentials
3️⃣ Send a video file to upload
4️⃣ Monitor progress and get the Dailymotion link

**Need Dailymotion API credentials?**
Visit: https://developers.dailymotion.com/

**Supported formats:** MP4, AVI, MOV, etc.
**Max file size:** 2GB
        """
        await update.message.reply_text(help_text)
    
    async def test_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        test_message = await update.message.reply_text("🔍 Testing Dailymotion API connection...")
        token = await self.uploader.get_access_token()
        await test_message.edit_text("✅ Dailymotion API authentication successful!" if token else "❌ Failed to authenticate")
    
    async def force_upload_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text("🚀 Force mode activated! Send your video now.")
    
    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        session = self.user_sessions.get(user_id, {})
        step = session.get('step', 'authenticated')
        
        if step == 'waiting_api_key':
            session['api_key'] = update.message.text.strip()
            session['step'] = 'waiting_api_secret'
            await update.message.reply_text("✅ API Key saved!\n📝 Send your **API Secret**:")
        elif step == 'waiting_api_secret':
            session['api_secret'] = update.message.text.strip()
            session['step'] = 'waiting_username'
            await update.message.reply_text("✅ API Secret saved!\n📝 Send your **Username**:")
        elif step == 'waiting_username':
            session['username'] = update.message.text.strip()
            session['step'] = 'waiting_password'
            await update.message.reply_text("✅ Username saved!\n📝 Send your **Password**:")
        elif step == 'waiting_password':
            session['password'] = update.message.text.strip()
            session['step'] = 'authenticated'
            self.uploader.api_key = session['api_key']
            self.uploader.api_secret = session['api_secret']
            self.uploader.username = session['username']
            self.uploader.password = session['password']
            await self.uploader.get_access_token()
            await update.message.reply_text("✅ Authentication Successful!\nSend a video to upload.")
        self.save_sessions()
    
    async def handle_video(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self.process_video_upload(update, context, update.message.video, update.message.video.file_name or "video.mp4")
    
    async def handle_document(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        document = update.message.document
        video_extensions = ['.mp4', '.avi', '.mov', '.mkv', '.wmv', '.flv', '.webm', '.m4v', '.3gp', '.mpg', '.mpeg']
        if document.file_name and any(document.file_name.lower().endswith(ext) for ext in video_extensions):
            await self.process_video_upload(update, context, document, document.file_name)
        else:
            await update.message.reply_text("❌ Please send a video file with a valid extension!")
    
    async def download_large_file(self, file_id: str, file_path: str, status_message, filename: str, file_size_mb: float):
        if not self.pyrogram_client.is_connected:
            await self.pyrogram_client.start()
        monitor = DownloadSpeedMonitor(status_message, filename, file_size_mb)
        def progress_callback(current, total):
            asyncio.create_task(monitor.update_progress(current, total))
        downloaded_file = await self.pyrogram_client.download_media(file_id, file_name=file_path, progress=progress_callback)
        final_size = os.path.getsize(file_path) if os.path.exists(file_path) else 0
        if final_size == 0:
            raise Exception("Downloaded file is empty")
        await monitor.finish(final_size)
        return downloaded_file
    
    async def process_video_upload(self, update: Update, context: ContextTypes.DEFAULT_TYPE, file_obj, filename: str):
        status_message = await update.message.reply_text("📥 Processing video...")
        temp_path = None
        
        try:
            file_size_mb = file_obj.file_size / (1024 * 1024)
            if file_size_mb > 2048:
                await status_message.edit_text("❌ File too large (>2GB)! Please compress it.")
                return
            
            file_extension = os.path.splitext(filename)[1] or '.mp4'
            with tempfile.NamedTemporaryFile(delete=False, suffix=file_extension, dir=self.temp_dir) as temp_file:
                temp_path = temp_file.name
            
            if file_size_mb <= 20:
                file = await context.bot.get_file(file_obj.file_id)
                await file.download_to_drive(temp_path)
                if os.path.getsize(temp_path) == 0:
                    raise Exception("Download failed: empty file")
            else:
                await self.download_large_file(file_obj.file_id, temp_path, status_message, filename, file_size_mb)
            
            await status_message.edit_text("☁️ Uploading to Dailymotion...")
            file_url = await self.uploader.upload_video_file(temp_path)
            if not file_url:
                raise Exception("Upload failed")
            
            video_title = os.path.splitext(filename)[0]
            video_description = f"Uploaded via Telegram Bot on {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime())}"
            await status_message.edit_text("📝 Creating video entry...")
            video_id = await self.uploader.create_video(file_url, video_title, video_description, ["telegram", "upload"])
            if not video_id:
                raise Exception("Video creation failed")
            
            dailymotion_url = f"https://www.dailymotion.com/video/{video_id}"
            success_text = f"✅ Uploaded!\n📹 Title: {video_title}\n🔗 {dailymotion_url}"
            await status_message.edit_text(success_text)
        
        except Exception as e:
            logger.error(f"Upload failed: {e}")
            error_msg = str(e)
            if "video_duration_exceeded" in error_msg:
                await status_message.edit_text("❌ Duration exceeded! Check daily quota (2hrs).")
            elif "quota_exceeded" in error_msg:
                await status_message.edit_text("❌ Quota exceeded! Wait 24hrs.")
            elif "Download failed" in error_msg or "empty" in error_msg:
                await status_message.edit_text("❌ Download failed: file is empty. Try again or check file size.")
            else:
                await status_message.edit_text(f"❌ Failed: {error_msg}")
        
        finally:
            if temp_path and os.path.exists(temp_path):
                os.unlink(temp_path)
                logger.info("Cleaned up temp file")
    
    async def start_bot(self):
        if not self.pyrogram_client.is_connected:
            await self.pyrogram_client.start()
        await self.app.initialize()
        await self.app.start()
        self.running = True
        logger.info("Bot started on Render at %s", time.strftime('%Y-%m-%d %H:%M:%S', time.localtime()))
        
        while self.running:
            await asyncio.sleep(1)
    
    async def stop_bot(self):
        self.running = False
        await self.app.stop()
        await self.pyrogram_client.stop()
        self.save_sessions()
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)
            logger.info("Cleaned up temporary directory")
    
    def run(self):
        web_runner = None
        async def shutdown_handler():
            logger.info("Shutting down on Render at %s", time.strftime('%Y-%m-%d %H:%M:%S', time.localtime()))
            await self.stop_bot()
            if web_runner:
                await web_runner.cleanup()
        
        def signal_handler(signum, frame):
            asyncio.create_task(shutdown_handler())
        
        signal.signal(signal.SIGTERM, signal_handler)
        signal.signal(signal.SIGINT, signal_handler)
        
        async def health_check(request):
            return web.Response(text="Bot is running", status=200)
        
        port = int(os.environ.get('PORT', '8080'))  # Use Render's PORT env var or default to 8080
        app = web.Application()
        app.router.add_get('/health', health_check)
        web_runner = web.AppRunner(app)
        asyncio.run_coroutine_threadsafe(web_runner.setup(), asyncio.get_event_loop())
        site = web.TCPSite(web_runner, '0.0.0.0', port)
        asyncio.run_coroutine_threadsafe(site.start(), asyncio.get_event_loop())
        
        try:
            asyncio.run(self.start_bot())
        except Exception as e:
            logger.error(f"Bot error at %s: {e}", time.strftime('%Y-%m-%d %H:%M:%S', time.localtime()))
            asyncio.run_coroutine_threadsafe(shutdown_handler(), asyncio.get_event_loop())

if __name__ == "__main__":
    TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', '8366671808:AAEDfoXpJyNmGn7QaITt1iO-mR0S2QvBwo0')
    TELEGRAM_API_ID = int(os.environ.get('TELEGRAM_API_ID', '27891965'))
    TELEGRAM_API_HASH = os.environ.get('TELEGRAM_API_HASH', '909e944f30752b2c47804cbccb8c5c4f')
    
    bot = TelegramDailymotionBot(TELEGRAM_BOT_TOKEN, TELEGRAM_API_ID, TELEGRAM_API_HASH)
    bot.run()
