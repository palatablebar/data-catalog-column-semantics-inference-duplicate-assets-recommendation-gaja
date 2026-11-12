"""pytest 夹具：SQLite 内存库跑通 service 层全生命周期（生产库为 PostgreSQL JSONB/UUID）。

仅在测试编译/绑参期把 PG 专有类型替换为 SQLite 等价物，不修改任何生产模型定义。
"""
from __future__ import annotations

import sys
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.types import CHAR, JSON, TypeDecorator

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


# DDL 渲染：PG 专有类型在 SQLite 下的建表语句降级
@compiles(JSONB, "sqlite")
def _sqlite_jsonb_ddl(element, compiler, **kw):  # noqa: ANN001
    return "JSON"


class _SqliteUuid(TypeDecorator):
    """PG UUID 在 SQLite 下的替身：CHAR(32) 存储，入参/结果统一规整为 uuid.UUID。"""

    impl = CHAR
    cache_ok = True

    def load_dialect_impl(self, dialect):
        return dialect.type_descriptor(CHAR(32))

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if isinstance(value, str):
            return uuid.UUID(value).hex
        return value.hex

    def process_result_value(self, value, dialect):
        return uuid.UUID(value) if value is not None else None


@compiles(UUID, "sqlite")
def _sqlite_uuid_ddl(element, compiler, **kw):  # noqa: ANN001
    return "CHAR(32)"


def _make_engine():
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    # 运行时绑参/取值：方言级类型替换（DDL 仍由上方 @compiles 负责）
    engine.dialect.colspecs[UUID] = _SqliteUuid
    engine.dialect.colspecs[JSONB] = JSON
    return engine


@pytest.fixture()
def db_session():
    from src.models import Base  # 触发全部模型注册

    engine = _make_engine()
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, class_=Session)
    session = factory()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()
