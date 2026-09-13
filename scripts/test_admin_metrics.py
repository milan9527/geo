#!/usr/bin/env python3
"""Regression coverage for complete statistics beyond the Data API's 1 MB limit."""

from contextlib import contextmanager
import importlib
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import uuid

import psycopg
from psycopg.rows import dict_row

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.analytics import empty_hll, encode_hll, hll_add
from backend.metrics import load_metrics_rows


class BufferedCursor:
    def __init__(self, rows):
        self.rows = rows

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def __iter__(self):
        return iter(self.rows)


class SizeLimitedConnection:
    def __init__(self, connection):
        self.connection = connection
        self.response_sizes = []

    def execute(self, sql, parameters=None):
        cursor = self.connection.execute(sql, parameters)
        rows = cursor.fetchall() if cursor.description else []
        size = len(json.dumps(rows, ensure_ascii=False, default=str).encode())
        if size > 1024 * 1024:
            raise RuntimeError("The result exceeds the size limit 1 MB.")
        self.response_sizes.append(size)
        return BufferedCursor(rows)


@unittest.skipUnless(os.environ.get("METRICS_TEST_DATABASE_URL"), "local PostgreSQL URL not set")
class AdminMetricsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.url = os.environ["METRICS_TEST_DATABASE_URL"]
        cls.schema = "metrics_test_" + uuid.uuid4().hex
        with psycopg.connect(cls.url, autocommit=True) as conn:
            conn.execute(f'CREATE SCHEMA "{cls.schema}"')
        with cls.connect() as conn:
            conn.execute("""
                CREATE TABLE traffic_hourly(
                    bucket_hour TEXT, visitor_type TEXT, agent_name TEXT,
                    path_group TEXT, article_slug TEXT, access_variant TEXT,
                    requests BIGINT, successful_requests BIGINT, bytes_sent BIGINT,
                    visitor_hll TEXT,
                    PRIMARY KEY(bucket_hour,visitor_type,agent_name,path_group,article_slug,access_variant)
                );
                CREATE TABLE articles(id BIGINT PRIMARY KEY,slug TEXT,title TEXT,status TEXT);
                CREATE TABLE crawler_agents(status TEXT,pages_today BIGINT);
                CREATE TABLE traffic_events(
                    id BIGINT PRIMARY KEY,event_type TEXT,visitor_type TEXT,agent_name TEXT,
                    article_id BIGINT,occurred_at TEXT,metadata TEXT
                );
                INSERT INTO articles VALUES(1,'retained-page','已发布研究','published');
                INSERT INTO crawler_agents VALUES('idle',10);
            """)
            traffic = []
            for day, count in [("2026-08-28", 129), ("2026-09-05", 800), ("2026-09-14", 1)]:
                for index in range(count):
                    visitor = "agent" if index % 2 else "human"
                    registers = empty_hll()
                    hll_add(registers, f"{visitor}-{index % 37}".encode())
                    traffic.append((
                        day + "T12:00:00+00:00", visitor, "Bingbot" if index % 2 else "",
                        "article", f"page-{index:05d}", "B" if index % 3 else "A",
                        index % 5 + 1, 1, 100, encode_hll(registers),
                    ))
            conn.cursor().executemany(
                "INSERT INTO traffic_hourly VALUES(" + ",".join(["%s"] * 10) + ")", traffic,
            )
            events = []
            for day, count in [("2026-08-28", 131), ("2026-09-05", 270), ("2026-09-14", 1)]:
                for index in range(count):
                    events.append((
                        len(events) + 1, "x402_payment" if index % 3 == 0 else "citation",
                        "agent", "Bingbot", 1, day + "T12:00:00+00:00",
                        json.dumps({"amountUsd": .002, "transactionHash": f"tx-{len(events)}"}),
                    ))
            conn.cursor().executemany(
                "INSERT INTO traffic_events VALUES(" + ",".join(["%s"] * 7) + ")", events,
            )
        with patch("backend.database.init_db"):
            cls.app = importlib.import_module("backend.app")

    @classmethod
    def tearDownClass(cls):
        with psycopg.connect(cls.url, autocommit=True) as conn:
            conn.execute(f'DROP SCHEMA "{cls.schema}" CASCADE')

    @classmethod
    def connect(cls):
        return psycopg.connect(cls.url, options=f"-c search_path={cls.schema}", row_factory=dict_row)

    @staticmethod
    def unpaged(conn, start, end):
        traffic = conn.execute(
            """SELECT * FROM traffic_hourly WHERE bucket_hour >= %s AND bucket_hour < %s
               ORDER BY bucket_hour,visitor_type,agent_name,path_group,article_slug,access_variant""",
            (start, end),
        ).fetchall()
        events = conn.execute(
            """SELECT te.*,a.slug article_slug,a.title article_title FROM traffic_events te
               LEFT JOIN articles a ON a.id=te.article_id
               WHERE te.occurred_at >= %s AND te.occurred_at < %s ORDER BY te.occurred_at,te.id""",
            (start, end),
        ).fetchall()
        return traffic, events

    def test_original_unpaged_query_exceeds_limit(self):
        with self.connect() as conn:
            with self.assertRaisesRegex(RuntimeError, "size limit 1 MB"):
                self.unpaged(SizeLimitedConnection(conn), "2026-08-19", "2026-09-14")

    def test_pages_preserve_all_rows_and_timestamp_ties(self):
        with self.connect() as conn:
            expected = self.unpaged(conn, "2026-08-19", "2026-09-14")
        with self.connect() as conn:
            limited = SizeLimitedConnection(conn)
            actual = load_metrics_rows(limited, "2026-08-19", "2026-09-14")
        self.assertEqual(actual, expected)
        self.assertEqual([len(rows) for rows in actual], [929, 401])
        self.assertLess(max(limited.response_sizes), 300000)

    def test_all_dashboard_values_match_unpaged_reference(self):
        query = {"start": ["2026-09-01"], "end": ["2026-09-13"]}
        handler = self.app.ApiHandler.__new__(self.app.ApiHandler)
        results = []
        handler._json = lambda payload, *args, **kwargs: results.append(payload)
        with patch.object(self.app, "connection", self.connect), \
             patch.object(self.app, "load_metrics_rows", self.unpaged):
            handler._admin_metrics(query)

        @contextmanager
        def limited_connection():
            with self.connect() as conn:
                yield SizeLimitedConnection(conn)

        with patch.object(self.app, "connection", limited_connection):
            handler._admin_metrics(query)
        self.assertEqual(results[0], results[1])
        self.assertEqual(results[1]["summary"]["humanRequests"], 1200)
        self.assertEqual(results[1]["summary"]["agentRequests"], 1200)
        self.assertEqual(results[1]["summary"]["payments"], 90)
        self.assertEqual(len(results[1]["abTest"]["recentEvents"]), 20)

    def test_empty_range_returns_empty_lists(self):
        with self.connect() as conn:
            self.assertEqual(load_metrics_rows(conn, "2025-01-01", "2025-01-02"), ([], []))

    def test_ingestion_during_pagination_cannot_change_snapshot(self):
        with self.connect() as conn:
            original = SizeLimitedConnection(conn)
            first_page = True

            class ConcurrentInsert:
                def execute(inner, sql, parameters=None):
                    nonlocal first_page
                    result = original.execute(sql, parameters)
                    if "FROM traffic_hourly" in sql and first_page:
                        first_page = False
                        with self.connect() as writer:
                            writer.execute(
                                """INSERT INTO traffic_hourly
                                   SELECT bucket_hour,visitor_type,agent_name,path_group,
                                          'new-concurrent-row',access_variant,999,0,0,visitor_hll
                                   FROM traffic_hourly LIMIT 1"""
                            )
                    return result

            try:
                traffic, _ = load_metrics_rows(ConcurrentInsert(), "2026-08-19", "2026-09-14")
                self.assertEqual(len(traffic), 929)
                self.assertNotIn("new-concurrent-row", [r["article_slug"] for r in traffic])
            finally:
                with self.connect() as writer:
                    writer.execute("DELETE FROM traffic_hourly WHERE article_slug='new-concurrent-row'")


if __name__ == "__main__":
    unittest.main()
