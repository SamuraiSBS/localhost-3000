# Инструменты сборки и проверки

Статичные файлы `design/` запускаются Python HTTP server без npm/pip runtime-зависимостей. Ни один инструмент ниже не выбирает стек конкурсного продукта и не входит в образ probe.

## Шрифты и wordmark

```powershell
python -m pip install --target .state/design-tools -r design/tooling/requirements.txt
python scripts/prepare-design-fonts.py --tool-path .state/design-tools --fetch
```

Скрипт получает исходники из закреплённых GitHub URL в `assets/fonts/sources.json`, проверяет SHA-256, экспортирует JetBrains Mono variable 100–800 в WOFF2 без удаления глифов (71736 bytes) и создаёт две SVG-версии wordmark из веса 600. Исходники и инструменты остаются в gitignored `.state`. При нерабочем локальном proxy можно явно добавить `--direct`: это настройка только данного запуска. Лицензия OFL сохраняется рядом со шрифтом.

## Графика

Источник — встроенный ImageGen, финальный промпт в `assets/columns.prompt.txt`, происхождение/хеши в `assets/columns.provenance.json`. Исходный PNG сохранён локально в `.state/design-sources/columns-generated.png`; основной WebP сохраняет пиксели исходника, мобильный — lossless экспорт после уменьшения до 768 px. Это экспорт готовой графики, не CSS/SVG-замена изображения.

Для повторного экспорта нужен Sharp 0.35.5 как build tool:

```powershell
node scripts/export-design-images.cjs --source .state/design-sources/columns-generated.png --sharp-path <absolute-path-to-sharp>
```

## Browser QA

Используется Playwright 1.62.1 из bundled workspace dependencies. На другой машине его можно установить как изолированный инструмент, без добавления runtime-пакетов в приложение. Браузеры хранятся в `.state/playwright-browsers`, результаты/скриншоты — в `artifacts/design`.

```powershell
$env:PLAYWRIGHT_BROWSERS_PATH = Join-Path (Get-Location).Path '.state/playwright-browsers'
node scripts/test-design.cjs --playwright-path <absolute-path-to-playwright> --browser edge
node scripts/test-design.cjs --playwright-path <absolute-path-to-playwright> --browser firefox
node scripts/test-design.cjs --playwright-path <absolute-path-to-playwright> --browser webkit
```

Перед тестами запустить `python -m http.server 3300 --bind 127.0.0.1 --directory design`. `edge` использует установленный Edge в новом headless-профиле, остальные — изолированные Playwright binaries. Они не подключаются к пользовательским браузерным сессиям. WebKit на Windows не является реальным Safari; CSS zoom, CPU throttling и media emulation не выдаются за проверки физического устройства или экранного диктора.
