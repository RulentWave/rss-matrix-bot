import httpx
import logging
from typing import Optional
from database import get_config

logger = logging.getLogger(__name__)

DEFAULT_CRITERIA = (
    "Is this article genuinely informative, educational, or insightful? "
    "It should teach the reader something meaningful or provide valuable "
    "analysis. Reject articles that are primarily promotional, clickbait, "
    "low-effort opinion pieces, or purely news aggregation with no depth."
)


async def get_llm_config() -> Optional[dict]:
    endpoint = await get_config("llm_endpoint")
    api_key = await get_config("llm_api_key")
    model = await get_config("llm_model")

    if not endpoint or not api_key or not model:
        return None

    return {"endpoint": endpoint, "api_key": api_key, "model": model}


async def should_post_article(
    title: str,
    content: str,
    criteria: Optional[str] = None,
) -> tuple[bool, str]:
    """
    Asks the LLM whether the article meets the criteria.
    Returns (should_post, reason).
    """
    config = await get_llm_config()
    if not config:
        logger.warning("LLM not configured, passing article through")
        return True, "LLM not configured"

    criteria = criteria or DEFAULT_CRITERIA

    system_prompt = (
        "You are a content filter assistant. You evaluate articles and decide "
        "whether they are worth reading based on the given criteria. "
        "Respond with a JSON object with two fields: "
        '"verdict" (true or false) and "reason" (one sentence explanation).'
    )

    user_prompt = (
        f"Criteria: {criteria}\n\n"
        f"Article Title: {title}\n\n"
        f"Article Content:\n{content}\n\n"
        "Should this article be shown to the user? Respond only with valid JSON."
    )

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{config['endpoint'].rstrip('/')}/chat/completions",
                headers={
                    "Authorization": f"Bearer {config['api_key']}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": config["model"],
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    "temperature": 0.1,
                    "max_tokens": 150,
                    "response_format": {"type": "json_object"},
                },
            )
            response.raise_for_status()
            data = response.json()
            result_text = data["choices"][0]["message"]["content"]

            import json
            result = json.loads(result_text)
            verdict = bool(result.get("verdict", True))
            reason = result.get("reason", "No reason given")
            return verdict, reason

    except Exception as e:
        logger.error(f"LLM request failed: {e}")
        # Fail open: if LLM is broken, post the article
        return True, f"LLM error (failing open): {e}"
