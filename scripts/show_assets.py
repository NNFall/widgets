import asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import load_config
from app.db.models import Widget, WidgetAsset


async def main() -> None:
    cfg = load_config()
    database_url = cfg.database_url
    if database_url.startswith('postgresql://'):
        database_url = database_url.replace('postgresql://', 'postgresql+asyncpg://', 1)

    engine = create_async_engine(database_url, future=True, echo=False)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with session_factory() as session:
        widget = await session.scalar(select(Widget).where(Widget.slug == 'demo-widget'))
        if widget is None:
            print('Widget not found')
        else:
            result = await session.execute(
                select(WidgetAsset.version, WidgetAsset.created_at)
                .where(WidgetAsset.widget_id == widget.id)
                .order_by(WidgetAsset.version)
            )
            rows = result.all()
            if not rows:
                print('No assets')
            else:
                for version, created_at in rows:
                    print(f'version={version}, created_at={created_at}')

    await engine.dispose()


if __name__ == '__main__':
    asyncio.run(main())
