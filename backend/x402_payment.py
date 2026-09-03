from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, urlparse

from x402 import x402ResourceServerSync
from x402.http import (
    HTTPFacilitatorClientSync,
    HTTPRequestContext,
    HTTPResponseInstructions,
    x402HTTPResourceServerSync,
)
from x402.http.types import HTTPTransportContext
from x402.mechanisms.evm.exact import ExactEvmServerScheme


X402_ENABLED = os.environ.get("X402_ENABLED", "true").lower() in {
    "1",
    "true",
    "yes",
}
X402_FACILITATOR_URL = os.environ.get(
    "X402_FACILITATOR_URL",
    "https://x402.org/facilitator",
)
X402_NETWORK = os.environ.get("X402_NETWORK", "eip155:84532")
X402_PAY_TO_ADDRESS = os.environ.get("X402_PAY_TO_ADDRESS", "")
X402_DEFAULT_PRICE_USD = float(os.environ.get("X402_DEFAULT_PRICE_USD", "0.002"))
X402_PUBLIC_BASE_URL = os.environ.get("X402_PUBLIC_BASE_URL", "").rstrip("/")

_server: x402ResourceServerSync | None = None
_server_lock = threading.Lock()


class X402ConfigurationError(RuntimeError):
    pass


class HandlerAdapter:
    def __init__(self, handler: Any, path: str) -> None:
        self.handler = handler
        self.path = path
        self.parsed = urlparse(handler.path)

    def get_header(self, name: str) -> str | None:
        return self.handler.headers.get(name)

    def get_method(self) -> str:
        return self.handler.command

    def get_path(self) -> str:
        return self.path

    def get_url(self) -> str:
        if X402_PUBLIC_BASE_URL:
            return f"{X402_PUBLIC_BASE_URL}{self.path}"
        scheme = self.handler.headers.get("X-Forwarded-Proto", "https")
        host = self.handler.headers.get("X-Forwarded-Host") or self.handler.headers.get(
            "Host", "localhost"
        )
        return f"{scheme}://{host}{self.path}"

    def get_accept_header(self) -> str:
        return self.handler.headers.get("Accept", "")

    def get_user_agent(self) -> str:
        return self.handler.headers.get("User-Agent", "")

    def get_query_params(self) -> dict[str, str | list[str]]:
        values = parse_qs(self.parsed.query)
        return {
            key: items[0] if len(items) == 1 else items
            for key, items in values.items()
        }

    def get_query_param(self, name: str) -> str | list[str] | None:
        return self.get_query_params().get(name)

    def get_body(self) -> None:
        return None


@dataclass
class X402Request:
    http_server: x402HTTPResourceServerSync
    context: HTTPRequestContext
    process_result: Any
    price_usd: float


def paid_price(configured_price: float | int | None) -> float:
    value = float(configured_price or 0)
    return value if value > 0 else X402_DEFAULT_PRICE_USD


def _resource_server() -> x402ResourceServerSync:
    global _server
    if _server is not None:
        return _server
    with _server_lock:
        if _server is not None:
            return _server
        if not X402_ENABLED:
            raise X402ConfigurationError("x402 seller service is disabled")
        if not X402_PAY_TO_ADDRESS:
            raise X402ConfigurationError("X402_PAY_TO_ADDRESS is not configured")
        facilitator = HTTPFacilitatorClientSync(
            {"url": X402_FACILITATOR_URL}
        )
        server = x402ResourceServerSync(facilitator)
        server.register("eip155:*", ExactEvmServerScheme())
        server.initialize()
        _server = server
        return server


def process_paid_request(
    handler: Any,
    *,
    path: str,
    title: str,
    configured_price: float | int | None,
) -> X402Request:
    price_usd = paid_price(configured_price)
    server = _resource_server()
    routes = {
        f"GET {path}": {
            "accepts": {
                "scheme": "exact",
                "payTo": X402_PAY_TO_ADDRESS,
                "price": f"${price_usd:.6f}".rstrip("0").rstrip("."),
                "network": X402_NETWORK,
            },
            "resource": HandlerAdapter(handler, path).get_url(),
            "description": f"Aperture GEO 深度分析：{title}",
            "mimeType": "application/json",
            "serviceName": "Aperture GEO",
            "tags": ["GEO", "AI research", "paid analysis"],
        }
    }
    http_server = x402HTTPResourceServerSync(server, routes)
    adapter = HandlerAdapter(handler, path)
    context = HTTPRequestContext(
        adapter=adapter,
        path=path,
        method="GET",
        payment_header=(
            adapter.get_header("PAYMENT-SIGNATURE")
            or adapter.get_header("X-PAYMENT")
        ),
    )
    return X402Request(
        http_server=http_server,
        context=context,
        process_result=http_server.process_http_request(context),
        price_usd=price_usd,
    )


def settle_paid_request(
    request: X402Request,
    *,
    response_body: Any,
) -> Any:
    result = request.process_result
    transport_context = HTTPTransportContext(
        request=request.context,
        response_body=response_body,
        response_headers={"Content-Type": "application/json; charset=utf-8"},
    )
    return request.http_server.process_settlement(
        result.payment_payload,
        result.payment_requirements,
        context=request.context,
        declared_extensions=result.declared_extensions,
        transport_context=transport_context,
        before_handler_settlement=result.before_handler_settlement,
    )


def error_instructions(error: Exception) -> HTTPResponseInstructions:
    return HTTPResponseInstructions(
        status=503,
        headers={"Cache-Control": "no-store"},
        body={
            "error": "x402 seller service unavailable",
            "details": type(error).__name__,
        },
    )
