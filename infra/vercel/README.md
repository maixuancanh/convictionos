# Vercel

Deploy the commercial landing page and Mission Control dashboard from
`apps/web`.

Use these project settings:

- Root directory: `apps/web`
- Build command: `pnpm build`
- Output directory: `dist`
- Framework preset: Other
- `NEXT_PUBLIC_API_BASE_URL`: public ConvictionOS API origin
- `RELEASE_SHA`: the same Git SHA deployed to Railway

Vercel must never receive broker credentials, model provider secrets, KMS
material, Alpaca account identifiers, or worker control tokens.
