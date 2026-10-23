# Инструменты сборки и проверки

Статичные файлы `design/` запускаются Python HTTP server без npm/pip runtime-зависимостей. Ни один инструмент ниже не выбирает стек конкурсного продукта и не входит в образ probe.

## Шрифты и wordmark

```powershell
python -m pip install --target .state/design-tools -r design/tooling/requirements.txt
python scripts/prepare-design-fonts.py --tool-path .state/design-tools --fetch
```

Скрипт получает PP Neue Machina Inktrap Variable по закреплённому официальному URL в `assets/fonts/sources.json`, проверяет SHA-256 и полный русский алфавит с `Ёё`, копирует шрифт без изменения байтов (146188 bytes) и создаёт две SVG-версии wordmark из веса 600. Входной URL имеет расширение `.woff`, фактическая сигнатура — WOFF2; локальный файл получает `.woff2`. Исходники и инструменты остаются в gitignored `.state`. При нерабочем локальном proxy можно явно добавить `--direct`: это настройка только данного запуска. Происхождение и условия локальной подготовки — в `assets/fonts/README.md`; OFL к этому шрифту не относится.

## Liquid Glass и Controls

Публичный Manual-исходник Aceternity сохранён локально в `.state/aceternity-glass/source-2.tsx`. Его SHA-256 и происхождение зафиксированы в `aceternity-liquid-glass.json`. `node scripts/export-liquid-glass.cjs` удаляет TypeScript-типы из исходных оптических helpers встроенным Node 24 API, экспортирует native JS и генерирует `liquid-glass-controls.tsx` из CSS-токенов. `node scripts/export-liquid-glass.cjs --check` проверяет соответствие конфигурации Controls токенам без исходного snapshot.

Hook использует `useDialKit("Controls", ...)` и утверждённые значения по умолчанию. Это подготовленный React/DialKit-адаптер; пакеты не установлены и hook не исполняется native-образцом. Синтаксис проверяется отдельно; React runtime/type-check/build не заявляются. Native-образец использует тот же SVG-алгоритм и пресет без выбора продуктового стека.

`scripts/test-liquid-glass.cjs` запускается с теми же `--playwright-path` / `--browser`, что остальные browser checks. Он проверяет точные настройки, сброс, изменение фактически нарисованных пикселей преломлением в Edge, frosted fallback Firefox/WebKit, 320–1440 px, no-JS, reduced motion/DPR 2 и отказы карты/режимы прозрачности в Edge. Отчёты: `artifacts/design/qa-liquid-glass-*.json`.

## Экспорт графики

Источник — встроенный ImageGen, финальный промпт в `assets/columns.prompt.txt`, происхождение/хеши в `assets/columns.provenance.json`. Исходный PNG сохранён локально в `.state/design-sources/columns-generated.png`; основной WebP сохраняет пиксели исходника, мобильный — lossless экспорт после уменьшения до 768 px. Это экспорт готовой графики, не CSS/SVG-замена изображения.

Для повторного экспорта нужен Sharp 0.35.5 как build tool:

```powershell
node scripts/export-design-images.cjs --source .state/design-sources/columns-generated.png --sharp-path <absolute-path-to-sharp>
```

## Browser QA

`scripts/test-components.cjs` использует те же `--playwright-path` / `--browser`. Проверяет `preview.html`: Space/native checkbox, disabled, tooltip focus/hover/Escape, regular/solid/aria-pressed, неизменную геометрию loading, ошибку/восстановление, dialog/Escape/возврат фокуса, 320–1440 px, центр формы, 200% размер текста, no-JS и reduced motion. Отчёты `artifacts/design/qa-components-*.json`, изображения `*-components-360.png` / `*-components-1440.png`.

Используется Playwright 1.62.1 из bundled workspace dependencies. На другой машине его можно установить как изолированный инструмент, без добавления runtime-пакетов в приложение. Браузеры хранятся в `.state/playwright-browsers`, результаты/скриншоты — в `artifacts/design`.

```powershell
$env:PLAYWRIGHT_BROWSERS_PATH = Join-Path (Get-Location).Path '.state/playwright-browsers'
node scripts/test-design.cjs --playwright-path <absolute-path-to-playwright> --browser edge
node scripts/test-design.cjs --playwright-path <absolute-path-to-playwright> --browser firefox
node scripts/test-design.cjs --playwright-path <absolute-path-to-playwright> --browser webkit
node scripts/test-dither.cjs --playwright-path <absolute-path-to-playwright> --browser edge
```

`test-dither.cjs` проверяет новый образец отдельно; для Firefox/WebKit заменить `--browser`. Проверки включают действительные цвета canvas, изменение/сброс параметров, отсутствие непрерывной перерисовки, 320–1440 px, DPR 2/reduced motion, no-JS и отказ Canvas. Отчёты — `artifacts/design/qa-dither-*.json`; предыдущие отчёты основной сцены не перезаписываются. Происхождение адаптированного алгоритма — `aceternity-dither.json`.

Перед тестами запустить `python -m http.server 3300 --bind 127.0.0.1 --directory design`. `edge` использует установленный Edge в новом headless-профиле, остальные — изолированные Playwright binaries. Они не подключаются к пользовательским браузерным сессиям. WebKit на Windows не является реальным Safari; CSS zoom, CPU throttling и media emulation не выдаются за проверки физического устройства или экранного диктора.

## Страницы ошибок

Общий статический шаблон находится в `design/errors/error-page.mjs`; он создаёт страницы 404, 403, 500 и потери связи, которые работают без браузерного JavaScript. После изменения шаблона или текстов пересобрать HTML-файлы:

```powershell
node design/errors/build.mjs
```

При запущенном локальном сервере страницы доступны по `/errors/404.html`, `/errors/403.html`, `/errors/500.html` и `/errors/connection.html`. Они остаются независимым образцом и не регистрируют HTTP-маршруты приложения.
