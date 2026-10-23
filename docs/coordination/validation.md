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

Решение о восстановлении зафиксировано здесь: старый B07 закрывается без приёмки; продолжение требует нового Claim и review. Добавление той же записи в `docs/decisions.md` отклонено guard `snapshot_drift`: файл закреплён прежним snapshot `142585de-4874-43d4-a084-bb623d59388d`. Собственная добавка полностью удалена, исходный SHA-256 `1fd89cd02d456b916057cf1bcab6206116e669406465e4bd8c066db5b3461cbd` восстановлен и проверен; snapshot и guard не менялись. Отказ Submit не изменил ledger.
