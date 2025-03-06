from flask import Flask, render_template, request, jsonify
from datetime import datetime
import logging
from dotenv import load_dotenv
import os
import requests
import sqlite3
from contextlib import contextmanager
from functools import wraps
import time
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
import re

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
if not TELEGRAM_BOT_TOKEN:
    raise ValueError("TELEGRAM_BOT_TOKEN environment variable is not set")

app = Flask(__name__)
limiter = Limiter(
    app=app,
    key_func=get_remote_address,
    default_limits=["200 per day", "50 per hour"]
)

# Database configuration
DB_PATH = 'tracking.db'
DB_TIMEOUT = 30  # seconds
MAX_RETRIES = 3
RETRY_DELAY = 1  # seconds

# Cache configuration
class Cache:
    def __init__(self):
        self.duration = 60  # seconds
        self.tracking_data = []
        self.tracking_numbers = []
        self.last_update = 0

    def is_fresh(self):
        return time.time() - self.last_update < self.duration

    def update(self, tracking_data=None, tracking_numbers=None):
        if tracking_data is not None:
            self.tracking_data = tracking_data
        if tracking_numbers is not None:
            self.tracking_numbers = tracking_numbers
        self.last_update = time.time()

    def clear(self):
        self.tracking_data = []
        self.tracking_numbers = []
        self.last_update = 0

cache = Cache()

def is_valid_tracking_number(tracking_number):
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

def format_tracking_number(tracking_number):
    """Format tracking number with spaces for better readability"""
    # Remove any existing spaces
    tracking_number = tracking_number.replace(" ", "")
    # Add spaces after first two letters and before last two letters
    return f"{tracking_number[:2]} {tracking_number[2:11]} {tracking_number[11:]}"

def validate_status(status):
    """Validate status is in allowed options"""
    return status in STATUS_OPTIONS or status == 'Custom Status'

@contextmanager
def get_db():
    """Get database connection with proper error handling"""
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT)
        conn.row_factory = sqlite3.Row
        yield conn
    except sqlite3.Error as e:
        logger.error(f"Database error: {str(e)}")
        raise
    finally:
        if conn:
            conn.close()

def db_operation(func):
    """Decorator for database operations with retry logic"""
    @wraps(func)
    def wrapper(*args, **kwargs):
        for attempt in range(MAX_RETRIES):
            try:
                return func(*args, **kwargs)
            except sqlite3.OperationalError as e:
                if attempt == MAX_RETRIES - 1:
                    raise
                logger.warning(f"Database operation failed, retrying... ({attempt + 1}/{MAX_RETRIES})")
                time.sleep(RETRY_DELAY)
    return wrapper

@db_operation
def init_db():
    """Initialize the database, preserving existing data"""
    if not os.path.exists(DB_PATH):
        with get_db() as conn:
            conn.execute("""
                CREATE TABLE tracking (
                    tracking_number TEXT PRIMARY KEY,
                    chat_id TEXT,
                    status TEXT,
                    status_details TEXT,
                    last_updated INTEGER
                )
            """)
            # Create indexes for faster queries
            conn.execute("CREATE INDEX idx_last_updated ON tracking(last_updated)")
            conn.execute("CREATE INDEX idx_chat_id ON tracking(chat_id)")
            logger.info("Created new database with indexes")
    else:
        logger.info("Using existing database")

def send_telegram_message(chat_id, message, retry_count=3):
    """Send a message to a Telegram chat with retry logic"""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "Markdown"
    }
    
    for attempt in range(retry_count):
        try:
            response = requests.post(url, json=payload, timeout=10)
            response.raise_for_status()
            logger.info(f"Successfully sent message to chat_id {chat_id}")
            return True
        except requests.RequestException as e:
            if attempt == retry_count - 1:
                logger.error(f"Failed to send message to chat_id {chat_id}: {str(e)}")
                return False
            logger.warning(f"Telegram API request failed, retrying... ({attempt + 1}/{retry_count})")
            time.sleep(1)
    return False

def format_status_message(tracking_number, status, details, last_updated):
    """Format the status message with emojis and better structure"""
    status_emojis = {
        'Order Placed': '📦',
        'Processing': '⚙️',
        'Picked Up': '🚚',
        'In Transit': '✈️',
        'Out for Delivery': '🚛',
        'Delivered': '✅',
        'Failed Delivery': '❌',
        'Returned': '↩️',
    }
    
    emoji = status_emojis.get(status, '📋')
    last_updated_str = datetime.fromtimestamp(last_updated).strftime('%Y-%m-%d %H:%M:%S')
    
    message = f"""
📦 *Package Status Update*

Tracking Number: `{tracking_number}`
Status: {emoji} *{status}*
Time: {last_updated_str}

{details}

_This is an automated message. Please do not reply._
"""
    return message

# Predefined status options
STATUS_OPTIONS = {
    'Order Placed': 'Your order has been placed and is being processed.',
    'Processing': 'Your package is being processed at our facility.',
    'Picked Up': 'Your package has been picked up by our courier.',
    'In Transit': 'Your package is on its way to the destination.',
    'Out for Delivery': 'Your package is out for delivery today.',
    'Delivered': 'Your package has been successfully delivered.',
    'Failed Delivery': 'Delivery was attempted but unsuccessful.',
    'Returned': 'Your package has been returned to sender.',
    'Custom Status': 'Enter a custom status message'
}

@app.route('/')
def index():
    return render_template('index.html', status_options=STATUS_OPTIONS)

@app.route('/api/tracking', methods=['GET'])
@limiter.limit("30 per minute")
@db_operation
def get_tracking():
    try:
        # Return cached data if it's still fresh
        if cache.is_fresh() and cache.tracking_data:
            return jsonify(cache.tracking_data)

        with get_db() as conn:
            cursor = conn.execute("""
                SELECT tracking_number, status, status_details, last_updated 
                FROM tracking 
                ORDER BY last_updated DESC
            """)
            items = cursor.fetchall()
            result = [{
                'tracking_number': item['tracking_number'],
                'status': item['status'],
                'status_details': item['status_details'],
                'last_updated': datetime.fromtimestamp(item['last_updated']).strftime('%Y-%m-%d %H:%M:%S')
            } for item in items]
            
            # Update cache
            cache.update(tracking_data=result)
            
            return jsonify(result)
    except Exception as e:
        logger.error(f"Error fetching tracking items: {str(e)}")
        return jsonify({'error': 'Failed to fetch tracking items'}), 500

@app.route('/api/tracking/numbers', methods=['GET'])
@limiter.limit("30 per minute")
@db_operation
def get_tracking_numbers():
    try:
        # Return cached data if it's still fresh
        if cache.is_fresh() and cache.tracking_numbers:
            return jsonify(cache.tracking_numbers)

        with get_db() as conn:
            cursor = conn.execute("SELECT tracking_number FROM tracking ORDER BY tracking_number")
            numbers = [row['tracking_number'] for row in cursor.fetchall()]
            
            # Update cache
            cache.update(tracking_numbers=numbers)
            
            return jsonify(numbers)
    except Exception as e:
        logger.error(f"Error fetching tracking numbers: {str(e)}")
        return jsonify({'error': 'Failed to fetch tracking numbers'}), 500

@app.route('/api/tracking/update', methods=['POST'])
@limiter.limit("10 per minute")
@db_operation
def update_tracking():
    try:
        data = request.json
        tracking_number = data.get('tracking_number')
        new_status = data.get('status')
        status_details = data.get('status_details')

        # Input validation
        if not tracking_number or not new_status:
            return jsonify({'error': 'Missing required fields'}), 400
        
        if not is_valid_tracking_number(tracking_number):
            return jsonify({
                'error': 'Invalid tracking number format',
                'details': 'Please provide a valid India Post tracking number in the format: XX 123 456 789 IN'
            }), 400
            
        if not validate_status(new_status):
            return jsonify({'error': 'Invalid status'}), 400

        with get_db() as conn:
            # Get current status
            cursor = conn.execute(
                "SELECT status FROM tracking WHERE tracking_number = ?",
                (tracking_number,)
            )
            result = cursor.fetchone()
            
            if not result:
                return jsonify({'error': 'Tracking number not found'}), 404

            # Update status
            current_time = int(datetime.now().timestamp())
            conn.execute("""
                UPDATE tracking 
                SET status = ?, status_details = ?, last_updated = ? 
                WHERE tracking_number = ?
            """, (new_status, status_details, current_time, tracking_number))
            conn.commit()

            # Clear cache
            cache.clear()

            logger.info(f"Updated status for {tracking_number} to {new_status}")
            return jsonify({'message': 'Status updated successfully'})
    except Exception as e:
        logger.error(f"Error updating status: {str(e)}")
        return jsonify({'error': 'Failed to update status'}), 500

if __name__ == '__main__':
    init_db()
    app.run(debug=True) 