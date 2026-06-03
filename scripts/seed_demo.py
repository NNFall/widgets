import asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import load_config
from app.db.base import Base  # noqa: F401
from app.db.models import Tenant, User, Widget, WidgetAsset
from core.security import hash_password

CLIENT_EMAIL = "client@example.com"
CLIENT_PASSWORD = "client123"


async def main() -> None:
    cfg = load_config()
    database_url = cfg.database_url
    if database_url.startswith("postgresql://"):
        database_url = database_url.replace("postgresql://", "postgresql+asyncpg://", 1)

    engine = create_async_engine(database_url, future=True, echo=False)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with session_factory() as session:
        tenant = await session.scalar(select(Tenant).where(Tenant.slug == "demo"))
        if tenant is None:
            tenant = Tenant(name="Demo Tenant", slug="demo")
            session.add(tenant)
            await session.flush()

        user = await session.scalar(select(User).where(User.email == CLIENT_EMAIL))
        if user is None:
            user = User(
                tenant_id=tenant.id,
                email=CLIENT_EMAIL,
                password_hash=hash_password(CLIENT_PASSWORD),
                role="tenant_admin",
            )
            session.add(user)

        widget = await session.scalar(select(Widget).where(Widget.slug == "demo-widget"))
        if widget is None:
            widget = Widget(
                tenant_id=tenant.id,
                name="Demo Widget",
                slug="demo-widget",
                ai_model="gemini-2.5-pro",
                intro_text="Здравствуйте!"
            )
            session.add(widget)
            await session.flush()

        latest_asset = await session.scalar(
            select(WidgetAsset)
            .where(WidgetAsset.widget_id == widget.id)
            .order_by(WidgetAsset.version.desc())
        )

        if latest_asset is None:
            asset = WidgetAsset(
                widget_id=widget.id,
                html='<div class="demo-widget">Добро пожаловать!</div>',
                css='.demo-widget { font-family: Arial; font-size: 18px; padding: 16px; border: 1px solid #2563eb; border-radius: 12px; color: #2563eb; }',
                js='console.log("Demo widget loaded");',
            )
            session.add(asset)

        await session.commit()

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
