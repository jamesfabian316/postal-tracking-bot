# India Post Tracking Bot

A Telegram bot for tracking India Post packages. This bot allows users to track their packages and receive status updates automatically.

## Features

- Track multiple packages simultaneously
- Real-time status updates
- Support for Speed Post, Registered Post, and Air Waybill tracking numbers
- User-friendly commands and interface
- Automatic status notifications

## Setup

1. Clone the repository:

```bash
git clone https://github.com/jamesfabian316/postal-tracking-bot.git
```
```bash
cd postal-tracking-bot
```

2. Create and activate a virtual environment:

```bash
python -m venv venv
```
```bash
venv\Scripts\activate
```

3. Install dependencies:

```bash
pip install -r requirements.txt
```

4. Create a `.env` file in the project root and add your Telegram bot token:

```
TELEGRAM_BOT_TOKEN=your_bot_token_here
```

5. Run the bot:

```bash
python user_bot.py
```
6. Run the app:

```bash
python app.py
```

Go to [The Admin Dashboard](http://localhost:5000/)

## Usage

Go to the Telegram App and search for

@postal_tracker_bot

The bot supports the following commands:

- `/track <tracking_number>` - Start tracking a new package
- `/status` - Check status of all tracked packages
- `/help` - Show help message

## Tracking Number Format

- Speed Post: `EU430410927IN`
- Registered Post: `RM286760959IN`
- Air Waybill: `AW595537795IN`

## Contributing

Feel free to submit issues and enhancement requests!
