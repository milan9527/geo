#!/usr/bin/env python3
"""Check attribution boundaries, diagnostic filtering, and event validation."""
import importlib
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.analytics import identify_visitor, is_diagnostic
from backend.growth import clean_metadata, growth_summary


class GrowthTests(unittest.TestCase):
    def metadata(self, **extra):
        return {"sessionId": "session-0123456789", "eventId": "event-01234567890",
                "path": "/article/real-page", "source": "juejin", "medium": "community",
                "campaign": "data_api_1mb", **extra}

    def event(self, kind, **extra):
        return {"event_type": kind, "visitor_type": "human", "occurred_at": "2026-09-20T12:00:00Z",
                "metadata": json.dumps(self.metadata(**extra))}

    def test_attribution_deduplicates_events_and_counts_sessions(self):
        page = self.event("page_view")
        engaged = self.event("engaged_read", eventId="engaged-0123456789")
        returning = self.event("rss_click", sessionId="return-0123456789",
                               eventId="rssclick-0123456789", returning=True)
        bot = {**self.event("page_view", eventId="bot-event-0123456789"), "visitor_type": "agent"}
        old = {**self.event("page_view", eventId="old-event-0123456789"), "occurred_at": "2026-09-19"}
        result = growth_summary([page, page, engaged, returning, bot, old], "2026-09-20", "2026-09-20")
        self.assertEqual(result["summary"], {"sessions": 2, "returningSessions": 1, "pageViews": 1,
                                             "engagedReads": 1, "rssClicks": 1, "relatedClicks": 0})
        self.assertEqual(result["channels"][0]["campaign"], "data_api_1mb")

    def test_metadata_drops_sensitive_and_unbounded_fields(self):
        cleaned = clean_metadata(self.metadata(referrerHost="example.com/path?token=secret",
                                               email="secret@example.com", ip="127.0.0.1",
                                               source="<script>" * 100, returning="false"))
        self.assertEqual(cleaned["referrerHost"], "example.com")
        self.assertNotIn("email", cleaned)
        self.assertNotIn("ip", cleaned)
        self.assertLessEqual(len(cleaned["source"]), 80)
        self.assertFalse(cleaned["returning"])
        with self.assertRaises(ValueError):
            clean_metadata(self.metadata(path="/article/x?token=secret"))

    def test_public_endpoint_ignores_diagnostics_and_rejects_other_origins(self):
        with patch("backend.database.init_db"):
            app = importlib.import_module("backend.app")
        handler = object.__new__(app.ApiHandler)
        responses = []
        handler._json = lambda payload, status=200: responses.append((payload, status))
        with patch.object(app, "connection") as db:
            handler.headers = {"User-Agent": "Aperture-Growth-Verification"}
            handler._track({"eventType": "page_view"})
            self.assertTrue(responses[-1][0]["ignored"])
            handler.headers = {"User-Agent": "Mozilla/5.0", "Origin": "https://unrelated.example"}
            handler._track({"eventType": "page_view", "metadata": self.metadata()})
            self.assertEqual(responses[-1][1], 403)
            handler.headers = {"User-Agent": "Mozilla/5.0"}
            handler._track({"eventType": "engaged_read", "metadata": self.metadata()})
            self.assertEqual(responses[-1][1], 400)
            db.assert_not_called()

    def test_bots_and_diagnostic_clients_are_not_human(self):
        for ua in ("curl/8.0", "python-requests/2", "HeadlessChrome", "ApertureSEOCheck", ""):
            self.assertEqual(identify_visitor(ua)[0], "agent")
        self.assertTrue(is_diagnostic("Aperture-Legacy-Verification"))
        self.assertEqual(identify_visitor("Mozilla/5.0 Chrome/131 Safari/537.36")[0], "human")
        self.assertEqual(identify_visitor("Googlebot")[1], "Google Search Crawler")

    def test_redirect_query_is_preserved_without_cross_campaign_caching(self):
        from contextlib import nullcontext
        with patch("backend.database.init_db"):
            app = importlib.import_module("backend.app")
        handler = object.__new__(app.ApiHandler)
        responses = []
        handler._text = lambda *args, **kwargs: responses.append(kwargs["extra_headers"])
        with patch.object(app, "connection", return_value=nullcontext(None)), \
             patch.object(app, "redirect_target", return_value="retained"):
            for campaign in ("juejin", "zhihu"):
                handler.path = f"/article/old?utm_source={campaign}"
                self.assertTrue(handler._redirect_legacy_article("old", "/article/"))
                self.assertTrue(responses[-1]["Location"].endswith(f"/article/retained?utm_source={campaign}"))
                self.assertIn("no-store", responses[-1]["Cache-Control"])


if __name__ == "__main__":
    unittest.main()
