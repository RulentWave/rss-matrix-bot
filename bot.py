#!/usr/bin/env python3
"""
RSS Matrix Bot — main entry point.
"""

import asyncio
import logging
import sys
import time
import yaml
from pathlib import Path
from typing import Optional

from nio import (
    AsyncClient,
    AsyncClientConfig,
    InviteMemberEvent,
    MatrixRoom,
    MegolmEvent,
    RoomMessageText,
    LoginResponse,
    SyncError,
)
from nio.store import SqliteStore
from apscheduler.schedulers.asyncio import AsyncIOScheduler

import database
from commands import handle_command, escape_html
from feed_manager import poll_all_feeds, poll_room_feeds

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("rssbot")


# ---------------------------------------------------------------------------
# Config loader
# ---------------------------------------------------------------------------

def load_config(path: str = "config.yml") -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


# ---------------------------------------------------------------------------
# Bot class
# ---------------------------------------------------------------------------

class RSSBot:
    def __init__(self, config: dict):
        self.config = config
        self.admin_user: str = config["admin_user"]

        matrix_cfg = config["matrix"]
        self.homeserver: str = matrix_cfg["homeserver"]
        self.user_id: str = matrix_cfg["user_id"]
        self.password: str = matrix_cfg.get("password", "")
        self.access_token: Optional[str] = matrix_cfg.get("access_token")
        self.device_name: str = matrix_cfg.get("device_name", "RSSBot")
        self.store_path: str = matrix_cfg.get("store_path", "./bot_store")

        Path(self.store_path).mkdir(parents=True, exist_ok=True)

        self.client = AsyncClient(
            homeserver=self.homeserver,
            user=self.user_id,
            config=AsyncClientConfig(
                store=SqliteStore,
                store_name="bot_store.db",
                encryption_enabled=True,
            ),
            store_path=self.store_path,
        )

        self.scheduler = AsyncIOScheduler()
        self.poll_interval: int = config.get("poll_interval_minutes", 15)

    # -----------------------------------------------------------------------
    # Matrix send helpers
    # -----------------------------------------------------------------------

    async def send_html(self, room_id: str, html: str):
        """Send an HTML-formatted message to a room."""
        import re
        plain = re.sub(r"<[^>]+>", "", html).strip()
        try:
            await self.client.room_send(
                room_id=room_id,
                message_type="m.room.message",
                content={
                    "msgtype": "m.text",
                    "body": plain,
                    "format": "org.matrix.custom.html",
                    "formatted_body": html,
                },
            )
        except Exception as e:
            logger.error(f"Failed to send message to {room_id}: {e}")

    async def post_article(
        self,
        room_id: str,
        title: str,
        url: str,
        description: str,
        feed_url: str,
        skipped: bool,
        reason: str,
    ):
        """Callback invoked by feed_manager when an article should be posted."""
        if skipped:
            logger.info(f"[{room_id}] Skipped: {title}")
            return

        short_desc = description[:300].strip()
        if len(description) > 300:
            short_desc += "…"

        html = (
            f'<b><a href="{url}">{escape_html(title)}</a></b><br>'
            f'<i>Feed: <code>{escape_html(feed_url)}</code></i><br>'
            f'{escape_html(short_desc)}'
        )
        await self.send_html(room_id, html)

    # -----------------------------------------------------------------------
    # Event handlers
    # -----------------------------------------------------------------------

    async def on_invite(self, room: MatrixRoom, event: InviteMemberEvent):
        """Auto-join rooms only when invited by the admin user."""
        if event.sender != self.admin_user:
            logger.info(
                f"Ignoring invite from non-admin {event.sender} "
                f"to {room.room_id}"
            )
            return

        logger.info(f"Joining room {room.room_id} (invited by admin)")
        await self.client.join(room.room_id)
        await self.send_html(
            room.room_id,
            "👋 Hello! I'm your RSS bot. "
            "Type <code>!rss help</code> to get started.",
        )

    async def on_message(self, room: MatrixRoom, event: RoomMessageText):
        """Handle incoming text messages."""
        # Ignore messages from ourselves
        if event.sender == self.client.user_id:
            return

        body = event.body.strip()
        if not body.lower().startswith("!rss"):
            return

        logger.info(
            f"Command from {event.sender} in {room.room_id}: {body[:80]}"
        )

        async def poll_func(room_id: str, url_filter: Optional[str]) -> int:
            return await poll_room_feeds(
                room_id, self.post_article, url_filter
            )

        await handle_command(
            room_id=room.room_id,
            sender=event.sender,
            admin_user=self.admin_user,
            raw_message=body,
            reply_func=self.send_html,
            poll_func=poll_func,
        )

    async def on_decryption_failure(
        self, room: MatrixRoom, event: MegolmEvent
    ):
        logger.warning(
            f"Failed to decrypt message in {room.room_id} "
            f"from {event.sender}"
        )

    # -----------------------------------------------------------------------
    # Scheduler
    # -----------------------------------------------------------------------

    async def scheduled_poll(self):
        logger.info("Running scheduled feed poll…")
        await poll_all_feeds(self.post_article)

    # -----------------------------------------------------------------------
    # Startup / login
    # -----------------------------------------------------------------------

    async def login(self):
        if self.access_token:
            self.client.access_token = self.access_token
            self.client.user_id = self.user_id
            logger.info("Logged in with access token")
        else:
            response = await self.client.login(
                self.password, device_name=self.device_name
            )
            if isinstance(response, LoginResponse):
                logger.info(f"Logged in as {self.user_id}")
            else:
                logger.error(f"Login failed: {response}")
                sys.exit(1)

    async def seed_initial_llm_config(self):
        """Seed LLM config from the YAML file if not already set in the DB."""
        llm_cfg = self.config.get("llm", {})
        if not await database.get_config("llm_endpoint") and llm_cfg.get(
            "endpoint"
        ):
            await database.set_config("llm_endpoint", llm_cfg["endpoint"])
            await database.set_config("llm_api_key", llm_cfg["api_key"])
            await database.set_config("llm_model", llm_cfg["model"])
            logger.info("Seeded initial LLM config from config.yml")

    # -----------------------------------------------------------------------
    # Run
    # -----------------------------------------------------------------------

    async def run(self):
        await database.init_db()
        await self.login()
        await self.seed_initial_llm_config()

        # Register event callbacks
        self.client.add_event_callback(self.on_invite, InviteMemberEvent)
        self.client.add_event_callback(self.on_message, RoomMessageText)
        self.client.add_event_callback(
            self.on_decryption_failure, MegolmEvent
        )

        # Schedule periodic feed polling
        self.scheduler.add_job(
            self.scheduled_poll,
            "interval",
            minutes=self.poll_interval,
            id="feed_poll",
        )
        self.scheduler.start()
        logger.info(
            f"Feed polling scheduled every {self.poll_interval} minute(s)"
        )

        # Perform an initial sync (skip old messages to avoid replaying
        # commands that happened while the bot was offline)
        logger.info("Performing initial sync…")
        sync_response = await self.client.sync(timeout=30000, full_state=True)
        if isinstance(sync_response, SyncError):
            logger.error(f"Initial sync failed: {sync_response.message}")
            sys.exit(1)

        # Mark the current sync token so we don't process stale messages
        self.client.next_batch = sync_response.next_batch

        logger.info("Bot is running. Listening for events…")
        try:
            await self.client.sync_forever(
                timeout=30000,
                full_state=False,
            )
        finally:
            self.scheduler.shutdown()
            await self.client.close()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def main():
    config_path = sys.argv[1] if len(sys.argv) > 1 else "config.yml"
    config = load_config(config_path)
    bot = RSSBot(config)
    await bot.run()


if __name__ == "__main__":
    asyncio.run(main())
