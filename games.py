import asyncio
import random
import re

import config
from forms import is_roll_command
from postgame import end_game
from send_queue import queued_send
from state import save_session_from_form

DA_HOOD_BOT_ID = 1200925985999171706
ROLL_EMBED_PATTERN = re.compile(r"(\d+)\s*(?:&|\+)\s*(\d+)")


async def get_ticket_channel(bot, form, fallback=None):
    if bot is None:
        return fallback
    channel = bot.get_channel(form["ticket_channel_id"])
    if channel is None:
        channel = await bot.fetch_channel(form["ticket_channel_id"])
    return channel


def is_bot_turn(state):
    return state["current_player"] in ("me", "@eggdicer")


async def trigger_bot_roll(roll_channel, form, bot_user):
    state = form["game_state"]
    if state.get("bot_roll_in_flight"):
        return

    state["bot_roll_in_flight"] = True
    state["waiting_for_embed"] = True
    state["roll_initiator_id"] = bot_user.id
    state["pending_bot_roll_cmd_id"] = None
    try:
        await asyncio.sleep(1)
        if "game_state" not in form or form["game_state"] is not state:
            return
        if state.get("scoring"):
            return

        hype = random.choice(config.ROLL_HYPE_MESSAGES)
        sent = await queued_send(roll_channel, f"-roll {hype}")
        state["waiting_for_embed"] = True
        state["roll_initiator_id"] = bot_user.id
        if sent is not None:
            state["pending_bot_roll_cmd_id"] = sent.id
    finally:
        state["bot_roll_in_flight"] = False


def _queue_user_roll(state, message_id):
    if message_id in state.get("consumed_roll_cmd_ids", set()):
        return
    pending = state.setdefault("pending_roll_message_ids", [])
    queued = state.setdefault("queued_user_roll_ids", [])
    if message_id not in pending and message_id not in queued:
        queued.append(message_id)


def _accept_user_roll(state, message_id, ticket_user_id):
    if message_id in state.get("consumed_roll_cmd_ids", set()):
        return
    pending = state.setdefault("pending_roll_message_ids", [])
    state.setdefault("pending_user_embeds", 0)
    if message_id not in pending:
        pending.append(message_id)
        state["pending_user_embeds"] += 1
    state["waiting_for_embed"] = True
    state["roll_initiator_id"] = ticket_user_id


def _user_can_accept_rolls(state, bot_user_id):
    if state.get("scoring"):
        return False
    if state.get("bot_roll_in_flight"):
        return False
    if state.get("awaiting_user_after_bot") or state.get("pending_bot_total") is not None:
        if state.get("pending_user_embeds", 0) > 0:
            return False
        return True
    if not is_bot_turn(state):
        waiting = state.get("waiting_for_embed")
        initiator = state.get("roll_initiator_id")
        if waiting and initiator == bot_user_id:
            return False
        if state.get("pending_user_embeds", 0) > 0:
            return False
        return True
    waiting = state.get("waiting_for_embed")
    initiator = state.get("roll_initiator_id")
    return bool(waiting and initiator != bot_user_id)


def _try_activate_queued_user_rolls(state, ticket_user_id, bot_user_id):
    queue = state.get("queued_user_roll_ids", [])
    if not queue or not _user_can_accept_rolls(state, bot_user_id):
        return
    while queue and _user_can_accept_rolls(state, bot_user_id):
        roll_id = queue.pop(0)
        _accept_user_roll(state, roll_id, ticket_user_id)


def _consume_user_roll_cmd(state, cmd_id):
    state.setdefault("consumed_roll_cmd_ids", set()).add(cmd_id)
    pending = state.get("pending_roll_message_ids", [])
    if cmd_id in pending:
        pending.remove(cmd_id)
        if state.get("pending_user_embeds", 0) > 0:
            state["pending_user_embeds"] -= 1
    queued = state.get("queued_user_roll_ids", [])
    if cmd_id in queued:
        queued.remove(cmd_id)


def _stash_prefetched_user_total(state, cmd_id, total):
    if cmd_id in state.get("consumed_roll_cmd_ids", set()):
        return
    _queue_user_roll(state, cmd_id)
    prefs = state.setdefault("prefetched_user_totals", [])
    if any(p["cmd_id"] == cmd_id for p in prefs):
        return
    prefs.append({"cmd_id": cmd_id, "total": total})


def _take_prefetched_user_total(state, cmd_id=None):
    prefs = state.get("prefetched_user_totals", [])
    if not prefs:
        return None
    if cmd_id is None:
        entry = prefs.pop(0)
    else:
        entry = None
        for i, item in enumerate(prefs):
            if item["cmd_id"] == cmd_id:
                entry = prefs.pop(i)
                break
        if entry is None:
            return None
    queued = state.get("queued_user_roll_ids", [])
    if entry["cmd_id"] in queued:
        queued.remove(entry["cmd_id"])
    return entry["total"]


async def handle_user_roll(message, form, bot_user):
    state = form["game_state"]
    ticket_user_id = form["ticket_user_id"]
    if message.author.id != ticket_user_id:
        return

    if state.get("scoring") or not _user_can_accept_rolls(state, bot_user.id):
        _queue_user_roll(state, message.id)
        return

    if state.get("awaiting_user_after_bot") or state.get("pending_bot_total") is not None:
        _accept_user_roll(state, message.id, ticket_user_id)
        return

    _accept_user_roll(state, message.id, ticket_user_id)


def _reset_round_state(state):
    consumed = state.get("consumed_roll_cmd_ids", set())
    pending = state.get("pending_roll_message_ids", [])
    queued = state.setdefault("queued_user_roll_ids", [])
    for roll_id in pending:
        if roll_id not in consumed and roll_id not in queued:
            queued.append(roll_id)

    state["user_totals_queue"] = []
    state["pending_user_embeds"] = 0
    state["pending_roll_message_ids"] = []
    state["bot_rolls_remaining"] = 0
    state["pending_bot_total"] = None
    state["awaiting_user_after_bot"] = False
    state.pop("bot_first_embed_id", None)
    state["pending_bot_roll_cmd_id"] = None
    state["waiting_for_embed"] = False
    state["roll_initiator_id"] = None
    state["bot_roll_in_flight"] = False
    state["current_player"] = state["first_player"]


async def _answer_early_user_rolls(roll_channel, form, bot_user, bot):
    state = form["game_state"]
    prefs = state.setdefault("prefetched_user_totals", [])
    queued = state.setdefault("queued_user_roll_ids", [])
    consumed = state.get("consumed_roll_cmd_ids", set())

    if queued:
        state["queued_user_roll_ids"] = [r for r in queued if r not in consumed]
        queued = state["queued_user_roll_ids"]

    if not prefs and not queued:
        return False

    while prefs:
        entry = prefs.pop(0)
        if entry["cmd_id"] in consumed:
            continue
        state.setdefault("user_totals_queue", []).append(entry["total"])
        _consume_user_roll_cmd(state, entry["cmd_id"])
        consumed = state.get("consumed_roll_cmd_ids", set())

    still_queued = []
    while queued:
        roll_id = queued.pop(0)
        if roll_id in consumed:
            continue
        stashed = _take_prefetched_user_total(state, roll_id)
        if stashed is not None:
            state.setdefault("user_totals_queue", []).append(stashed)
            _consume_user_roll_cmd(state, roll_id)
            consumed = state.get("consumed_roll_cmd_ids", set())
        else:
            still_queued.append(roll_id)
    queued.extend(still_queued)

    if state.get("user_totals_queue"):
        state["current_player"] = "you"
        state["waiting_for_embed"] = False
        state["bot_rolls_remaining"] = len(state["user_totals_queue"])
        await trigger_bot_roll(roll_channel, form, bot_user)
        return True

    return False


async def _start_next_round(roll_channel, form, bot_user, bot):
    if "game_state" not in form:
        return
    state = form["game_state"]
    state["current_player"] = state["first_player"]

    if await _answer_early_user_rolls(roll_channel, form, bot_user, bot):
        return

    if is_bot_turn(state):
        await do_next_roll(roll_channel, form, bot_user, bot)
    else:
        _try_activate_queued_user_rolls(state, form["ticket_user_id"], bot_user.id)


def _pair_winner(me_total, you_total, gamemode, roll_mode):
    if gamemode == "ties" and me_total == you_total:
        return "me"
    if me_total == you_total:
        return None
    if roll_mode == "crazy":
        return "me" if me_total < you_total else "you"
    return "me" if me_total > you_total else "you"


async def _score_pair(roll_channel, form, bot_user, bot, me_total, you_total, *, continue_batch=False):
    state = form["game_state"]
    state["scoring"] = True
    try:
        winner = _pair_winner(me_total, you_total, state["gamemode"], state["mode"])
        ticket_channel = await get_ticket_channel(bot, form, fallback=roll_channel)

        if winner == "me":
            state["self_score"] += 1
        elif winner == "you":
            state["adder_score"] += 1

        first_to = state["first_to"]
        if state["self_score"] >= first_to or state["adder_score"] >= first_to:
            await queued_send(ticket_channel, f"`{state['self_score']}-{state['adder_score']}`")
            self_won = state["self_score"] >= first_to
            await end_game(ticket_channel, form, self_won, bot_user, bot)
            return True

        if continue_batch:
            await queued_send(ticket_channel, f"`{state['self_score']}-{state['adder_score']}`")
            return False

        await queued_send(ticket_channel, f"`{state['self_score']}-{state['adder_score']}`")
        _reset_round_state(state)
        state["scoring"] = False
        await _start_next_round(roll_channel, form, bot_user, bot)
        return False
    finally:
        state["scoring"] = False


async def do_next_roll(roll_channel, form, bot_user, bot):
    state = form["game_state"]
    if state.get("game_type") != "dice":
        return
    if state.get("waiting_for_embed") or state.get("bot_roll_in_flight") or state.get("scoring"):
        return
    if is_bot_turn(state):
        await trigger_bot_roll(roll_channel, form, bot_user)


def parse_roll_from_embed(message):
    if not message.embeds:
        return None
    embed = message.embeds[0]
    parts = [embed.description or "", embed.title or ""]
    for field in embed.fields:
        parts.append(field.name or "")
        parts.append(field.value or "")
    for text in parts:
        match = ROLL_EMBED_PATTERN.search(text)
        if match:
            return int(match.group(1)), int(match.group(2))
    return None


async def _find_nearest_roll_cmd(channel, embed_message):
    async for msg in channel.history(limit=50, before=embed_message):
        if is_roll_command(msg.content):
            return msg
    return None


def dice_embed_active(state):
    return (
        state.get("waiting_for_embed")
        or state.get("bot_roll_in_flight")
        or state.get("scoring")
        or state.get("pending_bot_total") is not None
        or state.get("awaiting_user_after_bot")
        or state.get("user_totals_queue")
        or state.get("prefetched_user_totals")
        or state.get("queued_user_roll_ids")
    )


async def handle_roll_embed(message, form, bot_user, bot):
    state = form["game_state"]
    state.setdefault("consumed_embed_ids", set())
    state.setdefault("consumed_roll_cmd_ids", set())
    if message.id in state["consumed_embed_ids"]:
        return
    if not message.embeds:
        return
    if not (message.author.bot or message.author.id == DA_HOOD_BOT_ID):
        return

    rolls = parse_roll_from_embed(message)
    if not rolls:
        return

    state["consumed_embed_ids"].add(message.id)
    ticket_user_id = form["ticket_user_id"]
    total = rolls[0] + rolls[1]
    state.setdefault("user_totals_queue", [])
    state.setdefault("pending_user_embeds", 0)
    state.setdefault("bot_rolls_remaining", 0)
    state.setdefault("prefetched_user_totals", [])
    state.setdefault("queued_user_roll_ids", [])

    nearest = await _find_nearest_roll_cmd(message.channel, message)
    if not nearest:
        state["consumed_embed_ids"].discard(message.id)
        return

    if nearest.author.id == ticket_user_id:
        await _handle_user_roll_embed(message, form, bot_user, bot, nearest, total)
        return
    if nearest.author.id == bot_user.id:
        await _handle_bot_roll_embed(message, form, bot_user, bot, nearest, total)
        return

    state["consumed_embed_ids"].discard(message.id)


async def _handle_user_roll_embed(message, form, bot_user, bot, cmd, total):
    state = form["game_state"]
    ticket_user_id = form["ticket_user_id"]

    if state.get("scoring"):
        _stash_prefetched_user_total(state, cmd.id, total)
        return

    pending_bot_total = state.get("pending_bot_total")
    if pending_bot_total is not None:
        state["pending_bot_total"] = None
        state["awaiting_user_after_bot"] = False
        state.pop("bot_first_embed_id", None)
        state["pending_user_embeds"] = 0
        state["user_totals_queue"] = []
        state["waiting_for_embed"] = False
        _consume_user_roll_cmd(state, cmd.id)
        stashed = _take_prefetched_user_total(state, cmd.id)
        await _score_pair(
            message.channel, form, bot_user, bot, pending_bot_total,
            stashed if stashed is not None else total,
        )
        return

    pending_ids = state.get("pending_roll_message_ids", [])
    queued_ids = state.get("queued_user_roll_ids", [])

    waiting_on_bot = (
        state.get("waiting_for_embed")
        and state.get("roll_initiator_id") == bot_user.id
    ) or state.get("bot_roll_in_flight") or bool(state.get("pending_bot_roll_cmd_id"))

    out_of_turn = (
        is_bot_turn(state)
        and state.get("pending_bot_total") is None
        and not state.get("awaiting_user_after_bot")
        and cmd.id not in pending_ids
    )

    if waiting_on_bot or out_of_turn or (cmd.id in queued_ids and cmd.id not in pending_ids):
        _stash_prefetched_user_total(state, cmd.id, total)
        idle = (
            not state.get("bot_roll_in_flight")
            and not state.get("waiting_for_embed")
            and not state.get("scoring")
            and not state.get("user_totals_queue")
            and not state.get("pending_bot_roll_cmd_id")
        )
        if idle:
            await _start_next_round(message.channel, form, bot_user, bot)
        return

    _consume_user_roll_cmd(state, cmd.id)
    state["user_totals_queue"].append(total)

    if state.get("pending_user_embeds", 0) > 0:
        state["waiting_for_embed"] = True
        state["roll_initiator_id"] = ticket_user_id
        return

    state["waiting_for_embed"] = False
    state["bot_rolls_remaining"] = len(state["user_totals_queue"])
    await trigger_bot_roll(message.channel, form, bot_user)


async def _handle_bot_roll_embed(message, form, bot_user, bot, cmd, total):
    state = form["game_state"]
    ticket_user_id = form["ticket_user_id"]

    pending_cmd_id = state.get("pending_bot_roll_cmd_id")
    if pending_cmd_id is not None and cmd.id != pending_cmd_id:
        return
    if pending_cmd_id is None and not (
        state.get("waiting_for_embed")
        and state.get("roll_initiator_id") == bot_user.id
    ) and not state.get("user_totals_queue"):
        return

    state["pending_bot_roll_cmd_id"] = None

    if state["user_totals_queue"]:
        you_total = state["user_totals_queue"].pop(0)
        state["bot_rolls_remaining"] = max(0, state.get("bot_rolls_remaining", 1) - 1)
        state["waiting_for_embed"] = False
        remaining = state["bot_rolls_remaining"]
        game_over = await _score_pair(
            message.channel, form, bot_user, bot, total, you_total, continue_batch=remaining > 0
        )
        if game_over:
            return
        if remaining > 0:
            await trigger_bot_roll(message.channel, form, bot_user)
        return

    prefetched = _take_prefetched_user_total(state)
    if prefetched is not None:
        state["waiting_for_embed"] = False
        state["pending_bot_total"] = None
        state["awaiting_user_after_bot"] = False
        state.pop("bot_first_embed_id", None)
        await _score_pair(message.channel, form, bot_user, bot, total, prefetched)
        return

    state["pending_bot_total"] = total
    state["bot_first_embed_id"] = message.id
    state["awaiting_user_after_bot"] = True
    state["pending_user_embeds"] = 0
    state["user_totals_queue"] = []
    state["current_player"] = "you"
    state["waiting_for_embed"] = False
    _try_activate_queued_user_rolls(state, ticket_user_id, bot_user.id)

    pending_ids = list(state.get("pending_roll_message_ids", []))
    for cmd_id in pending_ids:
        stashed = _take_prefetched_user_total(state, cmd_id)
        if stashed is None:
            continue
        _consume_user_roll_cmd(state, cmd_id)
        bot_total = state.get("pending_bot_total")
        if bot_total is None:
            state.setdefault("user_totals_queue", []).append(stashed)
            continue
        state["pending_bot_total"] = None
        state["awaiting_user_after_bot"] = False
        state.pop("bot_first_embed_id", None)
        state["waiting_for_embed"] = False
        await _score_pair(message.channel, form, bot_user, bot, bot_total, stashed)
        return


async def handle_da_hood_message(message, form, bot_user, bot):
    await handle_roll_embed(message, form, bot_user, bot)


async def start_game(channel, form, bot_user, bot=None):
    form["game_started"] = True
    form["ticket_channel_id"] = channel.id
    save_session_from_form(channel.id, form)
    responses = form["responses"]
    first_to = int(responses.get("first_to", "ft3").replace("ft", ""))

    first_raw = responses.get("first", "@eggdicer 1").replace(" 1", "").strip()
    ticket_user_id = form.get("ticket_user_id")
    if first_raw in ("@mention", "you") or (
        ticket_user_id and str(ticket_user_id) in first_raw
    ):
        first_player = "you"
    elif first_raw in ("@eggdicer", "me") or str(bot_user.id) in first_raw:
        first_player = "me"
    else:
        first_player = first_raw
    form["game_state"] = {
        "game_type": "dice",
        "first_to": first_to,
        "mode": responses.get("mode", "normal"),
        "gamemode": responses.get("gamemode", "fair"),
        "self_score": 0,
        "adder_score": 0,
        "first_player": first_player,
        "current_player": first_player,
        "waiting_for_embed": False,
        "roll_initiator_id": None,
        "user_totals_queue": [],
        "pending_bot_total": None,
        "awaiting_user_after_bot": False,
        "bot_first_embed_id": None,
        "consumed_embed_ids": set(),
        "pending_user_embeds": 0,
        "pending_roll_message_ids": [],
        "queued_user_roll_ids": [],
        "prefetched_user_totals": [],
        "consumed_roll_cmd_ids": set(),
        "bot_rolls_remaining": 0,
        "bot_roll_in_flight": False,
        "pending_bot_roll_cmd_id": None,
        "scoring": False,
    }
    roll_channel = await get_ticket_channel(bot, form) if bot else channel
    await do_next_roll(roll_channel, form, bot_user, bot)
