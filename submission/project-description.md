# Project description

ConvictionOS is a commercial-grade paper options trading agent built on Alpaca. It turns the typical hackathon trading bot into a trust-first SaaS product: users can see the agent's market evidence, mandate, risk budget, abstention reasons, broker reconciliation state, and signed receipt trail.

The system targets two audiences:

- Small retail investors who want an AI-assisted paper options agent that explains risk before action.
- Quant and portfolio managers who want agent infrastructure that is auditable, provider-agnostic, and operationally safe.

The agent is not a free-form chatbot placing trades. AI produces grounded analysis; deterministic code validates option structure, exposure, lifecycle, and broker state before any paper mutation. The architecture supports OpenRouter/OpenAI-compatible model routing while keeping provider choices outside the domain model.
