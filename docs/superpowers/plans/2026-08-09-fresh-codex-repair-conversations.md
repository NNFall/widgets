# План: свежие Codex-диалоги для repair

1. Тестами зафиксировать уникальность ключа по logical invocation ID.
2. Передать logical invocation lineage из router metadata в Codex provider.
3. Изменить только ключи `repair` и `code_review`; остальные роли оставить как есть.
4. Запустить provider, router, bridge runner и worker focused suites.
5. Развернуть worker/bridge и выполнить короткий контролируемый repair-сценарий.
6. Сравнить входные токены и длительность последовательных repair-вызовов.
