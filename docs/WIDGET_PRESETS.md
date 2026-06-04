# Widget Presets

Дата обновления: 2026-06-04

## Что добавлено

В проекте есть набор готовых демо-шаблонов для наполнения базы и публичных страниц:

| Slug | Название | Сценарий |
| --- | --- | --- |
| `demka` | Kaigo Demo Consultant | Универсальный консультант Kaigo Widgets |
| `dental-consultant` | Стоматология: консультант записи | Услуги клиники, первичная маршрутизация, запись |
| `realty-consultant` | Недвижимость: подбор объекта | Покупка, аренда, подбор и заявка риелтору |
| `auto-service-consultant` | Автосервис: запись и диагностика | Симптомы авто, ТО, диагностика, запись |
| `beauty-consultant` | Салон красоты: запись на услугу | Подбор услуги, мастер, время записи |

Код preset-ов лежит в `app/widgets/presets.py`. Шаблоны подключены в `app/widgets/templates.py` и отображаются в админском выборе шаблонов.

## Как применить seed

Запускать на сервере внутри app-контейнера:

```bash
cd /root/ai_project
docker exec ai_project_app python scripts/seed_widget_presets.py
```

Скрипт идемпотентный:

- создает недостающие виджеты;
- обновляет настройки существующих preset-виджетов;
- добавляет новую asset-версию только если HTML изменился;
- не удаляет пользовательские виджеты.

## Prompt Source

Поле `prompt_source` теперь поддерживает два режима:

- `https://docs.google.com/document/...` - загрузка системного промпта из Google Docs;
- обычный текст - inline системный промпт, который хранится в базе.

Inline prompt нужен для демо-шаблонов, чтобы каждый виджет отвечал по своему сценарию без отдельного Google Doc.

## Smoke Tests

Проверка страниц:

```bash
curl -k https://kaigo.space/
curl -k https://kaigo.space/w/demka
curl -k https://kaigo.space/w/dental-consultant
curl -k https://kaigo.space/w/realty-consultant
curl -k https://kaigo.space/w/auto-service-consultant
curl -k https://kaigo.space/w/beauty-consultant
```

Проверка AI-ответа:

```bash
curl -k -H 'Content-Type: application/json' \
  --data '{"message":"У меня болит зуб, хочу записаться завтра вечером","user_id":"preset-smoke"}' \
  https://kaigo.space/w/dental-consultant/api/send
```
