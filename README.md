# localhost-3000

Репозиторий: [SamuraiSBS/localhost-3000](https://github.com/SamuraiSBS/localhost-3000). Локальная папка: `D:\localhost-3000`.

Подготовка к хакатону до публикации ТЗ: инфраструктура и утверждённая визуальная система. Продукт и его стек пока не выбраны. `infra/probe` — временное приложение для проверки инфраструктуры.

## Быстрый старт

Требуются Python 3.13+ и Docker с Compose >= 2.24.4 (для production override). Приложение не требует pip/npm пакетов.

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

## После публикации ТЗ

Сохранить ТЗ в `docs/challenge.md`, правила в `docs/rules.md`, критерии в `docs/evaluation.md`. Проверить разрешение на подготовленный код и AI, выбрать MVP/стек и заполнить архитектуру/план. Заменить probe вместе с контрактами Docker, smoke и CI. Предметных требований сейчас нет.

## Утверждённый дизайн

**Digital Ruins / Lost Signal** закреплён в [docs/design/README.md](docs/design/README.md) и инструкциях агентов. Перед UI-разработкой читать дизайн-документы; ТЗ определяет функциональность, визуальная идентичность сохраняется. Декоративные микроподписи запрещены, полезный текст — от 16 px.

Токены: `design/tokens.css`, базовые элементы: `design/foundation.css`. Реализованы главная сцена с тремя колоннами, консольный SVG-wordmark, локальный JetBrains Mono, вход в нейтральную оболочку и Prism Glass над собственной графикой. Отдельный локальный просмотр без продуктовой логики:

```powershell
python -m http.server 3300 --bind 127.0.0.1 --directory design
# http://127.0.0.1:3300/ — главная сцена
# http://127.0.0.1:3300/preview.html — базовые элементы
```

Это визуальная основа, не выбор стека приложения и не замена probe. Runtime-пакеты не нужны. Подготовка ассетов и изолированные browser tests описаны в [design/tooling/README.md](design/tooling/README.md). Проверки и оставшиеся ограничения: [docs/design/VERIFICATION.md](docs/design/VERIFICATION.md).
