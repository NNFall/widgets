# Adaptive reference capture latency

Дата: 2026-08-01

Статус: утверждено пользователем в рамках автономного выполнения дорожной карты
проверяемого MVP.

## Проблема

Production-прогон RAW BUREAU потратил 280,4 секунды на `reference_analysis`, а
контрольные Dodo/RAW-запуски не дошли до первого model prompt в течение 4–6 минут.
Профилирование показало, что задержка создаётся самим crawler: для desktop и mobile
последовательно выполняются прогрев вниз, возврат вверх и evidence-проход вниз. На
каждом шаге складываются фиксированная пауза, visual quiet и отдельное ожидание
изображений. При 40 шагах минимальная сумма ожиданий двух viewport составляет около
352,6 секунды без учёта сети и запуска браузера.

## Утверждённое поведение

Качество двухпроходного capture не ослабляется:

- остаются реальные wheel-события для прогрева и возврата;
- первый screenshot снимается только после проверенного возврата к исходной позиции;
- evidence-проход по-прежнему собирает top, middle и bottom/last-observed;
- desktop и mobile остаются независимыми проверками;
- step/height/byte/SSRF/robots ограничения не ослабляются.

Оптимизация состоит из четырёх локальных изменений:

1. При возврате наверх не ждать изображения и visual quiet после каждого обратного
   wheel. Состояние скролла проверяется после существующей 50-миллисекундной
   стабилизации, а полный settle выполняется один раз после доказанного возврата.
2. На прямых проходах заменить последовательность `fixed delay → visual quiet →
   images` одним bounded settle: visual quiet получает минимальное время не меньше
   `scroll_delay_ms` и параллельно ждёт viewport images. Общий шаг сохраняет минимум
   750 мс, но не суммирует независимые ожидания.
3. Локально ограничить `document.fonts.ready` тем же timeout, который уже передан в
   helper. Невыполнение становится таким же non-fatal settle warning, как медленное
   изображение; crawl-wide timeout больше не является единственной границей.
4. Не загружать sitemap, когда pipeline явно задаёт `max_pages=1`.

## Наблюдаемость

`ReferencePageEvidence.timings_ms` сохраняет минимум:

- `load_and_initial_settle`;
- `warm_pass`;
- `reset_pass`;
- `evidence_pass`;
- `final_settle_and_screenshots`;
- `total`.

Также сохраняются счётчики шагов прогрева, возврата и evidence-прохода. Значения не
содержат URL, prompt, cookies или другие приватные данные.

## Ошибки

- Невозможность вернуть исходную scroll-позицию остаётся fatal.
- Timeout viewport image/font settle остаётся non-fatal и попадает в
  `skipped_reasons`.
- Partial capture по step/height cap не превращается в complete.
- Простое уменьшение `max_scroll_steps` или общего timeout не используется как
  оптимизация.

## Критерии готовности

1. Детерминированный lazy fixture сохраняет все прежние доказательства и проходит
   минимум на 30% быстрее baseline 68 секунд.
2. Возврат содержит реальные отрицательные wheel-события и одну финальную
   стабилизацию, а не settle на каждом шаге.
3. `max_pages=1` не вызывает sitemap discovery.
4. Зависший `document.fonts.ready` завершается локальным timeout.
5. Полный crawler regression, Ruff, compileall и `git diff --check` проходят.
6. Production capture нового сайта фиксирует фазовые timings до первого model call.

## Не входит в этот срез

- межзапусковый reference cache;
- совместный browser cache desktop/mobile;
- замена двух проходов одним;
- прямой AgentRouter vision fallback. Он оформляется отдельным срезом после этой
  оптимизации и использует inline bytes без provider-fetchable URL.
