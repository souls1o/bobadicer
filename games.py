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


def _embed_text_parts(message):
    if not message.embeds:
        return []
    embed = message.embeds[0]
    parts = [embed.description or "", embed.title or ""]
    for field in embed.fields:
        parts.append(field.name or "")
        parts.append(field.value or "")
    return parts


def roll_cmd_matches_user(cmd, user_id, *, last_bot_roll=None):
    if cmd is None or cmd.author.id != user_id:
        return False
    if last_bot_roll is not None and cmd.id <= last_bot_roll:
        return False
    return True


def roll_embed_valid_for_user(cmd, user_id, *, last_bot_roll=None):
    return roll_cmd_matches_user(cmd, user_id, last_bot_roll=last_bot_roll)


def roll_embed_valid_for_bot(cmd, bot_user):
    return cmd is not None and cmd.author.id == bot_user.id


async def get_ticket_channel(bot, form, fallback=None):
    if bot is None:
        return fallback
    channel = bot.get_channel(form["ticket_channel_id"])
    if channel is None:
        channel = await bot.fetch_channel(form["ticket_channel_id"])
    return channel


def is_bot_turn(state):
    return state["current_player"] in ("me", "@gatodicer")


async def get_roll_command_before_embed(
    channel, embed_message, *, initiator_id=None, exclude_author_id=None, after_message_id=None
):
    async for msg in channel.history(limit=50, before=embed_message):
        if not is_roll_command(msg.content):
            continue
        if after_message_id and msg.id <= after_message_id:
            continue
        if exclude_author_id and msg.author.id == exclude_author_id:
            continue
        if initiator_id and msg.author.id != initiator_id:
            continue
        return msg
    return None


async def trigger_bot_roll(roll_channel, form, bot_user):
    state = form["game_state"]
    await asyncio.sleep(1)
    hype = random.choice(config.ROLL_HYPE_MESSAGES)
    msg = await queued_send(roll_channel, f"-roll {hype}")
    state["waiting_for_embed"] = True
    state["roll_initiator_id"] = bot_user.id
    if msg is not None:
        state["last_bot_roll_msg_id"] = msg.id


def _queue_user_roll(state, message_id):
    pending = state.setdefault("pending_roll_message_ids", [])
    queued = state.setdefault("queued_user_roll_ids", [])
    if message_id not in pending and message_id not in queued:
        queued.append(message_id)


def _accept_user_roll(state, message_id, ticket_user_id):
    pending = state.setdefault("pending_roll_message_ids", [])
    state.setdefault("pending_user_embeds", 0)
    if message_id not in pending:
        pending.append(message_id)
        state["pending_user_embeds"] += 1
    state["waiting_for_embed"] = True
    state["roll_initiator_id"] = ticket_user_id


def _user_can_accept_rolls(state, bot_user_id):
    if state.get("awaiting_user_after_bot") or state.get("pending_bot_total") is not None:
        return True
    if not is_bot_turn(state):
        waiting = state.get("waiting_for_embed")
        initiator = state.get("roll_initiator_id")
        if waiting and initiator == bot_user_id:
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
    pending = state.get("pending_roll_message_ids", [])
    if cmd_id in pending:
        pending.remove(cmd_id)
        if state.get("pending_user_embeds", 0) > 0:
            state["pending_user_embeds"] -= 1
        return
    queued = state.get("queued_user_roll_ids", [])
    if cmd_id in queued:
        queued.remove(cmd_id)


async def handle_user_roll(message, form, bot_user):
    state = form["game_state"]
    ticket_user_id = form["ticket_user_id"]
    if message.author.id != ticket_user_id:
        return

    if state.get("awaiting_user_after_bot") or state.get("pending_bot_total") is not None:
        _accept_user_roll(state, message.id, ticket_user_id)
        return

    if _user_can_accept_rolls(state, bot_user.id):
        _accept_user_roll(state, message.id, ticket_user_id)
    else:
        _queue_user_roll(state, message.id)


def _reset_round_state(state, ticket_user_id=None, bot_user_id=None, *, skip_next_roll=False):
    state["user_totals_queue"] = []
    state["pending_user_embeds"] = 0
    state["pending_roll_message_ids"] = []
    state["bot_rolls_remaining"] = 0
    state["pending_bot_total"] = None
    state["awaiting_user_after_bot"] = False
    state.pop("bot_first_embed_id", None)
    state["waiting_for_embed"] = False
    state["roll_initiator_id"] = None
    if skip_next_roll:
        state["current_player"] = "you"
    else:
        state["current_player"] = state["first_player"]
    if ticket_user_id is not None and bot_user_id is not None:
        _try_activate_queued_user_rolls(state, ticket_user_id, bot_user_id)


async def _handle_user_roll_embed(message, form, bot_user, bot, total):
    state = form["game_state"]
    ticket_user_id = form["ticket_user_id"]
    state.setdefault("user_totals_queue", [])
    state.setdefault("pending_user_embeds", 0)
    state.setdefault("bot_rolls_remaining", 0)

    state["user_totals_queue"].append(total)
    state["consumed_embed_ids"].add(message.id)

    if state.get("pending_user_embeds", 0) > 0:
        state["waiting_for_embed"] = True
        state["roll_initiator_id"] = ticket_user_id
        return

    state["waiting_for_embed"] = False
    state["bot_rolls_remaining"] = len(state["user_totals_queue"])
    await trigger_bot_roll(message.channel, form, bot_user)


def _pair_winner(me_total, you_total, gamemode, roll_mode):
    if gamemode == "ties" and me_total == you_total:
        return "me"
    if me_total == you_total:
        return None
    if roll_mode == "crazy":
        return "me" if me_total < you_total else "you"
    return "me" if me_total > you_total else "you"


async def _score_pair(roll_channel, form, bot_user, bot, me_total, you_total, *, continue_batch=False, skip_next_roll=False):
    state = form["game_state"]
    winner = _pair_winner(me_total, you_total, state["gamemode"], state["mode"])
    ticket_channel = await get_ticket_channel(bot, form, fallback=roll_channel)

    if winner == "me":
        state["self_score"] += 1
    elif winner == "you":
        state["adder_score"] += 1

    first_to = state["first_to"]
    game_over = state["self_score"] >= first_to or state["adder_score"] >= first_to

    if not continue_batch and not game_over:
        _reset_round_state(
            state,
            form["ticket_user_id"],
            bot_user.id,
            skip_next_roll=skip_next_roll,
        )

    await queued_send(ticket_channel, f"{state['self_score']}-{state['adder_score']}")

    if game_over:
        self_won = state["self_score"] >= first_to
        winner_id = bot_user.id if self_won else form["ticket_user_id"]
        await queued_send(ticket_channel, f"<@{winner_id}> won!")
        await end_game(ticket_channel, form, self_won, bot_user, bot)
        return True

    if continue_batch:
        return False

    if not skip_next_roll:
        await do_next_roll(roll_channel, form, bot_user, bot)
    return False


async def do_next_roll(roll_channel, form, bot_user, bot):
    state = form["game_state"]
    if state.get("game_type") != "dice" or state.get("waiting_for_embed"):
        return
    if is_bot_turn(state):
        await trigger_bot_roll(roll_channel, form, bot_user)


def parse_roll_from_embed(message):
    for text in _embed_text_parts(message):
        match = ROLL_EMBED_PATTERN.search(text)
        if match:
            return int(match.group(1)), int(match.group(2))
    return None


async def _find_roll_command(channel, embed_message, form, bot_user, *, pending_bot_total=None):
    ticket_user_id = form["ticket_user_id"]
    if pending_bot_total is not None:
        return await get_roll_command_before_embed(
            channel,
            embed_message,
            exclude_author_id=bot_user.id,
            after_message_id=form["game_state"].get("bot_first_embed_id"),
        )

    state = form["game_state"]
    cmd = await get_roll_command_before_embed(
        channel, embed_message, initiator_id=state.get("roll_initiator_id")
    )
    if cmd:
        return cmd
    return await get_roll_command_before_embed(
        channel, embed_message, initiator_id=ticket_user_id
    )


async def handle_roll_embed(message, form, bot_user, bot):
    state = form["game_state"]
    state.setdefault("consumed_embed_ids", set())
    if message.id in state["consumed_embed_ids"]:
        return
    if not message.author.bot or not message.embeds:
        return

    ticket_user_id = form["ticket_user_id"]
    pending_bot_total = state.get("pending_bot_total")
    expects_embed = (
        state.get("waiting_for_embed")
        or pending_bot_total is not None
        or state.get("bot_rolls_remaining", 0) > 0
        or state.get("pending_user_embeds", 0) > 0
    )
    if not expects_embed:
        return

    rolls = parse_roll_from_embed(message)
    if not rolls:
        return

    cmd = await _find_roll_command(
        message.channel, message, form, bot_user, pending_bot_total=pending_bot_total
    )
    if not cmd:
        return

    total = rolls[0] + rolls[1]
    state.setdefault("user_totals_queue", [])
    state.setdefault("pending_user_embeds", 0)
    state.setdefault("bot_rolls_remaining", 0)

    if pending_bot_total is not None and cmd.author.id != bot_user.id:
        if not roll_embed_valid_for_user(cmd, ticket_user_id):
            return
        bot_total = pending_bot_total
        state["pending_bot_total"] = None
        state["awaiting_user_after_bot"] = False
        state.pop("bot_first_embed_id", None)
        state["pending_user_embeds"] = 0
        state["user_totals_queue"] = []
        state["waiting_for_embed"] = False
        state["consumed_embed_ids"].add(message.id)
        _consume_user_roll_cmd(state, cmd.id)
        await _score_pair(message.channel, form, bot_user, bot, bot_total, total)
        return

    if cmd.author.id == ticket_user_id:
        last_bot_roll = state.get("last_bot_roll_msg_id")
        if not roll_embed_valid_for_user(cmd, ticket_user_id, last_bot_roll=last_bot_roll):
            return
        _try_activate_queued_user_rolls(state, ticket_user_id, bot_user.id)
        _consume_user_roll_cmd(state, cmd.id)
        await _handle_user_roll_embed(message, form, bot_user, bot, total)
        return

    if not state.get("waiting_for_embed") and not state["user_totals_queue"]:
        return

    if state["user_totals_queue"]:
        if not roll_embed_valid_for_bot(cmd, bot_user):
            return
        you_total = state["user_totals_queue"].pop(0)
        state["bot_rolls_remaining"] -= 1
        state["waiting_for_embed"] = False
        remaining = state["bot_rolls_remaining"]
        state["consumed_embed_ids"].add(message.id)
        game_over = await _score_pair(
            message.channel, form, bot_user, bot, total, you_total,
            continue_batch=remaining > 0,
            skip_next_roll=True,
        )
        if game_over:
            return
        if remaining > 0:
            await trigger_bot_roll(message.channel, form, bot_user)
        return

    if not roll_embed_valid_for_bot(cmd, bot_user):
        return

    state["pending_bot_total"] = total
    state["bot_first_embed_id"] = message.id
    state["awaiting_user_after_bot"] = True
    state["pending_user_embeds"] = 0
    state["user_totals_queue"] = []
    state["current_player"] = "you"
    state["waiting_for_embed"] = False
    state["consumed_embed_ids"].add(message.id)
    _try_activate_queued_user_rolls(state, ticket_user_id, bot_user.id)


async def handle_da_hood_message(message, form, bot_user, bot):
    await handle_roll_embed(message, form, bot_user, bot)


async def start_game(channel, form, bot_user, bot=None):
    form["game_started"] = True
    form["ticket_channel_id"] = channel.id
    save_session_from_form(channel.id, form)
    responses = form["responses"]
    first_to = int(responses.get("first_to", "ft3").replace("ft", ""))

    first_raw = responses.get("first", "@gatodicer 1").replace(" 1", "").strip()
    ticket_user_id = form.get("ticket_user_id")
    if first_raw in ("@mention", "you") or (
        ticket_user_id and str(ticket_user_id) in first_raw
    ):
        first_player = "you"
    elif first_raw in ("@gatodicer", "me") or str(bot_user.id) in first_raw:
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
        "bot_rolls_remaining": 0,
        "last_bot_roll_msg_id": None,
    }
    roll_channel = await get_ticket_channel(bot, form) if bot else channel
    await do_next_roll(roll_channel, form, bot_user, bot)
