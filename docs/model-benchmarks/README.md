# Сравнение моделей Kaigo Agent Kernel

Скрипт `scripts/compare_builder_models.py` сравнивает модели только на одном
замороженном входе и одном замороженном composition plan. Это исключает ситуацию,
когда одна модель получает более простой сайт, другой бриф или иной набор паттернов.

Публичный отчет содержит только:

- SHA-256 входного evidence bundle и composition plan;
- модель и провайдера;
- время, input/output/thinking tokens и стоимость в долларах и рублях;
- количество retry/repair;
- результаты детерминированной и браузерной проверки;
- visual score, только если его вернул отдельный общий визуальный оценщик;
- итоговую publishability.

Промпты, исходный текст сайта, ответы моделей, API-ключи и authorization headers в
публичный отчет не попадают. Полные артефакты сохраняются локально в
`data/benchmarks/private/`, который исключен из Git.

Пример запуска из корня репозитория:

```powershell
$env:AGENTROUTER_API_KEY = "<секретный ключ>"
python scripts/compare_builder_models.py `
  --evidence benchmarks/agentrouter/agent-kernel-frozen-v1/evidence.json `
  --composition benchmarks/agentrouter/agent-kernel-frozen-v1/composition.json `
  --visual-review benchmarks/agentrouter/agent-kernel-frozen-v1/visual-review.json `
  --target agentrouter:glm-5.2 `
  --target agentrouter:gpt-5.5 `
  --usd-to-rub 100 `
  --output docs/model-benchmarks/2026-07-29-agent-kernel-frozen-v1.json
```

Если генерация уже измерена, но Chromium нашёл геометрический дефект, повторно
оплачивать успешные generation/repair вызовы не нужно. Сохранённый отчет и последняя
ревизия продолжаются отдельным visual/browser repair циклом:

```powershell
python scripts/compare_builder_models.py `
  --evidence benchmarks/agentrouter/agent-kernel-frozen-v1/evidence.json `
  --composition benchmarks/agentrouter/agent-kernel-frozen-v1/composition.json `
  --target agentrouter:glm-5.2 `
  --target agentrouter:gpt-5.5 `
  --output docs/model-benchmarks/2026-07-29-agent-kernel-frozen-v1.json `
  --private-dir data/benchmarks/private/agent-kernel-frozen-v1-final `
  --browser-repair-limit 2 `
  --resume-browser-repairs
```

Thinking tokens считаются частью output, а не прибавляются к нему второй раз. Цена
в отчете рассчитывается по фактическим input/output tokens и зафиксированным ставкам
маршрутизатора. Если отдельный общий visual evaluator не запускался, `visual_score`
остается `null`: скрипт не выдумывает оценку.

Для итогового сравнения можно передать один замороженный `--visual-review`. Один и
тот же независимый оценщик обязан применить одну рубрику ко всем моделям. Файл
попадает в `input_identity` своим SHA-256, а в публичный отчет переносятся только
источник, версия рубрики и нормализованные оценки. Текущий review использует десять
измерений production-рубрики Kaigo и шесть BrowserAudit-кадров каждой модели;
полные кадры остаются в игнорируемом `data/benchmarks/private/`.
