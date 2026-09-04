"""Small server-rendered page templates for the paper-safe dashboard."""

# The HTML is intentionally kept close to the page landmarks for reviewability.
# ruff: noqa: E501

from decimal import Decimal
from html import escape
from typing import Any

from convictionos.application.control_plane import ControlPlaneSummary, LatestDecisionView


def _e(value: object) -> str:
    return escape(str(value))


def _money(value: Decimal) -> str:
    return f"{float(value):,.2f}"


def _readiness_text(readiness: Any, field: str, fallback: str = "—") -> str:
    value = getattr(readiness, field, None)
    return _e(value) if value is not None else fallback


def _readiness_money(readiness: Any, field: str) -> str:
    value = getattr(readiness, field, None)
    return _money(value) if isinstance(value, Decimal) else "—"


def _readiness_cards(readiness: Any) -> str:
    selected = getattr(readiness, "selected_candidate", None)
    risk = getattr(readiness, "portfolio_risk_budget", None)
    rejected = getattr(readiness, "rejected_candidates", ())
    candidate_label = (
        f"{_e(selected.direction)} {_e(selected.horizon.value)} · {_e(selected.long_symbol)} / "
        f"{_e(selected.short_symbol)}"
        if selected is not None
        else "No candidate selected"
    )
    candidate_detail = (
        f"{_money(selected.max_loss)} max loss · {_e(selected.expiry_dte)} DTE"
        if selected is not None
        else "Candidate metrics remain unavailable until a run produces one"
    )
    risk_label = _e(risk.outcome).upper() if risk is not None else "UNAVAILABLE"
    risk_detail = (
        f"{_money(risk.projected_max_loss)} projected max loss · {_e(risk.horizon.value)} budget"
        if risk is not None
        else "No portfolio risk evaluation is available"
    )
    rejected_detail = (
        _e(rejected[0].reason)
        if rejected
        else "No rejected candidate reason recorded"
    )
    options_feed = getattr(readiness, "options_feed", None)
    options_feed_value = str(options_feed) if options_feed is not None else None
    market_data_label = (
        "MARKET DATA UNAVAILABLE"
        if options_feed_value is None
        else {
            "indicative": "INDICATIVE DATA",
            "opra": "OPRA DATA",
            "unknown": "UNKNOWN DATA",
        }.get(options_feed_value, "MARKET DATA UNAVAILABLE")
    )
    market_data_detail = (
        "Paper fills do not establish live execution quality"
        if options_feed_value == "indicative"
        else "PERFORMANCE NOT VALIDATED · Paper fills do not establish live execution quality"
        if options_feed_value == "opra"
        else "Market data capability is unavailable"
    )
    accounting_state = getattr(readiness, "accounting_state", "unavailable")
    accounting_label = (
        "OPEN FILL ONLY" if accounting_state == "open_fill_only" else "ACCOUNTING UNAVAILABLE"
    )
    accounting_detail = (
        "Realized P&amp;L unavailable until reconciled close."
        if accounting_state == "open_fill_only"
        else "No paper accounting facts are available"
    )
    return (
        '<section class="metric-grid readiness-grid" aria-label="Strategy readiness">'
        f'<article class="metric readiness-card"><small>AI THESIS</small>'
        f'<strong>{_readiness_text(readiness, "provider_thesis_status").upper()}</strong>'
        f'<span>Direction {_readiness_text(readiness, "direction")} · Horizon {_readiness_text(readiness, "horizon")}</span></article>'
        f'<article class="metric readiness-card"><small>QUANT CONFIRMATION</small>'
        f'<strong>{_readiness_text(readiness, "ai_quant_agreement").upper()}</strong>'
        f'<span>Evidence freshness: {_readiness_text(readiness, "evidence_freshness")}</span></article>'
        f'<article class="metric readiness-card"><small>SELECTED CANDIDATE</small>'
        f'<strong>{candidate_label}</strong><span>{candidate_detail}</span></article>'
        f'<article class="metric readiness-card"><small>MAX LOSS / EXPIRY</small>'
        f'<strong>${_readiness_money(readiness, "max_loss")}</strong>'
        f'<span>Expiry {_readiness_text(readiness, "expiry")} · {_readiness_text(readiness, "expiry_dte")} DTE</span></article>'
        f'<article class="metric readiness-card"><small>LIQUIDITY</small>'
        f'<strong>{_readiness_text(readiness, "liquidity")}</strong>'
        f'<span>Candidate liquidity score; no synthetic market metrics</span></article>'
        f'<article class="metric readiness-card"><small>PORTFOLIO RISK BUDGET</small>'
        f'<strong>{risk_label}</strong><span>{risk_detail}</span></article>'
        f'<article class="metric readiness-card"><small>RISK GATE</small>'
        f'<strong>{_readiness_text(readiness, "status").upper()}</strong>'
        f'<span>{_readiness_text(readiness, "abstention_reason", "No abstention reason")}</span></article>'
        f'<article class="metric readiness-card"><small>REJECTED CANDIDATES</small>'
        f'<strong>{_e(len(rejected))}</strong><span>{rejected_detail}</span></article>'
        f'<article class="metric readiness-card"><small>MARKET DATA</small>'
        f'<strong>{market_data_label}</strong><span>{market_data_detail}</span></article>'
        f'<article class="metric readiness-card"><small>PAPER ACCOUNTING</small>'
        f'<strong>{accounting_label}</strong><span>{accounting_detail}</span></article>'
        '</section>'
    )


def _nav() -> str:
    return (
        '<nav aria-label="Primary"><a class="brand" href="/">CONVICTION<span>OS</span></a>'
        '<span class="nav-spacer"></span><a href="#product">Product</a>'
        '<a href="#how-it-works">How it works</a><a href="#for-teams">For teams</a>'
        '<a class="nav-link-quiet" href="/proof">Trust center</a>'
        '<a class="nav-cta" href="/dashboard">Open app</a></nav>'
    )


def _shell(title: str, content: str, *, scripts: bool = False) -> str:
    script = '<script src="/static/dashboard.js" defer></script>' if scripts else ""
    return f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>{_e(title)} · ConvictionOS</title><link rel="stylesheet" href="/static/dashboard.css">{script}</head><body><header>{_nav()}<span class="mode">PAPER-SAFE</span></header>{content}<footer><span>ConvictionOS / Glass-box control plane</span><span><a href="/decisions">Decisions</a> · <a href="/research">Research</a> · Synthetic fixture · no live capital authorization</span></footer></body></html>'


def _decision(decision: LatestDecisionView | None) -> str:
    if decision is None:
        return '<div class="empty">No reconciled decision yet. Run the fake demo from the API to create a deterministic receipt.</div>'
    reasons = ", ".join(decision.policy_reasons) or "No policy exceptions"
    return f'<div class="decision-grid"><div><small>UNDERLYING</small><strong>{_e(decision.underlying)}</strong></div><div><small>HORIZON</small><strong>{_e(decision.horizon.value)}</strong></div><div><small>MAX LOSS</small><strong>${_e(decision.max_loss)}</strong></div><div><small>POLICY</small><strong class="safe">{_e(decision.policy_outcome.value.upper())}</strong></div></div><p>{_e(decision.thesis)}</p><p class="muted">Reasons: {_e(reasons)}</p>'


def landing_page(summary: ControlPlaneSummary) -> str:
    receipt = summary.latest_decision.receipt_hash if summary.latest_decision else "No receipt yet"
    preview = f'<div class="preview-window"><div class="preview-top"><span class="dot"></span><span class="preview-title">Mission Control</span><span class="preview-live">PAPER MODE</span></div><div class="preview-body"><div class="preview-kicker">LATEST DECISION</div><div class="preview-symbol">{_e(summary.underlying)} <span>· {_e(summary.horizon.value)}</span></div><div class="preview-bars"><i style="width:78%"></i><i style="width:56%"></i><i style="width:88%"></i></div><div class="preview-row"><span>Mandate</span><b>{_e(summary.mandate_id)} / v{summary.mandate_version}</b></div><div class="preview-row"><span>Risk reserved</span><b>${_money(summary.risk_reserved) if summary.risk_reserved is not None else "—"}</b></div><div class="preview-row"><span>Receipt</span><b class="preview-hash">{_e(receipt[:22])}</b></div><div class="preview-verified">✓ Decision path verified</div></div></div>'
    content = f'<main class="landing"><section class="commercial-hero"><div class="hero-copy"><p class="eyebrow">THE OPERATING SYSTEM FOR CONVICTION</p><h1>Move with conviction.<br><em>Stay in control.</em></h1><p class="lede">A decision intelligence platform for investors and portfolio teams who want faster research, bounded risk, and an audit trail they can actually trust.</p><div class="hero-actions"><a class="button" href="/dashboard">Open Mission Control <span>↗</span></a><a class="text-link" href="#how-it-works">See how it works <span>↓</span></a></div><p class="notice"><span class="trust-dot"></span> {_e(summary.execution_mode)} · built for review before execution</p></div><div class="hero-product">{preview}<div class="float-card"><span class="float-check">✓</span><div><b>Risk-aware by design</b><small>Every action has a policy boundary</small></div></div></div></section><section class="logo-strip"><span>BUILT FOR PEOPLE WHO MANAGE RISK</span><b>INDIVIDUAL INVESTORS</b><b>QUANT RESEARCHERS</b><b>PORTFOLIO TEAMS</b></section><section id="product" class="value-section"><div class="section-intro"><p class="eyebrow">ONE SYSTEM. THREE ADVANTAGES.</p><h2>From signal to decision<br>without the black box.</h2><p>ConvictionOS gives every idea a clear path from evidence to action — with the controls and context to know when not to trade.</p></div><div class="value-grid"><article><span class="value-number">01</span><h3>Research that holds up</h3><p>Capture point-in-time evidence, thesis, invalidation and provenance in one place.</p><a href="/research">Explore Research Lab →</a></article><article><span class="value-number">02</span><h3>Risk before reaction</h3><p>Translate conviction into a mandate with explicit loss, notional and approval boundaries.</p><a href="/dashboard">See Mission Control →</a></article><article><span class="value-number">03</span><h3>Proof at the end</h3><p>Every decision resolves to a receipt that connects intent, policy, broker state and evidence.</p><a href="/proof">Visit Trust Center →</a></article></div></section><section id="how-it-works" class="process-section"><div><p class="eyebrow">HOW IT WORKS</p><h2>Clarity at every step.</h2></div><div class="process-line">{"".join(f"<div><b>{i:02}</b><span>{label}</span></div>" for i, label in enumerate(("Evidence", "Mandate", "Risk", "Execution", "Receipt"), 1))}</div></section><section id="for-teams" class="commercial-proof"><div><p class="eyebrow">TRUST IS A PRODUCT FEATURE</p><h2>Built for the moment<br>before the click.</h2><p>Use the same readable decision surface as a retail investor, a quant, or a portfolio manager. No hidden scoring. No invented performance. No live capital in this demo.</p><a class="button button-dark" href="/dashboard">Explore the product <span>↗</span></a></div><div class="proof-list"><p><span>✓</span> Synthetic data clearly labelled</p><p><span>✓</span> Paper-safe execution boundary</p><p><span>✓</span> Immutable policy and receipt trail</p><p><span>✓</span> Honest limitations, visible upfront</p></div></section></main>'
    return _shell("Trust-first landing", content)


def _scheduler_controls(scheduler: dict[str, object]) -> str:
    paused = bool(scheduler.get("scheduler_paused"))
    state = "PAUSED" if paused else "READY"
    runs = scheduler.get("runs", [])
    safe_runs = runs if isinstance(runs, list) else []
    history = "".join(
        f'<li><b>{_e(item.get("state", "unknown"))}</b> · '
        f'{_e(item.get("started_at", ""))}'
        f'{(" · " + _e(item["reason"])) if item.get("reason") else ""}</li>'
        for item in safe_runs[:5]
        if isinstance(item, dict)
    ) or "<li>No scheduler runs yet.</li>"
    return f'<section class="panel scheduler-panel"><div class="scheduler-head"><div><p class="eyebrow">SUPERVISED AGENT</p><h2>Scheduler · {_e(state)}</h2><p class="muted">Runs only in Alpaca Paper mode. Control token is used in memory and never stored.</p></div><span class="status-pill">PAPER ONLY</span></div><div class="scheduler-controls"><label>Control token<input id="agent-control-token" type="password" autocomplete="off" placeholder="X-Agent-Control-Token"></label><button type="button" data-scheduler-action="run-now">Run Now</button><button type="button" data-scheduler-action="pause">Pause</button><button type="button" data-scheduler-action="resume">Resume</button></div><p id="scheduler-result" aria-live="polite">Last state: {_e(scheduler.get("last_run_state") or "none")}</p><ul class="run-history">{history}</ul></section>'


def _managed_positions_panel() -> str:
    return '<section class="panel managed-positions-panel"><div class="section-heading"><div><p class="eyebrow">POSITION LIFECYCLE</p><h2>Managed Positions</h2></div><a class="text-link" href="/v1/positions">Open data view ↗</a></div><div class="lifecycle-counts"><span><b data-lifecycle-count="open">—</b><small>OPEN</small></span><span><b data-lifecycle-count="close_pending">—</b><small>CLOSE PENDING</small></span><span class="review"><b data-lifecycle-count="review">—</b><small>REVIEW / BLOCKED</small></span><span><b data-lifecycle-count="closed">—</b><small>CLOSED</small></span></div><div id="managed-positions-list" class="managed-positions-list"><p class="empty">Loading reconciled positions…</p></div><p class="muted">Lifecycle state, exit-policy evidence and accounting are read-only. Incomplete values remain unavailable until broker evidence is reconciled.</p><div class="accounting-labels"><span>Broker gross</span><span>Conservative gross</span><span>Verified fees</span><span>Verified after cost</span></div></section>'


def dashboard_page(
    summary: ControlPlaneSummary,
    *,
    agent_enabled: bool = False,
    agent_configured: bool = False,
    readiness: Any = None,
    scheduler: dict[str, object] | None = None,
) -> str:
    decision = summary.latest_decision
    broker = decision.broker_status if decision else "awaiting receipt"
    agent_state = "READY · PAPER ONLY" if agent_enabled and agent_configured else "DISABLED"
    readiness = readiness if readiness is not None else object()
    scheduler_panel = _scheduler_controls(scheduler or {
        "scheduler_paused": False,
        "last_run_state": None,
        "runs": [],
    })
    content = f'<main class="dashboard"><div class="page-head"><div><p class="eyebrow">OPERATIONS / READ-ONLY</p><h1>Mission Control</h1></div><span class="status-pill">{_e(summary.execution_mode)}</span></div><section class="metric-grid"><article class="metric"><small>AUTONOMOUS AGENT</small><strong>{agent_state}</strong><span>Observe → decide → policy → execute → reconcile</span></article><article class="metric"><small>MANDATE</small><strong>{_e(summary.mandate_id)} / v{summary.mandate_version}</strong><span>Max loss ${_money(summary.mandate_max_trade_loss)} · max notional ${_money(summary.mandate_max_notional)}</span></article><article class="metric"><small>RISK RESERVED</small><strong>{"$" + _money(summary.risk_reserved) if summary.risk_reserved is not None else "—"}</strong><span>Reservation is not inferred from PnL</span></article><article class="metric"><small>BROKER RECONCILIATION</small><strong>{_e(broker)}</strong><span>Live trading authorized: no</span></article></section>{scheduler_panel}{_managed_positions_panel()}{_readiness_cards(readiness)}<section class="dashboard-grid"><article class="panel span-2"><p class="eyebrow">LATEST DECISION</p><h2>{_e(summary.underlying)} · {_e(summary.horizon.value)}</h2>{_decision(decision)}<div class="rail"><span>Validated</span><i></i><span>Authorized</span><i></i><span>Submitting</span><i></i><span class="safe">Reconciled</span></div></article><article class="panel"><p class="eyebrow">DECISION FEED</p><p>{"1 deterministic receipt available" if decision else "No reconciled decision yet"}</p><a href="/decisions">View all decisions →</a><a href="/proof">Verify receipt →</a></article><article class="panel research-form"><p class="eyebrow">RESEARCH LAB</p><h2>Test a policy mutation</h2><p class="muted">Simulation only. This form never submits an order.</p><form id="mutation-form"><label>Quantity <input name="quantity" inputmode="decimal" value="20" required></label><label>Limit price <input name="limit_price" inputmode="decimal" value="1.25" required></label><button type="submit">Test policy mutation</button></form><p id="mutation-result" aria-live="polite">Simulation only — no order was submitted.</p></article></section></main>'
    return _shell("Mission Control", content, scripts=True)


def decisions_page(summary: ControlPlaneSummary) -> str:
    content = f'<main class="dashboard"><div class="page-head"><div><p class="eyebrow">AUDIT TRAIL</p><h1>Decisions</h1></div></div><section class="panel"><p class="eyebrow">RECONCILIATION TIMELINE</p>{_decision(summary.latest_decision)}<p class="muted">Receipt is derived from the persisted operation and evidence snapshot hashes.</p><a href="/proof">Open receipt proof →</a></section></main>'
    return _shell("Decisions", content)


def research_page(summary: ControlPlaneSummary) -> str:
    evidence = summary.evidence_claim
    content = f'<main class="dashboard"><div class="page-head"><div><p class="eyebrow">RESEARCH LAB</p><h1>Evidence, not vibes.</h1></div></div><section class="dashboard-grid"><article class="panel span-2"><p class="eyebrow">Synthetic evidence</p><h2>{_e(summary.underlying)} / {_e(summary.horizon.value)}</h2><p>{_e(evidence)}</p><p>{_e(summary.thesis)}</p><dl><dt>Event</dt><dd>{_e(summary.evidence_event_at)}</dd><dt>Observed</dt><dd>{_e(summary.evidence_observed_at)}</dd><dt>Ingested</dt><dd>{_e(summary.evidence_ingested_at)}</dd><dt>Checksum</dt><dd class="mono">{_e(summary.evidence_source_checksum)}</dd></dl></article><article class="panel"><p class="eyebrow">SAFE MUTATION</p><h2>Stress the mandate</h2><p>Change quantity or price and inspect the deterministic deny reason.</p><form id="mutation-form"><label>Quantity <input name="quantity" inputmode="decimal" value="20" required></label><label>Limit price <input name="limit_price" inputmode="decimal" value="1.25" required></label><button type="submit">Evaluate mutation</button></form><p id="mutation-result" aria-live="polite">Simulation only — no order was submitted.</p></article></section></main>'
    return _shell("Research Lab", content, scripts=True)


def proof_page(summary: ControlPlaneSummary) -> str:
    decision = summary.latest_decision
    receipt = decision.receipt_hash if decision else "No receipt to verify yet"
    integrity = (
        "Verified against operation + evidence snapshot"
        if decision
        else "Awaiting a persisted receipt"
    )
    content = f'<main class="dashboard"><div class="page-head"><div><p class="eyebrow">PROOF / HASH CHAIN</p><h1>Receipt verification</h1></div></div><section class="panel proof"><span class="status-pill">{_e(integrity)}</span><h2>Decision receipt</h2><p class="mono">{_e(receipt)}</p><dl><dt>Evidence snapshot</dt><dd class="mono">{_e(summary.evidence_snapshot_hash)}</dd><dt>Execution boundary</dt><dd>Fake/paper only · no live capital authorization</dd><dt>Broker status</dt><dd>{_e(decision.broker_status if decision else "No receipt to verify yet")}</dd></dl><a href="/decisions">Back to decisions →</a></section></main>'
    return _shell("Receipt Proof", content)
