from pathlib import Path
import asyncio

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import load_config
from app.db.models import Tenant, Widget, WidgetAsset
from app.widgets.templates import get_template_html, DEFAULT_TEMPLATE_KEY


async def main() -> None:
    cfg = load_config()
    url = cfg.database_url
    if url.startswith('postgresql://'):
        url = url.replace('postgresql://', 'postgresql+asyncpg://', 1)

    engine = create_async_engine(url, future=True, echo=False)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with session_factory() as session:
        tenant = await session.scalar(select(Tenant).where(Tenant.slug == 'demo'))
        if tenant is None:
            tenant = Tenant(name='Demo Tenant', slug='demo')
            session.add(tenant)
            await session.flush()

        widget = await session.scalar(select(Widget).where(Widget.slug == 'demo-widget'))
        if widget is None:
            widget = Widget(
                tenant_id=tenant.id,
                name='Demo Widget',
                slug='demo-widget',
                ai_model='gemini-2.5-pro',
                intro_text='Добро пожаловать!',
                template=DEFAULT_TEMPLATE_KEY,
            )
            session.add(widget)
            await session.flush()

        await session.execute(delete(WidgetAsset).where(WidgetAsset.widget_id == widget.id))

        html_template = get_template_html(widget.template)
        asset = WidgetAsset(
            widget_id=widget.id,
            html=html_template,
            css=None,
            js=None,
            version=1,
        )
        session.add(asset)
        await session.commit()

    await engine.dispose()


if __name__ == '__main__':
    asyncio.run(main())
