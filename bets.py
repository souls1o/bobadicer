import re
import time
import requests
import config

COIN = "ltc"
COINGECKO_ID = "litecoin"
UNITS = 100_000_000

_BECH32_CHARS = r"qpzry9x8gf2tvdw0s3jn54khce6mua7l"
_ADDRESS_PATTERNS = (
    re.compile(rf"(ltc1[{_BECH32_CHARS}]{{25,87}})", re.IGNORECASE),
    re.compile(r"([LM3][1-9A-HJ-NP-Za-km-z]{26,33})"),
)

_PRICE_CACHE = {}
_LAST_UPDATE = 0
CACHE_SECONDS = 180


def normalize_coin(_coin_str=None):
    return COIN


def extract_crypto_address(text, _coin=None):
    for pattern in _ADDRESS_PATTERNS:
        match = pattern.search(text)
        if match:
            return match.group(1)
    return None


def get_max_bet(form):
    gamemode = form.get("responses", {}).get("gamemode")
    if gamemode == "fair":
        return 200
    if gamemode == "ties":
        return 200
    return 50


def format_bet_display(value):
    num = round(float(value), 2)
    if num == int(num):
        return str(int(num))
    return f"{num:.2f}"


def calculate_my_bet(form):
    responses = form.get("responses", {})
    try:
        his_bet = float(responses.get("bet", "0").split()[0])
    except (ValueError, IndexError):
        his_bet = 0.0

    gamemode = responses.get("gamemode")
    first_to = responses.get("first_to")
    if gamemode == "ties" and first_to == "ft3":
        return round(his_bet * 1.2, 2)
    if gamemode == "ties" and first_to == "ft5":
        return round(his_bet * 1.3, 2)
    if gamemode == "fair":
        return round(his_bet * 0.85, 2)
    return None


def get_bet_info(form):
    parts = form.get("responses", {}).get("bet", "0").split()
    his_bet_usd = float(parts[0])
    my_bet_usd = calculate_my_bet(form) or 0.0
    return his_bet_usd, my_bet_usd, COIN


def get_price(coin=COIN):
    global _LAST_UPDATE
    now = time.time()
    if now - _LAST_UPDATE > CACHE_SECONDS:
        r = requests.get(
            f"https://api.coingecko.com/api/v3/simple/price?ids={COINGECKO_ID}&vs_currencies=usd",
            headers={"accept": "application/json", "x-cg-demo-api-key": config.COINGECKO_API_KEY},
            timeout=10,
        )
        r.raise_for_status()
        data = r.json()
        _PRICE_CACHE[COIN] = float(data[COINGECKO_ID]["usd"])
        _LAST_UPDATE = now
    return _PRICE_CACHE[COIN]


def usd_to_crypto_amount(usd, coin=COIN):
    return usd / get_price(coin)


def usd_to_smallest_unit(usd, coin, price_usd):
    return int((usd / price_usd) * UNITS)


def get_wager_usd(form):
    return get_bet_info(form)[1]


def add_wagered_usd(form, amount=None):
    if amount is None:
        amount = get_wager_usd(form)
    form["total_wagered_usd"] = round(form.get("total_wagered_usd", 0) + amount, 8)


def add_winnings_usd(form, usd, coin=COIN):
    form["winnings_usd"] = round(form.get("winnings_usd", 0) + usd, 8)
    form["winnings_crypto"] = round(form.get("winnings_crypto", 0) + usd_to_crypto_amount(usd, coin), 8)


def subtract_winnings_usd(form, usd, coin=COIN):
    form["winnings_usd"] = round(form.get("winnings_usd", 0) - usd, 8)
    form["winnings_crypto"] = round(form.get("winnings_crypto", 0) - usd_to_crypto_amount(usd, coin), 8)


def bet_validator(response, form=None):
    parts = response.strip().split()
    if len(parts) not in (1, 2):
        return False
    try:
        amount = float(parts[0].lstrip("$"))
    except ValueError:
        return False
    if len(parts) == 2 and normalize_coin(parts[1]) != COIN:
        return False
    if not form:
        return 1 <= amount <= 50
    return 1 <= amount <= get_max_bet(form)
