# Аудит механизма автономной разработки MTCH

Дата: 23 октября 2026 года. Назначение — перенести механизм управления разработкой на команду «Выполняй план MVP» в `D:\localhost-3000`. Это аудит исходников инструмента, не запуск очереди MTCH и не старт продуктовой задачи B01.

## Граница проверки

Прочитаны текущие `D:\mtch\AGENTS.md`, точные связанные документы и выбранные участки координатора/его тестов. Выполнены поиск по точной фразе, перечисление относящихся файлов и чтение исходников. Координатор MTCH не вызывался даже для List/Check; ReserveAssignment, AssignWorker, Claim и очередь не запускались. Файлы, индекс, worktrees, claims, процессы и данные MTCH не менялись. Текущее состояние задач/владельцев MTCH и успешность его тестов этим аудитом не устанавливаются.

Каталогов `D:\mtch\.agents\skills` и `D:\mtch\.codex` в просмотренном checkout нет. В `D:\Codex\skills` MTCH-навык не найден. Историческая памятка `D:\Codex\memories\skills\mtch-claim-safe-improvements\SKILL.md` прочитана как справочный материал, а не применена как исполняемый навык: у неё `disable-model-invocation: true` и `user-invocable: false` (стр. 5–6). Её старое правило остановки при overlap (стр. 37) прямо ограничено действующим `D:\mtch\AGENTS.md:20–26` и `docs/mtch_queue_selection.md:3–9`.

## Где находится настоящий trigger

Точная фраза и алиас определены в `D:\mtch\AGENTS.md:127–139`, особенно стр. 129–131:

- «Запусти следующее улучшение MTCH автономно»;
- «Делай улучшения MTCH автономно» — равнозначный алиас.

Это инструкция агенту, а не зарегистрированный shell-command, webhook или автоматический фоновый сервис. Агент распознаёт фразу, читает план, проверяет готовность, запускает необходимые агентные роли и вызывает детерминированный координатор. Сам `plan-coordination.ps1` не создаёт агентов/чаты и не выполняет продуктовые задачи: он проверяет и сохраняет назначения, claims, review, принятие и Git provenance. Настройка trigger сама по себе не запускает очередь (`AGENTS.md:70,130`).

План R-очереди — `D:\mtch\mtch_improvements_implementation_plan.md`; постоянные ID — `IMP-Rxx-Syy`, literal Stage/PlanRef и plan SHA-256. Это предметные идентификаторы MTCH; переносить их в MVP нельзя.

## Канонические источники и точные места

Во всех ссылках таблицы корень — `D:\mtch`.

| Источник | Что подтверждает |
|---|---|
| `AGENTS.md:28–34` | Короткий reserve до долгого setup, отсутствие глобальной блокировки новой очереди |
| `AGENTS.md:45–56` | Один registry, registered worktrees, stable contracts, sequential integration |
| `AGENTS.md:64–70` | Selecting orchestrator продолжает скан независимых задач, worker выполняет только свой scope |
| `AGENTS.md:89–98` | Dedicated executor, свежий независимый reviewer, NO-GO repair, GO/Accept/commit, это developer tooling |
| `AGENTS.md:127–141` | Trigger, полномочия, план, роли, границы принятия и отсутствия push/deploy |
| `docs/mtch_worktree_coordination.md:23–35` | ReserveAssignment → worktree/worker → AssignWorker CAS → Check/Claim |
| `docs/mtch_worktree_coordination.md:139–152` | Review bytes, RegisterCommit и отдельная последовательная интеграция |
| `docs/mtch_queue_selection.md:11–26,61–90` | Readiness scan, pending-verification, ledger и структурная граница conclusiveNoReady |
| `docs/mtch_runtime_preparation.md:5–19,21–29` | Подготовка task-owned runtime входит в автономную работу; доказательства настоящего blocker |
| `scripts/plan-coordination.ps1:64–78,86–104` | Канонический primary root, один registry, named mutex, статусы |
| `scripts/plan-coordination.ps1:945–1006` | Реальная реализация ReserveAssignment и AssignWorker |
| `scripts/plan-coordination.ps1:1058–1209` | Read-only Check и атомарное создание Claim/потребление assignment |
| `scripts/plan-coordination.ps1:1357–1440,1648–1667` | Mark, независимый Review, iteration, Accept |
| `scripts/plan-coordination.ps1:143–205,1555–1565` | Snapshot blob/mode, exact commit, RegisterCommit |
| `scripts/mtch-worktree.ps1:33–72` | Проверка корня/assignment и создание отдельного checkout без копирования dirty рабочего дерева |

## Последовательность работы

1. **Прочитать исходный план и критерии.** Сверить текущие chats/claims/assignments, dirty/index, зависимости, решения владельца, точный набор файлов, принятый base и runtime resources. На кандидата нужны наблюдаемый результат и приёмка; номер плана сам по себе не задаёт готовность.
2. **ReserveAssignment.** Оркестратор под коротким mutex сохраняет точный task/stage/plan/hash/base, paths, dependencies, decisions, contracts и resources. Получает `assignmentId`, `status=pending`, `workerThread=null`. Это разрешает подготовку конкретного рабочего места, но не продуктовые правки.
3. **Создать/запустить конкретного worker.** В MTCH это отдельный task chat/worktree с реальной identity. Долгий setup идёт вне глобального владения очередью. Пока identity не возвращена, assignment остаётся видимым; выдуманный ID недопустим.
4. **AssignWorker.** Selecting owner привязывает точный `WorkerThread` один раз. Требуется `ExpectedAssignmentUpdatedAt`, строго совпадающий с сохранённой строкой timestamp. Повторная привязка, другой selecting owner или занятый worker отклоняются.
5. **Check → Claim.** Check возвращает механический отчёт, ничего не резервирует. Claim повторяет проверки под mutex и атомарно создаёт claim с `in_progress`, одновременно превращая assignment в `claimed`. Проверяются base HEAD, plan hash, identity, точное совпадение fields с assignment, зависимости, paths и ресурсы. Только затем executor получает право менять свои файлы.
6. **Реализация dedicated executor.** Точный scope, относящиеся тесты, доказательства и task-owned runtime. Дополнительный файл сначала добавляется штатным Extend. Оркестратор не подменяет worker незаметной самостоятельной реализацией.
7. **Mark awaiting_review.** Executor прекращает правки; оркестратор передаёт результат свежему независимому read-only reviewer. Reviewer читает фактический diff и проверяет критерии/тесты, а не только отчёт автора.
8. **Review.** GO/NO-GO записывается отдельным действием reviewer с evidence и snapshot текущих blob/mode. NO-GO сохраняет тот же claim/scope, увеличивает iteration и возвращает исправление тому же executor. Следующий цикл требует нового reviewer.
9. **Accept.** Только владелец-оркестратор; нужен GO текущей итерации и совпадение reviewed bytes. Принятие среза освобождает резерв и не доказывает завершение фичи/продукта.
10. **Exact-path commit → RegisterCommit.** Commit содержит только принятые пути. В worktree-потоке commit разрешён и до review; его содержимое всё равно должно совпасть с GO. RegisterCommit требует accepted claim, чистый bound HEAD, один non-merge commit непосредственно от baseRef, непустой diff, scope subset и совпадение review manifest. `Accept` и `RegisterCommit` — разные события.
11. **Отдельная интеграция при использовании worktrees.** Один зарегистрированный commit переносится в чистый integration checkout. Конфликт сохраняется; автоматического destructive reset/abort нет. После исправления нужен новый ReviewIntegration и FinalizeIntegration. Git landing не доказывает runtime acceptance.
12. **Снова скан готовности.** Завершение одного среза не завершает очередь. Готовые независимые задачи можно запускать параллельно; если их нет, отчёт содержит текущие доказательства, а не историческую причину или один занятый путь.

## Роли и identity: важное различие для переноса

| Поле/роль MTCH | Реальное значение |
|---|---|
| Selecting dispatcher | Чат, выбирающий несколько independent assignments |
| `assignment.dispatcherThread` | Реальный `codex://threads/...` selecting owner |
| `assignment.workerThread` | Реальный отдельный task chat, который станет владельцем claim |
| `claim.ownerThread` | Чат задачи-оркестратор, не executor subagent |
| `claim.owner` | Identity отдельного dedicated executor subagent |
| `review.reviewer` | Identity другого свежего read-only reviewer subagent |
| `ActorRole`, `Actor` | Переданные CLI роль и identity; проверяются правилами операции |

MTCH действительно имеет два уровня: selecting dispatcher → task chat orchestrator → executor/reviewer. Это нельзя перенести в текущую collaboration-схему, подставляя выдуманные thread links вместо subagent ID. Для MVP достаточно одного пользовательского чата-оркестратора и реальных worker/reviewer subagents; ledger должен раздельно хранить root orchestrator identity и IDs, которые вернули инструменты создания агентов. Пользовательские чаты для внутренних подзадач не требуются.

## Механические гарантии и их пределы

- **Mutex:** `Local\MTCHPlanCoordination` (`plan-coordination.ps1:73–74`), захват с timeout 20 секунд (`894–904`). Он сериализует чтение/проверку/запись registry, но не удерживается на весь runtime/setup. Это имя относится к MTCH; новому репозиторию нужно собственное имя/идентификатор корня.
- **Один canonical registry:** `.agent-state/plan-coordination/claims.json`; registered checkout должен иметь тот же Git common directory (`86–104`). Локальный registry не является второй базой продукта.
- **CAS:** exact строка `updatedAt` на AssignWorker/CancelAssignment (`992–1016`), отдельный CAS у Transfer/Release. JSON читается `ConvertFrom-Json -DateKind String` (`309–325`). Не все операции имеют общий numeric state revision: копировать это ограничение в новый компактный coordinator не обязательно.
- **Запись:** временный UTF-8 JSON, backup старого файла и replacement под mutex (`351–382`). Это проверяемый код восстановления при ошибке записи; устойчивость к отключению питания здесь не испытывалась.
- **Plan binding:** literal Stage/PlanRef и SHA-256 (`770–808`), проверка каждого nonterminal claim/assignment (`234–241`). Изменение плана не лечится ручной заменой хеша.
- **Точные пути:** запрет директорий/globs/`..`/выхода за корень, `.git`, `.agent-state`, секретов, generated paths и reparse points (`393–456`). Для нового репозитория следует сохранить защиту, но адаптировать разрешённые `.env.example` и каталоги evidence осознанно, а не наследовать MTCH-список без проверки.
- **Ownership:** один active claim на task chat, запрет повторного claim TaskId, stage/file/runtime conflicts (`602–683,1129–1160`). `blocked`, `awaiting_review` и `revision_required` сохраняют резервы. Нет таймерного expiry.
- **Dependencies:** accepted результата недостаточно, его зарегистрированный/integrated commit должен быть ancestor доступного base (`220–230`). Reserve требует, чтобы plan в base совпадал с текущим plan (`951–953`).
- **Review bytes:** GO запоминает blob/mode и iteration; Accept и RegisterCommit проверяют их повторно (`143–205,1394–1440,1648–1665`). Любые новые bytes требуют нового review.

## Обнаруженные расхождения и что не следует обещать

1. **Legacy skill не является текущей политикой.** Устаревшая памятка предлагает остановку при первом overlap. Текущий AGENTS требует отклонить кандидата и продолжить независимый скан; новые worktree/reservation правила имеют приоритет. Нужен один локальный источник правил, без скрытого наследования этой памятки.
2. **Identity — дисциплина инструментов, не криптографическая авторизация.** `Assert-Orchestrator` проверяет строку роли/непустой actor (`744–747`), `Assert-Reviewer` — роль и равенство actor/reviewer (`780–784`). Скрипт не опрашивает collaboration API для доказательства реального происхождения identity. Reviewer имеет общие файловые инструменты, а его read-only режим задан задачей, не отдельной filesystem sandbox. Новый workflow должен связывать IDs с фактическим результатом spawn и проверять изменившийся scope после review; не объявлять это защитой от злонамеренного локального пользователя.
3. **Свежесть агентов проверяется уже, чем текст правила.** AGENTS:91 запрещает переиспользование агентов между задачами; `Assert-FreshStageAgentIdentity:513–531` просматривает только тот же stage. Для нового малого ledger проще запрещать повторное использование worker/reviewer identity глобально между задачами, а reviewer — и между итерациями.
4. **Семантика evidence остаётся предметом проверки.** Непустой ContractEvidence разрешает некоторые cross-worktree overlaps (`602–654`), но скрипт не способен доказать независимость API/политик. Queue ledger validator прямо не доказывает browser/G-gates/действительность вопроса владельцу (`mtch_queue_selection.md:90`). Новый статус «принято» не должен присваиваться по одному exit 0.
5. **Legacy compatibility ослабляет строгий путь.** `Assert-ReviewSnapshot:196–197` пропускает старые reviews без manifest; отдельные legacy зависимости без commit также сохраняют совместимость. В пустом новом registry такая совместимость не нужна: manifest и точная baseline/accepted lineage обязательны. Commit provenance добавляется, когда коммит разрешён и действительно сделан; сам Accept не должен выдумывать коммит.
6. **Bootstrap текущего проекта требует явного решения.** На момент аудита пакет планирования localhost содержит dirty/untracked файлы. Их нельзя молча выдавать за принятый immutable Git base. Перенос должен сначала зафиксировать точный baseline/его provenance в пределах отдельной tooling/setup-задачи, сохранив пользовательские фото и материалы. Это не запуск B01 и не разрешение смешанного коммита.
7. **Тесты найдены, но не запускались.** Есть disposable fixtures для race, stale binding, scope mismatch, NO-GO iteration и integration (`scripts/test-dispatch-reservations.ps1`, `test-plan-coordination.ps1`, `test-worktree-plan-coordination.ps1`, `test-queue-selection-evidence.ps1`). Положительный runtime результат не заявляется. Например, test-dispatch-reservations:9–10 нормализует Actor до OwnerThread; прежние literal fixture-значения не следует ошибочно считать доказательством сломанного теста.

## Что переносить в localhost-3000

- Trigger в AGENTS и компактные инструкции ролей: будущая фраза «Выполняй план MVP» распознаётся как запуск проверенного bounded backlog; установка механизма сама фразу не исполняет. Отдельный skill для этого переноса не требуется, как и в фактически найденном MTCH entrypoint.
- Один versioned локальный ledger и CLI с машинным JSON; locked read/validate/write, CAS, явные ошибки и неизменность ledger при отказе.
- Стабильные backlog ID Bxx, точные files/resources/dependencies/acceptance evidence, immutable plan fingerprint и baseline provenance.
- Отдельный pending reserve, реальный bind worker identity, затем повторный Check/Claim до первой правки; отсутствие identity не заменять выдуманным ID.
- Реальные роли: orchestrator, dedicated worker и свежий independent reviewer. Исправление после NO-GO в том же claim и тем же worker; новый reviewer на каждый цикл.
- Review manifest, current-iteration GO и отдельный Accept; локальные exact-path checkpoints выполняет оркестратор после проверки. Первая версия CLI сама не коммитит и не реализует MTCH RegisterCommit; фактический SHA фиксируется в отчёте. Успешная проверка tooling не равна продуктовой приёмке.
- Readiness ledger по всем Bxx и повторный скан после изменения dependency/блокера. Pending verification не равно «готовой работы нет».
- Проверки конфликтов путей и ресурсов, сохранение dirty/index, безопасная локальная подготовка среды, разграничение local/browser/Docker/public доказательств.
- Негативные и конкурентные тесты на одноразовом repo/state: duplicate reserve race, stale CAS, same worker/reviewer, changed scope after GO, unknown dependency, plan drift, out-of-scope commit, сохранение чужого dirty.

## Что не переносить автоматически

- Три MTCH-плана, R/N/FSP numbering, G1/G2/G3, Assessment/Match/qualification, нормы синтетических участников и их продуктовые решения.
- MTCH claims, chat links, реестр, runtime resources, процессы, lock name и рабочие деревья. В MTCH ничего не очищать и не резервировать.
- Двухуровневое создание пользовательских task chats, внешние сообщения владельцам старых claims и MTCH-specific Transfer/Release, если MVP использует только текущих subagents.
- Legacy глобальный dispatcher lease как обязательное условие нового dispatch. Конкретный резерв не должен удерживать всю очередь во время setup.
- Возможность одинаковых относительных файлов в разных worktrees без необходимости. Для небольшого хакатона безопаснее разделить общий контракт и независимые точные scopes; sequential integration добавлять лишь при реальной работе в нескольких checkouts.
- Свободную область копирования product policies, roadmap, чужой код или зависимость от Next/PostgreSQL. Переносится механизм, а не предметная модель MTCH.
- Push, deploy, main promotion, публичную публикацию, покупку ресурсов и фоновые automations. Эти действия не следуют из запуска локального MVP-плана.

## Fingerprints просмотренных канонических файлов

SHA-256 сохранены для воспроизводимости чтения, не как обещание неизменности активного MTCH после аудита.

| Файл относительно `D:\mtch` | SHA-256 |
|---|---|
| `AGENTS.md` | `6fecbc6b4c5d8bfa1027bc7667871dd63e2e476e280ddcdcdc978bc26fefa422` |
| `docs/mtch_worktree_coordination.md` | `f1fd0de0801c45923ec4426e29ab095b0aac21a9bd29d67c541a66bf1bbd1e9c` |
| `docs/mtch_queue_selection.md` | `453379e3809bcaea20d2af28da9afb26796e2bf8fd86663b647521619170aa7e` |
| `scripts/plan-coordination.ps1` | `be7182a148eb57f1db40090e8c10055a0870aef6788e5981753eb9f1fc97c138` |
| `scripts/mtch-worktree.ps1` | `0540e98932340eec4871486442ab71a9fb851f8d778ce071e0ef159094131213` |
| `scripts/test-dispatch-reservations.ps1` | `48b67dc28f872b70da8581bbd185c2728dbbf28398d65826a977903c378abd36` |
