# Swagger UI local distribution

Upstream: https://github.com/swagger-api/swagger-ui/tree/v5.33.1
Tag: `v5.33.1`; license: Apache-2.0 (full upstream LICENSE alongside).
Source assets are copied byte-for-byte from the official tag `dist/`.
Upstream installation guidance: https://github.com/swagger-api/swagger-ui/blob/v5.33.1/docs/usage/installation.md

The self-contained bundle and stylesheet are served only from session-protected
`/api/v1/docs/assets/`. No npm/runtime dependency is added. The initializer uses
only `/api/v1/openapi.json`, disables the validator and URL query configuration,
blocks external requests, and does not persist authorization. CSP limits network
connections to this origin. Browser rendering/offline network QA remains not run
while browser tooling is disabled; HTTP and file checks are separate evidence.

| File | Source | SHA-256 |
|---|---|---|
| swagger-ui-bundle.js | https://raw.githubusercontent.com/swagger-api/swagger-ui/v5.33.1/dist/swagger-ui-bundle.js | 050bc415ee7048dcd881682678f720264e7da5e373f7461d7c58c755305255f7 |
| swagger-ui.css | https://raw.githubusercontent.com/swagger-api/swagger-ui/v5.33.1/dist/swagger-ui.css | 1ac324f7dcd27e4b9386b4bd6421271ec147e922a22c05ba24b11515e9aa6321 |
| LICENSE | https://raw.githubusercontent.com/swagger-api/swagger-ui/v5.33.1/LICENSE | cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30 |
