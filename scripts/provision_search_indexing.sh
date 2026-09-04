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
ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
FUNCTION_NAME="${GEO_INDEXING_NOTIFIER_FUNCTION:-geo-intelligence-indexing-notifier}"
ROLE_NAME="${GEO_INDEXING_NOTIFIER_ROLE:-geo-intelligence-indexing-notifier-role}"
PUBLIC_DISTRIBUTION="${GEO_PUBLIC_DISTRIBUTION_ID:-E57TFN7Z03O69}"
PUBLIC_BASE_URL="${GEO_PUBLIC_BASE_URL:-https://aperture.zhangwangshu.com}"
INDEXNOW_KEY="${INDEXNOW_KEY:-$(printf '%s' "$PUBLIC_BASE_URL" | sha256sum | cut -c1-32)}"
FUNCTION_ARN="arn:aws:lambda:${DEPLOY_REGION}:${ACCOUNT_ID}:function:${FUNCTION_NAME}"

if [[ ! "$PUBLIC_BASE_URL" =~ ^https://[^/]+$ ]]; then
  echo "GEO_PUBLIC_BASE_URL must be an HTTPS origin without a path." >&2
  exit 1
fi

DEPLOY_TEMP_DIR="$(mktemp -d)"
cleanup() {
  rm -rf "$DEPLOY_TEMP_DIR"
}
trap cleanup EXIT

if ! aws iam get-role --role-name "$ROLE_NAME" >/dev/null 2>&1; then
  aws iam create-role \
    --role-name "$ROLE_NAME" \
    --assume-role-policy-document \
      "file://${PROJECT_DIR}/infrastructure/aws/lambda-trust-policy.json" \
    --description "Invalidate GEO content and notify IndexNow after publication" \
    --tags Key=Project,Value=ApertureGEO Key=Environment,Value=Demo >/dev/null
fi
ROLE_ARN="$(
  aws iam get-role \
    --role-name "$ROLE_NAME" \
    --query Role.Arn \
    --output text
)"
ROLE_POLICY_FILE="${DEPLOY_TEMP_DIR}/notifier-policy.json"
jq -n \
  --arg region "$DEPLOY_REGION" \
  --arg account "$ACCOUNT_ID" \
  --arg distribution "$PUBLIC_DISTRIBUTION" \
  '{
    Version:"2012-10-17",
    Statement:[
      {
        Sid:"InvalidatePublicDistribution",
        Effect:"Allow",
        Action:"cloudfront:CreateInvalidation",
        Resource:(
          "arn:aws:cloudfront::" + $account
          + ":distribution/" + $distribution
        )
      },
      {
        Sid:"LambdaLogs",
        Effect:"Allow",
        Action:[
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents"
        ],
        Resource:(
          "arn:aws:logs:" + $region + ":" + $account + ":*"
        )
      }
    ]
  }' >"$ROLE_POLICY_FILE"
aws iam put-role-policy \
  --role-name "$ROLE_NAME" \
  --policy-name geo-intelligence-indexing-notifier \
  --policy-document "file://${ROLE_POLICY_FILE}"
aws iam wait role-exists --role-name "$ROLE_NAME"

cp aws_indexing_notifier/lambda_function.py "$DEPLOY_TEMP_DIR/lambda_function.py"
(
  cd "$DEPLOY_TEMP_DIR"
  zip -q indexing-notifier.zip lambda_function.py
)
ENVIRONMENT_JSON="$(jq -cn \
  --arg base "$PUBLIC_BASE_URL" \
  --arg distribution "$PUBLIC_DISTRIBUTION" \
  --arg key "$INDEXNOW_KEY" \
  '{Variables:{
    GEO_PUBLIC_BASE_URL:$base,
    GEO_PUBLIC_DISTRIBUTION_ID:$distribution,
    INDEXNOW_KEY:$key,
    INDEXNOW_ENDPOINT:"https://api.indexnow.org/indexnow"
  }}')"
if aws lambda get-function \
  --region "$DEPLOY_REGION" \
  --function-name "$FUNCTION_NAME" >/dev/null 2>&1; then
  aws lambda update-function-code \
    --region "$DEPLOY_REGION" \
    --function-name "$FUNCTION_NAME" \
    --zip-file "fileb://${DEPLOY_TEMP_DIR}/indexing-notifier.zip" >/dev/null
  aws lambda wait function-updated-v2 \
    --region "$DEPLOY_REGION" \
    --function-name "$FUNCTION_NAME"
  aws lambda update-function-configuration \
    --region "$DEPLOY_REGION" \
    --function-name "$FUNCTION_NAME" \
    --runtime python3.13 \
    --handler lambda_function.lambda_handler \
    --role "$ROLE_ARN" \
    --timeout 30 \
    --memory-size 128 \
    --environment "$ENVIRONMENT_JSON" >/dev/null
else
  for _ in $(seq 1 6); do
    if aws lambda create-function \
      --region "$DEPLOY_REGION" \
      --function-name "$FUNCTION_NAME" \
      --runtime python3.13 \
      --architectures arm64 \
      --handler lambda_function.lambda_handler \
      --role "$ROLE_ARN" \
      --timeout 30 \
      --memory-size 128 \
      --environment "$ENVIRONMENT_JSON" \
      --zip-file "fileb://${DEPLOY_TEMP_DIR}/indexing-notifier.zip" \
      --tags Project=ApertureGEO,Environment=Demo >/dev/null; then
      break
    fi
    sleep 5
  done
fi
aws lambda wait function-active-v2 \
  --region "$DEPLOY_REGION" \
  --function-name "$FUNCTION_NAME"
aws lambda wait function-updated-v2 \
  --region "$DEPLOY_REGION" \
  --function-name "$FUNCTION_NAME"
aws lambda put-function-event-invoke-config \
  --region "$DEPLOY_REGION" \
  --function-name "$FUNCTION_NAME" \
  --qualifier '$LATEST' \
  --maximum-event-age-in-seconds 3600 \
  --maximum-retry-attempts 2 >/dev/null
aws logs create-log-group \
  --region "$DEPLOY_REGION" \
  --log-group-name "/aws/lambda/${FUNCTION_NAME}" 2>/dev/null || true
aws logs put-retention-policy \
  --region "$DEPLOY_REGION" \
  --log-group-name "/aws/lambda/${FUNCTION_NAME}" \
  --retention-in-days 30

ECS_TASK_ROLE_ARN="$(
  aws ecs describe-task-definition \
    --region "$DEPLOY_REGION" \
    --task-definition "${GEO_ECS_SERVICE:-geo-intelligence-api}" \
    --query taskDefinition.taskRoleArn \
    --output text
)"
AGENTCORE_ROLE_ARN="$(
  aws bedrock-agentcore-control get-agent-runtime \
    --region "$DEPLOY_REGION" \
    --agent-runtime-id "${AGENTCORE_RUNTIME_ARN##*/}" \
    --query roleArn \
    --output text
)"
CALLER_POLICY_FILE="${DEPLOY_TEMP_DIR}/caller-policy.json"
jq -n \
  --arg functionArn "$FUNCTION_ARN" \
  '{
    Version:"2012-10-17",
    Statement:[{
      Sid:"InvokeGeoIndexingNotifier",
      Effect:"Allow",
      Action:"lambda:InvokeFunction",
      Resource:[$functionArn,($functionArn + ":*")]
    }]
  }' >"$CALLER_POLICY_FILE"
for caller_role_arn in "$ECS_TASK_ROLE_ARN" "$AGENTCORE_ROLE_ARN"; do
  caller_role_name="${caller_role_arn##*/}"
  aws iam put-role-policy \
    --role-name "$caller_role_name" \
    --policy-name geo-intelligence-invoke-indexing-notifier \
    --policy-document "file://${CALLER_POLICY_FILE}"
done

RUNTIME_CURRENT="${DEPLOY_TEMP_DIR}/runtime-current.json"
RUNTIME_UPDATE="${DEPLOY_TEMP_DIR}/runtime-update.json"
RUNTIME_ID="${AGENTCORE_RUNTIME_ARN##*/}"
aws bedrock-agentcore-control get-agent-runtime \
  --region "$DEPLOY_REGION" \
  --agent-runtime-id "$RUNTIME_ID" \
  --output json >"$RUNTIME_CURRENT"
jq \
  --arg runtimeId "$RUNTIME_ID" \
  --arg functionName "$FUNCTION_NAME" \
  --arg publicBase "$PUBLIC_BASE_URL" \
  '{
    agentRuntimeId: $runtimeId,
    agentRuntimeArtifact,
    roleArn,
    networkConfiguration,
    description,
    protocolConfiguration,
    lifecycleConfiguration,
    environmentVariables: (
      (.environmentVariables // {})
      + {
          GEO_INDEXING_NOTIFIER_FUNCTION: $functionName,
          GEO_PUBLIC_BASE_URL: $publicBase
        }
    )
  }' \
  "$RUNTIME_CURRENT" >"$RUNTIME_UPDATE"
aws bedrock-agentcore-control update-agent-runtime \
  --region "$DEPLOY_REGION" \
  --cli-input-json "file://${RUNTIME_UPDATE}" >/dev/null
for _ in $(seq 1 90); do
  RUNTIME_STATUS="$(
    aws bedrock-agentcore-control get-agent-runtime \
      --region "$DEPLOY_REGION" \
      --agent-runtime-id "$RUNTIME_ID" \
      --query status \
      --output text
  )"
  if [ "$RUNTIME_STATUS" = "READY" ]; then
    break
  fi
  if [[ "$RUNTIME_STATUS" == *FAILED ]]; then
    echo "AgentCore Runtime environment update failed: ${RUNTIME_STATUS}" >&2
    exit 1
  fi
  sleep 10
done

aws lambda get-function-configuration \
  --region "$DEPLOY_REGION" \
  --function-name "$FUNCTION_NAME" \
  --query '{function:FunctionName,state:State,lastUpdate:LastUpdateStatus}' \
  --output json
printf 'IndexNow key URL: %s/indexnow-key.txt\n' "$PUBLIC_BASE_URL"
printf 'Indexing notifier: %s\n' "$FUNCTION_ARN"
