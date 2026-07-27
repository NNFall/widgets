from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.base import Base
from app.db.models import Tenant, User
from app.saas.repositories import ProjectRepository


@pytest.mark.asyncio
async def test_project_repository_scopes_reads_and_updates_to_owner(tmp_path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'projects.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    try:
        async with factory() as database, database.begin():
            database.add_all(
                [
                    Tenant(id=1, name="Alpha", slug="alpha"),
                    Tenant(id=2, name="Beta", slug="beta"),
                    User(id=10, tenant_id=1, email="owner@example.com"),
                    User(id=11, tenant_id=1, email="other@example.com"),
                    User(id=20, tenant_id=2, email="beta@example.com"),
                ]
            )

        async with factory() as database, database.begin():
            repository = ProjectRepository(database)
            with pytest.raises(ValueError, match="owner does not belong to tenant"):
                await repository.create(
                    tenant_id=2,
                    owner_user_id=10,
                    source_url="https://wrong-tenant.example.com/",
                    brief=None,
                )
            owned = await repository.create(
                tenant_id=1,
                owner_user_id=10,
                source_url="https://example.com/",
                brief="Build a support widget",
            )
            await repository.create(
                tenant_id=1,
                owner_user_id=11,
                source_url="https://other.example.com/",
                brief=None,
            )
            await repository.create(
                tenant_id=2,
                owner_user_id=20,
                source_url="https://beta.example.com/",
                brief=None,
            )

        async with factory() as database, database.begin():
            repository = ProjectRepository(database)
            restored = await repository.get_owned(
                owned.id, tenant_id=1, owner_user_id=10
            )
            assert restored is not None
            assert restored.id == owned.id
            assert (
                await repository.get_owned(
                    owned.id, tenant_id=1, owner_user_id=11
                )
                is None
            )
            assert [
                project.id
                for project in await repository.list_owned(
                    tenant_id=1, owner_user_id=10
                )
            ] == [owned.id]

            run_id = uuid4()
            updated = await repository.set_active_run(
                owned.id,
                tenant_id=1,
                owner_user_id=10,
                run_id=run_id,
                revision=3,
                status="completed",
            )
            assert updated is not None
            assert updated.active_run_id == run_id
            assert updated.active_revision == 3
            assert updated.status == "completed"
            assert (
                await repository.set_active_run(
                    owned.id,
                    tenant_id=2,
                    owner_user_id=20,
                    run_id=uuid4(),
                    revision=None,
                    status="running",
                )
                is None
            )
    finally:
        await engine.dispose()
