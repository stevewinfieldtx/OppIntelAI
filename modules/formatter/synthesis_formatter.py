"""
Synthesis Formatter — Agent 7
Converts OppIntelAI prospector/hydrator JSON output into a
self-contained, sales-rep-ready HTML intelligence report.

Matches the look and feel of the Ideal_Leads_Hydrated.html spec:
  - Priority-scored prospect cards
  - Who Is This narrative (amber box)
  - Lead module + contact title (blue box)
  - Pain tags
  - Opening question
  - Discovery Questions modal
  - ClearSignals Thread Analysis modal (paste-and-analyze)
"""
import json
import logging
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# Normalizers — OppIntelAI JSON → card format
# ─────────────────────────────────────────────

def _priority_class(score: int) -> str:
    if score >= 90:
        return "high"
    if score >= 80:
        return "medium"
    return "standard"


def _stage_label(stage: str) -> str:
    return {
        "opener": "Opening",
        "deepener": "Discovery",
        "quantifier": "Quantification",
        "vision": "Vision",
    }.get(stage, "Discovery")


def normalize_prospect(entry: dict, idx: int = 1) -> dict:
    """
    Map a single hydrated prospect entry to the card data format.
    Works for both prospector entries (which have a 'prospect' wrapper)
    and bare hydrator results.
    """
    prospect      = entry.get("prospect", entry)
    need_analysis = entry.get("need_analysis", {})
    toolkit       = entry.get("conversation_toolkit", {})
    customer_tdp  = entry.get("customer_tdp", {})

    # ── Priority ──
    priority = int(prospect.get("priority", need_analysis.get("fit_score", 70)) or 70)
    priority = max(0, min(100, priority))

    # ── Who Is This ──
    who_is_this = (
        prospect.get("whoIsThis")
        or need_analysis.get("fit_assessment")
        or customer_tdp.get("elevator_pitch")
        or "Intelligence narrative unavailable."
    )

    # ── Questions ──
    questions = []
    for dq in toolkit.get("discovery_questions", []):
        pos = dq.get("expected_answer_positive", {}) or {}
        neg = dq.get("expected_answer_negative", {}) or {}
        unx = dq.get("expected_answer_unexpected", {}) or {}
        questions.append({
            "stage":    _stage_label(dq.get("stage", "opener")),
            "question": dq.get("question", ""),
            "purpose":  dq.get("purpose", ""),
            "pain_point": dq.get("pain_it_targets", ""),
            "positive_responses": [{
                "response":  pos.get("answer", ""),
                "next_step": pos.get("follow_up", ""),
            }],
            "negative_responses": [{
                "response": neg.get("answer", ""),
                "pivot":    neg.get("pivot", ""),
            }],
            "objection_handling": unx.get("recovery", ""),
        })

    # ── Pain Tags ──
    pain_tags = list(prospect.get("painTags") or [])
    if not pain_tags:
        for need in (need_analysis.get("primary_needs") or [])[:5]:
            tag = need.get("need", "")
            if tag:
                pain_tags.append(tag)
    pain_tags = pain_tags[:5]

    # ── Opening Question ──
    opening_line = (toolkit.get("opening_approach") or {}).get("opening_line", "")
    if not opening_line and questions:
        opening_line = questions[0]["question"]

    # ── Lead Module ──
    lead_module = (
        prospect.get("leadModule")
        or (need_analysis.get("value_proposition") or {}).get("headline")
        or ""
    )

    # ── Location / Landmark ──
    location = prospect.get("location", "")
    if not location and customer_tdp.get("headquarters"):
        location = customer_tdp["headquarters"]

    # ── Timing Signals ──
    timing_signals = [
        s.get("signal", "") for s in (need_analysis.get("timing_signals") or [])
        if s.get("signal")
    ]

    # ── Email Draft ──
    email_draft = toolkit.get("email_draft") or {}

    # ── Objection Playbook ──
    objections = toolkit.get("objection_playbook") or []

    return {
        "id":            idx,
        "name":          prospect.get("name", "Unknown Company"),
        "location":      location,
        "landmark":      prospect.get("landmark", ""),
        "metro":         prospect.get("metro", ""),
        "employees":     prospect.get("employees", ""),
        "phone":         prospect.get("phone", ""),
        "website":       prospect.get("website", ""),
        "priority":      priority,
        "priorityClass": _priority_class(priority),
        "whoIsThis":     who_is_this,
        "contactTitle":  prospect.get("contactTitle", ""),
        "leadModule":    lead_module,
        "painTags":      pain_tags,
        "openingQuestion": opening_line,
        "questions":     questions,
        "emailDraft":    email_draft,
        "objections":    objections,
        "timingSignals": timing_signals,
    }


def normalize_prospector_output(result: dict) -> tuple[list[dict], dict]:
    """
    Convert prospector module output → (cards, meta).
    meta is used for the report header and stats bar.
    """
    meta = result.get("meta", {})
    sol  = result.get("solution_tdp", {})
    cards = []
    for i, entry in enumerate(result.get("prospects", []), start=1):
        try:
            cards.append(normalize_prospect(entry, idx=i))
        except Exception as e:
            logger.warning(f"Failed to normalize prospect {i}: {e}")

    report_meta = {
        "solution_name":  sol.get("solution_name") or meta.get("solution", "Intelligence Report"),
        "vendor":         sol.get("vendor", ""),
        "category":       sol.get("category", ""),
        "metro":          meta.get("selected_metro", ""),
        "vertical":       meta.get("selected_vertical", ""),
        "total":          len(cards),
        "high_priority":  sum(1 for c in cards if c["priorityClass"] == "high"),
        "generated_at":   datetime.utcnow().strftime("%B %d, %Y"),
        "module":         "prospector",
    }
    return cards, report_meta


def normalize_hydrator_output(result: dict) -> tuple[list[dict], dict]:
    """
    Convert hydrator module output (single lead) → (cards, meta).
    Returns a list with one card for uniform rendering.
    """
    meta = result.get("meta", {})
    sol  = result.get("solution_tdp", {})

    card = normalize_prospect(result, idx=1)
    cards = [card]

    report_meta = {
        "solution_name": sol.get("solution_name") or meta.get("solution", "Intelligence Report"),
        "vendor":        sol.get("vendor", ""),
        "category":      sol.get("category", ""),
        "metro":         card.get("metro", ""),
        "vertical":      meta.get("industry", ""),
        "total":         1,
        "high_priority": 1 if card["priorityClass"] == "high" else 0,
        "generated_at":  datetime.utcnow().strftime("%B %d, %Y"),
        "module":        "hydrator",
    }
    return cards, report_meta


# ─────────────────────────────────────────────
# HTML Generator
# ─────────────────────────────────────────────

def generate_html(
    cards: list[dict],
    meta: dict,
    oppintelai_base_url: str = "",
    clearsignals_url: str = "",
) -> str:
    """
    Render a self-contained HTML intelligence report from normalized card data.

    Args:
        cards:               Normalized list of prospect card dicts
        meta:                Report metadata (solution name, metro, etc.)
        oppintelai_base_url: OppIntelAI server URL for API calls (embedded in JS)
        clearsignals_url:    ClearSignals server URL for thread analysis proxy

    Returns:
        Complete HTML string — single file, no external dependencies
    """
    prospects_json = json.dumps(cards, ensure_ascii=False, indent=2)
    solution_name  = meta.get("solution_name", "Intelligence Report")
    vendor         = meta.get("vendor", "")
    metro          = meta.get("metro", "")
    vertical       = meta.get("vertical", "")
    total          = meta.get("total", len(cards))
    high_priority  = meta.get("high_priority", 0)
    generated_at   = meta.get("generated_at", "")
    module_label   = "Prospector Report" if meta.get("module") == "prospector" else "Hydrated Lead"

    # Unique metros for filter dropdown
    metros = sorted(set(c.get("metro", "") for c in cards if c.get("metro")))
    metro_options = "\n".join(
        f'<option value="{m}">{m}</option>' for m in metros
    )

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{solution_name} — OppIntelAI {module_label}</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}

        :root {{
            --sap-blue: #0070D2;
            --sap-dark: #00396C;
            --sap-light: #E8F4FD;
            --ms-gray: #605E5C;
            --ms-light-gray: #F3F2F1;
            --border-gray: #E1DFDD;
            --text-primary: #323130;
            --text-secondary: #605E5C;
            --white: #FFFFFF;
            --success: #107C10;
            --warning: #FFC107;
            --danger: #A80000;
            --info: #0078D4;
            --purple: #8764B8;
            --teal: #038387;
            --amber: #FFF4CE;
            --amber-border: #FFD335;
            --blue-highlight: #EFF6FF;
            --blue-highlight-border: #0070D2;
            --shadow-sm: 0 1px 2px rgba(0,0,0,0.08);
            --shadow-md: 0 2px 4px rgba(0,0,0,0.08);
            --shadow-lg: 0 4px 8px rgba(0,0,0,0.1);
            --shadow-xl: 0 8px 16px rgba(0,0,0,0.15);
        }}

        body {{
            font-family: "Segoe UI", "Helvetica Neue", Helvetica, Arial, sans-serif;
            background-color: var(--ms-light-gray);
            color: var(--text-primary);
            line-height: 1.5;
            min-height: 100vh;
        }}

        /* ── Header ── */
        .header {{ background: var(--white); border-bottom: 1px solid var(--border-gray); box-shadow: var(--shadow-sm); }}
        .header-top {{ background: var(--sap-blue); padding: 16px 32px; display: flex; justify-content: space-between; align-items: center; }}
        .logo-section {{ display: flex; align-items: center; gap: 16px; }}
        .logo-icon {{ width: 40px; height: 40px; background: var(--white); border-radius: 2px; display: flex; align-items: center; justify-content: center; font-weight: bold; color: var(--sap-blue); font-size: 18px; }}
        .logo-text {{ color: var(--white); }}
        .logo-title {{ font-size: 20px; font-weight: 600; letter-spacing: -0.3px; }}
        .logo-subtitle {{ font-size: 13px; opacity: 0.9; font-weight: 400; }}
        .badge-generated {{ color: var(--white); font-size: 12px; opacity: 0.8; }}

        /* ── Stats Bar ── */
        .header-stats {{ display: flex; padding: 0 32px; background: var(--white); border-bottom: 1px solid var(--border-gray); }}
        .stat-item {{ padding: 16px 24px; border-right: 1px solid var(--border-gray); min-width: 140px; }}
        .stat-item:last-child {{ border-right: none; }}
        .stat-label {{ font-size: 12px; color: var(--text-secondary); text-transform: uppercase; letter-spacing: 0.5px; font-weight: 600; margin-bottom: 4px; }}
        .stat-value {{ font-size: 24px; font-weight: 700; color: var(--text-primary); }}
        .stat-value.highlight {{ color: var(--sap-blue); }}

        /* ── Command Bar ── */
        .command-bar {{ background: var(--white); padding: 16px 32px; display: flex; justify-content: space-between; align-items: center; gap: 24px; border-bottom: 1px solid var(--border-gray); }}
        .filter-section {{ display: flex; gap: 12px; align-items: flex-end; flex: 1; }}
        .filter-group {{ display: flex; flex-direction: column; gap: 4px; }}
        .filter-label {{ font-size: 12px; color: var(--text-secondary); font-weight: 600; }}
        select, input[type="text"] {{ padding: 8px 12px; border: 1px solid var(--border-gray); border-radius: 2px; font-size: 14px; background: var(--white); color: var(--text-primary); min-width: 160px; font-family: inherit; }}
        select:focus, input:focus {{ outline: none; border-color: var(--sap-blue); }}
        .results-indicator {{ font-size: 14px; color: var(--text-secondary); white-space: nowrap; }}
        .results-indicator strong {{ color: var(--text-primary); }}

        /* ── Buttons ── */
        .btn {{ padding: 8px 16px; border: 1px solid transparent; border-radius: 2px; font-size: 14px; font-weight: 600; cursor: pointer; transition: all 0.15s; font-family: inherit; display: inline-flex; align-items: center; gap: 6px; }}
        .btn-primary {{ background: var(--sap-blue); color: var(--white); border-color: var(--sap-blue); }}
        .btn-primary:hover {{ background: var(--sap-dark); border-color: var(--sap-dark); }}
        .btn-secondary {{ background: var(--white); color: var(--text-primary); border-color: var(--border-gray); }}
        .btn-secondary:hover {{ background: var(--ms-light-gray); }}
        .btn-research {{ background: var(--purple); color: var(--white); border-color: var(--purple); font-size: 13px; }}
        .btn-research:hover {{ background: #6B4B9C; }}
        .btn-email {{ background: var(--info); color: var(--white); border-color: var(--info); font-size: 13px; }}
        .btn-email:hover {{ background: #005a9e; }}
        .btn-clearsignals {{ background: var(--teal); color: var(--white); border-color: var(--teal); font-size: 13px; }}
        .btn-clearsignals:hover {{ background: #026a6d; }}

        /* ── Content Grid ── */
        .content {{ padding: 24px 32px; }}
        .prospect-grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(480px, 1fr)); gap: 20px; }}

        /* ── Prospect Card ── */
        .prospect-card {{ background: var(--white); border: 1px solid var(--border-gray); border-radius: 2px; box-shadow: var(--shadow-sm); transition: box-shadow 0.15s; overflow: hidden; }}
        .prospect-card:hover {{ box-shadow: var(--shadow-lg); }}
        .card-header {{ padding: 20px 24px; border-bottom: 1px solid var(--border-gray); display: flex; justify-content: space-between; align-items: flex-start; }}
        .company-info h2 {{ font-size: 18px; font-weight: 600; color: var(--text-primary); margin-bottom: 8px; letter-spacing: -0.2px; }}
        .location-tag {{ display: inline-flex; align-items: center; gap: 6px; font-size: 13px; color: var(--text-secondary); background: var(--ms-light-gray); padding: 3px 10px; border-radius: 12px; }}
        .landmark-tag {{ display: inline-flex; align-items: center; gap: 6px; font-size: 12px; color: var(--sap-blue); background: var(--sap-light); padding: 3px 10px; border-radius: 12px; margin-left: 6px; }}
        .priority-badge {{ display: inline-flex; align-items: center; gap: 6px; padding: 6px 14px; border-radius: 2px; font-size: 13px; font-weight: 700; white-space: nowrap; }}
        .priority-high {{ background: #FFF4CE; color: #835B00; border: 1px solid #FFD335; }}
        .priority-medium {{ background: #E8F4FD; color: #0070D2; border: 1px solid #0070D2; }}
        .priority-standard {{ background: #F3F2F1; color: #605E5C; border: 1px solid #E1DFDD; }}
        .card-meta {{ padding: 12px 24px; background: var(--ms-light-gray); border-bottom: 1px solid var(--border-gray); display: flex; gap: 24px; font-size: 13px; color: var(--text-secondary); }}
        .meta-item {{ display: flex; align-items: center; gap: 6px; }}

        /* ── Card Body ── */
        .card-body {{ padding: 20px 24px; }}
        .narrative-box {{ background: var(--amber); border: 1px solid var(--amber-border); border-radius: 2px; padding: 14px 16px; margin-bottom: 16px; }}
        .narrative-label {{ font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.5px; color: #835B00; margin-bottom: 6px; }}
        .narrative-text {{ font-size: 14px; line-height: 1.6; color: var(--text-primary); }}
        .module-box {{ background: var(--blue-highlight); border: 1px solid var(--blue-highlight-border); border-radius: 2px; padding: 12px 16px; margin-bottom: 16px; display: flex; justify-content: space-between; align-items: flex-start; gap: 12px; }}
        .module-label {{ font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.5px; color: var(--sap-blue); margin-bottom: 4px; }}
        .module-text {{ font-size: 14px; font-weight: 600; color: var(--sap-dark); }}
        .contact-title {{ font-size: 13px; color: var(--text-secondary); text-align: right; }}
        .contact-title strong {{ display: block; font-size: 14px; color: var(--text-primary); }}
        .pain-tags {{ display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 16px; }}
        .pain-tag {{ background: var(--ms-light-gray); color: var(--text-secondary); border: 1px solid var(--border-gray); border-radius: 12px; padding: 4px 12px; font-size: 12px; font-weight: 500; }}
        .opening-question {{ font-style: italic; font-size: 14px; color: var(--text-primary); border-left: 3px solid var(--sap-blue); padding: 10px 14px; background: var(--sap-light); margin-bottom: 16px; line-height: 1.5; }}
        .card-actions {{ display: flex; gap: 8px; flex-wrap: wrap; padding-top: 4px; }}
        .card-hidden {{ display: none; }}

        /* ── Modal ── */
        .modal-overlay {{ display: none; position: fixed; inset: 0; background: rgba(0,0,0,0.5); z-index: 1000; justify-content: center; align-items: flex-start; padding-top: 40px; overflow-y: auto; }}
        .modal-overlay.open {{ display: flex; }}
        .modal-content {{ background: var(--white); border-radius: 4px; max-width: 950px; width: 90%; max-height: 90vh; overflow-y: auto; box-shadow: var(--shadow-xl); border: 1px solid var(--border-gray); }}
        .modal-header {{ background: var(--sap-blue); color: var(--white); padding: 16px 24px; display: flex; justify-content: space-between; align-items: center; position: sticky; top: 0; z-index: 10; }}
        .modal-header.teal {{ background: var(--teal); }}
        .modal-title {{ font-size: 18px; font-weight: 600; }}
        .modal-close {{ background: none; border: none; color: var(--white); font-size: 24px; cursor: pointer; width: 32px; height: 32px; display: flex; align-items: center; justify-content: center; border-radius: 2px; }}
        .modal-close:hover {{ background: rgba(255,255,255,0.1); }}
        .modal-body {{ padding: 24px; }}

        /* ── Discovery Question Cards ── */
        .question-card {{ border: 1px solid var(--border-gray); border-radius: 4px; margin-bottom: 20px; overflow: hidden; }}
        .question-card-header {{ padding: 16px 20px; background: linear-gradient(135deg, var(--sap-light) 0%, #f0f8ff 100%); border-bottom: 1px solid var(--border-gray); cursor: pointer; display: flex; justify-content: space-between; align-items: flex-start; transition: background 0.15s; }}
        .question-card-header:hover {{ background: linear-gradient(135deg, #d4ebff 0%, #e6f3ff 100%); }}
        .question-stage {{ font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.5px; color: var(--sap-blue); margin-bottom: 8px; }}
        .question-text {{ font-size: 16px; font-weight: 600; color: var(--text-primary); flex: 1; padding-right: 16px; line-height: 1.5; }}
        .question-toggle {{ color: var(--sap-blue); font-size: 20px; flex-shrink: 0; }}
        .question-details {{ display: none; padding: 20px; background: var(--white); }}
        .question-details.open {{ display: block; }}
        .detail-section {{ margin-bottom: 16px; }}
        .detail-label {{ font-size: 12px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.5px; color: var(--text-secondary); margin-bottom: 8px; }}
        .response-item {{ background: var(--ms-light-gray); border-radius: 2px; padding: 12px 14px; margin-bottom: 8px; font-size: 14px; }}
        .response-item.positive {{ border-left: 3px solid var(--success); }}
        .response-item.negative {{ border-left: 3px solid var(--danger); }}
        .response-label {{ font-size: 11px; font-weight: 700; text-transform: uppercase; color: var(--text-secondary); margin-bottom: 4px; }}
        .response-action {{ font-size: 13px; color: var(--sap-blue); margin-top: 6px; font-style: italic; }}

        /* ── ClearSignals Modal ── */
        .cs-paste-area {{ width: 100%; height: 200px; padding: 12px; border: 1px solid var(--border-gray); border-radius: 2px; font-family: inherit; font-size: 13px; resize: vertical; background: var(--ms-light-gray); color: var(--text-primary); }}
        .cs-paste-area:focus {{ outline: none; border-color: var(--teal); background: var(--white); }}
        .cs-mode-select {{ display: flex; gap: 8px; margin: 12px 0; }}
        .cs-mode-btn {{ flex: 1; padding: 10px; border: 2px solid var(--border-gray); border-radius: 2px; background: var(--white); cursor: pointer; font-family: inherit; font-size: 13px; font-weight: 600; color: var(--text-secondary); transition: all 0.15s; }}
        .cs-mode-btn.active {{ border-color: var(--teal); color: var(--teal); background: #e6f7f7; }}
        .cs-result {{ display: none; margin-top: 16px; }}
        .cs-result.visible {{ display: block; }}
        .cs-score-bar {{ background: #0B1929; border-radius: 6px; padding: 16px; display: flex; gap: 32px; margin-bottom: 16px; flex-wrap: wrap; }}
        .cs-score {{ text-align: center; min-width: 80px; }}
        .cs-score-val {{ font-size: 28px; font-weight: 700; font-family: Georgia, serif; color: #fff; }}
        .cs-score-val.red {{ color: #ff6b6b; }}
        .cs-score-val.green {{ color: #4ecdc4; }}
        .cs-score-lbl {{ font-size: 10px; color: #8A9BAD; text-transform: uppercase; letter-spacing: 0.1em; }}
        .cs-signal {{ display: inline-block; padding: 2px 8px; border-radius: 3px; font-size: 11px; font-weight: 600; margin: 2px; }}
        .cs-signal.red {{ background: #fde8e8; color: #C0392B; }}
        .cs-signal.yellow {{ background: #fef6e0; color: #E8A020; }}
        .cs-signal.green {{ background: #e6f5ed; color: #1A7A4A; }}
        .cs-box {{ background: var(--white); border: 1px solid var(--border-gray); border-radius: 4px; padding: 12px 14px; margin-bottom: 10px; }}
        .cs-box-label {{ font-size: 10px; font-weight: 700; letter-spacing: 0.1em; text-transform: uppercase; margin-bottom: 6px; color: var(--teal); }}
        .cs-box-text {{ font-size: 14px; color: var(--text-primary); line-height: 1.6; }}
        .cs-loading {{ text-align: center; padding: 24px; color: var(--text-secondary); font-style: italic; }}
        .cs-error {{ background: #fde8e8; border: 1px solid #C0392B; border-radius: 2px; padding: 12px; color: #C0392B; font-size: 14px; margin-top: 12px; }}

        @media (max-width: 768px) {{
            .prospect-grid {{ grid-template-columns: 1fr; }}
            .content {{ padding: 16px; }}
            .header-top {{ padding: 12px 16px; }}
            .header-stats {{ overflow-x: auto; }}
            .command-bar {{ flex-direction: column; padding: 12px 16px; }}
        }}
    </style>
</head>
<body>

<!-- ══════════════════════════════════════════════
     HEADER
══════════════════════════════════════════════ -->
<div class="header">
    <div class="header-top">
        <div class="logo-section">
            <div class="logo-icon">OI</div>
            <div class="logo-text">
                <div class="logo-title">OppIntelAI — {solution_name}</div>
                <div class="logo-subtitle">
                    {module_label}
                    {(' · ' + vertical) if vertical else ''}
                    {(' · ' + metro) if metro else ''}
                </div>
            </div>
        </div>
        <div class="badge-generated">Generated {generated_at}</div>
    </div>

    <div class="header-stats">
        <div class="stat-item">
            <div class="stat-label">Total Prospects</div>
            <div class="stat-value highlight" id="stat-total">{total}</div>
        </div>
        <div class="stat-item">
            <div class="stat-label">High Priority</div>
            <div class="stat-value" id="stat-high">{high_priority}</div>
        </div>
        <div class="stat-item">
            <div class="stat-label">Solution</div>
            <div class="stat-value" style="font-size:16px">{solution_name}</div>
        </div>
        {f'<div class="stat-item"><div class="stat-label">Metro</div><div class="stat-value" style="font-size:16px">{metro}</div></div>' if metro else ''}
    </div>
</div>

<!-- ══════════════════════════════════════════════
     COMMAND BAR / FILTERS
══════════════════════════════════════════════ -->
<div class="command-bar">
    <div class="filter-section">
        <div class="filter-group">
            <div class="filter-label">Search</div>
            <input type="text" id="filter-search" placeholder="Company name, pain tag…" oninput="applyFilters()">
        </div>
        {'''
        <div class="filter-group">
            <div class="filter-label">Metro</div>
            <select id="filter-metro" onchange="applyFilters()">
                <option value="">All Metros</option>
                ''' + metro_options + '''
            </select>
        </div>''' if metros else ''}
        <div class="filter-group">
            <div class="filter-label">Min Priority</div>
            <select id="filter-priority" onchange="applyFilters()">
                <option value="0">All</option>
                <option value="90">90+ (High)</option>
                <option value="80">80+ (Medium)</option>
                <option value="70">70+</option>
            </select>
        </div>
    </div>
    <div class="results-indicator"><strong id="results-count">{total}</strong> prospects shown</div>
</div>

<!-- ══════════════════════════════════════════════
     PROSPECT GRID
══════════════════════════════════════════════ -->
<div class="content">
    <div class="prospect-grid" id="prospect-grid">
        <!-- Cards rendered by JavaScript -->
    </div>
</div>

<!-- ══════════════════════════════════════════════
     DISCOVERY QUESTIONS MODAL
══════════════════════════════════════════════ -->
<div class="modal-overlay" id="questions-modal">
    <div class="modal-content">
        <div class="modal-header">
            <div class="modal-title" id="modal-company-name">Discovery Toolkit</div>
            <button class="modal-close" onclick="closeModal('questions-modal')">×</button>
        </div>
        <div class="modal-body" id="modal-questions-body">
            <!-- Populated by JS -->
        </div>
    </div>
</div>

<!-- ══════════════════════════════════════════════
     CLEARSIGNALS THREAD ANALYSIS MODAL
══════════════════════════════════════════════ -->
<div class="modal-overlay" id="cs-modal">
    <div class="modal-content">
        <div class="modal-header teal">
            <div>
                <div class="modal-title" id="cs-modal-title">ClearSignals — Thread Analysis</div>
                <div style="font-size:13px;opacity:0.85;margin-top:2px" id="cs-modal-company"></div>
            </div>
            <button class="modal-close" onclick="closeModal('cs-modal')">×</button>
        </div>
        <div class="modal-body">
            <p style="font-size:14px;color:#605E5C;margin-bottom:12px">
                Paste your email thread below to get buyer intent scoring, signal detection, and coaching.
            </p>
            <textarea class="cs-paste-area" id="cs-thread-input"
                placeholder="Paste your email thread here — oldest message first is ideal, but any order works…"></textarea>

            <div class="cs-mode-select">
                <button class="cs-mode-btn active" id="mode-coaching" onclick="setMode('coaching')">
                    🎯 Coaching Mode — Deal in Progress
                </button>
                <button class="cs-mode-btn" id="mode-postmortem" onclick="setMode('postmortem')">
                    📋 Postmortem Mode — Deal Closed
                </button>
            </div>

            <button class="btn btn-clearsignals" style="width:100%;justify-content:center;padding:12px"
                onclick="analyzeThread()" id="cs-analyze-btn">
                Analyze Thread
            </button>

            <div id="cs-result" class="cs-result">
                <!-- Populated by JS after analysis -->
            </div>
        </div>
    </div>
</div>

<!-- ══════════════════════════════════════════════
     JAVASCRIPT
══════════════════════════════════════════════ -->
<script>
    // ── Data ──
    const prospects = {prospects_json};
    const OPPINTELAI_URL = "{oppintelai_base_url}";
    const CLEARSIGNALS_URL = "{clearsignals_url}";
    let csMode = 'coaching';
    let activeProspect = null;

    // ── Render cards on load ──
    function renderCards(list) {{
        const grid = document.getElementById('prospect-grid');
        grid.innerHTML = '';
        list.forEach(p => grid.appendChild(buildCard(p)));
        document.getElementById('results-count').textContent = list.length;
    }}

    function buildCard(p) {{
        const div = document.createElement('div');
        div.className = 'prospect-card';
        div.dataset.id = p.id;
        div.dataset.metro = p.metro || '';
        div.dataset.priority = p.priority;

        const pClass = p.priorityClass === 'high' ? 'priority-high'
                     : p.priorityClass === 'medium' ? 'priority-medium' : 'priority-standard';
        const pLabel = p.priorityClass === 'high' ? '★ HIGH PRIORITY'
                     : p.priorityClass === 'medium' ? 'MEDIUM' : 'STANDARD';

        const painHtml = (p.painTags || []).map(t =>
            `<span class="pain-tag">${{t}}</span>`).join('');

        const locationHtml = p.location
            ? `<span class="location-tag">📍 ${{p.location}}</span>` : '';
        const landmarkHtml = p.landmark
            ? `<span class="landmark-tag">🏢 ${{p.landmark}}</span>` : '';
        const employeeHtml = p.employees ? `<span class="meta-item">👥 ${{p.employees}}</span>` : '';
        const phoneHtml    = p.phone     ? `<span class="meta-item">📞 ${{p.phone}}</span>` : '';

        div.innerHTML = `
            <div class="card-header">
                <div class="company-info">
                    <h2>${{p.name}}</h2>
                    <div>${{locationHtml}}${{landmarkHtml}}</div>
                </div>
                <div class="priority-badge ${{pClass}}">${{p.priority}} ${{pLabel}}</div>
            </div>
            ${{(employeeHtml || phoneHtml) ? `<div class="card-meta">${{employeeHtml}}${{phoneHtml}}</div>` : ''}}
            <div class="card-body">
                <div class="narrative-box">
                    <div class="narrative-label">Who Is This</div>
                    <div class="narrative-text">${{p.whoIsThis || ''}}</div>
                </div>
                ${{p.leadModule ? `
                <div class="module-box">
                    <div>
                        <div class="module-label">Lead Module</div>
                        <div class="module-text">${{p.leadModule}}</div>
                    </div>
                    ${{p.contactTitle ? `<div class="contact-title"><div class="module-label">Contact</div><strong>${{p.contactTitle}}</strong></div>` : ''}}
                </div>` : ''}}
                ${{painHtml ? `<div class="pain-tags">${{painHtml}}</div>` : ''}}
                ${{p.openingQuestion ? `<div class="opening-question">"${{p.openingQuestion}}"</div>` : ''}}
                <div class="card-actions">
                    ${{(p.questions && p.questions.length) ? `<button class="btn btn-research" onclick="openDiscovery(${{p.id}})">🔍 Discovery Questions (${{p.questions.length}})</button>` : ''}}
                    ${{p.emailDraft && p.emailDraft.subject_line ? `<button class="btn btn-email" onclick="openEmail(${{p.id}})">✉️ Draft Email</button>` : ''}}
                    <button class="btn btn-clearsignals" onclick="openClearSignals(${{p.id}})">📡 Analyze Thread</button>
                </div>
            </div>`;
        return div;
    }}

    // ── Discovery Modal ──
    function openDiscovery(id) {{
        const p = prospects.find(x => x.id === id);
        if (!p) return;
        document.getElementById('modal-company-name').textContent = p.name + ' — Discovery Toolkit';
        const body = document.getElementById('modal-questions-body');
        body.innerHTML = (p.questions || []).map((q, i) => `
            <div class="question-card">
                <div class="question-card-header" onclick="toggleQuestion(this)">
                    <div>
                        <div class="question-stage">${{q.stage}}</div>
                        <div class="question-text">${{q.question}}</div>
                        ${{q.pain_point ? `<div style="font-size:12px;color:#605E5C;margin-top:6px">Pain: ${{q.pain_point}}</div>` : ''}}
                    </div>
                    <div class="question-toggle">+</div>
                </div>
                <div class="question-details">
                    ${{q.purpose ? `<div class="detail-section"><div class="detail-label">Why Ask This</div><div style="font-size:14px">${{q.purpose}}</div></div>` : ''}}
                    ${{(q.positive_responses || []).filter(r => r.response).map(r => `
                    <div class="response-item positive">
                        <div class="response-label">✓ Good Response</div>
                        <div>${{r.response}}</div>
                        ${{r.next_step ? `<div class="response-action">→ ${{r.next_step}}</div>` : ''}}
                    </div>`).join('')}}
                    ${{(q.negative_responses || []).filter(r => r.response).map(r => `
                    <div class="response-item negative">
                        <div class="response-label">✗ Deflection / Pushback</div>
                        <div>${{r.response}}</div>
                        ${{r.pivot ? `<div class="response-action">↩ ${{r.pivot}}</div>` : ''}}
                    </div>`).join('')}}
                    ${{q.objection_handling ? `<div class="detail-section" style="margin-top:12px"><div class="detail-label">If They Surprise You</div><div style="font-size:14px">${{q.objection_handling}}</div></div>` : ''}}
                </div>
            </div>`).join('');
        openModal('questions-modal');
    }}

    function toggleQuestion(header) {{
        const details = header.nextElementSibling;
        const toggle  = header.querySelector('.question-toggle');
        const isOpen  = details.classList.contains('open');
        details.classList.toggle('open', !isOpen);
        toggle.textContent = isOpen ? '+' : '−';
    }}

    // ── Email Draft Modal (re-uses questions modal) ──
    function openEmail(id) {{
        const p = prospects.find(x => x.id === id);
        if (!p || !p.emailDraft) return;
        const e = p.emailDraft;
        document.getElementById('modal-company-name').textContent = p.name + ' — Draft Email';
        document.getElementById('modal-questions-body').innerHTML = `
            <div style="margin-bottom:16px">
                <div class="detail-label">Subject Line</div>
                <div style="font-size:16px;font-weight:600;margin-top:6px">${{e.subject_line || ''}}</div>
            </div>
            <div style="margin-bottom:16px">
                <div class="detail-label">Body</div>
                <div style="font-size:14px;line-height:1.7;white-space:pre-wrap;margin-top:6px">${{e.body || ''}}</div>
            </div>
            ${{e.ps_hook ? `<div><div class="detail-label">P.S.</div><div style="font-size:14px;font-style:italic;margin-top:6px">${{e.ps_hook}}</div></div>` : ''}}`;
        openModal('questions-modal');
    }}

    // ── ClearSignals Modal ──
    function openClearSignals(id) {{
        activeProspect = prospects.find(x => x.id === id);
        if (!activeProspect) return;
        document.getElementById('cs-modal-title').textContent = 'ClearSignals — Thread Analysis';
        document.getElementById('cs-modal-company').textContent = activeProspect.name;
        document.getElementById('cs-thread-input').value = '';
        document.getElementById('cs-result').className = 'cs-result';
        document.getElementById('cs-result').innerHTML = '';
        openModal('cs-modal');
    }}

    function setMode(mode) {{
        csMode = mode;
        document.getElementById('mode-coaching').classList.toggle('active', mode === 'coaching');
        document.getElementById('mode-postmortem').classList.toggle('active', mode === 'postmortem');
    }}

    async function analyzeThread() {{
        const thread = document.getElementById('cs-thread-input').value.trim();
        if (!thread) {{
            alert('Please paste an email thread first.');
            return;
        }}
        const resultEl = document.getElementById('cs-result');
        const btn = document.getElementById('cs-analyze-btn');
        resultEl.className = 'cs-result visible';
        resultEl.innerHTML = '<div class="cs-loading">Analyzing thread… this takes 10–20 seconds</div>';
        btn.disabled = true;
        btn.textContent = 'Analyzing…';

        try {{
            // Call OppIntelAI proxy → ClearSignals
            const endpoint = OPPINTELAI_URL
                ? OPPINTELAI_URL.replace(/\\/$/, '') + '/api/v1/analyze-thread'
                : '/api/v1/analyze-thread';

            const resp = await fetch(endpoint, {{
                method: 'POST',
                headers: {{ 'Content-Type': 'application/json' }},
                body: JSON.stringify({{ thread, mode: csMode }})
            }});

            if (!resp.ok) throw new Error('Server error: ' + resp.status);
            const data = await resp.json();
            renderClearSignalsResult(data);
        }} catch(err) {{
            resultEl.innerHTML = `<div class="cs-error">Analysis failed: ${{err.message}}<br>Make sure OppIntelAI is running and ClearSignals is connected.</div>`;
        }} finally {{
            btn.disabled = false;
            btn.textContent = 'Analyze Thread';
        }}
    }}

    function renderClearSignalsResult(data) {{
        const final = data.final || {{}};
        const signals = final.signals || [];
        const ryg = final.ryg || {{}};

        const redSignals   = signals.filter(s => s.severity === 'red').length   + (ryg.r || 0);
        const yellowSignals= signals.filter(s => s.severity === 'yellow').length + (ryg.y || 0);
        const greenSignals = signals.filter(s => s.severity === 'green').length  + (ryg.g || 0);

        const signalBadges = signals.slice(0, 6).map(s =>
            `<span class="cs-signal ${{s.severity}}">${{s.desc || s.type}}</span>`
        ).join('');

        const dealHealth  = final.deal_health  || '';
        const trajectory  = final.trajectory   || '';
        const dealStage   = (final.deal_stage || '').replace(/_/g, ' ');

        let html = `
            <div class="cs-score-bar">
                <div class="cs-score">
                    <div class="cs-score-val ${{(final.intent||0) < 5 ? 'red' : 'green'}}">${{final.intent || '?'}}/10</div>
                    <div class="cs-score-lbl">Buyer Intent</div>
                </div>
                <div class="cs-score">
                    <div class="cs-score-val ${{(final.win_pct||0) < 40 ? 'red' : 'green'}}">${{final.win_pct || '?'}}%</div>
                    <div class="cs-score-lbl">Win Likelihood</div>
                </div>
                <div class="cs-score">
                    <div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap">
                        ${{redSignals    ? `<span class="cs-signal red">● ${{redSignals}} Threat${{redSignals>1?'s':''}}</span>` : ''}}
                        ${{yellowSignals ? `<span class="cs-signal yellow">● ${{yellowSignals}} Caution</span>` : ''}}
                        ${{greenSignals  ? `<span class="cs-signal green">● ${{greenSignals}} Trust</span>` : ''}}
                    </div>
                    <div class="cs-score-lbl" style="margin-top:6px">Signals</div>
                </div>
                ${{dealStage ? `<div class="cs-score"><div class="cs-score-val" style="font-size:14px;color:#8A9BAD">${{dealStage}}</div><div class="cs-score-lbl">Stage</div></div>` : ''}}
                ${{dealHealth ? `<div class="cs-score"><div class="cs-score-val" style="font-size:14px;color:#4ecdc4">${{dealHealth}}</div><div class="cs-score-lbl">Health</div></div>` : ''}}
            </div>`;

        if (signalBadges) html += `<div style="margin-bottom:12px">${{signalBadges}}</div>`;

        if (final.summary) html += `
            <div class="cs-box">
                <div class="cs-box-label">Deal Status</div>
                <div class="cs-box-text">${{final.summary}}</div>
            </div>`;

        const coaching = final.coach || (final.recommended_actions || []).map(a => a.action).join(' · ');
        if (coaching) html += `
            <div class="cs-box">
                <div class="cs-box-label">Coaching</div>
                <div class="cs-box-text">${{coaching}}</div>
            </div>`;

        const nextSteps = final.next_steps || (final.recommended_actions || []).map((a,i) =>
            `${{i+1}}. ${{a.action}}`).join('<br>');
        if (nextSteps) html += `
            <div class="cs-box">
                <div class="cs-box-label">Next Steps</div>
                <div class="cs-box-text">${{nextSteps}}</div>
            </div>`;

        if (final.unresolved_items && final.unresolved_items.length) html += `
            <div class="cs-box">
                <div class="cs-box-label">⚠ Unresolved Items</div>
                <div class="cs-box-text">${{final.unresolved_items.map(i => `• ${{i}}`).join('<br>')}}</div>
            </div>`;

        document.getElementById('cs-result').innerHTML = html;
    }}

    // ── Filters ──
    function applyFilters() {{
        const search   = (document.getElementById('filter-search')?.value || '').toLowerCase();
        const metro    = document.getElementById('filter-metro')?.value || '';
        const minPri   = parseInt(document.getElementById('filter-priority')?.value || '0');
        let shown = 0;
        document.querySelectorAll('.prospect-card').forEach(card => {{
            const name = card.querySelector('h2')?.textContent.toLowerCase() || '';
            const tags = Array.from(card.querySelectorAll('.pain-tag')).map(t => t.textContent.toLowerCase()).join(' ');
            const cardMetro    = card.dataset.metro || '';
            const cardPriority = parseInt(card.dataset.priority || '0');
            const matchSearch = !search || name.includes(search) || tags.includes(search);
            const matchMetro  = !metro  || cardMetro === metro;
            const matchPri    = cardPriority >= minPri;
            const visible = matchSearch && matchMetro && matchPri;
            card.classList.toggle('card-hidden', !visible);
            if (visible) shown++;
        }});
        document.getElementById('results-count').textContent = shown;
    }}

    // ── Modal helpers ──
    function openModal(id)  {{ document.getElementById(id).classList.add('open'); document.body.style.overflow = 'hidden'; }}
    function closeModal(id) {{ document.getElementById(id).classList.remove('open'); document.body.style.overflow = ''; }}

    // Close on backdrop click
    document.querySelectorAll('.modal-overlay').forEach(m => {{
        m.addEventListener('click', e => {{ if (e.target === m) closeModal(m.id); }});
    }});

    // ESC to close
    document.addEventListener('keydown', e => {{
        if (e.key === 'Escape') {{
            document.querySelectorAll('.modal-overlay.open').forEach(m => closeModal(m.id));
        }}
    }});

    // ── Init ──
    renderCards(prospects);
</script>
</body>
</html>"""

    return html
