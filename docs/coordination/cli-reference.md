# CLI координации MVP

Интерфейс v1: `python scripts/mvp_coordination.py [--root WORKSPACE] ACTION ...`.
Корень по умолчанию — checkout, содержащий скрипт. Поддерживается один shared checkout; отдельные worktrees в v1 не поддерживаются. Ledger: `.agent-state/mvp-coordination/claims.json`. CLI не создаёт агентов, не пишет продукт, не коммитит, не удаляет рабочие файлы. Проверки identity и source evidence процедурные, а не криптографическая авторизация.

Каждый успешный ответ — JSON `{ok:true, action, revision, ...}`. Отказ — JSON `{ok:false, error:{code,message}}` в stderr, exit 2. **Любая мутация, включая root actions**, требует `--expected-revision N --actor TARGET --run-id UUID`. Для реальных native subagents TARGET имеет вид `/root/mvp_b01_worker`, а не `codex://threads/...`. Orchestrator — canonical target родительского агента, обычно `/root`, **вместе с его actual run UUID**; другой чат с target `/root`, но другим UUID, не владеет этим ledger.

Другой чат с target `/root`, но другим UUID, не владеет ledger. Единственное исключение — явная `handoff`: она передаёт владельца тому же `/root` после проверки свежей пользовательской авторизации и при отсутствии активных claims.

## Команды

| Action | Дополнительные аргументы | Кто выполняет |
|---|---|---|
| `status` | Нет | Любой; строго read-only |
| `next` | Нет; возвращает все причины готовности, P0 ready отдельно от P1 | Любой; строго read-only |
| `check` | `--task Bxx --file PATH` повторяемый, `--resource NAME` повторяемый, `--adopt-accepted ATTEMPT_ID` повторяемый | Любой; advisory read-only |
| `record-gate` | `--task Bxx --evidence FILE --reason TEXT` | Orchestrator; только фактический внешний gate, без фиктивного review |
| `record-snapshot` | `--file PATH` повторяемый, `--evidence FILE --reason TEXT` | Текущий orchestrator; только точные файлы завершённой отдельной работы с прямой авторизацией |
| `handoff` | `--from-run-id OLD_UUID --evidence USER_AUTHORIZATION_FILE --reason TEXT` | Текущий чат `/root`; только передача того же target при отсутствии active attempts |
| `recover-handoff` | `--from-run-id OLD_UUID --attempt ABANDONED_UUID --evidence FILE --reason TEXT` | Текущий `/root`; отдельная прямая авторизация operator abandonment единственного active claim |
| `reserve` | `--task Bxx --file PATH` повторяемый, `--resource NAME` повторяемый, `--reason TEXT`, optional `--adopt-accepted ATTEMPT_ID` повторяемый | Orchestrator |
| `bind` | `--attempt UUID --tool-evidence FILE` | Orchestrator; реальный freshly spawned worker |
| `claim` | `--attempt UUID --run-id UUID` | Bound worker; первая точка разрешения правок |
| `submit` | `--attempt UUID --run-id UUID --evidence FILE` | Bound worker; прекращает правки и создаёт immutable candidate digest |
| `bind-reviewer` | `--attempt UUID --tool-evidence FILE` | Orchestrator; свежий независимый reviewer |
| `review` | `--attempt UUID --run-id UUID --evidence FILE` | Bound reviewer; GO либо NO_GO из receipt |
| `accept` | `--attempt UUID --reason TEXT` | Orchestrator; только current GO и неизменные bytes |
| `block` | `--attempt UUID --reason TEXT` | Worker или orchestrator; резерв сохраняется |
| `resume` | `--attempt UUID --reason TEXT` | Orchestrator; тот же worker, новый review после исправления |
| `release`, `cancel` | `--attempt UUID --reason TEXT`; для bound worker обязательно `--evidence QUIESCENCE_FILE` | Orchestrator; release возвращает задачу в очередь; история и исходники сохраняются |
| `extend` | `--attempt UUID --file PATH` повторяемый, optional `--resource NAME`, `--reason TEXT` | Orchestrator; добавляет scope после проверки конфликтов, инвалидирует candidate/review |
| `rebase` | `--attempt UUID --reason TEXT` | Orchestrator; принимает текущую версию плана после её проверки, инвалидирует candidate/review |

`--file` всегда означает один literal repository-relative файл, включая ещё не созданный. Glob и directory scopes запрещены. `suggested_paths` в backlog — подсказки, не исполняемый allowlist. Защищены `.git`, локальное state, секреты, исходные `материалы/`. `.env.example` и `.env.production.example` разрешены как безопасные шаблоны. Exact evidence artifacts могут входить в scope; игнорируемые runtime/cache файлы автоматически не становятся исходниками.

## Evidence JSON

Файлы evidence UTF-8 JSON находятся внутри выбранного workspace, предпочтительно `artifacts/coordination/`. Ledger сохраняет копию payload и SHA-256: изменение внешнего файла не переписывает историю. Нельзя указывать `.env`, Git metadata или файлы вне workspace.

### Bind worker / reviewer

Это нормализованная запись **фактического результата** native agent tool и handshake. Не сочинять UUID или target. Если spawn/list tool не возвращает UUID, worker точечно читает собственный `CODEX_THREAD_ID` и сообщает parent; root так же точечно читает собственный ID. Полные окружения не выводятся. В receipt указать `identity_source="spawn+CODEX_THREAD_ID_handshake"`. Сравниваются parent target **и** run с сохранённым orchestrator, child target и run со своей ролью.

```json
{
  "tool": "collaboration.spawn_agent",
  "identity_source": "spawn+CODEX_THREAD_ID_handshake",
  "parent_target": "/root",
  "parent_run_id": "11111111-1111-4111-8111-111111111111",
  "target": "/root/mvp_b01_worker",
  "run_id": "22222222-2222-4222-8222-222222222222",
  "observed_at": "2026-10-23T12:00:00Z",
  "task_id": "B01",
  "attempt_id": "33333333-3333-4333-8333-333333333333",
  "role": "worker"
}
```

UUID в примерах — только форма, **не допустимые реальные свидетельства**. Для reviewer `role="reviewer"`, другой target/run; `bind-reviewer` дополнительно возвращает случайный `review_nonce`, который reviewer включает в receipt. Parent target/run должны совпасть с записанным orchestrator. Evidence не старше 5 минут; worker и reviewer identities нельзя повторно использовать в другом attempt, reviewer также нельзя повторять в следующем цикле.

### Worker submit

```json
{
  "task_id": "B01",
  "attempt_id": "33333333-3333-4333-8333-333333333333",
  "summary": "Конкретный результат и границы проверки",
  "checks": [{"name": "DTO import", "status": "passed", "evidence": "Команда, версия и наблюдаемый результат"}]
}
```

Допустимы `passed`, `failed`, `not_run`, `not_required`; непустой список checks обязателен. Не выполненные проверки не превращаются в passed. Candidate хранит exact scope manifest, plan hash, task definition hash, iteration, report. Возвращаемые `candidate_digest`, `iteration`, `plan_hash` передаются reviewer.

### Reviewer receipt

```json
{
  "task_id": "B01",
  "attempt_id": "33333333-3333-4333-8333-333333333333",
  "iteration": 1,
  "candidate_digest": "64 hex characters returned by submit",
  "plan_hash": "64 hex characters returned by submit",
  "review_nonce": "returned by bind-reviewer",
  "result": "GO",
  "summary": "Независимая проверка фактических файлов и критериев",
  "checks": [{"name": "Acceptance", "status": "passed", "evidence": "Ссылка на наблюдаемую проверку"}],
  "findings": []
}
```

NO_GO требует непустые actionable `findings`. GO запрещён при failed/not_run checks, но `not_required` с обоснованием разрешён. Reviewer должен сам проверить обязательные criteria; CLI не доказывает истинность текста отчёта. NO_GO → `changes_requested`, тот же worker исправляет и submit создаёт следующую iteration; нужен новый reviewer.

### Внешний gate

```json
{
  "task_id": "B00",
  "actor": "/root",
  "answers": [
    {"question_id": "Q-001", "decision": "allowed", "answer": "Фактический ответ", "source": "Первоисточник ответа", "received_at": "2026-10-23T12:00:00Z"},
    {"question_id": "Q-002", "decision": "allowed", "answer": "Фактический ответ", "source": "Первоисточник ответа", "received_at": "2026-10-23T12:00:00Z"},
    {"question_id": "Q-003", "decision": "allowed", "answer": "Фактический ответ", "source": "Первоисточник ответа", "received_at": "2026-10-23T12:00:00Z"}
  ]
}
```

Для B00 необходимы все `questions` из backlog. `denied`/`unknown`/null не открывают gate. Для task с `additional_gate` требуется отдельный answer с `question_id="additional_gate"`. Ответы должны быть полученными фактами, не предположениями агента. `record-gate` закрывает только external-facts задачу B00; для обычной продуктовой задачи он открывает gate, но не заменяет worker/reviewer/accept.

### Handoff orchestrator

Handoff требуется, когда прямое поручение пользователя продолжить работу именно в текущем чате, но сохранённая пара `/root` + run UUID принадлежит другому запуску. Передача не принимает, не снимает и не меняет claims: CLI откажет, пока есть любой active attempt. Target остаётся `/root`; `--from-run-id` должен совпасть с записанным владельцем, а `--run-id` — с точечным `CODEX_THREAD_ID` текущего процесса. CAS обязателен.

```json
{
  "kind": "mvp_orchestrator_handoff_authorization",
  "source": "direct_user_reply_in_current_chat",
  "current_chat_selection": "в текущем чате",
  "current_chat_selected": true,
  "user_authorization": "Прямая формулировка подтверждения пользователя",
  "previous_orchestrator": {"target": "/root", "run_id": "записанный UUID"},
  "next_orchestrator": {"target": "/root", "run_id": "CODEX_THREAD_ID текущего чата"},
  "observed_at": "2026-10-23T12:00:00Z"
}
```

Evidence создаётся по реальному ответу пользователя не более пяти минут назад и называет обе точные пары владельца. Успешный event хранит предыдущего/следующего владельца, payload и SHA-256 источника. Ошибка или устаревшая revision не меняет ledger. CLI фиксирует процедурные свидетельства; он не может криптографически удостоверить автора evidence.

```text
python scripts/mvp_coordination.py --root WORKSPACE handoff --from-run-id OLD_UUID --evidence USER_AUTHORIZATION_FILE --reason USER_SELECTED_CURRENT_CHAT --actor /root --run-id CURRENT_CODEX_THREAD_ID --expected-revision CURRENT_REVISION
```

### Явное восстановление после недоступного worker

`recover-handoff` — отдельная операторская процедура, требующая прямого разрешения пользователя именно на восстановление в текущем чате. Простое повторение команды MVP, timeout, `notLoaded` или отсутствие worker не подтверждают остановку. Обычные `handoff`, `release` и `cancel` сохраняют прежние ограничения. Bootstrap-ремонт самого coordinator при невозможности reserve допускается только по отдельному разрешению пользователя, с точным scope, тестами и независимым review до live mutation; это не разрешение править продукт или ledger вручную.

Команда проверяет CAS, точные прежний owner/run, новый `/root` и его фактический `CODEX_THREAD_ID`. Названный claimed attempt с worker должен быть единственным active attempt. Evidence использует поля обычного handoff (`source`, `current_chat_selection`, `current_chat_selected`, `user_authorization`, `previous_orchestrator`, `next_orchestrator`, `observed_at`), но `kind="mvp_orchestrator_recovery_authorization"`. Дополнительно обязательны `attempt_id`, `task_id`, `abandoned_worker={"target": "точный target", "run_id": "точный UUID"}`, `policy="abandon_unverified_worker"`, `abandonment_authorized=true`, `manifest`. Manifest — полный результат `Coordinator.manifest(attempt.files)`: mapping exact paths в `{kind:"file", sha256, size, executable}` либо `{kind:"absent"}`. `writers_stopped` и `quiescence` в recovery evidence запрещены: остановка не доказана.

Пять минут относятся к свежему наблюдению источника сохраняющегося прямого разрешения вместе с текущими CAS/manifest, а не к сроку действия ответа пользователя. Сохранять точный ответ и контекст исходного вопроса; если время ответа известно, записать его отдельно как `authorized_at`. `observed_at` обозначает свежую инструментальную проверку источника в текущем чате (например, `read_thread`), не новое время ответа. Нельзя выдать старый ответ за только что полученный.

За одну revision attempt становится `cancelled`, сохраняет историю и незавершённые bytes, получает `closed_manifest` и `operator_abandonment` с копией evidence/SHA-256; owner меняется на текущий чат. Приёмка, candidate и GO не переносятся в новую работу. Смена owner и closed status запрещают старому owner/worker дальнейшие CLI mutations. Это процедурный fence, не OS sandbox: coordinator не останавливает внешний процесс и не доказывает отсутствие filesystem writers. Если есть признаки продолжающихся записей, продуктовые работы не возобновлять до устранения writer.

Для новой попытки той же задачи `check` и `reserve` принимают явный повторяемый `--adopt-recovered ATTEMPT_ID`. Только recovery recipient может взять прежние claimed paths с точными сохранёнными bytes/modes/absence. Проверяется полный closed manifest исходного attempt; обычный cancelled/released claim, другая задача или изменённый файл не подходят. Остальные dirty paths требуют обычной accepted lineage либо остаются запрещены. Новая reserve записывает `adopted_recovered`, новый baseline текущего checkout и новый attempt ID; затем обязательны новый worker, Claim, независимый review всех результатов и Accept. Recovery adoption не создаёт accepted/dependency lineage.

```text
python scripts/mvp_coordination.py --root WORKSPACE recover-handoff --from-run-id OLD_UUID --attempt ABANDONED_UUID --evidence RECOVERY_JSON --reason USER_AUTHORIZED_RECOVERY --actor /root --run-id CURRENT_CODEX_THREAD_ID --expected-revision CURRENT_REVISION
```

### Snapshot завершённой отдельной работы

`record-snapshot` добавляет в текущий ledger отдельную неизменяемую запись provenance. Она разрешает `outside_changes` сохранить только записанные bytes и режим файлов; запись не создаёт task acceptance, dependency lineage или право `--adopt-accepted`. Snapshot не подтверждает содержательную корректность и не заменяет review исходного чата.

Текущий `/root` owner/run должен совпадать с `--actor`/`--run-id`, а `CODEX_THREAD_ID` процесса — с `--run-id`. Evidence не старше пяти минут подтверждает завершённый исходный чат в том же primary checkout и содержит тот же конечный список, который передан повторяющимся `--file`. Список сортируется по repository-relative пути; CLI, evidence и observation должны перечислять все пути один раз и в одинаковом порядке. При создании запрещены небезопасные/защищённые пути, любой overlap с active claim и overlap с уже сохранённым snapshot. Каждый указанный файл должен существовать как обычный файл.

```json
{
  "kind": "mvp_separate_workspace_snapshot",
  "observed_at": "2026-10-23T12:00:00Z",
  "source_chat": {
    "id": "11111111-1111-4111-8111-111111111111",
    "title": "Завершённая отдельная работа",
    "status": "completed",
    "root": "D:/localhost-3000",
    "files": ["design/example.js", "design/index.html"]
  },
  "source_observation": {
    "tool": "mcp__codex_app__read_thread",
    "thread_id": "11111111-1111-4111-8111-111111111111",
    "title": "Завершённая отдельная работа",
    "status": "completed",
    "root": "D:/localhost-3000",
    "files": ["design/example.js", "design/index.html"],
    "observed_at": "2026-10-23T12:00:00Z"
  },
  "authorization": {
    "source": "direct_user_reply_in_current_chat",
    "current_chat_run_id": "22222222-2222-4222-8222-222222222222",
    "authorized": true,
    "user_authorization": "Прямая формулировка пользователя о snapshot этой работы"
  }
}
```

UUID, заголовок, root, список файлов и формулировка авторизации выше — только схема. Оркестратор подставляет реальные значения из текущего `CODEX_THREAD_ID`, ответа пользователя и свежего результата `read_thread`; он не выводит из примера ни один факт. Пример команды:

```text
python scripts/mvp_coordination.py --root WORKSPACE record-snapshot --file design/example.js --file design/index.html --evidence artifacts/coordination/source-chat-snapshot.json --reason USER_AUTHORIZED_COMPLETED_CHAT --actor /root --run-id CURRENT_CODEX_THREAD_ID --expected-revision CURRENT_REVISION
```

Ledger хранит уникальный snapshot ID, source chat ID/title/status/root, конечные записи `{path, mode, sha256, size}`, direct authorization, raw-evidence payload с SHA-256, orchestrator target/run и время записи. Snapshot-ы только добавляются; запись на уже snapshotted path запрещена. На последующем `submit`/`accept` coordinator сначала сверяет все snapshotted files с их exact manifest. Любое изменение bytes, режима или отсутствие файла закрывает операцию с `snapshot_drift`, даже если другой механизм provenance мог бы разрешить эти bytes. Совпадающий snapshot даёт право только на точные текущие bytes. Старая v1 ledger без `snapshots` читается как пустой список; её attempts и audit events не переписываются.

### Quiescence для release/cancel

```json
{
  "tool": "collaboration.list_agents",
  "target": "/root/mvp_b01_worker",
  "run_id": "22222222-2222-4222-8222-222222222222",
  "observed_at": "2026-10-23T12:00:00Z",
  "status": "completed",
  "writers_stopped": true,
  "evidence": "Наблюдаемый статус и подтверждение, что task-owned writers остановлены"
}
```

Также принимается `collaboration.interrupt_agent`; inactive status — `idle`, `completed`, `interrupted`. Свидетельство не старше 5 минут и должно совпасть с bound worker target/run. Один прошедший таймер не подтверждает quiescence.

## Переходы, план и dirty provenance

`reserved → bound → in_progress → awaiting_review → accepted`; NO_GO → `changes_requested → submit` с новой iteration. Block удерживает ownership. Release/cancel закрывают attempt, но не удаляют bytes. После release новая попытка получает новый ID и новых агентов.

Fingerprint плана включает нормализованные определения backlog и содержимое `contract_documents`. Из task definitions исключены только runtime `status/evidence`, из верхнего уровня — `updated_on/product_status/planning_status`. Контрактные правки требуют явного `rebase` каждого затронутого live attempt. Rebase не переписывает backlog, сохраняет предыдущий plan/base в event history, обновляет bound plan и HEAD после явной проверки orchestrator и сбрасывает candidate/reviewer/GO. Он не обновляет исходный filesystem baseline и не скрывает изменения вне scope. Accepted dependencies проверяются по актуальному task definition, fingerprint контрактов и последнему accepted lineage файлов. Изменение контракта снимает актуальность прошлой приёмки: зависимости нужно заново проверить обычным worker/reviewer/Accept циклом, начиная с первой устаревшей задачи. Rebase текущей задачи не заменяет эту проверку. Новое обязательное условие также требует нового sourced gate. Последующее accepted изменение файла заменяет его lineage. Добавление несвязанной задачи само по себе не инвалидирует прежний accepted результат.

Reserve сохраняет baseline tracked+untracked nonignored файлов и HEAD. Pre-existing dirty scope отклоняется. Исключение — explicit `--adopt-accepted ID`, когда текущие bytes/absence совпадают с последним accepted manifest и нет live владельца. При claim повторно проверяются собственный baseline, dependencies и plan. При submit чужие изменения вне scope допускаются в scope другого live claim, совпадающем accepted lineage, точном `closed_manifest` остановленного released/cancelled peer или exact manifest свежего completed-chat snapshot. Snapshot не становится accepted lineage. Все его bytes и mode повторно проверяются перед разрешением; изменение либо удаление snapshotted файла блокирует Submit/Accept. Snapshot нельзя заменить или обновить поверх существующей записи. В общем checkout CLI не способен доказать автора filesystem write: это обнаружение изменений и дисциплина владения, не OS sandbox.

При повреждённом ledger — fail closed. Нет automatic reset, expiry, force, commit/push/deploy. Нужна совместная работа через CLI: неподчиняющийся локальный процесс имеет те же права на файловую систему.
