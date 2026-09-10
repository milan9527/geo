"""Database protection for stable, already-published article URLs."""

PUBLISHED_PROTECTION_MESSAGE = "已发布页面需保持在线，不能删除、退回草稿或审核，也不能修改原网址。"
REVIEW_REQUIRED_MESSAGE = "新文章必须先通过证据审核和全文去重，由审核流程发布；不能直接更改为已发布。"

PROTECTION_SCHEMA = [
    """
    CREATE OR REPLACE FUNCTION geo_protect_published_article()
    RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
        IF OLD.status = 'published' THEN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'Published articles cannot be deleted'
                    USING ERRCODE = '23514';
            END IF;
            IF NEW.status != 'published' OR NEW.slug IS DISTINCT FROM OLD.slug THEN
                RAISE EXCEPTION 'Published articles must retain their status and URL'
                    USING ERRCODE = '23514';
            END IF;
        END IF;
        IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
        RETURN NEW;
    END $$;
    """,
    "DROP TRIGGER IF EXISTS protect_published_article ON articles",
    """CREATE TRIGGER protect_published_article BEFORE UPDATE OR DELETE ON articles
       FOR EACH ROW EXECUTE FUNCTION geo_protect_published_article()""",
]


def ensure_publication_protection(conn):
    # Function bodies contain semicolons; execute each complete statement.
    for statement in PROTECTION_SCHEMA:
        conn.execute(statement)
