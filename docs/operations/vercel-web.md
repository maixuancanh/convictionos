# Vercel Web

ConvictionOS ships a standalone Vercel-ready web package at `apps/web`.
Deploy the same Git revision as the API and expose only public variables to
Vercel.

Required Vercel settings:

- Root directory: `apps/web`
- Build command: `pnpm build`
- Output directory: `dist`
- Public API origin: `NEXT_PUBLIC_API_BASE_URL`
- Release identity: `RELEASE_SHA`

Vercel must not receive broker credentials, model provider secrets, KMS
material, Alpaca account identifiers, database URLs, or worker control tokens.
