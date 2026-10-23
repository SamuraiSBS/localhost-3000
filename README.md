# localhost-3000

Репозиторий: [SamuraiSBS/localhost-3000](https://github.com/SamuraiSBS/localhost-3000). Локальная папка: `D:\localhost-3000`.

ТЗ получено: **анализатор рисков оттока** для маркетолога. Подготовлены разбор требований, аудит данных, исследование готовых решений и план MVP. **Реализация продукта ещё не начата**; исполняемый `infra/probe` остаётся временной проверкой инфраструктуры.

Начать работу: **[docs/START_HERE.md](docs/START_HERE.md)**. Затем [план реализации](docs/implementation-plan.md), [модель](docs/model.md), [API-контракт](docs/api-contract.md), [готовые компоненты](docs/reuse-research.md). Исходники организатора находятся в `материалы/`; условия OSS/AI/заранее подготовленного кода пока не установлены.

Автономная команда в чате проекта: **«Выполняй план MVP»**. [Описание](docs/EXECUTE_PLAN.md): оркестратор → бронирование → worker → независимый reviewer → принятие → следующий готовый P0 этап. Текущее исполнение хранится в локальном ledger, отдельно от планового backlog.

## Быстрый старт текущего probe

Требуются Python 3.13+ и Docker с Compose >= 2.24.4 (для production override). Текущий probe не требует pip/npm пакетов; зависимости будущего FastAPI-продукта ещё не установлены.

```powershell
python scripts/check.py
python infra/probe/server.py
# http://127.0.0.1:3000 — Ctrl+C для остановки
```

Контейнеры на Windows:

```powershell
if (!(Test-Path .env)) { Copy-Item .env.example .env }
./scripts/dev.ps1 start
python scripts/smoke.py http://127.0.0.1:8080
./scripts/dev.ps1 logs
./scripts/dev.ps1 stop
```

Linux/macOS: `bash scripts/dev.sh start|stop|logs|status|update|check`.
Страница: `http://127.0.0.1:8080`; health: `/healthz` (статус + revision). Локальные порты доступны только с этого компьютера.

На подготовленном компьютере порт задан в локальном `.env`: `http://127.0.0.1:3080` (8080 занят). Локальный `compose.override.yaml` также решает исчерпание Docker address pools, сохраняя сети других проектов; детали в `docs/infrastructure.md`.

## Работа команды

Короткая ветка от `main` → осмысленные коммиты → PR → зелёный `verify` → squash merge. Не коммитить `.env`, ключи, логи и состояние деплоя. Правила Codex: [AGENTS.md](AGENTS.md); навыки: `.agents/skills`.

## CI и деплой

CI проверяет Python/HTTP, shell-синтаксис, сборку Docker, Compose, проксирование, успешное обновление и автоматический/ручной откат. После успешного CI push в `main` публикует приватный GHCR-образ. CD использует digest образа и работает только при `DEPLOY_ENABLED=true` и настроенном окружении `production`.

Инструкции VPS, DNS, GitHub Secrets и отката: [docs/deployment.md](docs/deployment.md). Проверенное состояние и оставшиеся ограничения: [docs/infrastructure.md](docs/infrastructure.md).

## План конкурсного продукта

Разобраны [ТЗ](docs/challenge.md), [правила и неизвестные условия](docs/rules.md), [приёмка](docs/evaluation.md) и [реестр требований](docs/requirements.md). Минимальный путь: вход → рейтинг риска → причины/деньги клиента → изменение коэффициента или снимка → автоматический пересчёт → сохранение настроек после restart.

Выбран план FastAPI/Pydantic/sqlite3 с существующим native интерфейсом; [архитектура](docs/architecture.md), [машинный backlog](docs/backlog.json), [инструкция Codex](docs/agent-workflow.md). Это проектные решения, не запущенный MVP. В момент реализации заменить probe вместе с Docker, smoke и CI; их текущие проверки не подтверждают продукт.

Read-only аудит исходного набора:

```powershell
python scripts/profile_dataset.py
```

Данные синтетические:10 аккаунтов,30 услуг,100 транзакций, без истории оттока и меток для обучения. Ограничения и расхождения описаны в [data-audit](docs/data-audit.md); оригиналы сохраняются.

## Утверждённый дизайн

**Digital Ruins / Lost Signal** закреплён в [docs/design/README.md](docs/design/README.md) и инструкциях агентов. Перед UI-разработкой читать дизайн-документы; ТЗ определяет функциональность, визуальная идентичность сохраняется. Декоративные микроподписи запрещены, полезный текст — от 16 px.

Токены: `design/tokens.css`, базовые элементы: `design/foundation.css`. Главная `/` и страницы авторизации используют фото `photo_8_2026-10-22_21-11-47.png` с Bayer-дизерингом: прозрачный фон остаётся чёрным, камень — белым. На ПК изображение целиком помещается в viewport без обрезки, на узком экране кадр заполняет область. Заголовок `localhost3000` набран текстом, ссылка `CONNECT` использует Liquid Glass и тоже остаётся текстом. Локальная основа также содержит PP Neue Machina Inktrap, нейтральную оболочку и Prism Glass над собственной графикой. Утверждённая иерархия заголовков/текста — 32/24/16 px. Отдельный локальный просмотр без продуктовой логики:

```powershell
python -m http.server 3300 --bind 127.0.0.1 --directory design
# http://127.0.0.1:3300/ — главная сцена
# http://127.0.0.1:3300/preview.html — базовые элементы
```

Это визуальная основа, не выбор стека приложения и не замена probe. Runtime-пакеты не нужны. Подготовка ассетов и изолированные browser tests описаны в [design/tooling/README.md](design/tooling/README.md). Проверки и оставшиеся ограничения: [docs/design/VERIFICATION.md](docs/design/VERIFICATION.md).
