# Local interaction specimen verification — 2026-10-24

Scope: `frontend/`, generated `design/interaction-lab/`, isolated tokens under
`[data-design-lab]`. Existing product routes and backend were not changed.
Working tree contains unrelated pre-existing changes; no commit/push/deploy.

| Check | Result | Evidence / limit |
|---|---|---|
| Vite production build and postbuild attribution copy | passed | Vite 8.3.5; final command exited 0. Main JS 474.83 kB, chart 352.41 kB, GSAP animation 116.03 kB before gzip; all fonts local. |
| Upstream snapshot integrity | passed | `npm run check:sources`: 8 registry snapshots, additional 21st chart snapshot, 13 unchanged source files. |
| Project design-integrity script | passed | Asset hashes, local links, font headers, tokens and prior mobile/font budgets. This does not measure performance of the new WebGL specimen. |
| `git diff --check` | passed | No whitespace errors; Windows LF/CRLF informational warnings only. |
| Original FaultyTerminal and Liquid Glass visible | passed | Real in-app Chromium browser at `http://127.0.0.1:3387/interaction-lab/`; glyph field and refraction rendered. |
| Custom control geometry | passed | Browser computed style: selected 8 px radius and 48 px height; capsule selector also changed the computed radius to 999 px. |
| Loading width | passed | At narrow viewport the button measured 255 px before and during loading; `aria-busy=true`, disabled while pending. |
| Form validation | passed | Empty login sets `aria-invalid=true` and shows the linked error. Nonempty demo login completes local validation. |
| Dialog | passed | Opened by button; Escape closed it and restored focus to «Открыть диалог». |
| Tabs / search | passed | ArrowRight selected high risk and reduced rows to 2; ArrowLeft restored all; unmatched search displayed empty state. |
| Mobile menu | passed | Open state reflected `aria-expanded`; Escape restored focus to the menu button. |
| Responsive layout | passed | Actual measured CSS viewport / document widths: 320 / 305, 360 / 345, 768 / 753, 1440 / 1425. Difference is the scrollbar. No page-wide horizontal overflow; table retains its internal scroll region. |
| Browser console | passed for inspected session | No captured page errors/warnings in the tested session. Host Statsig messages after environment restart are not application errors. |
| Manual background pause | passed | Button toggled to «Продолжить фон». |
| OS reduced motion, forced colors, transparency | not_run in browser | Source/fallback review only; not emulated. |
| Physical weak GPU / real Safari / Firefox | not_run | No claim of cross-browser or device performance acceptance. |
| Original chart keyboard/touch parity | not_run / upstream limitation | Original hover-only interaction remains documented beside the sample. |
| Full pixel-for-pixel comparison with every original demo | not_run | Source identity is checked; themes, dimensions, wrapping, DPR and browser may still differ. External adapters are documented. |
| Full Python test suite / product E2E / Docker / CI / public deployment | not_run | This iteration is an isolated design specimen. |
| `21st review` CLI | not_run | CLI not present; MCP source retrieval, local checks and actual browser review were used. |

During the environment update, the running preview server and browser session
were reset. The server was restarted on the same loopback-only port and the
final built page reopened. Three captured 21st source files were found filled
with zero bytes, restored from `registry/chart-21st.json` using Python, then
verified again. The final production build completed successfully after this
repair. No ACL or global configuration changes were made.

Chosen control radius: **8 px**, explicitly selected by the user in the current
conversation. The original Magic UI capsule is a separate source example.
