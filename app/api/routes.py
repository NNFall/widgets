from aiohttp import web


async def healthcheck(request: web.Request) -> web.Response:
    return web.json_response({'status': 'ok'})


def setup_api_routes(app: web.Application) -> None:
    app.router.add_get('/api/health', healthcheck)
