#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

if [ ! -f ".env.aws" ]; then
  echo ".env.aws is required." >&2
  exit 1
fi

set -a
source .env.aws
set +a

DEPLOY_REGION="${AWS_REGION:-us-east-1}"
RUNTIME_ARN="${AGENTCORE_RUNTIME_ARN:?AGENTCORE_RUNTIME_ARN is required}"
RUNTIME_ID="${RUNTIME_ARN##*/}"
ECR_REPOSITORY="${GEO_AGENT_ECR_REPOSITORY:-geo-intelligence-agent}"
DEPLOY_TEMP_DIR="$(mktemp -d)"
DEPLOY_DOCKER_CONFIG="${DEPLOY_TEMP_DIR}/docker"
mkdir -p "$DEPLOY_DOCKER_CONFIG"
export DOCKER_CONFIG="$DEPLOY_DOCKER_CONFIG"

cleanup() {
  rm -rf "$DEPLOY_TEMP_DIR"
}
trap cleanup EXIT

ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
REGISTRY="${ACCOUNT_ID}.dkr.ecr.${DEPLOY_REGION}.amazonaws.com"
IMAGE_TAG="$(date -u +%Y%m%d%H%M%S)-$(git rev-parse --short HEAD 2>/dev/null || printf 'working')"
IMAGE_URI="${REGISTRY}/${ECR_REPOSITORY}:${IMAGE_TAG}"

aws ecr get-login-password --region "$DEPLOY_REGION" |
  docker login --username AWS --password-stdin "$REGISTRY"

docker build \
  --platform linux/arm64 \
  --file aws_runtime/Dockerfile \
  --tag "$IMAGE_URI" \
  .
docker push "$IMAGE_URI"

SCAN_FILE="${DEPLOY_TEMP_DIR}/scan.json"
for _ in $(seq 1 30); do
  SCAN_STATUS="$(
    aws ecr describe-image-scan-findings \
      --region "$DEPLOY_REGION" \
      --repository-name "$ECR_REPOSITORY" \
      --image-id imageTag="$IMAGE_TAG" \
      --query 'imageScanStatus.status' \
      --output text 2>/dev/null || true
  )"
  if [ "$SCAN_STATUS" = "COMPLETE" ]; then
    break
  fi
  sleep 5
done

aws ecr describe-image-scan-findings \
  --region "$DEPLOY_REGION" \
  --repository-name "$ECR_REPOSITORY" \
  --image-id imageTag="$IMAGE_TAG" \
  --output json >"$SCAN_FILE"

BLOCKING_FINDINGS="$(
  jq '[.imageScanFindings.findings[]? | select(.severity == "CRITICAL" or .severity == "HIGH")] | length' \
    "$SCAN_FILE"
)"
if [ "$BLOCKING_FINDINGS" -ne 0 ]; then
  echo "Runtime image scan found ${BLOCKING_FINDINGS} Critical/High findings." >&2
  exit 1
fi

IMAGE_DIGEST="$(
  aws ecr describe-images \
    --region "$DEPLOY_REGION" \
    --repository-name "$ECR_REPOSITORY" \
    --image-ids imageTag="$IMAGE_TAG" \
    --query 'imageDetails[0].imageDigest' \
    --output text
)"
PINNED_IMAGE_URI="${REGISTRY}/${ECR_REPOSITORY}@${IMAGE_DIGEST}"

CURRENT_FILE="${DEPLOY_TEMP_DIR}/current-runtime.json"
UPDATE_FILE="${DEPLOY_TEMP_DIR}/update-runtime.json"
aws bedrock-agentcore-control get-agent-runtime \
  --region "$DEPLOY_REGION" \
  --agent-runtime-id "$RUNTIME_ID" \
  --output json >"$CURRENT_FILE"

jq \
  --arg runtimeId "$RUNTIME_ID" \
  --arg image "$PINNED_IMAGE_URI" \
  '{
    agentRuntimeId: $runtimeId,
    agentRuntimeArtifact: {
      containerConfiguration: {containerUri: $image}
    },
    roleArn,
    networkConfiguration,
    description,
    protocolConfiguration,
    lifecycleConfiguration,
    environmentVariables
  }' \
  "$CURRENT_FILE" >"$UPDATE_FILE"

aws bedrock-agentcore-control update-agent-runtime \
  --region "$DEPLOY_REGION" \
  --cli-input-json "file://${UPDATE_FILE}" >/dev/null

for _ in $(seq 1 90); do
  STATUS="$(
    aws bedrock-agentcore-control get-agent-runtime \
      --region "$DEPLOY_REGION" \
      --agent-runtime-id "$RUNTIME_ID" \
      --query status \
      --output text
  )"
  if [ "$STATUS" = "READY" ]; then
    break
  fi
  if [ "$STATUS" = "CREATE_FAILED" ] || [ "$STATUS" = "UPDATE_FAILED" ]; then
    echo "AgentCore Runtime update failed with status ${STATUS}." >&2
    exit 1
  fi
  sleep 10
done

RUNTIME_RESULT="$(
  aws bedrock-agentcore-control get-agent-runtime \
    --region "$DEPLOY_REGION" \
    --agent-runtime-id "$RUNTIME_ID" \
    --query '{version:agentRuntimeVersion,status:status,image:agentRuntimeArtifact.containerConfiguration.containerUri}' \
    --output json
)"

printf 'Runtime image: %s\n' "$PINNED_IMAGE_URI"
printf 'Runtime result: %s\n' "$RUNTIME_RESULT"
