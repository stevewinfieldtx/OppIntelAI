"""
Customer Agent
Builds the Targeted Decomposition Profile for a specific prospect company.
Gathers everything publicly available about the company.
"""
import logging
from core.llm import call_llm_json
from core.cache import get_tdp, store_tdp

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are the Customer Agent in a lead hydration engine. Your job is to build a comprehensive Targeted Decomposition Profile (TDP) for a specific prospect company.

You are a business intelligence researcher. You must find everything publicly available about this company that would help a sales rep have an informed conversation.

Use web search extensively. Check the company website, LinkedIn, news articles, job postings, reviews (Google, Glassdoor, BBB), public records, and social media.

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

Every data point should be grounded in what you actually found. If you can't find something, say so rather than making it up. The worst thing a sales rep can do is cite a fabricated detail."""


async def run(customer_url: str, industry_context: str = "") -> dict:
    """
    Build or retrieve the Customer TDP.
    
    Args:
        customer_url: URL of the prospect company's website
        industry_context: Optional industry context from Industry Agent to inform research
        
    Returns:
        TDP dict with customer analysis
    """
    # Check cache first
    cached = await get_tdp("customer", customer_url)
    if cached:
        return cached

    logger.info(f"Building Customer TDP for: {customer_url}")

    industry_section = ""
    if industry_context:
        industry_section = f"""
The company operates in: {industry_context}
Use this industry context to inform your research — look for industry-specific pain points and technology patterns."""

    user_prompt = f"""Research and build a complete Customer Targeted Decomposition Profile for the company at: {customer_url}
{industry_section}

Search the web for:
1. Their website — what do they actually do, sell, or service?
2. LinkedIn company page — employee count, recent hires, leadership
3. Job postings — what roles are they hiring for? What tech skills do they require?
4. Google reviews, BBB, Glassdoor — what do customers and employees say?
5. News articles, press releases — any recent announcements?
6. Public records — any evidence of expansion, permits, filings?
7. Social media — what are they posting about? What does their activity reveal?

Be thorough but honest. Flag anything you couldn't verify. A sales rep needs to trust this data."""

    result = await call_llm_json(
        system_prompt=SYSTEM_PROMPT,
        user_prompt=user_prompt,
        use_web_search=True,
        max_tokens=4096,
    )

    # Store in cache
    tdp = await store_tdp(
        tdp_type="customer",
        identifier=customer_url,
        label=result["parsed"].get("company_name", customer_url),
        data=result["parsed"],
        citations=result.get("citations", []),
        token_cost=result.get("usage", {}).get("total_tokens", 0),
    )

    return tdp
