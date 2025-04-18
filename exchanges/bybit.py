import aiohttp

BASE_URL = "https://api.bybit.com"

async def place_order(api_key, api_secret, signal):
    """Place an order on Bybit."""
    url = f"{BASE_URL}/v2/private/order/create"
    headers = {
        "X-BYBIT-API-KEY": api_key,
    }

    payload = {
        "symbol": signal.symbol,
        "side": signal.action.upper(),
        "order_type": "Market",
        "qty": signal.quantity,
        "time_in_force": "GoodTillCancel",
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, headers=headers) as response:
                return await response.json()
    except Exception as e:
        return {"status": "error", "message": str(e)}
