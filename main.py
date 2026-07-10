import discord
from discord.ext import commands, tasks
import asyncio
import config
from forms import (
    build_dm_gamemodes_text,
    build_dm_help_text,
    handle_bot_added_to_channel,
    handle_form_step,
    handle_global_listeners,
    handle_ticket_command,
    is_roll_command,
    should_process_channel,
    start_ticket_form,
    was_bot_added_to_channel,
)
from games import DA_HOOD_BOT_ID, handle_da_hood_message, handle_user_roll, start_game
from send_queue import ensure_worker, queued_reply, queued_send
from services import get_house_balance_text
from state import active_forms, clear_ticket_session, get_form, is_ticket_channel, is_testing_mode, toggle_testing

bot = commands.Bot(command_prefix="!", self_bot=True)


def set_auto_post_channel_id(channel_id):
    old_id = config.AUTO_POST_CHANNEL_ID
    config.AUTO_POST_CHANNEL_ID = channel_id
    if old_id in config.CHANNEL_BLACKLIST:
        config.CHANNEL_BLACKLIST.remove(old_id)
    if channel_id not in config.CHANNEL_BLACKLIST:
        config.CHANNEL_BLACKLIST.append(channel_id)


def find_lf_players_channel():
    target = config.AUTO_POST_CHANNEL_NAME.lower()
    for guild in bot.guilds:
        for channel in guild.text_channels:
            if channel.name.lower() == target:
                return channel
    return None


async def resolve_auto_post_channel():
    channel_id = config.AUTO_POST_CHANNEL_ID
    channel = bot.get_channel(channel_id)
    if channel is None:
        try:
            channel = await bot.fetch_channel(channel_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            channel = None
    if isinstance(channel, discord.TextChannel):
        return channel

    replacement = find_lf_players_channel()
    if replacement is not None:
        if replacement.id != channel_id:
            set_auto_post_channel_id(replacement.id)
            print(f"[auto_post] switched to #{replacement.name} ({replacement.id})")
        return replacement

    return None


def _can_post_in(channel):
    if not isinstance(channel, discord.TextChannel):
        return False
    me = channel.guild.me if channel.guild else None
    if me is None:
        return True
    try:
        perms = channel.permissions_for(me)
        return perms.view_channel and perms.send_messages
    except Exception:
        return False


def ensure_auto_post():
    if auto_post.is_running():
        return
    auto_post.start()
    print("[auto_post] task started")


@bot.event
async def on_ready():
    print(f"✅ Selfbot logged in as {bot.user} (ID: {bot.user.id})")
    ensure_worker()
    channel = await resolve_auto_post_channel()
    if channel is None:
        print(f"[auto_post] no #{config.AUTO_POST_CHANNEL_NAME} channel found at startup")
    ensure_auto_post()
    if not watchdog.is_running():
        watchdog.start()


@bot.event
async def on_disconnect():
    print("⚠️ Disconnected from Discord gateway — reconnecting...")


@bot.event
async def on_resumed():
    print("✅ Session resumed")
    ensure_auto_post()


@tasks.loop(seconds=config.AUTO_POST_INTERVAL)
async def auto_post():
    try:
        if is_testing_mode():
            return
        channel = await resolve_auto_post_channel()
        if channel is None:
            print(
                f"[auto_post] no channel — configured={config.AUTO_POST_CHANNEL_ID}, "
                f"no #{config.AUTO_POST_CHANNEL_NAME} found"
            )
            return
        if not _can_post_in(channel):
            print(
                f"[auto_post] missing send permission in #{channel.name} ({channel.id})"
            )
            return
        await queued_send(channel, config.AUTO_POST_MESSAGE)
        print(f"[auto_post] posted in #{channel.name} ({channel.id})")
    except discord.NotFound:
        print(f"[auto_post] channel {config.AUTO_POST_CHANNEL_ID} deleted — searching for replacement")
        replacement = find_lf_players_channel()
        if replacement is not None:
            set_auto_post_channel_id(replacement.id)
            if _can_post_in(replacement):
                await queued_send(replacement, config.AUTO_POST_MESSAGE)
                print(f"[auto_post] posted in #{replacement.name} ({replacement.id})")
    except discord.Forbidden:
        print(f"[auto_post] forbidden in #{getattr(channel, 'name', '?')} ({getattr(channel, 'id', '?')})")
    except discord.HTTPException as exc:
        print(f"[auto_post] HTTP error ({exc.status}): {exc}")
    except Exception as exc:
        print(f"[auto_post] error: {exc}")


@auto_post.before_loop
async def before_auto_post():
    await bot.wait_until_ready()


@auto_post.error
async def auto_post_error(exc):
    print(f"[auto_post] task error: {exc}")


@tasks.loop(minutes=2)
async def watchdog():
    if not bot.is_ready():
        return
    if not auto_post.is_running():
        print("[watchdog] auto_post stopped — restarting")
        ensure_auto_post()


@watchdog.before_loop
async def before_watchdog():
    await bot.wait_until_ready()


@bot.event
async def on_guild_channel_create(channel):
    if isinstance(channel, discord.TextChannel):
        if channel.name.lower() == config.AUTO_POST_CHANNEL_NAME.lower():
            set_auto_post_channel_id(channel.id)
            print(f"[auto_post] new #{channel.name} channel detected ({channel.id})")
        if was_bot_added_to_channel(channel, bot.user):
            await handle_bot_added_to_channel(bot, channel)


@bot.event
async def on_guild_channel_update(before, after):
    if isinstance(after, discord.TextChannel) and was_bot_added_to_channel(after, bot.user, before):
        await handle_bot_added_to_channel(bot, after)


@bot.event
async def on_guild_channel_delete(channel):
    if channel.id == config.AUTO_POST_CHANNEL_ID:
        print(f"[auto_post] channel deleted ({channel.id}) — searching for #{config.AUTO_POST_CHANNEL_NAME}")
        replacement = await resolve_auto_post_channel()
        if replacement is None:
            print(f"[auto_post] no #{config.AUTO_POST_CHANNEL_NAME} replacement found yet")
    clear_ticket_session(channel.id)


@bot.event
async def on_message(message: discord.Message):
    if message.author == bot.user:
        return

    try:
        await _handle_message(message)
    except Exception as exc:
        print(f"[on_message] error in #{getattr(message.channel, 'name', '?')}: {exc}")


async def _handle_message(message: discord.Message):
    if isinstance(message.channel, discord.DMChannel):
        content = message.content.strip().lower()

        if content == "!help":
            await queued_reply(message, build_dm_help_text(message.author.id))
            return
        if content == "!gamemodes":
            await queued_reply(message, build_dm_gamemodes_text())
            return
        if content == "!housebal":
            await queued_reply(message, await get_house_balance_text())
            return
        if content == "!toggle testing" and message.author.id == config.ADMIN_USER_ID:
            enabled = toggle_testing()
            status = "enabled" if enabled else "disabled"
            await queued_reply(message, f"Testing mode is {status}.")
            return
        if message.author.id == config.ADMIN_USER_ID and content.startswith("!setchannel"):
            parts = message.content.strip().split(maxsplit=1)
            if len(parts) < 2:
                await queued_reply(message, "Usage: `!setchannel <channel_id>`")
                return
            raw = parts[1].strip()
            if raw.startswith("<#") and raw.endswith(">"):
                raw = raw[2:-1]
            try:
                channel_id = int(raw)
            except ValueError:
                await queued_reply(message, "❌ Invalid channel ID.")
                return
            set_auto_post_channel_id(channel_id)
            label = f"`{channel_id}`"
            channel = bot.get_channel(channel_id)
            if channel is None:
                try:
                    channel = await bot.fetch_channel(channel_id)
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    channel = None
            if channel is not None:
                label = f"#{channel.name} (`{channel_id}`)"
            await queued_reply(message, f"✅ Auto-post channel set to {label}.")
            if channel is not None and _can_post_in(channel) and not is_testing_mode():
                await queued_send(channel, config.AUTO_POST_MESSAGE)
                await queued_reply(message, "✅ Test auto-post sent.")
            elif is_testing_mode():
                await queued_reply(message, "⚠️ Testing mode is on — auto-post loop is paused.")
            return

    if not isinstance(message.channel, discord.TextChannel):
        return

    if not should_process_channel(message.channel, message, bot.user):
        return

    if is_ticket_channel(message.channel):
        if await handle_ticket_command(message, bot.user, bot):
            return

    channel_id = message.channel.id
    form = get_form(channel_id)

    if is_roll_command(message.content) and form and form.get("game_state", {}).get("game_type") == "dice":
        if message.author.id == form["ticket_user_id"]:
            await handle_user_roll(message, form, bot.user)
        return

    if form and "game_state" in form:
        state = form["game_state"]
        if state.get("game_type") == "dice" and message.author.bot and (
            state.get("waiting_for_embed")
            or state.get("pending_bot_total") is not None
            or state.get("awaiting_user_after_bot")
            or state.get("bot_rolls_remaining", 0) > 0
            or state.get("pending_user_embeds", 0) > 0
        ):
            await handle_da_hood_message(message, form, bot.user, bot)
            return
        if message.author.id == DA_HOOD_BOT_ID:
            await handle_da_hood_message(message, form, bot.user, bot)
            return

    form = get_form(channel_id)
    if form and not form.get("game_state") and not form.get("waiting_for_confirm") and not form.get("waiting_for_rerun") and not form.get("waiting_for_rerun_amount"):
        await handle_form_step(message, form, bot.user)

    if channel_id not in active_forms:
        await asyncio.sleep(1)
        await start_ticket_form(message.channel, bot.user, bot)
        return

    await handle_global_listeners(message, bot.user, start_game, bot)


if __name__ == "__main__":
    token = config.DISCORD_TOKEN
    if not token:
        token = input("Paste your Discord User Token: ")
    bot.run(token)
