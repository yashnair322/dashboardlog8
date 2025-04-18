from exchanges import binance, bybit, KuCoin, oanda, meta
from backend.types import TradeSignal, Bot

# Log streams for each bot
bot_logs = {}

def log_message(bot_name: str, message: str):
    """Log a message for a specific bot."""
    if bot_name not in bot_logs:
        bot_logs[bot_name] = []
    bot_logs[bot_name].append(message)

async def close_position(bot, signal):
    """
    Close the open position for a bot (sell or buy).
    """
    try:
        # Log the position closure attempt
        log_message(bot.name, f"🔒 Closing position for {bot.symbol} with quantity {bot.quantity}...")

        if bot.position == 'buy':
            # Close buy order by executing a sell
            sell_signal = TradeSignal(action="sell", symbol=signal.symbol, quantity=signal.quantity)
            
            # Use the exchange map to place the closing order
            exchange = bot.exchange.lower()
            exchange_map = {
                "binance": binance.place_order,
                "bybit": bybit.place_order,
                "kucoin": KuCoin.place_order,
                "oanda": oanda.place_order_oanda,
                "metatrader5": meta.place_order_metatrader5
            }
            
            if exchange in exchange_map:
                if exchange == "oanda":
                    order_result = await exchange_map[exchange](bot.api_key, bot.account_id, sell_signal)
                elif exchange == "metatrader5":
                    order_result = await exchange_map[exchange](bot.login, bot.password, bot.server, sell_signal)
                else:
                    order_result = await exchange_map[exchange](bot.api_key, bot.api_secret, sell_signal)
                
                log_message(bot.name, f"❌ Closed BUY position for {bot.symbol} ({bot.quantity})")
                bot.position = "neutral"  # Reset position
            else:
                log_message(bot.name, f"❌ Unsupported exchange for closing position: {exchange}")
                return f"Failed to close position: Unsupported exchange {exchange}"
                
        elif bot.position == 'sell':
            # Close sell order by executing a buy
            buy_signal = TradeSignal(action="buy", symbol=signal.symbol, quantity=signal.quantity)
            
            # Use the exchange map to place the closing order
            exchange = bot.exchange.lower()
            exchange_map = {
                "binance": binance.place_order,
                "bybit": bybit.place_order,
                "kucoin": KuCoin.place_order,
                "oanda": oanda.place_order_oanda,
                "metatrader5": meta.place_order_metatrader5
            }
            
            if exchange in exchange_map:
                if exchange == "oanda":
                    order_result = await exchange_map[exchange](bot.api_key, bot.account_id, buy_signal)
                elif exchange == "metatrader5":
                    order_result = await exchange_map[exchange](bot.login, bot.password, bot.server, buy_signal)
                else:
                    order_result = await exchange_map[exchange](bot.api_key, bot.api_secret, buy_signal)
                
                log_message(bot.name, f"❌ Closed SELL position for {bot.symbol} ({bot.quantity})")
                bot.position = "neutral"  # Reset position
            else:
                log_message(bot.name, f"❌ Unsupported exchange for closing position: {exchange}")
                return f"Failed to close position: Unsupported exchange {exchange}"
        else:
            log_message(bot.name, "⚠️ No position to close.")

        # Successfully closed position
        return f"Position for {bot.symbol} closed successfully."

    except Exception as e:
        log_message(bot.name, f"❌ Failed to close position: {str(e)}")
        raise Exception(f"Failed to close position: {str(e)}")


async def execute_trade(bot, signal):
    """
    Place an order for the bot depending on the trade signal.
    It first checks if the bot has an open position.
    If the position is conflicting, it closes the open position and then places the new trade.
    """
      """Execute the trade itself without updating trade count."""
    # Normalize the exchange name to lowercase for consistent comparison
    exchange = bot.exchange.lower()
    
    # Log the exchange type for debugging
    log_message(bot.name, f"🔍 Attempting trade with exchange: '{exchange}'")
    
    # First, check if the bot has an open position
    if bot.position in ['buy', 'sell']:
        # If the action in the signal is different from the current position, close the existing position
        if (signal.action == 'buy' and bot.position == 'sell') or (signal.action == 'sell' and bot.position == 'buy'):
            log_message(bot.name, f"🔁 Signal conflict detected: Closing '{bot.position}' position to switch to '{signal.action}'.")
            try:
                # Close current position
                close_result = await close_position(bot, signal)
                log_message(bot.name, f"✔️ {close_result}")
            except Exception as e:
                log_message(bot.name, f"❌ Failed to close position: {str(e)}")
                return {"status": "error", "message": f"Failed to close position: {str(e)}"}
        elif signal.action == bot.position:
            # Already in the same position
            log_message(bot.name, f"ℹ️ Bot already has an open {signal.action.upper()} position. Ignoring duplicate signal.")
            return {"status": "info", "message": f"Already in {signal.action} position for {bot.symbol}"}

    # After closing the conflicting position or if no position exists, place the new trade
    exchange_map = {
        "binance": binance.place_order,
        "bybit": bybit.place_order,
        "kucoin": KuCoin.place_order,
        "oanda": oanda.place_order_oanda,
        "metatrader5": meta.place_order_metatrader5
    }

    if exchange not in exchange_map:
        log_message(bot.name, f"❌ Unsupported exchange: {exchange}")
        return {"status": "error", "message": f"Unsupported exchange: {exchange}"}

    try:
        # Use the appropriate exchange handler based on the exchange type
        if exchange == "oanda":
            # Verify that account_id and api_key are available
            if not bot.account_id or not bot.api_key:
                log_message(bot.name, f"❌ Missing credentials for OANDA: account_id or api_key")
                return {"status": "error", "message": "Missing OANDA credentials"}
                
            log_message(bot.name, f"🔄 Placing {signal.action} order on OANDA for {signal.symbol}")
            order_result = await oanda.place_order_oanda(bot.api_key, bot.account_id, signal)
            
        elif exchange == "metatrader5":
            # Verify that MT5 credentials are available
            if not bot.login or not bot.password or not bot.server:
                log_message(bot.name, f"❌ Missing credentials for MetaTrader5: login, password, or server")
                return {"status": "error", "message": "Missing MetaTrader5 credentials"}
                
            log_message(bot.name, f"🔄 Placing {signal.action} order on MetaTrader5 for {signal.symbol}")
            order_result = await meta.place_order_metatrader5(bot.login, bot.password, bot.server, signal)
            
        else:
            # Standard exchange handling (Binance, Bybit, KuCoin)
            if not bot.api_key or not bot.api_secret:
                log_message(bot.name, f"❌ Missing credentials for {exchange}: api_key or api_secret")
                return {"status": "error", "message": f"Missing {exchange} credentials"}
                
            log_message(bot.name, f"🔄 Placing {signal.action} order on {exchange} for {signal.symbol}")
            order_result = await exchange_map[exchange](bot.api_key, bot.api_secret, signal)

        # Update the bot's position
        bot.position = signal.action
        log_message(bot.name, f"✅ Order placed successfully: {signal.action.upper()} {signal.symbol}")
        return {"status": "success", "message": f"Order placed: {signal.action} {signal.symbol}"}

    except Exception as e:
        log_message(bot.name, f"❌ Failed to place order: {str(e)}")
        return {"status": "error", "message": f"Failed to place order: {str(e)}"}

# Updated place_trade that manages both trade execution and count updates
async def place_trade(bot, signal):
    """Execute a trade and handle trade count updates for subscription limits."""
    exchange = bot.exchange.lower()
    log_message(bot.name, f"🔍 Attempting trade with exchange: '{exchange}'")
    
    # Create a single connection for the entire function
    conn = None
    
    try:
        # Execute the trade first
        result = await execute_trade(bot, signal)
        
        # Only update trade count if trade was successful
        if result["status"] == "success":
            log_message(bot.name, "🔢 Updating trade count...")
            
            try:
                # Get a database connection
                import psycopg2
                import os
                DATABASE_URL = os.getenv("DATABASE_URL")
                conn = psycopg2.connect(DATABASE_URL)
                cur = conn.cursor()
                
                # Get the user email for this bot
                cur.execute("SELECT user_email FROM bots WHERE name = %s", (bot.name,))
                bot_user = cur.fetchone()
                
                if not bot_user or not bot_user[0]:
                    log_message(bot.name, "❌ Could not find user email for this bot!")
                    return {
                        **result,
                        "warning": "Trade successful but trade count not updated: User not found"
                    }
                
                user_email = bot_user[0]
                log_message(bot.name, f"✅ Found user email: {user_email}")
                
                # Get current trade count
                cur.execute("SELECT trade_count FROM users WHERE email = %s", (user_email,))
                user_data = cur.fetchone()
                
                if not user_data:
                    log_message(bot.name, f"⚠️ User {user_email} not found in users table!")
                    return {
                        **result,
                        "warning": "Trade successful but trade count not updated: User record missing"
                    }
                    
                current_count = user_data[0] if user_data[0] is not None else 0
                log_message(bot.name, f"📊 Current trade count: {current_count}")
                
                # Update the trade count
                new_count = current_count + 1
                log_message(bot.name, f"📈 Updating trade count to: {new_count}")
                
                cur.execute("UPDATE users SET trade_count = %s WHERE email = %s", (new_count, user_email))
                
                # Check if rows were affected
                if cur.rowcount > 0:
                    log_message(bot.name, f"✅ Trade count updated successfully: {new_count}")
                else:
                    log_message(bot.name, "❌ No rows updated in trade count update")
                    return {
                        **result,
                        "warning": "Trade successful but trade count not updated: Database update failed"
                    }
                
                # Commit the transaction
                conn.commit()
                log_message(bot.name, "💾 Trade count update committed")
                
                # Check subscription limits
                cur.execute("SELECT subscription_plan FROM users WHERE email = %s", (user_email,))
                plan_data = cur.fetchone()
                if plan_data and plan_data[0] == 'free' and new_count >= 4:
                    log_message(bot.name, "⚠️ Free plan trade limit (4) has been reached!")
                    return {
                        **result,
                        "warning": "Trade limit reached. Please upgrade your subscription for unlimited trades."
                    }
                
                return result
                
            except Exception as e:
                log_message(bot.name, f"❌ Error updating trade count: {str(e)}")
                if conn:
                    conn.rollback()
                return {
                    **result,
                    "warning": f"Trade successful but trade count not updated: {str(e)}"
                }
        else:
            # Trade wasn't successful, just return the result
            return result
            
    except Exception as e:
        log_message(bot.name, f"❌ Error in place_trade: {str(e)}")
        raise
    finally:
        # Always close database connections
        if conn:
            conn.close()
