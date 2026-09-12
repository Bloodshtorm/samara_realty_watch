import sqlite3

from scripts.repair_listing_quality import repair


def test_quality_repair_has_dry_run_backup_and_preserves_history(tmp_path):
    path = tmp_path / "realty.sqlite3"
    with sqlite3.connect(path) as conn:
        conn.execute("""CREATE TABLE listings (id TEXT,address_raw TEXT,address_normalized TEXT,
            district TEXT,title TEXT,description TEXT,features TEXT,
            latitude REAL,longitude REAL)""")
        conn.executemany(
            "INSERT INTO listings VALUES (?,?,?,?,?,?,?,?,?)",
            [
                (
                    "known",
                    "Самара, Ново-Садовая, 100",
                    None,
                    "самарский",
                    "3к",
                    None,
                    "{}",
                    53.22,
                    50.15,
                ),
                (
                    "missing",
                    "Самара, Ново-Садовая, 100",
                    None,
                    "самарский",
                    "3к",
                    "Ипотека не подходит",
                    "{}",
                    None,
                    None,
                ),
            ],
        )
        conn.execute("CREATE TABLE price_history (value INTEGER)")
        conn.execute("INSERT INTO price_history VALUES (8500000)")
    preview = repair(path)
    assert preview["coordinates_inferred"] == 1
    with sqlite3.connect(path) as conn:
        assert (
            conn.execute("SELECT latitude FROM listings WHERE id='missing'").fetchone()[0] is None
        )
    assert not list(tmp_path.glob("backups/*"))
    assert repair(path, apply=True) == preview
    assert len(list(tmp_path.glob("backups/*.sqlite3"))) == 1
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT value FROM price_history").fetchone()[0] == 8500000
        assert (
            conn.execute("SELECT latitude FROM listings WHERE id='missing'").fetchone()[0] == 53.22
        )
    assert repair(path) == {}
