"""
Contact CPP Agent
Builds a lightweight Communication Personality Profile (CPP)
for a named contact using Firecrawl LinkedIn interact + web search.

Pipeline:
  1. If LinkedIn URL available and Firecrawl configured:
     — Use interact to extract full LinkedIn profile content
       (headline, about, experience, recommendations, activity)
  2. Web search via LLM :online fills any gaps and finds
     conference bios, articles, press mentions, etc.
  3. CPP synthesized from all gathered content

This is NOT the full 22-dimension TrueWriting CPP.
It is a first-contact read — 6 dimensions + rep guidance.
"""
import logging
from core.llm import call_llm_json
from core import firecrawl

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a Communication Intelligence Analyst building a first-outreach CPP (Communication Personality Profile) for a B2B sales rep.

You have been given a contact's name, title, company, and any content gathered from their public digital footprint.

Where SCRAPED LINKEDIN CONTENT is provided, treat it as the most reliable signal — it is their actual writing. Weight it heavily for vocabulary, formality, directness, and signature language.

For any dimensions not covered by scraped content, use web search to find:
- Company bio or team page
- Conference speaker bios or session descriptions
- Published articles, blog posts, quoted press coverage
- Any other public writing

Score each dimension 1-10 with justification grounded in actual evidence.

Return JSON:

{
  "contact_name": "Full name",
  "title": "Their current title",
  "company": "Their company",
  "headline": "Their LinkedIn headline or equivalent if found",
  "confidence": "high | medium | low | none",
  "sources_found": ["LinkedIn profile", "company bio", "conference speaker", etc.],
  "dimensions": {
    "directness": {
      "score": 7,
      "label": "direct | balanced | diplomatic",
      "justification": "One sentence grounded in observed language or career pattern",
      "signal": "Quote or specific observation"
    },
    "formality": {
      "score": 6,
      "label": "formal | professional | conversational | casual",
      "justification": "...",
      "signal": "..."
    },
    "decision_style": {
      "score": 0,
      "label": "analytical | intuitive | relationship-driven | process-driven",
      "justification": "...",
      "signal": "..."
    },
    "persuasion_receptivity": {
      "score": 0,
      "label": "data/ROI | social proof | authority | narrative | relationship",
      "justification": "What type of argument is most likely to land",
      "signal": "..."
    },
    "risk_tolerance": {
      "score": 0,
      "label": "conservative | moderate | aggressive",
      "justification": "...",
      "signal": "..."
    },
    "emotional_expressiveness": {
      "score": 0,
      "label": "stoic | measured | expressive | passionate",
      "justification": "...",
      "signal": "..."
    }
  },
  "signature_language": [
    "Words or phrases this person uses repeatedly"
  ],
  "rep_guidance": {
    "opening_tone": "How should the rep open?",
    "what_to_lead_with": "What angle or hook will most likely get a response",
    "what_to_avoid": "Things that will land badly based on their profile",
    "subject_line_style": "Guidance on subject line tone",
    "one_sentence_briefing": "10-second brief for the rep before they hit send"
  },
  "insufficient_data_flags": ["Dimensions where data was too thin to score confidently"]
}

CRITICAL RULES:
- Scraped LinkedIn content is your highest-quality signal. Use it directly.
- Never score above 4 on inference alone with no direct evidence.
- Signature language must be real phrases from their writing.
- If they have almost no public footprint, say so — that itself is signal.
- rep_guidance is the most important output. Make it specific and actionable.
"""


async def run(
    contact_name: str,
    contact_title: str = "",
    company_name: str = "",
    linkedin_url: str = "",
) -> dict:
    """
    Build a first-outreach CPP using Firecrawl LinkedIn interact
    and LLM web search.

    Args:
        contact_name:  Full name from Apollo
        contact_title: Title from Apollo
        company_name:  Company name for search context
        linkedin_url:  LinkedIn URL from Apollo (enables Firecrawl interact)

    Returns:
        CPP dict with dimensions + rep_guidance
    """
    if not contact_name:
        return _empty_cpp("", "", "No contact name provided")

    logger.info(
        f"[ContactCPP] Building CPP for {contact_name} | "
        f"{contact_title} | {company_name} | linkedin={'yes' if linkedin_url else 'no'}"
    )

    # ── Step 1: Firecrawl LinkedIn interact (best signal available) ──────────
    linkedin_content = ""
    if linkedin_url and firecrawl.is_available():
        logger.info(f"[ContactCPP] Firecrawl LinkedIn interact: {linkedin_url}")
        linkedin_content = await firecrawl.interact_linkedin(linkedin_url) or ""
        if linkedin_content:
            logger.info(f"[ContactCPP] LinkedIn content: {len(linkedin_content)} chars")
        else:
            logger.info("[ContactCPP] LinkedIn interact returned empty — falling back to web search")

    # ── Step 2: Build prompt with scraped content + web search fallback ──────
    linkedin_hint = f"\nLinkedIn URL: {linkedin_url}" if linkedin_url and not linkedin_content else ""

    scraped_block = ""
    if linkedin_content:
        scraped_block = (
            f"\n\nSCRAPED LINKEDIN PROFILE — treat as highest-quality signal:\n"
            f"{linkedin_content}"
        )

    web_search_instruction = (
        "The scraped LinkedIn content above is your primary source. "
        "Use web search to find additional public content: "
        "company bio, conference talks, articles, press mentions."
        if linkedin_content
        else
        "Search the web for this person's public digital footprint: "
        "LinkedIn profile, company bio, conference speaker bios, "
        "published articles, press coverage, any public writing."
    )

    user_prompt = f"""Build a first-outreach Communication Personality Profile for this contact.

CONTACT:
- Name: {contact_name}
- Title: {contact_title or 'Unknown'}
- Company: {company_name or 'Unknown'}{linkedin_hint}
{scraped_block}

{web_search_instruction}

Focus entirely on what this tells a sales rep about HOW to approach this person cold.
What tone? What angle? What to lead with? What to avoid?"""

    result = await call_llm_json(
        system_prompt=SYSTEM_PROMPT,
        user_prompt=user_prompt,
        max_tokens=2048,
        temperature=0.3,
        use_web_search=True,
    )

    parsed = result.get("parsed", {})

    # Tag whether LinkedIn scraping contributed
    parsed["_linkedin_scraped"] = bool(linkedin_content)
    parsed["_firecrawl_used"] = firecrawl.is_available()

    logger.info(
        f"[ContactCPP] Complete | confidence={parsed.get('confidence', '?')} | "
        f"linkedin_scraped={bool(linkedin_content)} | "
        f"sources={parsed.get('sources_found', [])}"
    )

    return parsed


def _empty_cpp(name: str, title: str, reason: str) -> dict:
    return {
        "contact_name": name,
        "title": title,
        "company": "",
        "headline": "",
        "confidence": "none",
        "sources_found": [],
        "dimensions": {},
        "signature_language": [],
        "rep_guidance": {
            "opening_tone": "Unknown — no data",
            "what_to_lead_with": "Unknown — no data",
            "what_to_avoid": "Unknown — no data",
            "subject_line_style": "Unknown — no data",
            "one_sentence_briefing": "No public data found. Lead with relevance to their role, keep it short, make the ask specific.",
        },
        "_linkedin_scraped": False,
        "_firecrawl_used": False,
        "insufficient_data_flags": [reason],
    }
