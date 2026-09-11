# Hackathon development

Use pnpm for JavaScript dependencies and the existing isolated Python environment
for the memory services. Keep credentials in ignored local files.

## Snyk security checks

Use Snyk during development, when fixing findings, and before submission:

- After a meaningful change to first-party source, run `pnpm security:code`.
- After Node dependency changes, run `pnpm security:node`.
- After Python dependency changes, run `pnpm security:python`.
- Before submission, run `pnpm security:scan` against the final source state.
- Review findings, fix applicable issues, and rescan affected code or dependencies.
- Do not hide findings with ignore policies or weaken validation to obtain a pass.
- Keep scan evidence in `sponsor-setup/snyk/reports/`, which is Git-ignored.
- Treat authentication failures, unsupported projects, and scanner errors as
  incomplete checks. A successful scan supports only its actual scope; do not
  claim the application has no vulnerabilities.

The project MCP configuration also exposes Snyk's local scanning tools to Codex
after its MCP connection is restarted. The CLI scan scripts provide repeatable
reports independently of that restart.
