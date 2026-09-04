FROM public.ecr.aws/docker/library/node:24-alpine AS codex-builder

WORKDIR /build
COPY agent_runtime/package.json agent_runtime/package-lock.json ./
RUN npm ci
COPY agent_runtime/tsconfig.json agent_runtime/codex_crawler.ts agent_runtime/codex_cli.ts agent_runtime/browser_cli.ts ./
RUN npm run build \
    && npm prune --omit=dev --no-audit --no-fund --ignore-scripts

FROM public.ecr.aws/docker/library/python:3.13-alpine@sha256:7415fbc3c9e4979cc717d92377ab2bc7b2b4a2af1ac03cc52b5f3f88efedaf3a

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    CODEX_HOME=/tmp/codex

WORKDIR /app
RUN apk add --no-cache nodejs
COPY aws_runtime/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY aws_runtime/app.py .
COPY aws_runtime/crawler_tools.py .
COPY --from=codex-builder /build/dist ./agent_runtime/dist
COPY --from=codex-builder /build/node_modules ./agent_runtime/node_modules
COPY --from=codex-builder /build/package.json ./agent_runtime/package.json

RUN addgroup -S -g 10001 agentcore \
    && adduser -S -D -H -u 10001 -G agentcore agentcore \
    && mkdir -p /tmp/codex /tmp/geo-crawler-workspace \
    && chown -R agentcore:agentcore /app /tmp/codex /tmp/geo-crawler-workspace
USER agentcore

EXPOSE 8080
CMD ["python", "app.py"]
