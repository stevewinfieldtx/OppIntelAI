"""
Hydrator Orchestrator (Module 2: Reactive)
Takes an inbound lead and runs it through the 5-agent intelligence pipeline.
Uses shared agents from core + Hydrator-specific synthesis agents.
"""
import logging
import time
from typing import Optional
from core.agents import solution_agent, industry_agent, customer_agent
from modules.hydrator.agents import need_id_agent, questions_agent
from core.cache import log_hydration, update_hydration_log

logger = logging.getLogger(__name__)


async def hydrate(
    solution_url: str,
    customer_url: str,
    solution_name: str = "",
    industry_name: str = "",
    contact_title: str = "",
    callback=None,
) -> dict:
    """
    Run the full hydration pipeline on a single inbound lead.
    
    Args:
        solution_name: Name of the product/solution being sold
        customer_url: URL of the prospect company
        industry_name: Optional industry override (auto-detected if empty)
        contact_title: Optional title of the contact person
        callback: Optional async callback for progress updates
        
    Returns:
        Complete hydrated lead card
    """
    start_time = time.time()
    total_tokens = 0
    cache_hits = []
    stages = {}

    log_id = await log_hydration(
        solution=solution_name,
        customer_url=customer_url,
        industry=industry_name,
        status="running",
    )

    async def _progress(stage: str, status: str, detail: str = ""):
        logger.info(f"[Hydration {log_id}] {stage}: {status} {detail}")
        if callback:
            await callback({"stage": stage, "status": status, "detail": detail})

    try:
        # === STAGE 1: Solution Agent (shared/cached) ===
        await _progress("solution", "running", f"Analyzing {solution_url}...")
        solution_tdp = await solution_agent.run(solution_url, solution_name)
        total_tokens += solution_tdp.get("token_cost", 0)
        if solution_tdp.get("from_cache"):
            cache_hits.append("solution")
        stages["solution"] = {
            "from_cache": solution_tdp.get("from_cache", False),
            "tokens": solution_tdp.get("token_cost", 0),
        }
        await _progress("solution", "complete",
            f"{'(cached)' if solution_tdp.get('from_cache') else '(fresh)'}")

        # === STAGE 2: Industry Agent (shared/cached) ===
        if not industry_name:
            sol_data = solution_tdp.get("data", {})
            industry_name = sol_data.get("category", "Technology")
            await _progress("industry", "running",
                f"Auto-detected industry: {industry_name}")
        else:
            await _progress("industry", "running", f"Analyzing {industry_name}...")

        industry_tdp = await industry_agent.run(industry_name)
        total_tokens += industry_tdp.get("token_cost", 0)
        if industry_tdp.get("from_cache"):
            cache_hits.append("industry")
        stages["industry"] = {
            "from_cache": industry_tdp.get("from_cache", False),
            "tokens": industry_tdp.get("token_cost", 0),
        }
        await _progress("industry", "complete",
            f"{'(cached)' if industry_tdp.get('from_cache') else '(fresh)'}")

        # === STAGE 3: Customer Agent (shared/cached) ===
        await _progress("customer", "running", f"Researching {customer_url}...")
        customer_tdp = await customer_agent.run(
            customer_url=customer_url,
            industry_context=industry_name,
        )
        total_tokens += customer_tdp.get("token_cost", 0)
        if customer_tdp.get("from_cache"):
            cache_hits.append("customer")
        stages["customer"] = {
            "from_cache": customer_tdp.get("from_cache", False),
            "tokens": customer_tdp.get("token_cost", 0),
        }
        await _progress("customer", "complete",
            f"{'(cached)' if customer_tdp.get('from_cache') else '(fresh)'}")

        # === STAGE 4: Need Identification Agent (Hydrator-specific) ===
        await _progress("need_id", "running", "Synthesizing intelligence...")
        need_analysis = await need_id_agent.run(
            solution_tdp=solution_tdp,
            industry_tdp=industry_tdp,
            customer_tdp=customer_tdp,
        )
        total_tokens += need_analysis.get("token_cost", 0)
        stages["need_id"] = {
            "from_cache": False,
            "tokens": need_analysis.get("token_cost", 0),
        }
        await _progress("need_id", "complete")

        # === STAGE 5: Questions Agent (Hydrator-specific) ===
        await _progress("questions", "running", "Generating conversation toolkit...")
        conversation_toolkit = await questions_agent.run(
            solution_tdp=solution_tdp,
            industry_tdp=industry_tdp,
            customer_tdp=customer_tdp,
            need_analysis=need_analysis,
            contact_title=contact_title,
        )
        total_tokens += conversation_toolkit.get("token_cost", 0)
        stages["questions"] = {
            "from_cache": False,
            "tokens": conversation_toolkit.get("token_cost", 0),
        }
        await _progress("questions", "complete")

        # === BUILD FINAL OUTPUT ===
        elapsed = round(time.time() - start_time, 1)

        hydrated_lead = {
            "meta": {
                "module": "hydrator",
                "hydration_id": log_id,
                "solution": solution_name,
                "customer_url": customer_url,
                "industry": industry_name,
                "contact_title": contact_title,
                "total_tokens": total_tokens,
                "estimated_cost_usd": round(total_tokens * 0.25 / 1_000_000, 4),
                "cache_hits": cache_hits,
                "elapsed_seconds": elapsed,
                "stages": stages,
            },
            "solution_tdp": solution_tdp.get("data", {}),
            "industry_tdp": industry_tdp.get("data", {}),
            "customer_tdp": customer_tdp.get("data", {}),
            "need_analysis": need_analysis.get("data", {}),
            "conversation_toolkit": conversation_toolkit.get("data", {}),
        }

        await update_hydration_log(
            log_id=log_id,
            status="complete",
            total_tokens=total_tokens,
            cache_hits=cache_hits,
            result_summary=f"Fit score: {need_analysis.get('data', {}).get('fit_score', '?')} | "
                          f"Elapsed: {elapsed}s | Cache hits: {len(cache_hits)}/3",
        )

        await _progress("complete", "complete",
            f"Done in {elapsed}s | {total_tokens:,} tokens | "
            f"~${total_tokens * 0.25 / 1_000_000:.4f}")

        return hydrated_lead

    except Exception as e:
        logger.error(f"Hydration failed: {e}")
        await update_hydration_log(
            log_id=log_id,
            status="failed",
            total_tokens=total_tokens,
            result_summary=str(e),
        )
        await _progress("error", "failed", str(e))
        raise
