# Expressive landing motion — evidence

Дата проверки: 2026-07-27.

## Кадры

- `hero-complete-1920.png` — финальное состояние первой cinematic-сборки hero при 1920×1080.
- `landing-full-1920.png` — полный desktop-лендинг после принудительного раскрытия viewport-секций.
- `landing-full-390.png` — полный mobile-лендинг при 390×844.
- `hero-complete-desktop-local.png` — ранний локальный контрольный кадр hero до финального QA.

## Проверенные свойства

- первая hero-сборка: `source → scanning → widget → complete`, затем пауза 12 секунд;
- повторные сборки быстрее, с паузой 11 секунд;
- при уходе hero из viewport фаза и единственный таймер приостанавливаются;
- все циклы отключаются при `prefers-reduced-motion: reduce`;
- desktop и mobile не создают горизонтального переполнения;
- центральный чат сохраняет собственный поворот после Reveal-анимации;
- Studio product/auth не изменялись.

## Автоматическая проверка

- Vitest: 13 файлов, 92/92 теста;
- Playwright: 11/11 сценариев;
- TypeScript, ESLint и production build: успешно;
- Axe: нет serious/critical нарушений на landing и Studio.
