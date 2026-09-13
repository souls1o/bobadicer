import config
from bets import format_bet_display, get_bet_info
from send_queue import queued_user_send
from services import get_house_balance_usd

GAMEMODE_LABELS = {
    "ties": "I Win Ties",
    "fair": "Fair",
}


async def _send_notify_dms(bot, content):
    for user_id in config.NOTIFY_USER_IDS:
        try:
            await queued_user_send(bot, user_id, content)
        except Exception as exc:
            print(f"[notify] DM failed for {user_id}: {exc}")


def _channel_label(channel):
    if channel is None:
        return "Unknown channel"
    guild = getattr(channel, "guild", None)
    if guild:
        return f"#{channel.name} (`{channel.id}`) — {guild.name}"
    return f"#{getattr(channel, 'name', 'unknown')} (`{channel.id}`)"


async def notify_admin_ticket_added(bot, channel):
    await _send_notify_dms(
        bot,
        f"**📃 New Ticket**\n"
        f"**Channel:** {_channel_label(channel)}",
    )


async def notify_admin_game_started(bot, channel, form):
    his_bet_usd, my_bet_usd, coin = get_bet_info(form)
    responses = form.get("responses", {})
    gm_key = responses.get("gamemode", "fair")
    gamemode = GAMEMODE_LABELS.get(gm_key, gm_key)
    first_to = responses.get("first_to")
    if first_to:
        gamemode = f"{gamemode} {str(first_to).upper()}"
    mode = responses.get("mode")
    if mode and mode != "normal":
        gamemode = f"{gamemode} ({mode})"
    coin_label = (coin or "ltc").upper()
    await _send_notify_dms(
        bot,
        f"**🎮 Game Started**\n"
        f"**Channel:** {_channel_label(channel)}\n"
        f"**Gamemode:** {gamemode}\n"
        f"**Your bet:** `${format_bet_display(my_bet_usd)}` {coin_label}\n"
        f"**Their bet:** `${format_bet_display(his_bet_usd)}` {coin_label}",
    )


async def notify_admin_game_result(bot, channel, form, self_won):
    outcome = "Win" if self_won else "Loss"
    emoji = "✅" if self_won else "❌"
    house_balance = await get_house_balance_usd()
    ticket_balance = form.get("winnings_usd", 0.0)
    new_balance = house_balance + ticket_balance
    await _send_notify_dms(
        bot,
        f"**{emoji} Game {outcome}**\n"
        f"**Channel:** {_channel_label(channel)}\n"
        f"**New balance:** `${new_balance:,.2f}`",
    )
