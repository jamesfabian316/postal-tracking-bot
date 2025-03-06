import requests
import sqlite3
import time
import os
from dotenv import load_dotenv
import logging
from datetime import datetime
import re
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from collections import defaultdict

# Set up logging with more detailed format
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('bot.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()
TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
if not TOKEN:
    raise ValueError("TELEGRAM_BOT_TOKEN environment variable is not set")

# Constants
API_BASE_URL = "https://api.telegram.org/bot"
MAX_RETRIES = 3
RETRY_DELAY = 1
RATE_LIMIT_DELAY = 1
MAX_REQUESTS_PER_MINUTE = 30
CHECK_INTERVAL = 10  # Check for updates every 10 seconds
DATABASE_PATH = "tracking.db"

def is_valid_tracking_number(tracking_number: str) -> bool:
    """Validate India Post tracking number format"""
    # Remove spaces from the tracking number
    tracking_number = tracking_number.replace(" ", "")
    
    # Check if the tracking number matches the format: 2 letters + 9 numbers + 2 letters
    if not re.match(r'^[A-Z]{2}\d{9}[A-Z]{2}$', tracking_number):
        return False
    
    # Check if the first letter is valid (E for Speed Post, R for Registered Post, A for Air Waybill)
    first_letter = tracking_number[0]
    if first_letter not in ['E', 'R', 'A']:
        return False
    
    # Check if the last two letters are 'IN'
    if tracking_number[-2:] != 'IN':
        return False
    
    return True

def format_tracking_number(tracking_number: str) -> str:
    """Format tracking number without spaces"""
    return tracking_number.replace(" ", "")

@dataclass
class TrackingInfo:
    tracking_number: str
    chat_id: str
    status: str
    status_details: str
    last_updated: int

class RateLimiter:
    def __init__(self, max_requests: int, time_window: int):
        self.max_requests = max_requests
        self.time_window = time_window
        self.requests: Dict[str, List[float]] = defaultdict(list)

    def is_allowed(self, chat_id: str) -> bool:
        current_time = time.time()
        self.requests[chat_id] = [t for t in self.requests[chat_id] 
                                if current_time - t < self.time_window]
        
        if len(self.requests[chat_id]) >= self.max_requests:
            return False
        
        self.requests[chat_id].append(current_time)
        return True

class DatabaseManager:
    def __init__(self, db_path: str = DATABASE_PATH):
        self.db_path = db_path
        self.setup_database()

    def setup_database(self):
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS tracking (
                        tracking_number TEXT PRIMARY KEY,
                        chat_id TEXT,
                        status TEXT,
                        status_details TEXT,
                        last_updated INTEGER,
                        created_at INTEGER DEFAULT (unixepoch())
                    )
                """)
                # Create index for faster queries
                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_chat_id 
                    ON tracking(chat_id)
                """)
                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_last_updated 
                    ON tracking(last_updated)
                """)
                logger.info("Database setup completed successfully")
        except sqlite3.Error as e:
            logger.error(f"Error setting up database: {str(e)}")
            raise

    def add_tracking(self, tracking_info: TrackingInfo) -> bool:
        try:
            with sqlite3.connect(self.db_path) as conn:
                # Check if tracking number already exists
                existing = conn.execute(
                    "SELECT status FROM tracking WHERE tracking_number = ?",
                    (tracking_info.tracking_number,)
                ).fetchone()
                
                if existing:
                    logger.info(f"Tracking number {tracking_info.tracking_number} already exists")
                    return False
                
                conn.execute("""
                    INSERT INTO tracking 
                    (tracking_number, chat_id, status, status_details, last_updated) 
                    VALUES (?, ?, ?, ?, ?)
                """, (tracking_info.tracking_number, tracking_info.chat_id,
                     tracking_info.status, tracking_info.status_details,
                     tracking_info.last_updated))
                return True
        except sqlite3.Error as e:
            logger.error(f"Database error while adding tracking: {str(e)}")
            return False

    def get_tracking_by_chat_id(self, chat_id: str) -> List[Tuple]:
        try:
            with sqlite3.connect(self.db_path) as conn:
                return conn.execute("""
                    SELECT tracking_number, status, status_details, last_updated 
                    FROM tracking 
                    WHERE chat_id = ?
                    ORDER BY last_updated DESC
                """, (chat_id,)).fetchall()
        except sqlite3.Error as e:
            logger.error(f"Database error while fetching tracking: {str(e)}")
            return []

    def get_all_tracking(self) -> List[Tuple]:
        try:
            with sqlite3.connect(self.db_path) as conn:
                return conn.execute("""
                    SELECT tracking_number, chat_id, status, status_details, last_updated 
                    FROM tracking 
                    ORDER BY last_updated DESC
                """).fetchall()
        except sqlite3.Error as e:
            logger.error(f"Database error while fetching all tracking: {str(e)}")
            return []

    def cleanup_old_tracking(self, days: int = 30) -> int:
        """Remove tracking entries older than specified days"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.execute("""
                    DELETE FROM tracking 
                    WHERE last_updated < unixepoch('now', '-' || ? || ' days')
                """, (days,))
                deleted_count = cursor.rowcount
                conn.commit()
                return deleted_count
        except sqlite3.Error as e:
            logger.error(f"Database error while cleaning up old tracking: {str(e)}")
            return 0

class TelegramBot:
    def __init__(self):
        self.token = TOKEN
        self.db = DatabaseManager()
        self.rate_limiter = RateLimiter(MAX_REQUESTS_PER_MINUTE, 60)
        self.status_emojis = {
            'Order Placed': '📦',
            'Processing': '⚙️',
            'Picked Up': '🚚',
            'In Transit': '✈️',
            'Out for Delivery': '🚛',
            'Delivered': '✅',
            'Failed Delivery': '❌',
            'Returned': '↩️',
        }
        # Set up commands menu
        self.set_commands()

    def set_commands(self) -> None:
        """Set up the bot's command menu"""
        url = f"{API_BASE_URL}{self.token}/setMyCommands"
        commands = [
            {"command": "track", "description": "Track a new package"},
            {"command": "status", "description": "Check status of all tracked packages"},
            {"command": "help", "description": "Show help message"}
        ]
        payload = {"commands": commands}
        
        try:
            response = requests.post(url, json=payload, timeout=10)
            response.raise_for_status()
            logger.info("Successfully set up bot commands menu")
        except requests.RequestException as e:
            logger.error(f"Failed to set up bot commands menu: {str(e)}")

    def send_message(self, chat_id: str, message: str, retry_count: int = 0) -> bool:
        if not self.rate_limiter.is_allowed(chat_id):
            logger.warning(f"Rate limit exceeded for chat_id {chat_id}")
            return False

        url = f"{API_BASE_URL}{self.token}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": message,
            "parse_mode": "HTML"
        }

        try:
            response = requests.post(url, json=payload, timeout=10)
            response.raise_for_status()
            logger.info(f"Successfully sent message to chat_id {chat_id}")
            return True
        except requests.RequestException as e:
            if retry_count < MAX_RETRIES:
                logger.warning(f"Retrying message send to chat_id {chat_id} after error: {str(e)}")
                time.sleep(RETRY_DELAY)
                return self.send_message(chat_id, message, retry_count + 1)
            logger.error(f"Failed to send message to chat_id {chat_id}: {str(e)}")
            return False

    def get_updates(self, offset: Optional[int] = None) -> List[Dict]:
        url = f"{API_BASE_URL}{self.token}/getUpdates"
        params = {"offset": offset} if offset else {}
        
        try:
            response = requests.get(url, params=params, timeout=10)
            response.raise_for_status()
            return response.json().get("result", [])
        except requests.RequestException as e:
            logger.error(f"Failed to get updates: {str(e)}")
            return []

    def format_status_message(self, tracking_number: str, status: str, 
                            details: str, last_updated: int, include_footer: bool = False) -> str:
        emoji = self.status_emojis.get(status, '📋')
        last_updated_str = datetime.fromtimestamp(last_updated).strftime('%Y-%m-%d %H:%M:%S')
        
        message = f"""
📦 <b>Package Status Update</b>

Tracking Number: <code>{format_tracking_number(tracking_number)}</code>
Status: {emoji} <b>{status}</b>
Time: {last_updated_str}

{details}"""

        if include_footer:
            message += """

<i>This is an automated message. Please do not reply.</i>"""

        return message

    def handle_command(self, chat_id: str, command: str, args: List[str]) -> None:
        command = command.lower()
        
        if command == "/help":
            self.send_message(chat_id, HELP_MESSAGE)
        elif command == "/track":
            if not args:
                self.send_message(chat_id, TRACK_USAGE_MESSAGE)
                return
            self.handle_track_command(chat_id, args[0])
        elif command == "/status":
            self.handle_status_command(chat_id)
        else:
            self.send_message(chat_id, UNKNOWN_COMMAND_MESSAGE)

    def handle_track_command(self, chat_id: str, tracking_number: str) -> None:
        if not is_valid_tracking_number(tracking_number):
            self.send_message(chat_id, INVALID_TRACKING_MESSAGE)
            return

        tracking_info = TrackingInfo(
            tracking_number=tracking_number,
            chat_id=chat_id,
            status="Order Placed",
            status_details="Your order has been placed and is being processed.",
            last_updated=int(time.time())
        )

        if self.db.add_tracking(tracking_info):
            self.send_message(chat_id, f"""
📦 <b>New Tracking Started</b>

Tracking Number: <code>{format_tracking_number(tracking_number)}</code>

Your package is now being tracked. You will receive updates about your package's status.

<i>This is an automated message. Please do not reply.</i>
""")
        else:
            self.send_message(chat_id, "❌ Sorry, there was an error registering your tracking number.")

    def handle_status_command(self, chat_id: str) -> None:
        tracked_items = self.db.get_tracking_by_chat_id(chat_id)
        
        if not tracked_items:
            self.send_message(chat_id, NO_TRACKING_MESSAGE)
            return

        status_message = "📋 <b>Your Tracked Packages</b>\n\n"
        for i, item in enumerate(tracked_items):
            # Only include the footer for the last package
            include_footer = (i == len(tracked_items) - 1)
            status_message += self.format_status_message(*item, include_footer=include_footer)
        
        self.send_message(chat_id, status_message)

    def run(self):
        offset = None
        last_check = 0
        last_status_check = {}
        
        logger.info("Bot started successfully")
        
        while True:
            try:
                current_time = time.time()
                if current_time - last_check >= CHECK_INTERVAL:
                    # Handle new messages
                    updates = self.get_updates(offset)
                    for update in updates:
                        if "message" not in update or "text" not in update["message"]:
                            continue
                            
                        chat_id = str(update["message"]["chat"]["id"])
                        text = update["message"]["text"]
                        offset = update["update_id"] + 1
                        
                        if text.startswith("/"):
                            command, *args = text.split()
                            logger.info(f"Received command: {command} with args: {args}")
                            self.handle_command(chat_id, command, args)
                        else:
                            # If it's not a command, send help message
                            self.send_message(chat_id, HELP_MESSAGE)

                    # Check for status updates
                    tracked_items = self.db.get_all_tracking()
                    for tracking_number, chat_id, current_status, details, last_updated in tracked_items:
                        try:
                            # Create a unique key for this tracking number and chat_id
                            key = f"{tracking_number}_{chat_id}"
                            
                            if key not in last_status_check:
                                last_status_check[key] = {
                                    'status': current_status,
                                    'timestamp': last_updated
                                }
                                continue

                            last_check_data = last_status_check[key]
                            if last_updated > last_check_data['timestamp'] and current_status != last_check_data['status']:
                                message = self.format_status_message(
                                    tracking_number, current_status, details, last_updated
                                )
                                if self.send_message(chat_id, message):
                                    last_status_check[key] = {
                                        'status': current_status,
                                        'timestamp': last_updated
                                    }
                                    logger.info(f"Sent status update for {tracking_number} to {chat_id}: {current_status}")
                        except Exception as e:
                            logger.error(f"Error processing update for {tracking_number}: {str(e)}")
                    
                    # Clean up old entries from last_status_check
                    current_time = time.time()
                    last_status_check = {k: v for k, v in last_status_check.items() 
                                      if current_time - v['timestamp'] < 86400}  # Keep only last 24 hours
                    
                    last_check = current_time
                    logger.debug("Completed status check cycle")

                time.sleep(1)
            except Exception as e:
                logger.error(f"Unexpected error in main loop: {str(e)}")
                time.sleep(5)

# Message templates
HELP_MESSAGE = """
📦 <b>India Post Tracking Bot Help</b>

<b>Available Commands:</b>

/track &lt;tracking_number&gt; - Track a new package
/help - Show this help message
/status - Check status of all tracked packages

<b>Tracking Number Format:</b>
- Speed Post: <code>EU430410927IN</code>
- Registered Post: <code>RM286760959IN</code>
- Air Waybill: <code>AW595537795IN</code>

<b>Format Rules:</b>
1. Start with 2 capital letters:
   - E for Speed Post
   - R for Registered Post
   - A for Air Waybill
2. Have 9 numbers in the middle
3. End with 'IN'
4. No spaces allowed

<b>Example Usage:</b>
<code>/track EU430410927IN</code>

<b>Status Updates:</b>
You will receive automatic updates when your package status changes.

<i>This is an automated message. Please do not reply.</i>
"""

TRACK_USAGE_MESSAGE = """
❌ <b>Invalid Command</b>

Usage: <code>/track &lt;tracking_number&gt;</code>

Example: <code>/track EU430410927IN</code>

<i>This is an automated message. Please do not reply.</i>
"""

INVALID_TRACKING_MESSAGE = """
❌ <b>Invalid Tracking Number Format</b>

Please provide a valid India Post tracking number in the following format:
- Speed Post: <code>EU430410927IN</code>
- Registered Post: <code>RM286760959IN</code>
- Air Waybill: <code>AW595537795IN</code>

The tracking number should:
1. Start with 2 capital letters (E for Speed Post, R for Registered Post, A for Air Waybill)
2. Have 9 numbers in the middle
3. End with 'IN'

<i>This is an automated message. Please do not reply.</i>
"""

NO_TRACKING_MESSAGE = """
📋 <b>No Tracked Packages</b>

You haven't tracked any packages yet. Use <code>/track &lt;tracking_number&gt;</code> to start tracking.

<i>This is an automated message. Please do not reply.</i>
"""

UNKNOWN_COMMAND_MESSAGE = """
❌ <b>Unknown Command</b>

Use <code>/help</code> to see available commands.

<i>This is an automated message. Please do not reply.</i>
"""

if __name__ == "__main__":
    bot = TelegramBot()
    bot.run()