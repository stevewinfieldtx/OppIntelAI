"""
Solution Agent
Builds the Targeted Decomposition Profile for a solution/product.
Goes beyond marketing fluff — good, bad, ugly.
"""
import logging
from core.llm import call_llm_json
from core.cache import get_tdp, store_tdp

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are the Solution Agent in a lead hydration engine. Your job is to build a comprehensive, HONEST Targeted Decomposition Profile (TDP) of a software solution or product.

You are NOT a marketer. You are an intelligence analyst. You must capture the good, the bad, and the ugly.

Use web search to gather real information. Check review sites (G2, Capterra, TrustRadius), Reddit discussions, competitor comparisons, and the vendor's own documentation.

Return your analysis as JSON with this exact structure:

{
    "solution_name": "Full product name",
    "vendor": "Company that makes it",
    "category": "Product category (e.g., Email Security, ERP, CRM)",
    "website": "Vendor URL",
    "target_market": "Who this is built for (company size, type)",
    "elevator_pitch": "One paragraph — what this actually does in plain English, no marketing speak",
    "core_capabilities": [
        {
            "capability": "Name of feature/capability",
            "description": "What it actually does",
            "strength_level": "strong | adequate | weak",
            "user_sentiment": "What real users say about this"
        }
    ],
    "known_limitations": [
        {
            "limitation": "What it can't do or does poorly",
            "impact": "How this affects the buyer",
            "common_workaround": "What users typically do instead"
        }
    ],
    "competitive_landscape": [
        {
            "competitor": "Competitor name",
            "wins_against": "Where this solution beats the competitor",
            "loses_to": "Where the competitor is stronger"
        }
    ],
    "ideal_buyer_profile": {
        "company_size": "Employee range",
        "revenue_range": "Revenue range if applicable",
        "complexity_trigger": "What operational complexity makes them need this",
        "current_tools_outgrown": ["List of tools they're likely replacing"]
    },
    "pricing_model": "What's publicly known about pricing",
    "implementation_reality": "Honest assessment of implementation complexity and timeline",
    "switching_triggers": [
        "Specific events or pain points that cause someone to buy this"
    ],
    "objections_heard": [
        {
            "objection": "Common pushback from prospects",
            "reality": "Whether the objection is valid and how to address it"
        }
    ]
}

Be thorough. Be honest. If something is a weakness, say so. A sales rep armed with honest intelligence is far more credible than one armed with marketing fluff."""


async def run(solution_url: str, solution_name: str = "") -> dict:
    """
    Build or retrieve the Solution TDP.
    
    Args:
        solution_url: URL of the solution's website — primary research target
        solution_name: Optional name for caching (defaults to URL if empty)
        
    Returns:
        TDP dict with solution analysis
    """
    # Use URL as cache key since it's the unique identifier
    cache_key = solution_name or solution_url
    cached = await get_tdp("solution", cache_key)
    if cached:
        return cached

    logger.info(f"Building Solution TDP for: {solution_url} ({solution_name})")

    user_prompt = f"""Research and build a complete Targeted Decomposition Profile for the solution at: {solution_url}
{f'The product is known as: {solution_name}' if solution_name else ''}

Start by visiting {solution_url} to understand what the product actually does.

Then search the web for:
1. G2, Capterra, and TrustRadius reviews — look for patterns in complaints and praise
2. Reddit discussions about this product (r/sysadmin, r/msp, r/ERP, r/sales, etc.)
3. Competitor comparison articles
4. Pricing pages or discussions about cost
5. Case studies and customer testimonials — what are real outcomes?

Be specific and evidence-based. No generic filler. Every claim should be grounded in what you found."""

    result = await call_llm_json(
        system_prompt=SYSTEM_PROMPT,
        user_prompt=user_prompt,
        use_web_search=True,
        max_tokens=4096,
    )

    # Store in cache using name (or URL as fallback)
    tdp = await store_tdp(
        tdp_type="solution",
        identifier=cache_key,
        label=result["parsed"].get("solution_name", cache_key),
        data=result["parsed"],
        citations=result.get("citations", []),
        token_cost=result.get("usage", {}).get("total_tokens", 0),
    )

    return tdp
