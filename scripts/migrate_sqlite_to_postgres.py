from __future__ import annotations

import argparse
import asyncio
import os
import subprocess
import sys
from pathlib import Path
from typing import Iterable

from sqlalchemy import MetaData, Table, create_engine, inspect, select, text

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from client_bot.domain.models import Base


def _sqlite_url_from_path(path: Path) -> str:
    return f"sqlite+aiosqlite:///{path.resolve().as_posix()}"


def _normalize_legacy_sqlite(source_db: Path) -> None:
    env = os.environ.copy()
    env["DATABASE_URL"] = _sqlite_url_from_path(source_db)
    code = """
import asyncio
from client_bot.services.seed import init_db

asyncio.run(init_db())
"""
    subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(REPO_ROOT),
        env=env,
        check=True,
    )


def _copy_rows(
    source_conn,
    target_conn,
    source_table: Table,
    target_table: Table,
    common_columns: list[str],
    chunk_size: int = 1000,
) -> int:
    if not common_columns:
        return 0

    source_columns = [source_table.c[name] for name in common_columns]
    rows = source_conn.execute(select(*source_columns)).mappings().all()
    if not rows:
        return 0

    copied = 0
    for start in range(0, len(rows), chunk_size):
        batch = [dict(row) for row in rows[start : start + chunk_size]]
        target_conn.execute(target_table.insert(), batch)
        copied += len(batch)
    return copied


def _reset_postgres_sequences(target_conn, table: Table) -> None:
    if target_conn.engine.dialect.name != "postgresql":
        return

    pk_columns = list(table.primary_key.columns)
    if len(pk_columns) != 1:
        return

    pk_column = pk_columns[0]
    if pk_column.name != "id":
        return

    sequence_name = target_conn.execute(
        text("SELECT pg_get_serial_sequence(:table_name, 'id')"),
        {"table_name": table.name},
    ).scalar_one_or_none()
    if not sequence_name:
        return

    max_id = target_conn.execute(
        text(f"SELECT MAX(id) FROM {table.name}")
    ).scalar_one_or_none()
    if max_id is None:
        return

    target_conn.execute(
        text(f"SELECT setval(:sequence_name, :max_id, true)"),
        {
            "sequence_name": sequence_name,
            "max_id": int(max_id),
        },
    )


def migrate_sqlite_to_postgres(
    source_db: Path, target_url: str, normalize_source: bool
) -> None:
    if normalize_source:
        _normalize_legacy_sqlite(source_db)

    source_engine = create_engine(
        f"sqlite:///{source_db.resolve().as_posix()}",
        future=True,
    )
    target_engine = create_engine(target_url, future=True)

    try:
        Base.metadata.create_all(target_engine)

        source_inspector = inspect(source_engine)
        target_inspector = inspect(target_engine)
        source_tables = set(source_inspector.get_table_names())
        target_tables = set(target_inspector.get_table_names())

        with (
            source_engine.connect() as source_conn,
            target_engine.begin() as target_conn,
        ):
            for table in Base.metadata.sorted_tables:
                if table.name not in source_tables or table.name not in target_tables:
                    continue

                source_table = Table(
                    table.name, MetaData(), autoload_with=source_engine
                )
                target_table = Table(
                    table.name, MetaData(), autoload_with=target_engine
                )
                common_columns = [
                    column.name
                    for column in target_table.columns
                    if column.name in source_table.columns
                ]

                copied = _copy_rows(
                    source_conn,
                    target_conn,
                    source_table,
                    target_table,
                    common_columns,
                )
                if copied:
                    _reset_postgres_sequences(target_conn, target_table)
                    print(f"{table.name}: copied {copied} rows")

        print("Migration completed successfully.")
    finally:
        source_engine.dispose()
        target_engine.dispose()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Migrate legacy SQLite data into the current Postgres schema.",
    )
    parser.add_argument(
        "--source-db",
        type=Path,
        default=REPO_ROOT / "data" / "esas.db",
        help="Path to the legacy SQLite database file.",
    )
    parser.add_argument(
        "--target-url",
        required=True,
        help="SQLAlchemy URL for the target Postgres database.",
    )
    parser.add_argument(
        "--skip-normalize",
        action="store_true",
        help="Skip running the current SQLite inline migrations before copying.",
    )
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    migrate_sqlite_to_postgres(
        source_db=args.source_db,
        target_url=args.target_url,
        normalize_source=not args.skip_normalize,
    )


if __name__ == "__main__":
    main()
