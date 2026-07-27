# Release packet: GLM-5.2 против GPT-5.5

Дата: 2026-07-28
Статус: готово к редактуре

## Крючок

У GPT-5.5 цена токена выше, но один и тот же AI-виджет он собрал почти в три
раза дешевле GLM-5.2. Причина оказалась не в тарифе, а в количестве reasoning и
output tokens.

## Факты для поста

- Одинаковый Flowwow brief и одинаковый Kaigo chat-контракт.
- Чистый прогон одной модели на вариант, без смешивания.
- Обеим моделям понадобились генерация и один автоматический ремонт.
- GLM: 12 → 0 ошибок, 511,637 с, 187 228 input+output tokens, 112,34 ₽.
- GPT: 4 → 0 ошибок, 542,154 с, 54 733 input+output tokens, 38,31 ₽.
- Оба варианта открываются, закрываются и принимают сообщение в реальном
  browser preview.
- GPT-вариант семантически аккуратнее; GLM-вариант выразительнее использует
  цветочную метафору, но launcher потребует дополнительной accessibility
  проверки.

## Что не утверждать

- Это один конкретный прогон, а не универсальный рейтинг моделей.
- Суммы расчётные по тарифам кабинета и курсу 100 ₽/$; фактическое списание
  сверяется по request ID.
- Ноль детерминированных ошибок не равен полной production-готовности: остаются
  browser, visual и accessibility gates.

## Материалы

- `benchmarks/agentrouter/results/flowwow-pure-model-v1/README.md`
- `benchmarks/agentrouter/results/flowwow-pure-model-v1/glm-5.2/preview.html`
- `benchmarks/agentrouter/results/flowwow-pure-model-v1/gpt-5.5/preview.html`
- отчёты `report.json` рядом с каждым preview
