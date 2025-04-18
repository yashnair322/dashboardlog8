
import aiohttp

BASE_URL = "https://api.bitget.com"

async def place_order(api_key, api_secret, passphrase, signal):
    """Place an order on Bitget."""
    url = f"{BASE_URL}/api/v2/spot/order"
    headers = {
        "ACCESS-KEY": api_key,
        "ACCESS-PASSPHRASE": passphrase,
    }

    payload = {
        "symbol": signal.symbol,
        "side": signal.action.lower(),
        "orderType": "market",
        "quantity": signal.quantity,
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, headers=headers) as response:
                return await response.json()
    except Exception as e:
        return {"status": "error", "message": str(e)}


# 📈 Update the bot manager to include Bitget
from exchanges import binance, bybit, kucoin, okx, bitget

async def place_trade(bot, api_key, api_secret, passphrase, signal):
    exchange = bot["exchange"]

    if exchange == "binance":
        return await binance.place_order(api_key, api_secret, signal)
    elif exchange == "bybit":
        return await bybit.place_order(api_key, api_secret, signal)
    elif exchange == "kucoin":
        return await kucoin.place_order(api_key, api_secret, signal)
    elif exchange == "okx":
        return await okx.place_order(api_key, api_secret, signal)
    elif exchange == "bitget":
        return await bitget.place_order(api_key, api_secret, passphrase, signal)
    else:
        return {"status": "error", "message": "Unsupported exchange"}


# ✅ Bitget is ready to go! Want me to add more features, like stop-loss or leverage trades? Let me know! 🚀
