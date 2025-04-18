import aiohttp

# Use the demo environment for testing
OANDA_BASE_URL = "https://api-fxpractice.oanda.com/v3"

async def place_order_oanda(api_key, account_id, signal):
    """Place an order on OANDA with API key and account ID."""
    url = f"{OANDA_BASE_URL}/accounts/{account_id}/orders"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    payload = {
        "order": {
            "instrument": signal.symbol,
            "units": str(signal.quantity) if signal.action.lower() == 'buy' else str(-signal.quantity),
            "type": "MARKET",
        }
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, headers=headers) as response:
                return await response.json()
    except Exception as e:
        return {"status": "error", "message": str(e)}


async def get_account_details(api_key):
    """Fetch account details to get the account ID."""
    url = f"{OANDA_BASE_URL}/accounts"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers) as response:
                return await response.json()
    except Exception as e:
        return {"status": "error", "message": str(e)}


# Example signal class for testing
class TradeSignal:
    def __init__(self, action, symbol, quantity):
        self.action = action
        self.symbol = symbol
        self.quantity = quantity


# Now your backend can fetch account details and place trades in the demo environment! 🚀
# Let me know if you want me to wire this up with your main bot logic or add error handling. 💡
