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
if [ -f ".env.deploy.aws" ]; then
  source .env.deploy.aws
fi
set +a

DEPLOY_REGION="${AWS_REGION:-us-east-1}"
ECR_REPOSITORY="${GEO_API_ECR_REPOSITORY:-geo-intelligence-api}"
ECS_CLUSTER="${GEO_ECS_CLUSTER:-geo-intelligence}"
ECS_SERVICE="${GEO_ECS_SERVICE:-geo-intelligence-api}"
ECS_CONTAINER="${GEO_ECS_CONTAINER:-api}"
PUBLIC_BUCKET="${GEO_PUBLIC_BUCKET:-}"
ADMIN_BUCKET="${GEO_ADMIN_BUCKET:-}"
PUBLIC_DISTRIBUTION="${GEO_PUBLIC_DISTRIBUTION_ID:-}"
ADMIN_DISTRIBUTION="${GEO_ADMIN_DISTRIBUTION_ID:-}"

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

if [ -z "$PUBLIC_BUCKET" ]; then
  PUBLIC_BUCKET="geo-intelligence-public-${ACCOUNT_ID}-${DEPLOY_REGION}"
fi
if [ -z "$ADMIN_BUCKET" ]; then
  ADMIN_BUCKET="geo-intelligence-admin-${ACCOUNT_ID}-${DEPLOY_REGION}"
fi
if [ -z "$PUBLIC_DISTRIBUTION" ]; then
  PUBLIC_DISTRIBUTION="$(
    aws cloudfront list-distributions \
      --query "DistributionList.Items[?Comment=='Aperture GEO public site - S3 OAC and ECS API'].Id | [0]" \
      --output text
  )"
fi
if [ -z "$ADMIN_DISTRIBUTION" ]; then
  ADMIN_DISTRIBUTION="$(
    aws cloudfront list-distributions \
      --query "DistributionList.Items[?Comment=='Aperture GEO admin site - S3 OAC and ECS API'].Id | [0]" \
      --output text
  )"
fi

for required_value in \
  "$PUBLIC_BUCKET" \
  "$ADMIN_BUCKET" \
  "$PUBLIC_DISTRIBUTION" \
  "$ADMIN_DISTRIBUTION"; do
  if [ -z "$required_value" ] || [ "$required_value" = "None" ]; then
    echo "Existing S3/CloudFront deployment resources could not be discovered." >&2
    exit 1
  fi
done

aws s3api head-bucket --bucket "$PUBLIC_BUCKET"
aws s3api head-bucket --bucket "$ADMIN_BUCKET"
aws cloudfront get-distribution --id "$PUBLIC_DISTRIBUTION" >/dev/null
aws cloudfront get-distribution --id "$ADMIN_DISTRIBUTION" >/dev/null
aws ecs describe-services \
  --region "$DEPLOY_REGION" \
  --cluster "$ECS_CLUSTER" \
  --services "$ECS_SERVICE" \
  --query 'services[0].serviceName' \
  --output text |
  grep -qx "$ECS_SERVICE"

if ! aws ecr describe-repositories \
  --region "$DEPLOY_REGION" \
  --repository-names "$ECR_REPOSITORY" >/dev/null 2>&1; then
  aws ecr create-repository \
    --region "$DEPLOY_REGION" \
    --repository-name "$ECR_REPOSITORY" \
    --image-scanning-configuration scanOnPush=true \
    --image-tag-mutability IMMUTABLE >/dev/null
else
  aws ecr put-image-scanning-configuration \
    --region "$DEPLOY_REGION" \
    --repository-name "$ECR_REPOSITORY" \
    --image-scanning-configuration scanOnPush=true >/dev/null
fi

aws ecr get-login-password --region "$DEPLOY_REGION" |
  docker login --username AWS --password-stdin "$REGISTRY"

docker build \
  --platform linux/arm64 \
  --file Dockerfile.backend \
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
  echo "Image scan found ${BLOCKING_FINDINGS} Critical/High findings; ECS was not updated." >&2
  exit 1
fi

CURRENT_TASK_FILE="${DEPLOY_TEMP_DIR}/current-task.json"
NEXT_TASK_FILE="${DEPLOY_TEMP_DIR}/next-task.json"
aws ecs describe-task-definition \
  --region "$DEPLOY_REGION" \
  --task-definition "$ECS_SERVICE" \
  --query taskDefinition \
  --output json >"$CURRENT_TASK_FILE"

jq \
  --arg image "$IMAGE_URI" \
  --arg container "$ECS_CONTAINER" \
  '
    del(
      .taskDefinitionArn,
      .revision,
      .status,
      .requiresAttributes,
      .compatibilities,
      .registeredAt,
      .registeredBy,
      .deregisteredAt
    )
    | .containerDefinitions = (
        .containerDefinitions
        | map(if .name == $container then .image = $image else . end)
      )
  ' \
  "$CURRENT_TASK_FILE" >"$NEXT_TASK_FILE"

NEXT_TASK_ARN="$(
  aws ecs register-task-definition \
    --region "$DEPLOY_REGION" \
    --cli-input-json "file://${NEXT_TASK_FILE}" \
    --query 'taskDefinition.taskDefinitionArn' \
    --output text
)"

aws ecs update-service \
  --region "$DEPLOY_REGION" \
  --cluster "$ECS_CLUSTER" \
  --service "$ECS_SERVICE" \
  --task-definition "$NEXT_TASK_ARN" \
  --force-new-deployment >/dev/null
aws ecs wait services-stable \
  --region "$DEPLOY_REGION" \
  --cluster "$ECS_CLUSTER" \
  --services "$ECS_SERVICE"

aws s3 sync frontend/public "s3://${PUBLIC_BUCKET}" \
  --region "$DEPLOY_REGION" \
  --delete \
  --exclude index.html \
  --cache-control 'public,max-age=300'
aws s3 cp frontend/public/index.html "s3://${PUBLIC_BUCKET}/index.html" \
  --region "$DEPLOY_REGION" \
  --content-type text/html \
  --cache-control 'no-cache'

aws s3 sync frontend/admin "s3://${ADMIN_BUCKET}" \
  --region "$DEPLOY_REGION" \
  --delete \
  --exclude index.html \
  --cache-control 'public,max-age=300'
aws s3 cp frontend/admin/index.html "s3://${ADMIN_BUCKET}/index.html" \
  --region "$DEPLOY_REGION" \
  --content-type text/html \
  --cache-control 'no-cache'

aws cloudfront create-invalidation \
  --distribution-id "$PUBLIC_DISTRIBUTION" \
  --paths '/*' >/dev/null
aws cloudfront create-invalidation \
  --distribution-id "$ADMIN_DISTRIBUTION" \
  --paths '/*' >/dev/null

PUBLIC_DOMAIN="$(
  aws cloudfront get-distribution \
    --id "$PUBLIC_DISTRIBUTION" \
    --query 'Distribution.DomainName' \
    --output text
)"
ADMIN_DOMAIN="$(
  aws cloudfront get-distribution \
    --id "$ADMIN_DISTRIBUTION" \
    --query 'Distribution.DomainName' \
    --output text
)"

curl --fail --silent --show-error "https://${PUBLIC_DOMAIN}/api/health" >/dev/null
curl --fail --silent --show-error "https://${PUBLIC_DOMAIN}/" >/dev/null
curl --fail --silent --show-error "https://${ADMIN_DOMAIN}/" >/dev/null

printf 'Backend image: %s\n' "$IMAGE_URI"
printf 'Task definition: %s\n' "$NEXT_TASK_ARN"
printf 'Public site: https://%s\n' "$PUBLIC_DOMAIN"
printf 'Admin site: https://%s\n' "$ADMIN_DOMAIN"
