import json
from pathlib import Path

from bets import COIN, extract_crypto_address, format_bet_display, get_price, usd_to_smallest_unit
from services import send_apirone

FEE_RATE = 0.02
_FEES_PATH = Path(__file__).parent / "data" / "fees.json"


def _load_fees():
    if not _FEES_PATH.exists():
        return {"balance_usd": 0.0}
    try:
        with open(_FEES_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return {"balance_usd": 0.0}
    data.setdefault("balance_usd", 0.0)
    return data


def _save_fees(data):
    _FEES_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(_FEES_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def get_fee_balance_usd():
    return round(float(_load_fees().get("balance_usd", 0.0)), 8)


def add_fee_for_self_wager(wager_usd):
    amount = round(float(wager_usd) * FEE_RATE, 8)
    if amount <= 0:
        return 0.0
    data = _load_fees()
    data["balance_usd"] = round(float(data.get("balance_usd", 0.0)) + amount, 8)
    _save_fees(data)
    return amount


def deduct_fee_usd(amount):
    amount = round(float(amount), 8)
    data = _load_fees()
    balance = round(float(data.get("balance_usd", 0.0)), 8)
    if amount <= 0 or amount > balance:
        return False, balance
    data["balance_usd"] = round(balance - amount, 8)
    _save_fees(data)
    return True, data["balance_usd"]


def build_fee_text():
    usd = get_fee_balance_usd()
    try:
        ltc = usd / get_price(COIN) if usd > 0 else 0.0
    except Exception:
        ltc = 0.0
    return (
        f"**💰 Fee Balance ({int(FEE_RATE * 100)}% of self wagers)**\n"
        f"**USD:** ${usd:,.2f}\n"
        f"**LTC:** {ltc:.8f}"
    )


async def handle_fee_withdraw(message):
    parts = message.content.strip().split(maxsplit=2)
    if len(parts) < 3:
        return "Usage: `!withdraw <ltc_address> <usd_amount|all>`"

    address = extract_crypto_address(parts[1])
    if not address:
        return "❌ Invalid LTC address."

    amount_raw = parts[2].strip().lower().lstrip("$")
    balance = get_fee_balance_usd()
    if amount_raw == "all":
        usd = balance
    else:
        try:
            usd = float(amount_raw)
        except ValueError:
            return "❌ Amount must be a number or `all`."

    if usd <= 0:
        return "❌ Amount must be greater than 0."
    if usd > balance:
        return f"❌ Fee balance is only `${format_bet_display(balance)}`."

    try:
        price = get_price(COIN)
        smallest = usd_to_smallest_unit(usd, COIN, price)
    except Exception as exc:
        return f"❌ Could not price LTC: {exc}"

    if smallest <= 0:
        return "❌ Amount too small to send."

    result = await send_apirone(COIN, address, smallest)
    if "error" in result:
        err = result["error"]
        return f"❌ Transfer failed: {err if isinstance(err, str) else err}"

    ok, remaining = deduct_fee_usd(usd)
    if not ok:
        return "❌ Transfer sent but fee ledger update failed — check balance manually."

    return (
        f"✅ Withdrew `${format_bet_display(usd)}` LTC to `{address}`\n"
        f"**Remaining fee balance:** `${format_bet_display(remaining)}`"
    )
