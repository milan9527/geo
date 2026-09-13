"""Read complete metric ranges without exceeding Aurora Data API response limits."""

METRICS_PAGE_SIZE = 128


def _read_pages(conn, query, start, end, keys):
    rows = []
    cursor = None
    columns = ", ".join(keys)
    while True:
        after = ""
        parameters = [start, end]
        if cursor is not None:
            after = f"AND ({columns}) > ({', '.join('%s' for _ in keys)})"
            parameters.extend(cursor)
        parameters.append(METRICS_PAGE_SIZE)
        page = conn.execute(
            query.format(after=after, columns=columns),
            tuple(parameters),
        ).fetchall()
        rows.extend(dict(row) for row in page)
        if len(page) < METRICS_PAGE_SIZE:
            return rows
        # Include every primary-key component so rows sharing an hour or event
        # timestamp cannot be skipped at a page boundary.
        cursor = tuple(page[-1][key.split(".")[-1]] for key in keys)


def load_metrics_rows(conn, start, end):
    # Keep every page and both periods on the same snapshot while log ingestion
    # continues. This must be the first statement in the metrics transaction.
    conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
    traffic = _read_pages(
        conn,
        """
        SELECT bucket_hour, visitor_type, agent_name, path_group,
               article_slug, access_variant, requests,
               successful_requests, bytes_sent, visitor_hll
        FROM traffic_hourly
        WHERE bucket_hour >= %s AND bucket_hour < %s
        {after}
        ORDER BY {columns} LIMIT %s
        """,
        start, end,
        ("bucket_hour", "visitor_type", "agent_name", "path_group",
         "article_slug", "access_variant"),
    )
    events = _read_pages(
        conn,
        """
        SELECT te.id, te.event_type, te.visitor_type, te.agent_name,
               te.article_id, te.occurred_at, te.metadata,
               a.slug article_slug, a.title article_title
        FROM traffic_events te
        LEFT JOIN articles a ON a.id = te.article_id
        WHERE te.occurred_at >= %s AND te.occurred_at < %s
        {after}
        ORDER BY {columns} LIMIT %s
        """,
        start, end, ("te.occurred_at", "te.id"),
    )
    return traffic, events
