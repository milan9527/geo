/**
 * Codex SDK worker used by the AgentCore Runtime.
 *
 * Codex uses the Runtime task role through its Amazon Bedrock provider. It
 * generates crawler source in an isolated workspace; execution happens later
 * inside AgentCore Code Interpreter.
 */
import { Codex } from "@openai/codex-sdk";

export type CrawlStyle =
  | "research"
  | "browser-rendered"
  | "financial-timeseries"
  | "evidence-verification";

export interface CrawlRequest {
  domain: string;
  style: CrawlStyle;
  allowedDomains: string[];
  requiredFields: string[];
  sampleUrls: string[];
  sources: Array<{
    publisher: string;
    url: string;
    maxItems: number;
    requestPolicy: {
      userAgent?: string;
      requestsPerSecond?: number;
      cacheTtlSeconds?: number;
      maxRetries?: number;
      retryStatusCodes?: number[];
      maxRetryAfterSeconds?: number;
    };
  }>;
  robotsPolicy: string;
}

export interface CrawlArtifact {
  threadId: string;
  sourceCode: string;
  testPlan: string;
  safetyNotes: string[];
  usage: {
    inputTokens: number;
    outputTokens: number;
  };
}

const BEDROCK_MODEL =
  process.env.BEDROCK_CODEX_MODEL ?? "openai.gpt-5.6-sol";
const AWS_REGION = process.env.AWS_REGION ?? "us-east-1";

const codex = new Codex({
  config: {
    model_provider: "amazon-bedrock",
    model_providers: {
      "amazon-bedrock": {
        aws: { region: AWS_REGION },
      },
    },
    approval_policy: "never",
    sandbox_mode: "workspace-write",
    sandbox_workspace_write: { network_access: false },
  },
});

function buildPrompt(request: CrawlRequest): string {
  return `
Create a self-contained Python crawler for an isolated AgentCore Code Interpreter.

Target domain: ${request.domain}
Crawler style: ${request.style}
Allowed network domains: ${request.allowedDomains.join(", ")}
Required output fields: ${request.requiredFields.join(", ")}
Sample URLs: ${request.sampleUrls.join(", ")}
Per-source request policies: ${JSON.stringify(request.sources)}
Robots policy: ${request.robotsPolicy}

Requirements:
- Respect robots policy, rate limits, authentication boundaries, and terms.
- Use urllib.request.urlopen for every network request. Runtime wraps this
  function to enforce the registered User-Agent, per-host rate limit,
  Retry-After handling, retry cap, and URL allowlist. Do not create a custom
  opener or use another HTTP client.
- Fetch each sample URL at most once unless the Runtime wrapper retries a
  configured 429/503 response. Do not crawl links discovered in a response.
- Do not attempt CAPTCHA bypass, credential discovery, or access-control bypass.
- Use only Python standard-library modules available in the sandbox.
- Fetch only the exact HTTPS sample URLs and allowed domains listed above.
- Do not use subprocess, shell commands, sockets, eval, exec, local credentials,
  cloud metadata endpoints, or filesystem paths outside /tmp.
- Emit exactly one JSON object on stdout with an "evidence" array.
- Every evidence item must preserve publisher, title, url, publishedAt,
  retrievedAt, sourceType, excerpt, and data.
- Cap response size, retries, redirects, and total downloaded bytes.
- For financial-timeseries, calculate changes from fetched observations.
- Include deterministic tests using saved fixtures.

Return exactly these sections:
SOURCE_CODE
<plain Python source without Markdown fences>
TEST_PLAN
<plain text>
SAFETY_NOTES
<one item per line>
`;
}

function parseArtifact(
  threadId: string,
  text: string,
  usage?: { input_tokens: number; output_tokens: number } | null,
): CrawlArtifact {
  const section = (name: string, next?: string): string => {
    const heading = String.raw`(?:^|\n)(?:\d+\.\s*)?${name}\s*\n`;
    const ending = next
      ? String.raw`(?=\n(?:\d+\.\s*)?${next}\s*\n)`
      : String.raw`$`;
    const match = text.match(new RegExp(`${heading}([\\s\\S]*?)${ending}`, "m"));
    return (match?.[1] ?? "")
      .replace(/^```(?:python|text)?\s*/i, "")
      .replace(/\s*```$/, "")
      .trim();
  };

  return {
    threadId,
    sourceCode: section("SOURCE_CODE", "TEST_PLAN"),
    testPlan: section("TEST_PLAN", "SAFETY_NOTES"),
    safetyNotes: section("SAFETY_NOTES")
      .split("\n")
      .map((line) => line.replace(/^[-*]\s*/, "").trim())
      .filter(Boolean),
    usage: {
      inputTokens: usage?.input_tokens ?? 0,
      outputTokens: usage?.output_tokens ?? 0,
    },
  };
}

export async function generateCrawler(request: CrawlRequest): Promise<CrawlArtifact> {
  const thread = codex.startThread({
    workingDirectory: "/tmp/geo-crawler-workspace",
    skipGitRepoCheck: true,
    model: BEDROCK_MODEL,
    sandboxMode: "workspace-write",
    networkAccessEnabled: false,
    approvalPolicy: "never",
    modelReasoningEffort: "high",
  });
  const result = await thread.run(buildPrompt(request));
  const finalResponse = result.finalResponse;
  const threadId = thread.id;

  if (!finalResponse) {
    throw new Error("Codex did not return a crawler artifact");
  }
  if (!threadId) {
    throw new Error("Codex did not persist a resumable thread id");
  }

  return parseArtifact(threadId, finalResponse, result.usage);
}

export async function repairCrawler(
  threadId: string,
  failureLog: string,
): Promise<CrawlArtifact> {
  const thread = codex.resumeThread(threadId, {
    workingDirectory: "/tmp/geo-crawler-workspace",
    skipGitRepoCheck: true,
    model: BEDROCK_MODEL,
    sandboxMode: "workspace-write",
    networkAccessEnabled: false,
    approvalPolicy: "never",
    modelReasoningEffort: "high",
  });
  const result = await thread.run(`
The crawler failed in AgentCore Code Interpreter.
Repair only the necessary code and preserve all safety constraints.
Use only urllib.request.urlopen for network requests. Do not use requests,
urllib3, http.client, build_opener, or a custom opener. Runtime wraps urlopen
to enforce the exact URL allowlist, User-Agent, rate limits, and retries.

  Failure log:
${failureLog.slice(0, 12_000)}

Return exactly:
SOURCE_CODE
<plain Python source without Markdown fences>
TEST_PLAN
<plain text>
SAFETY_NOTES
<one item per line>
`);
  const finalResponse = result.finalResponse;

  if (!finalResponse) {
    throw new Error("Codex did not return a repaired artifact");
  }

  return parseArtifact(threadId, finalResponse, result.usage);
}
