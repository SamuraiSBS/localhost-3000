---
name: demo-qa
description: Verify localhost-3000 jury journeys, browser behavior and demo readiness against the real evaluation criteria.
---

Read docs/demo.md and evaluation.md. Test the actual deployed revision. API assertions do not replace browser acceptance.

For UI acceptance, also read docs/design/README.md and apply docs/design/DESIGN_QA_CHECKLIST.md. Verify Digital Ruins / Lost Signal, no decorative micro-labels, text at least 16 CSS px, keyboard/focus, responsive layout and reduced-motion/fallback behavior. A foundation specimen does not prove final artwork, Prism Glass or the jury journey.

Use the available browser tool for the key user journey at narrow/wide widths; inspect visible failures and relevant network/console errors. Verify essential negative/empty states, external integration behavior and permitted demo data.

Record each scenario as passed, failed or not run with environment and revision. scripts/smoke.py only verifies the temporary probe contract and must change with the product. Keep fictional fixtures visibly fictional; never fabricate a completed run.

Rehearse the time-limited jury story, reset steps and network-loss fallback. Fix blocking problems in scope and update demo.md with evidence and remaining blockers.
