from pathlib import Path
from uuid import uuid4
import shutil
from fastapi.testclient import TestClient
from pwdlib import PasswordHash
from app.config import RuntimeConfig
from app.main import create_app


def test_product_pages_require_session_and_serve_local_assets():
    scratch=Path('.state')/('b06-web-test-'+uuid4().hex)
    scratch.mkdir(parents=True)
    config=RuntimeConfig(data_dir=scratch,login='b06-test',password_hash=PasswordHash.recommended().hash('local-test-only'),metrics_token='local-test-metrics-only',public_origin='http://testserver')
    try:
        with TestClient(create_app(config)) as client:
            for path in ['/customers','/customers/1','/settings']:
                response=client.get(path,follow_redirects=False)
                assert response.status_code==303
                assert response.headers['location'].startswith('/login?next=')
                assert response.headers['cache-control']=='no-store'
                assert 'text/html' not in response.headers.get('content-type','')
            assert client.get('/api/v1/customers').status_code==401
            home=client.get('/')
            assert home.status_code==200 and '<base href="/design/">' in home.text
            assert 'href="/login"' in home.text
            assert client.get('/login').status_code==200
            for path in ['/assets/api.js','/assets/app.css','/design/tokens.css','/design/foundation.css','/design/assets/fonts/pp-neue-machina-inktrap-variable.woff2']:
                assert client.get(path).status_code==200
            login=client.post('/api/v1/auth/login',json={'login':'b06-test','password':'local-test-only'},headers={'Origin':'http://testserver'})
            assert login.status_code==200
            for path in ['/customers','/customers/1','/settings']:
                response=client.get(path)
                assert response.status_code==200 and response.headers['cache-control']=='no-store'
                assert 'text/html' in response.headers['content-type']
            csrf=login.json()['csrf_token']
            assert client.post('/api/v1/auth/logout',headers={'Origin':'http://testserver','X-CSRF-Token':csrf}).status_code==204
            assert client.get('/customers',follow_redirects=False).status_code==303
            assert client.get('/api/v1/customers').status_code==401
    finally:
        # Remove only this test's unique runtime directory.
        shutil.rmtree(scratch)
