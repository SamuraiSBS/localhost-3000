# Инструменты

На этой машине найдены Git, GitHub CLI, Node.js 24, Python 3.13, Docker Desktop/Compose и OpenSSH. Отдельные npm/pip зависимости probe не нужны.

- GitHub CLI: авторизован вне sandbox, репозиторий доступен с admin. В sandbox сеть блокировалась локальным proxy; не путать это с отозванным токеном.
- OAuth-сессия CLI не имеет read:packages: REST-чтение метаданных GHCR этим токеном возвращает 403. Pipeline использует собственный GITHUB_TOKEN с packages permissions; расширять постоянный пользовательский токен ради него не требуется.
- GitHub MCP: live get_repo вернул 404; доступ этого подключения к репозиторию не подтверждён. Для работы используется CLI. Новые токены не нужны.
- Browser automation: в сессии доступны browser tools; доступность конкретной страницы проверять отдельным вызовом. Это не установленный в CI Playwright.
- Документация: доступен web tool; новые MCP не нужны только ради чтения официальной документации.
- Сервер: SSH-клиент установлен; live SSH невозможен до появления VM/доступа. Yandex CLI в PATH не обнаружен. Наличие доступа к облаку не установлено.
- API и диагностика: Python urllib, curl, Docker logs, Actions logs. Sentry/внешний мониторинг пока не подключены: необходимость определяется продуктом.

Skills проекта лежат в `.agents/skills` и валидируются bundled skill-creator. Они используют проектные документы и команды, не заменяют доступные специализированные навыки UI, документов или браузера. Уже открытый чат может потребовать перезагрузки каталога навыков; файлы также можно явно читать по пути.

Источники: [OpenAI skills](https://learn.chatgpt.com/docs/build-skills), [Compose health ordering](https://docs.docker.com/compose/how-tos/startup-order/), [GitHub environments](https://docs.github.com/en/actions/concepts/workflows-and-actions/deployment-environments), [Caddy HTTPS](https://caddyserver.com/docs/automatic-https).
