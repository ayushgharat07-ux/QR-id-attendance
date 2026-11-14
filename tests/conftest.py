import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QR_SECRET", "test-secret")
os.environ.setdefault("BOOTSTRAP_ADMIN_EMAIL", "owner@example.com")

SAMPLES = sorted(f for f in (ROOT / "samples").glob("*")
                 if f.suffix.lower() in (".jpg", ".jpeg", ".png", ".pdf")) if (ROOT / "samples").exists() else []


@pytest.fixture
def db():
    import mongomock
    import mongomock.gridfs

    from core import db as dbm
    from core import repo

    mongomock.gridfs.enable_gridfs_integration()
    database = mongomock.MongoClient().db
    dbm.ensure_indexes(database)
    dbm.set_db_for_tests(database)
    repo.ensure_default_event(database)
    yield database
    dbm.set_db_for_tests(None)


@pytest.fixture
def event(db):
    from core import repo

    return repo.get_event(db, "thane-2026")
