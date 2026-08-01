# Ускоренный визуальный анализ референсного сайта

Дата: 1 августа 2026 года.

## Результат

- Двухпроходное сканирование сохранено: прогрев вниз, возврат наверх, повторный проход с фиксацией доказательств.
- Для desktop и mobile по-прежнему создаются кадры `top`, `middle` и `bottom`.
- Обратная прокрутка больше не выполняет полную стабилизацию на каждом шаге: один полный settle выполняется после возврата наверх и повторной проверки позиции.
- Ожидание визуальной тишины и ресурсов во время прямой прокрутки выполняется параллельно, без дополнительной фиксированной паузы.
- `document.fonts.ready` ограничен локальным timeout. Таймаут шрифтов остаётся некритичным предупреждением и не скрывает ошибку изображения.
- При ошибке или отмене незавершённая параллельная browser-задача отменяется и дожидается, поэтому worker не оставляет фоновые операции на закрывающейся странице.
- При `max_pages=1` больше не выполняется ненужный поиск sitemap.

## Измерения

Локальный Playwright fixture с lazy-load:

- до изменения: `65,565 с`;
- после окончательного исправления: `34,441 с`;
- ускорение: примерно `47,5%`.

Production crawl `https://www.python.org/` без вызова модели:

- итог: `succeeded`, coverage `complete`;
- внешнее время процесса: `74,66 с`;
- desktop capture: `22,780 с`;
- mobile capture: `39,999 с`;
- токены: `0`;
- стоимость: `$0`.

Фазы desktop:

- load/initial settle: `5,974 с`;
- warm pass: `6,400 с`, 5 шагов;
- reset pass: `1,723 с`, 3 шага;
- evidence pass: `6,392 с`, 5 шагов;
- final settle/screenshots: `2,291 с`.

Фазы mobile:

- load/initial settle: `5,811 с`;
- warm pass: `14,097 с`, 13 шагов;
- reset pass: `2,784 с`, 11 шагов;
- evidence pass: `15,026 с`, 13 шагов;
- final settle/screenshots: `2,281 с`.

Контрольный `kontur.ru` не дошёл до capture: защищённая навигация дважды превысила page deadline примерно за 95 секунд. Это отдельная проблема pre-navigation/resource proxy, а не новый scroll-settle.

## Проверки

- 11 изменённых browser/unit-контрактов: passed.
- 6 reference pipeline тестов: passed.
- Дополнительный набор из 28 crawler/pipeline тестов: passed.
- Два независимых review: spec compliance — PASS; code quality — PASS.
- Ruff, compileall и `git diff --check`: passed.
- Полный исторический `test_reference_crawler.py` не завершился за 304 секунды; test failure не зафиксирован, но длительность самой legacy-suite остаётся отдельным техническим долгом.
- Production commit: `bcbbf73`.
- HTTP smoke: `https://kaigo.space/` вернул 200; лендинг проверен во встроенном браузере Codex.

## Снимки

- [`desktop-top.jpg`](assets/python-org-capture-2026-08-01/desktop-top.jpg)
- [`desktop-middle.jpg`](assets/python-org-capture-2026-08-01/desktop-middle.jpg)
- [`desktop-bottom.jpg`](assets/python-org-capture-2026-08-01/desktop-bottom.jpg)
- [`mobile-top.jpg`](assets/python-org-capture-2026-08-01/mobile-top.jpg)
- [`mobile-middle.jpg`](assets/python-org-capture-2026-08-01/mobile-middle.jpg)
- [`mobile-bottom.jpg`](assets/python-org-capture-2026-08-01/mobile-bottom.jpg)

## Следующий шаг

Главный блокер полной новой генерации остаётся внешним: Gemini vision возвращает billing `403`, а текущий AgentRouter credential на OpenAI-compatible endpoint возвращает `401`. Следующая безопасная работа — добавить feature-flagged мультимодальный fallback и проверить его только после выдачи рабочего credential/model route.
