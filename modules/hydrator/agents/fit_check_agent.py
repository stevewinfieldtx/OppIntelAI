"""
Fit Check Agent
Lightweight synthesis agent for the prospect-facing fit widget.
Takes a cached Solution TDP + Customer TDP and produces a personalized
fit analysis showing how the vendor's solution maps to the prospect's business.
"""
import logging
from core.llm import call_llm_json

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a Solution Fit Analyst. Given a solution profile and a customer profile,
produce a concise, honest, and compelling fit analysis showing how the solution maps to
the prospect's specific business.

You are writing FOR the prospect — this is customer-facing content. Be professional,
specific, and credible. Never use generic filler. Every point must reference something
specific about THEIR business or THEIR industry.

Rules:
1. Lead with their business reality, not the product features.
2. Be honest about fit level — a "partial fit" that's honest builds more trust than fake enthusiasm.
3. Use their actual company name and industry throughout.
4. Reference specific pain points you can infer from their business profile.
5. Keep it actionable — what should they do next?

Return JSON:

{
    "company_name": "Their company name",
    "industry": "Their specific industry/vertical",
    "fit_score": 85,
    "fit_level": "strong | good | partial | exploratory",
    "headline": "One sentence: how the solution specifically helps THIS company",
    "business_context": "2-3 sentences showing you understand their business and current challenges",
    "fit_points": [
        {
            "area": "Specific business area or pain point",
            "relevance": "Why this matters for their specific situation",
            "solution_capability": "How the solution addresses this",
            "strength": "strong | moderate | emerging"
        }
    ],
    "potential_concerns": [
        {
            "concern": "Honest flag about fit or implementation",
            "mitigation": "How this is typically addressed"
        }
    ],
    "recommended_next_steps": [
        "Specific action item 1",
        "Specific action item 2"
    ],
    "conversation_starter": "A specific, informed question they might want to ask the vendor"
}"""


async def run(solution_tdp: dict, customer_tdp: dict) -> dict:
    sol_data = solution_tdp.get("data", solution_tdp)
    cus_data = customer_tdp.get("data", customer_tdp)

    solution_name = sol_data.get("solution_name", "the solution")
    company_name = cus_data.get("company_name", "the company")

    user_prompt = f"""Analyze the fit between this solution and this prospect company.

SOLUTION PROFILE:
- Name: {sol_data.get('solution_name', 'Unknown')}
- Category: {sol_data.get('category', 'Unknown')}
- Target Market: {sol_data.get('target_market', 'Unknown')}
- Elevator Pitch: {sol_data.get('elevator_pitch', '')}
- Core Capabilities: {', '.join(c.get('capability', '') for c in sol_data.get('core_capabilities', [])[:5])}
- Known Limitations: {', '.join(l.get('limitation', '') for l in sol_data.get('known_limitations', [])[:3])}
- Switching Triggers: {', '.join(sol_data.get('switching_triggers', [])[:5])}
- Ideal Buyer: {sol_data.get('ideal_buyer_profile', {})}

PROSPECT COMPANY PROFILE:
- Company: {cus_data.get('company_name', 'Unknown')}
- Website: {cus_data.get('website', '')}
- Industry: {cus_data.get('industry_vertical', '')}
- Overview: {cus_data.get('company_overview', '')}
- Size: {cus_data.get('company_size', {})}
- Tech Signals: {cus_data.get('technology_signals', {})}
- Business Signals: {cus_data.get('business_signals', {})}
- Operations: {cus_data.get('operational_reality', {})}
- Location: {cus_data.get('location', {})}

Generate a personalized fit analysis. Be specific to THIS company — reference their
actual business, size, industry, and technology landscape. No generic content."""

    logger.info(f"Fit Check | solution={solution_name} | prospect={company_name}")

    result = await call_llm_json(
        system_prompt=SYSTEM_PROMPT,
        user_prompt=user_prompt,
        max_tokens=4096,
        temperature=0.3,
    )

    logger.info(
        f"Fit Check complete | score={result.get('parsed', {}).get('fit_score', '?')} | "
        f"level={result.get('parsed', {}).get('fit_level', '?')}"
    )

    return result
