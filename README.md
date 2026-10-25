# localhost-3000

Объяснимая аналитика риска непродления подписок и лаборатория для проверки решений до их применения.

**Стек:** FastAPI, SQLite, HTML/CSS и ES-модули без React и сборки основного интерфейса.

## Демо для проверяющего

1. Запустите приложение по инструкции ниже.
2. Откройте [http://127.0.0.1:8080/login](http://127.0.0.1:8080/login).
3. Нажмите **«Зайти на демо»** — логин и пароль для демо не нужны.
4. Пройдите [сценарий демонстрации](docs/demo.md).

Демо создаёт временную изолированную песочницу с учебными данными. Она не использует базу и настройки основного аккаунта.

## Быстрый запуск

Нужны Python 3.13, Docker Engine и Docker Compose. Выполняйте команды из корня репозитория.

### Windows / PowerShell

```powershell
python -m venv .venv
.venv/Scripts/python.exe -m pip install --require-hashes -r requirements-dev.txt
.venv/Scripts/python.exe scripts/configure_local.py
powershell -File scripts/dev.ps1 start
```

### Linux / macOS

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --require-hashes -r requirements-dev.txt
.venv/bin/python scripts/configure_local.py
bash scripts/dev.sh start
```

`configure_local.py` попросит задать локальные учётные данные администратора. Для демо они не используются. Настройки запуска сохраняются в `.state/local/`; не добавляйте этот каталог в Git.

## Что можно посмотреть

- Рейтинг клиентов, причины риска и ближайшие продления.
- Настройки модели и объяснение денежной оценки.
- Decision Lab: сценарии, чувствительность, очередь действий, календарь, preview импорта и replay экспортированного кейса.

Индекс риска помогает расставить приоритеты и не является статистической вероятностью оттока. Денежные оценки сценарные; API возвращает суммы в евроцентах.

## Дизайн и контракт

Визуальная идентичность и локальные правила интерфейса описаны в [дизайн-системе](docs/design/README.md). Контракт API доступен в [OpenAPI](openapi.json).
