"""Explicit Docker integration check, separate from the always-on PostgreSQL suite."""

import os
import unittest


@unittest.skipUnless(
    os.getenv("NABUCITE_TEST_CONTAINERS") == "1", "opt-in container migration check"
)
class ContainerSchema(unittest.TestCase):
    def test_empty_postgres_upgrade(self):
        import subprocess
        import sys
        from pathlib import Path

        from testcontainers.postgres import PostgresContainer

        with PostgresContainer("postgres:17") as pg:
            env = os.environ | {
                "POSTGRES_HOST": pg.get_container_host_ip(),
                "POSTGRES_PORT": str(pg.get_exposed_port(5432)),
                "POSTGRES_USER": pg.username,
                "POSTGRES_PASSWORD": pg.password,
                "POSTGRES_DB": pg.dbname,
            }
            subprocess.run(
                [sys.executable, "-m", "alembic", "upgrade", "head"],
                cwd=Path(__file__).parents[1],
                env=env,
                check=True,
            )
