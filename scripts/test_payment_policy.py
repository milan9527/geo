#!/usr/bin/env python3
"""Payment policy regressions; all HTTP and AWS payment calls are mocked."""
import base64
import importlib
import io
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from urllib.request import Request

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "aws_runtime"), str(ROOT)]
for name in ("BEDROCK_MODEL_ID", "AURORA_RESOURCE_ARN", "AURORA_SECRET_ARN", "AGENTCORE_RUNTIME_ARN"):
    os.environ.setdefault(name, "test")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
with patch("boto3.client", return_value=Mock()):
    tools = importlib.import_module("crawler_tools")
    runtime = importlib.import_module("app")
    bridge = importlib.import_module("aws_scheduler.lambda_function")
from scripts.provision_eventbridge import SCHEDULES, target


class PaymentPolicyTests(unittest.TestCase):
    def test_all_provisioned_schedules_disable_purchases(self):
        for _, slug, _, _ in SCHEDULES:
            self.assertIs(json.loads(target(slug)["Input"])["allowPayment"], False)

    def test_bridge_only_allows_explicit_manual_boolean_permission(self):
        for marker, permission, expected in [
            ("eventbridge", True, False), ("geo-commerce-feed-miner", True, False),
            ("admin-manual", "false", False), ("admin-manual", "true", False),
            ("admin-manual", False, False), ("admin-manual", True, True),
        ]:
            response = {"statusCode": 200, "response": io.BytesIO(b'{"status":"accepted"}')}
            with patch.object(bridge.agentcore, "invoke_agent_runtime", return_value=response) as invoke:
                bridge.lambda_handler({"crawlerSlug": "commerce-feed-miner",
                                       "scheduledTime": marker, "allowPayment": permission}, None)
                payload = json.loads(invoke.call_args.kwargs["payload"])
                self.assertIs(payload["allowPayment"], expected)

    def test_runtime_blocks_old_schedule_payload_but_keeps_open_crawling(self):
        profile = {"sources": [], "paidSources": [{"url": "https://external.example/data"}],
                   "sourceRegistry": {}}
        crawler = {"slug": "commerce-feed-miner", "kind": "Browser Tool"}
        for marker, permission, expected in [
            (None, True, 0), ("eventbridge", True, 0),
            ("admin-manual", "true", 0), ("admin-manual", True, 1),
        ]:
            with patch.object(runtime, "load_source_profile", return_value=profile), \
                 patch.object(runtime, "run_browser_crawler", return_value=([], {"provider": "Browser"})) as browser, \
                 patch.object(runtime, "run_x402_crawler", return_value=({}, {})) as pay, \
                 patch.object(runtime, "normalize_evidence", side_effect=lambda evidence: evidence):
                runtime.collect_evidence(crawler, {"scheduledTime": marker, "allowPayment": permission})
                browser.assert_called_once()
                self.assertEqual(pay.call_count, expected)

    def test_own_domains_and_configured_aliases_fail_before_network_or_payment(self):
        urls = [
            "https://aperture.zhangwangshu.com/paid",
            "https://APERTURE.ZHANGWANGSHU.COM.:443/paid",
            "https://d1tsbnft7iv51.cloudfront.net/paid",
            "https://deu7vkdd3jf5.cloudfront.net/paid",
            "https://alias.example/paid", "https://origin.example/paid",
            "https://www.aperture.zhangwangshu.com/paid",
        ]
        with patch.dict(os.environ, {"GEO_PUBLIC_BASE_URL": "https://alias.example",
                                     "X402_INTERNAL_HOSTS": "origin.example"}), \
             patch.object(tools, "build_opener") as network, \
             patch.object(tools, "PaymentManager") as manager:
            for url in urls:
                with self.subTest(url=url), self.assertRaisesRegex(tools.CrawlerToolError, "Internal"):
                    tools.run_x402_crawler({"url": url}, session_name="test")
            network.assert_not_called()
            manager.assert_not_called()

    def test_redirect_cannot_forward_a_payment_proof(self):
        request = Request("https://external.example/data", headers={"PAYMENT-SIGNATURE": "mock"})
        for destination in ("https://aperture.zhangwangshu.com/paid", "https://another.example/paid"):
            with self.assertRaisesRegex(tools.CrawlerToolError, "redirects"):
                tools.PaymentRedirectHandler().redirect_request(request, None, 302, "", {}, destination)

    def challenge(self, resource):
        challenge = {"resource": {"url": resource},
                     "accepts": [{"amount": "2000", "network": "eip155:84532"}]}
        return HTTPError("https://external.example/data", 402, "Payment Required",
                         {"PAYMENT-REQUIRED": base64.b64encode(json.dumps(challenge).encode()).decode()},
                         io.BytesIO(b"{}"))

    def test_external_source_cannot_request_payment_for_internal_resource(self):
        with patch.multiple(tools, PAYMENT_MANAGER_ARN="mock", PAYMENT_CONNECTOR_ID="mock"), \
             patch.object(tools, "build_opener") as network, \
             patch.object(tools, "PaymentManager") as manager:
            network.return_value.open.side_effect = self.challenge("https://aperture.zhangwangshu.com/paid")
            with self.assertRaisesRegex(tools.CrawlerToolError, "Internal"):
                tools.run_x402_crawler({"url": "https://external.example/data"}, session_name="test")
            manager.assert_not_called()

    def test_manual_external_payment_still_works_with_mocked_services(self):
        url = "https://external.example/data"
        paid = Mock(status=200)
        paid.read.return_value = b'{"evidence":"external"}'
        paid.headers = {}
        paid_context = Mock()
        paid_context.__enter__ = Mock(return_value=paid)
        paid_context.__exit__ = Mock(return_value=False)
        with patch.multiple(tools, PAYMENT_MANAGER_ARN="mock", PAYMENT_CONNECTOR_ID="mock"), \
             patch.object(tools, "build_opener") as network, \
             patch.object(tools, "PaymentManager") as manager, \
             patch.object(tools.agentcore, "list_payment_instruments", return_value={
                 "paymentInstruments": [{"status": "ACTIVE",
                    "paymentInstrumentType": "EMBEDDED_CRYPTO_WALLET", "paymentInstrumentId": "mock"}]}):
            network.return_value.open.side_effect = [self.challenge(url), paid_context]
            manager.return_value.create_payment_session.return_value = {"paymentSessionId": "mock"}
            manager.return_value.generate_payment_header.return_value = {"PAYMENT-SIGNATURE": "mock"}
            evidence, trace = tools.run_x402_crawler({"url": url, "publisher": "External"}, session_name="test")
            self.assertTrue(evidence["data"]["paid"])
            self.assertEqual(trace["httpStatus"], 200)
            self.assertEqual(network.return_value.open.call_count, 2)


if __name__ == "__main__":
    unittest.main()
