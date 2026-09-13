import json
from pathlib import Path

from bets import COIN, UNITS, extract_crypto_address, format_bet_display, get_price, usd_to_crypto_amount
from services import send_apirone

FEE_RATE = 0.02
_FEES_PATH = Path(__file__).parent / "data" / "fees.json"


def _load_fees():
    if not _FEES_PATH.exists():
        return {"balance_ltc": 0.0}
    try:
        with open(_FEES_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return {"balance_ltc": 0.0}
    if "balance_ltc" not in data and data.get("balance_usd"):
        try:
            data["balance_ltc"] = round(float(data["balance_usd"]) / get_price(COIN), 8)
        except Exception:
            data["balance_ltc"] = 0.0
        data.pop("balance_usd", None)
    data.setdefault("balance_ltc", 0.0)
    return data


def _save_fees(data):
    _FEES_PATH.parent.mkdir(parents=True, exist_ok=True)
    data.pop("balance_usd", None)
    with open(_FEES_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def get_fee_balance_ltc():
    return round(float(_load_fees().get("balance_ltc", 0.0)), 8)


def get_fee_balance_usd():
    ltc = get_fee_balance_ltc()
    if ltc <= 0:
        return 0.0
    try:
        return round(ltc * get_price(COIN), 2)
    except Exception:
        return 0.0


def add_fee_for_player_wager(player_wager_usd):
    try:
        player_ltc = usd_to_crypto_amount(float(player_wager_usd), COIN)
    except Exception:
        return 0.0
    fee_ltc = round(player_ltc * FEE_RATE, 8)
    if fee_ltc <= 0:
        return 0.0
    data = _load_fees()
    data["balance_ltc"] = round(float(data.get("balance_ltc", 0.0)) + fee_ltc, 8)
    _save_fees(data)
    return fee_ltc


def deduct_fee_ltc(amount_ltc):
    amount_ltc = round(float(amount_ltc), 8)
    data = _load_fees()
    balance = round(float(data.get("balance_ltc", 0.0)), 8)
    if amount_ltc <= 0 or amount_ltc > balance:
        return False, balance
    data["balance_ltc"] = round(balance - amount_ltc, 8)
    _save_fees(data)
    return True, data["balance_ltc"]


def _ltc_to_smallest_unit(ltc_amount):
    return int(round(float(ltc_amount) * UNITS))


def build_fee_text():
    ltc = get_fee_balance_ltc()
    try:
        usd = ltc * get_price(COIN) if ltc > 0 else 0.0
    except Exception:
        usd = 0.0
    return (
        f"**💰 Fee Balance ({int(FEE_RATE * 100)}% of player wager LTC)**\n"
        f"**LTC:** {ltc:.8f}\n"
        f"**USD:** ${usd:,.2f}"
    )


async def handle_fee_withdraw(message):
    parts = message.content.strip().split(maxsplit=2)
    if len(parts) < 3:
        return "Usage: `!withdraw <ltc_address> <usd_amount|all>`"

    address = extract_crypto_address(parts[1])
    if not address:
        return "❌ Invalid LTC address."

    amount_raw = parts[2].strip().lower().lstrip("$")
    balance_ltc = get_fee_balance_ltc()
    try:
        price = get_price(COIN)
    except Exception as exc:
        return f"❌ Could not price LTC: {exc}"

    balance_usd = balance_ltc * price if balance_ltc > 0 else 0.0

    if amount_raw == "all":
        ltc = balance_ltc
    else:
        try:
            usd = float(amount_raw)
        except ValueError:
            return "❌ Amount must be a number or `all`."
        if usd <= 0:
            return "❌ Amount must be greater than 0."
        if usd > balance_usd:
            return f"❌ Fee balance is only `${format_bet_display(balance_usd)}`."
        ltc = round(usd / price, 8)
        if ltc > balance_ltc:
            ltc = balance_ltc

    if ltc <= 0:
        return "❌ Amount too small to send."

    usd = ltc * price
    smallest = _ltc_to_smallest_unit(ltc)
    if smallest <= 0:
        return "❌ Amount too small to send."

    result = await send_apirone(COIN, address, smallest)
    if "error" in result:
        err = result["error"]
        return f"❌ Transfer failed: {err if isinstance(err, str) else err}"

    ok, remaining_ltc = deduct_fee_ltc(ltc)
    if not ok:
        return "❌ Transfer sent but fee ledger update failed — check balance manually."

    remaining_usd = remaining_ltc * price
    return (
        f"✅ Withdrew `${format_bet_display(usd)}` (`{ltc:.8f}` LTC) to `{address}`\n"
        f"**Remaining fee balance:** `${format_bet_display(remaining_usd)}` (`{remaining_ltc:.8f}` LTC)"
    )
