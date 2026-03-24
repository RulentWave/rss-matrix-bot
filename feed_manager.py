import asyncio
import time
import logging
import feedparser
from typing import Callable, Awaitable
from database import (
    get_all_feeds,
    get_feeds_for_room,
    is_article_seen,
    mark_article_seen,
    update_feed_last_checked,
    get_feed_by_id,
)
from scraper import scrape_article
from llm_client import should_post_article

logger = logging.getLogger(__name__)

# Callback type: (room_id, title, url, description, feed_url, skipped, reason)
PostCallback = Callable[
    [str, str, str, str, str, bool, str], Awaitable[None]
]


async def process_feed(
    feed: dict,
    post_callback: PostCallback,
    force: bool = False,
) -> int:
    """
    Polls a single feed and triggers post_callback for new articles.
    Returns the number of articles posted.
    """
    feed_id = feed["id"]
    room_id = feed["room_id"]
    url = feed["url"]
    filter_enabled = bool(feed["filter_enabled"])
    criteria = feed.get("criteria")

    loop = asyncio.get_event_loop()
    parsed = await loop.run_in_executor(None, feedparser.parse, url)

    if parsed.bozo and not parsed.entries:
        logger.warning(f"Failed to parse feed {url}: {parsed.bozo_exception}")
        return 0

    posted = 0
    # Process oldest-first so room timeline is chronological
    entries = list(reversed(parsed.entries))

    for entry in entries:
        article_url = entry.get("link", "")
        if not article_url:
            continue

        already_seen = await is_article_seen(feed_id, article_url)
        if already_seen and not force:
            continue

        await mark_article_seen(feed_id, article_url)

        if already_seen and force:
            # On manual poll, skip re-posting already-seen items
            # unless you want to allow it — here we skip
            continue

        title = entry.get("title", "No title")
        summary = entry.get("summary", "")
        # Strip HTML from summary for LLM use
        from bs4 import BeautifulSoup
        clean_summary = BeautifulSoup(summary, "lxml").get_text()

        if filter_enabled:
            # Try to scrape the full article
            scraped = await scrape_article(article_url)
            content_for_llm = scraped if scraped else (
                f"Title: {title}\n\nSummary: {clean_summary}"
            )

            verdict, reason = await should_post_article(
                title, content_for_llm, criteria
            )
            if not verdict:
                logger.info(f"Filtered out: {title} — {reason}")
                await post_callback(
                    room_id, title, article_url,
                    clean_summary, url, True, reason
                )
                continue

        await post_callback(
            room_id, title, article_url,
            clean_summary, url, False, ""
        )
        posted += 1

    await update_feed_last_checked(feed_id, time.time())
    return posted


async def poll_all_feeds(post_callback: PostCallback):
    feeds = await get_all_feeds()
    for feed in feeds:
        try:
            await process_feed(feed, post_callback)
        except Exception as e:
            logger.error(f"Error processing feed {feed['url']}: {e}")


async def poll_room_feeds(
    room_id: str,
    post_callback: PostCallback,
    url_filter: str = None,
) -> int:
    feeds = await get_feeds_for_room(room_id)
    if url_filter:
        feeds = [f for f in feeds if f["url"] == url_filter]

    total = 0
    for feed in feeds:
        try:
            count = await process_feed(feed, post_callback, force=True)
            total += count
        except Exception as e:
            logger.error(f"Error processing feed {feed['url']}: {e}")
    return total
