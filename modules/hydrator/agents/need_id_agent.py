"""
Need Identification Agent
Synthesis agent that crosses Solution TDP + Industry TDP + Customer TDP
to identify specific, actionable needs and pain point matches.
"""
import json
import logging
from core.llm import call_llm_json

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are the Need Identification Agent in a lead hydration engine. You receive three intelligence profiles:

1. SOLUTION TDP — What the product does, its strengths, weaknesses, and ideal buyer
2. INDUSTRY TDP — The industry's pain points, trends, technology landscape, and buying behavior  
3. CUSTOMER TDP — The specific prospect company's operations, signals, and likely challenges

Your job is to SYNTHESIZE these three profiles and identify the specific needs where the solution addresses the customer's pain, filtered through the industry context.

You are a strategic sales analyst. You connect dots. You find the non-obvious intersections where the solution's capabilities meet the customer's operational reality.

Return your analysis as JSON with this exact structure:

{
    "fit_score": 85,
    "fit_assessment": "One paragraph summary of how well this solution fits this customer",
    "primary_needs": [
        {
            "need": "Specific business need identified",
            "evidence": "What evidence from the customer profile supports this need",
            "solution_capability": "Which specific solution capability addresses this",
            "strength_of_match": "strong | moderate | partial",
            "industry_context": "Why this need is common or urgent in their industry",
            "pain_severity": "critical | high | moderate",
            "talk_track": "One sentence a rep could say to surface this need in conversation"
        }
    ],
    "secondary_needs": [
        {
            "need": "Less urgent but still relevant need",
            "evidence": "Supporting evidence",
            "solution_capability": "Relevant capability",
            "strength_of_match": "strong | moderate | partial"
        }
    ],
    "gaps_and_risks": [
        {
            "gap": "Where the solution doesn't fully address a customer need",
            "risk_level": "high | medium | low",
            "mitigation": "How to handle this in conversation"
        }
    ],
    "timing_signals": [
        {
            "signal": "Evidence that now is the right time to engage",
            "source": "Where this signal came from (hiring, news, growth, etc.)",
            "urgency": "immediate | near_term | developing"
        }
    ],
    "value_proposition": {
        "headline": "One sentence value prop tailored to this specific customer",
        "financial_angle": "Potential cost savings or revenue impact angle",
        "operational_angle": "How this improves their day-to-day operations",
        "strategic_angle": "How this positions them for growth or competitive advantage"
    },
    "competitive_threats": [
        {
            "competitor": "Who else might be talking to this prospect",
            "their_angle": "What the competitor would likely pitch",
            "our_counter": "How to position against them"
        }
    ]
}

Be specific and evidence-based. Every need should trace back to something concrete in the customer or industry profile. Do not invent needs that aren't supported by evidence."""


async def run(solution_tdp: dict, industry_tdp: dict, customer_tdp: dict) -> dict:
    """
    Synthesize three TDPs to identify needs and fit.
    
    This agent is NOT cached — it's unique per hydration since it
    combines specific solution + industry + customer contexts.
    
    Args:
        solution_tdp: Solution TDP data
        industry_tdp: Industry TDP data
        customer_tdp: Customer TDP data
        
    Returns:
        Need identification analysis
    """
    logger.info(
        f"Running Need ID synthesis: "
        f"{solution_tdp.get('label', '?')} × "
        f"{industry_tdp.get('label', '?')} × "
        f"{customer_tdp.get('label', '?')}"
    )

    # Extract just the data portion from TDP wrappers
    sol_data = solution_tdp.get("data", solution_tdp)
    ind_data = industry_tdp.get("data", industry_tdp)
    cust_data = customer_tdp.get("data", customer_tdp)

    user_prompt = f"""Analyze these three intelligence profiles and identify where the solution addresses the customer's specific needs.

=== SOLUTION PROFILE ===
{json.dumps(sol_data, indent=2)}

=== INDUSTRY PROFILE ===
{json.dumps(ind_data, indent=2)}

=== CUSTOMER PROFILE ===
{json.dumps(cust_data, indent=2)}

Cross-reference these profiles to find:
1. PRIMARY NEEDS: Where there's strong evidence the customer has a pain that the solution directly solves
2. SECONDARY NEEDS: Less obvious but still relevant connections
3. GAPS: Where the solution falls short for this specific customer
4. TIMING: Why now might be the right moment to engage
5. VALUE PROP: A tailored value proposition for THIS customer, not generic marketing

Be specific. Trace every need back to evidence from the profiles."""

    result = await call_llm_json(
        system_prompt=SYSTEM_PROMPT,
        user_prompt=user_prompt,
        temperature=0.3,
        max_tokens=6000,
    )

    return {
        "data": result["parsed"],
        "token_cost": result.get("usage", {}).get("total_tokens", 0),
    }
