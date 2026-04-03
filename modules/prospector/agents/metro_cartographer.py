"""
Metro Cartographer Agent (Prospector Module)
Selects the optimal geographic cluster for prospecting.
"""
import logging
from core.llm import call_llm_json

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are the Metro Cartographer Agent in a proactive lead prospecting engine. Given a solution profile and a target vertical, your job is to select the BEST metropolitan area for prospecting.

You are optimizing for prospect DENSITY and SALES EFFICIENCY — not just market size.

Evaluate metros against these criteria:
1. TARGET DENSITY: Minimum 50+ SMB targets in the selected vertical
2. LOGISTICS COMPLEXITY: Multiple counties/suburbs with branch operations = pain amplification
3. COMPETITIVE LANDSCAPE: Known incumbent vendors create competitive framing opportunities
4. ECONOMIC MOMENTUM: Growth indicators, new construction, business expansion signals

Return your analysis as JSON:

{
    "selected_metro": "Metro name (e.g., Dallas-Fort Worth, TX)",
    "city_core": "Primary city",
    "state": "State abbreviation",
    "rationale": "3-4 sentences explaining why this metro is optimal",
    "estimated_target_pool": "Estimated number of qualifying SMBs",
    "key_business_corridors": [
        {
            "corridor": "Name of industrial park, business district, or corridor",
            "description": "What types of businesses cluster here",
            "landmark": "Notable landmark for rapport building"
        }
    ],
    "economic_signals": [
        "Growth indicators, new development, industry trends specific to this metro"
    ],
    "incumbent_vendors": [
        "Known technology vendors or competitors active in this metro"
    ],
    "adjacent_metros": [
        {
            "metro": "Nearby metro that could expand the territory",
            "distance": "Drive time from core metro",
            "density": "Rough estimate of additional target pool"
        }
    ],
    "local_knowledge": {
        "major_highways": ["Key highways that define business geography"],
        "industrial_zones": ["Named industrial areas"],
        "rapport_references": ["Local references a rep could use to sound like a local — sports teams, landmarks, recent events"]
    }
}

Be specific and local. A sales rep should be able to read this and navigate the metro like they've been working it for months."""


async def run(solution_tdp: dict, vertical_data: dict, geo_seed: str = "") -> dict:
    """
    Select the optimal metro area for prospecting.
    
    Args:
        solution_tdp: Solution TDP data
        vertical_data: Output from Vertical Selector
        geo_seed: Optional user-specified geography to validate
        
    Returns:
        Metro selection with local intelligence
    """
    logger.info("Running Metro Cartographer")

    sol_data = solution_tdp.get("data", solution_tdp)
    vert_data = vertical_data.get("data", vertical_data)

    geo_instruction = ""
    if geo_seed:
        geo_instruction = f"""
The user has suggested targeting: "{geo_seed}"
Validate that this metro has sufficient target density for the selected vertical.
If density is insufficient, suggest expanding to adjacent areas or recommend a better metro."""

    user_prompt = f"""Select the optimal metropolitan area for prospecting given this solution and vertical.
{geo_instruction}

=== SOLUTION PROFILE ===
{__import__('json').dumps(sol_data, indent=2)}

=== SELECTED VERTICAL ===
{__import__('json').dumps(vert_data, indent=2)}

Search the web for:
1. Business density data for this vertical in candidate metros
2. Local business corridors and industrial parks
3. Economic development news and growth signals
4. Known technology vendors active in the area"""

    result = await call_llm_json(
        system_prompt=SYSTEM_PROMPT,
        user_prompt=user_prompt,
        use_web_search=True,
        max_tokens=3000,
    )

    return {
        "data": result["parsed"],
        "token_cost": result.get("usage", {}).get("total_tokens", 0),
    }
