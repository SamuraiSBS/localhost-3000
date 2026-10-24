"""Build the local delivery ZIP from an exact, reviewed safe allowlist.

Never discover members by walking the checkout: local credentials, original
materials, uploads, proprietary font, and unrelated work stay excluded.
"""
import hashlib
import json
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]
MEMBERS = (
    '.dockerignore',
    '.editorconfig',
    '.env.example',
    '.gitattributes',
    '.gitignore',
    'DESIGN.md',
    'Dockerfile',
    'PRODUCT.md',
    'README.md',
    'app/__init__.py',
    'app/api/analytics.py',
    'app/api/auth.py',
    'app/api/customers.py',
    'app/api/settings.py',
    'app/api/source.py',
    'app/auth.py',
    'app/config.py',
    'app/contracts.py',
    'app/domain/__init__.py',
    'app/domain/churn_chart.py',
    'app/domain/context.py',
    'app/domain/features.py',
    'app/domain/money.py',
    'app/domain/scoring.py',
    'app/evaluation.py',
    'app/main.py',
    'app/oidc.py',
    'app/metrics.py',
    'app/storage.py',
    'artifacts/acceptance/b10-narrow.png',
    'artifacts/acceptance/b10-results.json',
    'artifacts/acceptance/b10-wide.png',
    'artifacts/acceptance/b12-narrow.png',
    'artifacts/acceptance/b12-results.json',
    'artifacts/acceptance/b12-submission-README.md',
    'artifacts/acceptance/b12-wide.png',
    'artifacts/acceptance/b13-results.json',
    'artifacts/acceptance/b13-login-wide.png',
    'artifacts/acceptance/b13-login-narrow.png',
    'artifacts/acceptance/b13-keycloak.png',
    'artifacts/acceptance/b13-submission-README.md',
    'compose.production.yaml',
    'compose.yaml',
    'data/seed.json',
    'design/assets/columns-768.webp',
    'design/assets/columns.prompt.txt',
    'design/assets/columns.provenance.json',
    'design/assets/columns.webp',
    'design/assets/icons/LICENSE',
    'design/assets/icons/README.md',
    'design/assets/icons/UPSTREAM_README.md',
    'design/assets/icons/arrow-right.svg',
    'design/assets/icons/check.svg',
    'design/assets/icons/info-circle-solid.svg',
    'design/assets/icons/info-circle.svg',
    'design/assets/icons/sources.json',
    'design/chroma-current.js',
    'design/fonts.css',
    'design/foundation.css',
    'design/dither.js',
    'design/landing.css',
    'design/landing.js',
    'design/motion.css',
    'design/motion.js',
    'design/liquid-glass-optics.js',
    'design/liquid-glass-preset.js',
    'design/liquid-glass.css',
    'design/liquid-glass.js',
    'design/prism.js',
    'design/scene.css',
    'design/scene.js',
    'design/tokens.css',
    'docs/api-contract.md',
    'docs/architecture.md',
    'docs/challenge.md',
    'docs/demo.md',
    'docs/deployment.md',
    'docs/design/COMPONENTS.md',
    'docs/design/DESIGN_MAP.md',
    'docs/design/DESIGN_QA_CHECKLIST.md',
    'docs/design/DESIGN_SYSTEM.md',
    'docs/design/EFFECTS.md',
    'docs/design/IMPLEMENTATION_GUIDE.md',
    'docs/design/LIVING_DESIGN.md',
    'docs/design/README.md',
    'docs/design/VERIFICATION.md',
    'docs/design/VISUAL_IDENTITY.md',
    'docs/evaluation.md',
    'docs/infrastructure.md',
    'docs/model.md',
    'docs/open-questions.md',
    'docs/p1-chart.md',
    'docs/p1-oidc.md',
    'docs/requirements.md',
    'docs/reuse-manifest.json',
    'docs/reuse-research.md',
    'docs/rules.md',
    'docs/runtime.md',
    'docs/test-plan.md',
    'infra/Caddyfile',
    'infra/prometheus.yml',
    'infra/keycloak/localhost3000-p1-realm.json',
    'compose.oidc.yaml',
    'openapi.json',
    'requirements-dev.in',
    'requirements-dev.txt',
    'requirements.in',
    'requirements.txt',
    'scripts/configure_local.py',
    'scripts/configure_oidc.py',
    'scripts/dev.ps1',
    'scripts/dev.sh',
    'scripts/package_delivery.py',
    'scripts/smoke.py',
    'scripts/verify_delivery.py',
    'web/assets/api.js',
    'web/assets/app.css',
    'web/assets/churn-chart.js',
    'web/assets/customer.js',
    'web/assets/customers.js',
    'web/assets/login.js',
    'web/assets/living.css',
    'web/assets/settings.js',
    'web/assets/shell.js',
    'web/customer.html',
    'web/customers.html',
    'web/index.html',
    'web/login.html',
    'web/settings.html',
    'web/vendor/swagger/LICENSE',
    'web/vendor/swagger/README.md',
    'web/vendor/swagger/swagger-ui-bundle.js',
    'web/vendor/swagger/swagger-ui.css',
)


def build():
    output=ROOT/'artifacts/acceptance/b13-submission.zip'
    entries=[]
    with zipfile.ZipFile(output,'w',compression=zipfile.ZIP_DEFLATED) as archive:
        for name in MEMBERS:
            relative=Path(name)
            if relative.is_absolute() or '..' in relative.parts:
                raise ValueError('unsafe_package_path')
            path=ROOT/relative
            if path.is_symlink() or not path.is_file():
                raise ValueError(f'missing_or_unsafe_package_member:{name}')
            raw=path.read_bytes()
            archive.writestr(name,raw)
            entries.append({'path':name,'size_bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()})
    with zipfile.ZipFile(output) as archive:
        if archive.testzip() is not None: raise ValueError('invalid_zip')
    manifest={'task_id':'B13','archive':output.name,'archive_size_bytes':output.stat().st_size,
        'archive_sha256':hashlib.sha256(output.read_bytes()).hexdigest(),'member_count':len(entries),'members':entries,
        'scope':'B11 explicit safe members plus B12 chart and B13 OIDC; no directory expansion',
        'exclusions':['proprietary font and source photographs','original organizer data and root uploads','.env and credentials','.state and .git','tests and check.py (same B11 distribution boundary)'],
        'validation':{'zip_integrity':'passed','exact_allowlist':'passed'},
        'runtime_validation':'See b13-results.json and docs/p1-oidc.md. ZIP integrity does not prove Docker, CI, or public deployment.'}
    (ROOT/'artifacts/acceptance/b13-package-manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({key:manifest[key] for key in ('archive','member_count','archive_sha256')}))


if __name__=='__main__': build()
