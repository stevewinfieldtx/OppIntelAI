"""
Industry Agent
Builds the Targeted Decomposition Profile for an industry vertical.
Covers pain points, trends, regulations, technology landscape.
"""
import logging
from core.llm import call_llm_json
from core.cache import get_tdp, store_tdp

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are the Industry Agent in a lead hydration engine. Your job is to build a comprehensive Targeted Decomposition Profile (TDP) for a specific industry vertical.

You are a business intelligence analyst. Your output will be used by sales reps to sound deeply informed about the prospect's industry during discovery calls.

Use web search to gather current, real information about the industry.

Return your analysis as JSON with this exact structure:

{
    "industry_name": "Specific industry vertical name",
    "naics_codes": ["Relevant NAICS codes if applicable"],
    "industry_overview": "2-3 paragraph overview of the current state of this industry — written for a sales rep, not an academic",
    "market_size_signals": "What's publicly known about market size, growth rate, key players",
    "current_pain_points": [
        {
            "pain": "Specific operational or business pain",
            "severity": "critical | high | moderate",
            "who_feels_it": "Which role/title experiences this most",
            "root_cause": "Why this pain exists"
        }
    ],
    "technology_landscape": {
        "dominant_tools": ["Tools/platforms commonly used in this industry"],
        "emerging_technologies": ["New tech being adopted"],
        "legacy_debt": "Common legacy systems or processes that are holding companies back",
        "digital_maturity": "low | mixed | high — general assessment of how digitally mature this industry is"
    },
    "regulatory_environment": [
        {
            "regulation": "Name or description of regulation",
            "impact": "How it affects day-to-day operations",
            "compliance_pain": "What makes compliance difficult"
        }
    ],
    "industry_trends": [
        {
            "trend": "Name of trend",
            "direction": "Where this is heading",
            "implication_for_sales": "Why a sales rep should care about this"
        }
    ],
    "buying_behavior": {
        "typical_decision_makers": ["Titles involved in tech purchases"],
        "buying_triggers": ["Events that cause companies to buy new solutions"],
        "sales_cycle_length": "Typical sales cycle duration",
        "budget_cycle": "When budgets are typically set",
        "preferred_engagement": "How people in this industry prefer to be sold to"
    },
    "industry_language": {
        "key_terms": ["Industry jargon a rep should know"],
        "topics_to_avoid": ["Sensitive topics or common faux pas"],
        "credibility_builders": ["Things a rep can say to sound informed"]
    }
}

Be current. Be specific. Focus on information that makes a sales rep sound like an industry insider, not a cold caller reading a script."""


async def run(industry_name: str) -> dict:
    """
    Build or retrieve the Industry TDP.
    
    Args:
        industry_name: Name/description of the industry vertical
        
    Returns:
        TDP dict with industry analysis
    """
    # Check cache first
    cached = await get_tdp("industry", industry_name)
    if cached:
        return cached

    logger.info(f"Building Industry TDP for: {industry_name}")

    user_prompt = f"""Research and build a complete Industry Targeted Decomposition Profile for: {industry_name}

Search the web for:
1. Current industry reports, trends, and pain points
2. Reddit and forum discussions from people working in this industry
3. Regulatory requirements and compliance challenges
4. Technology adoption patterns and common tools used
5. Recent news affecting this industry

Focus on information that would help a B2B sales representative have an informed, credible conversation with someone in this industry. No generic filler — every point should be specific enough that an industry insider would nod and say 'yeah, that's real.'"""

    result = await call_llm_json(
        system_prompt=SYSTEM_PROMPT,
        user_prompt=user_prompt,
        use_web_search=True,
        max_tokens=4096,
    )

    # Store in cache
    tdp = await store_tdp(
        tdp_type="industry",
        identifier=industry_name,
        label=industry_name,
        data=result["parsed"],
        citations=result.get("citations", []),
        token_cost=result.get("usage", {}).get("total_tokens", 0),
    )

    return tdp
