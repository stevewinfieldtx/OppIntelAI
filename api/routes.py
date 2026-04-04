"""
OppIntelAI API Routes
Unified endpoints for Prospector, Hydrator, Fit Check, Render, and ClearSignals proxy.
"""
import asyncio
import hashlib
import json
import logging
import time
import uuid

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel, Field
from typing import Optional

from modules.hydrator.orchestrator import hydrate
from modules.prospector.orchestrator import prospect
from modules.formatter.synthesis_formatter import (
    generate_html,
    normalize_prospector_output,
    normalize_hydrator_output,
)
from core.cache import (
    get_cache_stats, expire_tdp,
    log_fit_check, update_fit_engagement, get_fit_check_leads,
)
from core.agents import solution_agent, customer_agent
from modules.hydrator.agents import fit_check_agent
from core.config import CLEARSIGNALS_URL, OPPINTELAI_PUBLIC_URL

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["oppintelai"])


# === Request Models ===

class HydrationRequest(BaseModel):
    """Module 2: Reactive — Hydrate a single inbound lead."""
    solution_url: str = Field(..., description="URL of the solution/product website")
    solution_name: Optional[str] = Field("", description="Name of the solution (auto-detected if empty)")
    customer_url: str = Field(..., description="URL of the prospect company's website")
    industry: Optional[str] = Field("", description="Industry vertical (auto-detected if empty)")
    contact_title: Optional[str] = Field("", description="Title of the person being contacted")


class ProspectorRequest(BaseModel):
    """Module 1: Proactive — Find and hydrate new leads."""
    solution_url: str = Field(..., description="URL of the solution/product website")
    solution_name: Optional[str] = Field("", description="Name of the solution (auto-detected if empty)")
    target_vertical: Optional[str] = Field("", description="Target vertical (auto-selected if empty)")
    geo_seed: Optional[str] = Field("", description="Target geography (auto-selected if empty)")
    account_volume: Optional[int] = Field(10, description="Number of prospects to find", ge=1, le=50)
    hydrate_results: Optional[bool] = Field(True, description="Run full hydration on found prospects")


class FitCheckRequest(BaseModel):
    """Prospect-facing fit check — just their URL + the vendor's URL."""
    prospect_url: str = Field(..., description="URL of the prospect's company website")
    solution_url: str = Field(..., description="URL of the vendor/solution website")
    solution_name: Optional[str] = Field("", description="Solution name for caching")


class FitEngagementUpdate(BaseModel):
    """Frontend heartbeat to track engagement behavior."""
    session_id: str = Field(..., description="Session ID from the fit check")
    sections_viewed: list = Field(default_factory=list)
    time_on_page_seconds: int = Field(0)
    cta_clicked: str = Field("")


class ExpireRequest(BaseModel):
    """Force-expire a cached TDP."""
    tdp_type: str = Field(..., description="Type of TDP: solution, industry, or customer")
    identifier: str = Field(..., description="The identifier (solution name, industry, or URL)")


# === Module 2: Hydrator (Reactive) ===

@router.post("/hydrate", summary="Hydrate an inbound lead")
async def hydrate_lead(request: HydrationRequest):
    """
    Module 2: Reactive Hydration
    Takes a single inbound lead and runs it through the 5-agent pipeline.
    """
    try:
        result = await hydrate(
            solution_url=request.solution_url,
            solution_name=request.solution_name,
            customer_url=request.customer_url,
            industry_name=request.industry,
            contact_title=request.contact_title,
        )
        return result
    except Exception as e:
        logger.error(f"Hydration error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# === Module 2: Hydrator — SSE Streaming ===

@router.get("/hydrate/stream", summary="Hydrate an inbound lead with real-time progress")
async def hydrate_stream(
    solution_url: str,
    customer_url: str,
    solution_name: str = "",
    industry: str = "",
    contact_title: str = "",
):
    """
    SSE version of /hydrate. Streams progress events as each agent completes,
    then delivers the full result. Keeps the HTTP connection alive for long runs.
    """
    queue: asyncio.Queue = asyncio.Queue()

    async def callback(data: dict):
        await queue.put({"type": "progress", **data})

    async def run_pipeline():
        try:
            result = await hydrate(
                solution_url=solution_url,
                solution_name=solution_name,
                customer_url=customer_url,
                industry_name=industry,
                contact_title=contact_title,
                callback=callback,
            )
            await queue.put({"type": "result", "data": result})
        except Exception as e:
            logger.error(f"Hydration SSE error: {e}")
            await queue.put({"type": "error", "detail": str(e)})

    async def generate():
        task = asyncio.create_task(run_pipeline())
        try:
            while True:
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=20.0)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                yield f"data: {json.dumps(item)}\n\n"
                if item.get("type") in ("result", "error"):
                    break
        finally:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


# === Module 1: Prospector (Proactive) ===

@router.post("/prospect", summary="Proactively find and hydrate leads")
async def prospect_leads(request: ProspectorRequest):
    """
    Module 1: Proactive Prospecting
    Takes a solution and optionally a vertical + geography, finds qualifying companies.
    """
    try:
        result = await prospect(
            solution_url=request.solution_url,
            solution_name=request.solution_name,
            target_vertical=request.target_vertical,
            geo_seed=request.geo_seed,
            account_volume=request.account_volume,
            hydrate_results=request.hydrate_results,
        )
        return result
    except Exception as e:
        logger.error(f"Prospecting error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# === Module 1: Prospector — SSE Streaming ===

@router.get("/prospect/stream", summary="Proactively find leads with real-time progress")
async def prospect_stream(
    solution_url: str,
    solution_name: str = "",
    target_vertical: str = "",
    geo_seed: str = "",
    account_volume: int = 10,
    hydrate_results: bool = True,
):
    """
    SSE version of /prospect. Streams progress events through all pipeline stages,
    then delivers the full result. Essential for long prospecting runs.
    """
    queue: asyncio.Queue = asyncio.Queue()

    async def callback(data: dict):
        await queue.put({"type": "progress", **data})

    async def run_pipeline():
        try:
            result = await prospect(
                solution_url=solution_url,
                solution_name=solution_name,
                target_vertical=target_vertical,
                geo_seed=geo_seed,
                account_volume=max(1, min(account_volume, 50)),
                hydrate_results=hydrate_results,
                callback=callback,
            )
            await queue.put({"type": "result", "data": result})
        except Exception as e:
            logger.error(f"Prospector SSE error: {e}")
            await queue.put({"type": "error", "detail": str(e)})

    async def generate():
        task = asyncio.create_task(run_pipeline())
        try:
            while True:
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=20.0)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                yield f"data: {json.dumps(item)}\n\n"
                if item.get("type") in ("result", "error"):
                    break
        finally:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


# === Fit Check (Prospect-Facing Widget) ===

@router.post("/fit-check", summary="Prospect-facing solution fit analysis")
async def fit_check(request: FitCheckRequest, req: Request):
    """
    Prospect-facing endpoint. The prospect enters their company URL and
    gets a personalized analysis of how the vendor's solution fits their business.

    Pipeline:
    1. Solution TDP (cached after first run — vendor pre-warms this)
    2. Customer TDP (cached per company URL — reused across fit checks)
    3. Fit synthesis (lightweight, always fresh per combination)

    Engagement is logged for the vendor's lead intelligence dashboard.
    """
    start_time = time.time()
    session_id = str(uuid.uuid4())
    total_tokens = 0
    cache_hits = []

    try:
        # Stage 1: Solution TDP (almost always cached)
        solution_tdp = await solution_agent.run(
            solution_url=request.solution_url,
            solution_name=request.solution_name,
        )
        total_tokens += solution_tdp.get("token_cost", 0)
        if solution_tdp.get("from_cache"):
            cache_hits.append("solution")

        # Stage 2: Customer TDP (cached per company URL)
        customer_tdp = await customer_agent.run(
            customer_url=request.prospect_url,
            industry_context=solution_tdp.get("data", {}).get("category", ""),
        )
        total_tokens += customer_tdp.get("token_cost", 0)
        if customer_tdp.get("from_cache"):
            cache_hits.append("customer")

        # Stage 3: Fit synthesis (always fresh per combination)
        fit_result = await fit_check_agent.run(
            solution_tdp=solution_tdp,
            customer_tdp=customer_tdp,
        )
        total_tokens += fit_result.get("usage", {}).get("total_tokens", 0)

        elapsed = round(time.time() - start_time, 1)
        fit_data = fit_result.get("parsed", {})

        # Log engagement for vendor intelligence
        ip_raw = req.client.host if req.client else ""
        ip_hash = hashlib.sha256(ip_raw.encode()).hexdigest()[:16] if ip_raw else ""

        await log_fit_check(
            session_id=session_id,
            prospect_url=request.prospect_url,
            solution_url=request.solution_url,
            prospect_name=fit_data.get("company_name", ""),
            prospect_industry=fit_data.get("industry", ""),
            solution_name=solution_tdp.get("data", {}).get("solution_name", request.solution_name),
            fit_score=fit_data.get("fit_score", 0),
            fit_level=fit_data.get("fit_level", ""),
            referrer=req.headers.get("referer", ""),
            user_agent=req.headers.get("user-agent", ""),
            ip_hash=ip_hash,
        )

        return {
            "session_id": session_id,
            "fit_analysis": fit_data,
            "meta": {
                "elapsed_seconds": elapsed,
                "total_tokens": total_tokens,
                "estimated_cost_usd": round(total_tokens * 0.25 / 1_000_000, 4),
                "cache_hits": cache_hits,
            },
        }

    except Exception as e:
        logger.error(f"Fit check error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/fit-check/engagement", summary="Update fit check engagement data")
async def fit_engagement(request: FitEngagementUpdate):
    """Frontend heartbeat — tracks sections viewed, time spent, CTA clicks."""
    try:
        await update_fit_engagement(
            session_id=request.session_id,
            sections_viewed=request.sections_viewed,
            time_on_page_seconds=request.time_on_page_seconds,
            cta_clicked=request.cta_clicked,
        )
        return {"status": "updated"}
    except Exception as e:
        logger.error(f"Engagement update error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/fit-check/leads", summary="Get fit check lead intelligence")
async def fit_leads(limit: int = 50):
    """Vendor dashboard — see who's been checking fit and what they looked at."""
    return await get_fit_check_leads(limit=limit)


# === Render Endpoints — Agent 7 ===

class RenderProspectorRequest(BaseModel):
    result: dict = Field(..., description="Full prospector output from POST /prospect")


class RenderHydratorRequest(BaseModel):
    result: dict = Field(..., description="Full hydrator output from POST /hydrate")


@router.post("/render/prospect", summary="Render prospector output as HTML report",
             response_class=HTMLResponse)
async def render_prospect_html(request: RenderProspectorRequest):
    try:
        cards, meta = normalize_prospector_output(request.result)
        html = generate_html(
            cards=cards, meta=meta,
            oppintelai_base_url=OPPINTELAI_PUBLIC_URL,
            clearsignals_url=CLEARSIGNALS_URL,
        )
        return HTMLResponse(content=html, media_type="text/html")
    except Exception as e:
        logger.error(f"Render (prospect) error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/render/hydrate", summary="Render hydrator output as HTML report",
             response_class=HTMLResponse)
async def render_hydrate_html(request: RenderHydratorRequest):
    try:
        cards, meta = normalize_hydrator_output(request.result)
        html = generate_html(
            cards=cards, meta=meta,
            oppintelai_base_url=OPPINTELAI_PUBLIC_URL,
            clearsignals_url=CLEARSIGNALS_URL,
        )
        return HTMLResponse(content=html, media_type="text/html")
    except Exception as e:
        logger.error(f"Render (hydrate) error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# === ClearSignals Proxy ===

class ThreadAnalysisRequest(BaseModel):
    thread: str = Field(..., description="Raw pasted email thread text")
    mode: str = Field("coaching", description="'coaching' or 'postmortem'")
    userId: Optional[str] = Field(None)


@router.post("/analyze-thread", summary="Analyze email thread via ClearSignals")
async def analyze_thread(request: ThreadAnalysisRequest):
    if not CLEARSIGNALS_URL:
        raise HTTPException(status_code=503, detail="ClearSignals not configured.")

    target = CLEARSIGNALS_URL.rstrip("/") + "/api/analyze"
    payload = {"thread": request.thread, "mode": request.mode}
    if request.userId:
        payload["userId"] = request.userId

    try:
        async with httpx.AsyncClient(timeout=90.0) as client:
            resp = await client.post(target, json=payload)
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPStatusError as e:
        logger.error(f"ClearSignals proxy error {e.response.status_code}: {e.response.text}")
        raise HTTPException(status_code=502, detail=f"ClearSignals returned {e.response.status_code}")
    except Exception as e:
        logger.error(f"ClearSignals proxy failed: {e}")
        raise HTTPException(status_code=502, detail=str(e))


# === Shared Endpoints ===

@router.post("/expire", summary="Force-expire a cached TDP")
async def expire_cache(request: ExpireRequest):
    success = await expire_tdp(request.tdp_type, request.identifier)
    if success:
        return {"status": "expired", "tdp_type": request.tdp_type, "identifier": request.identifier}
    return {"status": "not_found", "tdp_type": request.tdp_type, "identifier": request.identifier}


@router.get("/stats", summary="Cache and usage statistics")
async def cache_stats():
    return await get_cache_stats()


@router.get("/health", summary="Health check")
async def health():
    return {"status": "healthy", "service": "OppIntelAI",
            "modules": ["prospector", "hydrator", "fit-check"]}
