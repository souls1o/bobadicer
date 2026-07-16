import random

import discord

import config
from bets import (
    add_wagered_usd,
    bet_validator,
    calculate_my_bet,
    deduct_hold_up_to,
    extract_crypto_address,
    format_bet_display,
    get_bet_info,
    get_hold_usd,
    get_max_bet,
    get_price,
    get_wager_usd,
    normalize_coin,
    sync_hold_crypto,
    usd_to_smallest_unit,
)
from services import get_deposit_address, send_apirone
from send_queue import queued_reply, queued_send
from state import (
    active_forms,
    cancel_rerun_timeout,
    finish_form,
    get_form,
    get_hold_data,
    get_ticket_session,
    is_ticket_channel,
    is_ticket_closed,
    new_form_dict,
    register_ticket_channel,
    reopen_ticket_for_new_form,
    save_session_from_form,
    should_skip_payment,
    ticket_channels,
    ticket_has_played,
)

LISTEN_ROLES = [1258727325265297408, 1258732498482106398]
VALIDATORS = {"bet_validator": bet_validator}

DM_GAMEMODES_TEXT = """**🎲 Dice Gamemodes**
1. **I Win Ties** — FT3 → 20% MORE | FT5 → 30% MORE Bet
2. **Fair** — 10% LOWER Bet"""


def build_dm_gamemodes_text():
    return DM_GAMEMODES_TEXT


def build_dm_help_text(user_id):
    lines = [
        "**💡 Commands**",
        "!help — show this list",
        "!gamemodes — dice gamemode info",
        "!housebal — house LTC balance in USD",
        "",
        "**🎟️ Ticket commands**",
        "`!hold` — show current winnings for this ticket",
        "`!rerun` — start a new form in this ticket (keeps hold)",
        "`!ltc` / `!btc` / `!eth` / `!bnb` / `!tron` / `!sol` — deposit addresses",
    ]
    if user_id == config.ADMIN_USER_ID:
        lines.extend([
            "",
            "**🔧 Admin**",
            "!toggle testing — skip payment step when you are the ticket player",
            "!setchannel <id> — set auto-post channel",
        ])
    return "\n".join(lines)


def channel_can_send(channel):
    if not isinstance(channel, discord.TextChannel):
        return False
    me = channel.guild.me
    if me is None:
        return True
    perms = channel.permissions_for(me)
    return perms.view_channel and perms.send_messages


async def safe_channel_send(channel, content, *, form=None):
    if not channel_can_send(channel):
        print(f"[skip] no send permission in #{getattr(channel, 'name', '?')} ({channel.id})")
        if form is not None:
            finish_form(channel, form)
        return None
    try:
        return await queued_send(channel, content)
    except discord.Forbidden:
        print(f"[forbidden] cannot send in #{getattr(channel, 'name', '?')} ({channel.id})")
        if form is not None:
            finish_form(channel, form)
        return None


def is_roll_command(content):
    return (content or "").strip().lower().startswith("-roll")


def member_has_listen_role(member):
    return any(role.id in LISTEN_ROLES for role in member.roles)


def is_adder_confirm(content):
    text = (content or "").strip().lower()
    return text.startswith("conf")


def message_references_bot(message, bot_user):
    content = message.content or ""
    if "trumpdicer" in content.lower():
        return True
    if str(bot_user.id) in content:
        return True
    if f"<@{bot_user.id}>" in content or f"<@!{bot_user.id}>" in content:
        return True
    return any(user.id == bot_user.id for user in message.mentions)


def _overwrite_target_ids(channel):
    overwrites = getattr(channel, "overwrites", None)
    if not overwrites:
        return set()
    return {getattr(target, "id", None) for target in overwrites}


def is_channel_blacklisted(channel):
    """Accept a channel object, channel id, or channel name."""
    if channel is None:
        return False
    channel_id = getattr(channel, "id", None)
    channel_name = getattr(channel, "name", None)
    if isinstance(channel, (int, str)) and not hasattr(channel, "id"):
        # raw id or name passed directly
        entry = channel
        for item in config.CHANNEL_BLACKLIST:
            if isinstance(item, int) and isinstance(entry, int) and item == entry:
                return True
            if str(item).lower() == str(entry).lower():
                return True
        return False

    name_lower = (channel_name or "").lower()
    for item in config.CHANNEL_BLACKLIST:
        if isinstance(item, int):
            if channel_id is not None and item == channel_id:
                return True
        else:
            if name_lower and str(item).lower() == name_lower:
                return True
            if channel_id is not None and str(item) == str(channel_id):
                return True
    return False


def was_bot_added_to_channel(channel, bot_user, before=None):
    if is_channel_blacklisted(channel):
        return False
    member = channel.guild.get_member(bot_user.id)
    if member is None:
        return False
    try:
        can_view = channel.permissions_for(member).view_channel
    except Exception:
        return False
    if not can_view:
        return False

    bot_id = bot_user.id
    if bot_id in _overwrite_target_ids(channel):
        return True
    if before is None:
        return False

    try:
        if not before.permissions_for(member).view_channel:
            return True
    except Exception:
        return True

    before_ids = _overwrite_target_ids(before)
    after_ids = _overwrite_target_ids(channel)
    if bot_id in after_ids and bot_id not in before_ids:
        return True

    role_ids = {role.id for role in member.roles}
    return bool(role_ids & (after_ids - before_ids))


def should_process_channel(channel, message=None, bot_user=None):
    if is_channel_blacklisted(channel):
        return False
    if is_ticket_channel(channel):
        return True
    if message is not None and bot_user is not None and message_references_bot(message, bot_user):
        return True
    return False


async def resolve_ticket_user_id(channel, bot_user, *, was_tracked=False):
    session = get_ticket_session(channel.id)
    if session.get("ticket_user_id"):
        return session["ticket_user_id"]

    ticket_user_id = None
    bot_referenced = False
    async for msg in channel.history(limit=30):
        if message_references_bot(msg, bot_user):
            bot_referenced = True
            ticket_user_id = msg.author.id
            break
    if not bot_referenced and not was_tracked:
        return None
    if not ticket_user_id:
        async for msg in channel.history(limit=30):
            if not msg.author.bot:
                ticket_user_id = msg.author.id
                break
    return ticket_user_id


async def handle_bot_added_to_channel(bot, channel):
    register_ticket_channel(channel.id)


def ticket_mention(channel, form):
    user = channel.guild.get_member(form["ticket_user_id"])
    return user.mention if user else f"<@{form['ticket_user_id']}>"


def format_text(text, mention, responses, bot_user, dynamic=None):
    dynamic = dynamic or {}
    result = text.replace("@mention", mention).replace("@trumpdicer", bot_user.mention)
    for key, value in {**responses, **dynamic}.items():
        result = result.replace(f"{{{key}}}", str(value))
    return result


def build_confirm_text(channel, form, bot_user):
    mention = ticket_mention(channel, form)
    responses = form.get("responses", {})
    first_to = responses.get("first_to", "ft3")
    gamemode_key = responses.get("gamemode", "fair")
    first = responses.get("first", "@trumpdicer 1").replace("@mention", mention).replace("@trumpdicer", bot_user.mention)
    mode = responses.get("mode", "normal")
    mode_part = "" if mode == "normal" else f"{mode} "

    gamemode_text = {
        "ties": f", {bot_user.mention} wins ties",
        "fair": "",
    }.get(gamemode_key, "")

    return f"{first_to} {mode_part}{first}{gamemode_text}"


async def _skip_payment_step(channel, form, bot_user):
    wager_usd = get_wager_usd(form)
    add_wagered_usd(form, wager_usd)
    form["payout_address"] = "testing"
    form["waiting_for_address"] = False
    save_session_from_form(channel.id, form)
    form["step"] += 1
    await ask_next_step(channel, bot_user)


async def start_ticket_form(channel, bot_user, bot=None):
    if is_channel_blacklisted(channel):
        return
    if is_ticket_closed(channel.id) or ticket_has_played(channel.id):
        # After a game, only !rerun / "yes" may start a new form — never mentions
        return
    if get_form(channel.id):
        return

    was_tracked = channel.id in ticket_channels

    if not channel_can_send(channel):
        return

    ticket_user_id = await resolve_ticket_user_id(channel, bot_user, was_tracked=was_tracked)
    if not ticket_user_id:
        return

    register_ticket_channel(channel.id)
    active_forms[channel.id] = new_form_dict(channel.id, ticket_user_id)
    await ask_next_step(channel, bot_user)


async def start_fresh_form(channel, bot_user, *, ticket_user_id=None):
    """Start a brand-new form in this ticket (yes / !rerun). Keeps hold + payout address."""
    cancel_rerun_timeout(get_form(channel.id))
    session = reopen_ticket_for_new_form(channel.id)
    user_id = ticket_user_id or session.get("ticket_user_id")
    if not user_id:
        user_id = await resolve_ticket_user_id(channel, bot_user, was_tracked=True)
    if not user_id:
        await queued_send(channel, "❌ Could not find ticket player for new form.")
        return False

    form = new_form_dict(channel.id, user_id)
    # Carry session money state; wipe game answers so the full form is asked again
    form["responses"] = {"game": "dice"}
    form["step"] = 0
    form["waiting_for_rerun"] = False
    form["waiting_for_rerun_amount"] = False
    form["waiting_for_confirm"] = False
    form["waiting_for_adder_confirm"] = False
    form["waiting_for_address"] = False
    form.pop("game_state", None)
    form.pop("pending_hold_deduction", None)
    form.pop("confirm_text", None)
    active_forms[channel.id] = form
    save_session_from_form(channel.id, form)
    await ask_next_step(channel, bot_user)
    return True


async def ask_next_step(channel, bot_user):
    form = get_form(channel.id)
    if not form:
        return

    if form["step"] >= len(config.FORM_QUESTIONS):
        return

    q = config.FORM_QUESTIONS[form["step"]]
    mention = ticket_mention(channel, form)
    responses = form.get("responses", {})
    responses.setdefault("game", "dice")
    dynamic = {"max_bet": get_max_bet(form), "game_emoji": "Dices"}
    question_text = format_text(q.get("text", ""), mention, responses, bot_user, dynamic)

    if q["type"] in ("choice", "open"):
        await safe_channel_send(channel, question_text, form=form)
        return

    if q["type"] == "listen_address":
        if should_skip_payment(form):
            await _skip_payment_step(channel, form, bot_user)
            return
        dynamic.update({
            "coin": normalize_coin(),
            "my_bet": format_bet_display(calculate_my_bet(form) or 0),
            "his_bet": format_bet_display(responses.get("bet", "0").split()[0]),
        })
        question_text = format_text(q.get("text", ""), mention, responses, bot_user, dynamic)
        form["waiting_for_address"] = True
    elif q["type"] == "listen_confirm":
        question_text = build_confirm_text(channel, form, bot_user)
        form["confirm_text"] = question_text
        form["waiting_for_confirm"] = True

    await safe_channel_send(channel, question_text, form=form)


async def handle_form_step(message, form, bot_user):
    if form["step"] >= len(config.FORM_QUESTIONS):
        return
    if form["ticket_user_id"] != message.author.id:
        return

    q = config.FORM_QUESTIONS[form["step"]]
    response = message.content.strip()
    upper_resp = response.upper()

    if q["type"] == "choice":
        output_value = None
        random_inputs = q["mapping"].get("random", [])
        if upper_resp in ("RANDOM", "R") or any(upper_resp == inp.upper() for inp in random_inputs):
            options = [val for val in q["mapping"] if val.lower() != "random"]
            output_value = random.choice(options) if options else None
        else:
            for val, inputs in q["mapping"].items():
                if val.lower() == "random":
                    continue
                if any(upper_resp == inp.upper() for inp in inputs):
                    output_value = val
                    break
        if output_value is None:
            return
        if q.get("short_key"):
            form["responses"][q["short_key"]] = output_value
        form["step"] += 1
        await ask_next_step(message.channel, bot_user)
        return

    if q["type"] == "open":
        validator = VALIDATORS.get(q.get("validator"))
        if validator and not validator(response, form):
            await queued_reply(message, "❌ Invalid format or out of range.")
            return
        if q.get("short_key"):
            form["responses"][q["short_key"]] = response
        form["step"] += 1
        await ask_next_step(message.channel, bot_user)


async def handle_ticket_command(message, bot_user, bot=None):
    content = message.content.strip().lower()

    if content in config.COIN_ADDRESS_COMMANDS:
        coin = config.COIN_ADDRESS_COMMANDS[content]
        address = get_deposit_address(coin)
        if address:
            await queued_send(message.channel, f"`{address}`")
        else:
            await queued_send(message.channel, f"❌ No {coin.upper()} address configured.")
        return True

    if content == "!hold":
        await handle_hold_command(message)
        return True

    if content == "!rerun":
        await handle_rerun_command(message, bot_user, bot)
        return True

    return False


async def handle_hold_command(message):
    hold_usd, hold_crypto, coin = get_hold_data(message.channel.id)
    await queued_send(
        message.channel,
        f"**Hold for this ticket**\n"
        f"**USD:** ${hold_usd:.2f}\n"
        f"**{coin.upper()}:** {hold_crypto}\n"
        f"-# Available for payout or to cover your next rerun wager",
    )


async def handle_rerun_command(message, bot_user, bot=None):
    channel = message.channel
    form = get_form(channel.id)

    if form and form.get("game_state"):
        await queued_send(channel, "❌ Cannot rerun — a game is currently in progress.")
        return

    # !rerun always starts a fresh form (new gamemode/bet/etc.), keeping hold
    if form:
        cancel_rerun_timeout(form)
        form["waiting_for_rerun"] = False
        form["waiting_for_rerun_amount"] = False
        save_session_from_form(channel.id, form)

    await start_fresh_form(channel, bot_user, ticket_user_id=(form or {}).get("ticket_user_id"))


async def handle_global_listeners(message, bot_user, start_game_fn, bot=None):
    form = get_form(message.channel.id)
    if not form:
        return

    if form.get("waiting_for_rerun"):
        from postgame import handle_rerun_response
        if await handle_rerun_response(message, form, bot_user, start_game_fn, bot):
            return
        if message.channel.id not in active_forms:
            return

    form = get_form(message.channel.id)
    if not form:
        return

    if form.get("waiting_for_address") and member_has_listen_role(message.author):
        _, _, coin = get_bet_info(form)
        address = extract_crypto_address(message.content, coin)
        if address:
            wager_usd = get_wager_usd(form)
            hold = get_hold_usd(form)
            form["payout_address"] = address

            if hold >= wager_usd:
                # Sufficient hold — deduct only after confirm
                form["pending_hold_deduction"] = True
                form["waiting_for_address"] = False
                save_session_from_form(message.channel.id, form)
                await queued_send(
                    message.channel,
                    f"✅ Using {format_bet_display(wager_usd)} from hold for this game (deducted after confirm).",
                )
                form["step"] += 1
                await ask_next_step(message.channel, bot_user)
                return

            covered = deduct_hold_up_to(form, hold, coin) if hold > 0 else 0.0
            shortfall = round(wager_usd - covered, 2)
            amount = usd_to_smallest_unit(shortfall, coin, get_price(coin))
            result = await send_apirone(coin, address, amount)
            if "error" in result:
                err = result["error"]
                if covered > 0:
                    form["winnings_usd"] = round(get_hold_usd(form) + covered, 8)
                    sync_hold_crypto(form, coin)
                await queued_send(
                    message.channel,
                    f"❌ Transfer failed: {err if isinstance(err, str) else err}",
                )
                return
            form["pending_hold_deduction"] = False
            form["waiting_for_address"] = False
            add_wagered_usd(form, wager_usd)
            save_session_from_form(message.channel.id, form)
            if covered > 0:
                await queued_send(
                    message.channel,
                    f"📤 Used {format_bet_display(covered)} from hold and sent "
                    f"{format_bet_display(shortfall)} {coin.upper()} to {address}",
                )
            else:
                await queued_send(
                    message.channel,
                    f"📤 Sent {format_bet_display(shortfall)} {coin.upper()} to {address}",
                )
            form["step"] += 1
            await ask_next_step(message.channel, bot_user)

    if form.get("waiting_for_confirm"):
        expected = form.get("confirm_text")

        if expected and message.content.strip() == expected.strip() and member_has_listen_role(message.author):
            form["game_confirmer_user_id"] = message.author.id
            await queued_reply(message, "conf")
            form["waiting_for_adder_confirm"] = True

        if (
            form.get("waiting_for_adder_confirm")
            and message.author.id == form["ticket_user_id"]
            and is_adder_confirm(message.content)
        ):
            if form.get("pending_hold_deduction"):
                from postgame import deduct_hold_on_confirm
                if not await deduct_hold_on_confirm(message.channel, form):
                    form["waiting_for_adder_confirm"] = False
                    form["waiting_for_confirm"] = False
                    return
            form["waiting_for_confirm"] = False
            form["waiting_for_adder_confirm"] = False
            await start_game_fn(message.channel, form, bot_user, bot)
