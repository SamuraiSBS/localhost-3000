---
name: mvp-builder
description: Implement a planned localhost-3000 MVP slice and verify its user journey after challenge and architecture selection.
---

Read docs/implementation-plan.md and the relevant evaluation scenario. Resolve missing challenge/prebuilt-code rules before product development.

Before UI work, read docs/design/README.md, DESIGN_SYSTEM.md, VISUAL_IDENTITY.md and the relevant COMPONENTS.md/EFFECTS.md sections in that folder. Preserve Digital Ruins / Lost Signal and reuse design/tokens.css. No decorative micro-labels or invented statuses; useful text starts at 16 CSS px. The challenge defines functionality, not a replacement visual identity.

Check git status and preserve unrelated work. Implement a small vertical slice in the selected stack. Replace infra/probe deliberately, updating Docker, health, smoke and CI together. Use documented external APIs and environment-managed credentials; distinguish fixtures from live integrations.

Run relevant tests and the actual accepted journey, including important failure/empty states. scripts/check.py currently covers the temporary probe and offline design integrity: update its assertions when the product changes. Fix reproducible regressions in scope and record evidence in the plan.
