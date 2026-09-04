# Credential Rotation

Before making the repository public or submitting final media, revoke and
recreate every credential that appeared in chat, screenshots, terminals,
browser variable pages, local scratch files, or other non-secret channels.

Rotate at least:

- Google Gemini API keys.
- OpenRouter or other AI provider keys.
- Alpaca paper API key and secret.
- Railway, Vercel, GitHub, Clerk, Stripe, KMS, webhook, and control tokens.

Updating `.env` is not enough. The old provider-side credential must be
revoked, the replacement must be stored only in the provider secret manager,
and read-only smokes must be rerun after rotation.
