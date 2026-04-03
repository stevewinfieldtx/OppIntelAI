"""
Vertical Selector Agent (Prospector Module)
Identifies the highest-propensity industry vertical for a given solution.
"""
import logging
from core.llm import call_llm_json

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are the Vertical Selector Agent in a proactive lead prospecting engine. Given a solution's Technology DNA (from the Solution TDP), your job is to identify the BEST industry vertical to target.

You are selecting the vertical where this solution has the highest probability of closing deals — not the broadest market, but the deepest pain.

Evaluate verticals against these criteria:
1. STRUCTURAL COMPLEXITY: Does this vertical inherently require the solution's capabilities? (e.g., multi-location for ERP, project-based billing for PSA)
2. FRAGMENTED LANDSCAPE: Are there many local/regional SMB players (not just national giants)?
3. OPERATIONAL PAIN DENSITY: How acute and common is the pain this solution solves?
4. ACCESSIBILITY: Can we find and research these companies through public data?

Return your analysis as JSON:

{
    "selected_vertical": "Specific vertical name (not generic NAICS — use operational language)",
    "naics_codes": ["Relevant NAICS codes"],
    "rationale": "3-4 sentences explaining why this vertical has the highest propensity",
    "structural_fit": "Why this vertical inherently needs the solution",
    "pain_density": "How common and acute the pain is in this vertical",
    "competitive_landscape": "What the competitive environment looks like",
    "runner_up_verticals": [
        {
            "vertical": "Second best vertical",
            "why_not_first": "Why it ranked second"
        },
        {
            "vertical": "Third best vertical",
            "why_not_first": "Why it ranked third"
        }
    ],
    "micro_verticals": [
        "Hyper-specific sub-segments within the selected vertical (e.g., 'Commercial HVAC Parts Distributors with fleet service' not just 'Wholesale Trade')"
    ]
}

Be specific. 'Manufacturing' is too broad. 'Custom metal fabricators serving aerospace with lot traceability requirements' is the right level of specificity."""


async def run(solution_tdp: dict, target_vertical: str = "") -> dict:
    """
    Select the optimal industry vertical for prospecting.
    
    Args:
        solution_tdp: Solution TDP data from shared Solution Agent
        target_vertical: Optional user-specified vertical to validate
        
    Returns:
        Vertical selection with rationale
    """
    logger.info("Running Vertical Selector")

    sol_data = solution_tdp.get("data", solution_tdp)

    override_instruction = ""
    if target_vertical:
        override_instruction = f"""
The user has suggested targeting: "{target_vertical}"
Validate this choice against the solution's capabilities. If it's a strong fit, confirm it.
If there's a significantly better vertical, override it and explain why."""

    user_prompt = f"""Given this solution profile, identify the best industry vertical to prospect into.
{override_instruction}

=== SOLUTION PROFILE ===
{__import__('json').dumps(sol_data, indent=2)}

Use web search if needed to validate your vertical selection against real market data."""

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
