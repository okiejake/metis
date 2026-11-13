"""Opening the database file is most of what a page used to cost: every query
opened and closed it, up to a hundred times a page. Metis keeps one database
open per file and hands each query a light handle on it.

All data here is synthetic.
"""

from concurrent.futures import ThreadPoolExecutor

import duckdb


def test_queries_reuse_one_open_database(db, monkeypatch):
    opened = []
    real_connect = duckdb.connect
    monkeypatch.setattr(duckdb, "connect", lambda *a, **k: opened.append(a) or real_connect(*a, **k))

    for _ in range(5):
        db.fetch_one("SELECT COUNT(*) FROM users")

    assert opened == []


def test_a_write_is_seen_by_the_next_query(db, user_id):
    db.execute("UPDATE users SET display_name = 'Renamed' WHERE id = ?", [user_id])

    assert db.fetch_one("SELECT display_name FROM users WHERE id = ?", [user_id])[0] == "Renamed"


def test_queries_from_many_threads_at_once(db, user_id):
    users = db.fetch_one("SELECT COUNT(*) FROM users")[0]

    def work(n):
        db.execute("UPDATE users SET display_name = ? WHERE id = ?", [f"Name {n}", user_id])
        return db.fetch_one("SELECT COUNT(*) FROM users")[0]

    with ThreadPoolExecutor(max_workers=8) as pool:
        assert set(pool.map(work, range(80))) == {users}


def test_another_file_gets_its_own_database(db, user_id, tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "other.duckdb"))
    db.init_db()

    assert db.fetch_one("SELECT COUNT(*) FROM users WHERE slug = 'testuser'")[0] == 0


def test_closing_releases_the_file_and_the_next_query_reopens_it(db, user_id):
    db.close_database()

    # Opening read-only fails while the same process holds the file read-write.
    with duckdb.connect(db.DB_PATH, read_only=True) as other:
        other.execute("SELECT 1")
    assert db.fetch_one("SELECT COUNT(*) FROM users WHERE id = ?", [user_id])[0] == 1
