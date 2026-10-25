"""Anonymous demo sessions have real API access, origin/CSRF guards and isolated data."""
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient
from pwdlib import PasswordHash

from app.config import RuntimeConfig
from app.main import create_app


def test_demo_accounts_are_isolated_from_operator_and_each_other():
    with TemporaryDirectory(prefix="demo-test-", dir=Path('.state')) as directory:
        config = RuntimeConfig(data_dir=Path(directory), login='operator-test',
            password_hash=PasswordHash.recommended().hash('test-password'),
            metrics_token='test-metrics', public_origin='http://testserver')
        with TestClient(create_app(config)) as client:
            assert client.get('/api/v1/auth/demo/config').json() == {'enabled': True}
            assert client.post('/api/v1/auth/demo').status_code == 403
            origin = {'Origin': 'http://testserver'}
            result = client.post('/api/v1/auth/demo', headers=origin)
            assert result.status_code == 200
            assert result.json()['user']['id'] == 'demo'
            assert 'HttpOnly' in result.headers['set-cookie']
            first_cookie = client.cookies.get('session')
            csrf = result.json()['csrf_token']
            assert client.get('/api/v1/auth/me').json()['user']['id'] == 'demo'
            settings = client.get('/api/v1/model/settings')
            original = settings.json()['horizon_days']
            updated = 17 if original != 17 else 18
            headers = {**origin, 'If-Match': settings.headers['etag']}
            assert client.patch('/api/v1/model/settings', json={'horizon_days': updated}, headers=headers).status_code == 403
            headers['X-CSRF-Token'] = csrf
            assert client.patch('/api/v1/model/settings', json={'horizon_days': updated}, headers=headers).status_code == 200
            assert client.get('/api/v1/model/settings').json()['horizon_days'] == updated
            assert client.get('/api/v1/customers').json()['total'] > 0
            second = client.post('/api/v1/auth/demo', headers=origin)
            assert second.status_code == 200
            assert client.get('/api/v1/model/settings').json()['horizon_days'] == original
            assert client.get('/api/v1/analytics/summary').status_code == 200
            assert client.get('/api/v1/observations').status_code != 500
            logged = client.post('/api/v1/auth/login', json={'login': config.login, 'password': 'test-password'}, headers=origin)
            assert logged.status_code == 200
            assert client.get('/api/v1/model/settings').json()['horizon_days'] == original
            assert client.get('/api/v1/auth/me').json()['user']['id'] == 'operator'
            # Rotation revokes the old demo credential.
            client.cookies.set('session', first_cookie, domain='testserver.local', path='/')
            assert client.get('/api/v1/auth/me').status_code == 401


def test_demo_can_be_disabled():
    with TemporaryDirectory(prefix="demo-disabled-", dir=Path('.state')) as directory:
        config = RuntimeConfig(data_dir=Path(directory), login='operator-test',
            password_hash=PasswordHash.recommended().hash('test-password'),
            metrics_token='test-metrics', public_origin='http://testserver', demo_enabled=False)
        with TestClient(create_app(config)) as client:
            assert client.get('/api/v1/auth/demo/config').json() == {'enabled': False}
            assert client.post('/api/v1/auth/demo', headers={'Origin': 'http://testserver'}).status_code == 404
