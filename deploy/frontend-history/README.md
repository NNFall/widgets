# Архив интерфейса Kaigo

Статический архив собирает одиннадцать визуально значимых frontend-коммитов и публикует
их под `https://kaigo.space/frontend/`, не изменяя текущие маршруты `/` и
`/studio`.

Каждый коммит собирается отдельно с собственным Vite `base`. Корневые ссылки на
public-assets переписываются в неизменяемый versioned path. Для Studio, product
tour и четырёх правовых страниц (`privacy`, `personal-data-consent`, `terms`,
`offer`) создаются полноэкранные wrapper-страницы, поэтому верхний адрес
остаётся внутри `/frontend/vN/`, а историческое приложение получает ожидаемый
pathname. Для legal/tour views это `/<view>?archive=vN`; Studio дополнительно
сохраняет технический project query для восстановления старого экрана.

Запросы `/api/` из архивных страниц перенаправляются bootstrap-скриптом в
`/frontend-preview-api/`, который проксируется на изолированный preview API на
`127.0.0.1:18110`. Корневые API-ссылки и iframe URL также переписываются во
время сборки, а production cookies, authorization headers и `Set-Cookie`
отсекаются на nginx. Production API и production-сессию архив не использует.

Сборка на сервере:

```bash
scripts/deploy_frontend_history.sh
```

Nginx location-контракт находится в `deploy/frontend-history/nginx.conf`.
