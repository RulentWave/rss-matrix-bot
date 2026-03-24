import httpx
from bs4 import BeautifulSoup
import logging

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; RSSBot/1.0; +https://github.com/rssbot)"
    )
}
MAX_CONTENT_CHARS = 8000


async def scrape_article(url: str) -> Optional[str]:
    """
    Attempts to fetch and extract the main text content of an article.
    Returns the extracted text, or None if scraping fails.
    """
    try:
        async with httpx.AsyncClient(
            timeout=15.0, follow_redirects=True, headers=HEADERS
        ) as client:
            response = await client.get(url)
            response.raise_for_status()

            content_type = response.headers.get("content-type", "")
            if "html" not in content_type:
                return None

            soup = BeautifulSoup(response.text, "lxml")

            # Remove noise elements
            for tag in soup(
                ["script", "style", "nav", "header", "footer",
                 "aside", "form", "iframe", "noscript", "ads"]
            ):
                tag.decompose()

            # Try common article content selectors
            content = None
            for selector in [
                "article",
                '[role="main"]',
                "main",
                ".post-content",
                ".article-content",
                ".entry-content",
                ".content",
            ]:
                element = soup.select_one(selector)
                if element:
                    content = element.get_text(separator="\n", strip=True)
                    break

            if not content:
                # Fall back to body text
                body = soup.find("body")
                content = body.get_text(separator="\n", strip=True) if body else None

            if content:
                # Collapse excessive whitespace
                lines = [l.strip() for l in content.splitlines() if l.strip()]
                content = "\n".join(lines)
                return content[:MAX_CONTENT_CHARS]

            return None

    except Exception as e:
        logger.warning(f"Failed to scrape {url}: {e}")
        return None
