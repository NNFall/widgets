# Reference Stage Event Contract Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox state for resumability.

**Goal:** Устранить падение durable worker после успешного анализа исходного сайта и вернуть пользователю бесплатную попытку.

**Architecture:** Строгая валидация `StageResult` сохраняется. Меняется только контракт разрешённых событий: существующее событие `reference.completed` признаётся допустимым наряду с `repair.completed`. Provider reconciliation и защита от повторного платного вызова не затрагиваются.

**Tech Stack:** Python, pytest, PostgreSQL worker queue, Docker, systemd.

---

- [ ] Добавить регрессионный тест, создающий `StageResult` с `reference.completed`.
- [ ] Подтвердить, что тест падает с `stage result event type is not allowed`.
- [ ] Добавить `reference.completed` в белый список событий.
- [ ] Прогнать точечный тест и релевантные тесты worker.
- [ ] Закоммитить и отправить только относящиеся к исправлению файлы.
- [ ] Собрать и развернуть неизменяемый образ production worker.
- [ ] Проверить systemd, образ, контракт внутри контейнера и API health.
- [ ] Проверить, что у пользователя доступна одна бесплатная генерация.
- [ ] Обновить журнал продукта подтверждёнными результатами.
