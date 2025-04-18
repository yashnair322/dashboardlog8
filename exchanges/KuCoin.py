
import aiohttp
import hmac
import hashlib
import base64
import time

BASE_URL = "https://api.kucoin.com"

def generate_kucoin_signature(api_secret, api_passphrase, timestamp, method, endpoint, body=""):
    """Generate the KuCoin signature."""
    data_to_sign = f"{timestamp}{method}{endpoint}{body}"
    signature = base64.b64encode(hmac.new(api_secret.encode(), data_to_sign.encode(), hashlib.sha256).digest()).decode()
    passphrase = base64.b64encode(hmac.new(api_secret.encode(), api_passphrase.encode(), hashlib.sha256).digest()).decode()
    return signature, passphrase

async def place_order(api_key, api_secret, api_passphrase, signal):
    """Place an order on KuCoin."""
    endpoint = "/api/v1/orders"
    url = f"{BASE_URL}{endpoint}"
    method = "POST"
    timestamp = str(int(time.time() * 1000))

    payload = {
        "symbol": signal.symbol,
        "side": signal.action.lower(),
        "type": "market",
        "size": str(signal.quantity),
    }

    signature, passphrase = generate_kucoin_signature(api_secret, api_passphrase, timestamp, method, endpoint, body=str(payload))

    headers = {
        "KC-API-KEY": api_key,
        "KC-API-SIGN": signature,
        "KC-API-TIMESTAMP": timestamp,
        "KC-API-PASSPHRASE": passphrase,
        "Content-Type": "application/json",
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, headers=headers) as response:
                return await response.json()
    except Exception as e:
        return {"status": "error", "message": str(e)}
