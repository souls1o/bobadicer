import json
from datetime import datetime, timedelta
from pathlib import Path

from bets import get_bet_info

_STATS_PATH = Path(__file__).parent / "data" / "stats.json"
_EMPTY_PERIOD = {"wagered": 0.0, "profit": 0.0, "games": 0, "unique_users": []}
_PST_OFFSET = timedelta(hours=8)


def _stats_now_pst():
    return datetime.utcnow() - _PST_OFFSET


def _stats_today():
    return _stats_now_pst().date()


def _stats_date_key(dt=None):
    if dt is None:
        return _stats_now_pst().strftime("%Y-%m-%d")
    if hasattr(dt, "hour"):
        return (dt - _PST_OFFSET).strftime("%Y-%m-%d")
    return dt.strftime("%Y-%m-%d")


def _week_start_sunday(today=None):
    today = today or _stats_today()
    days_since_sunday = (today.weekday() + 1) % 7
    return today - timedelta(days=days_since_sunday)


def _month_start(today=None):
    today = today or _stats_today()
    return today.replace(day=1)


def _merge_unique_users(*groups):
    seen = []
    for group in groups:
        for user_id in group or []:
            uid = str(user_id)
            if uid not in seen:
                seen.append(uid)
    return seen


def _sum_daily_range(daily, start_date, end_date):
    totals = dict(_EMPTY_PERIOD)
    totals["unique_users"] = []
    day = start_date
    while day <= end_date:
        entry = daily.get(day.strftime("%Y-%m-%d"), _EMPTY_PERIOD)
        totals["wagered"] += entry.get("wagered", 0)
        totals["profit"] += entry.get("profit", 0)
        totals["games"] += entry.get("games", 0)
        totals["unique_users"] = _merge_unique_users(
            totals["unique_users"], entry.get("unique_users")
        )
        day += timedelta(days=1)
    return totals


def _default_stats():
    return {
        "daily": {},
        "all_time": dict(_EMPTY_PERIOD),
        "unique_users": [],
    }


def _load_stats():
    if not _STATS_PATH.exists():
        return _default_stats()
    try:
        data = json.loads(_STATS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _default_stats()
    if not isinstance(data.get("daily"), dict):
        data["daily"] = {}
    if not isinstance(data.get("all_time"), dict):
        data["all_time"] = dict(_EMPTY_PERIOD)
    if not isinstance(data.get("unique_users"), list):
        data["unique_users"] = []
    return data


def _save_stats(stats):
    _STATS_PATH.parent.mkdir(parents=True, exist_ok=True)
    _STATS_PATH.write_text(json.dumps(stats, indent=2), encoding="utf-8")


def _period_totals(stats, period):
    daily = stats.get("daily") or {}
    today = _stats_today()
    if period == "daily":
        entry = dict(daily.get(today.strftime("%Y-%m-%d"), _EMPTY_PERIOD))
        entry["unique_users"] = list(entry.get("unique_users") or [])
        return entry
    if period == "weekly":
        return _sum_daily_range(daily, _week_start_sunday(today), today)
    if period == "monthly":
        return _sum_daily_range(daily, _month_start(today), today)
    all_time = stats.get("all_time") or {}
    unique = all_time.get("unique_users") or stats.get("unique_users") or []
    return {
        "wagered": all_time.get("wagered", 0),
        "profit": all_time.get("profit", 0),
        "games": all_time.get("games", 0),
        "unique_users": list(unique),
    }


def _format_money(value):
    return f"${float(value):,.2f}"


def _format_period(label, totals):
    unique_count = len(totals.get("unique_users") or [])
    return (
        f"**{label}** — Wagered {_format_money(totals['wagered'])} | "
        f"Profit {_format_money(totals['profit'])} | Games {int(totals['games'])} | "
        f"Unique {unique_count}"
    )


def _format_day_label(date_key):
    try:
        return datetime.strptime(date_key, "%Y-%m-%d").strftime("%b %d, %Y")
    except (TypeError, ValueError):
        return str(date_key)


def _best_and_worst_days(stats):
    daily = stats.get("daily") or {}
    best_key = worst_key = None
    best_profit = worst_profit = None
    for key, entry in daily.items():
        if not isinstance(entry, dict):
            continue
        if int(entry.get("games", 0) or 0) <= 0:
            continue
        profit = round(float(entry.get("profit", 0) or 0), 2)
        if best_profit is None or profit > best_profit:
            best_profit = profit
            best_key = key
        if worst_profit is None or profit < worst_profit:
            worst_profit = profit
            worst_key = key
    return best_key, best_profit, worst_key, worst_profit


def _format_extreme_day(label, date_key, profit):
    if date_key is None or profit is None:
        return f"**{label}:** _None yet_"
    return f"**{label}:** {_format_day_label(date_key)} — {_format_money(profit)}"


def _add_unique_user(period_entry, user_id):
    users = period_entry.setdefault("unique_users", [])
    uid = str(user_id)
    if uid not in users:
        users.append(uid)


async def track_stats(form, self_won):
    his_bet_usd, my_bet_usd, _coin = get_bet_info(form)
    wagered = round(my_bet_usd, 2)
    profit = round(his_bet_usd if self_won else -my_bet_usd, 2)
    user_id = str(form["ticket_user_id"])

    stats = _load_stats()
    today = _stats_date_key()
    if today not in stats["daily"]:
        stats["daily"][today] = {
            "wagered": 0.0,
            "profit": 0.0,
            "games": 0,
            "unique_users": [],
        }
    day = stats["daily"][today]
    day.setdefault("unique_users", [])
    day["wagered"] = round(day.get("wagered", 0) + wagered, 2)
    day["profit"] = round(day.get("profit", 0) + profit, 2)
    day["games"] = day.get("games", 0) + 1
    _add_unique_user(day, user_id)

    all_time = stats.setdefault("all_time", dict(_EMPTY_PERIOD))
    all_time.setdefault("unique_users", [])
    all_time["wagered"] = round(all_time.get("wagered", 0) + wagered, 2)
    all_time["profit"] = round(all_time.get("profit", 0) + profit, 2)
    all_time["games"] = all_time.get("games", 0) + 1
    _add_unique_user(all_time, user_id)

    unique = stats.setdefault("unique_users", [])
    if user_id not in unique:
        unique.append(user_id)

    _save_stats(stats)


async def build_stats_text():
    stats = _load_stats()
    best_key, best_profit, worst_key, worst_profit = _best_and_worst_days(stats)
    lines = [
        "**📊 Stats**",
        "",
        _format_period("Today", _period_totals(stats, "daily")),
        _format_period("Weekly", _period_totals(stats, "weekly")),
        _format_period("Monthly", _period_totals(stats, "monthly")),
        _format_period("All Time", _period_totals(stats, "all_time")),
        "",
        _format_extreme_day("Good day", best_key, best_profit),
        _format_extreme_day("Worst day", worst_key, worst_profit),
    ]
    return "\n".join(lines)
