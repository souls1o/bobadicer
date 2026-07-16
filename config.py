import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN") or os.getenv("BOT_TOKEN")
APIRONE_ACCOUNT = os.getenv("APIRONE_ACCOUNT", "")
APIRONE_TRANSFER_KEY = os.getenv("APIRONE_TRANSFER_KEY", "")
COINGECKO_API_KEY = os.getenv("COINGECKO_API_KEY", "")

# Hardcoded deposit addresses (!ltc, !btc, etc.)
COIN_ADDRESSES = {
    "ltc": "ltc1qy3kq9h0c0pmllm6yzrl7gc9gd9tyfevhvvkqcg",
    "btc": "bc1qds3y6eyjms05zyx8kq7yayw5plmnt2mdtz8yuu",
    "eth": "0x612283553FDaC711ee03f685E9A2637Fd3Fd6D4e",
    "sol": "7EQWjVy4qcbNee6SkJi34F7WUDRqrQzieqEnkt7yBFJi",
    "usdt": "0x612283553FDaC711ee03f685E9A2637Fd3Fd6D4e",
    "usdc": "0x612283553FDaC711ee03f685E9A2637Fd3Fd6D4e",
    "bnb": "0x612283553FDaC711ee03f685E9A2637Fd3Fd6D4e"
}

COIN_ADDRESS_COMMANDS = {
    "!ltc": "ltc",
    "!btc": "btc",
    "!eth": "eth",
    "!bnb": "bnb",
    "!tron": "tron",
    "!sol": "sol",
}

ADMIN_USER_ID = 1350971691391651890

AUTO_POST_CHANNEL_ID = 1524789293607026879
AUTO_POST_CHANNEL_NAME = "lf-players"
AUTO_POST_INTERVAL = 300

GAME_LOG_CHANNEL_ID = 1258789286388568134
VOUCH_CHANNEL_ID = 1258789148702146700

ROLL_HYPE_MESSAGES = [
    "Sir Boba",
    "6&6",
    "Lord Tapioca",
    "rigged",
    "LOL",
    "ggs",
    "1&1",
    "🧋",
    "🥀"
]

# Channels where ticket scanning / form start is ignored (IDs and/or names)
CHANNEL_BLACKLIST = [
    AUTO_POST_CHANNEL_ID,
    AUTO_POST_CHANNEL_NAME,
    VOUCH_CHANNEL_ID,
    "vouch",
    "cmds"
]

AUTO_POST_MESSAGE = """<:Dices:1259259866254676049> **Dicing** from **$1** up to **$20** — make a ticket, **I'm fully automated** 🤖

<:Dices:1259259866254676049> **__I Win Ties__ | FT3 -> I offer 20% HIGHER bet / FT5 -> I offer 30% HIGHER bet**
<:Dices:1259259866254676049> **__Fair__ | FT3/FT5 -> I offer 10% LOWER bet**
"""

FORM_QUESTIONS = [
    {
        "type": "choice",
        "text": """<:Dices:1259259866254676049> Which gamemode would you like to play?
1. I Win Ties — FT3 → 20% HIGHER Bet | FT5 → 30% HIGHER Bet
2. Fair — 10% LOWER Bet

-# @mention
""",
        "mapping": {
            "ties": ["1"],
            "fair": ["2"]
        },
        "short_key": "gamemode"
    },
    {
        "type": "choice",
        "text": """🧋 First to how many?
1. FT3
2. FT5
3. Random

-# @mention
""",
        "mapping": {
            "ft3": ["1"],
            "ft5": ["2"],
            "random": ["3"]
        },
        "short_key": "first_to"
    },
    {
        "type": "open",
        "text": '🎲 **How much would you like to bet?**\n\n**(MIN: __1$__ | MAX: __20$__)**\n\n-# @mention',
        "short_key": "bet",
        "validator": "bet_validator"
    },
    {
        "type": "listen_address",
        "text": "send ltc addy, my {my_bet}v{his_bet}"
    },
    {
        "type": "choice",
        "text": """👤 Who goes first?

1. @trumpdicer
2. @mention
3. Random

-# @mention""",
        "mapping": {
            "@trumpdicer 1": ["1", "you", "@trumpdicer"],
            "@mention 1": ["2", "me", "@mention"],
            "random": ["3", "random", "r"]
        },
        "short_key": "first"
    },
    {
        "type": "choice",
        "text": """🎮 Which gamemode would you like to play?

1. Normal Mode
2. Crazy Mode
3. Random

-# @mention""",
        "mapping": {
            "normal": ["1", "normal", "normal mode", "n"],
            "crazy": ["2", "crazy", "crazy mode", "c"],
            "random": ["3", "random", "r"]
        },
        "short_key": "mode"
    },
    {
        "type": "listen_confirm",
        "text": ""
    }
]
