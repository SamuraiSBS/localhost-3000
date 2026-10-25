# Развёртывание: Timeweb Cloud

Для размещения выбран Timeweb Cloud. VM, Timeweb Firewall и bootstrap настроены. Ручной деплой, корневая DNS-запись и HTTPS для `localhost-3000.ru` проверены. Автоматический CD оставлен выключенным: безопасный маршрут через Tailscale ещё не настроен. Публично работает инфраструктурный probe, а не продуктовый MVP.

## Текущий сервер — 2026-10-22

В панели создан и отображается как «В сети» сервер `Localhost3000`, ID `9400609`: Cloud MSK Ultra 30, Ubuntu 24.04, 1 × 3.3 ГГц, 2 ГБ RAM, 30 ГБ NVMe, IPv4 `185.42.14.141`. При заказе форма показывала 3 000 ₽/месяц без бэкапа. Группа `localhost3000-allowlist` привязана только к этой VM: входящие TCP 22 с административного `/32`, TCP 80/443 и UDP 443 из Интернета; остальной входящий трафик блокируется, исходящих правил нет. Административный IP в репозитории не хранить. Root и `deploy` доступны по SSH-ключу; fingerprint сервера сверили через консоль Timeweb. Пароль и приватный ключ не копировались.

## Ранее рассмотренная конфигурация

Для временного probe предлагается небольшой стартовый шаг, близкий к прежней инженерной оценке: Москва, линейка Premium 3.3 ГГц, тариф MSK 50 (2 vCPU, 4 ГБ RAM, 50 ГБ NVMe), Ubuntu 24.04 LTS x86_64 без Marketplace-приложений. Это конфигурация инфраструктурного probe, а не оценка будущего продукта.

До заказа рассматривался Premium MSK 50 в Москве: 3 640 ₽/месяц за VM и 200 ₽/месяц за IPv4; вариант без бэкапа составлял 3 840 ₽/месяц. В MSK-1 тогда не было свободных Premium-ресурсов, и этот предзаказ не отправлялся. Позже была создана другая конфигурация — её текущие параметры приведены выше. Для Ultra MSK 50 форма показывала 5 280 ₽/месяц за сервер и 5 480 ₽/месяц с IPv4 до опций. Старую оценку 1 080 + 150 ₽/месяц не использовать.

Источники: [тарифы VPS/VDS](https://timeweb.cloud/services/vps-ubuntu), [создание облачного сервера](https://timeweb.cloud/docs/cloud-servers/manage-servers/create-server), [публичные IP](https://timeweb.cloud/docs/public-ip).

## Первичная защита созданной VM

Административный IPv4/CIDR для SSH пользователь сообщил; сохранять его в репозитории нельзя. Пользователь сверил fingerprint SSH-хоста через консоль Timeweb. Вход root и отдельный вход `deploy` по пользовательскому ключу проверены. Root-пароль и приватный ключ не раскрывались и не копировались.

Группа `localhost3000-allowlist` уже создана и привязана только к VM `9400609`. Проверенные входящие правила: TCP 22 только с сообщённого административного `/32`; TCP 80 и TCP 443 из Интернета; UDP 443 для HTTP/3. Не открывать 3000, 8080 или Docker API. Исходящих правил нет: у Timeweb allowlist исходящие правила включают отдельную фильтрацию всего остального исходящего трафика. Дополнительно настроить UFW через `scripts/bootstrap-ubuntu.sh`.

Автоматический CD пока не настроен. Для него в проектном плане предусмотрен Tailscale SSH: GitHub Actions получает краткоживущий узел через OIDC, а tag-политика разрешает SSH только на `deploy` этой VM. Tailnet и federated identity не настроены. После настройки этого маршрута открыть SSH на `tailscale0` в UFW и выполнить `tailscale set --ssh`. Не использовать личный tailnet с другими устройствами, пока не проверены и не ограничены его ACL. Постоянный self-hosted runner на production VM не устанавливать.

## Bootstrap выделенного сервера

Для VM `9400609` сверить IP и fingerprint SSH-хоста в панели Timeweb. Root-пароль не копировать и не передавать в чат. После настройки ограниченного Timeweb Firewall войти на VM из доверенной административной сети, передать скрипт `scripts/bootstrap-ubuntu.sh` и выполнить от root:

```bash
bash bootstrap-ubuntu.sh ADMIN_PUBLIC_IP/32 /path/to/deploy.pub
```

Вместо `ADMIN_PUBLIC_IP/32` подставить административный CIDR, а в `PUBLIC_KEY` передать публичный ключ. Скрипт принимает Ubuntu 22.04/24.04, устанавливает Docker из официального apt-репозитория, создаёт `deploy`, каталог `/opt/localhost-3000` и ограничивает публичный SSH этим CIDR. Опция `--tailscale-ssh` не добавляет постоянный SSH-ключ; её использовать только при настройке Tailscale-доступа.

Для VM `9400609` 2026-10-22 скрипт выполнен от root с пользовательским публичным ключом. Проверено: Docker активен, `docker compose version` — `v5.6.1`, `deploy` входит в группу Docker, UFW включён и разрешает SSH с административного `/32`, TCP 80/443 и UDP 443. Отдельный вход `deploy` по SSH-ключу успешен. Сам ключ, пароль и административный IP в документ не включать.

Для будущего Tailscale-маршрута после установки Tailscale и входа VM в tailnet:

```bash
sudo ufw allow in on tailscale0 to any port 22 proto tcp
sudo tailscale set --ssh
```

При включении Tailscale сначала проверить вход `deploy` по имени VM `*.ts.net`, сохранив доступ администратора по публичному `/32`. Не убирать административное правило, пока Tailscale-соединение не проверено.

Под пользователем `deploy` хранить `/opt/localhost-3000/.env` с правами `chmod 600`. На первом запуске без DNS использовать `SITE_ADDRESS=:80` и `PUBLIC_URL=http://REAL_IP`; после проверки домена задано `SITE_ADDRESS=localhost-3000.ru`.

## DNS и TLS

После проверки сервера по IP корневая A-запись `localhost-3000.ru` обновлена в Timeweb с `81.26.176.123` на `185.42.14.141`, TTL 600 секунд. Все четыре авторитетных NS и публичный DNS вернули новый адрес. AAAA не обнаружена; мешающей CAA-записи не найдено.

В Caddy задан `SITE_ADDRESS=localhost-3000.ru`; сохранённый том `caddy_data` не удалять. Проверено в браузере: `https://localhost-3000.ru/healthz` отвечает с текущим SHA, а HTTP перенаправляет на HTTPS. Для будущего CD использовать `PUBLIC_URL=https://localhost-3000.ru`; настройка GitHub Actions пока отключена.

## GitHub Actions

Использовать environment `production`, ограниченный `main`. GitHub CLI подтвердил repository variable `DEPLOY_ENABLED=false`; в ходе настройки её не меняли. Предыдущее чтение также подтвердило отсутствие остальных repository variables/secrets и значений в environment `production`; secrets не читали. По просьбе пользователя автоматический CD пока оставить выключенным. Вернуться к нему после настройки и проверки ограниченного Tailscale SSH-маршрута и OIDC.

Repository variables:

| Имя | Значение |
| --- | --- |
| `DEPLOY_ENABLED` | `true` только после серверной, сетевой и публичной приёмки |
| `DEPLOY_HOST` | MagicDNS имя VM с суффиксом `.ts.net` |
| `DEPLOY_USER` | `deploy` |
| `PUBLIC_URL` | `https://localhost-3000.ru` |

Secrets environment `production`:

| Имя | Содержимое |
| --- | --- |
| `TS_OAUTH_CLIENT_ID` | Client ID ограниченной Tailscale federated identity |
| `TS_AUDIENCE` | Audience этой federated identity |

В Tailscale federated identity ограничить issuer `https://token.actions.githubusercontent.com`, subject `repo:SamuraiSBS@102793438/localhost-3000@1429783568:environment:production`, ветку `refs/heads/main`, событие `workflow_run`, workflow `CD`, scope `auth_keys` и tag `tag:localhost3000-ci`. Числовые ID подтверждены GitHub API: репозиторий создан 2026-10-22 и использует immutable OIDC subject. В tailnet создать узел-тег `tag:localhost3000-prod`; сетевой и SSH-доступ от `tag:localhost3000-ci` разрешить только к `tag:localhost3000-prod` на TCP 22 и только Linux-пользователю `deploy`. Использовать tailnet без других устройств либо сначала проверить и сузить существующие правила доступа.

Не читать и не выводить существующие secrets. `GITHUB_TOKEN` создаётся на каждый workflow run; publish job получает `packages:write`, CD — `packages:read`. GHCR оставить приватным. CD получает только `id-token:write` для Tailscale OIDC; его Tailscale-узел временный. Workflow передаёт деплой по Tailscale SSH с проверкой host key, не используя постоянный SSH-ключ. Перед публичной приёмкой подтвердить, что новая версия workflow объединена в `main`.

## Эксплуатация и приёмка

Релизы: `/opt/localhost-3000/releases/SHA-RUN_ID`. `current`/`previous` — указатели принятых версий. Образы отката, релизные каталоги и persistent volumes не удалять.

```bash
cd /opt/localhost-3000/current
docker compose --project-name localhost-3000 --env-file /opt/localhost-3000/.env --env-file image.env -f compose.yaml -f compose.production.yaml ps
docker compose --project-name localhost-3000 --env-file /opt/localhost-3000/.env --env-file image.env -f compose.yaml -f compose.production.yaml logs --tail 100 -f
bash scripts/rollback.sh /opt/localhost-3000
```

Ручной публичный health smoke по HTTPS и точный SHA проверены. Полный прогон автоматического CD, отдельный деплой с изменённым видимым текстом и контролируемый rollback не выполнялись. Главная страница пока содержит старую фразу о том, что публичный деплой ждёт VM в Yandex Cloud; исправить её перед публичной демонстрацией. Это инфраструктурный probe, не готовый продукт.

Документация провайдера: [Firewall](https://timeweb.cloud/docs/firewall/upravlenie-fajrvolom), [сеть сервера](https://timeweb.cloud/docs/cloud-servers/manage-servers/network), [Caddy Automatic HTTPS](https://caddyserver.com/docs/automatic-https).

## Первая ручная публикация — 2026-10-22

- Успешный main CI run 39811602734 для коммита c333ad0d4a0a9f05cd6b531e5a352d5495c28aaa опубликовал приватный образ ghcr.io/samuraisbs/localhost-3000 с digest sha256:36607d1bf76c0444a4520804f1e5183c2cf6903b551a22615ccee889217580fa. В CI также прошли сборка, smoke и упражнение deploy/rollback.
- Архив runtime-файлов этого коммита передан в /opt/localhost-3000/releases/c333ad0d4a0a9f05cd6b531e5a352d5495c28aaa-39811602734; SHA-256 переданного архива проверен. На VM создан /opt/localhost-3000/.env с SITE_ADDRESS=:80 и правами 600 для проверки по IPv4.
- Образ не скачан: GitHub CLI авторизован, но его текущий токен не имеет read:packages; GHCR ответил permission_denied: The token provided does not match expected scopes. Артефакт CI deployment-image содержит только digest и не включает образ. Временные Docker credentials и скрипт pull удалены; контейнеры не запущены.
- A-записи localhost-3000.ru и www.localhost-3000.ru по-прежнему указывают на старый адрес 81.26.176.123. DNS не менялся; публичная HTTP-проверка, HTTPS и приёмка не выполнены. Следующий шаг — выдать CLI-токену read:packages и повторить pull точного digest, затем пройти HTTP smoke по IP.
