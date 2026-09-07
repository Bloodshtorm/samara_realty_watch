import importlib

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text


def test_sqlite_upgrade_preserves_rows_indexes_and_foreign_keys(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'migration.sqlite3'}")
    migration = importlib.import_module("migrations.versions.0004_apartment_groups")
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE listings (id CHAR(32) PRIMARY KEY, source TEXT, "
                "source_listing_id TEXT, UNIQUE(source, source_listing_id))"
            )
        )
        connection.execute(text("CREATE INDEX ix_listings_source ON listings(source)"))
        connection.execute(
            text(
                "CREATE TABLE listing_links (id CHAR(32) PRIMARY KEY, "
                "listing_id_a CHAR(32) REFERENCES listings(id), "
                "listing_id_b CHAR(32) REFERENCES listings(id), match_type TEXT, "
                "UNIQUE(listing_id_a, listing_id_b, match_type))"
            )
        )
        connection.execute(
            text(
                "CREATE TABLE listing_observations (id INTEGER PRIMARY KEY, "
                "listing_id CHAR(32) REFERENCES listings(id))"
            )
        )
        connection.execute(
            text("INSERT INTO listings VALUES ('00000000000000000000000000000001', 'n1', '123')")
        )
        connection.execute(
            text("INSERT INTO listing_observations VALUES (1, '00000000000000000000000000000001')")
        )
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
        assert (
            connection.execute(text("SELECT source_listing_id FROM listings")).scalar_one() == "123"
        )
        assert not connection.execute(text("PRAGMA foreign_key_check")).all()
        assert "ix_listings_source" in {
            i["name"] for i in inspect(connection).get_indexes("listings")
        }
        assert "group_id" in {col["name"] for col in inspect(connection).get_columns("listings")}
        with Operations.context(MigrationContext.configure(connection)):
            migration.downgrade()
        assert (
            connection.execute(text("SELECT count(*) FROM listing_observations")).scalar_one() == 1
        )
        assert "group_id" not in {
            col["name"] for col in inspect(connection).get_columns("listings")
        }
    engine.dispose()
