#!/usr/bin/env python3
"""Editorial rejection, evidence grounding, graph visibility and atomic revision regressions."""
from copy import deepcopy
import importlib
import json
import sys
from pathlib import Path
import unittest
from unittest.mock import Mock, patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend import editorial, knowledge, bilingual
from aws_runtime.publication import PublicationStore, content_identity, review_and_publish
from scripts.test_scheduled_publication import SqlPublicationTests, distinct


def bi(text="Specific reader decision"):
    return {"en":text,"zh":text}


def graph(article):
    quote = json.loads(article["body_json"])[0]["paragraphs"][0][:40]
    doc = {k:bi() for k in ("question","audience","decision","contribution","limitation")}
    doc.update(nodes=[{"key":"a","kind":"concept","name":bi("Evidence"),"mention":"正文事实"},
                      {"key":"b","kind":"concept","name":bi("Reasoning"),"mention":"论证"}],
               edges=[{"source":"a","target":"b","relation":"enables","stance":"analysis",
                       "explanation":bi(),"quote":bi(quote),"citations":[1]}])
    return {"document":doc,"review":{"approved":True,"score":96,"supported":True,"faithful":True,"issues":[]},
            "contentHash":bilingual.source_hash(article),"sourcesHash":knowledge.sources_hash(article)}


class EditorialTests(unittest.TestCase):
    def test_insufficient_evidence_holds_without_a_model_call(self):
        call=Mock()
        result=editorial.commission_topic("finance",[],[],[],call)
        self.assertEqual(result["action"],"hold")
        call.assert_not_called()
    def test_failed_request_and_empty_index_are_not_sources(self):
        for item in [{"title":"Source unavailable","excerpt":"x"*200},
                     {"title":"DEFA14A","excerpt":"x"*200},
                     {"title":"Specific release","excerpt":"short"},
                     {"title":"Specific release","excerpt":"x"*200,"data":{"httpStatus":503}}]:
            self.assertFalse(editorial.usable_evidence(item))
        self.assertTrue(editorial.usable_evidence({"title":"Measured results","excerpt":"x"*200}))

    def test_unsupported_quote_and_missing_citation_fail(self):
        article={"title":"标题","dek":"说明","summary":"摘要","keywords":"[]",
                 "body_json":json.dumps([{"paragraphs":["正文事实与论证说明来源，并给出边界条件[S1]"]}]),
                 "sources":[{"title":"source"}]}
        value=graph(article)
        knowledge.validate_document(article,value["document"])
        for field, bad in [("quote",bi("An invented sentence absent from the source")),("citations",[2])]:
            changed=deepcopy(value["document"]);changed["edges"][0][field]=bad
            with self.assertRaises(ValueError): knowledge.validate_document(article,changed)

    def test_high_score_cannot_override_reader_value_failure(self):
        call=Mock(return_value={"approved":True,"score":99,"focused":True,"decisionUseful":False,
            "evidenceDriven":True,"analyticalDepth":True,"deliversContribution":True,"issues":[],"reason":bi()})
        self.assertFalse(editorial.review_reader_value({},[],{},call)["passed"])

    def test_useful_standalone_guide_does_not_need_a_graph(self):
        article={"title":"标题","dek":"说明","summary":"摘要","keywords":"[]",
                 "body_json":json.dumps([{"paragraphs":["正文事实与论证说明来源，并给出边界条件[S1]"]}]),
                 "sources":[{"title":"source"}]}
        value=graph(article)
        value["document"].update(nodes=[],edges=[])
        call=Mock(side_effect=[value["document"],value["review"]])
        result=knowledge.create_document(article,call)
        self.assertEqual(result["document"]["nodes"],[])
        self.assertTrue(result["review"]["approved"])
        # Optional graph coverage does not waive review of the guide itself.
        rejected={**value["review"],"supported":False,"issues":["Invented reader benefit"]}
        call=Mock(side_effect=[value["document"],rejected,value["document"],rejected])
        with self.assertRaises(ValueError):
            knowledge.create_document(article,call)

    def test_commission_rejection_holds_before_writing(self):
        plan={k:bi() for k in ("question","audience","decision","whyNow","contribution","outline","reason")}
        plan.update(action="new",targetArticleId=None,evidenceIds=[1,2],relatedArticleIds=[])
        evidence=[{"publisher":"source","title":"actual","url":"https://example.com","publishedAt":"2026-10-16","excerpt":"x"*200}]*2
        review={"approved":False,"score":98,"focused":True,"useful":False,"supported":True,"incremental":False,"issues":["same question"],"reason":bi()}
        call=Mock(side_effect=[plan,review,plan,review])
        self.assertEqual(editorial.commission_topic("finance",evidence,[],[],call)["action"],"hold")
        self.assertEqual(call.call_count,4)


class ReadingPresentationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with patch("backend.database.init_db"):
            cls.app=importlib.import_module("backend.app")

    def test_reading_paths_show_grounded_value_in_both_languages(self):
        handler=object.__new__(self.app.ApiHandler)
        guide={k:{"en":"Actual contribution","zh":"实际研究贡献"}
               for k in ("question","audience","decision","contribution","limitation")}
        guide["nodes"]=[]
        context={"question":{"en":"How does recovery work?","zh":"如何恢复失败任务？"},
                 "contribution":{"en":"Explains retry boundaries","zh":"解释重试的适用边界"},
                 "topics":[{"name":{"en":"Recovery <script>","zh":"任务恢复 <script>"}}]}
        related=[{"slug":"recovery","title":"Recovery","dek":"Summary",
                  "category":{"name":"Agent"},"readingContext":context}]
        for path,locale in [("/article/current","en"),("/zh/article/current","zh")]:
            with self.subTest(locale=locale):
                self.app.i18n.begin_request(path,{})
                guide_html=handler._reader_guide(guide,related)
                html=handler._related_reading(related)
                destination="/zh/article/recovery" if locale=="zh" else "/article/recovery"
                self.assertIn(f'href="{destination}"',guide_html)
                self.assertIn(context["question"][locale],guide_html)
                self.assertIn(context["contribution"][locale],html)
                self.assertIn("&lt;script&gt;",html)
                self.assertNotIn("<script>",html)
                self.assertNotIn("knowledge-chips",guide_html)

    def test_fallback_suggestions_do_not_claim_a_knowledge_connection(self):
        handler=object.__new__(self.app.ApiHandler)
        self.app.i18n.begin_request("/article/current",{})
        html=handler._related_reading([{"slug":"other","title":"Other","dek":"Summary",
                                      "category":{"name":"Cloud"},"readingContext":None}])
        self.assertIn("More research",html)
        self.assertNotIn("Shared context",html)
        self.assertNotIn("Build a fuller picture",html)
        self.assertEqual(handler._related_reading([]),"")


class RevisionTests(SqlPublicationTests):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        with cls.connect() as conn:
            bilingual.ensure_schema(conn)
            knowledge.ensure_schema(conn)

    def setUp(self):
        with self.connect() as conn:
            conn.execute("TRUNCATE article_redirects,sources,articles RESTART IDENTITY CASCADE")
        self.store=PublicationStore(self.sql,self.transaction)

    def prepare(self,article):
        with self.connect() as conn:
            bilingual.save_english(conn,article,{"sourceHash":bilingual.source_hash(article),
                "content":bilingual.source_document(article),"review":{"approved":True,"score":96,"complete":True,"faithful":True,"issues":[]},"reviewedAt":"2026-10-16"})
            knowledge.save_document(conn,article,graph(article),"2026-10-16")

    def test_archive_lookup_prefers_evidence_available_at_article_revision(self):
        draft=self.draft();article=self.store.article(draft["id"])
        article["sources"]=article["sources"][:1]
        with self.connect() as conn:
            conn.execute("CREATE TEMP TABLE research_runs(id INT,output_article_id BIGINT)")
            conn.execute("""CREATE TEMP TABLE research_evidence(id INT,run_id INT,url TEXT,title TEXT,
                published_at TEXT,publisher TEXT,content_excerpt TEXT,retrieved_at TEXT)""")
            conn.execute("INSERT INTO research_runs VALUES(1,NULL),(2,%s)",(article["id"],))
            source=article["sources"][0]
            for index,stamp in [(1,"2026-09-08"),(2,"2026-10-16")]:
                conn.execute("INSERT INTO research_evidence VALUES(%s,%s,%s,%s,%s,%s,%s,%s)",
                             (index,index,source["url"],source["title"],source["published_at"],
                              source["publisher"],f"Archive {index}",stamp))
            data=knowledge.attach_evidence(conn,article)["evidence"][0]
            self.assertEqual(data["archiveId"],1)
            self.assertFalse(data["afterArticleRevision"])
            conn.execute("DELETE FROM research_evidence WHERE id=1")
            self.assertTrue(knowledge.attach_evidence(conn,article)["evidence"][0]["afterArticleRevision"])

    def test_graph_hides_drafts_and_stale_sources_and_content(self):
        draft=self.draft();article=self.store.article(draft["id"]);self.prepare(article)
        with self.connect() as conn:
            self.assertIsNone(knowledge.article_document(conn,article["id"]))
        self.store.commit_publication(article,[])
        with self.connect() as conn:
            self.assertIsNotNone(knowledge.article_document(conn,article["id"]))
            data=knowledge.overview(conn)
            self.assertEqual(data["totals"]["mapped"],1)
            selected=knowledge.overview(conn,node=data["nodes"][0]["id"])
            self.assertEqual(len(selected["edges"]),1)
            conn.execute("UPDATE sources SET title='Changed source' WHERE article_id=%s",(article["id"],))
            self.assertIsNone(knowledge.article_document(conn,article["id"]))

    def test_reading_connections_exclude_drafts_stale_and_unrelated_articles(self):
        origin=self.draft("origin");article=self.store.article(origin["id"]);self.prepare(article)
        self.store.commit_publication(article,[])
        target=self.draft("cross-category",category=2);other=self.store.article(target["id"]);self.prepare(other)
        self.store.commit_publication(other,self.store.published())
        pending=self.draft("pending");self.prepare(self.store.article(pending["id"]))
        isolated=self.draft("isolated");standalone=self.store.article(isolated["id"]);self.prepare(standalone)
        with self.connect() as conn:
            value=graph(standalone);value["document"].update(nodes=[],edges=[])
            knowledge.save_document(conn,standalone,value,"2026-10-16")
        self.store.commit_publication(standalone,self.store.published())
        with self.connect() as conn:
            paths=knowledge.reading_connections(conn,article["id"])
            self.assertEqual([p["article_id"] for p in paths],[other["id"]])
            self.assertEqual(len(paths[0]["topics"]),2)
            self.assertEqual(paths[0]["contribution"],bi())
            self.assertEqual(knowledge.reading_connections(conn,standalone["id"]),[])
            # New published research is also discovered from the older article.
            self.assertEqual(knowledge.reading_connections(conn,other["id"])[0]["article_id"],article["id"])
            conn.execute("UPDATE sources SET title='Changed source' WHERE article_id=%s",(other["id"],))
            self.assertEqual(knowledge.reading_connections(conn,article["id"]),[])

    def test_revision_preserves_url_date_and_history(self):
        first=self.draft("permanent");target=self.store.article(first["id"]);self.prepare(target)
        self.store.commit_publication(target,[]);target=self.store.article(first["id"])
        candidate=self.draft("private-revision");article=self.store.article(candidate["id"]);self.prepare(article)
        review={"passed":True,"targetHash":content_identity(target),"candidateHash":content_identity(article),"changeSummary":bi("Added measured evidence")}
        ok,reason=self.store.commit_revision(article,[target],target,review)
        self.assertTrue(ok,reason)
        updated=self.store.article(target["id"])
        self.assertEqual((updated["slug"],updated["published_at"],updated["status"]),("permanent",target["published_at"],"published"))
        with self.connect() as conn:
            row=conn.execute("SELECT * FROM article_revisions").fetchone()
            self.assertEqual(json.loads(row["previous_json"])["title"],target["title"])
            self.assertIsNotNone(knowledge.article_document(conn,target["id"]))
        self.assertEqual(self.store.article(article["id"])["status"],"review")
        ok,reason=self.store.commit_revision(article,[target],target,review)
        self.assertFalse(ok)
        self.assertEqual(reason,"published_catalog_changed")

    def test_independent_second_review_disagreement_remains_held(self):
        draft=self.draft();candidate=self.store.article(draft["id"])
        existing=self.publish_existing()
        first={**distinct(),"confidence":.85}
        second={"relation":"duplicate","confidence":.99,"materialDifferences":[],"preferredArticleId":existing["id"],"reason":"same"}
        retry=Mock(return_value=second)
        result=review_and_publish(self.store,candidate["id"],content_identity(candidate),{"ready":True},Mock(return_value=first),enabled=True,recompare=retry,prepare_language=Mock())
        self.assertEqual(result["reason"],"uncertain_comparison")
        retry.assert_called_once()


if __name__=="__main__": unittest.main()
