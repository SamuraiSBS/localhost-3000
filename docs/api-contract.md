# Проектный контракт API

Статус: **контракт для реализации**, не существующий сервер. Методы/пути обязательны по ТЗ с. 10–11 (R-014–025); тела, параметры, security и ошибки ниже — выбранные проектные решения. Генерируемый из будущего FastAPI `openapi.json` должен совпасть с ними; окончательную спецификацию приложить к сдаче и проверить по живому приложению.

## Общие правила

- Same-origin `/api/v1`, JSON UTF-8; даты RFC3339 с timezone, наружу UTC `Z`.
- Денежные поля с суффиксом `_cents` — целые евроценты; `currency="EUR"`. Входной DTO сохраняет исходные camelCase имена. Каждый входной и вычисленный денежный integer, включая суммы корзин/агрегаты, ограничен `0..9007199254740991` (`2^53-1`): JSON/JavaScript и Prometheus Gauge точно представляют целые в этом диапазоне. Переполнение проверяется до commit и отклоняется 422. Это наш эксплуатационный предел, не ограничение PDF.
- Входные ID — положительные integer в диапазоне signed bigint; в ответах ID представлены десятичными строками, чтобы JavaScript не терял точность за пределом `2^53-1`. Сортировка ID на сервере числовая.
- `risk_score` — число 0..100 с точностью до двух знаков или `null`; `risk_level` — `low|medium|high|unknown`. Unknown не передаётся как ноль.
- Результаты содержат `analysis_revision`, `data_revision`, `settings_revision`, `model_version`, `evaluated_at`, `horizon_days`. Все поля одной выдачи относятся к одному Evaluation.
- Клиентские данные, auth и настройки: `Cache-Control: no-store`. CORS не включать без отдельной задачи. Пользовательский текст выводить через textContent/экранирование, не HTML.

Пример общей ошибки:

```json
{
  "error": {
    "code": "invalid_snapshot",
    "message": "Снимок не загружен. Исправьте указанные поля.",
    "details": [{"path": "services[2].ownerId", "reason": "account_not_found"}]
  }
}
```

HTTP: 400 — синтаксис запроса, 401 — нет/истекла аутентификация, 403 — неверный CSRF или недостаточный тип доступа, 404 — неизвестный ID, 409 — конфликт ревизии, 413 — превышен лимит, 422 — валидация DTO/настроек, 429 — login rate limit, 500 — нейтральная ошибка сервера. FastAPI validation errors привести к общей форме обработчиком. Никаких секретов/traceback в ответе.

## Security

Вход создаёт opaque server-side session. Cookie `session`: HttpOnly, SameSite=Lax, Secure на HTTPS, Path=/, ограниченный срок жизни. В БД хранится hash токена, а не его исходное значение. После logout удалить запись и cookie. Credentials не передавать в query string.

`GET /auth/me` возвращает CSRF-token текущей сессии. Все изменяющие действия с cookie, кроме первичного login, требуют `X-CSRF-Token` и корректный Origin; login проверяет Origin. UI хранит CSRF в памяти. Стандартный логин/пароль обязателен даже при добавлении OIDC до отдельного уточнения ТЗ.

Для `/metrics` — отдельный `Authorization: Bearer <METRICS_TOKEN>`, доступный только сборщику. Он не даёт доступ к клиентским данным/настройкам/snapshot. Не добавлять bearer-auth к каждому API без потребности. Весь пользовательский API, Swagger и OpenAPI — по сессии. Это рабочая трактовка Q-007.

Публичны login, его локальные ассеты, художественная главная без аналитики и минимальный `/healthz`. Неавторизованный GET панели переводится на вход; API отдаёт JSON 401, не HTML-redirect. Маршрут документации и JSON schema не должны создавать обход этой проверки.

## 1. POST /api/v1/auth/login

```json
{"login": "analyst", "password": "вводится локально, не хранится в примерах"}
```

200, Set-Cookie и тело:

```json
{"user": {"id": "operator", "login": "analyst"}, "csrf_token": "session-bound-token"}
```

Неверные credentials — 401 с одинаковым сообщением для неизвестного логина/пароля. Не использовать фиксированный пароль из документации в публичном окружении. Валидация/лимит тела и ограничение попыток обязательны как обычная реализация защищённого входа.

## 2. GET /api/v1/auth/me

Сессия обязательна. 200: та же форма `user` и `csrf_token`. Без сессии 401; пароль/hash/cookie token не возвращаются. Frontend вызывает этот endpoint перед загрузкой защищённого экрана и после возврата из OIDC, если бонус будет реализован.

## 3. POST /api/v1/auth/logout

Сессия и CSRF. 204 без тела, сессия инвалидирована на сервере. Повторный вызов уже недействительной сессией — 401; UI всё равно показывает экран входа. После logout открытые вкладки при следующем запросе теряют доступ и очищают клиентские данные.

## 4. GET /api/v1/customers

| Параметр | Значения | Default |
|---|---|---|
| `sort_by` | `id`, `email`, `balance`, `totalReplenished`, `risk_score` | `risk_score` |
| `sort_order` | `asc`, `desc` | `desc` |
| `risk_level` | `low`, `medium`, `high`, `unknown`; отсутствует = все | все |
| `q` | Подстрока email или точный ID | пусто |
| `limit` | integer 1..100 | 50 |
| `offset` | integer >=0 | 0 |

Четыре первые сортировки в обе стороны обязательны; score-сортировка и пагинация — удобство реализации. Стабильный tie-breaker `id ASC`; ID сортируются как числа, email — детерминированно по полному значению, без зависимости от locale ОС. При сортировке score unknown всегда в конце. Некорректные имена полей/порядок — 422, не интерполяция произвольной SQL-строки. `q` не исполняет пользовательские SQL/LIKE-шаблоны.

200, пример для ручного M02 из [модели](model.md):

```json
{
  "analysis_revision": "a-17",
  "data_revision": 2,
  "settings_revision": 1,
  "model_version": "rules-v1",
  "evaluated_at": "2026-10-23T12:00:00Z",
  "horizon_days": 30,
  "currency": "EUR",
  "total": 1,
  "limit": 50,
  "offset": 0,
  "items": [{
    "id": "1001",
    "email": "manual-renewal@example.test",
    "balance_cents": 1000,
    "total_replenished_cents": 10000,
    "risk_score": 45,
    "risk_level": "medium",
    "monetary_risk_cents": 450,
    "top_reasons": ["Автопродление отключено", "Наступил срок ближайшего продления"],
    "data_quality_warnings": []
  }]
}
```

`total` — количество после фильтра, до пагинации. Общая сводка analytics остаётся для полного активного снимка; UI явно показывает, что фильтр таблицы не меняет общие показатели. Пустой фильтр — `items=[]`, 200, а не ошибка.

## 5. GET /api/v1/customers/{id}

200: общие metadata + account fields, итоговые score/level/money, `spendable_cents`, `due_services_price_cents`, `funding_gap_cents`, `unscored_reason`, список `services` и исходных `transactions` с датами/статусами. Несуществующий ID — 404; клиент из нового snapshot, который исчез, не остаётся старой карточкой.

Каждая услуга: исходные DTO поля; `eligible`, `in_horizon`, `excluded_reason`; `covered_cents`, `funding_gap_cents`; score/уровень/денежная сумма; `features[]` с `id`, `value`, `weight`, `contribution`, `available`, `explanation`, `evidence`. `raw_score` и флаг ограничения до 100 доступны для проверки объяснений. У `blocked/deleted` нет будущей денежной оценки услуги; текущий статус показывается.

Причины поступают из backend model: frontend только форматирует единицы и русские подписи. Не генерировать независимое объяснение LLM или вторую формулу в браузере.

## 6. GET /api/v1/analytics/summary

200: общие metadata, `currency`, `customers_total`, `customers_evaluated`, `risk_counts` с четырьмя ключами, `monetary_risk_cents` (известная подытоженная сумма), `monetary_coverage` с evaluated/unknown counts, `due_services_price_cents`, `funding_gap_cents`, количество предупреждений о данных. Можно возвращать `mean_risk_score` по оцениваемым клиентам или `null`, если их нет.

Инварианты:

```text
high + medium + low + unknown = customers_total
customers_evaluated + unknown = customers_total
summary.monetary_risk_cents = сумма известных customer money в полном снимке
summary revision = revision использованной выдачи/экспортируемых метрик
```

Если все unknown, summary подытоженная сумма 0 сопровождается coverage=0; frontend показывает отсутствие оценки. Итоги не должны меняться от сортировки/пагинации.

## 7. GET /api/v1/model/settings

200 + `ETag: "settings-1"`:

```json
{
  "settings_revision": 1,
  "model_version": "rules-v1",
  "horizon_days": 30,
  "thresholds": {"medium": 35, "high": 65},
  "weights": {
    "renewal_proximity": 20,
    "manual_renewal": 25,
    "funding_gap": 35,
    "payment_wait": 20
  },
  "features": [{
    "id": "manual_renewal",
    "title": "Автопродление отключено",
    "description": "Для услуги в горизонте потребуется действие клиента",
    "default_weight": 25,
    "min_weight": 0,
    "max_weight": 100
  }]
}
```

В реальном ответе metadata перечисляет **все** признаки registry; пример сокращён. Значения формы берутся из сохранённых weights, не из default_weight.

## 8. PATCH /api/v1/model/settings

Session, CSRF, `If-Match: "settings-1"`:

```json
{"weights": {"manual_renewal": 50}}
```

Операция — частичный patch известных ключей, а не замена всех весов одним полем. Аналогично допустимы H и thresholds. Неизвестный ID/ключ, NaN/Infinity, bool вместо числа, отрицательные веса, все нули и неверные пороги — 422; отсутствие If-Match — 400 с инструкцией прочитать settings. Конфликт версии — 409 с текущей revision.

200: полная сохранённая конфигурация + `analysis_revision` результата пересчёта, новый ETag. Запись атомарна и commit завершён до успешного ответа. После перезапуска данные остаются в volume. Изменённый результат появится без reload и в другой вкладке; UI не ждёт следующего ручного входа.

## 9. PUT /api/v1/source/snapshot

Session и CSRF. Тело — **не обёрнутый** исходный объект `data.json`:

```text
{
  accounts: AccountSource[],
  services: ServiceSource[],
  transactions: TransactionSource[]
}
```

| DTO | Поля фактического источника |
|---|---|
| AccountSource | `id`, `email`, `balance`, `withdrawBalance`, `totalReplenished`, `bonusBalance`, `bonusState` |
| ServiceSource | `id`, `name`, `ownerId`, `paymentTerm`, `autoProlong`, `createdAt`, `expiresAt`, `status`, `price` |
| TransactionSource | `id`, `amount`, `status`, `performedAt`, `createdAt`, `type`, `serviceId` |

Enum следуют поставленному JSON; `autoProlong` именно с такой капитализацией. `performedAt=null` допустим для not_performed. Даты с timezone, `expiresAt>=createdAt`, исполненная транзакция имеет дату не раньше создания. ID уникальны в каждой таблице, ссылки существуют. Пустые три массива — валидный пустой снимок; аккаунт без услуг валиден и даёт unknown.

Числа денег — integer `0..2^53-1`, bool/float отвергаются; проверяются также все суммы и промежуточное доступное покрытие. Неотрицательность — наш контракт v1 на основе предоставленного DTO, не заявление о всех реальных биллингах; отрицательные долги/возвраты потребуют явной версии схемы. **Нет ограничения amount<=1000, name допускает произвольный Unicode**. Не требовать `withdrawBalance<=balance`, `totalReplenished>=balance`, совпадения price и amount или соответствия статуса вычисленному времени.

Новые дополнительные поля можно сохранять в исходном JSON и не использовать до появления соответствующего признака. Неизвестный enum и изменённый тип обязательного поля не должны проходить молча. Полный активный snapshot заменяется, merge по ID не выполняется. При любой ошибке старый снимок и его результаты остаются активными.

200: `data_revision`, `analysis_revision`, `received_at`, `checksum`, counts трёх массивов, список **предупреждений** качества. Повтор того же канонического JSON: 200, `changed=false`, без новой data_revision. Изменение — `changed=true`. Импорт не меняет settings.

UI предлагает загрузку JSON и явно подписанное подтверждение **полной замены аналитического снимка**, сохраняя файл организатора. Внешний demo feeder может авторизоваться тем же login и выполнить PUT; отдельный source API token — только при реальной потребности и с доступом к одному маршруту.

## 10. GET /metrics

Точный путь без обязательного перехода на `/metrics/`. Bearer token сборщика; 200 с Prometheus text exposition, сформированным библиотекой. Не переписывать формат вручную.

Планируемые имена и единицы (не установлены организатором):

```text
# TYPE churn_customers gauge
churn_customers{risk_level="low"} 1
churn_customers{risk_level="medium"} 0
churn_customers{risk_level="high"} 0
churn_customers{risk_level="unknown"} 0
# TYPE churn_monetary_risk_cents gauge
churn_monetary_risk_cents 200
# TYPE churn_risk_score_mean gauge
churn_risk_score_mean 20
# TYPE churn_evaluated_customers gauge
churn_evaluated_customers 1
# TYPE churn_evaluation_timestamp_seconds gauge
churn_evaluation_timestamp_seconds 1792756800
```

Пример иллюстрирует один M01, не исходные 10 клиентов. При отсутствии оценок mean=0 и evaluated_customers=0 совместно показывают отсутствие измеренной средней. Labels только низкой кардинальности; не помещать customer/email/session/revision в labels. Номера data/settings revision можно выдать отдельными numeric Gauge для сопоставления. Значения обновляются через тот же Evaluation, включая сброс устаревших labels после нового снимка. Протокол допускает HELP/TYPE, emitted библиотекой; проверять parser и реальный scrape.

## 11. GET /api/v1/docs и файл OpenAPI

Swagger UI на точном пути, защищённая сессия, локальные JS/CSS. Настроить JSON schema по `/api/v1/openapi.json` с той же защитой, отключить неожиданные стандартные `/docs`/`redoc` обходы. Это дополнительный schema endpoint, его путь PDF не фиксирует.

Спецификация включает все 11 обязательных операций, cookie security и CSRF header, metrics bearer, enums, null score, cents, ошибки и примеры. Секретов/реальных паролей в examples нет. Экспортировать **из реализованного приложения** в `openapi.json` корня/согласованного каталога поставки; CI сравнивает export с app.openapi() и выполняет контрактные запросы. Наличие этого плана не выполняет R-025.

## Дополнительные маршруты

`GET /healthz` для health/revision готовности storage; страница `/customers`, `/customers/{id}`, `/settings`, публичный `/login` и опциональная художественная `/`. Никакой регистрации пользователей/CRM/редактирования биллинговых услуг в MVP. P1 OIDC callback и chart-series endpoints допускаются ТЗ, но не добавляются до приёмки P0.
