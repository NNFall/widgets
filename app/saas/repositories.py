from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import User
from app.saas.models import Project


class ProjectRepository:
    """Tenant- and owner-scoped persistence used by the builder API."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self,
        *,
        tenant_id: int,
        owner_user_id: int,
        source_url: str,
        brief: str | None,
    ) -> Project:
        owner = await self.session.execute(
            select(User.id).where(
                User.id == owner_user_id,
                User.tenant_id == tenant_id,
            )
        )
        if owner.scalar_one_or_none() is None:
            raise ValueError("project owner does not belong to tenant")
        project = Project(
            tenant_id=tenant_id,
            owner_user_id=owner_user_id,
            source_url=source_url,
            brief=brief,
        )
        self.session.add(project)
        await self.session.flush()
        return project

    async def get_owned(
        self,
        project_id: UUID,
        *,
        tenant_id: int,
        owner_user_id: int,
    ) -> Project | None:
        result = await self.session.execute(
            select(Project).where(
                Project.id == project_id,
                Project.tenant_id == tenant_id,
                Project.owner_user_id == owner_user_id,
            )
        )
        return result.scalar_one_or_none()

    async def list_owned(
        self,
        *,
        tenant_id: int,
        owner_user_id: int,
    ) -> Sequence[Project]:
        result = await self.session.execute(
            select(Project)
            .where(
                Project.tenant_id == tenant_id,
                Project.owner_user_id == owner_user_id,
            )
            .order_by(Project.created_at.desc(), Project.id)
        )
        return result.scalars().all()

    async def set_active_run(
        self,
        project_id: UUID,
        *,
        tenant_id: int,
        owner_user_id: int,
        run_id: UUID,
        revision: int | None,
        status: str,
    ) -> Project | None:
        result = await self.session.execute(
            update(Project)
            .where(
                Project.id == project_id,
                Project.tenant_id == tenant_id,
                Project.owner_user_id == owner_user_id,
            )
            .values(
                active_run_id=run_id,
                active_revision=revision,
                status=status,
            )
            .returning(Project)
        )
        return result.scalar_one_or_none()


__all__ = ["ProjectRepository"]
