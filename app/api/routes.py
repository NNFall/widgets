from aiohttp import web

from core import ai_service


async def healthcheck(request: web.Request) -> web.Response:
    return web.json_response({'status': 'ok'})


async def ai_healthcheck(request: web.Request) -> web.Response:
    model = request.query.get("model") or None
    status = await ai_service.check_provider(model=model)
    status_code = 200 if status.get("status") == "ok" else int(status.get("status_code", 502))
    return web.json_response(status, status=status_code)


def setup_api_routes(app: web.Application) -> None:
    app.router.add_get('/api/health', healthcheck)
    app.router.add_get('/api/health/ai', ai_healthcheck)
