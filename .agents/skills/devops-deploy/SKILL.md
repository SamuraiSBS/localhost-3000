---
name: devops-deploy
description: Build, deploy, diagnose or roll back Lazyum containers with the project release scripts and Yandex VM runbook.
---

Read docs/infrastructure.md and deployment.md. Verify the target, access, current release and local/production mode.

Local commands are scripts/dev.ps1 or dev.sh plus scripts/smoke.py. Production uses immutable digests from successful main CI. Keep GHCR private and use the run-scoped GITHUB_TOKEN for pulls. Keep credentials out of artifacts, images and logs.

deploy.sh checks health and the exact revision through the proxy, retaining the previous image and configuration. rollback.sh reactivates the previous accepted release. Do not prune rollback images, release folders or persistent volumes. Data migrations need a separate strategy after the challenge.

Before bootstrap verify a dedicated Ubuntu VM, administrative access and cloud firewall. Respect purchase/DNS authorization. A container result does not prove public HTTPS; report untested stages and rollback failures explicitly.
