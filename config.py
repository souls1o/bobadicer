import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN") or os.getenv("BOT_TOKEN")
APIRONE_ACCOUNT = os.getenv("APIRONE_ACCOUNT", "")
APIRONE_TRANSFER_KEY = os.getenv("APIRONE_TRANSFER_KEY", "")
COINGECKO_API_KEY = os.getenv("COINGECKO_API_KEY", "")

COIN_ADDRESSES = {
    "ltc": "ltc1qav7yjq32ugxud4kk4wl946cnz35uxf50hd6qrq",
    "btc": "bc1qds3y6eyjms05zyx8kq7yayw5plmnt2mdtz8yuu",
    "eth": "0xA65F50b9d02150A628191bc8B20Ea8C3086543a9",
    "sol": "HznFzJNmAuq8ds8dAvpq4rL5xLdc6aQmXscBjP7jjtRr",
    "usdt": "0xA65F50b9d02150A628191bc8B20Ea8C3086543a9",
    "usdc": "0xA65F50b9d02150A628191bc8B20Ea8C3086543a9",
    "bnb": "0xA65F50b9d02150A628191bc8B20Ea8C3086543a9"
}

COIN_ADDRESS_COMMANDS = {
    "!ltc": "ltc",
    "!btc": "btc",
    "!eth": "eth",
    "!bnb": "bnb",
    "!tron": "tron",
    "!sol": "sol",
}

ADMIN_USER_ID = 1517680352443764768
NOTIFY_USER_ID_2 = 1200925985999171706
NOTIFY_USER_IDS = [uid for uid in {ADMIN_USER_ID, NOTIFY_USER_ID_2} if uid]

AUTO_POST_CHANNEL_ID = 1524789293607026879
AUTO_POST_CHANNEL_NAME = "lf-players"
AUTO_POST_INTERVAL = 300

SEND_MIN_INTERVAL = 0.5
COMMAND_COOLDOWN_SECONDS = 3.0

GAME_LOG_CHANNEL_ID = 1258789286388568134
VOUCH_CHANNEL_ID = 1258789148702146700

ROLL_HYPE_MESSAGES = [
    "6&6",
    "1&1",
    "🥚",
    "🥀",
    "🍳",
    "wraps",
    "on my soul it's a 12",
    "it’s rigged"
]

CHANNEL_BLACKLIST = [
    AUTO_POST_CHANNEL_ID,
    AUTO_POST_CHANNEL_NAME,
    VOUCH_CHANNEL_ID,
    "vouch",
    "cmds"
]

AUTO_POST_MESSAGE = """<:Dices:1259259866254676049> **Dicing from $1 to $100 — open a ticket, I’m fully automated 🤖

<:Dices:1259259866254676049> I Win Ties: FT3 → I offer 20% HIGHER bet / FT5 → I offer 30% HIGHER bet
<:Dices:1259259866254676049> Standard: FT3/FT5 → I offer 9% LOWER bet**
"""

FORM_QUESTIONS = [
    {
        "type": "choice",
        "text": """<:Dices:1259259866254676049> Which gamemode would you like to play?
1. I Win Ties — FT3 → 20% HIGHER Bet | FT5 → 30% HIGHER Bet
2. Fair — 9% LOWER Bet

-# @mention
-# !setplayer <user_id|mention> to set a new player
""",
        "mapping": {
            "ties": ["1"],
            "fair": ["2"]
        },
        "short_key": "gamemode"
    },
    {
        "type": "choice",
        "text": """🥚 First to how many?
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
        "text": '<:Dices:1259259866254676049> **How much would you like to bet?**\n\n**(MIN: __1$__ | MAX: __100$__)**\n\n-# @mention',
        "short_key": "bet",
        "validator": "bet_validator"
    },
    {
        "type": "listen_address",
        "text": "send ltc addy, my {my_bet}v{his_bet}"
    },
    {
        "type": "choice",
        "text": """👤 Who rolls first?

1. @eggdicer
2. @mention
3. Random

-# @mention""",
        "mapping": {
            "@eggdicer 1": ["1", "you", "@eggdicer"],
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

-# @mention""",
        "mapping": {
            "normal": ["1", "normal", "normal mode", "n"],
            "crazy": ["2", "crazy", "crazy mode", "c"],
        },
        "short_key": "mode"
    },
    {
        "type": "listen_confirm",
        "text": ""
    }
]
