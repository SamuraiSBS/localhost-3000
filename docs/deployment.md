# Развёртывание: Яндекс Облако

VM пока не создана. Домен от пользователя: `localhost-3000.ru`. Ни адрес сервера, ни стоимость, ни DNS-доступ не предполагаются.

## Создание VM пользователем

В [консоли](https://console.yandex.cloud/) выбрать каталог → Compute Cloud → Виртуальные машины → Создать. Для probe и небольшого MVP стартовая конфигурация: Ubuntu 24.04 LTS x86_64, 2 vCPU, 4 ГБ RAM, SSD 30 ГБ, без GPU и прерываемости. Это инженерная отправная точка, не размерность неизвестного продукта. Проверить калькулятор и цену IPv4/диска перед созданием. Статический публичный IP позволит не менять DNS при остановках.

В разделе доступа использовать SSH-ключ (или OS Login при уже настроенном процессе); приватный ключ хранить локально. Передать Codex публичный IP, имя пользователя, SSH-алиас/путь к локальному ключу или агенту. Приватный ключ/пароль в чат не отправлять.

Создать отдельную security group: вход TCP 80/443 и UDP 443 для сайта; TCP 22 только от административного IP и согласованного пути CI. Исходящие DNS/HTTPS должны работать. Не публиковать 3000, 8080 и Docker API. Docker published ports могут обходить UFW, поэтому cloud security group обязательна.

Для GitHub-hosted runner нужны согласованный SSH ingress (диапазоны runner меняются) либо VPN/отдельный runner в сети. Скрипт bootstrap открывает SSH только для ADMIN_CIDR: CD без дополнительного маршрута не заработает. Не размещать постоянный self-hosted runner для недоверенных PR на production VM.

## Bootstrap выделенного сервера

Скопировать scripts/bootstrap-ubuntu.sh и **публичный** deploy-ключ на VM. Проверить fingerprint хоста через доверенную консоль. На сервере:

```bash
sudo bash bootstrap-ubuntu.sh ADMIN_PUBLIC_IP/32 /path/to/deploy.pub
```

Подставлять реальные значения. Скрипт устанавливает Docker из официального apt-репозитория, создаёт deploy-пользователя и `/opt/lazyum`, включает Docker при загрузке и настраивает UFW. Сначала проверить вход deploy-ключом во второй сессии. Не менять существующую production VM этим скриптом без проверки её служб и правил доступа.

Под пользователем deploy создать `/opt/lazyum/.env` из `.env.production.example`, `chmod 600`. Для первого теста без домена использовать `SITE_ADDRESS=:80`, публичный URL `http://REAL_IP`. После DNS переключить на домен.

## DNS и TLS

У DNS-провайдера добавить/обновить A для `localhost-3000.ru` на реальный публичный IPv4 VM. AAAA добавлять только при работающем IPv6; удалить/исправить мешающую AAAA после проверки. Перед изменением существующей записи проверить, какой сервис уже использует домен. Если корневой домен занят, согласовать поддомен.

Убедиться, что DNS уже указывает на VM, 80/443 доступны извне, сертификаты не запрещены CAA. В `.env` сервера: `SITE_ADDRESS=localhost-3000.ru`. GitHub `PUBLIC_URL=https://localhost-3000.ru`. Caddy получает и обновляет сертификат автоматически, том `caddy_data` сохранять. `curl`/smoke выполняются без отключения TLS-проверки.

## GitHub Actions

Settings → Environments → New environment → `production`, ограничить веткой `main`. Доступность protection rules зависит от тарифа приватного репозитория; платный тариф автоматически не менять.

Settings → Secrets and variables → Actions, переменные (repository vars, кроме явно указанных environment vars):

| Имя | Значение |
| --- | --- |
| `DEPLOY_ENABLED` | `true` только после готовности сервера, доступа и DNS |
| `DEPLOY_HOST` | Реальный IP/hostname VM (environment production) |
| `DEPLOY_USER` | `deploy` (environment production) |
| `DEPLOY_PORT` | `22` либо согласованный порт (environment production) |
| `PUBLIC_URL` | `https://localhost-3000.ru` или временно `http://REAL_IP` (environment production) |

Secrets окружения production:

| Имя | Содержимое |
| --- | --- |
| `DEPLOY_SSH_KEY` | Отдельный приватный deploy-ключ, загрузить непосредственно через GitHub UI/CLI, не в чат |
| `DEPLOY_KNOWN_HOSTS` | Строка known_hosts, fingerprint которой сверили через доверенную консоль VM; для нестандартного порта формат `[HOST]:PORT` |

`GITHUB_TOKEN` создаётся автоматически на запуск. Publish job получает packages:write, CD — packages:read. Приватность GHCR-пакета не менять. Если GHCR выдаёт отказ, Package settings → Manage Actions access: дать этому репозиторию доступ; не выдавать постоянный широкий PAT.

CI проверяет PR без deployment secrets. Успешный CI push `main` публикует digest-артефакт; CD принимает только этот доверенный run, отклоняет устаревший main, передаёт registry token по SSH stdin и удаляет временный Docker config. SSH host checking включён. В PR произвольные workflow_run-артефакты не исполняются.

## Эксплуатация и откат

Релизы: `/opt/lazyum/releases/SHA-RUN_ID`. `current`/`previous` — симлинки принятых версий. Подготовленный image digest и предыдущие образы не удалять до окончания хакатона.

```bash
cd /opt/lazyum/current
docker compose --project-name lazyum-autumn-hack --env-file /opt/lazyum/.env --env-file image.env -f compose.yaml -f compose.production.yaml ps
docker compose --project-name lazyum-autumn-hack --env-file /opt/lazyum/.env --env-file image.env -f compose.yaml -f compose.production.yaml logs --tail 100 -f
bash scripts/rollback.sh /opt/lazyum
```

deploy.sh сериализует деплои через flock, подтягивает образ до изменения служб, проверяет контейнеры и exact revision через внешний URL. При ошибке возвращает предыдущие image + compose + proxy config и проверяет их. Ошибка первого деплоя останавливает неработающий стенд; прежней версии тогда ещё нет. Если сам откат неуспешен, скрипт возвращает ошибку и требует диагностики. Данные БД отсутствуют; после ТЗ нужны согласованные backup/restore и backward-compatible миграции. Caddy-тома при обычной остановке не удалять.

После включения CD нужен реальный полный прогон: push → CI → GHCR → CD → HTTPS → новая видимая строка отдельным коммитом → повторная проверка exact SHA и текста. До этого публичный деплой не считается принятым.

Источники: [Linux VM](https://yandex.cloud/ru/docs/compute/quickstart/quick-create-linux), [Docker Ubuntu и firewall](https://docs.docker.com/engine/install/ubuntu/), [Caddy automatic HTTPS](https://caddyserver.com/docs/automatic-https).
