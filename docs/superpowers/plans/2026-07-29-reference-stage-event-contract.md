# Reference Stage Event Contract Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox state for resumability.

**Goal:** Устранить падение durable worker после успешного анализа исходного сайта и вернуть пользователю бесплатную попытку.

**Architecture:** Строгая валидация `StageResult` сохраняется. Меняется только контракт разрешённых событий: существующее событие `reference.completed` признаётся допустимым наряду с `repair.completed`. Provider reconciliation и защита от повторного платного вызова не затрагиваются.

**Tech Stack:** Python, pytest, PostgreSQL worker queue, Docker, systemd.

---

- [x] Добавить регрессионный тест, создающий `StageResult` с `reference.completed`.
- [x] Подтвердить, что тест падает с `stage result event type is not allowed`.
- [x] Добавить `reference.completed` в белый список событий.
- [x] Прогнать точечный тест и релевантные тесты worker.
- [x] Закоммитить и отправить только относящиеся к исправлению файлы.
- [x] Собрать и развернуть неизменяемый образ production worker.
- [x] Проверить systemd, образ, контракт внутри контейнера и API health.
- [x] Проверить, что у пользователя доступна одна бесплатная генерация.
- [x] Обновить журнал продукта подтверждёнными результатами.
