/**
 * Production integration skeleton.
 *
 * This worker is intentionally separate from the zero-dependency dashboard.
 * Install `@openai/codex-sdk` in the AgentCore Runtime image before use.
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
  robotsPolicy: string;
}

export interface CrawlArtifact {
  threadId: string;
  sourceCode: string;
  testPlan: string;
  safetyNotes: string[];
}

const BEDROCK_BASE_URL =
  process.env.BEDROCK_OPENAI_BASE_URL ??
  "https://bedrock-runtime.us-east-1.amazonaws.com/openai/v1";
const BEDROCK_MODEL =
  process.env.BEDROCK_CODEX_MODEL ?? "us.openai.gpt-5.6-sol";

const codex = new Codex({
  apiKey: process.env.BEDROCK_API_KEY,
  baseUrl: BEDROCK_BASE_URL,
});

function buildPrompt(request: CrawlRequest): string {
  return `
Create a site-specific crawler for an isolated AgentCore Code Interpreter.

Target domain: ${request.domain}
Crawler style: ${request.style}
Allowed network domains: ${request.allowedDomains.join(", ")}
Required output fields: ${request.requiredFields.join(", ")}
Sample URLs: ${request.sampleUrls.join(", ")}
Robots policy: ${request.robotsPolicy}

Requirements:
- Respect robots policy, rate limits, authentication boundaries, and terms.
- Do not attempt CAPTCHA bypass, credential discovery, or access-control bypass.
- Emit newline-delimited JSON and preserve source URL, title, published_at,
  fetched_at, author, body, structured_data, and evidence offsets.
- Cap response size, retries, redirects, and total downloaded bytes.
- Include deterministic tests using saved fixtures.

Return exactly these sections:
1. SOURCE_CODE
2. TEST_PLAN
3. SAFETY_NOTES
`;
}

function parseArtifact(threadId: string, text: string): CrawlArtifact {
  const section = (name: string, next?: string): string => {
    const start = text.indexOf(`${name}\n`);
    if (start < 0) return "";
    const bodyStart = start + name.length + 1;
    const end = next ? text.indexOf(`\n${next}\n`, bodyStart) : text.length;
    return text.slice(bodyStart, end < 0 ? text.length : end).trim();
  };

  return {
    threadId,
    sourceCode: section("SOURCE_CODE", "TEST_PLAN"),
    testPlan: section("TEST_PLAN", "SAFETY_NOTES"),
    safetyNotes: section("SAFETY_NOTES")
      .split("\n")
      .map((line) => line.replace(/^[-*]\s*/, "").trim())
      .filter(Boolean),
  };
}

export async function generateCrawler(request: CrawlRequest): Promise<CrawlArtifact> {
  const thread = codex.startThread({
    workingDirectory: "/tmp/geo-crawler-workspace",
    skipGitRepoCheck: true,
    model: BEDROCK_MODEL,
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

  return parseArtifact(threadId, finalResponse);
}

export async function repairCrawler(
  threadId: string,
  failureLog: string,
): Promise<CrawlArtifact> {
  const thread = codex.resumeThread(threadId);
  const result = await thread.run(`
The crawler failed in AgentCore Code Interpreter.
Repair only the necessary code and preserve all safety constraints.

  Failure log:
${failureLog.slice(0, 12_000)}

Return exactly:
1. SOURCE_CODE
2. TEST_PLAN
3. SAFETY_NOTES
`);
  const finalResponse = result.finalResponse;

  if (!finalResponse) {
    throw new Error("Codex did not return a repaired artifact");
  }

  return parseArtifact(threadId, finalResponse);
}
