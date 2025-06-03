# Telegram Credential Extractor Bot

A Telegram bot that extracts and processes credentials from text files.

## Features

- Extract credentials from text files
- Bulk processing mode
- File conversion mode
- Support for multiple chat types (private and channel)
- Secure credential storage and processing

## Deployment on Render

1. Fork or clone this repository
2. Create a new Web Service on Render
3. Connect your repository
4. Configure the following environment variables:
   - `BOT_TOKEN`: Your Telegram bot token from BotFather
   - `PORT`: Port for the webhook (default: 8443)
   - `RENDER_EXTERNAL_URL`: Will be automatically set by Render

### Build Settings

- Build Command: `docker build -t telegram-bot .`
- Start Command: `docker run -p $PORT:8443 telegram-bot`

### Environment Variables

Make sure to set these in your Render dashboard:

- `BOT_TOKEN`: Your Telegram bot token
- Any other secret configurations

## Local Development

1. Install dependencies:
```bash
pip install -r requirements.txt
```

2. Set environment variables:
```bash
export BOT_TOKEN=your_bot_token
export PORT=8443
export RENDER_EXTERNAL_URL=your_render_url
```

3. Run the bot:
```bash
python bot.py
```

## Usage

1. Start a chat with the bot on Telegram
2. Send `/start` to begin
3. Use `/bulk` to start bulk processing mode
4. Use `/cv` to start conversion mode
5. Send text files to process
6. Use `/stop` to stop processing
7. Use `/done` to finish bulk processing

## Support

For issues and feature requests, please create an issue in the repository. 