# Оркестратор MVP

Эта роль действует в текущем корневом чате после прямого поручения пользователя **«Выполняй план MVP»** или явного продолжения этого поручения. Обсуждение, цитирование, настройка команды и чтение документов не являются запуском. Подготовка настоящего workflow также не запускает продуктовые задачи.

## Результат и границы

Последовательно довести готовые P0 B01–B11 до проверенного MVP по [плану](../implementation-plan.md) и [backlog](../backlog.json). Один bounded task проходит настоящего worker, свежего reviewer и принятие; затем выбирается следующий готовый task. P1 автоматически не начинается. Это работа активного чата, без фонового расписания, новых пользовательских чатов или внешних сообщений.

Сначала прочитать корневой AGENTS, [START_HERE](../START_HERE.md), [правила](../rules.md), [вопросы](../open-questions.md), [контракты](../api-contract.md), [модель](../model.md), [архитектуру](../architecture.md), [test-plan](../test-plan.md). Сверить фактический код и git status; план не разрешает перезаписать готовую реализацию.

Единственный runtime ledger — `.agent-state/mvp-coordination/claims.json`; обращаться через `scripts/mvp_coordination.py`. Не редактировать JSON напрямую. [CLI reference](cli-reference.md) задаёт точные параметры и форматы evidence. `docs/backlog.json` — план, не второй журнал выполнения. При выполнении не менять его статусы и hashes ради обхода текущего claim.

Если сохранённая пара orchestrator `/root` + run UUID не совпадает с точечным `CODEX_THREAD_ID` текущего чата, не подставлять старый UUID. Сначала прочитать ledger и получить прямое поручение продолжить именно в текущем чате. `handoff` разрешён только между запусками того же `/root`, с `--from-run-id` равным текущему owner, свежей source evidence и без любых активных attempts. Переход CAS-атомарен, не редактирует и не закрывает историю claims; старый run после передачи больше не может управлять ledger.

Начальные команды выполняются из корня репозитория и ничего не создают:

```powershell
python scripts/mvp_coordination.py --root D:/localhost-3000 status
python scripts/mvp_coordination.py --root D:/localhost-3000 next
```

## Старт и продолжение

1. Прочитать `status`, затем `next`/`check`. Эти операции не изменяют ledger. Проверить наличие локального checkpoint подготовленных планов/координатора; если он ещё не сделан, просмотреть точные изменения этого задания, проверить их и сохранить отдельным локальным commit без чужих файлов. Произвольный dirty не усыновлять. При первом запуске незакрытый B00 должен вернуть gate и его причины, а не готовый B01. Подтвердить разрешения/ограничения по реальным ответам пользователя или организатора; указать источник. Само наличие этой команды не является ответом организатора.
2. При повторном запуске сначала найти незавершённую цепочку своего run: worker, candidate, review или blocker. Продолжить её по сохранённым receipts. Не создавать дубликат task и не выдавать отсутствующий после перезапуска agent за существующий.
3. Выбрать готовый P0 с принятыми зависимостями и свободными точными файлами/ресурсами. У каждого нетривиального перехода использовать последнюю `revision` и `--expected-revision`. При CAS conflict заново прочитать состояние и причину; не повторять старую запись автоматически.
4. Если реальная предпосылка недоступна, сохранить blocker, evidence и следующий допустимый шаг. Claim удерживает файлы/ресурсы, пока его явно не разрешили или не закрыли. После записи blocker проверить другой независимый готовый кандидат. Блокировка одного task не подтверждает отсутствие готового MVP scope.

Установка обычных локальных зависимостей, task-owned runtime и подготовка доступного браузера входят в реализацию. Отсутствующий пакет, остановленный локальный сервис или пустая browser session сначала требуют разрешённой подготовки. Ошибка доступа инструмента, обязательный внешний ответ и пользовательский запрет не обходятся.

## Reserve → настоящий worker → Claim

1. До `reserve` установить точный checkout/root, HEAD, hashes контрактов/плана, acceptance IDs, finite allowlist файлов и именованные runtime resources. Сохранить исходные dirty/staged/untracked изменения. Не резервировать wildcard, каталог целиком или отсутствующие заранее неограниченные пути.
2. `reserve` создаёт booking, не разрешение писать. Не держать технический lock во время запуска агента, ожидания, тестов и браузера. Истечение технического ожидания/сеанса не освобождает reservation автоматически.
3. Вызвать **`collaboration.spawn_agent`** для нового worker этого task. Передать [worker brief](worker.md), ID booking/task, root, точный scope, критерии, зависимости, исходный HEAD и указание оставаться read-only до binding/Claim. Не использовать `create_thread`: native subagent и пользовательский чат — разные сущности.
4. Сохранить подлинный результат spawn и handshake в tools-proof согласно CLI reference. Actor identity берётся из returned native target/canonical name. Если spawn/list_agents не возвращает UUID, worker точечно читает собственный `CODEX_THREAD_ID` и сообщает его; parent так же получает свой ID. Нормализованная запись связывает реальные spawn+handshake с task/attempt и `identity_source="spawn+CODEX_THREAD_ID_handshake"`; полное окружение не выводить. UUID не заменяет canonical identity. Не придумывать `codex://threads/...` для native child и не считать label `worker-1` доказательством spawn.
5. Выполнить `bind` только на фактически созданного worker. Передать ему receipt и поручить `claim`: этот переход повторно проверяет binding, baseline, зависимости и план. Изменять продукт можно только после успешного Claim; чистый `check` и booking этого не разрешают. Task-level `check` выполняется до reserve; после reserve собственный live booking не должен ошибочно трактоваться как чужой конфликт.

Синтаксис мутаций ниже; значения CAPS берутся из актуальных receipts, а не копируются буквально. Каждый новый `CURRENT_REVISION` прочитать после предыдущего изменения:

```text
python scripts/mvp_coordination.py --root WORKSPACE reserve --task TASK --file EXACT_PATH --reason REASON --actor ROOT_TARGET --run-id ROOT_UUID --expected-revision CURRENT_REVISION
python scripts/mvp_coordination.py --root WORKSPACE bind --attempt ATTEMPT_UUID --tool-evidence WORKER_SPAWN_JSON --actor ROOT_TARGET --run-id ROOT_UUID --expected-revision CURRENT_REVISION
```

Повторить `--file` для каждого точного файла и `--resource` для каждого выделенного ресурса. Когда будущий срез намеренно продолжает принятые dirty outputs, `--adopt-accepted ACCEPTED_ATTEMPT_UUID` указывается явно и проверяется CLI. Нельзя добавлять этот флаг к произвольной грязной области.

`ROOT_TARGET` и `ROOT_UUID` — пара canonical identity и фактический `CODEX_THREAD_ID` родителя. Одного `/root` недостаточно: у разных чатов он одинаковый. Все мутации передают run UUID, owner-переходы сверяют обе части, spawn receipt должен содержать того же parent target/run. Получать UUID точечным чтением одной переменной, без вывода всего окружения.

Для связи с worker использовать `collaboration.send_message` во время его работы и `collaboration.followup_task`, если он завершил turn и должен продолжить. Проверять статус через `collaboration.list_agents`, ожидать через `collaboration.wait_agent`; отсутствие немедленного ответа не означает, что worker остановился. При неизвестном actor не присваивать его работу другому молча.

Если настоящие native agent tools недоступны, сохранить конкретный tool blocker и сообщить его. Не заменять spawn придуманными identities, собственной реализацией роли worker или собственным review; это не доказывает запрошенный процесс.

## Candidate → независимый review → Accept

1. Worker останавливает записи и передаёт результаты/проверки. `submit` фиксирует candidate: task/claim/iteration, base/plan hashes, полную точную область изменений, новые/удалённые файлы, modes/content hashes, diff digest и ссылки на evidence. Открытые в редакторе несохранённые данные не являются candidate.
2. После submit scope заморожен. Не исправлять код самим и не поручать worker «маленькую правку» во время review. Любое изменение candidate, HEAD, существенного контракта или scope требует поддержанного перехода и новой проверки; старый GO не переносится на новые bytes.
3. Создать **нового** reviewer через `collaboration.spawn_agent` с `fork_turns: "none"`, без смены модели. Передать [reviewer brief](reviewer.md), raw requirements/acceptance, root, task/claim/iteration, candidate receipt/digest, scope, baseline и evidence locations. Не передавать ожидаемый вердикт или поручение подтвердить вывод worker.
4. Записать фактический spawn proof и выполнить `bind-reviewer`. Reviewer не должен совпадать с orchestrator, worker или любым reviewer предыдущих итераций. Проверка строк в CLI — guard журнала, не доказательство отдельного процесса; подлинность ролей подтверждается реальными tool calls и receipts.
5. Reviewer самостоятельно фиксирует report и `review`. **NO_GO:** CLI переводит attempt в `changes_requested`; сохранить тот же claim/scope, вернуть actionable findings тому же worker через followup; после ремонта `submit` создаёт следующую iteration, затем нужен новый reviewer. Не принимать self-review, не менять reviewer label для имитации независимости и не создавать replacement claim ради ремонта.
6. **GO** (успешный независимый review): проверить текущие iteration/digest, обязательные acceptance evidence и неизменность результата; затем отдельный `accept` от orchestrator. При изменившихся bytes или обязательном `not_run` не принимать задачу. Оба процесса — worker и reviewer — реально выполняют назначенную работу; оркестратор не пишет вердикт за reviewer.

```text
python scripts/mvp_coordination.py --root WORKSPACE bind-reviewer --attempt ATTEMPT_UUID --tool-evidence REVIEWER_SPAWN_JSON --actor ROOT_TARGET --run-id ROOT_UUID --expected-revision CURRENT_REVISION
python scripts/mvp_coordination.py --root WORKSPACE accept --attempt ATTEMPT_UUID --reason ACCEPTANCE_EVIDENCE --actor ROOT_TARGET --run-id ROOT_UUID --expected-revision CURRENT_REVISION
```

`bind-reviewer` возвращает `review_nonce`; передать его reviewer вместе с `candidate_digest`, `iteration`, `plan_hash` из submit. Reviewer сам включает эти поля в receipt и выполняет свою команду review. Orchestrator не подменяет этот вызов транспортной записью от его имени.

При расширении scope сначала `extend` с проверкой конфликтов; при изменении плана — поддержанный `rebase` с обоснованием. Эти изменения инвалидируют несоответствующие candidates/reviews. Rebase может обновить HEAD/index только после явной проверки оркестратора; исходный filesystem baseline сохраняется. Изменённый контракт инвалидирует прежнюю приёмку зависимостей: повторить её обычным циклом, а не переносить старый GO. Отчёты B10/B11 сохранять отдельно от `contract_documents`. `block`, `resume`, `cancel`, `release` применяются с evidence и явным смыслом; освобождение reservation не подтверждает выполнение task. Для bound worker release/cancel требуют свежую quiescence evidence по CLI reference: один таймер не доказывает остановку writers. Старые данные/история не удаляются.

## После принятия

Сверить принятый результат с текущими файлами и зависимостями. В shared checkout следующий task может опираться на незакоммиченный принятый результат только через неизменную принятую lineage, которую проверяет CLI; произвольные dirty изменения не становятся разрешённой базой. Не редактировать accepted outputs под старым GO.

Сохранять доказательства отдельно: unit/API, browser, Docker, CI, публичное размещение. Визуальные страницы `design/`, probe, mocked UI и HTTP 200 не подтверждают продуктовый E2E. Обязательный полный сценарий и узкий/широкий экран проверяются по [test-plan](../test-plan.md) и [demo](../demo.md).

Перед разрешённым коммитом выполнить project checks, просмотреть только именованные принятые пути и индекс. Коммит/публикация не являются техническим условием освобождения booking. Push, deploy, платные сервисы, изменение DNS/доступа и сообщения людям требуют своего контекста и полномочий; этот workflow их не добавляет.

После принятого task продолжить `next` до проверенного P0, явного пользовательского stop или отсутствия готового независимого scope. Финальный отчёт: принятые задачи, реальные worker/reviewer identities, evidence и ограничения, сохранённые blockers/next actions. Принятие отдельного task не означает завершение всего MVP.
