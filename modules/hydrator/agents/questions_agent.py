"""
Questions Agent
Generates discovery questions, expected answers, objection handling,
and pivot strategies based on all prior intelligence.
"""
import json
import logging
from core.llm import call_llm_json

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are the Questions Agent in a lead hydration engine. You receive the complete intelligence package — solution profile, industry profile, customer profile, and identified needs — and your job is to generate the EXECUTABLE CONVERSATION TOOLKIT for the sales rep.

You are a master sales coach. You don't write generic questions. You write questions that:
1. Force a binary admission of pain (yes/no that reveals operational truth)
2. Reference specific details that prove you've done your homework
3. Have pre-mapped expected answers with follow-up pivots
4. Include objection handling for the most likely pushback

Return your output as JSON with this exact structure:

{
    "opening_approach": {
        "recommended_channel": "phone | email | linkedin | warm_intro",
        "channel_reasoning": "Why this channel for this specific prospect",
        "opening_line": "The first sentence out of the rep's mouth or in the first message",
        "rapport_hook": "A specific local or personal detail to establish credibility",
        "tone_guidance": "How to calibrate tone for this specific person and industry"
    },
    "discovery_questions": [
        {
            "question": "The exact question to ask",
            "purpose": "What this question is designed to uncover",
            "pain_it_targets": "Which identified need this maps to",
            "stage": "opener | deepener | quantifier | vision",
            "expected_answer_positive": {
                "answer": "What they'll say if the pain is real",
                "follow_up": "What to say next to deepen the conversation"
            },
            "expected_answer_negative": {
                "answer": "What they'll say if they deny the pain or deflect",
                "pivot": "How to redirect to a related pain point"
            },
            "expected_answer_unexpected": {
                "scenario": "A curveball response the rep might get",
                "recovery": "How to handle it gracefully"
            }
        }
    ],
    "objection_playbook": [
        {
            "objection": "The exact pushback the rep will hear",
            "likelihood": "very_likely | likely | possible",
            "classification": "timing | budget | authority | need | competitor | status_quo",
            "response_strategy": "How to address this objection",
            "example_response": "Word-for-word what the rep could say",
            "bridge_to": "Where to steer the conversation after handling the objection"
        }
    ],
    "email_draft": {
        "subject_line": "Email subject if channel is email or for follow-up",
        "body": "Full email body — personalized, concise, with a specific call-to-action",
        "ps_hook": "A P.S. line with a provocative question or insight"
    },
    "conversation_exit": {
        "success_signal": "How the rep knows the call is going well",
        "next_step_ask": "The specific next step to propose",
        "failure_signal": "How the rep knows to gracefully end",
        "graceful_exit": "How to leave the door open for future contact"
    }
}

Every question must be specific to THIS customer, THIS solution, and THIS industry. No generic discovery questions. If a rep could ask this question to any prospect, it's too generic."""


async def run(
    solution_tdp: dict,
    industry_tdp: dict,
    customer_tdp: dict,
    need_analysis: dict,
    contact_title: str = "",
) -> dict:
    """
    Generate the conversation toolkit based on all prior intelligence.
    
    Args:
        solution_tdp: Solution TDP data
        industry_tdp: Industry TDP data
        customer_tdp: Customer TDP data
        need_analysis: Output from Need ID Agent
        contact_title: Optional title of the person being contacted
        
    Returns:
        Discovery questions, objection handling, and scripts
    """
    logger.info("Generating conversation toolkit")

    sol_data = solution_tdp.get("data", solution_tdp)
    ind_data = industry_tdp.get("data", industry_tdp)
    cust_data = customer_tdp.get("data", customer_tdp)
    need_data = need_analysis.get("data", need_analysis)

    title_context = ""
    if contact_title:
        title_context = f"""
The sales rep will be speaking with someone whose title is: {contact_title}
Tailor all questions, tone, and approach to this specific role. A CFO cares about different things than an IT Director."""

    user_prompt = f"""Generate a complete conversation toolkit for a sales rep approaching this prospect.
{title_context}

=== SOLUTION PROFILE ===
{json.dumps(sol_data, indent=2)}

=== INDUSTRY PROFILE ===
{json.dumps(ind_data, indent=2)}

=== CUSTOMER PROFILE ===
{json.dumps(cust_data, indent=2)}

=== IDENTIFIED NEEDS ===
{json.dumps(need_data, indent=2)}

Generate:
1. OPENING APPROACH: How to start the conversation with maximum credibility
2. DISCOVERY QUESTIONS: 4-6 questions with full answer trees (positive/negative/unexpected responses with pivots for each)
3. OBJECTION PLAYBOOK: The 3-5 most likely objections with word-for-word responses
4. EMAIL DRAFT: A personalized outreach email they could send
5. EXIT STRATEGY: How to close the call successfully or gracefully disengage

Every element must reference specific details from the intelligence profiles. The rep should feel like they have an unfair advantage walking into this conversation."""

    result = await call_llm_json(
        system_prompt=SYSTEM_PROMPT,
        user_prompt=user_prompt,
        temperature=0.4,
        max_tokens=5000,
    )

    return {
        "data": result["parsed"],
        "token_cost": result.get("usage", {}).get("total_tokens", 0),
    }
