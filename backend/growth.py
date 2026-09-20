"""Bounded, anonymous browser-event attribution; separate from crawler counts."""

import json
import re
from collections import defaultdict
from urllib.parse import urlsplit

GROWTH_EVENTS = {"page_view", "engaged_read", "rss_click", "related_click"}


def clean_metadata(value):
    if not isinstance(value, dict):
        raise ValueError("metadata must be an object")
    result = {}
    for key in ("sessionId", "eventId"):
        item = str(value.get(key, ""))
        if not re.fullmatch(r"[a-zA-Z0-9-]{16,64}", item):
            raise ValueError(f"Invalid {key}")
        result[key] = item
    path = str(value.get("path", ""))
    if not re.fullmatch(r"/(?:[a-zA-Z0-9_/-]{0,240})", path):
        raise ValueError("Invalid page path")
    result["path"] = path
    for key in ("source", "medium", "campaign"):
        result[key] = re.sub(r"[^a-zA-Z0-9_.-]", "", str(value.get(key, "")))[:80].lower()
    result["source"] = result["source"] or "direct_or_unknown"
    result["medium"] = result["medium"] or "none"
    # Persist the source hostname only; never a referrer URL, query, or IP.
    try:
        host = urlsplit("https://" + str(value.get("referrerHost", ""))).hostname or ""
        result["referrerHost"] = host[:120] if re.fullmatch(r"[a-zA-Z0-9.-]+", host) else ""
    except ValueError:
        result["referrerHost"] = ""
    result["returning"] = value.get("returning") is True
    return result


def growth_summary(events, start, end):
    def empty():
        return {"sessions": set(), "returningSessions": set(), "pageViews": 0,
                "engagedReads": 0, "rssClicks": 0, "relatedClicks": 0}

    total = empty()
    channels = defaultdict(empty)
    seen = set()
    for event in events:
        kind = event["event_type"]
        if kind not in GROWTH_EVENTS or event["visitor_type"] != "human":
            continue
        if not start <= str(event["occurred_at"])[:10] <= end:
            continue
        try:
            metadata = clean_metadata(json.loads(event["metadata"]))
        except (ValueError, TypeError):
            continue
        if metadata["eventId"] in seen:
            continue
        seen.add(metadata["eventId"])
        key = tuple(metadata[name] for name in ("source", "medium", "campaign"))
        for bucket in (total, channels[key]):
            bucket["sessions"].add(metadata["sessionId"])
            if metadata["returning"]:
                bucket["returningSessions"].add(metadata["sessionId"])
            field = {"page_view": "pageViews", "engaged_read": "engagedReads",
                     "rss_click": "rssClicks", "related_click": "relatedClicks"}[kind]
            bucket[field] += 1

    def counts(bucket):
        return {key: len(value) if isinstance(value, set) else value for key, value in bucket.items()}

    rows = [{**dict(zip(("source", "medium", "campaign"), key)), **counts(bucket)}
            for key, bucket in channels.items()]
    return {"summary": counts(total),
            "channels": sorted(rows, key=lambda row: (-row["sessions"], row["source"], row["campaign"]))[:50],
            "definition": "浏览器上报的近似会话；30 分钟无活动后新建。参与阅读需前台停留 30 秒且到达正文一半。RSS 点击不等于订阅。"}
