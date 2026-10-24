# Проверка координатора MVP

Дата: 2026-10-23. Это проверка инструмента разработки, не приёмка анализатора оттока.

## Исполняемый код и независимый review

Проверенный SHA-256 `scripts/mvp_coordination.py`:

```text
c37c2ab8b3bdda834521124e0c7095708cddd7683d13a99b52d90c904c2f9703
```

- `python scripts/check.py`: 20 tests passed — 16 coordination и 4 существующих probe tests; design checks и Python syntax passed.
- `git diff --check` и `git diff --cached --check`: passed.
- Отдельный `/root/brief_requirements` независимо воспроизвёл контрпримеры и выполнил 16 tests в собственной disposable-копии: GO после исправлений.
- Проверены 8 SHA-256 исходных файлов организаторов: совпадают с source manifest. Ссылки документов координации существуют.

Регрессии покрывают CAS/конкурентный Reserve, OS lock после остановки процесса, атомарность записи, повреждение ledger, ownership/run binding, unbound Claim, protected paths, dirty preservation, dependencies, accepted lineage, NO_GO repair с новым reviewer, изменения новых/удалённых файлов и staged mode после GO, Extend/Rebase, sourced gates, contract drift и сохранение незавершённых outputs закрытого peer.

В независимом review найдены и исправлены три дефекта:

1. Accepted dependency теперь проверяет актуальный fingerprint контрактов; старый GO не переносится на изменённые требования.
2. Release/cancel после quiescence сохраняет exact `closed_manifest`; его неизменные bytes не блокируют чужой Submit, но не становятся принятой реализацией.
3. Новое обязательное условие проверяется также на Rebase/Claim/Submit/Accept и в актуальности accepted задачи.

Windows cleanup тестов отдельно учитывает read-only Git objects только внутри проверенного временного repo.

## Цикл с настоящими агентами

Отдельный disposable Git repo:

```text
D:/localhost-3000/.state/coordination-live-70203eb6ed1d4bf8934e8d8ed65fac92
```

Fixture B01 означает только `answer() -> int 42` и meaningful unittest. Он не является B01 настоящего MVP. Область — `app/demo_value.py`, `tests/test_demo_value.py` внутри fixture.

| Роль | Actual target | Actual CODEX_THREAD_ID |
|---|---|---|
| Orchestrator | `/root` | `01a167b2-ddd1-7d73-88c1-ef7b32aa7b0b` |
| Worker | `/root/coordination_smoke_worker` | `01a167fc-7b14-71f0-9467-a94af4b88b6f` |
| Reviewer | `/root/coordination_smoke_reviewer` | `01a16807-5066-7100-a77e-b562793ad339` |

Attempt: `dfcbe438-cb40-4776-9e16-6b75eb8887bc`. Worker сам выполнил Claim, реализацию, два unittest и Submit. После добавления contract-provenance guard оркестратор сделал штатный Rebase, тот же worker повторил Submit без изменения bytes. Новый независимый reviewer прочитал реальные файлы/diff, запустил два теста, проверил их чувствительность к `0` и `True`, самостоятельно зарегистрировал GO. После этого оркестратор отдельно выполнил Accept.

Результат: **accepted**, revision **9**, iteration **2**. Candidate:

```text
6d1a0c08f05833992a02f2f16cf5340d5fdb6a22d9dbc759acf8cf0c1b3d0b1d
```

Raw evidence осталось в `artifacts/` fixture: `worker-spawn.json`, `worker-report.json`, `reviewer-spawn.json`, `bind-reviewer.json`, `reviewer-report.json`, `accept.json`. SHA-256 reviewer report: `08698fba08a3265ed59cb5b7f6d2683b19e9c00dddc05f6e80ddda9d711ec0e9`. Эти runtime-артефакты и `.state` не входят в Git; здесь сохранён переносимый итог.

Настоящий `python scripts/mvp_coordination.py status`: revision 0, `state_exists=false`. `next`: нет готовых P0, B00 требует фактических ответов Q-001–003. MVP очередь не запущена. Чтение и аудит MTCH не изменяли его исходники/claims и не запускали его координатор.

## 2026-10-23 — Handoff владельца ledger

- Проверенный SHA-256 `scripts/mvp_coordination.py`: `48f79cb52432af1cf4e1e9226e8504a1cd484f96f560cea172a806e0eb507d15`.
- `python scripts/check.py`: 22 tests passed (18 coordination и 4 probe); design checks и Python syntax passed. Отдельный handoff targeted run: 3 passed. `git diff --check`: passed.
- Новый независимый reviewer `/root/handoff_review2`: GO. Проверены точный owner pair, текущий `CODEX_THREAD_ID`, CAS, закрытые attempts, свежая user authorization evidence, audit path/hash и сохранение истории.
- Это подтверждает только процедуру handoff. Реальная передача владельца и продуктовые задачи B02+ фиксируются последующими runtime events; Docker/VPS/HTTPS/product E2E не запускались.

## 2026-10-23 — Snapshot завершённого source chat

B14 добавляет только provenance для отдельного завершённого чата внутри того же checkout. `record-snapshot` требует текущего `/root` owner/run и matching `CODEX_THREAD_ID`, свежую observation evidence `mcp__codex_app__read_thread`, direct authorization в текущем чате, completed source status, одинаковые source/CLI file lists и существующие обычные файлы. Запись содержит точный конечный manifest с path/Git mode/SHA-256/size, evidence SHA-256 и actor/run/time. Она не входит в accepted lineage и не меняет статусы/результаты product tasks.

Disposable-repo regression tests покрывают точное сохранение manifest/evidence hash, stale observation, неверные source/status/root/authorization, mismatch file list, unsafe/unsorted paths, конфликт с active claim, exact out-of-scope bytes, byte/deletion drift после capture, запрет замены snapshot и чтение/дополнение старого v1 ledger без изменения прежней event history. Все предыдущие handoff и coordination regression tests сохраняются.

- SHA-256 текущего `scripts/mvp_coordination.py`: `0d1e7d5772133db67cdfeac736e721cb825d4781e999deba9f69973670c1d3ec`.
- `python scripts/check.py`: 28 passed (24 coordination + 4 probe); design asset/link/font/token/mobile checks и Python syntax passed.
- `git diff --check`: passed. Полный check подтверждает только локальные coordination/probe/design checks; Docker/VPS/HTTPS/product E2E проверяются отдельно.

## Локальный checkpoint

Планы и tooling подготовлены для отдельного exact-path локального checkpoint в ветке `docs/mvp-autonomy`. После commit проверить чистоту именно его файлов; пользовательские фото и `материалы/` остаются вне коммита. SHA checkpoint находится в Git history и итоговом сообщении задачи, а не подставляется заранее. Push/CI/Docker/VPS/HTTPS и продуктовый browser E2E этой проверкой не выполнялись.

## 2026-10-23 — Повторная проверка B14 после восстановления координации

На HEAD `08ba40d2e0136323cb8d8492b1da90884bb95d8e` повторно проверен существующий snapshot-механизм вместе с recovery guards. SHA-256 `scripts/mvp_coordination.py`: `75f72132083cffee5f87dc4d5ab8f140ac2a8d3df007c226e535492275b78525`. Хеши скрипта, тестов, CLI reference и orchestrator совпали с точным четырёхфайловым manifest независимого bootstrap GO в `artifacts/coordination/recovery-review1.json`. Ранее указанные хеши выше относятся к историческим версиям, а не к текущему коду.

Worker `/root/b14_revalidate`, actual run `01a16988-111b-7260-b486-870a90128894`, самостоятельно получил Claim revision 119, attempt `53769908-e9a0-4b56-bf5b-8fd0e66dcfa0`. Собственная targeted проверка через `.venv/Scripts/python.exe -m unittest` охватывает шесть `test_snapshot_*` и три `test_recovery_*` из `tests/test_mvp_coordination.py`: точный manifest/источник/разрешение, отсутствие продуктовой приёмки, active-scope conflict, byte/deletion drift, запрет замены snapshot, legacy history, атомарность recovery, CAS/identity/evidence/manifest отказы и ограничения adoption. Результат: 9 tests passed за 75.059 s, exit 0. Точная команда сохранена в `artifacts/coordination/b14-recovery-worker.json`.

Наблюдаемый live event sequence 116 — `recover-handoff`; B07 attempt `8e8bba80-efab-4923-ac29-1619b341a8be` имеет статус `cancelled`, `closed_manifest` и `operator_abandonment`, без `quiescence`. В evidence сохранены исходное прямое разрешение пользователя и точные прежняя/новая пары владельца. Events 1–115 присутствуют последовательно; отдельной копии старого ledger для независимого побайтового сравнения нет. Автоматический recovery test отдельно сравнивает всю прежнюю event history и gates до/после перехода в disposable repo.

Независимый bootstrap reviewer ранее выполнил 27 coordination tests и три собственных adversarial cases на тех же source hashes. Это его evidence, не собственный повторный прогон B14 worker. Нового дефекта targeted проверка не выявила; source, backlog и контракты этой повторной проверкой не меняются. Прежняя accepted B14 остаётся историей, а текущему candidate ещё нужны новый независимый review и Accept.

Recovery не принимает B07 и не подтверждает остановку внешнего filesystem writer. Snapshot остаётся provenance, а не принятой реализацией. Эта проверка локального tooling не доказывает product/browser E2E, Docker, CI, VPS, HTTPS или готовность сдачи.

## B14-R2 — continuation immutable snapshot после NO_GO

Root audit обнаружил, что текущий exact manifest исходного 13-path snapshot изменился в 10 paths после отправленного candidate и NO_GO; прежний pre-candidate amendment намеренно запрещено менять или расширять после NO_GO. Для следующей iteration добавлен отдельный append-only `continue-snapshot-recovery`, сохраняющий proposal, исходный amendment, snapshot и все prior receipts. Action требует root identity/current run, точное прямое разрешение пользователя, matching prior candidate + independent NO_GO, exact current manifest digest и неизменные plan/HEAD/index/baseline/scope/workspace-origin pins.

Focused regression покрывает NO_GO → continuation → workspace chain amendment → resubmit → повторный NO_GO → новая continuation того же exact manifest → workspace amendment → свежий GO и paired Accept; также проверяет тампер predecessor/hash, неверную авторизацию/actor/run/snapshot, plan/HEAD/index и manifest digest mismatch, duplicate continuation, неполный history, drift после записи, pending access только связанной B14 попытке и неизменность snapshot/proposal/amendment. Каждый candidate/review связывает continuation summary и текущий workspace-delta chain entry; Accept проверяет обе receipt arrays в одном событии. Результаты именно текущего worker run фиксируются в `artifacts/acceptance/b14-snapshot-recovery-results.json`; пока нет нового independent GO/Accept, этот кодовый результат не означает принятие B14.

Решение о восстановлении зафиксировано здесь: старый B07 закрывается без приёмки; продолжение требует нового Claim и review. Добавление той же записи в `docs/decisions.md` отклонено guard `snapshot_drift`: файл закреплён прежним snapshot `142585de-4874-43d4-a084-bb623d59388d`. Собственная добавка полностью удалена, исходный SHA-256 `1fd89cd02d456b916057cf1bcab6206116e669406465e4bd8c066db5b3461cbd` восстановлен и проверен; snapshot и guard не менялись. Отказ Submit не изменил ledger.

## 2026-10-24 — B14 reviewed snapshot recovery

Реализован отдельный append-only путь `propose-snapshot-recovery`. Он адресует только snapshot `142585de-4874-43d4-a084-bb623d59388d` с его 13 исходными путями; второй snapshot `ac1a0c41-a76b-46c6-84f2-aa2008ae2837` не затрагивается. Исходная snapshot-запись и manifest остаются неизменными. Proposal хранит полный exact manifest (`path`, Git `mode`, SHA-256, `size`), provenance-ссылку и прямую авторизацию пользователя.

Если proposal устарел до первого candidate, `amend-snapshot-recovery` допускает одну root-only append-only amendment для исходного B14 attempt/snapshot/iteration. Требуется отдельная прямая авторизация текущего чата, совпадающий `CODEX_THREAD_ID` и live `in_progress` attempt без submission/review. Amendment сохраняет исходные proposal, snapshot и audit, фиксирует новый exact manifest, authorization, proposal hash и amendment ID. Один same-iteration rebase до amendment разрешён только до candidate/review и сохраняет iteration; после amendment plan change/rebase отклоняется. Повторная amendment, NO_GO, смена iteration, candidate/review/close до amendment или любое дальнейшее manifest drift fail closed.

Pending proposal/amendment разрешает drift только той же live claimed B14 attempt и только при полном совпадении фактического effective manifest. Другие attempts получают доступ лишь после свежего независимого GO для совпадающих candidate/iteration/digest и отдельного orchestrator Accept. Candidate и acceptance receipt фиксируют effective manifest digest и amendment ID/hash. После Accept любое изменение bytes, mode или deletion снова блокирует Submit/Accept. Proposal и amendment audit evidence дописываются; создать второй proposal или amendment для той же пары snapshot/attempt запрещено. Для repair разрешено повторно использовать исходные immutable proposal/amendment, если каждая промежуточная iteration содержит submitted candidate с теми же recovery IDs и matching `NO_GO`; summary сохраняет origin `proposal_iteration` отдельно от iteration candidate. Новый candidate, reviewer GO и Accept привязаны к текущим iteration/digest, а пропуск iteration или отсутствие review закрывает путь. Проверки fail closed для неправильного snapshot/attempt/task, неполной или неверной авторизации, повреждённого или устаревшего proposal/amendment, NO_GO без нового review+Accept, закрытой attempt и legacy v1 ledger без recovery-полей.

Adversarial regression cases проверяют: неизменность исходного snapshot/proposal; amendment-local прямую авторизацию; pending-доступ только связанной B14 attempt; блокировку до GO+Accept; открытие только exact amended manifest после Accept; повторный byte/mode/deletion drift; неправильные snapshot, attempt и actor/run; duplicate proposal/amendment; amendment до/после same-iteration rebase; отказ amendment после candidate, NO_GO, смены iteration или close; same-proposal retry после NO_GO с новым GO+Accept; iteration jump и missing NO_GO audit; чтение legacy v1 ledger без автоматической миграции recovery-полей.

- Recovery-focused selection (`pytest -k snapshot_recovery`): 13 passed, 27 deselected, exit 0; 109.16 s.
- Amendment-only selection (`pytest -k snapshot_recovery_amendment`): 6 passed, 34 deselected, exit 0; 37.72 s.
- Latest full coordinator unittest module stopped at the two-minute cutoff after 25 progress markers, with no reported assertion/error before Ctrl-C; exit 1 and incomplete. It is not counted as passed. Earlier pre-amendment module evidence (34 passed) is historical only.
- `py_compile scripts/mvp_coordination.py tests/test_mvp_coordination.py`: passed, exit 0.
- Последний `scripts/check.py` дошёл до 100%, но pytest завершился с 8 setup errors в `tests/integration/test_delivery.py` и `PermissionError [WinError 5]` при `pytest_sessionfinish → cleanup_dead_symlinks → os.scandir` на basetemp. Из-за ошибки cleanup итоговая pytest summary не была выведена, поэтому число passed для этого прогона не указано. Design asset/hash/link/font/token/mobile checks прошли. Повтор с forward-slash task-owned basetemp был остановлен orchestrator примерно после 28 завершённых тестов и не учитывается как полный прогон. ACL не менялись, временные каталоги вручную не удалялись.

Перед Submit проверка CLI status и независимое вычисление exact manifest выявили stale pending proposal: из 13 закреплённых путей 9 отличались по текущим bytes (включая `docs/decisions.md` и design snapshot files). Worker после создания proposal эти пути не редактировал. Из-за manifest drift candidate не отправлен; pending recovery должен оставаться fail-closed.

В последнем отчёте B14 фиксируются точные команды и результаты. Это локальная проверка координатора; она не является review GO или orchestrator Accept и не подтверждает продуктовый/browser E2E, CI, Docker, VPS либо HTTPS.

## 2026-10-24 — B14 точное восстановление workspace delta

После отказа B14 Submit с `out_of_scope_change` добавлен отдельный root-only append-only `record-workspace-recovery`. Он принимает только заранее наблюдённый exact manifest digest, связывает запись с текущими B14 attempt/iteration/plan/base/index/scope и amended snapshot recovery, и сохраняет path/mode/hash/size либо explicit absence. Candidate/review/Accept привязаны к recovery ID/hash/digest. После Accept workspace и snapshot approval receipts проверяются как один bundle; обе части должны совпасть по candidate, reviewer/evidence и orchestrator. Изменение, добавление, удаление или mode drift после записи fail closed. Старый v1 ledger читается без добавления новых полей.

Focused coverage проверяет: точное одноразовое root действие и прямую авторизацию; mismatch заранее наблюдённого SHA при дополнительном пути; added/deleted/byte/mode drift; неизменность исходного snapshot/proposal/amendment; отказ при возврате snapshot к исходным bytes; candidate digest/task binding; reuse того же record после NO_GO только при matching candidate/review истории; paired receipt output и отказ обоих unlock при удалении любого receipt; exact Accepted delta для peer и повторный отказ после нового drift; wrong role/run/task, duplicate, late, close и tampered record.

- `$env:MVP_COORDINATION_TEST_ROOT='D:/Codex/visualizations/2026/10/24/01a16e03-0de9-7b00-ac87-2154c891c8a1/b14-workspace-recovery-fixtures-final1'; python -m pytest -p no:cacheprovider --basetemp=D:/Codex/visualizations/2026/10/24/01a16e03-0de9-7b00-ac87-2154c891c8a1/b14-workspace-recovery-pytest-final1 tests/test_mvp_coordination.py -k workspace_delta_recovery -q`: 4 passed, 40 deselected, exit 0, 53.26 s. Scratch полностью находится в task-owned writable root.
- Ранее в этом цикле промежуточный focused запуск завершился с одной test assertion из-за предположения о LF вместо фактического Windows CRLF. Проверка исправлена на хеш фактически сохранённых bytes; итоговый повтор выше полностью прошёл.
- `python -m py_compile scripts/mvp_coordination.py tests/test_mvp_coordination.py`: passed, exit 0; `git diff --check` по точным B14 изменённым файлам: ожидает итоговую проверку перед передачей.
- Full `scripts/check.py` и полный coordinator module в этом этапе не запускались; ранее описанные WinError 5 delivery setup/cleanup ошибки и остановленный retry остаются отдельными историческими ограничениями, не засчитываются как успешные проверки.

На revision 222 root записал recovery ID `359510c4-52f7-4506-83ea-89a75fd8b1f8` для exact 105-path manifest с digest `79138c047beed8671ae54410c124849bf6dc4c9e1e64a4cb46696438d7e4edec`. Эта запись и предыдущие observations остаются immutable. Позднее root read-only inspection сообщил, что receipt уже stale из-за последующих внешних изменений в `.21st/`, `design/interaction-lab/`, `frontend/` и `docs/design/REDESIGN_DETAILS.md`; worker эти пути не редактировал, не сканировал повторно и не менял.

После Submit внешний drift раньше блокировал `bind-reviewer` до возможности получить NO_GO, что оставляло attempt без разрешённого перехода к новой recovery iteration. Исправление допускает reviewer bind только после обычных строгих candidate digest/scope/HEAD/index checks и неизменной exact snapshot/recovery chain binding. Review может продолжиться только с `NO_GO`, содержащим свежий root-observed sorted exact delta, его SHA-256, root actor/run, observed_at, candidate digest/iteration/plan/base/index/baseline/scope и chain-head ID/hash. Coordinator проверяет формат manifest и равенство live delta при review; stale GO, NO_GO без observation или с forged/stale observation fail closed. Observation остаётся в reviewer evidence и audit event, а validator использует её при проверке исторического NO_GO перед amendment; она не является разрешением на Submit/Accept.

Чтобы продолжить fail-closed, добавлено `amend-workspace-recovery`: root-only hash-pinned append-only amendment к исходному receipt с exact current manifest. Оно требует свежую прямую пользовательскую авторизацию и неизменные attempt/iteration/plan/HEAD/index/baseline/scope/snapshot bindings; допускается до Submit либо после matching NO_GO в следующей iteration. Candidate/review/Accept должны указывать конкретный chain entry; предыдущие версии допускают repair только через непрерывные matching candidate + NO_GO audit events. После GO/Accept/close, stale pins, duplicate iteration или нового drift до наблюдения amendment action отказано. Исходный root receipt и 13-path snapshot остаются неизменными; root должен выполнить ровно одну hash-pinned amendment после стабилизации внешней дельты.

Проверки текущего chain кода: amendment-focused selection — 4 passed; combined workspace-delta, snapshot-amendment и NO_GO recovery regression filter — 16 passed. Полный coordinator module на этом этапе не запускался. Submit и worker-side recovery amendment action не выполнялись; report обновляется до новой root observation.

### B14-R2 continuation после snapshot drift в следующей iteration

Когда исходный 13-path manifest дрейфует после candidate/NO_GO, `amend-snapshot-recovery` остаётся запрещённым. Отдельный `continue-snapshot-recovery` дописывает точный root-observed manifest только для той же живой B14 attempt после matching независимого NO_GO, свежей прямой авторизации и проверки исходных proposal/amendment/snapshot, plan/HEAD/index/baseline/scope и workspace-origin pins. До первой continuation без drift допустимо прежнее same-proposal retry по matching NO_GO истории; после начала continuation chain каждый следующий iteration требует своей append-only записи. Submit, review и Accept связывают актуальные snapshot и workspace цепочки; Accept записывает обе approval receipts одним событием. Старые snapshot/recovery записи не переписываются, дальнейший drift закрывает обе цепочки.

В текущем worker run узкий regression filter прошёл 8 тестов, 43 теста deselected, exit 0; он включает continuation chain, pairing snapshot/workspace receipts и три прежних retry regressions. Полный coordinator модуль и `scripts/check.py` повторно не запускались. Submit не выполнялся; root должен независимо проверить diff и добавить hash-pinned live continuation/workspace chain record перед свежим review.

### B31 — reviewed recovery второго неизменяемого snapshot

B31 расширяет allowlist только для exact task/snapshot пары `B31` / `ac1a0c41-a76b-46c6-84f2-aa2008ae2837`. Единственное initial proposal возможно для claimed attempt до первого candidate и закрепляет exact recovery manifest, source provenance и direct user authorization. B31 candidate, review и Accept связаны с тем же immutable proposal; amendment и continuation запрещены. Изменение bytes/mode, появление или удаление любого из двух путей блокирует Submit/Accept. Source snapshot, provenance и записи B14 не меняются.

- `MVP_COORDINATION_TEST_ROOT=D:/Codex/visualizations/2026/10/24/01a16fbd-db55-7960-a815-35edebb27dd0/b31-regression-fixtures-01`; `python -m pytest -p no:cacheprovider --basetemp=D:/Codex/visualizations/2026/10/24/01a16fbd-db55-7960-a815-35edebb27dd0/b31-regression-pytest-01 tests/test_mvp_coordination.py -k 'snapshot_recovery or snapshot_reads_legacy_v1' -q`: 20 passed, 35 deselected, exit 0, 172.24 s. This focused regression includes B31 success, B14 history preservation, wrong task/snapshot, cross-task tampering, amendment/continuation rejection, Submit/Accept drift, and legacy v1 read compatibility.
- Separate B31 focused run: 4 passed, 51 deselected, exit 0. `py_compile` for the coordinator and its test module passed before the final docs/evidence update; repeat at the final candidate boundary.
- Full `scripts/check.py` and full coordinator test module are not claimed by this B31 worker run. These focused checks are local coordinator evidence, not independent review GO or orchestrator Accept.
