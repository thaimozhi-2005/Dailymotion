import os
import asyncio
import aiohttp
import aiofiles
from telethon import TelegramClient, events
from telethon.tl.types import DocumentAttributeVideo
import requests
from requests_oauthlib import OAuth2Session
import json
import time
from urllib.parse import urlencode
import tempfile
import shutil
import logging
from aiohttp import web
import signal
import sys
from telethon.errors import RPCError

# Configure logging for Render
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

class DailymotionUploader:
    def __init__(self):
        self.access_token = None
        self.api_key = None
        self.api_secret = None
        self.username = None
        self.password = None
        
    def get_access_token(self, api_key, api_secret, username, password):
        """Get access token using username and password"""
        try:
            token_url = "https://api.dailymotion.com/oauth/token"
            data = {
                'grant_type': 'password',
                'client_id': api_key,
                'client_secret': api_secret,
                'username': username,
                'password': password,
                'scope': 'read write',
                'version': '2'
            }
            headers = {'Content-Type': 'application/x-www-form-urlencoded'}
            response = requests.post(token_url, data=data, headers=headers, timeout=30)
            
            if response.status_code == 200:
                token_data = response.json()
                self.access_token = token_data.get('access_token')
                if not self.access_token:
                    logger.error("No access token found in response")
                    return False
                logger.info("Successfully obtained Dailymotion access token")
                self.api_key = api_key
                self.api_secret = api_secret
                self.username = username
                self.password = password
                return True
            else:
                logger.error(f"Token error: {response.status_code} - {response.text}")
                return False
        except requests.exceptions.RequestException as e:
            logger.error(f"Network error during authentication: {e}")
            return False
        except ValueError as e:
            logger.error(f"JSON parsing error: {e}")
            return False
        except Exception as e:
            logger.error(f"Unexpected authentication error: {e}")
            return False
    
    def upload_video(self, file_path, title, description="", tags="", channel=""):
        """Upload video to Dailymotion with optional channel"""
        if not self.access_token:
            logger.error("No access token available for upload")
            return None, "No access token available"
        
        try:
            # Step 1: Get upload URL
            upload_url = "https://api.dailymotion.com/file/upload"
            headers = {
                'Authorization': f'Bearer {self.access_token}',
                'Content-Type': 'application/json'
            }
            response = requests.get(upload_url, headers=headers, timeout=30)
            
            if response.status_code != 200:
                logger.error(f"Failed to get upload URL: {response.status_code} - {response.text}")
                return None, f"Failed to get upload URL: {response.status_code} - {response.text}"
            
            upload_data = response.json()
            upload_endpoint = upload_data.get('upload_url')
            if not upload_endpoint:
                logger.error("No upload URL received in response: %s", upload_data)
                return None, "No upload URL received"
            
            logger.info(f"Received upload endpoint: {upload_endpoint}")
            
            # Step 2: Upload file
            if not os.path.exists(file_path):
                logger.error(f"File not found: {file_path}")
                return None, f"File not found: {file_path}"
            
            with open(file_path, 'rb') as video_file:
                files = {'file': (os.path.basename(file_path), video_file, 'video/mp4')}
                upload_response = requests.post(upload_endpoint, files=files, timeout=600)
            
            if upload_response.status_code != 200:
                logger.error(f"File upload failed: {upload_response.status_code} - {upload_response.text}")
                return None, f"File upload failed: {upload_response.status_code} - {upload_response.text}"
            
            upload_result = upload_response.json()
            file_url = upload_result.get('url')
            if not file_url:
                logger.error("No file URL received after upload: %s", upload_result)
                return None, "No file URL received after upload"
            
            logger.info(f"File uploaded, received file URL: {file_url}")
            
            # Step 3: Create video object
            create_url = "https://api.dailymotion.com/videos"
            video_data = {
                'url': file_url,
                'title': title,
                'description': description,
                'tags': tags,
                'published': 'true'
            }
            if channel:
                video_data['channel'] = channel
            
            create_response = requests.post(create_url, data=video_data, headers=headers, timeout=30)
            
            if create_response.status_code in [200, 201]:
                result = create_response.json()
                video_id = result.get('id')
                if video_id:
                    video_url = f"https://www.dailymotion.com/video/{video_id}"
                    logger.info(f"Successfully uploaded video: {video_id}")
                    return video_url, "Success"
                else:
                    logger.error("Video created but no ID received: %s", result)
                    return None, "Video created but no ID received"
            else:
                logger.error(f"Video creation failed: {create_response.status_code} - {create_response.text}")
                return None, f"Video creation failed: {create_response.status_code} - {create_response.text}"
                
        except requests.exceptions.RequestException as e:
            logger.error(f"Network error during upload: {e}, File: {file_path}")
            return None, f"Upload error: {str(e)}"
        except ValueError as e:
            logger.error(f"JSON parsing error during upload: {e}, File: {file_path}")
            return None, f"Upload error: {str(e)}"
        except Exception as e:
            logger.error(f"Unexpected upload error: {e}, File: {file_path}")
            return None, f"Upload error: {str(e)}"

class TelegramBot:
    def __init__(self, api_id, api_hash, bot_token):
        self.api_id = api_id
        self.api_hash = api_hash
        self.bot_token = bot_token
        self.client = None
        self.uploader = DailymotionUploader()
        self.user_sessions = {}  # In-memory sessions
        self.temp_dir = tempfile.mkdtemp()
        self.session_file = os.path.join(self.temp_dir, 'sessions.json')
        self.running = False
        self.load_sessions()
        
    def load_sessions(self):
        """Load user sessions from file if it exists"""
        try:
            if os.path.exists(self.session_file):
                with open(self.session_file, 'r') as f:
                    self.user_sessions = json.load(f)
                logger.info("Loaded user sessions from file")
                # Re-authenticate if token is missing
                for user_id, session in self.user_sessions.items():
                    if all(k in session for k in ['api_key', 'api_secret', 'username', 'password']) and not self.uploader.access_token:
                        self.uploader.get_access_token(session['api_key'], session['api_secret'], session['username'], session['password'])
        except Exception as e:
            logger.error(f"Error loading sessions: {e}")
            self.user_sessions = {}
    
    def save_sessions(self):
        """Save user sessions to file, excluding non-serializable objects"""
        try:
            # Create a copy without video_message
            sessions_copy = {}
            for user_id, session in self.user_sessions.items():
                sessions_copy[user_id] = {k: v for k, v in session.items() if k != 'video_message'}
            with open(self.session_file, 'w') as f:
                json.dump(sessions_copy, f)
            logger.info("Saved user sessions to file")
        except Exception as e:
            logger.error(f"Error saving sessions: {e}")
    
    async def start_bot(self):
        """Start the Telegram bot on Render"""
        try:
            self.client = TelegramClient('bot_session', self.api_id, self.api_hash)
            await self.client.start(bot_token=self.bot_token)
            self.running = True
            logger.info("Telegram bot started successfully on Render")
            
            @self.client.on(events.NewMessage(pattern='/start'))
            async def start_handler(event):
                user_id = event.sender_id
                if user_id not in self.user_sessions:
                    self.user_sessions[user_id] = {'step': 'waiting_api_key', 'channels': {}}
                welcome_msg = """
🎬 **Dailymotion Video Upload Bot**

This bot will help you upload videos to Dailymotion.

First, I need your Dailymotion API credentials:
📝 Please send your **API Key**:
                """
                await event.respond(welcome_msg)
                logger.info(f"Started session for user {user_id}")
                self.save_sessions()
            
            @self.client.on(events.NewMessage(pattern='/help'))
            async def help_handler(event):
                help_msg = """
🆘 **Help - How to use this bot:**

1️⃣ /start - Begin setup
2️⃣ /addch <channel_id> - Add a new Dailymotion channel (use channel ID, e.g., x123abc)
3️⃣ /list - Show all your channels
4️⃣ /upload - Start video upload process
5️⃣ Provide Dailymotion API credentials, then send a video and title

**Need Dailymotion API credentials?**
Visit: https://developers.dailymotion.com/

**Supported formats:** MP4, AVI, MOV, WMV, etc.
**Max file size:** Depends on your Dailymotion account
                """
                await event.respond(help_msg)
                logger.info(f"Help requested by user {event.sender_id}")
            
            @self.client.on(events.NewMessage(pattern='/addch (.*)'))
            async def add_channel_handler(event):
                user_id = event.sender_id
                if user_id not in self.user_sessions:
                    await event.respond("Please start with /start command")
                    return
                channel_id = event.pattern_match.group(1).strip()
                if channel_id:
                    self.user_sessions[user_id]['channels'][channel_id] = True
                    await event.respond(f"✅ Added channel: {channel_id}")
                    logger.info(f"Added channel {channel_id} for user {user_id}")
                else:
                    await event.respond("❌ Please provide a channel ID, e.g., /addch x123abc")
                self.save_sessions()
            
            @self.client.on(events.NewMessage(pattern='/list'))
            async def list_channels_handler(event):
                user_id = event.sender_id
                if user_id not in self.user_sessions:
                    await event.respond("Please start with /start command")
                    return
                channels = self.user_sessions[user_id].get('channels', {})
                if channels:
                    await event.respond("📋 **Your Channels:**\n" + "\n".join(channels.keys()))
                else:
                    await event.respond("📭 No channels added yet. Use /addch <channel_id> to add one.")
                logger.info(f"Listed channels for user {user_id}")
            
            @self.client.on(events.NewMessage(pattern='/upload'))
            async def upload_handler(event):
                user_id = event.sender_id
                if user_id not in self.user_sessions:
                    await event.respond("Please start with /start command")
                    return
                session = self.user_sessions[user_id]
                if not all(k in session for k in ['api_key', 'api_secret', 'username', 'password']) or not self.uploader.access_token:
                    await event.respond("Please authenticate with /start and provide credentials first.")
                    return
                session['step'] = 'waiting_video'
                await event.respond("📹 **Upload Process Started**\nPlease send the video file you want to upload.")
                logger.info(f"Upload process started for user {user_id}")
                self.save_sessions()
            
            @self.client.on(events.NewMessage)
            async def message_handler(event):
                if event.text and event.text.startswith('/'):
                    return  # Skip commands already handled
                
                user_id = event.sender_id
                
                if user_id not in self.user_sessions:
                    await event.respond("Please start with /start command")
                    logger.info(f"New user {user_id} prompted to start")
                    return
                
                session = self.user_sessions[user_id]
                step = session.get('step', 'authenticated')
                
                logger.info(f"Processing message for user {user_id}, step: {step}")
                
                if step == 'waiting_api_key':
                    session['api_key'] = event.text.strip()
                    session['step'] = 'waiting_api_secret'
                    await event.respond("✅ API Key saved!\n📝 Please send your **API Secret**:")
                    logger.info(f"API Key saved for user {user_id}")
                    
                elif step == 'waiting_api_secret':
                    session['api_secret'] = event.text.strip()
                    session['step'] = 'waiting_username'
                    await event.respond("✅ API Secret saved!\n📝 Please send your **Dailymotion Username**:")
                    logger.info(f"API Secret saved for user {user_id}")
                    
                elif step == 'waiting_username':
                    session['username'] = event.text.strip()
                    session['step'] = 'waiting_password'
                    await event.respond("✅ Username saved!\n📝 Please send your **Dailymotion Password**:")
                    logger.info(f"Username saved for user {user_id}")
                    
                elif step == 'waiting_password':
                    session['password'] = event.text.strip()
                    session['step'] = 'authenticated'
                    
                    await event.respond("🔐 Authenticating with Dailymotion...")
                    success = self.uploader.get_access_token(
                        session['api_key'],
                        session['api_secret'],
                        session['username'],
                        session['password']
                    )
                    
                    if success:
                        await event.respond("✅ **Authentication Successful!**\n\nUse /upload to start uploading a video.")
                        logger.info(f"Authentication successful for user {user_id}")
                    else:
                        await event.respond("❌ **Authentication Failed!**\n\nPlease check your credentials and try again with /start")
                        del self.user_sessions[user_id]
                        logger.error(f"Authentication failed for user {user_id}")
                        
                elif step == 'waiting_video' and event.document:
                    if event.document.mime_type and event.document.mime_type.startswith('video/'):
                        session['step'] = 'waiting_title'
                        await event.respond("📝 Please send the **title** for this video:")
                        logger.info(f"Video received from user {user_id}, waiting for title")
                    else:
                        await event.respond("❌ Please send a video file!")
                        logger.info(f"Non-video file received from user {user_id}")
                        
                elif step == 'waiting_title':
                    title = event.text.strip()
                    session['video_title'] = title
                    session['step'] = 'waiting_channel'
                    channels = session.get('channels', {})
                    if channels:
                        await event.respond(f"📡 **Select a channel to upload to:**\n" + "\n".join([f"{i+1}. {ch}" for i, ch in enumerate(channels.keys())]) + "\nSend the number (e.g., 1) or 'skip' to use default.")
                    else:
                        session['step'] = 'processing_upload'
                        await self.process_video_upload(event, session)
                    logger.info(f"Title '{title}' received from user {user_id}")
                    self.save_sessions()
                
                elif step == 'waiting_channel':
                    try:
                        choice = event.text.strip().lower()
                        channels = session.get('channels', {})
                        if choice == 'skip' or not channels:
                            session['step'] = 'processing_upload'
                            await self.process_video_upload(event, session)
                        else:
                            index = int(choice) - 1
                            if 0 <= index < len(channels):
                                channel = list(channels.keys())[index]
                                session['selected_channel'] = channel
                                session['step'] = 'processing_upload'
                                await self.process_video_upload(event, session)
                            else:
                                await event.respond("❌ Invalid choice. Please send a valid number or 'skip'.")
                        logger.info(f"Channel selection processed for user {user_id}")
                    except ValueError:
                        await event.respond("❌ Please send a number or 'skip'.")
                        logger.info(f"Invalid channel selection from user {user_id}")
                    self.save_sessions()
                elif step == 'processing_upload':
                    await event.respond("⚠️ Upload is already in progress or was interrupted. Please wait or use /upload to start a new upload.")
                    logger.info(f"Upload processing state for user {user_id}")
                
        except Exception as e:
            logger.error(f"Error starting bot on Render: {e}")
            raise
    
    async def process_video_upload(self, event, session, max_retries=3):
        """Process video upload with progress tracking and retry logic"""
        for attempt in range(max_retries):
            try:
                title = session['video_title']
                channel = session.get('selected_channel', '')
                logger.info(f"Starting video upload process (Attempt {attempt + 1}/{max_retries}) for user {event.sender_id} with title: {title}, channel: {channel}")
                
                # Send initial progress message
                progress_msg = await event.respond("📥 **Downloading video...**\n⏳ 0%")
                logger.info(f"Sent initial progress message for download")
                
                # Download video with progress
                file_name = f"video_{int(time.time())}_{event.sender_id}.mp4"
                file_path = os.path.join(self.temp_dir, file_name)
                logger.info(f"Attempting to download video to: {file_path}")
                
                await event.download_media(file=file_path, progress_callback=lambda current, total: 
                    asyncio.create_task(self.update_progress(progress_msg, "📥 Downloading", current, total)))
                
                logger.info(f"Video downloaded to: {file_path}")
                
                # Verify file exists and is not empty
                if not os.path.exists(file_path) or os.path.getsize(file_path) == 0:
                    logger.error(f"Downloaded file is missing or empty: {file_path}")
                    await progress_msg.edit("❌ **Error:** Downloaded file is missing or empty. Please try again.")
                    session['step'] = 'authenticated'
                    self.save_sessions()
                    return
                
                # Update progress for upload
                await progress_msg.edit("📤 **Uploading to Dailymotion...**\n⏳ Processing...")
                logger.info("Updated progress message to uploading state")
                
                # Upload to Dailymotion
                video_url, error_msg = self.uploader.upload_video(file_path, title, channel=channel)
                logger.info(f"Upload result - URL: {video_url}, Error: {error_msg}")
                
                # Clean up temporary file
                try:
                    if os.path.exists(file_path):
                        os.remove(file_path)
                        logger.info(f"Cleaned up temporary file: {file_path}")
                    else:
                        logger.warning(f"Temporary file not found for cleanup: {file_path}")
                except Exception as e:
                    logger.error(f"Error cleaning up temporary file: {e}")
                
                if video_url:
                    success_msg = f"""
✅ **Upload Successful!**

📹 **Video Title:** {title}
🔗 **Video URL:** {video_url}

The video is now available on Dailymotion!
Use /upload to send another video.
                    """
                    await progress_msg.edit(success_msg)
                    logger.info(f"Successfully processed upload for user {event.sender_id}")
                    session['step'] = 'authenticated'
                    self.save_sessions()
                    return
                else:
                    await progress_msg.edit(f"❌ **Upload Failed:**\n{error_msg}\n\nPlease try again with /upload.")
                    logger.error(f"Upload failed for user {event.sender_id}: {error_msg}")
                    session['step'] = 'authenticated'
                    self.save_sessions()
                    return
                
            except RPCError as e:
                logger.error(f"RPC error during upload attempt {attempt + 1}: {e}")
                if "disconnected" in str(e).lower() or attempt < max_retries - 1:
                    logger.info("Reconnecting and retrying...")
                    await asyncio.sleep(2)
                    await self.client.connect()
                    continue
                else:
                    logger.error(f"Max retries reached for user {event.sender_id}: {e}")
                    await event.respond(f"❌ **Error:** Max retries reached. Please try again with /upload. Error: {str(e)}")
                    session['step'] = 'authenticated'
                    self.save_sessions()
            except Exception as e:
                logger.error(f"Error processing upload for user {event.sender_id} on Render: {e}")
                await event.respond(f"❌ **Error:** {str(e)}")
                session['step'] = 'authenticated'
                self.save_sessions()
                return
    
    async def update_progress(self, message, action, current, total):
        """Update progress message"""
        try:
            if total > 0:
                percentage = (current / total) * 100
                progress_text = f"{action}...\n⏳ {percentage:.1f}%"
                await message.edit(progress_text)
        except Exception as e:
            logger.error(f"Error updating progress on Render: {e}")
    
    async def stop_bot(self):
        """Stop the bot gracefully for Render shutdown"""
        self.running = False
        if self.client:
            await self.client.disconnect()
            logger.info("Disconnected Telegram client")
        self.save_sessions()
        self.cleanup()
        logger.info("Bot stopped successfully on Render")
    
    def cleanup(self):
        """Clean up temporary directory for Render"""
        try:
            if os.path.exists(self.temp_dir):
                shutil.rmtree(self.temp_dir)
                logger.info(f"Cleaned up temporary directory: {self.temp_dir}")
            else:
                logger.warning(f"Temporary directory not found for cleanup: {self.temp_dir}")
        except Exception as e:
            logger.error(f"Error cleaning up temp dir on Render: {e}")

# Global bot instance
bot_instance = None

async def health_check(request):
    """Health check endpoint for Render with keep-alive"""
    logger.info("Health check received")
    return web.Response(text="Bot is running", status=200)

async def webhook_handler(request):
    """Webhook endpoint (optional)"""
    return web.Response(text="Webhook received", status=200)

async def init_web_server():
    """Initialize web server for Render with keep-alive"""
    app = web.Application()
    app.router.add_get('/', health_check)
    app.router.add_get('/health', health_check)
    app.router.add_post('/webhook', webhook_handler)
    
    port = int(os.environ.get('PORT', 8080))
    host = '0.0.0.0'
    
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    
    logger.info(f"Web server started on {host}:{port} for Render with keep-alive")
    return runner

async def main():
    """Main function for Render deployment"""
    global bot_instance
    
    # Get and validate environment variables
    api_id = os.environ.get('TELEGRAM_API_ID')
    api_hash = os.environ.get('TELEGRAM_API_HASH')
    bot_token = os.environ.get('TELEGRAM_BOT_TOKEN')
    
    if not all([api_id, api_hash, bot_token]):
        logger.error("Missing required environment variables on Render!")
        logger.error("Required: TELEGRAM_API_ID, TELEGRAM_API_HASH, TELEGRAM_BOT_TOKEN")
        sys.exit(1)
    
    try:
        api_id = int(api_id)
    except ValueError:
        logger.error("TELEGRAM_API_ID must be a number on Render!")
        sys.exit(1)
    
    # Initialize web server for Render
    web_runner = await init_web_server()
    
    # Initialize bot
    bot_instance = TelegramBot(api_id, api_hash, bot_token)
    
    async def shutdown_handler():
        """Handle shutdown gracefully for Render"""
        logger.info("Shutting down on Render...")
        if bot_instance:
            await bot_instance.stop_bot()
        await web_runner.cleanup()
        logger.info("Shutdown complete on Render")
    
    def signal_handler(signum, frame):
        logger.info(f"Received signal {signum} on Render")
        asyncio.create_task(shutdown_handler())
    
    # Register signal handlers for Render
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    try:
        await bot_instance.start_bot()
        logger.info("✅ Bot started successfully on Render!")
        logger.info("👤 Users can interact with the bot using /start")
        
        while bot_instance.running:
            await asyncio.sleep(1)
            
    except Exception as e:
        logger.error(f"❌ Error running bot on Render: {e}")
        await shutdown_handler()
        sys.exit(1)
    finally:
        await shutdown_handler()

if __name__ == "__main__":
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot stopped by user on Render")
    except Exception as e:
        logger.error(f"Fatal error on Render: {e}")
        sys.exit(1)
