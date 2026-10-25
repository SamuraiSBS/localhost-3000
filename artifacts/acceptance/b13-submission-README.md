# Общий локальный пакет B12+B13

Включает принятый P0, nativeграфик с происхождением данных и настоящий OIDC с локальным Keycloak.
Архив не отправлен и не опубликован. Точные проверки — artifacts/acceptance/b13-results.json.

## Обычный вход

Python3.13, DockerCompose. Из распакованного каталога:

```powershell
python -m venv .venv
.venv/Scripts/python.exe -m pip install --require-hashes -r requirements.txt
.venv/Scripts/python.exe scripts/configure_local.py
powershell -File scripts/dev.ps1 start
```

## Локальный OIDC

```powershell
.venv/Scripts/python.exe scripts/configure_oidc.py --directory .state/oidc --origin http://127.0.0.1:8373 --provider-origin http://127.0.0.1:8390 --project p1-oidc
# При занятых Docker addresspools передать helper --subnet с проверенной свободной сетью.
docker compose --project-name p1-oidc --env-file .state/oidc/compose.env -f compose.yaml -f compose.oidc.yaml --profile oidc up -d --build
```

Открыть http://127.0.0.1:8373/login. Обычные credentials задаются helper.
IdPaccount p1-marketer, пароль только в приватном P1_OIDC_PASSWORD созданногоenv.
На выходе подтвердить Logout у Keycloak. Обычный вход работает безIdP.
Подробные границы/документы/отказIdP — docs/p1-oidc.md.

Explicitallowlist исключает .env, secrets/state/git, rootuploads, исходные материалы,
proprietaryWOFF2 и исходное фото. Arialfallback и отсутствиефоновогофото вZIP — решениепользователя.
Автоматическийsuite вZIPне входит; tests/check.py доступны в исходномcheckout.
ИсторическиеB10/B12screenshots/results обозначают своюrevision; новаяOIDCприёмка вB13results.
Manifest рядомсZIP содержит exactmemberhashes и archivehash; его нетвнутриZIP, чтобыне былоцикла.
