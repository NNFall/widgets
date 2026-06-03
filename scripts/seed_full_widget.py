import asyncio
import re
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import load_config
from app.db.models import Tenant, Widget, WidgetAsset


async def main() -> None:
    cfg = load_config()
    database_url = cfg.database_url
    if database_url.startswith("postgresql://"):
        database_url = database_url.replace("postgresql://", "postgresql+asyncpg://", 1)

    engine = create_async_engine(database_url, future=True, echo=False)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    source = Path("wrappers/web_app_aiohttp.py").read_text(encoding="utf-8")
    match = re.search(r"HTML_PAGE\s*=\s*\"\"\"(.*?)\"\"\"", source, re.S)
    if not match:
        raise RuntimeError("Не удалось найти HTML_PAGE в wrappers/web_app_aiohttp.py")
    html_template = match.group(1)

    async with session_factory() as session:
        tenant = await session.scalar(select(Tenant).where(Tenant.slug == "demo"))
        if tenant is None:
            tenant = Tenant(name="Demo Tenant", slug="demo")
            session.add(tenant)
            await session.flush()

        widget = await session.scalar(select(Widget).where(Widget.slug == "demo-widget"))
        if widget is None:
            widget = Widget(
                tenant_id=tenant.id,
                name="Demo Widget",
                slug="demo-widget",
                ai_model="gemini-3-flash-preview",
                intro_text="Добро пожаловать!",
            )
            session.add(widget)
            await session.flush()

        current_version = await session.scalar(
            select(func.coalesce(func.max(WidgetAsset.version), 0)).where(WidgetAsset.widget_id == widget.id)
        )

        asset = WidgetAsset(
            widget_id=widget.id,
            html=html_template,
            css=None,
            js=None,
            version=current_version + 1,
        )
        session.add(asset)
        await session.commit()

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
