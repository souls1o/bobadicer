import requests
import config
from bets import UNITS, get_price

HOUSE_COIN = "ltc"


def get_deposit_address(coin):
    return config.COIN_ADDRESSES.get(coin.lower(), "")


def get_payout_address(coin=HOUSE_COIN):
    if coin.lower() == HOUSE_COIN:
        return get_deposit_address(HOUSE_COIN)
    return get_deposit_address(coin)


async def send_apirone(coin, address, amount):
    try:
        resp = requests.post(
            f"https://apirone.com/api/v2/accounts/{config.APIRONE_ACCOUNT}/transfer",
            params={"transfer-key": config.APIRONE_TRANSFER_KEY},
            json={"currency": coin.lower(), "destinations": [{"address": address, "amount": amount}]},
        )
        if resp.status_code == 200:
            return resp.json()
        return {"error": resp.text}
    except Exception as e:
        return {"error": str(e)}


async def get_account_balance():
    if not config.APIRONE_ACCOUNT:
        return None
    try:
        resp = requests.get(
            f"https://apirone.com/api/v2/accounts/{config.APIRONE_ACCOUNT}/balance",
            timeout=15,
        )
        if resp.status_code == 200:
            return resp.json()
    except Exception:
        pass
    return None


async def get_house_balance_text():
    data = await get_account_balance()
    if not data:
        return "❌ Could not fetch house balance from Apirone."

    balances = {
        entry.get("currency", "").lower(): entry.get("total", 0)
        for entry in data.get("balance", [])
    }

    smallest = balances.get(HOUSE_COIN, 0)
    try:
        usd = (smallest / UNITS) * get_price(HOUSE_COIN)
    except Exception:
        usd = 0.0

    return f"🏦 House Balance\nLTC: ${usd:,.2f}"
