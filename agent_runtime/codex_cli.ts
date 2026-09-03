import { generateCrawler, repairCrawler, type CrawlRequest } from "./codex_crawler.js";

async function readInput(): Promise<Record<string, unknown>> {
  const chunks: Buffer[] = [];
  for await (const chunk of process.stdin) {
    chunks.push(Buffer.from(chunk));
  }
  const text = Buffer.concat(chunks).toString("utf8").trim();
  if (!text) {
    throw new Error("Codex worker requires a JSON request on stdin");
  }
  return JSON.parse(text) as Record<string, unknown>;
}

async function main(): Promise<void> {
  const input = await readInput();
  const action = String(input.action ?? "generate");
  if (action === "generate") {
    const request = input.request as unknown as CrawlRequest;
    process.stdout.write(JSON.stringify(await generateCrawler(request)));
    return;
  }
  if (action === "repair") {
    const threadId = String(input.threadId ?? "");
    const failureLog = String(input.failureLog ?? "");
    if (!threadId) {
      throw new Error("repair requires threadId");
    }
    process.stdout.write(JSON.stringify(await repairCrawler(threadId, failureLog)));
    return;
  }
  throw new Error(`Unsupported Codex worker action: ${action}`);
}

main().catch((error: unknown) => {
  const message = error instanceof Error ? error.message : String(error);
  process.stderr.write(`${message}\n`);
  process.exitCode = 1;
});
