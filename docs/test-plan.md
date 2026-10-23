# Проверки будущего продукта

Статус: **план приёмки**. На продукте ни один сценарий пока не запущен. Текущие фактические проверки подготовки перечислены в [planning-status](planning-status.md); исходные обязательные acceptance ID — [evaluation](evaluation.md).

## Условия воспроизводимости

Для каждого результата сохранять commit/revision, окружение, hash входного снимка, settings/model version и `as_of`. Чистые вычисления тестировать с фиксированным clock; browser live — с действующим источником и реальными запросами. Контрольные M01–M12 из [model](model.md) — маленькие ручные эталоны, исходный fixture — отдельный integration input. Нельзя написать ожидания, просто скопировав вывод тестируемой реализации.

Тесты финансов, auth, атомарности и контрактов содержательны: проверяют ошибки, которые меняют вывод/доступ/сохранность. Не нужны snapshot tests всех статичных подписей или тесты, повторяющие каждую строку реализации.

## Модель и данные

| ID | Проверка | Существенное ожидание |
|---|---|---|
| U01 | M01–M12, в том числе один баланс на две услуги | Ручные score/levels/cents совпали; нет двойного расходования |
| U02 | Границы 34.99/35/64.99/65, clamp100, нулевые веса | Правильный уровень; неправильная конфигурация не принята |
| U03 | Cents, Decimal, half-up, price0 | Нет деления на ноль/ошибки в100 раз; money sum детерминирована |
| U04 | Locked/unlocked, withdrawBalance, totalReplenished | Потратить можно только основной и доступный бонус; не складывать исторические пополнения в потери |
| U05 | active/suspended/prolong_wait/blocked/deleted | Suspended учитывается; blocked/deleted не forecast; stale wait warning не меняет upstream |
| U06 | Нет услуг, только исключённые, пустые массивы | Unknown/null отличается от измеренного low0; пустой total корректен |
| U07 | Нет transactions / performed vs not_performed | Отсутствие истории не означает высокий риск; баланс не реконструируется по fixture |
| U08 | Смена as_of, граница горизонта, просрочка | Один clock на расчёт, UTC, временное обновление без нового снимка |
| U09 | Unicode name, amount1001, email с HTML | Вход не ограничен fixture regex/range; UI экранирует текст |
| U10 | Невалидные типы, bool за integer, duplicate ID, FK, дата | Полный reject; старые данные не повреждены |
| U11 | Деньги >2^53−1 или сумма допустимых входов переполняется | 422 до commit; JSON/UI/метрики не теряют центы |

## API, persistence, конкуренция

| ID | Связь с приёмкой | Проверка |
|---|---|---|
| I01 | A-02 | Login/me/logout: correct/wrong/no/expired session; после logout доступ потерян |
| I02 | A-02 | Без сессии клиенты/settings/source/docs/openapi закрыты; metrics token не открывает другие API |
| I03 | A-02 | CSRF/Origin на PATCH/PUT/logout; неверный токен403; password/hash/session не попали в responses/logs |
| I04 | A-04 | ID/email/balance/totalReplenished × asc/desc, неодинаковые данные, стабильные ties, ID2 раньше10 |
| I05 | A-04,A-05 | Фильтр риска/q, unknown last при score, пагинация, пустой фильтр, detail404 |
| I06 | A-06 | PATCH одного веса сохраняет остальные; bad values422, конфликт409, no-op/актуальная версия определены |
| I07 | A-06 | Реальный process/container restart с прежним volume сохраняет weights/H/thresholds и active snapshot |
| I08 | A-07 | PUT — полная замена; изменились score/rank/money; удалённый из снимка ID не висит в деталях |
| I09 | A-07 | Невалидный, слишком большой и повторный snapshot: atomic reject/413/no-op, прошлые данные сохранены |
| I10 | A-06,A-07 | Одновременные PUT/PATCH, специально задержанный старый расчет: старый Evaluation не публикуется после нового, metadata согласованы |
| I11 | A-08,A-09 | Summary/list/detail/metrics берутся из одной evaluation: counts=total, money=sum по known, revision совпадает |
| I12 | A-09 | Exact /metrics 200 без redirect, правильный MIME, parser принимает, уровни сброшены после нового snapshot; нет email/ID labels |
| I13 | A-10 | Все 11 операций из PDF, локальный Swagger и export openapi.json совпадают с live app; нет обходных /docs/redoc |
| I14 | A-11 | Контрольный признак через registry появляется в настройке/score/reason небольшим diff, его отсутствие данных объяснено |

## Docker и поставка

Не запускать команды удаления volumes ради «чистого» теста на пользовательском окружении. Для A-01 создать отдельный Compose project/volume с подтверждённым именем и путём. Для A-06 restart должен **сохранять** volume. Отдельно проверить доступность read-only seed по кириллическому пути на Windows и внутри образа; образ может включать явно выбранную копию seed только при разрешённых правах на поставку.

Проверки: Compose config; build; up + health; вход/данные через proxy; новый snapshot; restart; повторный вход/сохранённая настройка; export OpenAPI; отсутствие dev credentials в образе/репозитории. Минимальный smoke проверяет revision и boundary доступа, но не объявляется E2E.

Prometheus profile: сборщик с bearer_token_file действительно делает scrape; секреты не печатаются. Это дополняет text parser, потому что успешный parser не проверяет доступ сборщика.

CI обновляется вместе с product Docker/check/smoke. Локальная команда зелёная ≠ CI зелёный; image build ≠ VPS/HTTPS; public health ≠ browser product journey.

## Браузерный путь

Использовать доступный browser tool. Проверить на широком1440 и узком360 экране, затем320/768 и200%zoom. Если shell Playwright не запускается, использовать browser/CUA, не подменять браузер API-тестом. Для автоматизированных прогонов выбрать официальный Playwright release и установить только dev tooling после реальной проверки окружения.

1. Гость открывает прямой URL карточки/таблицы → вход, клиентские данные не видны. Неверный пароль → понятная ошибка; правильный → рейтинг.
2. Видны все обязательные поля/единицы, score/level/reasons; unknown имеет текст отсутствия оценки.
3. Все8 обязательных сортировок и фильтр реально меняют строки; клавиатура и `aria-sort` работают.
4. Карточка выбранного клиента показывает исходные услуги/даты и ручной денежный пример.
5. Во второй вкладке открыт рейтинг. В первой изменить manual_renewal25→50 на клиенте M02: 45→70, medium→high,450→700cents. Обе вкладки обновились без reload в проверочной цели<=5с; сортировка/фокус сохранены.
6. Загрузить изменённый snapshot: баланс/бонус/autoProlong на контрольном клиенте меняют результат и рейтинг автоматически; невалидный импорт оставляет прошлый результат.
7. Перезапустить app/container с тем же volume: настройки и snapshot на месте.
8. Показать metrics и совпадение суммы/числа уровней; открыть Swagger и рабочую документацию.
9. Logout: текущая и другие вкладки теряют доступ на следующем запросе; старые client данные не остаются на login screen.

Негативные UI: offline/server500, expired session, пустой набор/фильтр, disappeared customer404, конфликт settings409, плохой JSON, длинное Unicode имя/email. Не стирать draft настроек при polling, не сбрасывать фокус, не создавать фиктивные loading percentages.

Применить [design QA](design/DESIGN_QA_CHECKLIST.md): размер текста>=16px, контраст, focus, labels/ошибки, touch controls, reduced motion, layout без горизонтальной прокрутки страницы. Auth/данные/изменения проверяются на **продукте**, не на `design/dashboard`.

## Evidence ledger для реализации

Будущий агент создаёт файл отчёта с таблицей:

| Поле | Содержимое |
|---|---|
| Scenario | A-xx / Uxx / Ixx |
| Status | passed / failed / not_run |
| Environment | local process / Docker / CI / VPS / browser |
| Revision | commit + analysis/data/settings revisions |
| Inputs | snapshot hash, settings, as_of |
| Evidence | точная команда/вывод или browser screenshot/record |
| Limits | что этим прогоном не проверено |

Не ставить passed заранее. Перед финальным handoff выполнить `python scripts/check.py`, актуальные product tests, `git diff --check`; повторять дополнительные проверки после содержательной правки, а не ради объёма лога.
