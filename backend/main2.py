import imaplib
import email
import asyncio
import re
import logging
import os
import psycopg2
from datetime import datetime
import psycopg2.extras
from dotenv import load_dotenv
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, HTTPException, Depends, Header
from pydantic import BaseModel
from dataclasses import dataclass
from typing import Optional, Dict, List
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from email.header import decode_header
import time
from fastapi.responses import JSONResponse
import random

# Load environment variables
load_dotenv()

# Get database URL and secret key from environment variables
DATABASE_URL = os.getenv("DATABASE_URL")
SECRET_KEY = os.getenv("SECRET_KEY", "your_secret_key")
ALGORITHM = "HS256"

# OAuth2 setup
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/login")

router = APIRouter()

# Log streams for each bot
bot_logs = {}

# Active bots dictionary
active_bots: Dict[str, 'Bot'] = {}


from backend.types import Bot, TradeSignal


class BotConfigRequest(BaseModel):
    name: str
    exchange: str
    symbol: str
    quantity: float = 1.0

    # API fields for standard exchanges
    api_key: str | None = None
    api_secret: str | None = None
    account_id: str | None = None

    # MetaTrader5-specific fields
    login: str | None = None
    password: str | None = None
    server: str | None = None
    slopping: int | None = None
    deviation: int | None = None
    magic_number: int | None = None

    # Email configuration fields
    email_address: str  # Changed from email to email_address
    email_password: str
    imap_server: str
    email_subject: str | None = None


def get_db_connection():
    """Get a connection to the PostgreSQL database."""
    try:
        conn = psycopg2.connect(DATABASE_URL)
        return conn
    except Exception as e:
        logging.error(f"Database connection error: {str(e)}")
        raise HTTPException(status_code=500,
                            detail="Could not connect to database")


def normalize_symbol(symbol: str) -> str:
    return re.sub(r'\W+', '', symbol.upper())


def log_message(bot_name: str, message: str):
    """Log a message for a specific bot."""
    if bot_name not in bot_logs:
        bot_logs[bot_name] = []

    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    formatted_message = f"[{timestamp}] {message}"
    bot_logs[bot_name].append(formatted_message)
    logging.info(f"Bot {bot_name}: {message}")


def connect_imap(bot: Bot):
    """Establish a secure IMAP connection for a bot."""
    try:
        log_message(
            bot.name,
            f"📩 Connecting to IMAP server {bot.imap_server} for bot '{bot.name}'..."
        )
        bot.imap_session = imaplib.IMAP4_SSL(bot.imap_server)
        status, response = bot.imap_session.login(bot.email_address,
                                                  bot.email_password)

        if status != "OK":
            raise Exception(f"Login failed: {response}")

        status, mailbox = bot.imap_session.select("inbox")
        if status != "OK":
            raise Exception("Failed to select INBOX.")

        log_message(
            bot.name,
            f"✅ IMAP session established successfully for bot '{bot.name}'! Inbox selected."
        )
        return True
    except Exception as e:
        log_message(bot.name, f"⚠️ IMAP connection failed: {str(e)}")
        bot.imap_session = None
        return False


def decode_email_subject(subject):
    """Decode email subject line to proper text format.

    Args:
        subject (str): Raw email subject possibly with encoding

    Returns:
        str: Decoded subject text
    """
    if not subject:
        return ""

    # If subject is already a string, return it
    if isinstance(subject, str):
        return subject

    # Handle encoded email headers
    decoded_header = decode_header(subject)
    decoded_parts = []

    for content, encoding in decoded_header:
        if isinstance(content, bytes):
            # Try with provided encoding or default to utf-8, with fallback to latin-1
            if encoding:
                try:
                    decoded_parts.append(content.decode(encoding))
                except (UnicodeDecodeError, LookupError):
                    try:
                        decoded_parts.append(content.decode('utf-8'))
                    except UnicodeDecodeError:
                        decoded_parts.append(
                            content.decode('latin-1', errors='replace'))
            else:
                try:
                    decoded_parts.append(content.decode('utf-8'))
                except UnicodeDecodeError:
                    decoded_parts.append(
                        content.decode('latin-1', errors='replace'))
        else:
            decoded_parts.append(str(content))

    return ''.join(decoded_parts)


def get_email_body(msg):
    """Extract the body text from an email message.

    Args:
        msg (email.message.Message): Email message object

    Returns:
        str: Plain text body of the email
    """
    body = ""

    if msg.is_multipart():
        # Handle multipart messages
        for part in msg.get_payload():
            if part.get_content_type() == "text/plain":
                try:
                    charset = part.get_content_charset() or 'utf-8'
                    body = part.get_payload(decode=True).decode(
                        charset, errors='replace')
                    break
                except Exception as e:
                    logging.error(f"Error decoding email part: {str(e)}")
            elif part.is_multipart():
                # Handle nested multipart messages
                nested_body = get_email_body(part)
                if nested_body:
                    body = nested_body
                    break
    else:
        # Handle non-multipart messages
        if msg.get_content_type() == "text/plain":
            try:
                charset = msg.get_content_charset() or 'utf-8'
                body = msg.get_payload(decode=True).decode(charset,
                                                           errors='replace')
            except Exception as e:
                logging.error(f"Error decoding email body: {str(e)}")
        elif msg.get_content_type() == "text/html":
            try:
                charset = msg.get_content_charset() or 'utf-8'
                body = msg.get_payload(decode=True).decode(charset,
                                                           errors='replace')
                # Could add HTML to plain text conversion here if needed
            except Exception as e:
                logging.error(f"Error decoding HTML email: {str(e)}")

    return body


async def check_email_for_signals():
    """Check unread emails in the inbox for trade signals for each bot, prioritizing newest first."""
    # Import bot_manager at the function level to avoid circular imports
    from backend import bot_manager

    while True:
        for bot_name, bot in active_bots.items():
            # Skip paused bots
            if bot.paused:
                # Only log this once in a while to avoid spamming logs
                if random.random() < 0.01:  # ~1% chance to log
                    log_message(bot_name,
                                "⏸️ Bot is paused, skipping email check")
                continue

            # Only try to reconnect if bot is not paused
            if not bot.paused and not bot.imap_session:
                log_message(bot_name,
                            "⚠️ IMAP session inactive. Reconnecting...")
                if not connect_imap(bot):
                    log_message(
                        bot_name,
                        "⚠️ Failed to reconnect to IMAP, will retry later")
                    await asyncio.sleep(
                        5)  # Wait a bit before trying other bots
                continue

            try:
                # Check pause state AGAIN before search
                if bot.paused:
                    continue

                # Search for unread emails in newest-first order
                try:
                    # First try with SORT command which is more reliable for sorting
                    status, messages = bot.imap_session.sort(
                        'REVERSE DATE', 'UTF-8', 'UNSEEN')
                except Exception as e:
                    log_message(
                        bot_name,
                        f"⚠️ SORT command failed, falling back to standard search: {str(e)}"
                    )
                    # Fallback to basic search without date sorting (IMAP servers without SORT capability)
                    status, messages = bot.imap_session.search(
                        None, "(UNSEEN)")

                if status != "OK":
                    log_message(bot_name, "⚠️ IMAP search failed.")
                    continue

                # Process the message IDs
                if isinstance(messages[0], bytes):
                    # Handle the standard search result format
                    unread_ids = messages[0].split()
                else:
                    # Handle the SORT command result format
                    unread_ids = messages

                log_message(
                    bot_name,
                    f"📥 Found {len(unread_ids)} unread emails to process")

                for num in unread_ids:
                    # Check pause state AGAIN before each email
                    if bot.paused:
                        break

                    status, msg_data = bot.imap_session.fetch(num, "(RFC822)")

                    if status != "OK" or not msg_data or not msg_data[0]:
                        log_message(bot_name, f"⚠️ Failed to fetch email")
                        continue

                    msg = email.message_from_bytes(msg_data[0][1])

                    # Extract subject, date, and body
                    subject = decode_email_subject(msg.get("Subject", ""))
                    date_str = msg.get("Date", "Unknown date")

                    # Get email body
                    body = get_email_body(msg)
                    if not body:
                        log_message(
                            bot_name,
                            "⚠️ Could not extract email body. Skipping.")
                        continue

                    # Check for subject match using email_subject, symbol, or order fill alerts
                    should_process = False
                    if "order" in subject.lower() and any(symbol in subject.upper() for symbol in [bot.symbol, "GBPUSD", "NZDUSD", "XAUUSD"]):
                        should_process = True
                        log_message(
                            bot_name,
                            f"📊 Order fill detected: {subject}"
                        )
                    elif bot.email_subject and bot.email_subject.strip():
                        # If email_subject is specified in database, check if it's in the subject
                        if bot.email_subject.lower() in subject.lower():
                            should_process = True
                            log_message(
                                bot_name,
                                f"📄 Email subject match found: '{bot.email_subject}'"
                            )
                    else:
                        # Fallback to symbol matching if no email_subject is specified
                        normalized_symbol = normalize_symbol(bot.symbol)
                        if normalized_symbol.lower() in subject.lower():
                            should_process = True
                            log_message(
                                bot_name,
                                f"📄 Symbol match found in subject: '{normalized_symbol}'"
                            )

                    if should_process:
                        log_message(
                            bot_name,
                            f"📄 Processing email body for trading signals...")

                        # Process the body for buy/sell signals
                        body_lower = body.lower()

                        # Define the signal based on keywords in the body
                        action = None
                        if re.search(r'\b(buy|demand)\b', body_lower):
                            action = "buy"
                            log_message(
                                bot_name,
                                "🔍 BUY signal detected in the email body!")
                        elif re.search(r'\b(sell|supply)\b', body_lower):
                            action = "sell"
                            log_message(
                                bot_name,
                                "🔍 SELL signal detected in the email body!")

                        if action:
                            # Check for position conflict
                            if bot.position != "neutral" and bot.position != action:
                                log_message(
                                    bot_name,
                                    f"🔁 Signal conflict detected: Closing '{bot.position}' positions to switch to '{action}'."
                                )

                                try:
                                    # Close existing position before switching
                                    close_signal = TradeSignal(
                                        action="close",
                                        symbol=bot.symbol,
                                        quantity=bot.quantity)
                                    close_result = await bot_manager.close_position(
                                        bot, close_signal)
                                    log_message(
                                        bot_name,
                                        f"🔒 Closed '{bot.position}' position: {close_result}"
                                    )

                                    # Update position to neutral after closing
                                    bot.position = "neutral"
                                except Exception as e:
                                    log_message(
                                        bot_name,
                                        f"❌ Failed to close position: {str(e)}"
                                    )
                                    continue

                            # Create a trade signal
                            signal = TradeSignal(action=action,
                                                 symbol=bot.symbol,
                                                 quantity=bot.quantity)

                            try:
                                # Execute the trade
                                log_message(
                                    bot_name,
                                    f"🚀 Executing {action.upper()} order for {bot.symbol}..."
                                )
                                result = await bot_manager.place_trade(
                                    bot, signal)

                                log_message(
                                    bot_name, f"""
                                ✅ Trade executed successfully: {action.upper()} {bot.symbol}
                                📅 Email Date: {date_str}  
                                📨 Subject: {subject}  
                                📝 Body: {body[:500]}{'...' if len(body) > 500 else ''}
                                """)

                                # Update bot position
                                bot.position = action

                                # Mark email as seen since we found and executed a valid signal
                                if bot.imap_session:
                                    try:
                                        bot.imap_session.store(
                                            num, '+FLAGS', '\\Seen')
                                        log_message(
                                            bot_name,
                                            f"📧 Marked email as seen after successful trade"
                                        )
                                    except Exception as e:
                                        log_message(
                                            bot_name,
                                            f"⚠️ Failed to mark email as seen: {str(e)}"
                                        )
                                        # Try to reconnect
                                        reconnect_bot(bot)

                            except Exception as e:
                                log_message(
                                    bot_name, f"""
                                ❌ Trade failed: {str(e)}  
                                📅 Email Date: {date_str}  
                                📨 Subject: {subject}  
                                📝 Body: {body[:500]}{'...' if len(body) > 500 else ''}
                                """)

                                # Mark email as UNSEEN if trade failed
                                if bot.imap_session:
                                    try:
                                        # Remove the \Seen flag to mark as unread again
                                        bot.imap_session.store(
                                            num, '-FLAGS', '\\Seen')
                                        log_message(
                                            bot_name,
                                            f"📧 Marked email as UNSEEN again (trade failed)"
                                        )
                                    except Exception as e:
                                        log_message(
                                            bot_name,
                                            f"⚠️ Failed to mark email as unseen: {str(e)}"
                                        )
                                        # Try to reconnect
                                        reconnect_bot(bot)
                        else:
                            log_message(
                                bot_name, f"""
                            🚫 No valid trade signal found in email.  
                            📅 Email Date: {date_str}  
                            📨 Subject: {subject}  
                            📝 Body: {body[:500]}{'...' if len(body) > 500 else ''}
                            """)

                            # Mark email as UNSEEN again so it remains unread for the user
                            if bot.imap_session:
                                try:
                                    # Remove the \Seen flag to mark as unread again
                                    bot.imap_session.store(
                                        num, '-FLAGS', '\\Seen')
                                    log_message(
                                        bot_name,
                                        f"📧 Marked email as UNSEEN again (no valid signal)"
                                    )
                                except Exception as e:
                                    log_message(
                                        bot_name,
                                        f"⚠️ Failed to mark email as unseen: {str(e)}"
                                    )
                                    # Try to reconnect
                                    reconnect_bot(bot)
                    else:
                        log_message(
                            bot_name, f"""
                        🚫 Email ignored — Subject mismatch.  
                        📅 Email Date: {date_str}  
                        📨 Subject: {subject}  
                        📝 Body: {body[:500]}{'...' if len(body) > 500 else ''}
                        """)

                        # Mark email as UNSEEN again so it remains unread for the user
                        if bot.imap_session:
                            try:
                                # Remove the \Seen flag to mark as unread again
                                bot.imap_session.store(num, '-FLAGS', '\\Seen')
                                log_message(
                                    bot_name,
                                    f"📧 Marked email as UNSEEN again (subject mismatch)"
                                )
                            except Exception as e:
                                log_message(
                                    bot_name,
                                    f"⚠️ Failed to mark email as unseen: {str(e)}"
                                )
                                # Try to reconnect
                                reconnect_bot(bot)

                    # If there's an error with the IMAP session, break and try to reconnect
                    if not bot.imap_session:
                        log_message(bot_name,
                                    "⚠️ IMAP session lost during processing")
                        reconnect_bot(bot)
                        break

            except Exception as e:
                log_message(bot_name, f"⚠️ Email check failed: {str(e)}")
                bot.imap_session = None

        await asyncio.sleep(1)


async def keep_imap_alive():
    """Keep each bot's IMAP session alive."""
    while True:
        try:
            for bot_name, bot in active_bots.items():
                # Skip IMAP checks for paused bots
                if bot.paused:
                    continue

                if bot.imap_session:
                    try:
                        status, response = bot.imap_session.noop()
                        if status == "OK":
                            log_message(bot_name,
                                        "✅ IMAP connection is healthy")
                        else:
                            log_message(
                                bot_name,
                                "⚠️ IMAP connection appears stale, reconnecting..."
                            )
                            # Close old connection if possible
                            try:
                                bot.imap_session.close()
                                bot.imap_session.logout()
                            except:
                                pass
                            # Reconnect
                            connect_imap(bot)
                    except Exception as e:
                        log_message(bot_name,
                                    f"⚠️ Error in IMAP keep-alive: {str(e)}")
                        # Reconnect on error
                        connect_imap(bot)

            # Check every 5 minutes
            await asyncio.sleep(30)
        except Exception as e:
            logging.error(f"Error in keep_imap_alive: {str(e)}")
            await asyncio.sleep(60)  # Shorter sleep on error


def reconnect_bot(bot):
    """Explicitly reconnect a bot's IMAP session."""
    try:
        # Close any existing connection first
        if bot.imap_session:
            try:
                bot.imap_session.close()
                bot.imap_session.logout()
            except:
                pass
            bot.imap_session = None

        # Attempt to establish a new connection
        if connect_imap(bot):
            log_message(bot.name,
                        "✅ Bot IMAP connection re-established successfully")
            return True
        else:
            log_message(bot.name,
                        "❌ Failed to re-establish bot IMAP connection")
            return False
    except Exception as e:
        log_message(bot.name, f"❌ Error reconnecting bot: {str(e)}")
        return False


@router.on_event("startup")
async def start_tasks():
    """Initialize tasks that run on application startup."""
    logging.info("🚀 Starting background tasks...")
    asyncio.create_task(keep_imap_alive())
    asyncio.create_task(check_email_for_signals())
    asyncio.create_task(startup_check_emails())


# Modify the startup_check_emails function to load the paused state
async def startup_check_emails():
    """Initial check for all active bots on startup."""
    logging.info("📨 Performing initial email check for all bots...")

    # Retrieve bots from database
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

        # Add the paused column to the bots table if it doesn't exist
        try:
            cursor.execute(
                "ALTER TABLE bots ADD COLUMN IF NOT EXISTS paused BOOLEAN DEFAULT FALSE"
            )
            conn.commit()
            logging.info(
                "✅ Added 'paused' column to bots table if it didn't exist")
        except Exception as e:
            logging.error(f"Error adding paused column: {str(e)}")
            conn.rollback()

        cursor.execute("SELECT * FROM bots")
        bots_data = cursor.fetchall()
        conn.close()

        # Initialize and start each bot
        for bot_data in bots_data:
            bot_name = bot_data["name"]  # Use consistent column name

            # Create bot instance if not already active
            if bot_name not in active_bots:
                # Check if the paused column exists in the result
                paused_state = bot_data.get("paused", False)

                bot = Bot(
                    name=bot_data["name"],
                    exchange=bot_data["exchange"],
                    symbol=bot_data["symbol"],
                    quantity=bot_data["quantity"],
                    email_address=bot_data["email_address"],
                    email_password=bot_data["email_password"],
                    imap_server=bot_data["imap_server"],
                    email_subject=bot_data["email_subject"],
                    api_key=bot_data["api_key"],
                    api_secret=bot_data["api_secret"],
                    account_id=bot_data["account_id"],
                    paused=paused_state  # Set the paused state from the database
                )

                # Only establish IMAP connection if bot is not paused
                if not paused_state:
                    if connect_imap(bot):
                        log_message(
                            bot_name,
                            f"✅ Bot loaded from database (active) and connected to IMAP"
                        )
                    else:
                        log_message(
                            bot_name,
                            "⚠️ Bot loaded from database but failed to connect to IMAP"
                        )
                else:
                    log_message(
                        bot_name,
                        f"✅ Bot loaded from database (paused) - IMAP connection skipped"
                    )
                active_bots[bot_name] = bot

        logging.info(f"✅ Initialized {len(active_bots)} bots from database")
    except Exception as e:
        logging.error(f"Error initializing bots on startup: {str(e)}")


async def get_current_user(authorization: str = Header(None)):
    """Extract user from the Authorization header."""
    if not authorization or not authorization.startswith("Bearer "):
        logging.warning("❌ No Bearer token found in header!")
        raise HTTPException(status_code=401, detail="Bearer token missing")

    token = authorization.split("Bearer ")[1]
    logging.info(f"✅ Token extracted: {token[:10]}...")

    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        email = payload.get("sub")
        if not email:
            logging.warning("❌ 'sub' (email) missing from token!")
            raise HTTPException(status_code=401, detail="Invalid token")
        logging.info(f"✅ Successfully authenticated user: {email}")
        return {"email": email}
    except JWTError as e:
        logging.error(f"❌ JWT Error: {str(e)}")
        raise HTTPException(status_code=401,
                            detail="Could not validate credentials")


@router.post("/create-bot")
async def create_bot(config: BotConfigRequest,
                     current_user: dict = Depends(get_current_user)):
    """Create a trading bot and save to the database with user email."""
    conn = None
    try:
        user_email = current_user["email"]
        logging.info(f"✅ Creating bot for authenticated user: {user_email}")

        # Create a database connection
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

        # Check if bot name already exists
        cursor.execute("SELECT name FROM bots WHERE name = %s",
                       (config.name, ))
        if cursor.fetchone():
            conn.close()
            return JSONResponse(status_code=400,
                                content={"detail": "Bot name already exists"})

        # Validate MetaTrader5 fields if needed
        if config.exchange.lower() == "metatrader5":
            required_fields = [
                config.login, config.password, config.server, config.slopping,
                config.deviation, config.magic_number
            ]
            if not all(required_fields):
                conn.close()
                return JSONResponse(
                    status_code=400,
                    content={
                        "detail":
                        "MetaTrader5 bot requires login, password, server, slopping, deviation, and magic_number."
                    })

        # Create a temporary bot instance for IMAP connection testing
        temp_bot = Bot(name=config.name,
                       exchange=config.exchange,
                       symbol=config.symbol,
                       quantity=config.quantity,
                       email_address=config.email_address,
                       email_password=config.email_password,
                       imap_server=config.imap_server,
                       email_subject=config.email_subject,
                       api_key=config.api_key,
                       api_secret=config.api_secret,
                       account_id=config.account_id,
                       login=config.login,
                       password=config.password,
                       server=config.server,
                       slopping=config.slopping,
                       deviation=config.deviation,
                       magic_number=config.magic_number)

        # Test IMAP connection
        if not connect_imap(temp_bot):
            conn.close()
            return JSONResponse(
                status_code=400,
                content={"detail": "Failed to connect to the IMAP server."})

        # Insert bot into the database with consistent column names
        cursor.execute(
            """
            INSERT INTO bots (
                name, exchange, symbol, quantity, 
                email_address, email_password, imap_server, email_subject,
                api_key, api_secret, account_id, user_email
            ) VALUES (
                %s, %s, %s, %s, 
                %s, %s, %s, %s,
                %s, %s, %s, %s
            )
        """, (config.name, config.exchange, config.symbol, config.quantity,
              config.email_address, config.email_password, config.imap_server,
              config.email_subject, config.api_key, config.api_secret,
              config.account_id, user_email))

        conn.commit()
        conn.close()
        conn = None  # Set to None to avoid "connection already closed" error

        log_message(
            temp_bot.name,
            f"✅ Bot '{temp_bot.name}' created by {user_email} and saved to database!"
        )

        # Store the bot in active_bots
        active_bots[temp_bot.name] = temp_bot

        # Instead of calling monitor_emails, use the existing check_email_for_signals function
        # This function is already running in the background
        # No need to start a separate task for each bot

        return {
            "message":
            f"Bot '{config.name}' created successfully for {config.exchange}!",
            "botName": config.name,
            "userEmail": user_email
        }
    except psycopg2.IntegrityError as e:
        logging.error(f"Database integrity error: {str(e)}")
        if conn is not None:
            conn.rollback()
            conn.close()
        return JSONResponse(status_code=400,
                            content={"detail": f"Database error: {str(e)}"})
    except Exception as e:
        logging.error(f"Error creating bot: {str(e)}")
        if conn is not None:
            try:
                conn.rollback()
                conn.close()
            except:
                pass
        return JSONResponse(
            status_code=500,
            content={"detail": f"Failed to create bot: {str(e)}"})


# Add a new endpoint to toggle bot pause state
@router.post("/toggle-bot/{bot_name}")
async def toggle_bot(bot_name: str,
                     current_user: dict = Depends(get_current_user)):
    try:
        user_email = current_user["email"]

        # Verify the bot exists and belongs to the user
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        cursor.execute(
            "SELECT * FROM bots WHERE name = %s AND user_email = %s",
            (bot_name, user_email))
        bot_data = cursor.fetchone()

        if not bot_data:
            conn.close()
            return JSONResponse(
                status_code=404,
                content={
                    "detail":
                    f"Bot '{bot_name}' not found or doesn't belong to you"
                })

        # Check if the bot is in active_bots
        if bot_name not in active_bots:
            # Bot exists in DB but not in active_bots, let's add it
            bot = Bot(
                name=bot_data["name"],
                exchange=bot_data["exchange"],
                symbol=bot_data["symbol"],
                quantity=bot_data["quantity"],
                email_address=bot_data["email_address"],
                email_password=bot_data["email_password"],
                imap_server=bot_data["imap_server"],
                email_subject=bot_data["email_subject"],
                api_key=bot_data["api_key"],
                api_secret=bot_data["api_secret"],
                account_id=bot_data["account_id"],
                paused=False  # Default to not paused
            )

            # Connect to IMAP
            connect_imap(bot)
            active_bots[bot_name] = bot
            log_message(bot_name, f"Bot activated by user {user_email}")

            conn.close()
            return {
                "message": f"Bot '{bot_name}' has been activated",
                "paused": False
            }

        # Get the new paused state (toggle current state)
        new_paused_state = not active_bots[bot_name].paused

        # Update the paused state immediately
        active_bots[bot_name].paused = new_paused_state

        # If pausing, immediately close IMAP connection
        if new_paused_state:
            if active_bots[bot_name].imap_session:
                try:
                    log_message(
                        bot_name,
                        "Forcefully closing IMAP session due to pause")
                    active_bots[bot_name].imap_session.close()
                    active_bots[bot_name].imap_session.logout()
                except Exception as e:
                    log_message(bot_name,
                                f"Error during forced IMAP logout: {str(e)}")
                finally:
                    # Always set to None even if close/logout failed
                    active_bots[bot_name].imap_session = None
                    log_message(
                        bot_name,
                        "IMAP session set to None - bot is now fully paused")
        # If resuming, re-establish the connection
        elif not active_bots[bot_name].imap_session:
            if connect_imap(active_bots[bot_name]):
                log_message(bot_name,
                            "IMAP session re-established after resume")
            else:
                log_message(
                    bot_name,
                    "Failed to re-establish IMAP session after resume")

        # Update the database
        cursor.execute("UPDATE bots SET paused = %s WHERE name = %s",
                       (new_paused_state, bot_name))
        conn.commit()
        conn.close()

        state = "paused" if new_paused_state else "resumed"
        return {
            "message": f"Bot '{bot_name}' has been {state}",
            "paused": new_paused_state
        }
    except Exception as e:
        if 'conn' in locals() and conn:
            conn.close()
        log_message(bot_name, f"Error toggling bot state: {str(e)}")
        raise HTTPException(status_code=500,
                            detail=f"Failed to toggle bot: {str(e)}")


@router.websocket("/ws/logs/{bot_name}")
async def websocket_logs(websocket: WebSocket, bot_name: str):
    """WebSocket to stream logs for a specific bot."""
    await websocket.accept()
    last_sent_message = ""  # Keep track of the last sent message

    try:
        # Send initial connection message
        await websocket.send_text(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Connected to logs for bot '{bot_name}'")

        while True:
            # Send any queued messages from bot_logs
            if bot_name in bot_logs and bot_logs[bot_name]:
                # Get next message
                message = bot_logs[bot_name][0]

                # Only send if not a duplicate
                if message != last_sent_message:
                    # Remove from queue and send
                    bot_logs[bot_name].pop(0)
                    await websocket.send_text(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {message}")
                    last_sent_message = message
                else:
                    # Skip duplicate but remove from queue
                    bot_logs[bot_name].pop(0)

            # Small delay to prevent excessive CPU usage
            await asyncio.sleep(0.1)

            # Check if there are more messages to process
            if bot_name in bot_logs and bot_logs[bot_name]:
                message = bot_logs[bot_name][0]
                is_paused_message = "⏸️ Bot is paused" in message

                # Only send message if it's not a repeated pause message
                if not (is_paused_message
                        and "Bot is paused" in last_sent_message):
                    # Remove from the queue and send
                    bot_logs[bot_name].pop(0)
                    try:
                        await websocket.send_text(message)
                        last_sent_message = message  # Update the last sent message
                        logging.info(f"Sent log: {message}")
                    except RuntimeError:
                        logging.warning(
                            "WebSocket closed before sending message.")
                        break
                else:
                    # Skip repeated pause message but still remove it from the queue
                    bot_logs[bot_name].pop(0)
            await asyncio.sleep(0.5)
    except WebSocketDisconnect:
        logging.warning(f"WebSocket disconnected for bot {bot_name}")
    except Exception as e:
        logging.error(f"WebSocket Error: {e}")
    finally:
        try:
            await websocket.close()
        except Exception:
            logging.info(f"WebSocket already closed for bot {bot_name}")
        logging.info(f"WebSocket connection closed for bot {bot_name}")


@router.get("/get-bots")
async def get_bots(current_user: dict = Depends(get_current_user)):
    """Retrieve all bots from the database for the authenticated user with all columns."""
    try:
        user_email = current_user["email"]
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

        # Check if bots table exists
        cursor.execute("""
            SELECT EXISTS (
                SELECT FROM information_schema.tables 
                WHERE table_name = 'bots'
            );
        """)
        table_exists = cursor.fetchone()[0]

        if not table_exists:
            # Create bots table if it doesn't exist
            cursor.execute("""
                CREATE TABLE bots (
                    id SERIAL PRIMARY KEY,
                    name VARCHAR(100) UNIQUE NOT NULL,
                    exchange VARCHAR(50) NOT NULL,
                    symbol VARCHAR(50) NOT NULL,
                    quantity FLOAT NOT NULL,
                    email_address VARCHAR(100) NOT NULL,
                    email_password VARCHAR(200) NOT NULL,
                    imap_server VARCHAR(100) NOT NULL,
                    email_subject VARCHAR(200) NOT NULL,
                    api_key VARCHAR(200),
                    api_secret VARCHAR(200),
                    account_id VARCHAR(100),
                    user_email VARCHAR(100) NOT NULL,
                    paused BOOLEAN DEFAULT FALSE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)
            conn.commit()

        # Select all columns from the bots table for the authenticated user
        cursor.execute("SELECT * FROM bots WHERE user_email = %s",
                       (user_email, ))
        bots = cursor.fetchall()
        conn.close()

        # Convert to list of dictionaries
        bots_list = []
        for bot in bots:
            bot_dict = dict(bot)
            # Mask sensitive information
            if "email_password" in bot_dict:
                bot_dict["email_password"] = "********"
            if "api_key" in bot_dict:
                bot_dict["api_key"] = "********"
            if "api_secret" in bot_dict:
                bot_dict["api_secret"] = "********"
            if "password" in bot_dict:
                bot_dict["password"] = "********"

            # Add status information if the bot is active
            if bot_dict["name"] in active_bots:
                active_bot = active_bots[bot_dict["name"]]
                bot_dict["status"] = "active" if (
                    active_bot.monitoring_task
                    and not active_bot.monitoring_task.done()) else "stopped"
                bot_dict["position"] = active_bot.position
                bot_dict[
                    "paused"] = active_bot.paused  # Include the paused state
            else:
                bot_dict["status"] = "stopped"
                bot_dict["position"] = "neutral"
                bot_dict["paused"] = bot_dict.get(
                    "paused", False)  # Default to False if not present

            bots_list.append(bot_dict)

        return {"bots": bots_list}
    except Exception as e:
        if 'conn' in locals() and conn:
            conn.close()
        raise HTTPException(status_code=500,
                            detail=f"Failed to retrieve bots: {str(e)}")


@router.get("/status")
async def status():
    return {"message": "Trading Bot is running with database integration!"}

async def place_trade(bot, signal):
    exchange = bot.exchange.lower()
    log_message(bot.name, f"🔍 Attempting trade with exchange: '{exchange}'")
    
    try:
        # Check trade limits for free plan
        conn = get_db_connection()
        cur = conn.cursor()
        
        # First check if the user exists and has a subscription plan
        cur.execute("""
            SELECT subscription_plan, trade_count, email 
            FROM users WHERE email = (
                SELECT user_email FROM bots WHERE name = %s
            )""", (bot.name,))
        
        user_data = cur.fetchone()
        log_message(bot.name, f"📊 User data before trade: {user_data}")
        
        if user_data:
            user_email = user_data[2]
            if user_data[0] == 'free':
                trade_count = user_data[1] or 0
                if trade_count >= 4:
                    log_message(bot.name, "❌ Trade limit reached for free plan. Bot will be paused.")
                    cur.execute("UPDATE bots SET paused = TRUE WHERE name = %s", (bot.name,))
                    bot.paused = True
                    conn.commit()
                    cur.close()
                    conn.close()
                    raise Exception("Trade limit reached for free plan")
        else:
            log_message(bot.name, "⚠️ Warning: User not found in database")
            
        cur.close()
        conn.close()
        
        # Execute the trade logic here
        await asyncio.sleep(1)
        
        # Update trade count AFTER successful trade execution
        conn = get_db_connection()
        cur = conn.cursor()
        
        if user_data:
            log_message(bot.name, f"📝 Updating trade count for user {user_email}")
            cur.execute("""
                UPDATE users 
                SET trade_count = COALESCE(trade_count, 0) + 1 
                WHERE email = %s
                RETURNING trade_count
            """, (user_email,))
            
            new_count = cur.fetchone()
            log_message(bot.name, f"📊 New trade count: {new_count}")
            conn.commit()
            
            # Double-check the update
            cur.execute("SELECT trade_count FROM users WHERE email = %s", (user_email,))
            verified_count = cur.fetchone()
            log_message(bot.name, f"✅ Verified trade count in database: {verified_count}")
        
        cur.close()
        conn.close()
        
        log_message(bot.name, f"✅ Order placed successfully: {signal.action.upper()} {signal.symbol}")
        return {"status": "success", "message": f"Order placed: {signal.action} {signal.symbol}"}
    
    except Exception as e:
        log_message(bot.name, f"❌ Error in place_trade: {str(e)}")
        # Make sure connections are closed if there's an error
        if 'conn' in locals() and conn:
            conn.close()
        raise
