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

## Локальный checkpoint

Планы и tooling подготовлены для отдельного exact-path локального checkpoint в ветке `docs/mvp-autonomy`. После commit проверить чистоту именно его файлов; пользовательские фото и `материалы/` остаются вне коммита. SHA checkpoint находится в Git history и итоговом сообщении задачи, а не подставляется заранее. Push/CI/Docker/VPS/HTTPS и продуктовый browser E2E этой проверкой не выполнялись.
