"""MongoDB connection, indexes and GridFS file storage."""
from __future__ import annotations

from functools import lru_cache

import gridfs
from bson import ObjectId
from pymongo import ASCENDING, DESCENDING, MongoClient
from pymongo.database import Database

from core.config import secret


@lru_cache(maxsize=1)
def _client(uri: str) -> MongoClient:
    if uri.startswith("mongomock://"):
        import mongomock
        import mongomock.gridfs

        mongomock.gridfs.enable_gridfs_integration()
        return mongomock.MongoClient()
    return MongoClient(uri, serverSelectionTimeoutMS=10000, tz_aware=True)


_db_override: Database | None = None


def set_db_for_tests(db: Database | None) -> None:
    global _db_override
    _db_override = db


def has_db_override() -> bool:
    return _db_override is not None


def get_db() -> Database:
    if _db_override is not None:
        return _db_override
    uri = secret("MONGO_URI") or secret("MONGODB_URI")  # accept both common names
    if not uri:
        raise RuntimeError("MONGO_URI is not configured. Add it to .streamlit/secrets.toml.")
    db = _client(uri)[secret("MONGO_DB", "ubg_events")]
    _ensure_indexes_once(db)
    return db


_indexed: set[str] = set()


def _ensure_indexes_once(db: Database) -> None:
    if db.name in _indexed:
        return
    ensure_indexes(db)
    _indexed.add(db.name)


def ensure_indexes(db: Database) -> None:
    db.people.create_index([("event_id", ASCENDING), ("status", ASCENDING)])
    db.people.create_index([("event_id", ASCENDING), ("pid", ASCENDING)])
    db.people.create_index([("event_id", ASCENDING), ("mobile", ASCENDING)])
    db.attendance.create_index([("event_id", ASCENDING), ("scanned_at", DESCENDING)])


def fs(db: Database | None = None) -> gridfs.GridFS:
    return gridfs.GridFS(db if db is not None else get_db())


def put_file(data: bytes, filename: str, content_type: str, db: Database | None = None, **meta) -> ObjectId:
    return fs(db).put(data, filename=filename, contentType=content_type, metadata=meta)


def get_file(file_id, db: Database | None = None) -> bytes | None:
    if not file_id:
        return None
    try:
        return fs(db).get(ObjectId(file_id) if isinstance(file_id, str) else file_id).read()
    except gridfs.errors.NoFile:
        return None


def delete_file(file_id, db: Database | None = None) -> None:
    if file_id:
        try:
            fs(db).delete(ObjectId(file_id) if isinstance(file_id, str) else file_id)
        except gridfs.errors.NoFile:
            pass
