"""
Customer Agent
Builds the Targeted Decomposition Profile for a specific prospect company.

Scraping strategy (in priority order):
  1. Firecrawl scrape — homepage clean markdown
  2. Firecrawl interact — team/leadership + careers pages in one session
  3. LLM web search (:online) — fills gaps and enriches with news,
     reviews, LinkedIn signals, job postings

Firecrawl is optional — if FIRECRAWL_API_KEY is not set the agent
falls back entirely to LLM web search, same as before.
"""
import logging
from core.llm import call_llm_json
from core.cache import get_tdp, store_tdp
from core import firecrawl

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are the Customer Agent in a lead hydration engine. Your job is to build a comprehensive Targeted Decomposition Profile (TDP) for a specific prospect company.

You are a business intelligence researcher. You must find everything publicly available about this company that would help a sales rep have an informed conversation.

Where SCRAPED CONTENT is provided below, treat it as ground truth — it came directly from the company's own website. Build on it, don't contradict it.

Use web search to fill any gaps: LinkedIn, news articles, job postings, reviews (Google, Glassdoor, BBB), public records, and social media.

Return your analysis as JSON with this exact structure:

{
    "company_name": "Official company name",
    "website": "Company URL",
    "location": {
        "headquarters": "City, State",
        "other_locations": ["Additional locations if found"],
        "local_landmark": "Nearest notable landmark, industrial park, or cross-street for rapport building"
    },
    "company_overview": "2-3 paragraph description of what this company actually does — in operational terms, not marketing language",
    "industry_vertical": "Their specific industry or sub-industry",
    "company_size": {
        "employee_estimate": "Best estimate of employee count",
        "revenue_estimate": "Best estimate of revenue if available",
        "growth_signals": "Evidence of growth or contraction"
    },
    "leadership": [
        {
            "name": "Person's name",
            "title": "Their title",
            "linkedin_signal": "Any relevant info from their LinkedIn (new role, background, etc.)",
            "relevance": "Why this person matters for a sales conversation"
        }
    ],
    "technology_signals": {
        "known_tools": ["Software/platforms they're known to use"],
        "job_posting_clues": ["Tech mentioned in their job postings"],
        "tech_maturity": "low | mixed | high — assessment based on evidence",
        "legacy_indicators": ["Signs they're running outdated systems"]
    },
    "business_signals": {
        "recent_news": ["Recent press releases, news articles, or announcements"],
        "hiring_patterns": ["Notable hiring activity and what it suggests"],
        "expansion_signals": ["Evidence of growth — new locations, permits, equipment"],
        "financial_signals": ["Any public financial indicators — funding, filings, etc."]
    },
    "operational_reality": {
        "what_they_actually_do": "Day-to-day operations described simply",
        "likely_pain_points": ["Inferred operational challenges based on their size, industry, and tech signals"],
        "complexity_factors": ["What makes their operations complex"]
    },
    "online_presence": {
        "google_reviews_summary": "Summary of Google review sentiment if applicable",
        "glassdoor_signals": "Employee sentiment if available",
        "social_media_presence": "What their social media activity reveals",
        "customer_complaints": "Any patterns in public customer feedback"
    },
    "rapport_hooks": [
        "Specific conversation starters based on what you found — local references, recent achievements, shared connections, industry events"
    ]
}

Every data point should be grounded in what you actually found. If you can't find something, say so rather than making it up."""


async def run(customer_url: str, industry_context: str = "") -> dict:
    """
    Build or retrieve the Customer TDP.

    Args:
        customer_url:     URL of the prospect company's website
        industry_context: Optional industry context from Industry Agent

    Returns:
        TDP dict with customer analysis
    """
    cached = await get_tdp("customer", customer_url)
    if cached:
        return cached

    logger.info(f"Building Customer TDP for: {customer_url}")

    # ── Step 1: Firecrawl scrape + interact (if available) ──────────────────
    scraped_homepage = ""
    scraped_subpages = ""

    if firecrawl.is_available():
        logger.info(f"[CustomerAgent] Firecrawl active — scraping {customer_url}")
        scraped_homepage = await firecrawl.scrape_page(customer_url) or ""

        # Interact to navigate to team and careers pages in one session
        scraped_subpages = await firecrawl.interact_extract_pages(
            base_url=customer_url,
            page_hints=[
                "team, leadership, or about us page — extract all staff names and titles",
                "careers or jobs page — extract open roles and required technologies",
            ],
        )
        logger.info(
            f"[CustomerAgent] Firecrawl complete — "
            f"homepage={len(scraped_homepage)}c subpages={len(scraped_subpages)}c"
        )

    # ── Step 2: Build context block for the LLM ─────────────────────────────
    scraped_block = ""
    if scraped_homepage or scraped_subpages:
        parts = []
        if scraped_homepage:
            parts.append(f"=== HOMEPAGE (scraped) ===\n{scraped_homepage}")
        if scraped_subpages:
            parts.append(f"=== SUB-PAGES (scraped via browser) ===\n{scraped_subpages}")
        scraped_block = (
            "\n\nSCRAPED CONTENT — treat as ground truth:\n"
            + "\n\n".join(parts)
        )

    industry_section = ""
    if industry_context:
        industry_section = f"\nThe company operates in: {industry_context}\n"

    user_prompt = f"""Research and build a complete Customer Targeted Decomposition Profile for: {customer_url}
{industry_section}{scraped_block}

{"Use web search to fill gaps not covered by the scraped content above." if scraped_block else "Use web search extensively:"}
1. {"Any leadership or team members not visible in scraped content" if scraped_block else "Company website — what do they do?"}
2. LinkedIn company page — employee count, recent hires
3. {"News, funding, expansion signals" if scraped_block else "Job postings — what tech skills do they require?"}
4. Google reviews, BBB, Glassdoor — customer and employee sentiment
5. {"Social media and public records" if scraped_block else "News articles, press releases, public records"}

Be thorough but honest. Flag anything you couldn't verify."""

    result = await call_llm_json(
        system_prompt=SYSTEM_PROMPT,
        user_prompt=user_prompt,
        use_web_search=True,
        max_tokens=6000,
    )

    tdp = await store_tdp(
        tdp_type="customer",
        identifier=customer_url,
        label=result["parsed"].get("company_name", customer_url),
        data=result["parsed"],
        citations=result.get("citations", []),
        token_cost=result.get("usage", {}).get("total_tokens", 0),
    )

    return tdp
