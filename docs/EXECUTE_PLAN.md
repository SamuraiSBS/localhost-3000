# Команда «Выполняй план MVP»

В чате проекта `D:\localhost-3000` написать:

```text
Выполняй план MVP
```

Это проектная инструкция в [AGENTS.md](../AGENTS.md), устроенная по образцу реального MTCH workflow. Создание/обсуждение этой команды не является её запуском. [Аудит источника](coordination/mtch-reference-audit.md) объясняет, какие механизмы перенесены и какие специфичны для MTCH.

[Проверка механизма](coordination/validation.md): автоматические негативные сценарии, независимый review и фактический цикл worker → reviewer → Accept на отдельной тестовой задаче.

## Результат одного запуска

Текущий чат ведёт P0 по [плану](implementation-plan.md) до B11: выбирает готовую задачу, бронирует её, запускает исполнителя, организует независимую проверку, исправления и принятие, затем проверяет следующую задачу. Приёмка всего MVP требует полного [demo](demo.md), а не только нескольких accepted claims.

Команда продолжается, пока есть доступная работа в выданном scope. Реальная внешняя предпосылка, отсутствие независимой готовой задачи или прямой stop пользователя сохраняются как точный незавершённый статус. Они не превращают проект в «готово». Повторная фраза сверяет ledger и фактические файлы, затем продолжает без дублирования работы.

Необязательные график/OIDC B12–B13 требуют отдельного расширения scope после P0. Фоновое расписание не создаётся: если сеанс прерван, новый запуск возобновляется по сохранённому состоянию.

## Три реальные роли

| Роль | Ответственность | Инструкция |
|---|---|---|
| Оркестратор — текущий чат | Выбор, зависимости, резерв, actual actor binding, независимый review, Accept, следующий срез | [orchestrator.md](coordination/orchestrator.md) |
| Worker — новый субагент задачи | Read-only preflight, Claim, реализация точного scope, проверки, Submit; исправления после NO-GO | [worker.md](coordination/worker.md) |
| Reviewer — новый субагент каждого цикла | Фактический diff, критерии, нужные проверки; GO/NO-GO для текущего manifest | [reviewer.md](coordination/reviewer.md) |

Названия ролей в тексте не заменяют агентов. Привязывать IDs, реально возвращённые инструментом запуска, вместе с подтверждением роли/назначения и собственным фактическим `CODEX_THREAD_ID` агента. У родителя также сохраняется target/run UUID: строка `/root` встречается в разных чатах. Не подставлять `codex://threads/...` вместо native subagent ID. Другие пользовательские чаты не создаются и не получают сообщений.

## Механизм бронирования

[CLI](coordination/cli-reference.md) на Python stdlib управляет одним `.agent-state/mvp-coordination/claims.json`. Это локальное состояние вне Git, общее для работающих с данным checkout агентов. В v1 используется один checkout с непересекающимися точными scopes; отдельные clones/worktrees не объявляются поддержанными без регистрации общего состояния и интеграции.

Основной путь:

```text
readiness → Reserve → spawn actual worker → Bind → Check → Claim
→ implementation → Submit
→ spawn fresh reviewer → Bind reviewer → GO / NO-GO
→ repair + new review при NO-GO → Accept → next readiness scan
```

При продолжении в новом запуске сверить пару владельца из status с точечным `CODEX_THREAD_ID`. Если UUID расходятся, передать ownership через документированную `handoff` с прямым поручением пользователя продолжить именно здесь; не переиспользовать прежний run ID. Переход доступен только для того же `/root`, точного ожидаемого старого владельца и при отсутствии активных attempts; он требует CAS и сохраняет обе пары в истории. Ledger не редактируется вручную.

Скрипт сериализует короткие изменения ledger через OS lock, проверяет CAS revision, зависимости, хеш плана, точные paths/resources и reviewed manifest. Он не запускает AI-агентов сам: это делает оркестратор доступными tools. Долгая реализация и ожидание review происходят вне lock.

Pending reservation уже удерживает task/files/resources. Claim — отдельный допуск к правкам после реального binding. Изменённые после GO байты не принимаются; reviewer не принимает свой код. Заблокированная задача сохраняет резерв до штатного Resume/Release/Cancel с необходимыми доказательствами. Старый возраст записи не доказывает остановку worker.

Завершённая работа отдельного чата в том же checkout может получить только provenance через предусмотренный B14 `record-snapshot`, если это прямо разрешил пользователь в текущем чате. Snapshot фиксирует конечные пути и точный manifest, не становится принятой реализацией и не заменяет task/review. CLI требует свежего наблюдения исходного чата, совпадения root и списка `--file`, а перед Submit/Accept снова сверяет все captured bytes/mode. Отдельный прямой запрос пользователя может разрешить reviewed recovery уже drifted snapshot через append-only `propose-snapshot-recovery`: pending manifest открывается только конкретному claimed B14 attempt, а другим attempts — после свежего независимого GO по совпадающему candidate/iteration и отдельного orchestrator Accept. Если proposal устарел до первого candidate, текущий orchestrator может один раз выполнить append-only `amend-snapshot-recovery` для той же live B14 attempt, snapshot и исходной iteration. Это требует отдельного прямого разрешения пользователя текущего чата и фиксирует новый exact manifest без изменения исходных proposal/snapshot. Amendment запрещена после candidate/review/NO_GO/смены iteration/закрытия; второй amendment нет. До amendment допускается не более одного same-iteration plan rebase без candidate/review, чтобы привязать authorization к обновлённому плану; после amendment rebase запрещён. Любое изменение bytes/mode/deletion после pinned manifest закрывает путь; после NO_GO повторное использование исходных proposal и amendment для repair допустимо лишь при exact manifest и непрерывной audit-цепочке candidate + matching NO_GO на каждой промежуточной iteration. Proposal сохраняет исходную iteration, а каждый новый candidate/review/Accept связывается с текущими iteration, digest, exact manifest и amendment ID/hash; новый proposal для той же пары attempt/snapshot запрещён. Исходная snapshot запись и manifest остаются неизменны. Не добавлять игнорируемые пути и не редактировать ledger напрямую.

Если manifest дрейфует после candidate/NO_GO, исходный `amend-snapshot-recovery` по-прежнему запрещён. На следующей iteration тот же root может append-only вызвать `continue-snapshot-recovery` только при matching independent NO_GO, точной root observation текущего полного snapshot manifest и свежем прямом разрешении пользователя. Continuation связывает прежний candidate/NO_GO, исходный proposal/pre-submit amendment, B14 attempt, текущие plan/HEAD/index/baseline/scope и workspace-delta origin; action требует `--manifest-sha256` и точное совпадение pins. Новый candidate и review включают актуальный continuation ID/hash/sequence и exact workspace-delta chain entry; при изменении внешней delta root отдельно append-ит её amendment на этой же iteration. Accept требует свежий GO и записывает snapshot + workspace approval receipts одним событием; без полного bundle другой попытке recovery недоступен. Любой новый drift снова fail-closed. Не менять snapshots, прежние receipts или ledger вручную.

Если Submit B14 всё ещё фиксирует конкретное unexplained workspace delta вне B14 scope, отдельная прямая user authorization может разрешить одно append-only `record-workspace-recovery` до первого candidate. Root должен независимо сверить sorted exact changed-path manifest (path/mode/SHA-256/size или явное absence), исключив accepted lineage, closed manifests и текущий snapshot recovery, а затем передать его digest через обязательный `--manifest-sha256`. Запись привязывается к той же live B14 attempt, iteration, plan/base/index, scope и одному актуальному amended snapshot recovery. Она не даёт другим attempts pending доступа; после независимого GO нужен отдельный Accept с двумя взаимно связанными snapshot/workspace receipts. Candidate digest, review evidence и Accept должны ссылаться на recovery ID/hash/manifest digest. Любой новый byte/mode/deletion/addition drift снова блокирует Submit/Accept. После NO_GO исходную запись можно повторно использовать для repair только с полной candidate + matching NO_GO audit цепочкой на каждой предыдущей iteration; новую запись после NO_GO/candidate/review, повторную запись, rebase/plan change или close создавать нельзя. Сохранять исходные snapshot/proposal/amendment и все подтверждённые внешние файлы; ledger менять только штатным CLI.

Git dirty/untracked сохраняются. Для последующего изменения ещё не закоммиченного принятого файла нужна явная проверяемая lineage предыдущего accepted manifest; чужой dirty нельзя «усыновить» общим force. Дополнительные файлы/изменение плана проходят поддержанную Extend/Rebase операцию, которая инвалидирует старый review.

Actor IDs, evidence и роли проверяются на согласованность; локальный CLI не удостоверяет личность процесса или истинность ответа организатора. Оркестратор связывает реальные tool outputs и проверяет источники. Это защита от ошибок координации, не новая граница безопасности ОС.

## Что читать

В начале: [START_HERE](START_HERE.md), план/backlog и статус ledger. Далее — относящиеся к выбранной задаче model/API/architecture/дизайн/приёмка. Не перечитывать весь комплект документов перед каждой мелкой правкой. Реализующий worker применяет `mvp-builder`, browser приёмка — `demo-qa`.

Read-only проверка состояния:

```powershell
python scripts/mvp_coordination.py status
python scripts/mvp_coordination.py next
```

Эти команды не резервируют задачи, не создают state и не запускают продукт. Детали мутаций и актуальные флаги находятся в CLI reference и `--help`.

## Готовность и границы

Сейчас B00 требует подтверждённых Q-001–003 о конкурсной допустимости prebuilt/OSS/AI. Настройка этого механизма не выдаёт такого разрешения. Оркестратор учитывает уже полученные ответы; неизвестный ответ не заменяется предположением. Дедлайн/канал Q-004 выясняются параллельно до сдачи, права Q-005 — до соответствующего распространения.

В task-owned scope разрешены обычные локальные prerequisite setup, зависимости, запуск сервера и браузера, синтетические примеры в отдельных файлах, исправления и нужные проверки. Сохранить Digital Ruins, исходные материалы и чужую работу. Не объявлять probe/mock/API200 доказательством browser MVP.

Как в MTCH, команда включает небольшие локальные checkpoints после проверки: отдельная короткая ветка, только просмотренные exact paths, без чужих dirty/staged файлов. Подготовленные этим заданием планы и координатор сохраняются отдельным локальным checkpoint до первого продуктового Claim, чтобы позднее B09–B11 могли штатно менять документацию. CLI не выполняет commit и не регистрирует его автоматически; оркестратор сохраняет фактический SHA в отчёте. Push, публичный deploy, DNS/доступы, покупки и внешние сообщения не входят в короткую команду.

Отчёты B10/B11 хранить в `artifacts/acceptance/` и документах сдачи. Изменение файлов из `contract_documents` означает изменение основы приёмки: старые accepted зависимости становятся неактуальными и требуют повторной проверки. Добавлять результаты тестов в такой контракт вместо отдельного отчёта не следует.

Краткий итог запуска: accepted task IDs, реальные worker/reviewer IDs, проверки/окружение/revision, текущий blocker или следующая задача. Полная готовность ставится только после приёмки всего P0 и подготовленного пакета сдачи.

## Узкое правило B31 snapshot recovery

Preflight B31 разрешает ровно одно начальное recovery proposal для claimed B31 attempt и неизменяемого snapshot `ac1a0c41-a76b-46c6-84f2-aa2008ae2837`. Root записывает его после Claim и после восстановления task-owned `AGENTS.md`, до первого candidate. Исходный snapshot, provenance и B14 history остаются неизменными; proposal фиксирует exact current manifest двух claimed путей. `AGENTS.md` и `components.json` остаются замороженными до Accept. B31 запрещает amendment и continuation, а любой manifest drift блокирует Submit/Accept. Candidate, review и Accept связываются с единственным proposal, точной provenance, прямой авторизацией, task/attempt/iteration и manifest. Другие tasks или snapshots не получают этот recovery allowlist.
