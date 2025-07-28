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

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
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
            # Corrected OAuth token URL
            token_url = "https://api.dailymotion.com/oauth/token"
            
            # Data payload with version=2 for password grant type
            data = {
                'grant_type': 'password',
                'client_id': api_key,
                'client_secret': api_secret,
                'username': username,
                'password': password,
                'scope': 'read write',
                'version': '2'  # Added as per Dailymotion API examples
            }
            
            # Ensure proper headers for form-encoded data
            headers = {
                'Content-Type': 'application/x-www-form-urlencoded'
            }
            
            # Make POST request
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
    
    def upload_video(self, file_path, title, description="", tags=""):
        """Upload video to Dailymotion"""
        if not self.access_token:
            return None, "No access token available"
        
        try:
            # Step 1: Get upload URL
            upload_url = "https://api.dailymotion.com/file/upload"  # Corrected base URL
            
            headers = {
                'Authorization': f'Bearer {self.access_token}',
                'Content-Type': 'application/json'
            }
            
            response = requests.get(upload_url, headers=headers, timeout=30)
            
            if response.status_code != 200:
                return None, f"Failed to get upload URL: {response.status_code} - {response.text}"
            
            upload_data = response.json()
            upload_endpoint = upload_data.get('upload_url')
            
            if not upload_endpoint:
                return None, "No upload URL received"
            
            # Step 2: Upload file
            with open(file_path, 'rb') as video_file:
                files = {'file': video_file}
                upload_response = requests.post(upload_endpoint, files=files, timeout=300)
            
            if upload_response.status_code != 200:
                return None, f"File upload failed: {upload_response.status_code} - {upload_response.text}"
            
            upload_result = upload_response.json()
            file_url = upload_result.get('url')
            
            if not file_url:
                return None, "No file URL received after upload"
            
            # Step 3: Create video object
            create_url = "https://api.dailymotion.com/videos"  # Corrected base URL
            
            video_data = {
                'url': file_url,
                'title': title,
                'description': description,
                'tags': tags,
                'published': 'true'
            }
            
            create_response = requests.post(create_url, data=video_data, headers=headers, timeout=30)
            
            if create_response.status_code in [200, 201]:
                result = create_response.json()
                video_id = result.get('id')
                if video_id:
                    video_url = f"https://www.dailymotion.com/video/{video_id}"
                    logger.info(f"Successfully uploaded video: {video_id}")
                    return video_url, "Success"
                else:
                    return None, "Video created but no ID received"
            else:
                return None, f"Video creation failed: {create_response.status_code} - {create_response.text}"
                
        except requests.exceptions.RequestException as e:
            logger.error(f"Network error during upload: {e}")
            return None, f"Upload error: {str(e)}"
        except ValueError as e:
            logger.error(f"JSON parsing error during upload: {e}")
            return None, f"Upload error: {str(e)}"
        except Exception as e:
            logger.error(f"Unexpected upload error: {str(e)}")
            return None, f"Upload error: {str(e)}"

class TelegramBot:
    def __init__(self, api_id, api_hash, bot_token):
        self.api_id = api_id
        self.api_hash = api_hash
        self.bot_token = bot_token
        self.client = None
        self.uploader = DailymotionUploader()
        self.user_sessions = {}
        self.temp_dir = tempfile.mkdtemp()
        self.running = False
        
    async def start_bot(self):
        """Start the Telegram bot"""
        try:
            self.client = TelegramClient('bot_session', self.api_id, self.api_hash)
            await self.client.start(bot_token=self.bot_token)
            self.running = True
            logger.info("Telegram bot started successfully")
            
            @self.client.on(events.NewMessage(pattern='/start'))
            async def start_handler(event):
                user_id = event.sender_id
                self.user_sessions[user_id] = {'step': 'waiting_api_key'}
                
                welcome_msg = """
🎬 **Dailymotion Video Upload Bot**

This bot will help you upload videos to Dailymotion.

First, I need your Dailymotion API credentials:
📝 Please send your **API Key**:
                """
                await event.respond(welcome_msg)
            
            @self.client.on(events.NewMessage(pattern='/help'))
            async def help_handler(event):
                help_msg = """
🆘 **Help - How to use this bot:**

1️⃣ Send /start to begin
2️⃣ Provide your Dailymotion API credentials
3️⃣ Send a video file
4️⃣ Provide a title for your video
5️⃣ Wait for upload to complete

**Need Dailymotion API credentials?**
Visit: https://developers.dailymotion.com/

**Supported formats:** MP4, AVI, MOV, WMV, etc.
**Max file size:** Depends on your Dailymotion account

For issues, contact the bot administrator.
                """
                await event.respond(help_msg)
            
            @self.client.on(events.NewMessage)
            async def message_handler(event):
                if event.text and event.text.startswith('/'):
                    return  # Skip commands already handled
                    
                user_id = event.sender_id
                
                if user_id not in self.user_sessions:
                    await event.respond("Please start with /start command")
                    return
                
                session = self.user_sessions[user_id]
                step = session.get('step')
                
                if step == 'waiting_api_key':
                    session['api_key'] = event.text.strip()
                    session['step'] = 'waiting_api_secret'
                    await event.respond("✅ API Key saved!\n📝 Please send your **API Secret**:")
                    
                elif step == 'waiting_api_secret':
                    session['api_secret'] = event.text.strip()
                    session['step'] = 'waiting_username'
                    await event.respond("✅ API Secret saved!\n📝 Please send your **Dailymotion Username**:")
                    
                elif step == 'waiting_username':
                    session['username'] = event.text.strip()
                    session['step'] = 'waiting_password'
                    await event.respond("✅ Username saved!\n📝 Please send your **Dailymotion Password**:")
                    
                elif step == 'waiting_password':
                    session['password'] = event.text.strip()
                    session['step'] = 'authenticated'
                    
                    # Try to authenticate
                    await event.respond("🔐 Authenticating with Dailymotion...")
                    
                    success = self.uploader.get_access_token(
                        session['api_key'],
                        session['api_secret'],
                        session['username'],
                        session['password']
                    )
                    
                    if success:
                        await event.respond("✅ **Authentication Successful!**\n\n📹 Now send me a video file to upload to Dailymotion!")
                    else:
                        await event.respond("❌ **Authentication Failed!**\n\nPlease check your credentials and try again with /start")
                        del self.user_sessions[user_id]
                        
                elif step == 'authenticated' and event.document:
                    # Check if it's a video file
                    if event.document.mime_type and event.document.mime_type.startswith('video/'):
                        session['step'] = 'waiting_title'
                        session['video_message'] = event
                        await event.respond("📝 Please send the **title** for this video:")
                    else:
                        await event.respond("❌ Please send a video file!")
                        
                elif step == 'waiting_title':
                    title = event.text.strip()
                    session['video_title'] = title
                    session['step'] = 'authenticated'
                    
                    # Start upload process
                    await self.process_video_upload(event, session)
        
        except Exception as e:
            logger.error(f"Error starting bot: {e}")
            raise
    
    async def process_video_upload(self, event, session):
        """Process video upload with progress tracking"""
        try:
            video_message = session['video_message']
            title = session['video_title']
            
            # Send initial progress message
            progress_msg = await event.respond("📥 **Downloading video...**\n⏳ 0%")
            
            # Download video with progress
            file_name = f"video_{int(time.time())}_{event.sender_id}.mp4"
            file_path = os.path.join(self.temp_dir, file_name)
            
            # Download file
            await video_message.download_media(file_path, progress_callback=lambda current, total: 
                asyncio.create_task(self.update_progress(progress_msg, "📥 Downloading", current, total)))
            
            # Update progress for upload
            await progress_msg.edit("📤 **Uploading to Dailymotion...**\n⏳ Processing...")
            
            # Upload to Dailymotion
            video_url, error_msg = self.uploader.upload_video(file_path, title)
            
            # Clean up temporary file
            try:
                os.remove(file_path)
            except:
                pass
            
            if video_url:
                success_msg = f"""
✅ **Upload Successful!**

📹 **Video Title:** {title}
🔗 **Video URL:** {video_url}

The video is now available on Dailymotion!
You can send another video to upload more.
                """
                await progress_msg.edit(success_msg)
                logger.info(f"Successfully processed upload for user {event.sender_id}")
            else:
                await progress_msg.edit(f"❌ **Upload Failed:**\n{error_msg}\n\nPlease try again.")
                logger.error(f"Upload failed for user {event.sender_id}: {error_msg}")
                
        except Exception as e:
            logger.error(f"Error processing upload: {e}")
            await event.respond(f"❌ **Error:** {str(e)}")
    
    async def update_progress(self, message, action, current, total):
        """Update progress message"""
        try:
            if total > 0:
                percentage = (current / total) * 100
                progress_text = f"{action}...\n⏳ {percentage:.1f}%"
                await message.edit(progress_text)
        except:
            pass  # Ignore edit conflicts
    
    async def stop_bot(self):
        """Stop the bot gracefully"""
        self.running = False
        if self.client:
            await self.client.disconnect()
        self.cleanup()
        logger.info("Bot stopped successfully")
    
    def cleanup(self):
        """Clean up temporary directory"""
        try:
            shutil.rmtree(self.temp_dir)
        except:
            pass

# Global bot instance
bot_instance = None

async def health_check(request):
    """Health check endpoint for Render"""
    return web.Response(text="Bot is running", status=200)

async def webhook_handler(request):
    """Webhook endpoint (optional)"""
    return web.Response(text="Webhook received", status=200)

async def init_web_server():
    """Initialize web server for Render"""
    app = web.Application()
    app.router.add_get('/', health_check)
    app.router.add_get('/health', health_check)
    app.router.add_post('/webhook', webhook_handler)
    
    port = int(os.environ.get('PORT', 8080))
    
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    
    logger.info(f"Web server started on port {port}")
    return runner

async def main():
    """Main function for Render deployment"""
    global bot_instance
    
    # Get environment variables
    api_id = os.environ.get('TELEGRAM_API_ID')
    api_hash = os.environ.get('TELEGRAM_API_HASH')
    bot_token = os.environ.get('TELEGRAM_BOT_TOKEN')
    
    if not all([api_id, api_hash, bot_token]):
        logger.error("Missing required environment variables!")
        logger.error("Required: TELEGRAM_API_ID, TELEGRAM_API_HASH, TELEGRAM_BOT_TOKEN")
        return
    
    try:
        api_id = int(api_id)
    except ValueError:
        logger.error("TELEGRAM_API_ID must be a number!")
        return
    
    # Initialize web server for Render
    web_runner = await init_web_server()
    
    # Initialize bot
    bot_instance = TelegramBot(api_id, api_hash, bot_token)
    
    async def shutdown_handler():
        """Handle shutdown gracefully"""
        logger.info("Shutting down...")
        if bot_instance:
            await bot_instance.stop_bot()
        await web_runner.cleanup()
    
    # Setup signal handlers
    def signal_handler(signum, frame):
        logger.info(f"Received signal {signum}")
        asyncio.create_task(shutdown_handler())
    
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    try:
        # Start bot
        await bot_instance.start_bot()
        logger.info("✅ Bot started successfully on Render!")
        logger.info("👤 Users can interact with the bot using /start")
        
        # Keep running
        while bot_instance.running:
            await asyncio.sleep(1)
            
    except Exception as e:
        logger.error(f"❌ Error running bot: {e}")
    finally:
        await shutdown_handler()

if __name__ == "__main__":
    # Set up asyncio for Render
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.error(f"Fatal error: {e}")
