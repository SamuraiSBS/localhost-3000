# Рабочий интерфейс

`npm ci` и `npm run build:product` из этой папки собирают реальные страницы.
Результат — `web/assets/interface/`; FastAPI уже обслуживает его через `/assets`.
Все шрифты локальные. Node нужен для сборки, но не для исполнения готового сервера.
`npm run build` по-прежнему собирает отдельный образец для сравнения.

Локальная проверка настоящего приложения: из корня `.venv/Scripts/python.exe
scripts/preview_redesign.py`, адрес `http://127.0.0.1:8397/`. Это отдельный loopback
runtime с учебными данными; существующий операторский пароль не используется.
Для штатного запуска используйте текущие инструкции FastAPI/Docker проекта.

Публичная кнопка «Зайти на демо» создаёт отдельную временную SQLite-песочницу.
Её настройки/импорт не меняют основной аккаунт. Срок — срок сессии; новый вход
начинает заново. `DEMO_ENABLED=false` отключает демо, в том числе через Compose.

После пересборки меняются хеши chunks: обновите точный список product assets в
`scripts/package_delivery.py` перед подготовкой ZIP. Не добавляйте в него каталоги
целиком или пользовательские данные. Проверки: `npm run check:sources`,
`python scripts/check.py`, браузерные сценарии в `PRODUCT_VERIFICATION.md`.

# Interface specimen

React/Vite approved by the user on 2026-10-24. This is currently a local,
reviewable interaction specimen, not the product frontend migration.

Run `npm ci`, `npm run check:sources`, `npm run build`. The generated page is
`../design/interaction-lab/index.html`; serve `design/` on loopback, or visit
`/design/interaction-lab/` through the existing FastAPI app.

Source components are preserved under `src/vendor/`. Registry provenance and
hashes are in `registry/manifest.json`. Do not regenerate these snapshots during
an ordinary build. `scripts/fetch-registry.mjs` explicitly refreshes sources and
should be used only when intentionally selecting a new upstream version.

The source-version contract, proposed geometry, interaction inventory and
known accessibility differences are in `../docs/design/REDESIGN_DETAILS.md`.
Third-party terms and attribution: `THIRD_PARTY.md`, `licenses/`.

Product HTML, API controllers, authentication, data polling, revision checks and
import confirmation are deliberately outside this specimen. The next production
integration must preserve those contracts and provide browser proof on the actual
product routes.
