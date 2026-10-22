# Состояние инфраструктуры

Дата: 2026-10-22. Этот документ обновляется по фактическим проверкам.

## Подготовлено

Приватный репозиторий SamuraiSBS/localhost-3000, AGENTS.md, документация до ТЗ, четыре проектных навыка, временный HTTP probe, Dockerfile, local/production Compose, Caddy, health/revision, команды запуска/логов, release deploy и rollback, раздельные Actions CI/CD.

## Проверки

- Python syntax + HTTP page/health/revision/HEAD/404: пройдены локально (4 теста).
- Docker/Desktop: сборка и локальный Compose пройдены; app healthy, reverse proxy и smoke на `http://127.0.0.1:3080` успешны. Python и Caddy закреплены digest.
- GitHub CLI: реальный приватный репозиторий и admin-доступ подтверждены.
- GitHub MCP: get_repo 404, доступ коннектора к этому репозиторию не подтверждён.
- CI до переименования: реальные runs успешны, включая build, proxy smoke, обновление, автоматический и ручной откат. [Первый CI](https://github.com/SamuraiSBS/localhost-3000/actions/runs/39790558262), [проверка первого PR](https://github.com/SamuraiSBS/localhost-3000/actions/runs/39791434313).
- Main CI/GHCR до переименования: [run 39792016635](https://github.com/SamuraiSBS/localhost-3000/actions/runs/39792016635), commit `aa860ab73773d4a703256cd22fbb35068968e357`, image digest `sha256:3e22783f412d1800b094774fb84c60bf2afe7eef169f1b60f9ba34a33823aa0b`. Эти проверки подтверждают старый namespace.
- VPS/SSH/CD/DNS/HTTPS/публичная страница: не выполнены, VM отсутствует.
- Повторный публичный деплой с изменённым текстом: не выполнен.
- Страница проверена во встроенном браузере (узкая панель); проверка на отдельном широком viewport не выполнялась. Вторым изменением обновлён подзаголовок о состоянии стенда; это локальная проверка обновления, а не публичный CD.

## Зависимости от пользователя

Создать согласованную VM в Яндекс Облаке; предоставить IP, SSH-пользователя и локальный способ доступа. Уточнить доступ к DNS и свободен ли корневой домен `localhost-3000.ru`. Затем настроить scoped deploy-доступ и маршрут из Actions, секреты окружения, DNS и включить CD. Расходов и публичной публикации репозитория в рамках подготовки не выполняется.

DNS A на момент проверки: `localhost-3000.ru → 81.26.176.123`. Пользователь подтвердил, что корневой домен свободен для нового проекта. Не считать текущий адрес новым VPS; запись заменить после создания VM.

На этой машине все автоматические Docker address pools заняты существующими сетями. Они сохранены. Для стенда создан локальный gitignored `compose.override.yaml` с проверенной свободной подсетью `10.253.247.0/24`; обычные `docker compose` и dev-скрипты подхватывают его автоматически. На другой машине подсеть выбирать после проверки маршрутов; конфигурацию не копировать вслепую.

Порты 8080 и 18080 заняты другими контейнерами. Для этого компьютера в локальном `.env` выбран свободный `HTTP_PORT=3080`; пример для чистой машины сохраняет 8080.

GitHub: настроены squash merge, удаление слитых веток, environment `production`, repository variable `DEPLOY_ENABLED=false`. API защиты main вернул HTTP 403: на текущем тарифе для приватного репозитория требуется Pro. Видимость и тариф сохранены. PR-процесс описан, техническое enforcement защиты недоступно.

Environment `production` ограничен веткой main через deployment branch policy (API подтвердил настройку). GitHub Actions закреплены на официальных v7 commit SHA; Dependabot объединяет будущие обновления Actions в один PR. Локально actionlint и bash syntax checks пройдены.

## Границы готовности

Разработку после анализа ТЗ можно начинать на локальном стенде. Готовность публичного хостинга и полного CI/CD требует отдельной runtime-приёмки после появления VM. Docker/CI probe не подтверждает будущий продукт.

## Переименование проекта — 2026-10-22

Название проекта и локальной папки: `localhost-3000`. Репозиторий переименован с сохранением истории: https://github.com/SamuraiSBS/localhost-3000.git. Будущие релизы используют `/opt/localhost-3000`; Compose и локальный образ — `localhost-3000`.

Перечисленные выше CI/GHCR/Docker проверки выполнены до переименования. Старый digest не подтверждает наличие образа в новом GHCR namespace.

После переименования локально повторно пройдены: `python scripts/check.py` (4 HTTP-теста), `git diff --check`, actionlint, проверка local/production Compose, сборка `localhost-3000:local` и запуск через `scripts/dev.ps1 start`. App нового Compose-проекта healthy, proxy запущен; HTTP smoke прошёл. Smoke через `http://127.0.0.1:3080` и браузер подтвердили заголовок `localhost-3000 — Infrastructure Ready`.

PR [#6](https://github.com/SamuraiSBS/localhost-3000/pull/6) объединён squash-коммитом `fae89b218d1673bf6189f61261e4509e7814fc22` 2026-10-22. [PR CI run 39809750211](https://github.com/SamuraiSBS/localhost-3000/actions/runs/39809750211): verify успешно, включая offline checks, Docker build/proxy smoke и тест деплоя/отката.

После слияния [main CI run 39810198794](https://github.com/SamuraiSBS/localhost-3000/actions/runs/39810198794) завершился успешно: verify и publish прошли. Опубликованный образ: `ghcr.io/samuraisbs/localhost-3000@sha256:7466cc9bea467bd08922dc0f636dd81f1b11f87b84b5fc637e42c45a27870af8`. Publish проверил private visibility пакета и pull этого же digest с run-scoped `GITHUB_TOKEN`.

[CD run 39810672585](https://github.com/SamuraiSBS/localhost-3000/actions/runs/39810672585) пропущен; `DEPLOY_ENABLED=false`, VM нет. Это подтверждает CI и публикацию GHCR-образа, но не публичный деплой, DNS, HTTPS или серверную приёмку.
