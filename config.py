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
    "btc": "bc1q24ptksu6uyu5kedfge3enum4v2drq0kxgfqlaw",
    "eth": "0xd4f77Fce773927477dC543B150e4c2223FC67Db9",
    "sol": "DgPd6dvF7HKyZuHMkT89hWzErXbuPmrjTjFBTd8UvFyF",
    "usdt": "0xd4f77Fce773927477dC543B150e4c2223FC67Db9",
    "usdc": "0xd4f77Fce773927477dC543B150e4c2223FC67Db9",
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

# Channels where ticket scanning / form start is ignored
CHANNEL_BLACKLIST = [
    AUTO_POST_CHANNEL_ID,
    1258789148702146700,
]

AUTO_POST_MESSAGE = """<:Dices:1259259866254676049> **Dicing** from **$1** up to **$30** — make a ticket, **I'm fully automated** 🤖

<:Dices:1259259866254676049> **__I Win Ties__ | FT3 -> I offer 20% HIGHER bet / FT5 -> I offer 30% HIGHER bet**
<:Dices:1259259866254676049> **__Fair__ | FT3/FT5 -> I offer 15% LOWER bet**
"""

FORM_QUESTIONS = [
    {
        "type": "choice",
        "text": """<:Dices:1259259866254676049> Which gamemode would you like to play?
1. I Win Ties — FT3 → 20% HIGHER Bet | FT5 → 30% HIGHER Bet
2. Fair — 15% LOWER Bet

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
        "text": '🎲 **How much would you like to bet?**\n\n**(MIN: __1$__ | MAX: __30$__)**\n\n-# @mention',
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

1. @bobadicer
2. @mention
3. Random

-# @mention""",
        "mapping": {
            "@bobadicer 1": ["1", "you", "@bobadicer"],
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
