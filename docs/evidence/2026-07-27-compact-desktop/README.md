# Compact desktop и бренд Kaigo — evidence

Дата проверки: 2026-07-27. Проверенный HEAD: `3b3ad1c`.

## Scope

Подтверждён локальный релизный контракт для первого экрана лендинга:

- compact desktop `1536×830`, соответствующий рабочей области браузера при
  физическом разрешении 1920×1200 и системном масштабе Windows 125%;
- повторная проверка компактного режима при `1366×768`;
- сохранение desktop `1920×1080`, mobile `390×844`, reduced motion и
  accessibility-сценариев;
- новый геометрический знак `K`, favicon `/favicon.svg` и заголовок вкладки
  `Kaigo — AI в вашем бизнесе за 10 минут`.

Production/deploy в этот пакет доказательств не входят.

## RED → GREEN

RED был зафиксирован отдельным коммитом контрактов `525afbd` до реализации:

- unit-контракт не находил `[data-kaigo-mark="K"]`;
- compact E2E не находил новый title и favicon;
- высота прежнего header составляла 112 px при требовании не более 96 px.

GREEN на текущем HEAD:

- unit suite: 93/93;
- полный Playwright suite: 12/12;
- compact E2E сначала проверяет `1536×830`, затем в том же сценарии повторяет
  контракт первого экрана на `1366×768`;
- новый `K`, title, favicon, минимальная цель логотипа 44 px, отсутствие
  перекрытий и горизонтального overflow входят в исполняемый контракт.

## Computed metrics

Все значения получены из браузерного layout в CSS-пикселях после перехода hero
в фазу `complete`.

| Viewport | Header | H1 font-size | Logo target | Label bottom | Widget bottom | Horizontal overflow |
|---|---:|---:|---:|---:|---:|---:|
| 1536×830 | 94 px | 48.384 px | 44 px | 768.52 px | 691.10 px | 0 px |
| 1366×768 | 94 px | 46 px | 44 px | 700.76 px | 632.45 px | 0 px |

## Verification

Команды выполнялись из `frontend`:

| Команда | Результат |
|---|---|
| `npm test` | PASS, 93/93 unit-теста |
| `npm run lint` | PASS |
| `npm run build` | PASS; CSS 100.87 kB, JS 483.62 kB |
| `npx playwright test` | PASS, 12/12: compact 1536 с повторной проверкой 1366, desktop 1920, mobile 390, reduced motion и a11y |

## Brand assets

- [Концепт и описание K-mark](../2026-07-27-kaigo-k-mark/README.md)
- [Production-aligned K-mark preview](../2026-07-27-kaigo-k-mark/concept.png)
- [React-источник логотипа](../../../frontend/src/shared/KaigoLogo.tsx)
- [SVG favicon](../../../frontend/public/favicon.svg)
- [Title и подключение favicon](../../../frontend/index.html)

## Ограничение и следующий шаг

Факт: во время финальной попытки снять кадры in-app Browser был временно
отключён. Поэтому в этой директории нет финальных screenshot-файлов; пустые или
неподтверждённые PNG не создавались.

План: после восстановления подключения отдельно сохранить проверенные кадры
`1536×830` и `1920×1080`, не меняя приведённые выше автоматические результаты.
