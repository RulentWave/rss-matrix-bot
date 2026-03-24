import logging
from typing import Optional
from database import (
    add_feed,
    remove_feed,
    get_feeds_for_room,
    update_feed_filter,
    set_config,
    get_config,
)

logger = logging.getLogger(__name__)

HELP_TEXT = """<b>RSS Bot Commands</b>

<b>Feed Management (per-room):</b>
<code>!rss add &lt;url&gt; [--filter] [--criteria "your criteria"]</code>
Add a feed to this room. Use <code>--filter</code> to enable LLM filtering.

<code>!rss remove &lt;url&gt;</code>
Remove a feed from this room.

<code>!rss list</code>
List all feeds subscribed in this room.

<code>!rss filter &lt;url&gt; on|off [--criteria "your criteria"]</code>
Toggle LLM filtering for a feed and optionally set custom criteria.

<code>!rss poll [url]</code>
Manually poll all feeds (or a specific feed) in this room for new articles.

<b>Bot Configuration (admin only):</b>
<code>!rss setllm &lt;endpoint&gt; &lt;api_key&gt; &lt;model&gt;</code>
Configure the LLM endpoint. Example:
<code>!rss setllm https://api.openai.com/v1 sk-... gpt-4o-mini</code>

<code>!rss llmstatus</code>
Show the current LLM configuration (key is masked).

<code>!rss help</code>
Show this help message."""


def escape_html(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


async def handle_command(
    room_id: str,
    sender: str,
    admin_user: str,
    raw_message: str,
    reply_func,  # async (room_id, html) -> None
    poll_func,   # async (room_id, url_filter) -> int
):
    """
    Parse and dispatch bot commands. reply_func sends HTML to the room.
    """
    parts = raw_message.strip().split(None, 1)
    if not parts or parts[0].lower() != "!rss":
        return

    args = parts[1].strip() if len(parts) > 1 else ""

    # Tokenize remaining args, respecting quoted strings
    import shlex
    try:
        tokens = shlex.split(args)
    except ValueError:
        tokens = args.split()

    if not tokens:
        await reply_func(room_id, HELP_TEXT)
        return

    subcommand = tokens[0].lower()
    rest = tokens[1:]

    is_admin = sender == admin_user

    # --- help ---
    if subcommand == "help":
        await reply_func(room_id, HELP_TEXT)

    # --- add ---
    elif subcommand == "add":
        if not rest:
            await reply_func(
                room_id,
                "Usage: <code>!rss add &lt;url&gt; [--filter]"
                " [--criteria \"...\"]</code>",
            )
            return

        url = rest[0]
        filter_enabled = "--filter" in rest
        criteria = None
        if "--criteria" in rest:
            idx = rest.index("--criteria")
            if idx + 1 < len(rest):
                criteria = rest[idx + 1]

        added = await add_feed(room_id, url, filter_enabled, criteria)
        if added:
            filter_note = ""
            if filter_enabled:
                crit = escape_html(criteria) if criteria else "default"
                filter_note = (
                    f" with LLM filtering enabled"
                    f" (criteria: <i>{crit}</i>)"
                )
            await reply_func(
                room_id,
                f"✅ Added feed: <code>{escape_html(url)}</code>{filter_note}",
            )
        else:
            await reply_func(
                room_id,
                f"⚠️ Feed already added: <code>{escape_html(url)}</code>",
            )

    # --- remove ---
    elif subcommand == "remove":
        if not rest:
            await reply_func(
                room_id, "Usage: <code>!rss remove &lt;url&gt;</code>"
            )
            return
        url = rest[0]
        removed = await remove_feed(room_id, url)
        if removed:
            await reply_func(
                room_id,
                f"🗑️ Removed feed: <code>{escape_html(url)}</code>",
            )
        else:
            await reply_func(
                room_id,
                f"⚠️ Feed not found: <code>{escape_html(url)}</code>",
            )

    # --- list ---
    elif subcommand == "list":
        feeds = await get_feeds_for_room(room_id)
        if not feeds:
            await reply_func(room_id, "No feeds subscribed in this room.")
            return
        lines = ["<b>Feeds in this room:</b>"]
        for f in feeds:
            filter_status = "🔍 filtered" if f["filter_enabled"] else "📋 unfiltered"
            crit = f.get("criteria")
            crit_note = (
                f" — criteria: <i>{escape_html(crit)}</i>" if crit else ""
            )
            lines.append(
                f"• <code>{escape_html(f['url'])}</code> "
                f"[{filter_status}{crit_note}]"
            )
        await reply_func(room_id, "<br>".join(lines))

    # --- filter ---
    elif subcommand == "filter":
        if len(rest) < 2:
            await reply_func(
                room_id,
                "Usage: <code>!rss filter &lt;url&gt; on|off"
                " [--criteria \"...\"]</code>",
            )
            return
        url = rest[0]
        toggle = rest[1].lower()
        if toggle not in ("on", "off"):
            await reply_func(room_id, "Use <code>on</code> or <code>off</code>.")
            return
        enabled = toggle == "on"
        criteria = None
        if "--criteria" in rest:
            idx = rest.index("--criteria")
            if idx + 1 < len(rest):
                criteria = rest[idx + 1]
        await update_feed_filter(room_id, url, enabled, criteria)
        state = "enabled" if enabled else "disabled"
        crit_note = (
            f" Criteria set to: <i>{escape_html(criteria)}</i>"
            if criteria else ""
        )
        await reply_func(
            room_id,
            f"✅ Filtering {state} for <code>{escape_html(url)}</code>.{crit_note}",
        )

    # --- poll ---
    elif subcommand == "poll":
        url_filter = rest[0] if rest else None
        target = (
            f"<code>{escape_html(url_filter)}</code>"
            if url_filter
            else "all feeds"
        )
        await reply_func(room_id, f"🔄 Polling {target}…")
        count = await poll_func(room_id, url_filter)
        await reply_func(
            room_id,
            f"✅ Poll complete. {count} new article(s) posted.",
        )

    # --- setllm (admin only) ---
    elif subcommand == "setllm":
        if not is_admin:
            await reply_func(room_id, "⛔ Only the admin can configure the LLM.")
            return
        if len(rest) < 3:
            await reply_func(
                room_id,
                "Usage: <code>!rss setllm &lt;endpoint&gt;"
                " &lt;api_key&gt; &lt;model&gt;</code>",
            )
            return
        endpoint, api_key, model = rest[0], rest[1], rest[2]
        await set_config("llm_endpoint", endpoint)
        await set_config("llm_api_key", api_key)
        await set_config("llm_model", model)
        await reply_func(
            room_id,
            f"✅ LLM configured: <code>{escape_html(endpoint)}</code>"
            f" model <code>{escape_html(model)}</code>",
        )

    # --- llmstatus (admin only) ---
    elif subcommand == "llmstatus":
        if not is_admin:
            await reply_func(room_id, "⛔ Only the admin can view LLM config.")
            return
        endpoint = await get_config("llm_endpoint") or "<not set>"
        model = await get_config("llm_model") or "<not set>"
        api_key = await get_config("llm_api_key")
        masked_key = (
            api_key[:6] + "…" + api_key[-4:] if api_key and len(api_key) > 10
            else "<not set>"
        )
        await reply_func(
            room_id,
            f"<b>LLM Configuration</b><br>"
            f"Endpoint: <code>{escape_html(endpoint)}</code><br>"
            f"Model: <code>{escape_html(model)}</code><br>"
            f"API Key: <code>{masked_key}</code>",
        )

    else:
        await reply_func(
            room_id,
            f"Unknown command: <code>{escape_html(subcommand)}</code>. "
            f"Try <code>!rss help</code>.",
        )
