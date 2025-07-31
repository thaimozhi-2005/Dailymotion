
**Notes:**
- Supports large files up to 4GB (Partner API)
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
        "**Max size:** 4GB"
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

    if (user_id not in user_credentials or
        user_credentials[user_id].get('waiting_for') != 'video' or
        'api_key' not in user_credentials[user_id]):
        await message.reply_text("Please use `/upload` command first.")
        return

    try:
        video = message.video
        file_size_mb = video.file_size / (1024 * 1024)

        if file_size_mb > 4096:  # 4GB limit for Partner API
            await message.reply_text("❌ File too large! Maximum size is 4GB.")
            return

        await message.reply_text(
            f"📹 **Video received!**\n\n"
            f"📁 Size: {file_size_mb:.1f} MB\n"
            f"⏱️ Duration: {video.duration}s\n\n"
            f"🔄 Starting upload process..."
        )

        with tempfile.NamedTemporaryFile(delete=False, suffix='.mp4') as temp_file:
            temp_path = temp_file.name

        try:
            progress_msg = await message.reply_text("📥 **Downloading from Telegram...**")
            progress_tracker = ProgressTracker(progress_msg, video.file_size, "Downloading")

            await app.download_media(
                message.video.file_id,
                temp_path,
                progress=lambda current, total: asyncio.create_task(progress_tracker.update(current, total))
            )

            await progress_msg.edit_text("🔄 **Starting Dailymotion upload...**")

            creds = user_credentials[user_id]
            uploader = DailymotionUploader(
                creds['api_key'],
                creds['api_secret'],
                creds['username'],
                creds['password']
            )

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
            if os.path.exists(temp_path):
                try:
                    os.unlink(temp_path)
                except Exception as e:
                    logger.warning(f"Could not delete temp file: {e}")

            user_credentials[user_id]['waiting_for'] = None

    except Exception as e:
        logger.error(f"Video upload error: {e}")
        await message.reply_text(
            "❌ **Error during upload!**\n\n"
            "An unexpected error occurred. Please try again."
        )

# Health check server for Render
async def start_health_server():
    """Start health check server for Render"""
    from aiohttp import web

    async def health_check(request):
        return web.json_response({"status": "healthy", "service": "dailymotion-bot"})

    try:
        health_app = web.Application()
        health_app.router.add_get('/health', health_check)  # Single route for health
        port = int(os.getenv('PORT', 10000))
        runner = web.AppRunner(health_app)
        await runner.setup()
        site = web.TCPSite(runner, '0.0.0.0', port)
        await site.start()
        logger.info(f"Health server started on port {port}")
        return runner
    except Exception as e:
        logger.error(f"Health server error: {e}")
        return None

# Graceful shutdown handler
def setup_signal_handlers(loop):
    """Setup signal handlers for graceful shutdown"""
    import signal

    def signal_handler():
        logger.info("Received shutdown signal, initiating graceful shutdown...")
        for task in asyncio.all_tasks(loop):
            task.cancel()

    if hasattr(signal, 'SIGTERM'):
        loop.add_signal_handler(signal.SIGTERM, signal_handler)
    if hasattr(signal, 'SIGINT'):
        loop.add_signal_handler(signal.SIGINT, signal_handler)

# Main function for Render deployment
async def main():
    """Main function to run bot and health server"""
    health_runner = None

    try:
        # Get event loop for signal handling
        loop = asyncio.get_event_loop()

        # Setup signal handlers (only on Unix systems)
        try:
            setup_signal_handlers(loop)
        except Exception as e:
            logger.warning(f"Could not setup signal handlers: {e}")

        # Start health check server
        health_runner = await start_health_server()

        if not health_runner:
            logger.warning("Health server failed to start, continuing without it")

        # Start the bot
        logger.info("🚀 Starting Dailymotion Upload Bot...")
        await app.start()  # Use bot token directly
        logger.info("✅ Bot started successfully!")

        # Keep running until interrupted
        await app.idle()

    except asyncio.CancelledError:
        logger.info("Tasks cancelled, shutting down...")
    except KeyboardInterrupt:
        logger.info("Received keyboard interrupt, shutting down...")
    except Exception as e:
        logger.error(f"Startup error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        logger.info("Starting cleanup process...")
        try:
            if app.is_connected:
                logger.info("Stopping Pyrogram client...")
                await app.stop()
                logger.info("Pyrogram client stopped")
        except Exception as e:
            logger.error(f"Error stopping bot: {e}")
        if health_runner:
            try:
                logger.info("Stopping health server...")
                await health_runner.cleanup()
                logger.info("Health server stopped")
            except Exception as e:
                logger.error(f"Error stopping health server: {e}")
        logger.info("Shutdown complete")

# Run the bot
if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.error(f"Fatal error: {e}")
