"""
Account Prospector Agent (Prospector Module)

Identifies specific companies matching the propensity profile.
Qualification criteria are derived at runtime from the solution TDP and
vertical data — no hardcoded filters here.
"""
import json
import logging
from core.llm import call_llm_json

logger = logging.getLogger(__name__)

# ── System prompt: role only, zero hardcoded criteria ────────────────────────
# All qualification logic (size, revenue, tech footprint, scoring rubric) is
# injected at runtime via the user prompt, derived from the solution TDP and
# vertical selection.  This makes the agent work correctly for any solution
# from SMB ERP to enterprise NDR without any code changes.
SYSTEM_PROMPT = """You are the Account Prospector Agent in a proactive B2B lead prospecting engine.

Your job: given a solution profile, a target vertical, and a target metro, identify SPECIFIC REAL
COMPANIES that are strong candidates to buy the solution RIGHT NOW.

You will be given:
- SOLUTION PROFILE  — what the product does, who it's built for, switching triggers
- QUALIFICATION CRITERIA — derived from the solution profile (size, revenue, complexity signals)
- DISQUALIFICATION SIGNALS — tech footprint or operational patterns that indicate a poor fit
- SCORING RUBRIC — how to assign a 0-100 priority score for this specific solution
- TARGET VERTICAL — the industry segment to focus on
- TARGET METRO — the geographic area to search within

Rules:
1. Every company MUST be real.  Use web search to find actual businesses.
2. Never fabricate company names, addresses, phone numbers, or websites.
3. Apply the provided qualification criteria exactly as given — do NOT substitute your own.
4. Score each prospect using the provided scoring rubric — do NOT use a generic rubric.
5. The "who_is_this" narrative must contain specific intel a rep couldn't guess from the name alone.
6. Pain tags must reflect things the prospect would actually say out loud.
7. Landmarks must be specific enough that a rep could mention them without looking at a map.
8. If you cannot find enough qualifying companies, return what you have with honest counts.
   Do NOT pad results with fabricated or marginally-qualifying entries.

   Return your findings as JSON:
   {
     "prospects": [
         {
               "id": 1,
                     "name": "Exact company name",
                           "website": "Company URL or empty string if not found",
                                 "metro": "Metro area",
                                       "location": "City, ST",
                                             "landmark": "Specific local landmark or business park",
                                                   "employees": "Estimated range e.g. 150-300",
                                                         "phone": "(XXX) XXX-XXXX or empty string if not found",
                                                               "priority": 85,
                                                                     "priority_class": "high | medium | low",
                                                                           "who_is_this": "2-3 sentence narrative: company type + local market position + current trigger event + pain implication",
                                                                                 "contact_title": "Most likely decision-maker title",
                                                                                       "lead_module": "The specific solution capability that maps to their top pain",
                                                                                             "pain_tags": ["Pain point 1", "Pain point 2", "Pain point 3"],
                                                                                                   "growth_signals": ["Specific evidence of growth, hiring, or change"],
                                                                                                         "disqualification_risk": "Any reason this lead might not qualify on deeper inspection"
    }
      ],
        "search_summary": {
            "total_found": 0,
                "high_priority": 0,
                    "medium_priority": 0,
                        "metros_covered": ["Specific sub-areas searched within the metro"],
                            "verticals_represented": ["Micro-verticals found"]
                              }
                              }"""


def _build_qualification_block(sol_data: dict, vert_data: dict) -> str:
       """
           Build the qualification criteria, disqualification signals, and scoring
               rubric dynamically from the solution TDP and vertical selection.

                   This is the core of the fix: nothing is hardcoded.  Every run derives its
                       own criteria from the actual solution being prospected.
                           """
       ibp = sol_data.get("ideal_buyer_profile", {})
       company_size = ibp.get("company_size", "any size — use judgment based on solution complexity")
       revenue_range = ibp.get("revenue_range", "not specified — infer from company size")
       complexity_trigger = ibp.get("complexity_trigger", "")
       tools_outgrown = ibp.get("current_tools_outgrown", [])

    switching_triggers = sol_data.get("switching_triggers", [])
    target_market = sol_data.get("target_market", "")
    solution_category = sol_data.get("category", "")

    micro_verticals = vert_data.get("micro_verticals", [])
    structural_fit = vert_data.get("structural_fit", "")
    pain_density = vert_data.get("pain_density", "")

    # Build disqualifiers: only include tools that are direct replacements,
    # NOT ancillary tools that happen to share the market (e.g., having Salesforce
    # is NOT a disqualifier for a security product).
    disqualifier_block = ""
    if tools_outgrown:
               disqualifier_block = f"""
               DISQUALIFICATION SIGNALS (companies already well-served — likely not a fit):
               - Already running: {', '.join(tools_outgrown)}
                 Note: only disqualify if these tools directly replace the solution's core function.
                   Presence of unrelated enterprise tools (CRM, HRIS, etc.) is NOT a disqualifier."""

    # Build scoring rubric derived from the solution's switching triggers
    trigger_lines = "\n".join(
               f"  - {t}" for t in switching_triggers[:6]
    ) if switching_triggers else "  - Operational complexity matching the solution's core use case"

    micro_lines = "\n".join(
               f"  - {m}" for m in micro_verticals[:5]
    ) if micro_verticals else ""

    return f"""
    QUALIFICATION CRITERIA (derived from solution profile — apply these, not generic SMB filters):
    - Target company size: {company_size}
    - Target revenue range: {revenue_range}
    - Complexity trigger: {complexity_trigger}
    - Solution category: {solution_category}
    - Target market: {target_market}
    {disqualifier_block}

    SCORING RUBRIC (0-100) — score based on fit to THIS solution, not generic signals:
      90-100: Multiple active switching triggers present + size/complexity match + no incumbent
        80-89:  Strong size/complexity match + at least one clear switching trigger
          70-79:  Size/complexity match, switching triggers inferred but not confirmed
            60-69:  Partial fit — matches vertical but size or complexity is borderline
              Below 60: Marginal — include only if the vertical has thin pickings

              Switching triggers that drive a 90+ score for this solution:
              {trigger_lines}

              PRIORITY MICRO-VERTICALS within the target vertical (highest-propensity sub-segments):
              {micro_lines if micro_lines else "  - Use the vertical selection rationale to identify sub-segments"}

              Structural fit rationale for this vertical: {structural_fit}
              Pain density context: {pain_density}"""


async def run(
       solution_tdp: dict,
       vertical_data: dict,
       metro_data: dict,
       account_volume: int = 10,
) -> dict:
       """
           Find and qual
