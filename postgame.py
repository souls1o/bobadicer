import asyncio
import re

import config
from bets import (
    add_wagered_usd,
    apply_win_to_hold,
    bet_validator,
    deduct_hold_up_to,
    format_bet_display,
    get_bet_info,
    get_hold_usd,
    get_price,
    get_wager_usd,
    usd_to_smallest_unit,
)
from services import get_payout_address, send_apirone
from send_queue import queued_send
from state import cancel_rerun_timeout, finish_form, get_form, save_session_from_form, should_skip_payment
from forms import build_confirm_text, ticket_mention

RERUN_TIMEOUT_SECONDS = 180
GAME_NUMBER_PATTERN = re.compile(r"#(\d+)")


async def _bootstrap_game_number(guild, bot=None):
    channel = guild.get_channel(config.GAME_LOG_CHANNEL_ID)
    if channel is None and bot is not None:
        try:
            channel = await bot.fetch_channel(config.GAME_LOG_CHANNEL_ID)
        except Exception:
            channel = None
    if channel is None:
        return 1
    async for msg in channel.history(limit=25):
        match = GAME_NUMBER_PATTERN.search(msg.content or "")
        if match:
            return int(match.group(1)) + 1
    return 1


async def get_next_game_number(guild, bot=None):
    return await _bootstrap_game_number(guild, bot)


async def _get_guild_channel(guild, channel_id, bot=None):
    channel = guild.get_channel(channel_id)
    if channel is None and bot is not None:
        try:
            channel = await bot.fetch_channel(channel_id)
        except Exception:
            return None
    return channel


async def post_victory_message(guild, form, bot=None):
    confirmer_id = form.get("game_confirmer_user_id")
    if not confirmer_id:
        return
    channel = await _get_guild_channel(guild, config.VOUCH_CHANNEL_ID, bot)
    if channel:
        await queued_send(channel, f"v <@{confirmer_id}>")


async def announce_game_result(ticket_channel, form, self_won, bot_user, bot=None):
    game_num = await get_next_game_number(ticket_channel.guild, bot)
    mention = ticket_mention(ticket_channel, form)
    his_bet_usd, my_bet_usd, _coin = get_bet_info(form)
    his_bet = format_bet_display(his_bet_usd)
    my_bet = format_bet_display(my_bet_usd)

    if self_won:
        winner, loser = bot_user.mention, mention
        winner_bet, loser_bet = my_bet, his_bet
    else:
        winner, loser = mention, bot_user.mention
        winner_bet, loser_bet = his_bet, my_bet

    text = (
        f"Game #{game_num} <:dahoodcasino:1259258576015458426>\n"
        f"<:Dices:1259259866254676049>\n"
        f"{winner} overtakes {loser}\n"
        f"{winner_bet}v{loser_bet}"
    )
    await queued_send(ticket_channel, text)


async def record_winnings(channel, form, self_won):
    if self_won:
        apply_win_to_hold(form)
    save_session_from_form(channel.id, form)


async def _post_game_background(channel, form, self_won, bot_user, bot):
    try:
        await post_victory_message(channel.guild, form, bot)
    except Exception as exc:
        print(f"[end_game] post_victory_message failed: {exc}")


def _has_payout_winnings(form):
    return get_hold_usd(form) > 0


async def payout_winnings_if_any(channel, form):
    if _has_payout_winnings(form):
        coin = form.get("winnings_coin", "ltc")
        address = get_payout_address(coin)
        if address:
            await queued_send(channel, f"`{address}`")
        else:
            await queued_send(channel, f"❌ No {coin.upper()} payout address configured.")
    finish_form(channel, form, payout=True)


async def end_game(channel, form, self_won, bot_user, bot=None):
    form.pop("game_state", None)

    try:
        await record_winnings(channel, form, self_won)
    except Exception as exc:
        print(f"[end_game] record_winnings failed: {exc}")

    try:
        await announce_game_result(channel, form, self_won, bot_user, bot)
    except Exception as exc:
        print(f"[end_game] announce_game_result failed: {exc}")

    mention = ticket_mention(channel, form)
    rerun_text = f"{mention} Do you want to rerun? (yes/no)"
    await queued_send(channel, rerun_text)
    form["waiting_for_rerun"] = True
    form["rerun_timeout_task"] = asyncio.create_task(_rerun_timeout(channel))
    save_session_from_form(channel.id, form)

    asyncio.create_task(_post_game_background(channel, form, self_won, bot_user, bot))


async def _rerun_timeout(channel):
    try:
        await asyncio.sleep(RERUN_TIMEOUT_SECONDS)
        form = get_form(channel.id)
        if not form or not form.get("waiting_for_rerun"):
            return
        form["waiting_for_rerun"] = False
        if _has_payout_winnings(form):
            await payout_winnings_if_any(channel, form)
        else:
            finish_form(channel, form, payout=True)
    except asyncio.CancelledError:
        pass


async def prompt_rerun_amount(channel, form, bot_user):
    mention = ticket_mention(channel, form)
    form["waiting_for_rerun_amount"] = True
    await queued_send(channel, f"{mention} How much would you like to bet?")
    save_session_from_form(channel.id, form)


async def fund_rerun_on_confirm(channel, form):
    wager_usd, coin = get_wager_usd(form), get_bet_info(form)[2]
    covered = deduct_hold_up_to(form, wager_usd, coin)
    shortfall = round(wager_usd - covered, 2)

    if shortfall <= 0:
        add_wagered_usd(form, wager_usd)
        save_session_from_form(channel.id, form)
        return True

    if should_skip_payment(form):
        add_wagered_usd(form, wager_usd)
        save_session_from_form(channel.id, form)
        return True

    address = form.get("payout_address")
    if not address or address == "testing":
        await queued_send(channel, "❌ No payout address on file for rerun.")
        return False

    amount = usd_to_smallest_unit(shortfall, coin, get_price(coin))
    result = await send_apirone(coin, address, amount)
    if "error" in result:
        err = result["error"]
        await queued_send(channel, f"❌ Rerun transfer failed: {err if isinstance(err, str) else err}")
        return False

    add_wagered_usd(form, wager_usd)
    save_session_from_form(channel.id, form)
    if covered > 0:
        await queued_send(
            channel,
            f"📤 Used {format_bet_display(covered)} from hold and sent "
            f"{format_bet_display(shortfall)} {coin.upper()} to {address}",
        )
    else:
        await queued_send(
            channel,
            f"📤 Sent {format_bet_display(shortfall)} {coin.upper()} to {address} for rerun",
        )
    return True


async def process_rerun(channel, form, bot_user, bot=None):
    if form.get("game_state"):
        await queued_send(channel, "❌ Cannot rerun — a game is currently in progress.")
        return False

    if not form.get("responses", {}).get("bet"):
        await queued_send(channel, "❌ No previous game to rerun.")
        return False

    cancel_rerun_timeout(form)
    form["waiting_for_rerun"] = False
    form.pop("waiting_for_rerun_amount", None)
    form["pending_rerun_funding"] = True

    form["waiting_for_confirm"] = True
    form["waiting_for_adder_confirm"] = False
    form["confirm_text"] = build_confirm_text(channel, form, bot_user)
    await queued_send(channel, form["confirm_text"])
    save_session_from_form(channel.id, form)
    return True


async def handle_rerun_response(message, form, bot_user, start_game_fn, bot=None):
    if not form.get("waiting_for_rerun") or message.author.id != form["ticket_user_id"]:
        return False

    resp = message.content.strip().lower()
    if resp not in ("yes", "no"):
        return False

    cancel_rerun_timeout(form)
    form["waiting_for_rerun"] = False

    if resp == "no":
        if _has_payout_winnings(form):
            await payout_winnings_if_any(message.channel, form)
        else:
            finish_form(message.channel, form, payout=True)
        return True

    await prompt_rerun_amount(message.channel, form, bot_user)
    return True


async def handle_rerun_amount(message, form, bot_user, bot=None):
    if not form.get("waiting_for_rerun_amount") or message.author.id != form["ticket_user_id"]:
        return False

    response = message.content.strip()
    if response.lower() in ("yes", "no"):
        return False

    if not bet_validator(response, form):
        await queued_reply(message, "❌ Invalid format or out of range.")
        return True

    form["responses"]["bet"] = response
    form["waiting_for_rerun_amount"] = False
    save_session_from_form(message.channel.id, form)
    await process_rerun(message.channel, form, bot_user, bot)
    return True
