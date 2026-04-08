"""
Firecrawl Client
Provides scrape + interact capabilities for the Lead Hydration pipeline.

Used by:
  - customer_agent.py  — scrape company homepage + team/careers pages
  - solution_agent.py  — scrape vendor homepage + pricing/integrations pages
  - contact_cpp_agent.py — interact with LinkedIn profile URL from Apollo

Falls back gracefully to None when FIRECRAWL_API_KEY is not set,
so all agents continue working via LLM web search alone.

Pricing reference:
  - Scrape: 1 credit per page
  - Interact (code only): 2 credits/minute
  - Interact (AI prompt): 7 credits/minute

We use AI prompts for LinkedIn (needs navigation intelligence)
and code-only interact for structured page extraction elsewhere.
"""
import logging
import os
import httpx

logger = logging.getLogger(__name__)

FIRECRAWL_API_KEY = os.getenv("FIRECRAWL_API_KEY", "")
FIRECRAWL_BASE_URL = "https://api.firecrawl.dev/v2"

# Hard cap on content returned to LLM — keeps token costs sane
MAX_CONTENT_CHARS = 12000


# ─── Public helpers ───────────────────────────────────────────────────────────

def is_available() -> bool:
    """Returns True if Firecrawl is configured."""
    return bool(FIRECRAWL_API_KEY)


async def scrape_page(url: str, timeout: int = 20) -> str | None:
    """
    Scrape a single URL and return clean markdown content.
    Returns None on any failure — callers fall back to LLM web search.

    Args:
        url:     Full URL to scrape
        timeout: Request timeout in seconds

    Returns:
        Markdown string (truncated to MAX_CONTENT_CHARS) or None
    """
    if not is_available():
        return None

    logger.info(f"[Firecrawl] Scraping: {url}")

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                f"{FIRECRAWL_BASE_URL}/scrape",
                headers={
                    "Authorization": f"Bearer {FIRECRAWL_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "url": url,
                    "formats": ["markdown"],
                    "onlyMainContent": True,
                },
            )

        if resp.status_code != 200:
            logger.warning(f"[Firecrawl] Scrape HTTP {resp.status_code} for {url}")
            return None

        data = resp.json()
        content = (
            data.get("data", {}).get("markdown")
            or data.get("markdown")
            or ""
        )

        if not content:
            logger.info(f"[Firecrawl] Empty content for {url}")
            return None

        content = content[:MAX_CONTENT_CHARS]
        logger.info(f"[Firecrawl] Scraped {len(content)} chars from {url}")
        return content

    except Exception as e:
        logger.warning(f"[Firecrawl] Scrape failed for {url}: {e}")
        return None


async def scrape_pages(urls: list[str]) -> dict[str, str | None]:
    """
    Scrape multiple URLs concurrently.
    Returns dict of {url: content_or_none}.
    """
    import asyncio
    tasks = [scrape_page(url) for url in urls]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    return {
        url: (result if not isinstance(result, Exception) else None)
        for url, result in zip(urls, results)
    }


async def interact_linkedin(linkedin_url: str) -> str | None:
    """
    Scrape a LinkedIn profile URL then interact to extract
    the full public profile content: headline, about, experience,
    recommendations, and recent activity.

    Uses AI prompt interact — ~7 credits/minute, typically 30-60s.
    Returns None on failure.

    Args:
        linkedin_url: Full LinkedIn profile URL from Apollo

    Returns:
        Extracted profile text or None
    """
    if not is_available():
        return None
    if not linkedin_url:
        return None

    logger.info(f"[Firecrawl] LinkedIn interact: {linkedin_url}")

    scrape_id = await _scrape_get_id(linkedin_url)
    if not scrape_id:
        return None

    try:
        # Single focused prompt — extract profile content
        result = await _interact_prompt(
            scrape_id=scrape_id,
            prompt=(
                "Extract the full public profile content from this LinkedIn page. "
                "Include: headline, about/summary section, all work experience entries "
                "(company, title, dates, description), skills listed, any recommendations "
                "received (full text), and any recent posts or activity visible. "
                "Return as plain text with clear section labels."
            ),
            timeout=60,
        )
        return result

    finally:
        await _stop_interact(scrape_id)


async def interact_extract_pages(
    base_url: str,
    page_hints: list[str],
) -> str:
    """
    Scrape a site's homepage then interact to navigate to and extract
    content from specific page types.

    Used by customer_agent to hit team/leadership, careers/jobs,
    and about pages in a single session.

    Args:
        base_url:    Company homepage URL
        page_hints:  List of page types to find, e.g.
                     ["team or leadership page", "careers or jobs page"]

    Returns:
        Concatenated extracted content from all pages found, or empty string.
    """
    if not is_available():
        return ""

    logger.info(f"[Firecrawl] Multi-page extract: {base_url} — {page_hints}")

    scrape_id = await _scrape_get_id(base_url)
    if not scrape_id:
        return ""

    collected = []

    try:
        for hint in page_hints:
            result = await _interact_prompt(
                scrape_id=scrape_id,
                prompt=(
                    f"Navigate to the {hint} and extract all text content you find there. "
                    "Include names, titles, descriptions, and any other relevant details. "
                    "Return as plain text."
                ),
                timeout=45,
            )
            if result:
                collected.append(f"--- {hint.upper()} ---\n{result}")

    finally:
        await _stop_interact(scrape_id)

    return "\n\n".join(collected)


# ─── Internal helpers ─────────────────────────────────────────────────────────

async def _scrape_get_id(url: str) -> str | None:
    """Scrape a URL and return the scrapeId for subsequent interact calls."""
    try:
        async with httpx.AsyncClient(timeout=25) as client:
            resp = await client.post(
                f"{FIRECRAWL_BASE_URL}/scrape",
                headers={
                    "Authorization": f"Bearer {FIRECRAWL_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "url": url,
                    "formats": ["markdown"],
                    "onlyMainContent": True,
                },
            )

        if resp.status_code != 200:
            logger.warning(f"[Firecrawl] _scrape_get_id HTTP {resp.status_code} for {url}")
            return None

        data = resp.json()
        scrape_id = (
            data.get("data", {}).get("metadata", {}).get("scrapeId")
            or data.get("data", {}).get("metadata", {}).get("scrape_id")
        )

        if not scrape_id:
            logger.warning(f"[Firecrawl] No scrapeId returned for {url}")
            return None

        logger.info(f"[Firecrawl] scrapeId={scrape_id} for {url}")
        return scrape_id

    except Exception as e:
        logger.warning(f"[Firecrawl] _scrape_get_id failed for {url}: {e}")
        return None


async def _interact_prompt(
    scrape_id: str,
    prompt: str,
    timeout: int = 45,
) -> str | None:
    """Execute a single AI prompt interact call on an open session."""
    try:
        async with httpx.AsyncClient(timeout=timeout + 10) as client:
            resp = await client.post(
                f"{FIRECRAWL_BASE_URL}/scrape/{scrape_id}/interact",
                headers={
                    "Authorization": f"Bearer {FIRECRAWL_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={"prompt": prompt, "timeout": timeout},
            )

        if resp.status_code != 200:
            logger.warning(f"[Firecrawl] Interact HTTP {resp.status_code}: {resp.text[:200]}")
            return None

        data = resp.json()
        output = data.get("output") or data.get("result") or ""
        if output:
            output = output[:MAX_CONTENT_CHARS]
            logger.info(f"[Firecrawl] Interact returned {len(output)} chars")
        return output or None

    except Exception as e:
        logger.warning(f"[Firecrawl] _interact_prompt failed: {e}")
        return None


async def _stop_interact(scrape_id: str) -> None:
    """Stop an interact session to avoid unnecessary billing."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.delete(
                f"{FIRECRAWL_BASE_URL}/scrape/{scrape_id}/interact",
                headers={"Authorization": f"Bearer {FIRECRAWL_API_KEY}"},
            )
        logger.info(f"[Firecrawl] Stopped session {scrape_id}")
    except Exception as e:
        logger.warning(f"[Firecrawl] Failed to stop session {scrape_id}: {e}")
