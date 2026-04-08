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



# === Contact Intelligence Endpoints ===

from modules.hydrator.agents import contact_agent, contact_cpp_agent  # noqa: E402


class ContactLookupRequest(BaseModel):
    """On-demand contact lookup — triggered by rep clicking 'Get Contact'."""
    customer_url: str = Field(..., description="Prospect company website URL")
    title_hint: Optional[str] = Field("", description="Title hint to bias Apollo search")


class ContactCPPRequest(BaseModel):
    """On-demand CPP build — triggered by rep clicking 'Profile Contact'."""
    contact_name: str = Field(..., description="Full name from Apollo")
    contact_title: Optional[str] = Field("", description="Title from Apollo")
    company_name: Optional[str] = Field("", description="Company name")
    linkedin_url: Optional[str] = Field("", description="LinkedIn URL from Apollo if available")


@router.post("/contact/lookup", summary="Find primary contact for a prospect company via Apollo")
async def contact_lookup(request: ContactLookupRequest):
    """
    On-demand contact lookup. Called when rep clicks 'Get Contact'.
    Uses Apollo to find name, title, email, and LinkedIn URL for the
    best-fit contact at the prospect company domain.
    """
    try:
        # Build a minimal customer_tdp stub so contact_agent can extract the domain
        stub_tdp = {
            "data": {
                "website": request.customer_url,
                "company_name": "",
                "leadership": [],
            }
        }

        # If a title hint was passed, inject it as a fake leadership entry
        # so contact_agent._pick_title_hint() returns it
        if request.title_hint:
            stub_tdp["data"]["leadership"] = [
                {"name": "", "title": request.title_hint, "relevance": "user hint"}
            ]

        result = await contact_agent.run(customer_tdp=stub_tdp)
        return result

    except Exception as e:
        logger.error(f"Contact lookup error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/contact/cpp", summary="Build first-outreach CPP for a named contact via web search")
async def contact_cpp(request: ContactCPPRequest):
    """
    On-demand CPP build. Called when rep clicks 'Profile Contact'.
    Searches the web for the contact's public footprint and returns
    a 6-dimension first-outreach Communication Personality Profile.
    Takes ~15-25 seconds (web search + LLM synthesis).
    """
    try:
        result = await contact_cpp_agent.run(
            contact_name=request.contact_name,
            contact_title=request.contact_title or "",
            company_name=request.company_name or "",
            linkedin_url=request.linkedin_url or "",
        )
        return result

    except Exception as e:
        logger.error(f"Contact CPP error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/contact/cpp/view", summary="Render CPP as readable HTML card", response_class=HTMLResponse)
async def contact_cpp_view(
    name: str = "",
    title: str = "",
    company: str = "",
    linkedin_url: str = "",
):
    """
    Renders a CPP card in the browser — rep can bookmark or share.
    Calls /contact/cpp internally and returns a styled HTML page.
    Query params: name, title, company, linkedin_url
    """
    if not name:
        return HTMLResponse("<p style='font-family:sans-serif;padding:40px;color:#999'>No contact name provided.</p>")

    try:
        cpp = await contact_cpp_agent.run(
            contact_name=name,
            contact_title=title,
            company_name=company,
            linkedin_url=linkedin_url,
        )
    except Exception as e:
        logger.error(f"CPP view error: {e}")
        return HTMLResponse(f"<p style='font-family:sans-serif;padding:40px;color:#f06449'>Error: {e}</p>")

    return HTMLResponse(content=_render_cpp_html(cpp), media_type="text/html")


def _render_cpp_html(cpp: dict) -> str:
    """Render a CPP dict as a standalone dark-themed HTML card."""

    dims = cpp.get("dimensions", {})
    guidance = cpp.get("rep_guidance", {})
    confidence = cpp.get("confidence", "none")
    sources = cpp.get("sources_found", [])
    flags = cpp.get("insufficient_data_flags", [])
    sig_lang = cpp.get("signature_language", [])

    confidence_color = {
        "high": "#42d392",
        "medium": "#f0a946",
        "low": "#f06449",
        "none": "#5e6578",
    }.get(confidence, "#5e6578")

    def score_bar(score):
        if not isinstance(score, (int, float)):
            return ""
        pct = min(100, max(0, score * 10))
        color = "#42d392" if pct >= 70 else "#f0a946" if pct >= 40 else "#f06449"
        return (
            f'<div style="background:#252a35;border-radius:4px;height:6px;margin-top:6px">'
            f'<div style="background:{color};width:{pct}%;height:6px;border-radius:4px"></div></div>'
        )

    def dim_row(key, label):
        d = dims.get(key, {})
        if not d:
            return ""
        score = d.get("score", 0)
        lbl = d.get("label", "")
        just = d.get("justification", "")
        signal = d.get("signal", "")
        return (
            f'<div style="padding:14px 0;border-bottom:1px solid #252a35">'
            f'<div style="display:flex;justify-content:space-between;align-items:baseline">'
            f'<span style="font-weight:600;color:#e8eaf0;font-size:14px">{label}</span>'
            f'<span style="font-family:monospace;font-size:12px;color:#9ba3b5">{score}/10 &nbsp;·&nbsp; {lbl}</span>'
            f'</div>'
            f'{score_bar(score)}'
            f'<p style="font-size:13px;color:#9ba3b5;margin-top:8px;line-height:1.6">{just}</p>'
            + (f'<p style="font-size:12px;color:#5e6578;margin-top:4px;font-style:italic">&ldquo;{signal}&rdquo;</p>' if signal else "")
            + f'</div>'
        )

    dims_html = (
        dim_row("directness", "Directness") +
        dim_row("formality", "Formality") +
        dim_row("decision_style", "Decision Style") +
        dim_row("persuasion_receptivity", "Persuasion Receptivity") +
        dim_row("risk_tolerance", "Risk Tolerance") +
        dim_row("emotional_expressiveness", "Emotional Expressiveness")
    )

    sig_html = ""
    if sig_lang:
        chips = "".join(
            f'<span style="background:#1a1e27;border:1px solid #333a48;border-radius:20px;'
            f'padding:4px 12px;font-size:12px;color:#9ba3b5;font-family:monospace">{p}</span>'
            for p in sig_lang
        )
        sig_html = (
            f'<div style="margin-top:24px">'
            f'<div style="font-family:monospace;font-size:10px;letter-spacing:2px;'
            f'text-transform:uppercase;color:#5e6578;margin-bottom:12px">Signature Language</div>'
            f'<div style="display:flex;flex-wrap:wrap;gap:8px">{chips}</div>'
            f'</div>'
        )

    def guidance_row(label, value):
        if not value or "Unknown" in value:
            return ""
        return (
            f'<div style="padding:12px 0;border-bottom:1px solid #252a35">'
            f'<div style="font-size:11px;font-weight:600;letter-spacing:0.5px;'
            f'text-transform:uppercase;color:#5e6578;margin-bottom:6px">{label}</div>'
            f'<p style="font-size:14px;color:#e8eaf0;line-height:1.6">{value}</p>'
            f'</div>'
        )

    guidance_html = (
        guidance_row("Opening Tone", guidance.get("opening_tone", "")) +
        guidance_row("Lead With", guidance.get("what_to_lead_with", "")) +
        guidance_row("Avoid", guidance.get("what_to_avoid", "")) +
        guidance_row("Subject Line Style", guidance.get("subject_line_style", ""))
    )

    briefing = guidance.get("one_sentence_briefing", "")
    briefing_html = ""
    if briefing:
        briefing_html = (
            f'<div style="background:#14171e;border:1px solid #3ecfcf33;border-radius:10px;'
            f'padding:20px;margin-top:24px;font-size:15px;color:#e8eaf0;'
            f'font-style:italic;line-height:1.6;text-align:center">'
            f'&ldquo;{briefing}&rdquo;</div>'
        )

    flags_html = ""
    if flags:
        flag_items = "".join(f'<li style="color:#f0a946;font-size:13px;margin-bottom:4px">{f}</li>' for f in flags)
        flags_html = (
            f'<div style="margin-top:16px;padding:12px 16px;background:#1a1e27;'
            f'border:1px solid #f0a94633;border-radius:8px">'
            f'<div style="font-size:11px;font-weight:600;letter-spacing:0.5px;'
            f'text-transform:uppercase;color:#f0a946;margin-bottom:8px">Low Confidence Flags</div>'
            f'<ul style="list-style:none;padding:0">{flag_items}</ul>'
            f'</div>'
        )

    sources_str = " · ".join(sources) if sources else "No sources found"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>CPP — {cpp.get('contact_name', name)}</title>
<link href="https://fonts.googleapis.com/css2?family=Instrument+Serif:ital@0;1&family=DM+Sans:opsz,wght@9..40,400;9..40,500;9..40,600&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{background:#0c0e12;color:#e8eaf0;font-family:'DM Sans',sans-serif;min-height:100vh}}
.c{{max-width:640px;margin:0 auto;padding:48px 24px 80px}}
</style>
</head>
<body>
<div class="c">

  <!-- Header -->
  <div style="margin-bottom:32px">
    <div style="font-family:'JetBrains Mono',monospace;font-size:10px;letter-spacing:3px;
      text-transform:uppercase;color:#3ecfcf;margin-bottom:10px">First-Outreach CPP</div>
    <div style="font-family:'Instrument Serif',serif;font-size:32px;font-weight:400;
      line-height:1.2;margin-bottom:6px">{cpp.get('contact_name', name)}</div>
    <div style="font-size:14px;color:#9ba3b5">{cpp.get('title', title)}
      {"&nbsp;·&nbsp;" + cpp.get('company', company) if cpp.get('company', company) else ""}</div>
    {('<div style="font-size:13px;color:#5e6578;margin-top:4px">' + cpp.get('headline','') + '</div>') if cpp.get('headline') else ""}
    <div style="display:flex;align-items:center;gap:12px;margin-top:14px">
      <span style="background:{confidence_color}22;border:1px solid {confidence_color}44;
        border-radius:20px;padding:3px 12px;font-size:11px;font-weight:600;
        letter-spacing:0.5px;text-transform:uppercase;color:{confidence_color}">
        {confidence} confidence</span>
      <span style="font-size:12px;color:#5e6578;font-family:'JetBrains Mono',monospace">{sources_str}</span>
    </div>
  </div>

  <!-- Dimensions -->
  <div style="background:#14171e;border:1px solid #252a35;border-radius:14px;
    padding:24px;margin-bottom:20px">
    <div style="font-family:'JetBrains Mono',monospace;font-size:10px;letter-spacing:2px;
      text-transform:uppercase;color:#5e6578;margin-bottom:4px">Communication Dimensions</div>
    {dims_html if dims_html else '<p style="color:#5e6578;font-size:13px;padding-top:12px">No dimension data available.</p>'}
    {sig_html}
  </div>

  <!-- Rep Guidance -->
  <div style="background:#14171e;border:1px solid #252a35;border-radius:14px;
    padding:24px;margin-bottom:20px">
    <div style="font-family:'JetBrains Mono',monospace;font-size:10px;letter-spacing:2px;
      text-transform:uppercase;color:#5e6578;margin-bottom:4px">Rep Guidance</div>
    {guidance_html if guidance_html else '<p style="color:#5e6578;font-size:13px;padding-top:12px">No guidance available.</p>'}
    {briefing_html}
  </div>

  {flags_html}

  <div style="text-align:center;margin-top:32px;font-family:'JetBrains Mono',monospace;
    font-size:10px;letter-spacing:2px;color:#5e6578">
    Powered by <a href="https://oppintelai.com" style="color:#3ecfcf;text-decoration:none">OppIntelAI</a>
    &nbsp;·&nbsp; CPP-Light v1.0
  </div>

</div>
</body>
</html>"""


# === Contact Draft Endpoint ===

class ContactDraftRequest(BaseModel):
    """Generate a CPP-shaped email draft on demand."""
    # Contact info from Apollo
    contact_name: str = Field(..., description="Contact full name")
    contact_title: Optional[str] = Field("", description="Contact title")
    company_name: Optional[str] = Field("", description="Company name")
    contact_email: Optional[str] = Field("", description="Contact email from Apollo")

    # CPP from contact_cpp_agent (optional but shapes the email if present)
    cpp: Optional[dict] = Field(None, description="CPP dict from /contact/cpp")

    # Hydration intelligence (pass the relevant slices)
    solution_tdp: Optional[dict] = Field(None, description="solution_tdp from hydration result")
    need_analysis: Optional[dict] = Field(None, description="need_analysis from hydration result")
    customer_tdp: Optional[dict] = Field(None, description="customer_tdp from hydration result")
    conversation_toolkit: Optional[dict] = Field(None, description="conversation_toolkit from hydration result")


@router.post("/contact/draft", summary="Generate CPP-shaped email draft on demand")
async def contact_draft(request: ContactDraftRequest):
    """
    On-demand email draft. Called when rep clicks 'Draft Email'.

    Combines:
    - Contact identity (name, title, company)
    - CPP communication profile (if available) — shapes tone, formality, angle
    - Hydration intelligence (solution fit, needs, value prop, opening approach)

    Returns a subject line, body, and P.S. hook tailored to this specific person.
    """
    import json as _json

    cpp = request.cpp or {}
    dims = cpp.get("dimensions", {})
    guidance = cpp.get("rep_guidance", {})
    confidence = cpp.get("confidence", "none")

    # Build CPP instruction block
    cpp_block = ""
    if confidence not in ("none", "low", None) and dims:

        def score(k):
            return dims.get(k, {}).get("score", 5)

        def lbl(k):
            return dims.get(k, {}).get("label", "")

        lines = [f"CONTACT CPP for {request.contact_name} (confidence: {confidence}):"]

        d = score("directness")
        if d >= 7:
            lines.append("- DIRECTNESS: High. Open with the business problem in sentence one. No warm-up.")
        elif d <= 4:
            lines.append("- DIRECTNESS: Low. Brief context before the pitch. Two sentences of setup.")
        else:
            lines.append("- DIRECTNESS: Balanced. Short setup, then the point.")

        f = score("formality")
        if f >= 7:
            lines.append(f"- FORMALITY: High ({lbl('formality')}). Full sentences, professional vocabulary, no contractions.")
        elif f <= 3:
            lines.append(f"- FORMALITY: Low ({lbl('formality')}). Conversational, contractions fine, peer-to-peer.")
        else:
            lines.append(f"- FORMALITY: Professional. Standard business tone.")

        ds = lbl("decision_style")
        if "analytical" in ds:
            lines.append("- DECISION STYLE: Analytical. Include at least one specific metric or data point.")
        elif "relationship" in ds:
            lines.append("- DECISION STYLE: Relationship-driven. Lead with shared context before the value prop.")
        elif "process" in ds:
            lines.append("- DECISION STYLE: Process-driven. Be clear about what happens next and why it's low-risk.")

        ps = lbl("persuasion_receptivity")
        if "data" in ps or "roi" in ps.lower():
            lines.append("- PERSUASION: Lead with ROI or cost impact angle.")
        elif "social" in ps:
            lines.append("- PERSUASION: Reference similar companies or peer outcomes.")
        elif "narrative" in ps:
            lines.append("- PERSUASION: Tell a short story — situation → problem → resolution.")

        if guidance.get("what_to_avoid"):
            lines.append(f"- AVOID: {guidance['what_to_avoid']}")
        if guidance.get("subject_line_style"):
            lines.append(f"- SUBJECT LINE STYLE: {guidance['subject_line_style']}")
        if guidance.get("what_to_lead_with"):
            lines.append(f"- LEAD WITH: {guidance['what_to_lead_with']}")

        sig = cpp.get("signature_language", [])
        if sig:
            lines.append(f"- MIRROR THEIR LANGUAGE: Consider using: {', '.join(sig[:4])}")

        cpp_block = "\n".join(lines)
    else:
        cpp_block = f"No CPP available for {request.contact_name}. Write a professional, direct outreach email."

    # Extract intelligence from hydration result
    sol = (request.solution_tdp or {})
    need = (request.need_analysis or {})
    cust = (request.customer_tdp or {})
    toolkit = (request.conversation_toolkit or {})

    sol_name = sol.get("solution_name", "the solution")
    company = request.company_name or cust.get("company_name", "the company")
    fit_assessment = need.get("fit_assessment", "")
    value_prop = (need.get("value_proposition") or {}).get("headline", "")
    primary_needs = need.get("primary_needs", [])[:3]
    opening = toolkit.get("opening_approach", {})
    rapport_hook = opening.get("rapport_hook", "")
    existing_opening = opening.get("opening_line", "")

    needs_summary = "; ".join(n.get("need", "") for n in primary_needs if n.get("need"))

    system_prompt = """You are an elite B2B sales email writer. Your job is to write a single cold outreach email
that is specific, credible, and gets a reply.

Rules:
- Under 150 words in the body. Every sentence earns its place.
- Open with one observation about their business — not a compliment, a signal that you've done research.
- Lead with what this means for THEM, not what the product does.
- One clear, low-commitment ask. Not "let's hop on a call." Something specific and easy to say yes to.
- No: "I hope this finds you well", "I wanted to reach out", "synergy", "exciting opportunity"
- The CPP block tells you exactly how to adjust tone and angle. Follow it precisely.

Return JSON:
{
    "subject_line": "specific, benefit-oriented, under 10 words",
    "body": "full email body — plain text, no HTML, use newlines for paragraphs",
    "ps_hook": "one P.S. line — a provocative question or insight that makes them think"
}"""

    user_prompt = f"""Write a cold outreach email for this situation.

RECIPIENT: {request.contact_name}, {request.contact_title or 'unknown title'} at {company}

CPP INSTRUCTIONS (follow these precisely):
{cpp_block}

SOLUTION: {sol_name}
{f'Value proposition: {value_prop}' if value_prop else ''}
{f'Fit summary: {fit_assessment}' if fit_assessment else ''}
{f'Top needs: {needs_summary}' if needs_summary else ''}
{f'Rapport hook: {rapport_hook}' if rapport_hook else ''}
{f'Suggested opening: {existing_opening}' if existing_opening else ''}

Write the email. Subject line, body, P.S. Return only valid JSON."""

    try:
        from core.llm import call_llm_json
        result = await call_llm_json(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            max_tokens=1024,
            temperature=0.4,
        )
        draft = result.get("parsed", {})
        return {
            "contact_name": request.contact_name,
            "contact_title": request.contact_title,
            "company_name": company,
            "contact_email": request.contact_email,
            "cpp_confidence": confidence,
            "cpp_applied": confidence not in ("none", "low", None),
            "draft": draft,
        }
    except Exception as e:
        logger.error(f"Contact draft error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
