# Browser Smoke Checklist

Manual click-path for release verification (API-level coverage lives in
`scripts/smoke.sh`; this covers what only a browser shows). ~5 minutes.

1. **Seed** — Cockpit → `Sample Data`. A completed sample run appears;
   Cockpit tiles (coverage, volume, gates, readiness) populate.
2. **Views** — open all seven: Cockpit, Review, Repositories, Reports,
   Governance, Audit, and the run detail. No blank panels, no console errors.
3. **Review detail** — select the sample run: risk gate chip, findings list,
   Cross-File Impact panel (may be empty), generated test cases render.
4. **Finding feedback** — expand a finding → Dismiss (risk score drops,
   finding greys out) → Restore (returns). Both appear in Audit.
5. **Verdict chips** — a verified finding shows `✓ verified`; a re-reviewed
   repo shows the muted `carried` chip on carried findings.
6. **Export** — Reports → export JSON, Markdown, CSV; each downloads
   non-empty.
7. **Publish preview** — Reports → platform github, any repo slug, PR 1 →
   Preview renders summary + inline comments; Publish stays disabled until
   the preview succeeds; "No tokens configured" state shows without env
   tokens.
8. **Governance** — change Block Severity, toggle CI auto-publish, Save →
   reload → values persisted; Audit shows `policies_updated`.
9. **Auth** — with `CODE_DOCTOR_TOKEN` set: wrong token → 401 banner;
   correct token → everything above works.
