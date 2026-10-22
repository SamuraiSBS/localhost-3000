---
name: mvp-builder
description: Implement a planned Lazyum MVP slice and verify its user journey after challenge and architecture selection.
---

Read docs/implementation-plan.md and the relevant evaluation scenario. Resolve missing challenge/prebuilt-code rules before product development.

Check git status and preserve unrelated work. Implement a small vertical slice in the selected stack. Replace infra/probe deliberately, updating Docker, health, smoke and CI together. Use documented external APIs and environment-managed credentials; distinguish fixtures from live integrations.

Run relevant tests and the actual accepted journey, including important failure/empty states. scripts/check.py currently covers only the temporary probe: replace its assertions when the product changes. Fix reproducible regressions in scope and record evidence in the plan.
