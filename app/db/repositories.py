from __future__ import annotations

from typing import Optional, Sequence

from sqlalchemy import delete, select, update, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import models


class TenantRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_all(self) -> Sequence[models.Tenant]:
        result = await self.session.execute(select(models.Tenant).order_by(models.Tenant.id))
        return result.scalars().all()

    async def get(self, tenant_id: int) -> models.Tenant | None:
        result = await self.session.execute(
            select(models.Tenant).where(models.Tenant.id == tenant_id)
        )
        return result.scalars().first()

    async def get_by_slug(self, slug: str) -> models.Tenant | None:
        result = await self.session.execute(
            select(models.Tenant).where(models.Tenant.slug == slug)
        )
        return result.scalars().first()

    async def add(self, tenant: models.Tenant) -> models.Tenant:
        self.session.add(tenant)
        await self.session.flush()
        return tenant

    async def update(self, tenant_id: int, **fields) -> None:
        if not fields:
            return
        await self.session.execute(
            update(models.Tenant)
            .where(models.Tenant.id == tenant_id)
            .values(**fields)
        )


class UserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_email(self, email: str) -> models.User | None:
        result = await self.session.execute(
            select(models.User).where(models.User.email == email)
        )
        return result.scalars().first()

    async def list_for_tenant(self, tenant_id: int) -> Sequence[models.User]:
        result = await self.session.execute(
            select(models.User)
            .where(models.User.tenant_id == tenant_id)
            .order_by(models.User.created_at)
        )
        return result.scalars().all()

    async def add(self, user: models.User) -> models.User:
        self.session.add(user)
        await self.session.flush()
        return user

    async def update(self, user_id: int, **fields) -> None:
        if not fields:
            return
        await self.session.execute(
            update(models.User)
            .where(models.User.id == user_id)
            .values(**fields)
        )

    async def delete(self, user_id: int) -> None:
        await self.session.execute(
            delete(models.User).where(models.User.id == user_id)
        )

    async def update_password(self, user_id: int, password_hash: str) -> None:
        await self.session.execute(
            update(models.User)
            .where(models.User.id == user_id)
            .values(password_hash=password_hash)
        )


class WidgetRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_for_tenant(self, tenant_id: int) -> Sequence[models.Widget]:
        result = await self.session.execute(
            select(models.Widget)
            .where(models.Widget.tenant_id == tenant_id)
            .order_by(models.Widget.id)
        )
        return result.scalars().all()

    async def get_by_slug(self, slug: str) -> models.Widget | None:
        result = await self.session.execute(
            select(models.Widget).where(models.Widget.slug == slug)
        )
        return result.scalars().first()

    async def get(self, widget_id: int) -> models.Widget | None:
        result = await self.session.execute(
            select(models.Widget).where(models.Widget.id == widget_id)
        )
        return result.scalars().first()

    async def add(self, widget: models.Widget) -> models.Widget:
        self.session.add(widget)
        await self.session.flush()
        return widget

    async def update(self, widget_id: int, **fields) -> None:
        await self.session.execute(
            update(models.Widget)
            .where(models.Widget.id == widget_id)
            .values(**fields)
        )

    async def delete(self, widget_id: int) -> None:
        await self.session.execute(
            delete(models.Widget).where(models.Widget.id == widget_id)
        )


class WidgetAssetRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_versions(self, widget_id: int) -> list[models.WidgetAsset]:
        result = await self.session.execute(
            select(models.WidgetAsset)
            .where(models.WidgetAsset.widget_id == widget_id)
            .order_by(models.WidgetAsset.version.desc())
        )
        return list(result.scalars())

    async def get_latest(self, widget_id: int) -> models.WidgetAsset | None:
        versions = await self.list_versions(widget_id)
        return versions[0] if versions else None

    async def add_new_version(
        self,
        widget_id: int,
        html: Optional[str],
        css: Optional[str],
        js: Optional[str],
    ) -> models.WidgetAsset:
        result = await self.session.execute(
            select(func.coalesce(func.max(models.WidgetAsset.version), 0)).where(models.WidgetAsset.widget_id == widget_id)
        )
        current_version = result.scalar_one()
        asset = models.WidgetAsset(
            widget_id=widget_id,
            html=html,
            css=css,
            js=js,
            version=current_version + 1,
        )
        self.session.add(asset)
        await self.session.flush()
        return asset

    async def get_version(self, widget_id: int, version: int) -> models.WidgetAsset | None:
        result = await self.session.execute(
            select(models.WidgetAsset)
            .where(
                models.WidgetAsset.widget_id == widget_id,
                models.WidgetAsset.version == version,
            )
        )
        return result.scalars().first()



class WidgetBindingRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_for_widget(self, widget_id: int) -> Sequence[models.WidgetBinding]:
        result = await self.session.execute(
            select(models.WidgetBinding)
            .where(models.WidgetBinding.widget_id == widget_id)
            .order_by(models.WidgetBinding.id)
        )
        return result.scalars().all()

    async def add(self, widget_id: int, domain: str) -> models.WidgetBinding:
        binding = models.WidgetBinding(widget_id=widget_id, domain=domain)
        self.session.add(binding)
        await self.session.flush()
        return binding

    async def delete(self, binding_id: int) -> None:
        await self.session.execute(
            delete(models.WidgetBinding).where(models.WidgetBinding.id == binding_id)
        )


__all__ = [
    "TenantRepository",
    "UserRepository",
    "WidgetRepository",
    "WidgetAssetRepository",
    "WidgetBindingRepository",
]
