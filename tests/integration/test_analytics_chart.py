"""Protected chart endpoint, coherent revisions, and unavailable source."""
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient
from pwdlib import PasswordHash

from app.config import RuntimeConfig
from app.main import create_app


def test_chart_requires_auth_and_tracks_published_snapshot_and_settings():
    scratch=Path('.state/p1-b12/tests')/uuid4().hex
    scratch.mkdir(parents=True)
    config=RuntimeConfig(data_dir=scratch,login='chart-test',password_hash=PasswordHash.recommended().hash('chart-local-test'),metrics_token='chart-test-token',public_origin='http://testserver')
    app=create_app(config)
    with TestClient(app) as client:
        assert client.get('/api/v1/analytics/churn-chart').status_code==401
        login=client.post('/api/v1/auth/login',json={'login':'chart-test','password':'chart-local-test'},headers={'Origin':'http://testserver'})
        assert login.status_code==200
        chart=client.get('/api/v1/analytics/churn-chart').json()
        summary=client.get('/api/v1/analytics/summary').json()
        rows=client.get('/api/v1/customers').json()
        for key in ('analysis_revision','data_revision','settings_revision','model_version'):
            assert chart[key]==summary[key]==rows[key]
        assert chart['history']['available'] is False
        source=client.put('/api/v1/source/snapshot',json={'accounts':[],'services':[],'transactions':[]},headers={'Origin':'http://testserver','X-CSRF-Token':login.json()['csrf_token']})
        assert source.status_code==200
        empty=client.get('/api/v1/analytics/churn-chart').json()
        assert empty['data_revision']>chart['data_revision']
        assert sum(empty['risk_counts'].values())==0
        app.state.coordinator._published=None
        assert client.get('/api/v1/analytics/churn-chart').status_code==503
        assert '/api/v1/analytics/churn-chart' in app.openapi()['paths']
