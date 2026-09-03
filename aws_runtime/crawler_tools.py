from __future__ import annotations

import ast
import base64
import json
import os
import re
import subprocess
import uuid
from datetime import datetime, timezone
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

import boto3
from bedrock_agentcore.payments import PaymentManager
from bedrock_agentcore.tools.browser_client import browser_session


REGION = os.environ.get("AWS_REGION", "us-east-1")
BROWSER_ID = os.environ.get("AGENTCORE_BROWSER_ID", "")
CODE_INTERPRETER_ID = os.environ.get("AGENTCORE_CODE_INTERPRETER_ID", "")
CODEX_WORKER = os.environ.get(
    "CODEX_CRAWLER_WORKER",
    "/app/agent_runtime/dist/codex_cli.js",
)
BROWSER_WORKER = os.environ.get(
    "AGENTCORE_BROWSER_WORKER",
    "/app/agent_runtime/dist/browser_cli.js",
)
PAYMENT_MANAGER_ARN = os.environ.get("AGENTCORE_PAYMENT_MANAGER_ARN", "")
PAYMENT_CONNECTOR_ID = os.environ.get("AGENTCORE_PAYMENT_CONNECTOR_ID", "")
PAYMENT_USER_ID = os.environ.get("AGENTCORE_PAYMENT_USER_ID", "geo-research-agent")
PAYMENT_MAX_SPEND_USD = os.environ.get("X402_MAX_SESSION_SPEND_USD", "0.01")
PAYMENT_MAX_BASE_UNITS = int(os.environ.get("X402_MAX_CHALLENGE_BASE_UNITS", "2000"))

agentcore = boto3.client("bedrock-agentcore", region_name=REGION)


class CrawlerToolError(RuntimeError):
    pass


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def codex_request(
    action: str,
    *,
    request: dict[str, Any] | None = None,
    thread_id: str = "",
    failure_log: str = "",
) -> dict[str, Any]:
    payload: dict[str, Any] = {"action": action}
    if request is not None:
        payload["request"] = request
    if thread_id:
        payload["threadId"] = thread_id
    if failure_log:
        payload["failureLog"] = failure_log[:12_000]
    environment = os.environ.copy()
    environment.setdefault("CODEX_HOME", "/tmp/codex")
    completed = subprocess.run(
        ["node", CODEX_WORKER],
        input=json.dumps(payload, ensure_ascii=False),
        text=True,
        capture_output=True,
        timeout=360,
        check=False,
        env=environment,
        cwd="/tmp/geo-crawler-workspace",
    )
    if completed.returncode:
        raise CrawlerToolError(
            f"Codex SDK worker failed ({completed.returncode}): "
            f"{completed.stderr.strip()[:4000]}"
        )
    try:
        artifact = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise CrawlerToolError("Codex SDK worker returned invalid JSON") from error
    if not artifact.get("sourceCode") or not artifact.get("threadId"):
        raise CrawlerToolError("Codex SDK worker returned an incomplete artifact")
    return artifact


def build_codex_request(profile: dict[str, Any]) -> dict[str, Any]:
    sources = profile.get("sources") or []
    domains = sorted(
        {
            hostname
            for source in sources
            if (hostname := urlsplit(str(source["url"])).hostname)
        }
    )
    return {
        "domain": ",".join(domains),
        "style": profile.get("crawlStyle", "research"),
        "allowedDomains": domains,
        "requiredFields": [
            "publisher",
            "title",
            "url",
            "publishedAt",
            "retrievedAt",
            "sourceType",
            "excerpt",
            "data",
        ],
        "sampleUrls": [str(source["url"]) for source in sources],
        "robotsPolicy": (
            "Public official publisher endpoints only. One request per URL, "
            "no authentication bypass, no CAPTCHA bypass, 2.5 MB maximum per response."
        ),
    }


def validate_generated_code(source_code: str, profile: dict[str, Any]) -> None:
    if len(source_code) > 180_000:
        raise CrawlerToolError("Generated crawler exceeds the source size limit")
    banned = {
        r"\bsubprocess\b": "subprocess",
        r"\bsocket\b": "raw socket",
        r"\bos\.system\b": "shell execution",
        r"\b(eval|exec)\s*\(": "dynamic code execution",
        r"169\.254\.169\.254": "cloud metadata",
        r"/proc/|/sys/|/etc/": "host filesystem",
    }
    for pattern, label in banned.items():
        if re.search(pattern, source_code):
            raise CrawlerToolError(f"Generated crawler contains forbidden {label}")

    allowed = {
        urlsplit(str(source["url"])).hostname
        for source in profile.get("sources") or []
    }
    try:
        tree = ast.parse(source_code)
    except SyntaxError as error:
        raise CrawlerToolError(
            f"Generated crawler is not valid Python: {error.msg}"
        ) from error

    class UrlLiteralVisitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self.function_stack: list[str] = []
            self.unapproved: set[str] = set()

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            self.function_stack.append(node.name)
            self.generic_visit(node)
            self.function_stack.pop()

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            self.function_stack.append(node.name)
            self.generic_visit(node)
            self.function_stack.pop()

        def visit_Constant(self, node: ast.Constant) -> None:
            if not isinstance(node.value, str):
                return
            in_test = any(
                name.startswith("test_")
                or name in {"self_test", "run_tests"}
                for name in self.function_stack
            )
            for url in re.findall(r"https://[^\s\"'<>]+", node.value):
                hostname = urlsplit(url.rstrip(".,;)]}")).hostname
                if hostname and hostname not in allowed and not in_test:
                    self.unapproved.add(hostname)

    visitor = UrlLiteralVisitor()
    visitor.visit(tree)
    if visitor.unapproved:
        raise CrawlerToolError(
            "Generated crawler references unapproved domain(s): "
            + ", ".join(sorted(visitor.unapproved))
        )


def _code_interpreter_output(response: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    stdout: list[str] = []
    stderr: list[str] = []
    exit_code = 0
    execution_time = 0.0
    is_error = False
    for event in response["stream"]:
        result = event.get("result")
        if not result:
            error = next(
                (value for key, value in event.items() if key.endswith("Exception")),
                None,
            )
            if error:
                raise CrawlerToolError(f"Code Interpreter API error: {error}")
            continue
        is_error = is_error or bool(result.get("isError"))
        structured = result.get("structuredContent") or {}
        if structured.get("stdout"):
            stdout.append(str(structured["stdout"]))
        if structured.get("stderr"):
            stderr.append(str(structured["stderr"]))
        exit_code = int(structured.get("exitCode") or exit_code)
        execution_time += float(structured.get("executionTime") or 0)
        if not structured:
            for block in result.get("content") or []:
                if block.get("type") == "text":
                    stdout.append(str(block.get("text") or ""))
    output = "\n".join(stdout).strip()
    trace = {
        "exitCode": exit_code,
        "executionTimeSeconds": round(execution_time, 3),
        "stderr": "\n".join(stderr)[-4000:],
        "isError": is_error,
    }
    combined_error = "\n".join([*stderr, output])
    benign_system_exit = (
        is_error
        and "SystemExit: 0" in combined_error
    )
    if (exit_code and not benign_system_exit) or (is_error and not benign_system_exit):
        raise CrawlerToolError(
            f"Code Interpreter execution failed: {trace['stderr'] or output[-2000:]}"
        )
    if benign_system_exit:
        trace["exitCode"] = 0
        trace["isError"] = False
        trace["normalizedSystemExit"] = True
    return output, trace


def execute_code_interpreter(
    source_code: str,
    *,
    session_name: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if not CODE_INTERPRETER_ID:
        raise CrawlerToolError("AGENTCORE_CODE_INTERPRETER_ID is not configured")
    session = agentcore.start_code_interpreter_session(
        codeInterpreterIdentifier=CODE_INTERPRETER_ID,
        name=session_name[:100],
        sessionTimeoutSeconds=900,
    )
    session_id = session["sessionId"]
    try:
        executable_source = (
            "import sys\n"
            "sys.argv = ['agentcore_crawler.py']\n"
            + source_code
        )
        response = agentcore.invoke_code_interpreter(
            codeInterpreterIdentifier=CODE_INTERPRETER_ID,
            sessionId=session_id,
            name="executeCode",
            arguments={"language": "python", "code": executable_source},
        )
        try:
            output, trace = _code_interpreter_output(response)
        except CrawlerToolError as execution_error:
            traceback_response = agentcore.invoke_code_interpreter(
                codeInterpreterIdentifier=CODE_INTERPRETER_ID,
                sessionId=session_id,
                name="executeCode",
                arguments={"language": "python", "code": "%tb"},
            )
            try:
                traceback_output, _ = _code_interpreter_output(traceback_response)
            except CrawlerToolError as traceback_error:
                traceback_output = str(traceback_error)
            raise CrawlerToolError(
                f"{execution_error}\nSandbox traceback:\n{traceback_output[-6000:]}"
            ) from execution_error
        try:
            parsed = json.loads(output)
        except json.JSONDecodeError as error:
            start = output.find("{")
            end = output.rfind("}")
            if start < 0 or end <= start:
                raise CrawlerToolError(
                    f"Code Interpreter did not emit JSON: {output[-2000:]}"
                ) from error
            parsed = json.loads(output[start : end + 1])
        evidence = parsed.get("evidence")
        if not isinstance(evidence, list) or not evidence:
            errors = parsed.get("errors") or []
            raise CrawlerToolError(
                f"Code Interpreter returned no evidence: {json.dumps(errors)[:2000]}"
            )
        trace.update(
            {
                "provider": "AgentCore Code Interpreter",
                "sessionId": session_id,
                "documents": len(evidence),
            }
        )
        return evidence, trace
    finally:
        agentcore.stop_code_interpreter_session(
            codeInterpreterIdentifier=CODE_INTERPRETER_ID,
            sessionId=session_id,
        )


def run_generated_crawler(
    profile: dict[str, Any],
    artifact: dict[str, Any],
    *,
    session_name: str,
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    source_code = str(artifact["sourceCode"])
    validate_generated_code(source_code, profile)
    try:
        evidence, trace = execute_code_interpreter(
            source_code,
            session_name=session_name,
        )
        return evidence, trace, artifact
    except Exception as first_error:
        repaired = codex_request(
            "repair",
            thread_id=str(artifact["threadId"]),
            failure_log=str(first_error),
        )
        validate_generated_code(str(repaired["sourceCode"]), profile)
        evidence, trace = execute_code_interpreter(
            str(repaired["sourceCode"]),
            session_name=f"{session_name}-repair",
        )
        trace["repairApplied"] = True
        trace["firstFailure"] = str(first_error)[:1000]
        return evidence, trace, repaired


def run_browser_crawler(
    profile: dict[str, Any],
    *,
    session_name: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if not BROWSER_ID:
        raise CrawlerToolError("AGENTCORE_BROWSER_ID is not configured")
    with browser_session(
        REGION,
        identifier=BROWSER_ID,
        name=session_name[:100],
        viewport={"width": 1440, "height": 900},
    ) as client:
        ws_url, headers = client.generate_ws_headers()
        completed = subprocess.run(
            ["node", BROWSER_WORKER],
            input=json.dumps(
                {
                    "wsUrl": ws_url,
                    "headers": headers,
                    "sources": profile.get("sources") or [],
                },
                ensure_ascii=False,
            ),
            text=True,
            capture_output=True,
            timeout=300,
            check=False,
        )
        if completed.returncode:
            raise CrawlerToolError(
                f"AgentCore Browser worker failed ({completed.returncode}): "
                f"{completed.stderr[-4000:]}"
            )
        try:
            result = json.loads(completed.stdout)
        except json.JSONDecodeError as error:
            raise CrawlerToolError("AgentCore Browser worker returned invalid JSON") from error
        evidence = result.get("evidence")
        page_records = result.get("pages")
        if not isinstance(evidence, list):
            evidence = []
        if not isinstance(page_records, list):
            page_records = []
        trace = {
            "provider": "AgentCore Browser",
            "sessionId": client.session_id,
            "browserIdentifier": BROWSER_ID,
            "webBotAuth": True,
            "documents": len(evidence),
            "pages": page_records,
        }
    if not evidence:
        raise CrawlerToolError("AgentCore Browser returned no extractable evidence")
    return evidence, trace


def _decode_payment_header(value: str) -> dict[str, Any]:
    try:
        return json.loads(base64.b64decode(value).decode("utf-8"))
    except Exception:
        return {}


def run_x402_crawler(
    source: dict[str, Any],
    *,
    session_name: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if not PAYMENT_MANAGER_ARN or not PAYMENT_CONNECTOR_ID:
        raise CrawlerToolError("AgentCore Payments configuration is incomplete")
    url = str(source["url"])
    if urlsplit(url).scheme != "https":
        raise CrawlerToolError("x402 resources must use HTTPS")
    request = Request(
        url,
        headers={"User-Agent": "ApertureGEOResearchBot/2.0"},
    )
    try:
        with urlopen(request, timeout=30) as response:
            body = response.read(2_500_000)
            status = response.status
            response_headers = dict(response.headers.items())
    except HTTPError as error:
        if error.code != 402:
            raise
        body = error.read(1_000_000)
        status = error.code
        response_headers = dict(error.headers.items())
    if status != 402:
        raise CrawlerToolError(f"Expected x402 challenge, received HTTP {status}")

    normalized_response_headers = {
        key.lower(): value for key, value in response_headers.items()
    }
    challenge = _decode_payment_header(
        normalized_response_headers.get("payment-required", "")
    )
    accepted = challenge.get("accepts") or []
    amounts = [
        int(option["amount"])
        for option in accepted
        if str(option.get("amount", "")).isdigit()
    ]
    if not amounts or min(amounts) > PAYMENT_MAX_BASE_UNITS:
        raise CrawlerToolError("x402 price is missing or exceeds the configured limit")

    instruments = agentcore.list_payment_instruments(
        paymentManagerArn=PAYMENT_MANAGER_ARN,
        paymentConnectorId=PAYMENT_CONNECTOR_ID,
        userId=PAYMENT_USER_ID,
        agentName=session_name[:100],
        maxResults=20,
    )
    instrument = next(
        (
            item
            for item in instruments.get("paymentInstruments") or []
            if item.get("status") == "ACTIVE"
            and item.get("paymentInstrumentType") == "EMBEDDED_CRYPTO_WALLET"
        ),
        None,
    )
    if not instrument:
        raise CrawlerToolError("No active embedded crypto wallet was found")

    manager = PaymentManager(
        payment_manager_arn=PAYMENT_MANAGER_ARN,
        region_name=REGION,
        agent_name=session_name[:100],
    )
    session = manager.create_payment_session(
        expiry_time_in_minutes=15,
        user_id=PAYMENT_USER_ID,
        limits={
            "maxSpendAmount": {
                "value": PAYMENT_MAX_SPEND_USD,
                "currency": "USD",
            }
        },
        client_token=str(uuid.uuid4()),
    )
    payment_session = session.get("paymentSession") or session
    session_id = payment_session.get("paymentSessionId")
    proof_headers = manager.generate_payment_header(
        user_id=PAYMENT_USER_ID,
        payment_instrument_id=instrument["paymentInstrumentId"],
        payment_session_id=session_id,
        payment_connector_id=PAYMENT_CONNECTOR_ID,
        payment_required_request={
            "statusCode": 402,
            "headers": response_headers,
            "body": body.decode("utf-8", errors="replace"),
        },
        client_token=str(uuid.uuid4()),
    )
    paid_request = Request(
        url,
        headers={
            "User-Agent": "ApertureGEOResearchBot/2.0",
            **proof_headers,
        },
    )
    with urlopen(paid_request, timeout=45) as paid_response:
        paid_body = paid_response.read(2_500_000)
        paid_status = paid_response.status
        paid_headers = dict(paid_response.headers.items())
    if paid_status != 200:
        raise CrawlerToolError(f"x402 paid retry returned HTTP {paid_status}")
    decoded = paid_body.decode("utf-8", errors="replace")
    normalized_paid_headers = {
        key.lower(): value for key, value in paid_headers.items()
    }
    payment_response = _decode_payment_header(
        normalized_paid_headers.get("payment-response", "")
    )
    transaction = (
        payment_response.get("transaction")
        or payment_response.get("transactionHash")
        or ""
    )
    evidence = {
        "publisher": source["publisher"],
        "title": source.get("title") or "x402 paid machine-readable resource",
        "url": url,
        "publishedAt": now()[:10],
        "retrievedAt": now(),
        "sourceType": source.get("sourceType", "x402 付费数据"),
        "excerpt": re.sub(r"\s+", " ", decoded)[:3000],
        "data": {
            "paid": True,
            "httpStatus": paid_status,
            "transactionHash": transaction,
            "network": next(
                (item.get("network") for item in accepted if item.get("network")),
                "",
            ),
            "amountBaseUnits": min(amounts),
        },
    }
    trace = {
        "provider": "AgentCore Payments",
        "paymentSessionId": session_id,
        "paymentInstrumentId": instrument["paymentInstrumentId"],
        "transactionHash": transaction,
        "amountBaseUnits": min(amounts),
        "httpStatus": paid_status,
    }
    return evidence, trace
