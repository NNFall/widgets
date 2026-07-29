# Сравнение моделей Kaigo Agent Kernel

Обе модели получили один и тот же frozen evidence bundle и composition plan.

| Модель | Время, с | Токены | Стоимость, ₽ | Retry | Repairs | Browser repairs | Visual | Publishable | Ошибка |
|---|---:|---:|---:|---:|---:|---:|---:|:---:|---|
| glm-5.2 | 786.89 | 270628 | 162.38 | 4 | 4 | 3 | 0.62 | нет | invalid_response |
| gpt-5.5 | 1482.47 | 144428 | 101.10 | 1 | 4 | 3 | 0.77 | нет | provider_unavailable |

Visual score получен одним независимым оценщиком для обеих моделей: `codex_independent_visual_review`, рубрика `kaigo-strict-visual-v1`.
Visual review SHA-256: `3f38eacc2d90b80ea351667e97cf7cf019bd1a51683f1c262250aedd2160f788`

Evidence SHA-256: `eb77405b50e20578c1c7f49ae3d33854533d2a409f5857eb43b70cc8ae116c50`
Composition SHA-256: `eaaea9b9a5edf84c9526719ad0f0a94f7bad3b476264c7310a5fc5b7c750443c`

Полные prompts, исходный текст сайта, API-ключи и authorization headers в отчёт не записываются.
