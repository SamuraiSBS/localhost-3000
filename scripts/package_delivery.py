"""Build the local delivery ZIP from an exact, reviewed safe allowlist.

Never discover members by walking the checkout: local credentials, original
materials, uploads, proprietary font, and unrelated work stay excluded.
"""
import hashlib
import json
from pathlib import Path, PurePosixPath, PureWindowsPath
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
    'app/api/data_quality.py',
    'app/api/exports.py',
    'app/api/lab.py',
    'app/api/observations.py',
    'app/api/settings.py',
    'app/api/source.py',
    'app/api/workflow.py',
    'app/auth.py',
    'app/config.py',
    'app/contracts.py',
    'app/data_quality.py',
    'app/demo.py',
    'app/domain/__init__.py',
    'app/domain/churn_chart.py',
    'app/domain/context.py',
    'app/domain/features.py',
    'app/domain/money.py',
    'app/domain/lab.py',
    'app/domain/lab_case.py',
    'app/domain/lab_sensitivity.py',
    'app/domain/lab_solver.py',
    'app/domain/lab_source_diff.py',
    'app/domain/lab_worklist.py',
    'app/domain/renewal_calendar.py',
    'app/domain/scoring.py',
    'app/evaluation.py',
    'app/export_csv.py',
    'app/main.py',
    'app/lab_contracts.py',
    'app/oidc.py',
    'app/metrics.py',
    'app/observation_storage.py',
    'app/storage.py',
    'app/workflow_storage.py',
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
    'design/terminal-field.css',
    'design/terminal-field.js',
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
    'docs/design/THIRD_PARTY_NOTICES.md',
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
    'web/assets/lab.css',
    'web/assets/lab.js',
    'web/assets/living.css',
    'web/assets/settings.js',
    'web/assets/shell.js',
    'web/customer.html',
    'web/customers.html',
    'web/index.html',
    'web/login.html',
    'web/lab.html',
    'web/settings.html',
    'web/vendor/swagger/LICENSE',
    'web/vendor/swagger/README.md',
    'web/vendor/swagger/swagger-ui-bundle.js',
    'web/vendor/swagger/swagger-ui.css',
)


OUTPUT = Path('artifacts/acceptance/decision-lab/submission.zip')
MANIFEST = Path('artifacts/acceptance/decision-lab/package-results.json')
ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)


def _member_bytes(name):
    """Validate explicit portable paths and every ancestor before reading bytes."""
    relative = PurePosixPath(name)
    if (not name or relative.is_absolute() or PureWindowsPath(name).drive
            or "\\" in name or relative.as_posix() != name
            or any(part in {'.', '..'} for part in name.split('/'))):
        raise ValueError('unsafe_package_path')
    path = ROOT
    for part in relative.parts:
        path = path / part
        if path.is_symlink():
            raise ValueError(f'missing_or_unsafe_package_member:{name}')
    if not path.is_file() or not path.resolve().is_relative_to(ROOT.resolve()):
        raise ValueError(f'missing_or_unsafe_package_member:{name}')
    return path.read_bytes()


def build():
    # Validate every required input before creating/replacing a deliverable.
    if len(MEMBERS) != len(set(MEMBERS)):
        raise ValueError('duplicate_package_member')
    payloads = [(name, _member_bytes(name)) for name in sorted(MEMBERS)]
    output = ROOT / OUTPUT
    output.parent.mkdir(parents=True, exist_ok=True)
    entries = []
    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, raw in payloads:
            info = zipfile.ZipInfo(name, ZIP_TIMESTAMP)
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, raw, compresslevel=9)
            entries.append({'path': name, 'size_bytes': len(raw),
                            'sha256': hashlib.sha256(raw).hexdigest()})
    with zipfile.ZipFile(output) as archive:
        if archive.testzip() is not None:
            raise ValueError('invalid_zip')
    manifest = {
        'task_id': 'B22',
        'archive': OUTPUT.as_posix(),
        'archive_size_bytes': output.stat().st_size,
        'archive_sha256': hashlib.sha256(output.read_bytes()).hexdigest(),
        'member_count': len(entries),
        'members': entries,
        'scope': 'Final Decision Lab application/API/domain/UI/docs/OpenAPI; explicit safe members only; no directory expansion',
        'exclusions': ['proprietary WOFF2 and source photographs',
                       'original organizer data and root uploads', '.env and credentials',
                       '.state, .agent-state, .git and scratch',
                       'internal execution plans, coordination data and unreviewed notes',
                       'raw local test results, screenshots and candidate notes',
                       'tests and unrelated acceptance artifacts'],
        'validation': {'zip_integrity': 'passed', 'exact_allowlist': 'passed'},
        'runtime_validation': 'not_run by packaging; see docs/demo.md for the local application scenario',
        'docker': 'not_run', 'ci': 'not_run', 'public_environment': 'not_run',
    }
    (ROOT / MANIFEST).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    print(json.dumps({key: manifest[key] for key in ('archive', 'member_count', 'archive_sha256')}))
    return manifest


if __name__ == '__main__':
    build()
