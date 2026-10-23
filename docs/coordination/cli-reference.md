# CLI координации MVP

Интерфейс v1: `python scripts/mvp_coordination.py [--root WORKSPACE] ACTION ...`.
Корень по умолчанию — checkout, содержащий скрипт. Поддерживается один shared checkout; отдельные worktrees в v1 не поддерживаются. Ledger: `.agent-state/mvp-coordination/claims.json`. CLI не создаёт агентов, не пишет продукт, не коммитит, не удаляет рабочие файлы. Проверки identity и source evidence процедурные, а не криптографическая авторизация.

Каждый успешный ответ — JSON `{ok:true, action, revision, ...}`. Отказ — JSON `{ok:false, error:{code,message}}` в stderr, exit 2. **Любая мутация, включая root actions**, требует `--expected-revision N --actor TARGET --run-id UUID`. Для реальных native subagents TARGET имеет вид `/root/mvp_b01_worker`, а не `codex://threads/...`. Orchestrator — canonical target родительского агента, обычно `/root`, **вместе с его actual run UUID**; другой чат с target `/root`, но другим UUID, не владеет этим ledger.

## Команды

| Action | Дополнительные аргументы | Кто выполняет |
|---|---|---|
| `status` | Нет | Любой; строго read-only |
| `next` | Нет; возвращает все причины готовности, P0 ready отдельно от P1 | Любой; строго read-only |
| `check` | `--task Bxx --file PATH` повторяемый, `--resource NAME` повторяемый, `--adopt-accepted ATTEMPT_ID` повторяемый | Любой; advisory read-only |
| `record-gate` | `--task Bxx --evidence FILE --reason TEXT` | Orchestrator; только фактический внешний gate, без фиктивного review |
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

Reserve сохраняет baseline tracked+untracked nonignored файлов и HEAD. Pre-existing dirty scope отклоняется. Исключение — explicit `--adopt-accepted ID`, когда текущие bytes/absence совпадают с последним accepted manifest и нет live владельца. При claim повторно проверяются собственный baseline, dependencies и plan. При submit чужие изменения вне scope допускаются в scope другого live claim, совпадающем accepted lineage либо точном `closed_manifest` остановленного released/cancelled peer. Последний фиксируется после quiescence; он сохраняет незавершённые bytes, но не даёт task acceptance или права их adoption. Изменение этих bytes после закрытия снова отклоняется. В общем checkout CLI не способен доказать автора filesystem write: это обнаружение изменений и дисциплина владения, не OS sandbox.

При повреждённом ledger — fail closed. Нет automatic reset, expiry, force, commit/push/deploy. Нужна совместная работа через CLI: неподчиняющийся локальный процесс имеет те же права на файловую систему.
