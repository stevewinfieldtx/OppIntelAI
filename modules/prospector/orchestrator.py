"""
Prospector Orchestrator (Module 1: Proactive)
Takes a solution + optional vertical + optional geography and goes hunting.
Finds prospects, then hydrates each one through the shared intelligence layer.
"""
import asyncio
import logging
import time
from typing import Optional
from core.agents import solution_agent, industry_agent, customer_agent
from modules.prospector.agents import vertical_selector, metro_cartographer, account_prospector
from modules.hydrator.agents import need_id_agent, questions_agent
from core.cache import log_hydration, update_hydration_log

logger = logging.getLogger(__name__)


async def prospect(
    solution_url: str,
    solution_name: str = "",
    target_vertical: str = "",
    geo_seed: str = "",
    account_volume: int = 10,
    hydrate_results: bool = True,
    callback=None,
) -> dict:
    """
    Run the full proactive prospecting pipeline.
    
    Stage 1: Build Solution TDP (shared/cached)
    Stage 2: Select optimal vertical
    Stage 3: Select optimal metro
    Stage 4: Find and score prospects
    Stage 5: (Optional) Hydrate each prospect through Need ID + Questions
    
    Args:
        solution_name: Name of the product/solution to prospect for
        target_vertical: Optional vertical override
        geo_seed: Optional geography override
        account_volume: Number of prospects to find
        hydrate_results: Whether to run full hydration on found prospects
        callback: Optional async callback for progress updates
        
    Returns:
        Complete prospecting results with optional hydrations
    """
    start_time = time.time()
    total_tokens = 0
    cache_hits = []
    stages = {}

    log_id = await log_hydration(
        solution=solution_name,
        customer_url=f"prospector:{geo_seed or 'auto'}",
        industry=target_vertical,
        status="running",
    )

    async def _progress(stage: str, status: str, detail: str = ""):
        logger.info(f"[Prospector {log_id}] {stage}: {status} {detail}")
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
        await _progress("solution", "complete")

        # === STAGE 2: Vertical Selector (Prospector-specific) ===
        await _progress("vertical", "running", "Selecting optimal vertical...")
        vertical_data = await vertical_selector.run(
            solution_tdp=solution_tdp,
            target_vertical=target_vertical,
        )
        total_tokens += vertical_data.get("token_cost", 0)
        stages["vertical"] = {"tokens": vertical_data.get("token_cost", 0)}

        selected_vertical = vertical_data.get("data", {}).get("selected_vertical", target_vertical)
        await _progress("vertical", "complete", f"Selected: {selected_vertical}")

        # === STAGE 2b: Industry Agent for selected vertical (shared/cached) ===
        await _progress("industry", "running", f"Deep-diving {selected_vertical}...")
        industry_tdp = await industry_agent.run(selected_vertical)
        total_tokens += industry_tdp.get("token_cost", 0)
        if industry_tdp.get("from_cache"):
            cache_hits.append("industry")
        stages["industry"] = {
            "from_cache": industry_tdp.get("from_cache", False),
            "tokens": industry_tdp.get("token_cost", 0),
        }
        await _progress("industry", "complete")

        # === STAGE 3: Metro Cartographer (Prospector-specific) ===
        await _progress("metro", "running", "Mapping optimal geography...")
        metro_data = await metro_cartographer.run(
            solution_tdp=solution_tdp,
            vertical_data=vertical_data,
            geo_seed=geo_seed,
        )
        total_tokens += metro_data.get("token_cost", 0)
        stages["metro"] = {"tokens": metro_data.get("token_cost", 0)}

        selected_metro = metro_data.get("data", {}).get("selected_metro", geo_seed)
        await _progress("metro", "complete", f"Selected: {selected_metro}")

        # === STAGE 4: Account Prospector (Prospector-specific) ===
        await _progress("prospecting", "running",
            f"Finding {account_volume} prospects in {selected_metro}...")
        prospects_data = await account_prospector.run(
            solution_tdp=solution_tdp,
            vertical_data=vertical_data,
            metro_data=metro_data,
            account_volume=account_volume,
        )
        total_tokens += prospects_data.get("token_cost", 0)
        stages["prospecting"] = {"tokens": prospects_data.get("token_cost", 0)}

        prospects_list = prospects_data.get("data", {}).get("prospects", [])
        await _progress("prospecting", "complete",
            f"Found {len(prospects_list)} prospects")

        # === STAGE 5: Optional Hydration of each prospect (parallel, max 3 at once) ===
        hydrated_prospects = []
        if hydrate_results and prospects_list:
            await _progress("hydrating", "running",
                f"Hydrating {len(prospects_list)} prospects in parallel...")

            sem = asyncio.Semaphore(3)

            async def hydrate_one(prospect_item: dict) -> dict:
                prospect_url = prospect_item.get("website", "")
                prospect_name = prospect_item.get("name", "prospect")
                async with sem:
                    try:
                        if prospect_url:
                            c_tdp = await customer_agent.run(
                                customer_url=prospect_url,
                                industry_context=selected_vertical,
                            )
                        else:
                            c_tdp = {"data": prospect_item}

                        n_analysis = await need_id_agent.run(
                            solution_tdp=solution_tdp,
                            industry_tdp=industry_tdp,
                            customer_tdp=c_tdp,
                        )

                        qs = await questions_agent.run(
                            solution_tdp=solution_tdp,
                            industry_tdp=industry_tdp,
                            customer_tdp=c_tdp,
                            need_analysis=n_analysis,
                            contact_title=prospect_item.get("contact_title", ""),
                        )

                        tokens = (
                            c_tdp.get("token_cost", 0)
                            + n_analysis.get("token_cost", 0)
                            + qs.get("token_cost", 0)
                        )
                        cache = c_tdp.get("from_cache", False)
                        return {
                            "prospect": prospect_item,
                            "customer_tdp": c_tdp.get("data", {}),
                            "need_analysis": n_analysis.get("data", {}),
                            "conversation_toolkit": qs.get("data", {}),
                            "_tokens": tokens,
                            "_cache": cache,
                            "_name": prospect_name,
                        }
                    except Exception as e:
                        logger.warning(f"Failed to hydrate {prospect_name}: {e}")
                        return {
                            "prospect": prospect_item,
                            "hydration_error": str(e),
                            "_tokens": 0,
                            "_cache": False,
                            "_name": prospect_name,
                        }

            results = await asyncio.gather(
                *[hydrate_one(p) for p in prospects_list]
            )

            for r in results:
                total_tokens += r.pop("_tokens", 0)
                if r.pop("_cache", False):
                    cache_hits.append(f"customer:{r.pop('_name', '')}")
                else:
                    r.pop("_name", None)
                hydrated_prospects.append(r)

            await _progress("hydrating", "complete",
                f"Hydrated {len(hydrated_prospects)} prospects")

        # === BUILD FINAL OUTPUT ===
        elapsed = round(time.time() - start_time, 1)

        result = {
            "meta": {
                "module": "prospector",
                "log_id": log_id,
                "solution": solution_name,
                "selected_vertical": selected_vertical,
                "selected_metro": selected_metro,
                "account_volume_requested": account_volume,
                "accounts_found": len(prospects_list),
                "accounts_hydrated": len(hydrated_prospects),
                "total_tokens": total_tokens,
                "estimated_cost_usd": round(total_tokens * 0.25 / 1_000_000, 4),
                "cache_hits": cache_hits,
                "elapsed_seconds": elapsed,
                "stages": stages,
            },
            "solution_tdp": solution_tdp.get("data", {}),
            "vertical_selection": vertical_data.get("data", {}),
            "industry_tdp": industry_tdp.get("data", {}),
            "metro_selection": metro_data.get("data", {}),
            "prospects": hydrated_prospects if hydrate_results else prospects_list,
            "search_summary": prospects_data.get("data", {}).get("search_summary", {}),
        }

        await update_hydration_log(
            log_id=log_id,
            status="complete",
            total_tokens=total_tokens,
            cache_hits=cache_hits,
            result_summary=f"Found {len(prospects_list)} prospects in {selected_metro} | "
                          f"Hydrated: {len(hydrated_prospects)} | "
                          f"Elapsed: {elapsed}s",
        )

        await _progress("complete", "complete",
            f"Done in {elapsed}s | {total_tokens:,} tokens | "
            f"{len(prospects_list)} prospects found")

        return result

    except Exception as e:
        logger.error(f"Prospecting failed: {e}")
        await update_hydration_log(
            log_id=log_id,
            status="failed",
            total_tokens=total_tokens,
            result_summary=str(e),
        )
        await _progress("error", "failed", str(e))
        raise
