"""
OppIntelAI API Routes
Unified endpoints for both Prospector and Hydrator modules.
"""
import logging
import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from typing import Optional
from modules.hydrator.orchestrator import hydrate
from modules.prospector.orchestrator import prospect
from modules.formatter.synthesis_formatter import (
    generate_html,
    normalize_prospector_output,
    normalize_hydrator_output,
)
from core.cache import get_cache_stats, expire_tdp
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


class ExpireRequest(BaseModel):
    """Force-expire a cached TDP."""
    tdp_type: str = Field(..., description="Type of TDP: solution, industry, or customer")
    identifier: str = Field(..., description="The identifier (solution name, industry, or URL)")


# === Module 2: Hydrator (Reactive) ===

@router.post("/hydrate", summary="Hydrate an inbound lead")
async def hydrate_lead(request: HydrationRequest):
    """
    Module 2: Reactive Hydration
    
    Takes a single inbound lead and runs it through the 5-agent pipeline:
    Solution → Industry → Customer → Need ID → Questions
    
    Returns the complete hydrated lead card.
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


# === Module 1: Prospector (Proactive) ===

@router.post("/prospect", summary="Proactively find and hydrate leads")
async def prospect_leads(request: ProspectorRequest):
    """
    Module 1: Proactive Prospecting
    
    Takes a solution and optionally a vertical + geography, then:
    1. Analyzes the solution
    2. Selects optimal vertical
    3. Selects optimal metro
    4. Finds qualifying companies
    5. (Optional) Hydrates each prospect
    
    Returns the complete prospecting results.
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


# === Render Endpoints — Agent 7 ===

class RenderProspectorRequest(BaseModel):
    """Render a prospector result as a self-contained HTML report."""
    result: dict = Field(..., description="Full prospector output from POST /prospect")


class RenderHydratorRequest(BaseModel):
    """Render a hydrator result as a self-contained HTML report."""
    result: dict = Field(..., description="Full hydrator output from POST /hydrate")


@router.post("/render/prospect", summary="Render prospector output as HTML report",
             response_class=HTMLResponse)
async def render_prospect_html(request: RenderProspectorRequest):
    """
    Agent 7 — Synthesis Formatter (Prospector)
    Converts prospector JSON output into a self-contained, sales-rep-ready HTML card report.
    Returns a full HTML page — save or open directly in browser.
    """
    try:
        cards, meta = normalize_prospector_output(request.result)
        html = generate_html(
            cards=cards,
            meta=meta,
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
    """
    Agent 7 — Synthesis Formatter (Hydrator)
    Converts a single hydrated lead into a self-contained HTML card report.
    Returns a full HTML page — save or open directly in browser.
    """
    try:
        cards, meta = normalize_hydrator_output(request.result)
        html = generate_html(
            cards=cards,
            meta=meta,
            oppintelai_base_url=OPPINTELAI_PUBLIC_URL,
            clearsignals_url=CLEARSIGNALS_URL,
        )
        return HTMLResponse(content=html, media_type="text/html")
    except Exception as e:
        logger.error(f"Render (hydrate) error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# === ClearSignals Proxy ===

class ThreadAnalysisRequest(BaseModel):
    """Proxy a thread analysis request to ClearSignals."""
    thread: str  = Field(..., description="Raw pasted email thread text")
    mode:   str  = Field("coaching", description="'coaching' (live deal) or 'postmortem' (closed deal)")
    userId: Optional[str] = Field(None, description="Optional user ID for ClearSignals memory")


@router.post("/analyze-thread", summary="Analyze email thread via ClearSignals")
async def analyze_thread(request: ThreadAnalysisRequest):
    """
    Proxy endpoint — forwards the pasted email thread to ClearSignals' /api/analyze
    and returns the structured analysis (intent score, signals, coaching).
    """
    if not CLEARSIGNALS_URL:
        raise HTTPException(
            status_code=503,
            detail="ClearSignals not configured. Set CLEARSIGNALS_URL in environment."
        )
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
    """Force-expire a cached TDP (simulates scanner major relevance trigger)."""
    success = await expire_tdp(request.tdp_type, request.identifier)
    if success:
        return {"status": "expired", "tdp_type": request.tdp_type, "identifier": request.identifier}
    return {"status": "not_found", "tdp_type": request.tdp_type, "identifier": request.identifier}


@router.get("/stats", summary="Cache and usage statistics")
async def cache_stats():
    """Get cache and usage statistics."""
    return await get_cache_stats()


@router.get("/health", summary="Health check")
async def health():
    """Health check endpoint."""
    return {"status": "healthy", "service": "OppIntelAI", "modules": ["prospector", "hydrator"]}
