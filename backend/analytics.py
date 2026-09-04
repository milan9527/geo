from __future__ import annotations

import base64
import hashlib
import math
from collections.abc import Iterable


AGENT_PATTERNS = {
    "OpenAI Search Crawler": ("oai-searchbot",),
    "OpenAI Crawler": ("gptbot", "chatgpt-user", "openai"),
    "ClaudeBot": ("claudebot", "claude-web", "anthropic-ai"),
    "PerplexityBot": ("perplexitybot", "perplexity-user"),
    "Google-Extended": ("google-extended", "gemini"),
    "Google Search Crawler": ("googlebot",),
    "Microsoft Search Crawler": ("bingbot", "adidxbot"),
    "Applebot": ("applebot",),
    "Amazonbot": ("amazonbot",),
    "Meta External Agent": ("meta-externalagent", "facebookbot"),
    "ByteDance Spider": ("bytespider",),
    "You.com Crawler": ("youbot",),
    "DuckDuckGo Crawler": ("duckduckbot",),
    "Baidu Spider": ("baiduspider",),
    "Yandex Crawler": ("yandexbot",),
    "Common Crawl": ("ccbot",),
}

HLL_PRECISION = 10
HLL_REGISTER_COUNT = 1 << HLL_PRECISION


def identify_visitor(user_agent: str) -> tuple[str, str | None]:
    normalized = user_agent.lower()
    for name, patterns in AGENT_PATTERNS.items():
        if any(pattern in normalized for pattern in patterns):
            return "agent", name
    return "human", None


def empty_hll() -> bytearray:
    return bytearray(HLL_REGISTER_COUNT)


def hll_add(registers: bytearray, fingerprint: bytes) -> None:
    digest = hashlib.sha256(fingerprint).digest()
    value = int.from_bytes(digest[:8], "big")
    index = value >> (64 - HLL_PRECISION)
    remainder_bits = 64 - HLL_PRECISION
    remainder = value & ((1 << remainder_bits) - 1)
    rank = (
        remainder_bits + 1
        if remainder == 0
        else remainder_bits - remainder.bit_length() + 1
    )
    registers[index] = max(registers[index], rank)


def hll_merge(target: bytearray, sources: Iterable[bytes | bytearray]) -> bytearray:
    for source in sources:
        if len(source) != HLL_REGISTER_COUNT:
            continue
        for index, rank in enumerate(source):
            if rank > target[index]:
                target[index] = rank
    return target


def hll_count(registers: bytes | bytearray) -> int:
    if len(registers) != HLL_REGISTER_COUNT:
        return 0
    register_count = float(HLL_REGISTER_COUNT)
    alpha = 0.7213 / (1 + 1.079 / register_count)
    inverse_sum = sum(2.0 ** -rank for rank in registers)
    estimate = alpha * register_count * register_count / inverse_sum
    zero_count = registers.count(0)
    if estimate <= 2.5 * register_count and zero_count:
        estimate = register_count * math.log(register_count / zero_count)
    return max(0, int(round(estimate)))


def encode_hll(registers: bytes | bytearray) -> str:
    return base64.b64encode(bytes(registers)).decode("ascii")


def decode_hll(value: object) -> bytearray:
    try:
        decoded = base64.b64decode(str(value or ""), validate=True)
    except (ValueError, TypeError):
        return empty_hll()
    return bytearray(decoded) if len(decoded) == HLL_REGISTER_COUNT else empty_hll()
