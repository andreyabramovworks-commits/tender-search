import os

from sqlalchemy import create_engine, inspect

engine = create_engine(os.environ["DATABASE_URL"])
try:
    assert {"tenders", "tender_versions", "runs", "query_runs", "alembic_version"} <= set(
        inspect(engine).get_table_names()
    )
    print("PostgreSQL schema is ready")
finally:
    engine.dispose()
