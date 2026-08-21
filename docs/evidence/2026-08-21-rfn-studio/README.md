# RFN: история доработки и mobile preview

Дата проверки: 2026-08-21.

Источник: <https://rfn-protection.com/>

Project ID: `46c6a329-4c2e-5c99-927d-afc28de4311d`

Refinement run ID: `104de13f-669c-4fff-91fa-f9788a37b394`
Активная версия: 2, `aab086de-7bb3-4818-99ec-8e63fd2c5e0c`.

Снимки сделаны во встроенном браузере из production-сборки `frontend/dist`,
которую локальный owner-scoped QA server наполнял подтверждёнными публичными
данными RFN. Это проверка интерфейса, а не production deployment и не запрос к
живой базе.

## Кадры

- `rfn-studio-1280x720.png` — одновременно видны запрос пользователя,
  «Готово — версия 2», публичный итог и mobile preview.
- `rfn-studio-chat-390x844.png` — мобильный левый чат с восстановленной после
  reload историей доработки.
- `rfn-studio-preview-390x844.png` — полный исходный viewport виджета 390 × 844
  вписан в доступный canvas.
- `rfn-studio-preview-320x568.png` — тот же viewport полностью виден на самом
  маленьком обязательном размере.

## Геометрия до и после

До исправления, по production-диагностике задачи:

| Host viewport | Нижняя граница iframe | Выход за viewport |
| --- | ---: | ---: |
| 390 × 720 | ≈835 px | ≈115 px |
| 360 × 640 | ≈776 px | ≈136 px |
| 320 × 568 | ≈712 px | ≈144 px |

После исправления, измерено во встроенном браузере:

| Host viewport | Scale | Canvas bottom | Iframe bottom | Запас снизу |
| --- | ---: | ---: | ---: | ---: |
| 1280 × 720 | 0.6209 | 699 px | 684 px | 15 px |
| 390 × 844 | 0.7227 | 828 px | 813 px | 15 px |
| 390 × 720 | 0.5758 | 704 px | 689 px | 15 px |
| 360 × 640 | 0.4810 | 624 px | 609 px | 15 px |
| 320 × 568 | 0.3957 | 552 px | 537 px | 15 px |

На всех размерах `document.scrollWidth === innerWidth` и
`document.scrollHeight === innerHeight`. Внутренний iframe сохраняет viewport
390 × 844; уменьшается только внешний device через transform.

## Проверки и граница релиза

- refinement/history unit: 11/11;
- `SaasStudioFlow`: 43/43;
- полный frontend unit: 37 файлов, 337/337;
- Playwright: 24/24, включая mobile geometry и accessibility;
- TypeScript, ESLint и production build: passed;
- backend route/deployment scope: 101 passed, 3 skipped.

Production deployment не выполнялся. Первый serial unit run поймал timing-флейк
в неизменённом тесте публикации; повтор текущего полного набора прошёл 337/337.
Billing/publication в рамках задачи не изменялись.

Локальная ссылка, пока запущен QA server:
<http://127.0.0.1:4175/studio?project=46c6a329-4c2e-5c99-927d-afc28de4311d>.
