from __future__ import annotations

import asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import load_config
from app.db.models import Tenant, Widget, WidgetAsset
from app.widgets.presets import PRESETS, build_widget_html
from core.config import settings as core_settings


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

        created = 0
        updated = 0
        asset_versions = 0

        for preset in PRESETS:
            widget = await session.scalar(select(Widget).where(Widget.slug == preset.slug))
            if widget is None:
                widget = Widget(
                    tenant_id=tenant.id,
                    name=preset.name,
                    slug=preset.slug,
                    ai_model=core_settings.default_model,
                    prompt_source=preset.prompt,
                    intro_text=preset.greeting,
                    status="published",
                    template=preset.template_key,
                    stt_model=core_settings.default_stt_model,
                    temperature=preset.temperature,
                    max_tokens=preset.max_tokens,
                )
                session.add(widget)
                await session.flush()
                created += 1
            else:
                widget.tenant_id = tenant.id
                widget.name = preset.name
                widget.ai_model = core_settings.default_model
                widget.prompt_source = preset.prompt
                widget.intro_text = preset.greeting
                widget.status = "published"
                widget.template = preset.template_key
                widget.stt_model = core_settings.default_stt_model
                widget.temperature = preset.temperature
                widget.max_tokens = preset.max_tokens
                updated += 1

            html = build_widget_html(preset)
            latest_asset = await session.scalar(
                select(WidgetAsset)
                .where(WidgetAsset.widget_id == widget.id)
                .order_by(WidgetAsset.version.desc())
            )
            if latest_asset is None or latest_asset.html != html:
                next_version = (latest_asset.version if latest_asset else 0) + 1
                session.add(
                    WidgetAsset(
                        widget_id=widget.id,
                        html=html,
                        css=None,
                        js=None,
                        version=next_version,
                    )
                )
                asset_versions += 1

        await session.commit()

    await engine.dispose()
    print(
        "Seeded widget presets: "
        f"created={created}, updated={updated}, asset_versions={asset_versions}"
    )


if __name__ == "__main__":
    asyncio.run(main())
