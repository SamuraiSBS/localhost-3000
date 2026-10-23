# Что переиспользовать для анализатора риска оттока

Дата исследования: **2026-10-23**. Проверены официальные документации, исходные репозитории и их файлы лицензий. Это исследование и рекомендация для реализации: пакеты не устанавливались, репозитории не клонировались, совместный запуск компонентов не проверялся. Версии в этом документе не являются dependency lock.

Основание: [ТЗ](../материалы/ТЗ.pdf), [требования](challenge.md), [правила](rules.md), [критерии](evaluation.md), [дизайн](design/README.md). Допустимость готового кода, AI и сторонних компонентов для конкурсной сдачи должна быть подтверждена по правилам организаторов; наличие permissive-лицензии само по себе этого не подтверждает. Исследование и подготовка плана продолжаются независимо от этого уточнения.

## Рекомендованная основа

**FastAPI + Pydantic + Uvicorn + sqlite3 + pwdlib[argon2] + prometheus-client; интерфейс на существующих native HTML/CSS/ES modules.** Один процесс приложения, одна SQLite в постоянном volume, тот же origin для панели и API. Изменения снимка и настроек публикуют новую согласованную версию расчёта; интерфейс проверяет обновление каждые 2 секунды. Это проектное решение для хакатонного объёма данных, а не заявленная проверка производительности.

Так повторно используются API-валидация, генерация OpenAPI/Swagger, HTTP-сервер, БД, password hashing, формат Prometheus и готовый дизайн. Собственная работа остаётся там, где её требует кейс: интерпретация биллинга, объяснимые признаки, веса, отсутствие оценки, денежная оценка, связность изменений и понятный пользовательский сценарий.

Готового приложения, которое можно честно объявить выполнением этого ТЗ после замены логотипа, в рассмотренном наборе не найдено. Это вывод по проверенным кандидатам ниже, а не утверждение обо всех публичных проектах.

## Компоненты обязательного решения

| Задача | Что взять | Что уже даёт | Что реализовать в проекте | Сложность интеграции |
|---|---|---|---|---|
| Точные REST endpoints и OpenAPI | FastAPI | Routing, dependency injection, request/response schemas, OpenAPI, Swagger UI | Пути из ТЗ, auth dependencies, фильтры/сортировки, ошибки, экспорт спецификации | Низкая |
| Проверка source snapshot и настроек | Pydantic | Типы, enum, ограничения, вложенные схемы, валидаторы | Отношения account/service/transaction, денежные единицы, допустимые статусы, диагностику и политику неполных данных | Низкая |
| ASGI runtime | Uvicorn | HTTP-сервер приложения | Startup, healthcheck, graceful shutdown, один worker и контейнерный запуск | Низкая |
| Сохранение source, weights, sessions и revisions | sqlite3 из Python + SQLite | Дисковая БД, SQL и транзакции без отдельного сервера | Схему, миграции, commit перед публикацией версии, restart persistence, seed только при первом запуске | Низкая для одного процесса |
| Пароли | pwdlib с Argon2 backend | Hash/verify через готовую криптографическую реализацию | Bootstrap администратора, login/me/logout, ограничение попыток, сессии, CSRF, защита всех data/settings routes | Средняя: библиотека хеширования не является всей аутентификацией |
| Prometheus | Официальный prometheus-client | Gauge/collector, encoding, content type, parser для тестов | Бизнес-метрики из того же расчёта, что summary; exact `/metrics`; token collector auth | Низкая |
| Панель | Текущие tokens/foundation/fonts и native модули | Уже утверждённую визуальную основу и доступные controls | Рейтинг, карточку клиента, причины, настройки, live update и реальные состояния | Средняя |
| Live update | Browser `fetch`, таймер и AbortController | Штатные возможности браузера, без runtime-пакета | 2s polling, обработку 401/offline, revision check, обновление открытых данных без потери фокуса и формы | Низкая |
| Запуск | Существующие Docker/Compose/Caddy scripts | Подготовленный инфраструктурный каркас | Заменить probe продуктом, новый health/smoke, persistent volume, минимальные services | Средняя; готовый probe не доказывает запуск продукта |

FastAPI позволяет задать требуемые `docs_url` и `openapi_url`. Официальное описание: [Metadata and Docs URLs](https://fastapi.tiangolo.com/tutorial/metadata/). Pydantic даёт проверку структуры, но не знает биллинговые правила организатора: [Pydantic documentation](https://docs.pydantic.dev/latest/). SQLite не требует отдельного server process: [Python sqlite3](https://docs.python.org/3/library/sqlite3.html).

### API и Swagger

Переиспользовать FastAPI routing/schema generation. Конкретные обязательные пути взять из [детализации требований](requirements.md), включая `POST /api/v1/auth/login`, `GET /api/v1/auth/me`, `POST /api/v1/auth/logout`, customers/detail, analytics/summary, model/settings и source/snapshot. Генерация спецификации не освобождает от проверки наличия каждого метода и пути.

Обычный `docs_url="/api/v1/docs"` — быстрый старт. Для сдачи документация должна открываться и работать в локальной среде жюри: разместить Swagger UI JS/CSS локально, указать локальный OpenAPI URL, проверить Try it out с рабочей авторизацией. FastAPI описывает [self-hosted Swagger assets](https://fastapi.tiangolo.com/how-to/custom-docs-ui-assets/). Не подменять обязательную Swagger-документацию страницей с описанием endpoint-ов. При защите Swagger route через сессию схема и её assets должны корректно проходить тот же сценарий.

Положить экспорт `openapi.json` или `openapi.yml` в поставку и проверять соответствие runtime-схеме. Выбранный путь схемы, её доступ и security schemes фиксирует API-контракт; ТЗ задаёт Swagger path, но не предписывает отдельное имя URL самой схемы.

### Хранение и согласованность

Для наборов 10/30/100 аккаунтов и одного контейнерного процесса начать со стандартного `sqlite3`. Настройки и снимок сохраняются транзакционно; не хранить веса только в Python globals или localStorage. SQLite подходит для встраиваемых и небольших серверных приложений; у неё есть границы параллельной записи: [официальные области применения](https://www.sqlite.org/whentouse.html).

Source revision + model revision + evaluation time идентифицируют один неизменяемый результат. Сначала проверить и пересчитать кандидат, затем атомарно опубликовать согласованные данные. Summary, список, detail и метрики должны брать одну версию. Это собственная небольшая orchestration-логика, которую библиотека БД не реализует за нас.

Не добавлять Redis, Celery, Kafka, отдельный feature store или scheduler service для нескольких сотен записей. Если схема действительно усложняется, **SQLAlchemy** — проверенная альтернатива для persistence, [ORM Quick Start](https://docs.sqlalchemy.org/en/20/orm/quickstart.html), [MIT](https://github.com/sqlalchemy/sqlalchemy/blob/main/LICENSE). В текущем MVP дополнительный ORM не обязателен. Не использовать sqlite3 и ORM как два независимых источника модели данных.

### Аутентификация

Взять `pwdlib[argon2]` для hash/verify: [официальный quickstart](https://frankie567.github.io/pwdlib/) и [рецепт FastAPI](https://fastapi.tiangolo.com/tutorial/security/oauth2-jwt/). Не копировать всю JWT-архитектуру примера автоматически: в кейсе нужны полноценные login/me/logout и защищённая same-origin панель.

Предпочтение для MVP: случайный непрозрачный session ID в HttpOnly cookie, server-side session record с TTL и отзывом при logout. Сохранять hash токена сессии, не открытый токен; отдельный CSRF token и проверка origin для изменяющих запросов. Cookie SameSite, path и Secure соответствуют локальному HTTP или настоящему HTTPS режиму. Это архитектурное решение приложения; pwdlib обеспечивает только password hashing.

Starlette `SessionMiddleware` доступен через FastAPI, но его подписанный cookie читается клиентом; это не шифрование и не server-side revocation. Поэтому не класть туда исходные данные, пароли и provider tokens. [Документация Starlette](https://starlette.dev/middleware/) описывает параметры cookie. OIDC может потребовать отдельное временное хранение state/nonce, но не меняет обязательный базовый login.

### Prometheus

Использовать официальный клиент, а не форматировать текст метрик руками. Минимум: количество клиентов по low/medium/high/unknown и денежная оценка; добавить агрегат score и время/версию актуальности без customer ID/email в labels. Значения брать из текущего immutable evaluation, а не из разрозненных mutable Gauge, которые могут отражать разные версии.

Нужен именно `GET /metrics`, доступный сборщику с документированным collector token. Удобно вернуть `generate_latest(registry)` через обычный защищённый FastAPI route с `CONTENT_TYPE_LATEST`. Не вводить в ответ данные клиентов и не разрешать collector token доступ к customer/settings API.

Официальный пример [FastAPI + Gunicorn](https://prometheus.github.io/client_python/exporting/http/fastapi-gunicorn/) отдельно предупреждает: ASGI `mount("/metrics", ...)` обслуживает `/metrics/` и даёт redirect с `/metrics`; `add_route` имеет отличие совместимости между версиями клиента. Поэтому exact route проверить реальным GET без follow redirects. Один worker также уменьшает риск нескольких несогласованных registries; multiprocess setup для этого MVP не нужен.

Проверенная тонкость лицензии: у пакета expression **Apache-2.0 AND BSD-2-Clause**, а не только Apache. Включённый `decorator` объясняет вторую часть: [pyproject.toml](https://github.com/prometheus/client_python/blob/master/pyproject.toml), [NOTICE](https://github.com/prometheus/client_python/blob/master/NOTICE).

### Деньги и модель

Не искать готовую вероятность оттока для чужого telecom dataset. В данных кейса нет labels, событийной истории и дат удаления; нельзя подтвердить AUC, calibration или прогноз фактической выручки.

Собственная функция расчёта должна быть небольшой и чистой: service feature registry, редактируемые веса, баллы с ограничением 0–100, причины и `unknown` при недостатке данных. Признаки: близость продления, ручное продление, недостаток доступного финансирования, `prolong_wait`. Итог клиента — максимальный риск подходящей услуги либо отсутствие оценки по правилам модели. Подробная формула и граничные случаи находятся в [описании модели](model.md).

Денежный показатель — операционный proxy потенциальных потерь: сумма цены одного ближайшего продления каждой подходящей услуги в горизонте, взвешенная её risk-score. Это не откалиброванное математическое ожидание. Баланс влияет через funding-gap: `balance + bonusBalance`, только когда `bonusState` разрешает бонусы; `withdrawBalance` исключён как не автоматически доступный для продления. Избежать повторного независимого расходования одного баланса на каждую услугу. Рекомендуется использовать integer cents и `Decimal` для долей/округления; отдельная финансовая библиотека не нужна.

## Дополнительные компоненты после обязательного сценария

| Компонент | Решение | Что взять и что дописать | Ограничение |
|---|---|---|---|
| Chart.js | Выбранный optional для графика | Готовый rendering/update; свои series из backend | Отдельно факт и прогноз, нет дат удалений — историческая серия недоступна, а не нарисована из expiresAt |
| Authlib | Optional после P0 | Готовый OIDC client: discovery, authorization flow, ID token verification | Нужен реально рабочий provider, credentials, callback, state/nonce и связка с local session |
| Keycloak | Только если для OIDC нет готового provider | Локальный IdP и воспроизводимый realm export в отдельном Compose profile | Дополнительный сервис и boot/debug; отключённый профиль не должен мешать базовому login |
| Tabulator | Резерв при росте требований к таблице | Готовые header sort/filter/update и remote sorting adapter | На 100 строках native table + backend sort проще; обязательный API нельзя заменить только client-side сортировкой |
| SQLAlchemy | Резерв при усложнении schema | ORM и transactions | Не добавлять просто ради шаблона; sqlite3 уже закрывает объём MVP |

**Chart.js:** [обновление datasets](https://www.chartjs.org/docs/latest/developers/updates.html), [доступность canvas](https://www.chartjs.org/docs/latest/general/accessibility.html), [MIT](https://github.com/chartjs/Chart.js/blob/master/LICENSE.md). Использовать локальный pinned asset, `update('none')` при reduced motion, читаемую таблицу/текстовую альтернативу, цвета и шрифт из существующих токенов. Библиотека не восстанавливает отсутствующую историю.

**Authlib:** [FastAPI integration](https://docs.authlib.org/en/latest/oauth2/client/web/fastapi.html), [Starlette integration](https://docs.authlib.org/en/latest/oauth2/client/web/starlette.html), [BSD-3-Clause](https://github.com/authlib/authlib/blob/master/LICENSE). Старые адреса `/client/starlette.html` при исследовании уже не открылись. Актуальные docs упоминают HTTPX2 backend: transport и зависимости выбирать по конкретному release, не переносить слепо старые команды установки. Секреты provider хранятся сервером.

**Keycloak:** [официальный Docker quickstart](https://www.keycloak.org/getting-started/getting-started-docker), [Apache-2.0](https://github.com/keycloak/keycloak/blob/main/LICENSE.txt). Наличие работающего IdP — только часть E2E: дополнительно проверить вход в нашу панель, доступ к данным, выход и сохранение password fallback.

**Tabulator:** [сортировки](https://www.tabulator.info/docs/6.x/sort/), [MIT](https://github.com/olifolkerd/tabulator/blob/master/LICENSE). Не добавлять одновременно Tabulator, TanStack Table и ещё один grid. Для P0 решение — семантическая native table с кнопками сортировки, aria-sort и серверным whitelist полей.

## Публичные приложения и шаблоны: пригодность целиком

| Кандидат и первоисточник | Проверенная лицензия | Что можно переиспользовать | Решение по этому кейсу |
|---|---|---|---|
| [Full Stack FastAPI Template](https://github.com/fastapi/full-stack-fastapi-template) | [MIT](https://github.com/fastapi/full-stack-fastapi-template/blob/master/LICENSE) | Рецепты структуры backend, tests, Docker, auth и OpenAPI client | Не импортировать целиком: React/SQLModel/PostgreSQL/Tailwind/shadcn/Traefik и почта потребуют адаптации текущего native дизайна; бизнес-модели кейса всё равно нет |
| [Django](https://docs.djangoproject.com/en/6.0/topics/auth/default/) | [BSD-3-Clause](https://github.com/django/django/blob/main/LICENSE) | Готовые auth, sessions, CSRF, ORM/admin | Сильная альтернатива, если команда уже умеет Django; для выбранного API-first MVP FastAPI прямее. Не смешивать два backend framework |
| [Streamlit](https://docs.streamlit.io/develop/concepts/connections/authentication) | [Apache-2.0](https://github.com/streamlit/streamlit/blob/develop/LICENSE) | Быстрые исследовательские панели, native OIDC | Не основа P0: exact REST/login/password, отдельная Swagger и Digital Ruins UI требуют дополнительного backend/адаптации. Ускоряет EDA, но не закрывает приёмку |
| [Dash](https://dash.plotly.com/) | [MIT core](https://github.com/plotly/dash/blob/dev/LICENSE) | Реактивные callbacks и аналитические графики | Допустимая альтернатива для команды с Dash; менять готовый native frontend ради неё нецелесообразно. Enterprise features не включаются в MIT core автоматически |
| [Supabase self-hosted](https://supabase.com/docs/guides/self-hosting/docker) | [Apache-2.0 umbrella repo](https://github.com/supabase/supabase/blob/master/LICENSE); отдельные компоненты имеют свои лицензии | Auth, PostgreSQL, Realtime, storage | Отложить: набор сервисов и собственные API namespaces; точный контракт и risk engine всё равно писать. Не нужен внешний cloud account |
| [Appwrite self-hosted](https://appwrite.io/docs/advanced/self-hosting/installation) | [BSD-3-Clause main repo](https://github.com/appwrite/appwrite/blob/main/LICENSE); остальные компоненты проверяются отдельно | Auth, DB, functions и Realtime | Отложить: новая multi-service платформа без готовой модели оттока, больше настройки и API adapter |
| [Evidently](https://github.com/evidentlyai/evidently) | [Apache-2.0](https://github.com/evidentlyai/evidently/blob/main/LICENSE) | Data-quality/drift reports и monitoring после появления baseline | Не business-risk engine: distribution drift требует reference/current; сравнение распределений не говорит, кому не хватит денег на продление |
| [scikit-learn Pipeline](https://scikit-learn.org/stable/modules/generated/sklearn.pipeline.Pipeline.html) | [BSD-3-Clause](https://github.com/scikit-learn/scikit-learn/blob/main/COPYING) | Pipeline/validation после появления разметки и достаточной истории | Не брать для P0. Регулируемые экспертные веса не требуют model training или ML dependency |
| [IBM/customer-churn-prediction](https://github.com/IBM/customer-churn-prediction) | [Apache-2.0](https://github.com/IBM/customer-churn-prediction/blob/master/LICENSE) | Только исторический пример data-science процесса | Отклонить как основу: репозиторий явно archived/read-only с 2022-01-14, Watson/cloud и иной Telco dataset; не переносить старые зависимости |
| [Adarsh-GPT/telco-churn-prediction](https://github.com/Adarsh-GPT/telco-churn-prediction) | [MIT](https://github.com/Adarsh-GPT/telco-churn-prediction/blob/main/LICENSE) | Пример раздельного preprocessing/evaluation и оформления model limitations | Не копировать веса/модель/метрики: IBM Telco labels и признаки не соответствуют account/service/transaction. Запуск и заявления README независимо не проверялись |

Для Evidently различие проверено по [официальному explainer drift](https://docs.evidentlyai.com/metrics/explainer_drift): нужны два набора и сравниваются распределения. Это полезная будущая проверка входных данных, но не замена формуле текущего кейса.

Оценка сложности в таблицах — инженерная оценка для этого репозитория, а не измеренное время интеграции. Популярность, число звёзд и дата поискового crawl не используются как доказательство сопровождаемости или безопасности. Для IBM архивирование подтверждено владельцем; для остальных здесь не заявлен независимый аудит сопровождения.

## Что уже есть в репозитории и нужно сохранить

| Локальный ресурс | Использование | Граница |
|---|---|---|
| `design/tokens.css`, `foundation.css`, `fonts.css` | Единственная основа темы, controls и типографики | Не дублировать палитру в новом theme или заменять дизайн UI-шаблоном |
| `design/dashboard/` | Переносить reusable rendering/state patterns по необходимости | Demo JSON и нейтральная навигация не являются продуктовой реализацией |
| `design/login.html`, `auth-page.*` | Визуальная основа настоящего входа | Текущие формы статичны; подключение auth и API ещё предстоит |
| `design/assets/icons/` | Только выбранные SVG с текущей атрибуцией | По upstream README иконки отдельно от MIT code: сохранить credit/link; в проекте указана CC BY 4.0 |
| `design/assets/fonts/` | Утверждённый PP Neue Machina Inktrap | Proprietary, не OFL и не MIT; local eval provenance не доказывает право публичного распространения |
| `design/assets/*.provenance.json` | Сохранить source/hash происхождение графики | Подготовленные/AI/generated/user-provided assets отдельно проверить по правилам конкурса |
| Docker/Compose/scripts | Переиспользовать здоровый каркас после чтения runbook | Старый probe и старый browser QA не подтверждают product build/E2E |

У шрифта уже есть [README](../design/assets/fonts/README.md) и [sources.json](../design/assets/fonts/sources.json): лицензия для коммерческого использования не приобреталась. [Официальный FAQ](https://pangrampangram.com/pages/faq#free-fonts-and-personal-use) допускает личное пробное использование и описывает отдельные Web/App/Logo licenses. **До публичной публикации продукта или распространения шрифта требуется подтверждение подходящих прав именно на этот сценарий и файл.** Нельзя автоматически считать bytes из specimen лицензированным release-пакетом. Это отдельный вопрос прав, а не причина менять утверждённый шрифт или останавливать локальную подготовку. Покупка и замена шрифта требуют решения пользователя.

Для icons первоисточник разделяет лицензирование [в README HackerNoon](https://github.com/hackernoon/pixel-icon-library#license-free--starter--pro). При переносе включить существующую credits page и точные сохранённые notices; не объявлять весь набор просто MIT.

## Минимальный набор инструментов проверки

| Инструмент | Что проверять | Отношение к доказательствам |
|---|---|---|
| pytest + FastAPI TestClient/HTTPX | Формулу/unknown, integer money, запрет доступа, сортировки, валидацию source, рестарт persistence | API/unit-проверки не заменяют browser E2E |
| Официальный parser prometheus-client | `/metrics` format, counts/sum согласованы с summary одной версии | Проверять exact `/metrics` и collector credentials |
| Доступный browser tool, затем Playwright при рабочем окружении | Полный сценарий на узком/широком экране, два окна, обновление после PATCH/PUT, logout | Проверять настоящую форму и DOM; не сообщать успех по curl |
| Docker Compose | Чистая сборка, startup health, volume/restart, инструкция README | Compose config отдельно от build/start и публичного deployment |
| Генерация runtime OpenAPI | Наличие exact paths/methods/security, round-trip valid snapshot через Swagger | Лежащий в репозитории YAML должен соответствовать runtime |

FastAPI описывает [TestClient](https://fastapi.tiangolo.com/tutorial/testing/). Лицензии инструментов: [pytest MIT](https://github.com/pytest-dev/pytest/blob/main/LICENSE), [HTTPX BSD-3-Clause](https://github.com/encode/httpx/blob/master/LICENSE.md), [Playwright Apache-2.0](https://github.com/microsoft/playwright/blob/main/LICENSE). Это dev dependencies; browser binaries имеют свои notices. Использование уже доступного browser tool не требует добавлять Playwright в production image.

## Версии, provenance и порядок переноса

1. Перед реализацией зафиксировать допуск OSS/prebuilt/AI в [rules](rules.md), не приписывать организаторам отсутствующие разрешения. Библиотеки и локальный дизайн — разные источники переиспользования.
2. На выбранном Python minor получить **совместимые выпущенные версии** FastAPI/Pydantic/Uvicorn/pwdlib/prometheus-client из официальных package metadata; сохранить exact pins и hashes в lock. Проверить wheels Argon2 в целевом Linux image. Не брать неподтверждённый `latest`, git HEAD или prerelease.
3. Записать образ Python по конкретному tag/digest после проверки сборки. Пиновать transitive dependencies lock-файлом; не задавать несовместимые независимые версии Starlette/Pydantic поверх ограничений FastAPI.
4. Для каждого включённого asset или скопированного snippet сохранить исходный URL, tag/commit, license/NOTICE и hash. В [machine-readable manifest](reuse-manifest.json) поля version/hash пока `null`: это честно отражает исследование, а не готовый dependency lock.
5. Скопировать только нужные рецепты, соблюдая их notices. Пакетная зависимость предпочтительнее ручного переноса исходников framework. Не импортировать весь шаблон ради нескольких endpoint-ов.
6. Swagger UI и optional Chart.js обслуживать локально из pinned релизов. Проверить отсутствие runtime CDN и реальную загрузку документации без внешней сети.
7. Выполнить узкий integration smoke: login → GET summary/list/detail → PATCH weights → live update → PUT snapshot → live update → restart → те же weights → метрики. Это критерий удачного переиспользования, а не количество установленных библиотек.
8. Зафиксировать direct/transitive licenses в `THIRD_PARTY_NOTICES` при фактическом включении. Лицензии, проверенные сейчас на main/master, нужно сопоставить выбранным точным артефактам; датой исследования нельзя заменить provenance поставки.

Номера runtime-пакетов намеренно не придуманы: документации `latest`, default branch и release wheel могут расходиться. Проверенный пример этого риска — различие `prometheus-client` ASGI routing, описанное выше. Версии фиксирует первый проверенный build, затем обновлять их только по конкретной причине и с повторной проверкой.

## Задание следующему агенту

Начать с [implementation-plan](implementation-plan.md) и [architecture](architecture.md). Реализовать вертикальный обязательный сценарий на выбранной минимальной основе. У каждой новой зависимости назвать конкретный закрываемый пункт приёмки. Не добавлять ML, внешние SaaS, тяжёлую платформу, rule DSL и новые визуальные эффекты ради количества технологий. Когда P0 проходит полный browser/Docker/restart сценарий, добавить один работающий график, затем OIDC при достаточном запасе времени и наличии provider.

Полный реестр рассматриваемых компонентов, URL, решений и незавершённых версионных проверок: [reuse-manifest.json](reuse-manifest.json).
