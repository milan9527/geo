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
STACK_NAME="${GEO_WEB_STACK_NAME:-geo-intelligence-web}"
ECR_REPOSITORY="${GEO_API_ECR_REPOSITORY:-geo-intelligence-api}"
DESIRED_COUNT="${GEO_API_DESIRED_COUNT:-1}"
DEPLOY_DOCKER_CONFIG="$(mktemp -d)"
export DOCKER_CONFIG="$DEPLOY_DOCKER_CONFIG"

cleanup() {
  rm -rf "$DEPLOY_DOCKER_CONFIG"
}
trap cleanup EXIT

ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
REGISTRY="${ACCOUNT_ID}.dkr.ecr.${DEPLOY_REGION}.amazonaws.com"
IMAGE_TAG="$(date -u +%Y%m%d%H%M%S)-$(git rev-parse --short HEAD 2>/dev/null || printf 'working')"
IMAGE_URI="${REGISTRY}/${ECR_REPOSITORY}:${IMAGE_TAG}"

VPC_ID="$(
  aws ec2 describe-vpcs \
    --region "$DEPLOY_REGION" \
    --filters Name=isDefault,Values=true \
    --query 'Vpcs[0].VpcId' \
    --output text
)"
SUBNET_IDS="$(
  aws ec2 describe-subnets \
    --region "$DEPLOY_REGION" \
    --filters Name=vpc-id,Values="$VPC_ID" Name=default-for-az,Values=true \
    --query 'sort_by(Subnets,&AvailabilityZone)[0:2].SubnetId' \
    --output text |
    tr '\t' ','
)"
CLOUDFRONT_PREFIX_LIST_ID="$(
  aws ec2 describe-managed-prefix-lists \
    --region "$DEPLOY_REGION" \
    --filters Name=prefix-list-name,Values=com.amazonaws.global.cloudfront.origin-facing \
    --query 'PrefixLists[0].PrefixListId' \
    --output text
)"
ECS_EXECUTION_ROLE_ARN="$(
  aws iam get-role \
    --role-name ecsTaskExecutionRole \
    --query 'Role.Arn' \
    --output text
)"
SCHEDULER_BRIDGE_ARN="$(
  aws lambda get-function \
    --region "$DEPLOY_REGION" \
    --function-name geo-intelligence-scheduler-bridge \
    --query 'Configuration.FunctionArn' \
    --output text
)"

if ! aws ecr describe-repositories \
  --region "$DEPLOY_REGION" \
  --repository-names "$ECR_REPOSITORY" >/dev/null 2>&1; then
  aws ecr create-repository \
    --region "$DEPLOY_REGION" \
    --repository-name "$ECR_REPOSITORY" \
    --image-scanning-configuration scanOnPush=true \
    --image-tag-mutability IMMUTABLE >/dev/null
fi

aws ecr get-login-password --region "$DEPLOY_REGION" |
  docker login --username AWS --password-stdin "$REGISTRY"

docker build \
  --platform linux/arm64 \
  --file Dockerfile.backend \
  --tag "$IMAGE_URI" \
  .
docker push "$IMAGE_URI"

ORIGIN_VERIFY_SECRET="$(openssl rand -hex 32)"

CF_PARAMETERS=(
  ImageUri="$IMAGE_URI"
  VpcId="$VPC_ID"
  PublicSubnetIds="$SUBNET_IDS"
  CloudFrontPrefixListId="$CLOUDFRONT_PREFIX_LIST_ID"
  EcsExecutionRoleArn="$ECS_EXECUTION_ROLE_ARN"
  AuroraResourceArn="$AURORA_RESOURCE_ARN"
  AuroraSecretArn="$AURORA_SECRET_ARN"
  AuroraDatabase="${AURORA_DATABASE:-geo}"
  SchedulerBridgeArn="$SCHEDULER_BRIDGE_ARN"
  DesiredCount="$DESIRED_COUNT"
)
if ! aws cloudformation describe-stacks \
  --region "$DEPLOY_REGION" \
  --stack-name "$STACK_NAME" >/dev/null 2>&1; then
  CF_PARAMETERS+=(OriginVerifySecret="$ORIGIN_VERIFY_SECRET")
fi

aws cloudformation deploy \
  --region "$DEPLOY_REGION" \
  --stack-name "$STACK_NAME" \
  --template-file infrastructure/aws/web-ecs.yaml \
  --capabilities CAPABILITY_NAMED_IAM \
  --no-fail-on-empty-changeset \
  --parameter-overrides "${CF_PARAMETERS[@]}"

stack_output() {
  aws cloudformation describe-stacks \
    --region "$DEPLOY_REGION" \
    --stack-name "$STACK_NAME" \
    --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue | [0]" \
    --output text
}

PUBLIC_BUCKET="$(stack_output PublicBucketName)"
ADMIN_BUCKET="$(stack_output AdminBucketName)"
PUBLIC_DISTRIBUTION="$(stack_output PublicDistributionId)"
ADMIN_DISTRIBUTION="$(stack_output AdminDistributionId)"
PUBLIC_DOMAIN="$(stack_output PublicDomainName)"
ADMIN_DOMAIN="$(stack_output AdminDomainName)"

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

printf 'Backend image: %s\n' "$IMAGE_URI"
printf 'Public site:   https://%s\n' "$PUBLIC_DOMAIN"
printf 'Admin site:    https://%s\n' "$ADMIN_DOMAIN"
printf 'API health:    https://%s/api/health\n' "$PUBLIC_DOMAIN"
