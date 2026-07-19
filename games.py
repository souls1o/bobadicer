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
# Da Hood format: "(display_name) rolled 5 & 6"
ROLLER_EMBED_PATTERN = re.compile(r"\(([^)]+)\)\s*rolled", re.IGNORECASE)


def _embed_text_parts(message):
    if not message.embeds:
        return []
    embed = message.embeds[0]
    parts = [embed.description or "", embed.title or ""]
    for field in embed.fields:
        parts.append(field.name or "")
        parts.append(field.value or "")
    footer = getattr(embed, "footer", None)
    if footer is not None and getattr(footer, "text", None):
        parts.append(footer.text)
    return parts


def _strip_md(text):
    return re.sub(r"[*_`~|]", "", text or "")


def _normalize_name(text):
    """Normalize for comparison: drop ZWSP/markdown, collapse spaces, casefold."""
    if not text:
        return ""
    import unicodedata
    cleaned = _strip_md(text)
    cleaned = unicodedata.normalize("NFKC", cleaned)
    cleaned = re.sub(r"[\u200b\u200c\u200d\ufeff\u00a0]", "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned.casefold()


def parse_roll_from_embed(message):
    for text in _embed_text_parts(message):
        match = ROLL_EMBED_PATTERN.search(text)
        if match:
            return int(match.group(1)), int(match.group(2))
    return None


def parse_roller_from_embed(message):
    """Extract display name from '(name) rolled …'."""
    for text in _embed_text_parts(message):
        clean = _strip_md(text)
        match = ROLLER_EMBED_PATTERN.search(clean)
        if match:
            return match.group(1).strip()
    return None


def names_from_author(author):
    """All name spellings discord.py exposes on a User/Member."""
    names = {
        getattr(author, "display_name", None),
        getattr(author, "global_name", None),
        getattr(author, "name", None),
        getattr(author, "nick", None),
    }
    return [n for n in names if n]


async def resolve_member(guild, user_id):
    if guild is None or user_id is None:
        return None
    member = guild.get_member(user_id)
    if member is not None:
        return member
    try:
        return await guild.fetch_member(user_id)
    except Exception:
        return None


async def names_for_roller(guild, author):
    """Prefer guild Member names (server nick is what Da Hood puts in embeds)."""
    names = set(names_from_author(author))
    member = await resolve_member(guild, getattr(author, "id", None))
    if member is not None:
        names.update(names_from_author(member))
    return [n for n in names if n]


async def roller_matches_author(roller_name, author, guild=None):
    """AND helper: embed '(name) rolled' vs -roll author (guild-nick aware)."""
    if not roller_name or author is None:
        return False
    needle = _normalize_name(roller_name)
    if not needle:
        return False
    for name in await names_for_roller(guild, author):
        if _normalize_name(name) == needle:
            return True
    return False


async def _find_nearest_roll_cmd(channel, embed_message, state):
    """Most recent unconsumed -roll before this embed (same rule as working selfbot)."""
    consumed = state.setdefault("consumed_roll_cmd_ids", set())
    async for msg in channel.history(limit=50, before=embed_message):
        if not is_roll_command(msg.content):
            continue
        if msg.id in consumed:
            continue
        return msg
    return None


async def get_ticket_channel(bot, form, fallback=None):
    if bot is None:
        return fallback
    channel = bot.get_channel(form["ticket_channel_id"])
    if channel is None:
        channel = await bot.fetch_channel(form["ticket_channel_id"])
    return channel


def is_bot_turn(state):
    return state["current_player"] in ("me", "@bobadice", "@gatodicer")


def _waiting_for_bot_embed(state, bot_user_id):
    return bool(
        state.get("bot_roll_in_flight")
        or (
            state.get("waiting_for_embed")
            and state.get("roll_initiator_id") == bot_user_id
        )
        or state.get("bot_rolls_remaining", 0) > 0
    )


async def get_roll_command_before_embed(
    channel, embed_message, *, initiator_id=None, exclude_author_id=None, after_message_id=None
):
    async for msg in channel.history(limit=30, before=embed_message):
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


def _bot_roll_outstanding(state, bot_user_id):
    """True when self has sent (or is sending) a -roll whose embed is not in yet."""
    if state.get("bot_roll_in_flight"):
        return True
    if state.get("pending_bot_total") is not None:
        return False
    return bool(
        state.get("last_bot_roll_msg_id")
        and state.get("waiting_for_embed")
        and state.get("roll_initiator_id") == bot_user_id
    )


def _bot_embed_pending(state, bot_user_id):
    """True when a -roll was already sent and its embed is still owed (not merely in-flight)."""
    if state.get("pending_bot_total") is not None:
        return False
    return bool(
        state.get("last_bot_roll_msg_id")
        and state.get("waiting_for_embed")
        and state.get("roll_initiator_id") == bot_user_id
    )


def _queue_out_of_turn_user_roll(state, message_id):
    queued = state.setdefault("queued_user_roll_ids", [])
    if message_id not in queued and message_id not in state.get("pending_roll_message_ids", []):
        queued.append(message_id)


def _stash_early_user_total(state, cmd_id, total, embed_id):
    early = state.setdefault("early_user_totals_by_cmd", {})
    early[cmd_id] = total
    state.setdefault("consumed_embed_ids", set()).add(embed_id)


def _pop_early_user_total(state, cmd_id):
    early = state.get("early_user_totals_by_cmd") or {}
    return early.pop(cmd_id, None)


async def _pair_pending_bot_with_user_total(channel, form, bot_user, bot, user_total, *, cmd_id=None):
    state = form["game_state"]
    bot_total = state.get("pending_bot_total")
    if bot_total is None:
        return False
    state["pending_bot_total"] = None
    state["awaiting_user_after_bot"] = False
    state.pop("bot_first_embed_id", None)
    if cmd_id is not None:
        _consume_user_roll_cmd(state, cmd_id)
        _pop_early_user_total(state, cmd_id)
    state["pending_user_embeds"] = 0
    state["user_totals_queue"] = []
    state["waiting_for_embed"] = False
    await _score_pair(channel, form, bot_user, bot, bot_total, user_total)
    return True


def _clear_bot_roll_wait(state, bot_user_id=None):
    """Drop bot-wait flags so a new -roll is allowed."""
    if bot_user_id is not None and state.get("roll_initiator_id") not in (None, bot_user_id):
        return
    state["waiting_for_embed"] = False
    state["last_bot_roll_msg_id"] = None
    if state.get("roll_initiator_id") == bot_user_id or bot_user_id is None:
        state["roll_initiator_id"] = None


async def trigger_bot_roll(roll_channel, form, bot_user):
    """Send exactly one bot -roll. Concurrent/duplicate calls are ignored."""
    state = form["game_state"]

    # Already rolling or already waiting on our embed — never send a second -roll
    if state.get("bot_roll_in_flight"):
        return
    if _bot_roll_outstanding(state, bot_user.id):
        return
    if state.get("pending_bot_total") is not None:
        return

    state["bot_roll_in_flight"] = True
    try:
        await asyncio.sleep(0.35)
        # Re-check after sleep in case state changed — do NOT use
        # _bot_roll_outstanding here (it is True while we hold in_flight).
        if state.get("pending_bot_total") is not None:
            return
        if _bot_embed_pending(state, bot_user.id):
            return

        hype = random.choice(config.ROLL_HYPE_MESSAGES)
        msg = await queued_send(roll_channel, f"-roll {hype}")
        # Only mark "waiting" after a real send — otherwise mid-game retries get blocked
        if msg is not None:
            state["last_bot_roll_msg_id"] = msg.id
            state["waiting_for_embed"] = True
            state["roll_initiator_id"] = bot_user.id
        else:
            _clear_bot_roll_wait(state, bot_user.id)
    except Exception as exc:
        print(f"[roll] bot -roll send failed: {exc}")
        _clear_bot_roll_wait(state, bot_user.id)
    finally:
        state["bot_roll_in_flight"] = False


def _accept_user_roll(state, message_id, ticket_user_id):
    """Always register a player -roll. Never drop it."""
    pending = state.setdefault("pending_roll_message_ids", [])
    state.setdefault("pending_user_embeds", 0)
    if message_id not in pending:
        pending.append(message_id)
        state["pending_user_embeds"] = state.get("pending_user_embeds", 0) + 1
    queued = state.setdefault("queued_user_roll_ids", [])
    if message_id in queued:
        queued.remove(message_id)
    state["waiting_for_embed"] = True
    if state.get("pending_user_embeds", 0) > 0:
        state["roll_initiator_id"] = ticket_user_id


def _try_activate_queued_user_rolls(state, ticket_user_id):
    queue = state.setdefault("queued_user_roll_ids", [])
    while queue:
        roll_id = queue.pop(0)
        _accept_user_roll(state, roll_id, ticket_user_id)


def _consume_user_roll_cmd(state, cmd_id):
    pending = state.get("pending_roll_message_ids", [])
    if cmd_id in pending:
        pending.remove(cmd_id)
        if state.get("pending_user_embeds", 0) > 0:
            state["pending_user_embeds"] -= 1
        state.setdefault("consumed_roll_cmd_ids", set()).add(cmd_id)
        return True
    queued = state.get("queued_user_roll_ids", [])
    if cmd_id in queued:
        queued.remove(cmd_id)
        state.setdefault("consumed_roll_cmd_ids", set()).add(cmd_id)
        return True
    state.setdefault("consumed_roll_cmd_ids", set()).add(cmd_id)
    return False


async def handle_user_roll(message, form, bot_user):
    """Register every player -roll instantly, then wait for its embed."""
    state = form["game_state"]
    ticket_user_id = form["ticket_user_id"]
    if message.author.id != ticket_user_id:
        return

    # Bot-first / bot already rolled: queue out-of-turn -rolls so they cannot
    # steal self's embed (which previously swapped me/you totals).
    if state.get("pending_bot_total") is None and (
        _bot_roll_outstanding(state, bot_user.id)
        or (is_bot_turn(state) and not state.get("awaiting_user_after_bot"))
    ):
        _queue_out_of_turn_user_roll(state, message.id)
        return

    _accept_user_roll(state, message.id, ticket_user_id)


async def _apply_user_roll_total(form, bot_user, bot, total, *, channel=None):
    state = form["game_state"]
    ticket_user_id = form["ticket_user_id"]
    state.setdefault("user_totals_queue", [])
    state.setdefault("pending_user_embeds", 0)
    state.setdefault("bot_rolls_remaining", 0)

    state["user_totals_queue"].append(total)

    if state.get("pending_user_embeds", 0) > 0:
        state["waiting_for_embed"] = True
        state["roll_initiator_id"] = ticket_user_id
        return

    # Extend an in-progress bot response batch — do not start a second roll
    if state.get("bot_rolls_remaining", 0) > 0 or _waiting_for_bot_embed(state, bot_user.id):
        if state.get("bot_rolls_remaining", 0) > 0:
            state["bot_rolls_remaining"] += 1
        else:
            # Bot roll already in flight for the first queued total
            state["bot_rolls_remaining"] = len(state["user_totals_queue"])
        return

    state["bot_rolls_remaining"] = len(state["user_totals_queue"])
    roll_channel = channel
    if roll_channel is None and bot is not None:
        roll_channel = await get_ticket_channel(bot, form)
    if roll_channel is None:
        return
    await trigger_bot_roll(roll_channel, form, bot_user)


async def _handle_user_roll_embed(message, form, bot_user, bot, total, *, cmd=None):
    state = form["game_state"]
    if cmd is not None:
        _consume_user_roll_cmd(state, cmd.id)
    elif state.get("pending_roll_message_ids"):
        _consume_user_roll_cmd(state, state["pending_roll_message_ids"][0])

    state.setdefault("consumed_embed_ids", set())
    state["consumed_embed_ids"].add(message.id)
    await _apply_user_roll_total(form, bot_user, bot, total, channel=message.channel)


def _reset_round_state(state, ticket_user_id=None, *, skip_next_roll=False):
    leftover_pending = list(state.get("pending_roll_message_ids", []))
    leftover_queued = list(state.get("queued_user_roll_ids", []))

    state["user_totals_queue"] = []
    state["pending_user_embeds"] = 0
    state["pending_roll_message_ids"] = []
    state["bot_rolls_remaining"] = 0
    state["pending_bot_total"] = None
    state["awaiting_user_after_bot"] = False
    state["bot_roll_in_flight"] = False
    state.pop("bot_first_embed_id", None)
    state["waiting_for_embed"] = False
    state["roll_initiator_id"] = None
    state["last_bot_roll_msg_id"] = None
    state["early_user_totals_by_cmd"] = {}
    if skip_next_roll:
        state["current_player"] = "you"
    else:
        state["current_player"] = state["first_player"]

    state["queued_user_roll_ids"] = leftover_queued
    for roll_id in leftover_pending:
        if roll_id not in state["queued_user_roll_ids"]:
            state["queued_user_roll_ids"].append(roll_id)

    # Only activate queued rolls when the player leads. If bot goes first,
    # keep them queued until self's embed is stored (pending_bot_total).
    if ticket_user_id is not None and state.get("current_player") == "you":
        _try_activate_queued_user_rolls(state, ticket_user_id)


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
            skip_next_roll=skip_next_roll,
        )

    await queued_send(ticket_channel, f"{state['self_score']}-{state['adder_score']}")

    if game_over:
        self_won = state["self_score"] >= first_to
        winner_id = bot_user.id if self_won else form["ticket_user_id"]
        await end_game(ticket_channel, form, self_won, bot_user, bot)
        return True

    if continue_batch:
        return False

    if state.get("pending_user_embeds", 0) > 0:
        return False

    # After a player-initiated pair, wait for the player — never auto-roll
    if skip_next_roll:
        return False

    await do_next_roll(roll_channel, form, bot_user, bot)
    return False


async def do_next_roll(roll_channel, form, bot_user, bot):
    state = form["game_state"]
    if state.get("game_type") != "dice":
        return
    if state.get("pending_user_embeds", 0) > 0:
        return
    if state.get("pending_bot_total") is not None:
        return
    if _waiting_for_bot_embed(state, bot_user.id):
        return
    if not is_bot_turn(state):
        return
    await trigger_bot_roll(roll_channel, form, bot_user)


async def handle_roll_embed(message, form, bot_user, bot):
    state = form["game_state"]
    state.setdefault("consumed_embed_ids", set())
    state.setdefault("consumed_roll_cmd_ids", set())
    if message.id in state["consumed_embed_ids"]:
        return
    # Da Hood may not always set .bot depending on client; allow by id too
    if not message.embeds:
        return
    if not (message.author.bot or message.author.id == DA_HOOD_BOT_ID):
        return

    rolls = parse_roll_from_embed(message)
    if not rolls:
        return

    # Claim immediately so concurrent handlers can't double-process after await
    state["consumed_embed_ids"].add(message.id)

    total = rolls[0] + rolls[1]
    ticket_user_id = form["ticket_user_id"]
    pending_bot_total = state.get("pending_bot_total")
    state.setdefault("user_totals_queue", [])
    state.setdefault("pending_user_embeds", 0)
    state.setdefault("bot_rolls_remaining", 0)
    state.setdefault("pending_roll_message_ids", [])
    state.setdefault("queued_user_roll_ids", [])
    state.setdefault("early_user_totals_by_cmd", {})

    bot_outstanding = _bot_roll_outstanding(state, bot_user.id)
    guild = getattr(message.channel, "guild", None)

    roller_name = parse_roller_from_embed(message)
    if not roller_name:
        preview = " | ".join(p for p in _embed_text_parts(message) if p)[:240]
        print(f"[roll] no '(name) rolled' parsed — embed text: {preview!r}")
        state["consumed_embed_ids"].discard(message.id)
        return

    # HARD RULE (same as working selfbot): nearest unconsumed -roll owns this embed
    nearest = await _find_nearest_roll_cmd(message.channel, message, state)
    if nearest is None:
        print(f"[roll] AND fail: name={roller_name!r} but no unconsumed -roll before embed")
        state["consumed_embed_ids"].discard(message.id)
        return

    is_user_cmd = nearest.author.id == ticket_user_id
    is_bot_cmd = nearest.author.id == bot_user.id
    cmd = nearest

    if not is_user_cmd and not is_bot_cmd:
        state["consumed_embed_ids"].discard(message.id)
        return

    # AND: '(name) rolled' should match that -roll author's guild nick/display/username.
    # Resolve Member so server nick is included (history authors are often bare Users).
    author_names = await names_for_roller(guild, nearest.author)
    needle = _normalize_name(roller_name)

    def _name_hits(n):
        target = _normalize_name(n)
        if not target:
            return False
        return needle == target or needle.startswith(target) or target.startswith(needle)

    name_ok = any(_name_hits(n) for n in author_names) if author_names else False
    if author_names and not name_ok:
        # Do not softlock the game: nearest -roll already identifies the roller.
        # Log the mismatch so we can see what Da Hood prints vs Discord names.
        print(
            f"[roll] name mismatch (continuing via -roll): embed_name={roller_name!r} "
            f"roll_author={nearest.author.id} names={author_names}"
        )
    elif not author_names:
        print(
            f"[roll] warning: no guild names for author {nearest.author.id}; "
            f"accepting via -roll (embed_name={roller_name!r})"
        )

    # Out-of-turn / early user embed while self still owed an embed
    if is_user_cmd and (
        bot_outstanding
        or (
            is_bot_turn(state)
            and state.get("pending_bot_total") is None
            and not state.get("awaiting_user_after_bot")
        )
    ):
        _stash_early_user_total(state, cmd.id, total, message.id)
        if (
            cmd.id not in state.get("queued_user_roll_ids", [])
            and cmd.id not in state.get("pending_roll_message_ids", [])
        ):
            _queue_out_of_turn_user_roll(state, cmd.id)
        return

    # --- Player embed ---
    if is_user_cmd:
        _try_activate_queued_user_rolls(state, ticket_user_id)

        if pending_bot_total is not None:
            await _pair_pending_bot_with_user_total(
                message.channel, form, bot_user, bot, total, cmd_id=cmd.id
            )
            return

        await _handle_user_roll_embed(message, form, bot_user, bot, total, cmd=cmd)
        return

    # --- Bot embed ---
    state.setdefault("consumed_roll_cmd_ids", set()).add(cmd.id)

    # Pair with queued player totals
    if state["user_totals_queue"]:
        you_total = state["user_totals_queue"].pop(0)
        state["bot_rolls_remaining"] = max(0, state.get("bot_rolls_remaining", 1) - 1)
        remaining = state["bot_rolls_remaining"]
        game_over = await _score_pair(
            message.channel, form, bot_user, bot, total, you_total,
            continue_batch=remaining > 0,
            skip_next_roll=True,
        )
        if game_over:
            return
        if remaining > 0:
            _clear_bot_roll_wait(state, bot_user.id)
            await trigger_bot_roll(message.channel, form, bot_user)
        else:
            state["waiting_for_embed"] = False
            _try_activate_queued_user_rolls(state, ticket_user_id)
        return

    if state.get("bot_rolls_remaining", 0) > 0:
        state["bot_rolls_remaining"] = 0
        _clear_bot_roll_wait(state, bot_user.id)
        print("[roll] bot embed arrived with empty user queue during batch — cleared stuck batch")
        return

    # Bot went first this round
    state["pending_bot_total"] = total
    state["bot_first_embed_id"] = message.id
    state["awaiting_user_after_bot"] = True
    state["current_player"] = "you"
    state["waiting_for_embed"] = False
    state["roll_initiator_id"] = None
    state["last_bot_roll_msg_id"] = None
    _try_activate_queued_user_rolls(state, ticket_user_id)

    # Out-of-turn user embed already arrived — score with correct me/you sides
    for cmd_id in list(state.get("pending_roll_message_ids", [])):
        early_total = state.get("early_user_totals_by_cmd", {}).get(cmd_id)
        if early_total is not None:
            await _pair_pending_bot_with_user_total(
                message.channel, form, bot_user, bot, early_total, cmd_id=cmd_id
            )
            return
    for cmd_id, early_total in list(state.get("early_user_totals_by_cmd", {}).items()):
        await _pair_pending_bot_with_user_total(
            message.channel, form, bot_user, bot, early_total, cmd_id=cmd_id
        )
        return


async def handle_da_hood_message(message, form, bot_user, bot):
    await handle_roll_embed(message, form, bot_user, bot)


async def start_game(channel, form, bot_user, bot=None):
    form["game_started"] = True
    form["ticket_channel_id"] = channel.id
    save_session_from_form(channel.id, form)
    responses = form["responses"]
    first_to = int(responses.get("first_to", "ft3").replace("ft", ""))

    first_raw = responses.get("first", "@bobadice 1").replace(" 1", "").strip()
    ticket_user_id = form.get("ticket_user_id")
    if first_raw in ("@mention", "you") or (
        ticket_user_id and str(ticket_user_id) in first_raw
    ):
        first_player = "you"
    elif first_raw in ("@bobadice", "me") or str(bot_user.id) in first_raw:
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
        "consumed_roll_cmd_ids": set(),
        "pending_user_embeds": 0,
        "pending_roll_message_ids": [],
        "queued_user_roll_ids": [],
        "bot_rolls_remaining": 0,
        "bot_roll_in_flight": False,
        "last_bot_roll_msg_id": None,
    }
    roll_channel = await get_ticket_channel(bot, form) if bot else channel
    await do_next_roll(roll_channel, form, bot_user, bot)
