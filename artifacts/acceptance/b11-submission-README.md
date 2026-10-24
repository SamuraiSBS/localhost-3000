# Пакет сдачи localhost-3000 — B11

Это локальный кандидат ZIP-пакета для передачи организаторам. Он не загружался и не отправлялся. Соседний файл `b11-package-manifest.json` перечисляет точное содержимое архива и SHA-256; manifest хранится снаружи, чтобы не создавать самоссылку на ZIP-хеш.

## Запуск

Нужны Python 3.13 и Docker Engine с Compose >= 2.24.4. Распакуйте архив и выполняйте команды из его корня. Продуктовый сценарий начинается на `/login`; `/` — отдельная демонстрационная/design-оболочка.

Windows PowerShell:

```powershell
python -m venv .venv
.venv/Scripts/python.exe -m pip install --require-hashes -r requirements-dev.txt
.venv/Scripts/python.exe scripts/configure_local.py
powershell -File scripts/dev.ps1 start
```

Откройте `http://127.0.0.1:8080/login` и войдите с логином и паролем, которые вы задали локально в `configure_local.py`. Архив не содержит готовых credentials.

Linux/macOS:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install --require-hashes -r requirements-dev.txt
.venv/bin/python scripts/configure_local.py
bash scripts/dev.sh start
```

`configure_local.py` создаёт приватную локальную конфигурацию в `.state/local/compose.env`; её не следует добавлять в архив или публиковать. `stop` сохраняет named volume. Не используйте `down -v`, если требуется сохранить настройки.

## Что собрано

- FastAPI/Python 3.13 с REST API `/api/v1`, SQLite в Docker volume, Argon2-паролем, серверными сессиями и CSRF-защитой.
- Native HTML, CSS и ES modules: формы входа, рейтинг клиентов, карточка с объяснением, настройки и загрузка полного JSON snapshot.
- Compose с Caddy reverse proxy; опциональный профиль `monitoring` поднимает Prometheus. Метрики доступны по `/metrics` только с collector token.
- Swagger UI и актуальная для запущенного приложения OpenAPI-схема доступны после входа на `/api/v1/docs` и `/api/v1/openapi.json`.
- Разрешённый безопасный seed `data/seed.json` для локального запуска. Архив не содержит исходный набор организатора, пользовательские root-фотографии, `.env`, токены, пароли, `.state`, `.agent-state` или весь каталог `artifacts`.
- В архив отдельно включены `artifacts/acceptance/b10-results.json`, `b10-wide.png` и `b10-narrow.png`, только чтобы сохранить ссылки и доказательства B10, явно помеченные в `docs/demo.md` как исторические. Они не подтверждают свежий B11 сценарий.

Исполняемый код — в `app/`, `web/` и runtime-файлах `design/`; точные пути и контрольные суммы перечислены во внешнем manifest. Добавляя признак модели в исходный репозиторий, согласованно обновите вычисление/registry в `app/domain/features.py`, веса и объяснения в `app/domain/scoring.py`, DTO/settings validation в `app/contracts.py`, UI редактора в `web/settings.html` и `web/assets/settings.js`, а также соответствующие исходные тесты. Успех unit/API-тестов сам по себе не заменяет браузерную проверку полного пути.

## Набор тестов в ZIP

В delivery ZIP нет автоматизированных тестов: каталог `tests/`, `scripts/check.py` и `scripts/check_design.py` исключены. Причина — исходный `tests/integration/test_web.py` ожидает HTTP 200 от URL proprietary WOFF2, тогда как этот архив намеренно не содержит шрифт и ожидаемый ответ для этого ZIP — 404 с CSS fallback. Полный test suite для B11 не запускался. Состав тестов архива указан в companion manifest как пустой (`paths: []`); выборочный `TestClient` smoke извлечённого ASGI-приложения отдельно описан в `b11-results.json` и не является полным suite, запуском Docker/Compose или браузерной приёмкой.

## As-built модель и архитектура

В этой версии приложение действительно исполняет `rules-v1`; это не обученная churn-модель и score не является вероятностью ухода. Для оцениваемой услуги суммируются вклады доступных признаков `feature × weight`, результат ограничивается диапазоном 0–100. У клиента берётся максимальный score услуг в горизонте. Денежный риск считается для каждой услуги как `цена продления × score / 100`, округляется до евроцента и складывается; это ориентир стоимости под риском, а не прогноз выручки.

В B11 репетиции горизонт был 30 дней; исходные веса для контрольного клиента: proximity=20, manual renewal=25, funding gap=35, payment wait=20, high renewal cost=10. Карточка M02 объяснила `1×20 + 1×25 + 0×35 + 0×20 + 1×10 = 55`. При цене €10 и балансе €10 дефицит равен €0; оценка €5.50. После ручного изменения `manual_renewal` с 25 на 50 получились 80/100, High и €8.00.

Фактические модули и потоки следует считать источником истины. Замороженные проектные документы `docs/model.md`, `docs/architecture.md`, `docs/api-contract.md`, `docs/requirements.md`, `docs/evaluation.md` и `docs/test-plan.md` включены без правок; их формулировки планового этапа не были переписаны в B11. Этот файл содержит краткое as-built описание для сдачи.

## Свежая B11 репетиция и ограничения

Реальный локальный endpoint: `http://127.0.0.1:8360`; свежий `/healthz` вернул `status=ok`, `stage=product`, revision `eaed00108459c4f8f66199676d68e4c689d9518f+extension-1696cafdd1ab+etagfix-09be7d48e502`. В первой вкладке открыт M02: `55/100`, «Средний», €5.50, data=10/settings=7/analysis=a-24. Во второй вкладке открыт тот же список. Изменение `manual_renewal` 25→50 обновило первую вкладку до settings=8/analysis=a-25 и M02 80/100, «Высокий», €8.00; вторая вкладка получила то же состояние без ручного reload. Сброс через UI к manual=25 и extension=10 дал обеим вкладкам data=10/settings=9/analysis=a-26 и M02 55/100, €5.50.

Свежий B11 тест outage/retry — `not_run`: остановка task-owned app через Docker API была отклонена Windows ACL. Исторический A-12 B10 описан отдельно в `docs/demo.md` и не подтверждает B11 fallback. B10 wide/narrow viewport captures — отдельное историческое evidence, не скриншоты полной B11 поездки. Viewport B11 отдельно не измерялся; browser console и network panel не инспектировались; Docker image metadata и полный clean-room Compose build в B11 не проверялись.

Proprietary PP Neue Machina Inktrap WOFF2 намеренно исключён по прямому решению пользователя. CSS объявляет системный fallback Arial; функциональность приложения сохраняется, но типографика отличается. В этом delivery ZIP запрос `/design/assets/fonts/pp-neue-machina-inktrap-variable.woff2` ожидаемо возвращает 404 из-за отсутствующего файла; это характеристика состава архива вместе с CSS fallback, а не свидетельство успешного полного тестового suite. Фото, используемое как фон формы входа, также исключено вместе с root photo uploads; соответствующий фон в архив не входит. Q-005 о более широких правах распространения остаётся открытым.

Проверка этого пакета должна отдельно различать выборочный запуск приложения, тесты, Docker/CI и публикацию. Локальный запуск не означает публичную доступность. Полный suite для B11 не запускался; тесты и оба `check`-скрипта не входят в архив. Предыдущий полный запуск source checkout в B10 завершился 194 passed и 8 pytest setup errors `PermissionError [WinError 5]` при обходе временного каталога. В B11 из распакованного ZIP выполнен только ограниченный Starlette `TestClient`/ASGI route smoke с безопасным seed и временной конфигурацией; он не запускал server process, Docker/Compose или браузер. В архиве `scripts/check_design.py` не поставляется: штатная проверка дизайна ожидает намеренно исключённые proprietary font metadata/WOFF2 и производные wordmark. Чистая Docker/Compose-сборка не запускалась. Пакет не публиковался и не отправлялся.
