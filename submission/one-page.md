# ConvictionOS: paper-only options alpha with deterministic risk authority

ConvictionOS is a paper-only autonomous options trading agent for the Alpaca AI Trading Agents Hackathon. The product is built around one principle: the AI may explain, challenge, and rank opportunities, but deterministic software owns execution authority.

The agent loop is intentionally auditable. It collects point-in-time market and account evidence, asks a provider-agnostic AI layer for grounded analysis, routes the result through catalyst, swing, or thematic options mandates, applies hard risk gates, submits only approved paper orders through Alpaca infrastructure, reconciles actual broker state through Alpaca Trading API reads, and writes a decision receipt that explains the action or abstention.

For judges and future customers, the commercial SaaS surface is trust-first: landing page, Mission Control dashboard, readiness disclosures, runtime health, incidents, strategy evidence, position lifecycle, and receipt verification. The system is designed for small retail investors who want transparent automation and for quant/portfolio managers who want reviewable option-strategy infrastructure.

Safety boundaries are not presentation copy; they are implemented as product mechanics. ConvictionOS is paper-only, uses server-side secrets, separates API/worker/migration process roles, persists scheduler leases and heartbeats in PostgreSQL, and can pause, freeze, or switch to closing-only through durable controls. When evidence is stale, quotes are incomplete, risk limits are exceeded, model output is invalid, or broker state cannot be reconciled, the agent abstains.

The current submission does not claim a proven P&L edge. It claims a stronger foundation: a real paper-trading agent architecture that can keep running, explain itself, respect options risk, and produce evidence without hiding behind black-box AI. Live paper results should be collected continuously and reported exactly as observed.
