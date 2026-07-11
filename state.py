import config
from bets import get_hold_usd, usd_to_crypto_amount

active_forms = {}
ticket_channels = set()
ticket_sessions = {}
closed_ticket_channels = set()
testing_mode = False


def is_testing_mode():
    return testing_mode


def toggle_testing():
    global testing_mode
    testing_mode = not testing_mode
    return testing_mode


def should_skip_payment(form):
    return is_testing_mode() and form.get("ticket_user_id") == config.ADMIN_USER_ID


def register_ticket_channel(channel_id):
    closed_ticket_channels.discard(channel_id)
    if channel_id in ticket_channels:
        return False
    ticket_channels.add(channel_id)
    return True


def is_ticket_closed(channel_id):
    return channel_id in closed_ticket_channels


def mark_ticket_closed(channel_id):
    """End the form/session but keep the channel recognized for !hold / coin commands."""
    closed_ticket_channels.add(channel_id)
    active_forms.pop(channel_id, None)
    prev = ticket_sessions.get(channel_id) or {}
    ticket_sessions[channel_id] = {
        "ticket_user_id": prev.get("ticket_user_id"),
        "winnings_usd": 0.0,
        "winnings_crypto": 0.0,
        "winnings_coin": "ltc",
        "closed": True,
        "played": True,
    }
    ticket_channels.add(channel_id)


def ticket_has_played(channel_id):
    """True after a game has started or the ticket was closed — blocks auto form start."""
    if channel_id in closed_ticket_channels:
        return True
    session = ticket_sessions.get(channel_id) or {}
    return bool(session.get("played") or session.get("game_started"))


def reopen_ticket_for_new_form(channel_id):
    """Allow a fresh form after !rerun / yes while keeping hold + user."""
    closed_ticket_channels.discard(channel_id)
    session = get_ticket_session(channel_id)
    session.pop("closed", None)
    session["played"] = True
    session["game_started"] = False
    ticket_channels.add(channel_id)
    return session


def get_ticket_session(channel_id):
    return ticket_sessions.setdefault(channel_id, {
        "ticket_user_id": None,
        "winnings_usd": 0.0,
        "winnings_crypto": 0.0,
        "winnings_coin": "ltc",
    })


def save_session_from_form(channel_id, form):
    if not form:
        return
    session = get_ticket_session(channel_id)
    if form.get("ticket_user_id"):
        session["ticket_user_id"] = form["ticket_user_id"]
    session["winnings_usd"] = form.get("winnings_usd", 0.0)
    session["winnings_crypto"] = form.get("winnings_crypto", 0.0)
    session["winnings_coin"] = form.get("winnings_coin", "ltc")
    session["total_wagered_usd"] = form.get("total_wagered_usd", 0.0)
    if form.get("payout_address"):
        session["payout_address"] = form["payout_address"]
    if form.get("game_confirmer_user_id"):
        session["game_confirmer_user_id"] = form["game_confirmer_user_id"]
    if form.get("game_started"):
        session["game_started"] = True
        session["played"] = True


def get_hold_data(channel_id):
    form = active_forms.get(channel_id)
    if form:
        source = form
    else:
        source = ticket_sessions.get(channel_id) or {
            "winnings_usd": 0.0,
            "winnings_crypto": 0.0,
            "winnings_coin": "ltc",
        }
    coin = source.get("winnings_coin", "ltc")
    usd = get_hold_usd(source)
    crypto = round(usd_to_crypto_amount(usd, coin), 8) if usd > 0 else 0.0
    return usd, crypto, coin


def new_form_dict(channel_id, ticket_user_id):
    session = get_ticket_session(channel_id)
    if ticket_user_id:
        session["ticket_user_id"] = ticket_user_id
    return {
        "ticket_user_id": ticket_user_id or session.get("ticket_user_id"),
        "step": 0,
        "responses": {"game": "dice"},
        "waiting_for_address": False,
        "waiting_for_confirm": False,
        "winnings_usd": session.get("winnings_usd", 0.0),
        "winnings_crypto": session.get("winnings_crypto", 0.0),
        "winnings_coin": session.get("winnings_coin", "ltc"),
        "game_confirmer_user_id": session.get("game_confirmer_user_id"),
        "total_wagered_usd": session.get("total_wagered_usd", 0.0),
        "payout_address": session.get("payout_address"),
    }


def is_ticket_channel(channel):
    if channel.id in closed_ticket_channels:
        return True
    if channel.id in ticket_channels or channel.id in active_forms or channel.id in ticket_sessions:
        return True
    return "ticket" in channel.name.lower()


def get_form(channel_id):
    return active_forms.get(channel_id)


def cancel_rerun_timeout(form):
    if not form:
        return
    task = form.pop("rerun_timeout_task", None)
    if task and not task.done():
        task.cancel()


def clear_ticket_session(channel_id):
    cancel_rerun_timeout(active_forms.get(channel_id))
    active_forms.pop(channel_id, None)
    ticket_sessions.pop(channel_id, None)
    ticket_channels.discard(channel_id)
    closed_ticket_channels.discard(channel_id)


def finish_form(channel, form, *, payout=False):
    cancel_rerun_timeout(form)
    channel_id = channel.id
    if payout:
        # Ticket is done — do not auto-restart the form on later messages
        mark_ticket_closed(channel_id)
    else:
        save_session_from_form(channel_id, form)
        active_forms.pop(channel_id, None)
