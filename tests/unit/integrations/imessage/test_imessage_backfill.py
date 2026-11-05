"""The iMessage catch-up on a chat.db whose ROWIDs are not in date order: Messages in iCloud
downloads a fresh library's history in chunks (newest first, old, recent again), so the last
ROWID from before the horizon can sit near the end of the table. The cursor must start just
before the first in-horizon row and the reads must skip the pre-horizon rows between."""
import sqlite3
import time
from unittest.mock import patch

import pytest

from integrations.imessage.IMessageIntegration import IMessageIntegration, APPLE_EPOCH_OFFSET

NOW = time.time()
HORIZON = NOW - 90 * 86400
DAY = 86400


def apple(unix_ts: float) -> int:
    return int((unix_ts - APPLE_EPOCH_OFFSET) * 1_000_000_000)


def make_chat_db(path, rows):
    """`rows`: (rowid, unix_ts, text) incoming messages from one handle; the tables the
    feed's queries join, nothing more."""
    with sqlite3.connect(path) as conn:
        conn.executescript("""
            CREATE TABLE message (ROWID INTEGER PRIMARY KEY, text TEXT, attributedBody BLOB, date INTEGER,
                                  is_from_me INTEGER, handle_id INTEGER, is_system_message INTEGER DEFAULT 0,
                                  schedule_type INTEGER DEFAULT 0, schedule_state INTEGER DEFAULT 0);
            CREATE INDEX message_idx_date ON message(date);
            CREATE TABLE handle (ROWID INTEGER PRIMARY KEY, id TEXT, service TEXT);
            CREATE TABLE chat (ROWID INTEGER PRIMARY KEY, guid TEXT, display_name TEXT);
            CREATE TABLE chat_message_join (chat_id INTEGER, message_id INTEGER);
            CREATE TABLE chat_handle_join (chat_id INTEGER, handle_id INTEGER);
            CREATE TABLE attachment (ROWID INTEGER PRIMARY KEY, filename TEXT, mime_type TEXT);
            CREATE TABLE message_attachment_join (message_id INTEGER, attachment_id INTEGER);
            INSERT INTO handle VALUES (1, '+46701740605', 'iMessage');
            INSERT INTO chat VALUES (1, 'iMessage;-;+46701740605', NULL);
        """)
        for rowid, ts, text in rows:
            conn.execute("INSERT INTO message (ROWID, text, date, is_from_me, handle_id) VALUES (?, ?, ?, 0, 1)",
                         (rowid, text, apple(ts)))
            conn.execute("INSERT INTO chat_message_join VALUES (1, ?)", (rowid,))


# ROWID order is nothing like date order: a recent chunk first, then years-old ones, then
# recent again, and an old chunk at the very end — the rows a fresh library syncs down.
ROWS = [
    (1, NOW - 10 * DAY, "recent, first chunk"),
    (2, NOW - 9 * DAY, "recent, first chunk 2"),
    (3, HORIZON - 400 * DAY, "old"),
    (4, HORIZON - 399 * DAY, "old 2"),
    (5, HORIZON + 5 * DAY, "just inside the horizon"),
    (6, HORIZON - 1 * DAY, "just before the horizon"),
    (7, NOW - 2 * DAY, "recent again"),
    (8, HORIZON - 2000 * DAY, "old, written last"),
]
IN_HORIZON = {text for _, ts, text in ROWS if ts >= HORIZON}


@pytest.fixture
def feed(tmp_path):
    db = tmp_path / "chat.db"
    make_chat_db(db, ROWS)
    IMessageIntegration.reset_instance()
    m = IMessageIntegration()
    m._contact_cache = {"+46701740605": "Alice"}
    m._contact_cache_time = 1e18
    with patch("integrations.imessage.IMessageIntegration.CHAT_DB_PATH", str(db)):
        yield m, db
    IMessageIntegration.reset_instance()


def catch_up(m, cursor):
    texts = []
    while True:
        batch = m.fetch(cursor, backfill=True)
        texts += [e.content.split(": ", 1)[1] for e in batch.entries]
        cursor = batch.cursor
        if batch.exhausted:
            return texts, cursor


@pytest.mark.unit
class TestOutOfOrderRowids:
    def test_the_catch_up_reads_every_in_horizon_row_and_no_older_one(self, feed):
        m, _ = feed
        cursor = m.initial_cursor(HORIZON)
        assert cursor == "0"    # ROWID 1 is inside the horizon, so the catch-up starts before it
        texts, cursor = catch_up(m, cursor)
        assert set(texts) == IN_HORIZON
        assert cursor == "7"    # the old row written last is never read; the date test keeps it out of every poll

    def test_remaining_counts_what_fetch_will_read(self, feed):
        m, _ = feed
        cursor = m.initial_cursor(HORIZON)
        assert m.remaining(cursor) == len(IN_HORIZON)
        _, cursor = catch_up(m, cursor)
        assert m.remaining(cursor) == 0

    def test_an_old_row_synced_down_late_is_not_a_fresh_message(self, feed):
        m, db = feed
        _, cursor = catch_up(m, m.initial_cursor(HORIZON))
        with sqlite3.connect(db) as conn:
            conn.execute("INSERT INTO message (ROWID, text, date, is_from_me, handle_id) VALUES (9, ?, ?, 0, 1)",
                         ("a 2013 message iCloud wrote today", apple(HORIZON - 3000 * DAY)))
            conn.execute("INSERT INTO message (ROWID, text, date, is_from_me, handle_id) VALUES (10, ?, ?, 0, 1)",
                         ("a real new message", apple(NOW)))
            conn.execute("INSERT INTO chat_message_join VALUES (1, 9), (1, 10)")
        batch = m.fetch(cursor, backfill=False)
        assert [e.content.split(": ", 1)[1] for e in batch.entries] == ["a real new message"]
        assert batch.cursor == "10"

    def test_a_horizon_past_every_row_starts_at_the_end_of_the_table(self, feed):
        m, _ = feed
        assert m.initial_cursor(NOW + DAY) == "8"
        assert m.remaining("8") == 0

    def test_without_a_horizon_every_row_is_read(self, feed):
        m, _ = feed
        m.horizon_ts = None
        texts, _ = catch_up(m, "0")
        assert len(texts) == len(ROWS)
