# Public Release Checklist

Do not publish until all items pass:

- `info.txt` and any other scratch credential files are deleted or kept outside
  the repository and outside release artifacts.
- `gitleaks detect --source . --log-opts=--all` has no unreviewed findings.
- A fresh public clone installs and runs the documented tests without private
  files, local `.env`, Railway state, browser cookies, screenshots, or artifacts.
- Railway exposes only API; worker and migration services have no public domain.
- Vercel, if used later, contains only public web variables.
- README links point to the submitted release SHA and do not claim profit,
  live trading, or guaranteed autonomous execution.
