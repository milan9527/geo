"""Permanent mappings for reviewed legacy article URLs."""

REDIRECT_PROTECTION_MESSAGE = "旧网址已永久重定向，不能删除原稿、修改网址或在该网址重新发布；新内容请新建稿件。"

REDIRECT_SCHEMA = [
    """
    CREATE TABLE IF NOT EXISTS article_redirects (
        source_slug TEXT PRIMARY KEY CHECK(source_slug ~ '^[a-z0-9][a-z0-9-]{0,239}$'),
        source_article_id BIGINT NOT NULL UNIQUE REFERENCES articles(id),
        target_article_id BIGINT NOT NULL REFERENCES articles(id),
        source_content_hash TEXT NOT NULL,
        target_content_hash TEXT NOT NULL,
        review_json TEXT NOT NULL,
        created_at TEXT NOT NULL,
        CHECK(source_article_id != target_article_id)
    )
    """,
    """
    CREATE OR REPLACE FUNCTION geo_validate_article_redirect()
    RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE source articles%ROWTYPE; target articles%ROWTYPE;
    BEGIN
        SELECT * INTO source FROM articles WHERE id=NEW.source_article_id FOR SHARE;
        SELECT * INTO target FROM articles WHERE id=NEW.target_article_id FOR SHARE;
        IF source.id IS NULL OR target.id IS NULL
           OR source.slug != NEW.source_slug OR source.status = 'published'
           OR target.status != 'published' OR source.id = target.id THEN
            RAISE EXCEPTION 'Legacy redirect requires an unpublished source and a published target'
                USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END $$;
    """,
    "DROP TRIGGER IF EXISTS validate_article_redirect ON article_redirects",
    """CREATE TRIGGER validate_article_redirect BEFORE INSERT OR UPDATE ON article_redirects
       FOR EACH ROW EXECUTE FUNCTION geo_validate_article_redirect()""",
    """
    CREATE OR REPLACE FUNCTION geo_protect_redirect_source()
    RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
        IF EXISTS(SELECT 1 FROM article_redirects WHERE source_article_id=OLD.id)
           AND (NEW.slug IS DISTINCT FROM OLD.slug OR NEW.status='published') THEN
            RAISE EXCEPTION 'A permanently redirected article URL cannot be republished or renamed'
                USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END $$;
    """,
    "DROP TRIGGER IF EXISTS protect_redirect_source ON articles",
    """CREATE TRIGGER protect_redirect_source BEFORE UPDATE OF slug,status ON articles
       FOR EACH ROW EXECUTE FUNCTION geo_protect_redirect_source()""",
]


def ensure_article_redirects(conn):
    for statement in REDIRECT_SCHEMA:
        conn.execute(statement)


def redirect_target(conn, source_slug):
    row = conn.execute(
        """SELECT a.slug FROM article_redirects r
           JOIN articles a ON a.id=r.target_article_id
           WHERE r.source_slug=%s AND a.status='published'""",
        (source_slug,),
    ).fetchone()
    return row["slug"] if row else None
