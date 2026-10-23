# Implementation notes

## B09 — product delivery, 2026-10-23

Replace probe Docker/Compose/check/smoke/CI coherently with the accepted FastAPI/native product.
Keep one Uvicorn worker, SQLite named volume, non-root UID65532, read-only root, dropped capabilities,
no-new-privileges and bounded memory. Initial organizer JSON is deliberately allowlisted into `/app/data/seed.json`;
app/web/design and locked requirements are the only other build inputs. No Node/React product dependencies.
Secrets are supplied at runtime from a private env file; configure helper never overwrites or prints them.
Argon2 hash is single-quoted in Compose env syntax to preserve dollar signs. Default startup refuses missing credentials.
`APP_REVISION` is baked from VCS_REF, returned by product health; dirty local builds explicitly say dirty.

Prometheus is an optional private profile with separate bearer credentials_file (current official HTTP authorization syntax),
no published host port and bounded temporary storage. Its token is a file secret, never baked into either image.
Proxy and Uvicorn request access logs are disabled to avoid customer email query disclosure.
Actual scrape/restart evidence belongs in artifacts/acceptance/b09-compose.json, not contract documents.
Legacy deploy/rollback scripts continue immutable image/health revision verification; migration rollback of future schema
changes is not promised. Product database volumes must not be removed on update/rollback.
Upstream Swagger JS/CSS/LICENSE have -text attributes so Windows Git checkout preserves official bytes/hashes.
CI configuration prepared locally does not claim a successful remote run. Public deployment/CD/DNS were not changed.

### B09 review repair: Windows secret permissions

`chmod(0600)` does not restrict inherited Windows ACLs. Configure now protects a dedicated empty directory and each file
with an explicit current-user SID-only protected DACL, verifies owner/rights/no inherited entries before writing any secret,
and fails closed on ACL application/inspection failure. Paths cross the PowerShell boundary as JSON stdin, never interpolated code.
Windows PowerShell .NET File/Directory ACL APIs avoid reliance on an optional security module. POSIX directory/files remain0700/0600.
Existing B09 synthetic secret directory and compose.env/metrics-token/smoke-password were repaired and verified using metadata only.
