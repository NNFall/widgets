# Developer-доступ к проектам Kaigo

Дата: 2026-08-24

Статус: реализовано и проверено локально; production rollout ожидает штатного
app/static release и настройки серверного OAuth allowlist.

## Что изменилось

Проверенная OAuth-сессия разработчика получает capability `all_projects`.
Она открывает любой проект в Studio, его версии, запуски, безопасные события,
артефакты, предпросмотр, чат и публикацию. В списке проектов показан владелец,
чтобы диагностика не теряла контекст.

Это не общий админский пароль и не ключ в ссылке. Capability вычисляется на
сервере по текущей verified Google/Yandex identity и allowlist. Без неё запросы
остаются owner/tenant-scoped.

## Биллинг и публикация

Для cross-project генерации, доработки и публикации entitlement проверяется у
владельца проекта. Developer-аккаунт не получает чужие лимиты и не оплачивает
чужой запуск. Платёжные, подписочные и удаляющие операции не расширялись.

## Проверка

- developer scope: verified allowlist, non-allowlisted identity и `/api/auth/session`;
- projects: список, чужой project/run, запуск с owner trial entitlement;
- publication: чужое состояние и публикация с entitlement владельца;
- frontend: TypeScript typecheck и StudioLibrary owner label.

Все проверки выполнены в рабочей копии. Production deployment и изменение
серверных секретов не выполнялись.
