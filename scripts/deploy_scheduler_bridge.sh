#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

DEPLOY_REGION="${AWS_REGION:-us-east-1}"
FUNCTION_NAME="${GEO_SCHEDULER_BRIDGE_FUNCTION:-geo-intelligence-scheduler-bridge}"
DEPLOY_TEMP_DIR="$(mktemp -d)"

cleanup() {
  rm -rf "$DEPLOY_TEMP_DIR"
}
trap cleanup EXIT

cp aws_scheduler/lambda_function.py "$DEPLOY_TEMP_DIR/lambda_function.py"
(
  cd "$DEPLOY_TEMP_DIR"
  zip -q scheduler-bridge.zip lambda_function.py
)

aws lambda update-function-code \
  --region "$DEPLOY_REGION" \
  --function-name "$FUNCTION_NAME" \
  --zip-file "fileb://${DEPLOY_TEMP_DIR}/scheduler-bridge.zip" >/dev/null
aws lambda wait function-updated-v2 \
  --region "$DEPLOY_REGION" \
  --function-name "$FUNCTION_NAME"

aws lambda update-function-configuration \
  --region "$DEPLOY_REGION" \
  --function-name "$FUNCTION_NAME" \
  --timeout 60 >/dev/null
aws lambda wait function-updated-v2 \
  --region "$DEPLOY_REGION" \
  --function-name "$FUNCTION_NAME"

aws lambda put-function-event-invoke-config \
  --region "$DEPLOY_REGION" \
  --function-name "$FUNCTION_NAME" \
  --qualifier '$LATEST' \
  --maximum-event-age-in-seconds 300 \
  --maximum-retry-attempts 0 >/dev/null

aws lambda get-function-configuration \
  --region "$DEPLOY_REGION" \
  --function-name "$FUNCTION_NAME" \
  --query '{function:FunctionName,state:State,lastUpdate:LastUpdateStatus,timeout:Timeout,codeSha256:CodeSha256}' \
  --output json
