# P1: локальный OIDC с Keycloak

Провайдер выбран пользователем: **локальный Keycloak в отдельном Compose-профиле**.
Это дополнение B13 / R-041 к принятому P0 и графику B12. Основной login/password
остаётся доступен. Публичный OIDC/deploy, CI и продуктивная конфигурация не заявляются.

## Запуск

Python 3.13 и Docker Compose. Из корня поставки:

```powershell
python -m venv .venv
.venv/Scripts/python.exe -m pip install --require-hashes -r requirements.txt
.venv/Scripts/python.exe scripts/configure_oidc.py --directory .state/oidc --origin http://127.0.0.1:8373 --provider-origin http://127.0.0.1:8390 --project p1-oidc
docker compose --project-name p1-oidc --env-file .state/oidc/compose.env -f compose.yaml -f compose.oidc.yaml --profile oidc up -d --build
```

Helper запрашивает обычный пароль панели без вывода. Секреты Keycloak генерируются
в отдельном защищённом каталоге; существующий каталог не перезаписывается.
Открыть `http://127.0.0.1:8373/login`. Для IdP используется `p1-marketer`;
его пароль находится только в локальном `P1_OIDC_PASSWORD` в созданном env.
Не передавать env/пароль в сдаваемый архив или чат. Первый старт Keycloak может
занимать несколько минут. Пока IdP запускается, обычный вход работает.

Профиль добавляет только loopback `8390`, отдельный volume `keycloak_data` и
официальный Keycloak 26.8.1 с digest
`sha256:a78d6b79572168cb25886b80a8b5b8d4ed4390743e3eecb810538683e209dd6b`.
`start-dev` предназначен только для этого локального сценария. Realm import
пропускает уже существующий realm: изменение callback в шаблоне не обновит
сохранённый realm автоматически. Не удалять volumes ради обновления.

Для обычного P0 запуска использовать только `compose.yaml`, без overlay.
Для OIDC включать **и** `compose.oidc.yaml`, **и** `--profile oidc`.
Остановка своего проекта без удаления данных:

```powershell
docker compose --project-name p1-oidc --env-file .state/oidc/compose.env -f compose.yaml -f compose.oidc.yaml --profile oidc stop
```

## Протокол и границы доступа

Authlib 1.9.1 (BSD-3-Clause), joserfc и HTTPX2 выполняют auth code flow / PKCE S256
и проверку подписанного RS256 ID token. Проверяются issuer, audience,
authorized party, expiry/issued-at, nonce и access-token hash, если он передан.
`nonce_supported=false` не отменяет обязательную проверку nonce. Разрешён только
точный subject из `OIDC_ALLOWED_SUBJECTS` у настроенного issuer. Это локальный
single-operator MVP: разрешённый subject получает существующую роль operator;
email/имя из IdP не дают полномочий и не меняют обязательный `/auth/me`.

State, nonce и verifier хранятся в ограниченном серверном хранилище на 256
pending flows с TTL 5 минут. Cookie `oidc_flow` — только случайная привязка
браузера, HttpOnly, SameSite=Lax, Path `/api/v1/auth/oidc`, Secure на HTTPS.
State атомарно одноразовый; неподходящий browser/kind/TTL не обменивает code.
Перезапуск процесса инвалидирует незавершённые flows; начать вход заново.
Сессия панели сохраняется в SQLite как hash opaque token; вход ротирует старую
сессию. ID/access/refresh tokens не сохраняются в JS, localStorage или cookie.

Дополнительные публичные маршруты — только точные GET
`/api/v1/auth/oidc/config`, `/start`, `/callback`, `/logout`, `/logout-return`.
Остальной API, Swagger и OpenAPI остаются закрыты сессией. Config содержит только
enabled/provider name. Discovery не выполняется при startup. Сеть имеет timeout
5 секунд, redirects запрещены, proxy environment не применяется. Issuer совпадает
с конфигурацией, browser endpoints используют публичный origin; token/JWKS могут
использовать только origin настроенного internal metadata URL.

Compose использует публичный issuer `http://127.0.0.1:8390/realms/localhost3000-p1`
и discovery `http://keycloak:8080/realms/localhost3000-p1/.well-known/openid-configuration`.
Фиксированный hostname и dynamic backchannel Keycloak дают внутренние token/JWKS
endpoints без отключения проверки публичного issuer.

## Выход и ошибки

`POST /api/v1/auth/logout` сохраняет **204 без тела**, Origin и CSRF. Сначала
отзывается локальная сессия. Для OIDC создаётся одноразовый browser-bound ticket
в заголовке `X-OIDC-Logout-URL`, ведущий только на локальный `/oidc/logout`.
UI проверяет формат перед переходом. Этот endpoint отправляет пользователя
на RP-initiated logout Keycloak с client_id, без ID token в URL. Keycloak просит
подтвердить Logout; затем одноразовый logout state проверяется на callback.
Повторное нажатие OIDC после завершения выхода требует credentials снова.

При недоступном IdP локальная сессия всё равно отозвана; сообщение честно
предупреждает, что SSO-сессия Keycloak могла сохраниться. Обычный logout не
обращается к IdP. Start/callback/logout responses имеют no-store и
Referrer-Policy: no-referrer. Ошибки фиксированные, без token/secret/traceback.

## Проверенные сценарии

Точные окружение, результаты, screenshot и ограничения записываются в
`artifacts/acceptance/b13-results.json`; пакет — `b13-submission.zip`.
Проверки на моках и локально подписанных unit tokens дополняют реальный браузерный
flow и не выдаются за взаимодействие с IdP. Полный check охватывает также P0/B12.

## Первоисточники

- [Authlib Starlette integration](https://docs.authlib.org/en/stable/oauth2/client/web/starlette.html)
- [Authlib HTTP client](https://docs.authlib.org/en/stable/oauth2/client/http/index.html)
- [Keycloak hostname/backchannel](https://www.keycloak.org/server/hostname)
- [Keycloak containers](https://www.keycloak.org/server/containers)
- [Keycloak realm import](https://www.keycloak.org/server/importExport)

Решение записано здесь как дополнение: исторический `docs/decisions.md` является
неизменяемым provenance snapshot принятой отдельной работы.
