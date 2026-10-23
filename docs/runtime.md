# Локальный runtime MVP

B01 — каркас FastAPI и строгий входной DTO; это ещё не рабочая панель, auth,
модель, persistence или замена Docker probe. Публичен только `/healthz` без
клиентских данных. Swagger/OpenAPI отключены до защищённой реализации B08.

Python 3.13. Из корня проекта в PowerShell:

```powershell
python -m venv .venv
.venv/Scripts/python.exe -m pip install --require-hashes -r requirements.txt
.venv/Scripts/python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8311 --workers 1
```

Из другого каталога добавить `--app-dir D:/localhost-3000`. Seed определяется
относительно `app/config.py`, а не текущего каталога. При старте читается
`материалы/data/data.json`, исходный файл не меняется. Невалидный или отсутствующий
seed останавливает старт с диагностикой процесса; в HTTP traceback не публикуется.
`GET http://127.0.0.1:8311/healthz` возвращает `status=ok`,
`stage=runtime-scaffold`. Это проверка каркаса, не готовности storage или MVP.

Тесты:

```powershell
.venv/Scripts/python.exe -m pip install --require-hashes -r requirements-dev.txt
.venv/Scripts/python.exe -m pip check
.venv/Scripts/python.exe -m pytest tests/test_contracts.py -q -p no:cacheprovider
python scripts/check.py
git diff --check
```

`requirements.txt` и `requirements-dev.txt` фиксируют точные версии и SHA-256
runtime и dev зависимостей, включая транзитивные. Dev lock включает runtime pins;
`pip install --require-hashes` проверяет скачанные артефакты. В `.in` записаны
только прямые зависимости. Перегенерация — осознанное обновление, после него
обязательны install, `pip check`, product tests и проверка старта:

```powershell
.venv/Scripts/python.exe -m pip install pip-tools==7.6.2
.venv/Scripts/python.exe -m piptools compile --strip-extras --resolver=backtracking --output-file=requirements.txt requirements.in
.venv/Scripts/python.exe -m piptools compile --strip-extras --resolver=backtracking --output-file=requirements-dev.txt requirements-dev.in
.venv/Scripts/python.exe scripts/lock_hashes.py requirements.txt requirements-dev.txt
```

`lock_hashes.py` получает SHA-256 всех неотозванных артефактов каждой зафиксированной
версии из официального PyPI JSON API. Это включает Windows/Linux wheels. Метаданные
полностью загружаются до записи lock; последующий `--require-hashes` проверяет
фактические скачанные артефакты. Такой способ выбран после диагностики
`pip-compile --generate-hashes`: он скачивал wheels всех платформ для хеширования,
включая неиспользуемые iOS/macOS. Hash metadata не заменяют install/build проверку
конкретной платформы. Повторный запуск скрипта с теми же release metadata идемпотентен.

Tooling генерации lock не входит в runtime. Windows install/start и hash validation
не доказывают Linux image build, CI, VPS, HTTPS или E2E. Их проверяет B09/B10.
На проверенной связке Starlette выдаёт предупреждение об устаревшем HTTPX
TestClient. Текущие проверки проходят; переход на его новый клиент потребует
отдельного обновления выбранного test tooling. Параметр `-p no:cacheprovider`
позволяет прогонять этот набор без записи временного pytest cache.

## Входы следующих срезов

`app.contracts` экспортирует `SourceSnapshot`, три `*Source`, `load_source(path)`,
`MAX_MONEY_CENTS`, `MAX_SOURCE_ID` и `checked_money(int)` для контроля вычисленных
денежных сумм перед публикацией. DTO сохраняет camelCase и неизвестные поля,
валидирует связи/уникальность и RFC3339, приводит даты к UTC. Канонический JSON
сортирует ключи, сохраняет порядок массивов, сериализует даты с `Z`. Это нормализация
представления в памяти, не исправление исходных бизнес-данных или upstream файла.
Суммы корзин/денежных рисков зависят от расчёта и проверяются B02 через
`checked_money`, а не предположением по сумме всех исторических транзакций.

`ModelSettings` содержит `model_version=rules-v1`, `horizon_days`,
`thresholds.medium/high`, `weights`. Вычисления получают Decimal; JSON настроек
содержит числа. `SettingsPatch.apply(current)` объединяет частичные weights/thresholds и
валидирует полный результат, не меняя прежний объект. Registry v1 содержит четыре
ID; расширение registry и миграция сохранённых настроек принадлежат B02/следующим
задачам. Никакого обучения и истории событий B01 не создаёт.

`RuntimeConfig.from_env()` читает только явные имена: `SOURCE_SNAPSHOT_PATH`,
`DATA_DIR`, `APP_LOGIN`, `APP_PASSWORD_HASH`, `METRICS_TOKEN`, `PUBLIC_ORIGIN`.
Секреты отсутствуют в дефолтах и repr. Проверка `require_credentials()` подготовлена
для старта защищённого продукта B04; health-only каркас пока не требует credentials.
Локальный data dir по умолчанию `.state`, лимит snapshot 10 MiB, session TTL 8 часов.
Не хранить локальные значения секретов в коммитах или командах отчётов.
