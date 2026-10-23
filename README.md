# localhost-3000

Локальный анализатор риска непродления: FastAPI, SQLite и native HTML/CSS/ES modules.
Вход → рейтинг клиентов → объяснение риска и денег → настройки и импорт полного snapshot.
Риск — объяснимый индекс, не статистическая вероятность. Денежные значения API — евроценты.

## Запуск из чистого checkout

Нужны Python 3.13 и Docker Engine с Compose >= 2.24.4. Команды из корня репозитория.

```powershell
python -m venv .venv
.venv/Scripts/python.exe -m pip install --require-hashes -r requirements-dev.txt
.venv/Scripts/python.exe scripts/configure_local.py
powershell -File scripts/dev.ps1 start
```

Linux/macOS: `python3 -m venv .venv`, затем `.venv/bin/python -m pip install --require-hashes -r requirements-dev.txt`,
`.venv/bin/python scripts/configure_local.py` и `bash scripts/dev.sh start`.
Открыть [локальную панель](http://127.0.0.1:8080); ввести заданные при настройке логин/пароль.
Скрипт запрашивает пароль без отображения, хранит только Argon2 hash и случайный collector token;
готовых публичных credentials нет. На Windows helper создаёт защищённую DACL только для SID текущего пользователя,
проверяет отсутствие наследуемых/широких разрешений до записи секретов и завершает работу с ошибкой, если ограничить доступ нельзя.
На POSIX каталог получает 0700, файлы 0600. Нужен отдельный пустой каталог; существующие файлы не перезаписываются.
`.state/local/compose.env` и token приватны, не добавлять их в Git/архив сдачи и не публиковать вывод `compose config` без `--quiet`.

Если Docker сообщает об исчерпанных address pools, проверьте занятые сети и передайте
`--subnet` со свободной подсетью для нового project; не удаляйте чужие сети. По умолчанию subnet выбирает Docker.

Существующий корневой `.env` не меняется. Все команды используют явный env-файл и `-f compose.yaml`,
поэтому локальный старый `compose.override.yaml` не влияет на продукт. Для другого порта/проекта:

```powershell
.venv/Scripts/python.exe scripts/configure_local.py --directory .state/my-demo --origin http://127.0.0.1:8360 --project my-demo
powershell -File scripts/dev.ps1 start -EnvFile .state/my-demo/compose.env
```

`PUBLIC_ORIGIN` обязан точно совпадать с адресом браузера: иной Origin отклоняется.
Для Linux передайте env-файл вторым аргументом: `bash scripts/dev.sh start .state/my-demo/compose.env`.
Команды `status`, `logs`, `stop`, `update`, `check` доступны тем же скриптом.
`stop` сохраняет named volume SQLite; не удалять volumes при обновлении или restart.
Один Uvicorn worker, один SQLite volume на Compose project. App работает non-root с read-only root filesystem.
Образ содержит разрешённую отдельную seed-копию `data/seed.json`, локальные web/design assets и locked dependencies.
Оригиналы данных, root-фотографии, PDF, `.env`, `.state` и `.git` не передаются в build context.

## Проверки

```powershell
.venv/Scripts/python.exe scripts/check.py
git diff --check
docker compose --env-file .state/local/compose.env -f compose.yaml config --quiet
.venv/Scripts/python.exe scripts/smoke.py http://127.0.0.1:8080
```

`check.py` выполняет pytest продукта и offline integrity дизайна. Smoke проверяет `/healthz`, revision
(если передать `--revision`), главную и закрытость customer API, metrics, Swagger/OpenAPI.
Это HTTP smoke, отдельная браузерная приёмка описана в [demo](docs/demo.md).
При сборке передайте `VCS_REF` текущего commit (CI делает это автоматически); local build по умолчанию помечен `local`.
Незакоммиченные изменения обозначать явно, не выдавать SHA базы за точную версию изменённого образа.

Для автоматической изолированной проверки использовать **новый** project и отдельную директорию:

```powershell
.venv/Scripts/python.exe scripts/configure_local.py --ephemeral --directory .state/delivery-check --origin http://127.0.0.1:8361 --project delivery-check
docker compose --project-name delivery-check --env-file .state/delivery-check/compose.env -f compose.yaml --profile monitoring up -d --build --wait
.venv/Scripts/python.exe scripts/smoke.py http://127.0.0.1:8361 --env-file .state/delivery-check/compose.env --password-file .state/delivery-check/smoke-password
.venv/Scripts/python.exe scripts/verify_delivery.py --project delivery-check --env-file .state/delivery-check/compose.env --password-file .state/delivery-check/smoke-password --url http://127.0.0.1:8361 --revision local --output .state/delivery-check/result.json
```

`--ephemeral` создаёт случайные тестовые credentials и приватный password-file только для smoke.
`verify_delivery.py` изменяет тестовые настройки/snapshot, restart сохраняет volume;
проверяет повторный вход, CSRF, persistence и реальный collector. Не применять к рабочей базе.

## API и мониторинг

После входа [Swagger](http://127.0.0.1:8080/api/v1/docs) и `/api/v1/openapi.json` доступны только с session cookie.
Основные endpoint contracts: [API](docs/api-contract.md), расчёт: [модель](docs/model.md),
[архитектура](docs/architecture.md), [план проверок](docs/test-plan.md).
Записи требуют session-bound CSRF token, точный Origin и для настроек текущий If-Match.
`/metrics` требует отдельный bearer token; session не даёт доступа. Token не даёт customer API.
Опциональный `--profile monitoring` запускает Prometheus с token-файлом Docker secret;
его UI не публикуется на хост. Настройка [authorization.credentials_file](https://prometheus.io/docs/prometheus/latest/configuration/configuration/#http_config).

## Границы поставки

CI подготовлен для locked install, product pytest, Docker/proxy smoke и deploy/rollback exercise.
Публикация GHCR ограничена push в `main`, пакет остаётся private. Автоматический CD остаётся выключен
(`DEPLOY_ENABLED=false`); эта локальная работа не разрешает push, публичный deploy, DNS или доступы.
Локальный Docker не доказывает CI, VPS, HTTPS или публичный browser E2E.
История старого инфраструктурного probe и текущие факты: [infrastructure](docs/infrastructure.md).
Предыдущий deploy runbook: [deployment](docs/deployment.md); публичный релиз продукта требует отдельной приёмки.

Дизайн Digital Ruins / Lost Signal: [правила](docs/design/README.md). `design/` содержит изолированные прототипы;
рабочие страницы продукта находятся в `web/`. Прежний `infra/probe` сохранён как исторический стенд и не запускается образом продукта.
