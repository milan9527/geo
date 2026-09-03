import { chromium, type Page } from "playwright-core";

type BrowserSource = {
  publisher: string;
  url: string;
  sourceType: string;
  maxItems?: number;
  renderWaitMs?: number;
};

type BrowserRequest = {
  wsUrl: string;
  headers: Record<string, string>;
  sources: BrowserSource[];
};

function now(): string {
  return new Date().toISOString().replace(/\.\d{3}Z$/, "+00:00");
}

function clean(value: string): string {
  return value.replace(/\s+/g, " ").trim();
}

async function bodyText(page: Page): Promise<string> {
  for (const selector of ["article", "main", "[role=main]", "body"]) {
    const value = await page
      .locator(selector)
      .first()
      .innerText({ timeout: 3_000 })
      .catch(() => "");
    if (clean(value).length >= 120) return clean(value);
  }
  return "";
}

async function metaContent(page: Page, selectors: string[]): Promise<string> {
  return page.evaluate((items) => {
    for (const selector of items) {
      const value = document.querySelector(selector)?.getAttribute("content");
      if (value) return value.trim();
    }
    return "";
  }, selectors);
}

async function candidateLinks(
  page: Page,
  baseUrl: string,
  limit: number,
): Promise<Array<{ title: string; url: string }>> {
  const base = new URL(baseUrl);
  const anchors = await page.locator("a[href]").evaluateAll((nodes) =>
    nodes.slice(0, 500).map((node) => ({
      text: (node.textContent || "").replace(/\s+/g, " ").trim(),
      href: (node as HTMLAnchorElement).href || node.getAttribute("href") || "",
    })),
  );
  const seen = new Set<string>();
  const result: Array<{ title: string; url: string }> = [];
  for (const anchor of anchors) {
    if (anchor.text.length < 20) continue;
    let url: URL;
    try {
      url = new URL(anchor.href, baseUrl);
    } catch {
      continue;
    }
    url.hash = "";
    if (url.protocol !== "https:" || url.hostname !== base.hostname) continue;
    if (url.href.replace(/\/$/, "") === base.href.replace(/\/$/, "")) continue;
    if (seen.has(url.href)) continue;
    seen.add(url.href);
    result.push({ title: anchor.text.slice(0, 300), url: url.href });
    if (result.length >= limit) break;
  }
  return result;
}

async function readInput(): Promise<BrowserRequest> {
  const chunks: Buffer[] = [];
  for await (const chunk of process.stdin) chunks.push(Buffer.from(chunk));
  return JSON.parse(Buffer.concat(chunks).toString("utf8")) as BrowserRequest;
}

async function main(): Promise<void> {
  const input = await readInput();
  const browser = await chromium.connectOverCDP(input.wsUrl, {
    headers: input.headers,
  });
  const context = browser.contexts()[0];
  const pages = context.pages();
  const page = pages[0] ?? (await context.newPage());
  const evidence: Array<Record<string, unknown>> = [];
  const pageRecords: Array<Record<string, unknown>> = [];
  try {
    for (const source of input.sources) {
      const response = await page.goto(source.url, {
        waitUntil: "domcontentloaded",
        timeout: 45_000,
      });
      await page.waitForTimeout(source.renderWaitMs ?? 1_800);
      let links = await candidateLinks(page, source.url, source.maxItems ?? 3);
      if (!links.length) links = [{ title: await page.title(), url: page.url() }];
      for (const candidate of links) {
        let detailResponse = response;
        if (candidate.url !== page.url()) {
          detailResponse = await page
            .goto(candidate.url, {
              waitUntil: "domcontentloaded",
              timeout: 45_000,
            })
            .catch(() => null);
          if (!detailResponse) continue;
          await page.waitForTimeout(900);
        }
        const body = await bodyText(page);
        if (!body) continue;
        const title = clean((await page.title()) || candidate.title);
        const publishedAt =
          (await metaContent(page, [
            "meta[property='article:published_time']",
            "meta[name='date']",
            "meta[name='publish-date']",
            "meta[itemprop='datePublished']",
          ])) || now().slice(0, 10);
        const item = {
          publisher: source.publisher,
          title: title.slice(0, 500),
          url: page.url(),
          publishedAt,
          retrievedAt: now(),
          sourceType: source.sourceType,
          excerpt: body.slice(0, 3_000),
          data: {
            httpStatus: detailResponse?.status() ?? null,
            rendered: true,
            textLength: body.length,
          },
        };
        evidence.push(item);
        pageRecords.push({
          url: item.url,
          status: item.data.httpStatus,
          title: item.title.slice(0, 200),
          textLength: item.data.textLength,
        });
        if (evidence.length >= 10) break;
      }
      if (evidence.length >= 10) break;
    }
  } finally {
    await browser.close();
  }
  process.stdout.write(JSON.stringify({ evidence, pages: pageRecords }));
}

main().catch((error: unknown) => {
  process.stderr.write(`${error instanceof Error ? error.stack : String(error)}\n`);
  process.exitCode = 1;
});
