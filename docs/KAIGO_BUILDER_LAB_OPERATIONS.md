# Kaigo Gemini Builder Lab: эксплуатация

Builder Lab — изолированный эксперимент для сравнения двух способов создания
виджета:

- `Gemini staged`: пять последовательных вызовов `gemini-3.5-flash` с JSON-схемой;
- `Antigravity agent`: автономная сборка в Google remote environment с последующим
  скачиванием и независимой проверкой tar snapshot.

Lab не зарегистрирован в production aiohttp-приложении, не подключён к nginx, не
имеет publish-маршрута и не читает/не изменяет таблицы `widgets` и
`widget_assets`. Все запуски и валидные ревизии хранятся только в памяти и
исчезают после перезапуска.

## Переменные окружения

Для модели нужен один из ключей в порядке приоритета:

```text
GEMINI_API_KEY
GOOGLE_AI_API_KEY
GOOGLE_API_KEY
```

Ключ нельзя передавать в URL, коммитить или копировать в логи. Основные настройки:

```env
KAIGO_BUILDER_LAB_HOST=127.0.0.1
KAIGO_BUILDER_LAB_PORT=8091
KAIGO_BUILDER_ENABLE_ANTIGRAVITY=true
GEMINI_BUILDER_MODEL=gemini-3.5-flash
GEMINI_BUILDER_TEMPERATURE=0.9
GEMINI_BUILDER_MAX_REPAIRS=2
GEMINI_ANTIGRAVITY_AGENT=antigravity-preview-05-2026
GEMINI_ANTIGRAVITY_TIMEOUT_SECONDS=900
GEMINI_ANTIGRAVITY_MAX_SNAPSHOT_BYTES=10485760
```

`GOOGLE_AI_NATIVE_BASE_URL` может указывать на официальный Google endpoint или
на существующий защищённый Gemini-only маршрут через американский сервер. SDK
нормализует финальный `/v1beta`, поэтому оба варианта допустимы:

```text
https://generativelanguage.googleapis.com
https://generativelanguage.googleapis.com/v1beta
http://host-gateway:PORT/PROTECTED_PREFIX/v1beta
```

Не используйте Builder Lab как универсальный прокси. Его клиенты обращаются
только к Gemini GenerateContent, Interactions и Files API.

## Локальный запуск

Из корня репозитория:

```powershell
$env:GEMINI_API_KEY = "значение-из-секретного-хранилища"
python scripts/run_builder_lab.py
```

Откройте `http://127.0.0.1:8091`. Не задавайте `0.0.0.0` на рабочей машине.
Проверка без браузера:

```powershell
python scripts/smoke_builder_lab.py --engine direct
```

Smoke выводит только sequence, stage, status, usage и elapsed time. В нём нет
ключа, полного prompt, provider URL или содержимого виджета.

## Запуск в Docker на сервере

Сервис находится в отдельном Compose profile и не стартует при обычном
`docker compose up -d`:

```bash
docker compose --profile builder-lab build builder-lab
docker compose --profile builder-lab up -d builder-lab
docker compose --profile builder-lab logs -f --tail=200 builder-lab
```

Внутри контейнера процесс слушает `0.0.0.0:8091`, но Docker публикует его
исключительно на host loopback `127.0.0.1:8091`. У сервиса нет nginx labels,
public domain route и зависимости от PostgreSQL.

Обязательная проверка после запуска:

```bash
docker compose --profile builder-lab ps
ss -lntp | grep 8091
curl -I http://127.0.0.1:8091/
```

В `ss` должен присутствовать только `127.0.0.1:8091`, а не `0.0.0.0:8091` на
хосте.

## Доступ через SSH

Создайте локальный port forward в отдельном терминале:

```bash
ssh -L 8091:127.0.0.1:8091 root@SERVER_IP
```

После подключения откройте локально `http://127.0.0.1:8091`. Не добавляйте для
этого эксперимента location в nginx и не переключайте домен.

## Как читать журнал

Preview обновляется только после пары событий:

```text
artifact.validated  status=completed
artifact.committed  status=completed
```

`stage.completed` означает лишь, что модель закончила ответ. Если после него
валидатор нашёл проблему, текущий preview сохраняется, а в журнале появляются
`repair.started` и `repair.completed`. Повторяющийся fingerprint ошибки или две
неудачные repair-попытки завершают run с `invalid_artifact`.

В direct-режиме нормальный результат содержит пять committed revisions:

```text
art_direction -> foundation -> identity -> conversation -> motion_polish
```

Antigravity считается успешным только после скачивания
`environment-<environment_id>`, безопасного чтения
`out/widget-artifact.json`/`out/build-report.json` и независимой Kaigo-валидации.
Финальный текст агента не является артефактом.

## Остановка и откат

Остановить только лабораторию:

```bash
docker compose --profile builder-lab stop builder-lab
docker compose --profile builder-lab rm -f builder-lab
```

Это не останавливает `app`, `db`, nginx или статический сайт. После live-smoke
дополнительно проверьте неизменность production:

```bash
curl -fsS http://127.0.0.1:8080/api/health
curl -fsS http://127.0.0.1:8080/w/demka >/dev/null
```

## Ограничения

- Antigravity находится в Public Preview и может быть недоступен проекту.
- У Antigravity более высокий и менее предсказуемый расход токенов; один bounded
  запуск используйте как сравнение, а не как автоматический fallback.
- Lab не выполняет website crawl, не публикует embed-код, не списывает баланс и
  не хранит результат после restart.
- Для публичного доступа потребуется отдельная спецификация аутентификации,
  tenant isolation, durable jobs и production preview origin.
