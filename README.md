# ConvictionOS

ConvictionOS is a paper-only options trading agent for Alpaca. It combines market evidence, structured model reasoning, deterministic risk checks, broker reconciliation, and a public dashboard that shows what the system is allowed to do.

The project was built for the Alpaca AI Trading Agents Hackathon. The goal is simple: let an autonomous agent trade only when the evidence, account state, and risk rules agree. If they do not agree, the agent abstains and records why.

Live demo:

- Dashboard: [https://convictionos-web.vercel.app/dashboard](https://convictionos-web.vercel.app/dashboard)
- API readiness: [https://divine-illumination-production-7f99.up.railway.app/v1/public/competition-readiness](https://divine-illumination-production-7f99.up.railway.app/v1/public/competition-readiness)
- Runtime status: [https://divine-illumination-production-7f99.up.railway.app/v1/runtime/status](https://divine-illumination-production-7f99.up.railway.app/v1/runtime/status)

## What it does

ConvictionOS runs a supervised paper-trading loop:

1. Pulls point-in-time market evidence from Alpaca.
2. Builds a structured thesis through a provider-agnostic model gateway.
3. Routes the idea into one of three mandates: catalyst, swing, or thematic.
4. Checks liquidity, spread, freshness, max loss, buying power, and portfolio exposure.
5. Submits only defined-risk paper options orders.
6. Reconciles broker state after execution.
7. Records the decision path in receipts and public readiness endpoints.

The current production build is configured for SPY options on Alpaca paper trading. It uses the Alpaca paper account and labels the options feed it receives. The free Alpaca `indicative` options feed is treated as valid for paper execution. OPRA remains a better data feed for performance analysis, but it is not required for the hackathon paper account flow.

## Why it is different

Most trading demos focus on the trade. ConvictionOS spends just as much effort on the refusal.

The agent can say no because a quote is stale, a spread is too wide, a position is already under review, a mandate is exhausted, or the model and quant signal disagree. Those refusals are first-class records, not silent failures.

The model is advisory. It can explain and challenge an idea, but it cannot bypass the risk engine. Execution is deterministic, idempotent, and paper-only.

## Architecture

```mermaid
flowchart LR
    AlpacaData[Alpaca market data] --> Evidence[Evidence builder]
    Evidence --> Model[Model gateway]
    Evidence --> Quant[Quant signals]
    Model --> Router[Strategy router]
    Quant --> Router
    Router --> Risk[Risk governor]
    Risk -->|approved| Executor[Paper execution]
    Risk -->|denied| Receipts[Decision receipts]
    Executor --> AlpacaPaper[Alpaca paper trading API]
    AlpacaPaper --> Reconcile[Broker reconciliation]
    Reconcile --> Receipts
    Receipts --> API[FastAPI control plane]
    API --> Web[Vercel dashboard]
    Worker[Railway worker] --> Evidence
    Worker --> Reconcile
```

The system has three runtime roles:

```mermaid
flowchart TD
    DB[(PostgreSQL)]
    Migrate[convictionos-migrate]
    API[convictionos-api]
    Worker[convictionos-worker]
    Web[Vercel web dashboard]

    Migrate --> DB
    API --> DB
    Worker --> DB
    Web --> API
    API --> Alpaca[Alpaca paper account]
    Worker --> Alpaca
```

## Strategy model

ConvictionOS separates research horizon from options expiry:

| Mandate | Research horizon | Options DTE budget | Typical use |
| --- | --- | --- | --- |
| Catalyst | 1-10 days | 7-30 DTE | Event-driven setups |
| Swing | 2-8 weeks | 30-90 DTE | Trend and volatility structure |
| Thematic | 3-12 months | 90-450 DTE | Slower thesis with review cadence |

Every mandate has its own risk budget. A valid idea still needs fresh data, tradable quotes, enough buying power, and a computable maximum loss before it can become a paper order.

## Safety boundary

ConvictionOS is paper-only by design.

- The broker adapter accepts only Alpaca paper trading URLs.
- Alpaca keys stay on the backend and are never returned by public endpoints.
- The web dashboard cannot place live orders.
- The worker reconciles state before entry.
- New entries are blocked when a position is in review.
- Defined-risk options structures are required.
- Idempotency keys prevent duplicate order submission for the same decision.
- Receipts separate model reasoning, risk decisions, broker acknowledgement, and reconciliation.

No profitability claim is made from a short paper run. Paper trading verifies plumbing and decision discipline, not live performance.

## Configuration

Copy the example environment file if one is available, or create a local `.env` with the same keys. Do not commit `.env`.

Required backend variables:

```dotenv
DATABASE_URL=postgresql+psycopg://user:password@localhost:5432/convictionos

BROKER_MODE=alpaca_paper
ALPACA_PAPER_BASE_URL=https://paper-api.alpaca.markets/v2
ALPACA_DATA_BASE_URL=https://data.alpaca.markets
ALPACA_API_KEY_ID=replace-with-rotated-paper-key-id-in-secret-manager
ALPACA_API_SECRET_KEY=replace-with-rotated-paper-secret-in-secret-manager
ALPACA_OPTION_DATA_FEED=indicative

AGENT_ENABLED=true
AGENT_CONTROL_TOKEN=replace-with-random-control-token-in-secret-manager
AGENT_UNDERLYING=SPY
AGENT_INTERVAL_SECONDS=300

AI_PROVIDER=openrouter
AI_MODEL=openrouter/auto
AI_BASE_URL=https://openrouter.ai/api/v1
AI_API_KEY=replace-with-rotated-openrouter-key-in-secret-manager
```

Production-only variables:

```dotenv
COMPETITION_ACCOUNT_ID=replace-with-paper-account-id
COMPETITION_BASELINE_EQUITY=100000.00
COMPETITION_MANDATE_VERSION=competition-defined-risk-v1
RUNTIME_RELEASE_SHA=replace-with-current-git-sha
REQUIRE_AGENT_STARTUP_INVARIANTS=true
```

Supported model providers:

| Provider | Configuration |
| --- | --- |
| OpenRouter | `AI_PROVIDER=openrouter`, `AI_BASE_URL=https://openrouter.ai/api/v1`, `AI_MODEL=openrouter/auto` |
| OpenAI-compatible gateway | `AI_PROVIDER=openai`, custom `AI_BASE_URL`, Chat Completions-compatible model |
| Gemini | `AI_PROVIDER=gemini`, Gemini API key and model |
| Claude | `AI_PROVIDER=claude`, Claude API key and model |

The gateway validates structured output locally. If the provider returns empty, malformed, or unsupported output, the agent abstains instead of inventing a thesis.

## Run locally

Install Python dependencies:

```bash
uv sync
```

Run migrations:

```bash
uv run python -m convictionos.commands migrate
```

Start the API:

```bash
uv run python -m convictionos.commands api --host 0.0.0.0 --port 8000
```

Start the worker in another terminal:

```bash
uv run python -m convictionos.commands worker
```

Build the web dashboard:

```bash
pnpm --dir apps/web install
pnpm --dir apps/web build
```

Open:

- Landing page: `http://localhost:8000/`
- Mission Control: `http://localhost:8000/dashboard`
- Agent status: `http://localhost:8000/v1/agent/status`
- Public readiness: `http://localhost:8000/v1/public/competition-readiness`

Run one supervised paper cycle:

```bash
curl -X POST http://localhost:8000/v1/agent/run-once \
  -H "X-Agent-Control-Token: $AGENT_CONTROL_TOKEN"
```

Possible cycle outcomes include `abstained`, `denied`, `submitted`, `filled`, and `rejected`.

## Deployment

The production setup uses Railway for the backend and Vercel for the dashboard. Only the API receives a public domain; worker, migration, and database services stay private.

Railway services:

| Service | Role | Public |
| --- | --- | --- |
| `convictionos-api` | FastAPI control plane | Yes |
| `convictionos-worker` | Singleton 24/7 paper agent loop | No |
| `convictionos-migrate` | Alembic migration runner | No |
| PostgreSQL | Durable store | No |

Vercel serves the static dashboard from `apps/web`.

Build-time web variables:

```dotenv
NEXT_PUBLIC_API_BASE_URL=https://your-api-service.up.railway.app
RELEASE_SHA=replace-with-current-git-sha
```

The worker renews a runtime lease in PostgreSQL. Public runtime status exposes only safe fields such as live/stale state, last heartbeat, cycle state, and release SHA. It does not expose account IDs, broker order IDs, control tokens, or credentials.

## API surface

Public endpoints:

| Endpoint | Purpose |
| --- | --- |
| `GET /health/live` | Process liveness |
| `GET /health/ready` | Database and migration readiness |
| `GET /v1/public/competition-readiness` | Public paper-account readiness summary |
| `GET /v1/runtime/status` | Sanitized worker runtime state |
| `GET /v1/strategy/readiness` | Paper-safe strategy projection |

Control endpoints require `X-Agent-Control-Token`:

| Endpoint | Purpose |
| --- | --- |
| `POST /v1/agent/run-once` | Trigger one supervised agent cycle |
| `POST /v1/agent/pause` | Pause the timed worker loop |
| `POST /v1/agent/resume` | Resume the timed worker loop |
| `POST /v1/workspaces/{workspace_id}/eligibility/capture-baseline` | Capture the current paper eligibility manifest |

## Verification

Run the Python test suite:

```bash
uv run pytest -q
```

Run lint and type checks:

```bash
uv run ruff check .
uv run mypy
```

Run web checks:

```bash
pnpm --dir apps/web test
pnpm --dir apps/web build
```

Useful focused checks:

```bash
uv run pytest tests/domain/test_eligibility.py tests/application/test_eligibility.py -q
uv run pytest tests/api/test_eligibility_api.py tests/api/test_health_api.py -q
uv run pytest tests/application/test_agent_scheduler.py tests/domain/test_competition.py -q
```

## Data and credential handling

Do not commit:

- `.env` files
- API keys
- Alpaca account IDs
- control tokens
- broker order IDs from a real account
- screenshots of provider dashboards
- local deployment metadata
- scratch files or private notes

If a key appears in chat, screenshots, `info.txt`, shell history, or a deployment page capture, revoke it in the provider dashboard and create a new one before publishing.

## License

MIT
