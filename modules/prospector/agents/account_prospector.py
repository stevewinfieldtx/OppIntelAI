"""
Account Prospector Agent (Prospector Module)
Identifies specific companies matching the propensity profile.
Finds, qualifies, scores, and localizes each prospect.
"""
import logging
from core.llm import call_llm_json

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are the Account Prospector Agent in a proactive lead prospecting engine. Given a solution profile, target vertical, and target metro, your job is to identify SPECIFIC REAL COMPANIES that match the propensity profile.

You are finding companies that are most likely to buy this solution RIGHT NOW based on operational signals.

For each prospect, you must:
1. FIND: Real company name, location, estimated employee count, phone, website
2. QUALIFY against ALL criteria:
   - Employee count: 20-250 (SMB sweet spot)
   - Multi-location OR high operational complexity
   - Growth indicator (hiring, permits, funding, expansion)
   - No obvious enterprise-tech footprint (no NetSuite, no Salesforce, no SAP S/4)
3. SCORE 0-100:
   - 90-100: Family-owned + multi-location + succession event + no modern system
   - 80-89: Multi-location + growth strain
   - 70-79: Operational complexity but tech footprint unclear
4. LOCALIZE: Nearest major intersection, highway exit, or industrial park

Return your findings as JSON:

{
    "prospects": [
        {
            "id": 1,
            "name": "Exact company name",
            "website": "Company URL",
            "metro": "Metro area",
            "location": "City, ST",
            "landmark": "Specific local landmark — must pass the specificity test",
            "employees": "XX-XXX",
            "phone": "(XXX) XXX-XXXX",
            "priority": 95,
            "priority_class": "high",
            "who_is_this": "2-3 sentence narrative: company type + local market position + current trigger event + pain implication",
            "contact_title": "Most likely decision maker title",
            "lead_module": "Specific solution module/feature that matches their pain",
            "pain_tags": ["Specific Pain 1", "Specific Pain 2", "Specific Pain 3"],
            "growth_signals": ["Specific evidence of growth or change"],
            "disqualification_risk": "Any reason this lead might not qualify on deeper inspection"
        }
    ],
    "search_summary": {
        "total_found": 0,
        "high_priority": 0,
        "medium_priority": 0,
        "metros_covered": ["List of specific areas within the metro"],
        "verticals_represented": ["Micro-verticals found"]
    }
}

CRITICAL RULES:
- Every company must be REAL. Use web search to find actual businesses.
- Never fabricate company names, addresses, or phone numbers.
- The "who_is_this" narrative must contain specific intel a rep couldn't guess from the company name alone.
- Pain tags must be things the prospect would actually say out loud ("Manual Financial Consolidation" yes; "Digital Transformation Opportunity" no).
- Landmarks must pass the specificity test: could a rep mention it without looking at a map?
- If you can't find enough qualifying companies, return what you have with honest counts. Do not pad with fabricated entries."""


async def run(
    solution_tdp: dict,
    vertical_data: dict,
    metro_data: dict,
    account_volume: int = 10,
) -> dict:
    """
    Find and qualify specific prospect companies.
    
    Args:
        solution_tdp: Solution TDP data
        vertical_data: Output from Vertical Selector
        metro_data: Output from Metro Cartographer
        account_volume: Number of prospects to find (default 10)
        
    Returns:
        List of qualified, scored, localized prospects
    """
    logger.info(f"Running Account Prospector — targeting {account_volume} accounts")

    sol_data = solution_tdp.get("data", solution_tdp)
    vert_data = vertical_data.get("data", vertical_data)
    metro = metro_data.get("data", metro_data)

    user_prompt = f"""Find {account_volume} real companies that match the propensity profile.

=== SOLUTION PROFILE ===
{__import__('json').dumps(sol_data, indent=2)}

=== TARGET VERTICAL ===
{__import__('json').dumps(vert_data, indent=2)}

=== TARGET METRO ===
{__import__('json').dumps(metro, indent=2)}

Search the web to find REAL companies. Look at:
1. Google Maps / Google Business listings for businesses in the target vertical and metro
2. Industry association directories
3. LinkedIn company searches
4. Local business journals and directories
5. Trade association member lists

For each company found, search for additional qualifying signals:
- Job postings (growth indicator)
- News mentions (expansion, funding, leadership changes)
- Google reviews (operational quality signals)
- Social media presence (tech maturity signals)

Score each honestly. If you can only find {account_volume - 3} qualifying companies, return that many. Quality over quantity."""

    result = await call_llm_json(
        system_prompt=SYSTEM_PROMPT,
        user_prompt=user_prompt,
        use_web_search=True,
        max_tokens=8000,
        temperature=0.3,
    )

    return {
        "data": result["parsed"],
        "token_cost": result.get("usage", {}).get("total_tokens", 0),
    }
