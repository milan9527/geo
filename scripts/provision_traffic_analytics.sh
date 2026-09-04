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
PUBLIC_DISTRIBUTION="${GEO_PUBLIC_DISTRIBUTION_ID:-E57TFN7Z03O69}"
LOG_BUCKET="${GEO_TRAFFIC_LOG_BUCKET:-geo-intelligence-traffic-logs-${ACCOUNT_ID}-${DEPLOY_REGION}}"
FUNCTION_NAME="${GEO_TRAFFIC_AGGREGATOR_FUNCTION:-geo-intelligence-traffic-aggregator}"
ROLE_NAME="${GEO_TRAFFIC_AGGREGATOR_ROLE:-geo-intelligence-traffic-aggregator-role}"
QUEUE_NAME="${GEO_TRAFFIC_QUEUE:-geo-intelligence-traffic-log-events}"
DLQ_NAME="${GEO_TRAFFIC_DLQ:-geo-intelligence-traffic-log-events-dlq}"
HASH_SECRET_NAME="${GEO_TRAFFIC_HASH_SECRET:-geo-intelligence-traffic-hash-salt}"
DELIVERY_SOURCE_NAME="geo-intelligence-cloudfront-public"
DELIVERY_DESTINATION_NAME="geo-intelligence-cloudfront-s3"
RESOURCE_ARN="arn:aws:cloudfront::${ACCOUNT_ID}:distribution/${PUBLIC_DISTRIBUTION}"

DEPLOY_TEMP_DIR="$(mktemp -d)"
cleanup() {
  rm -rf "$DEPLOY_TEMP_DIR"
}
trap cleanup EXIT

if ! aws s3api head-bucket --bucket "$LOG_BUCKET" >/dev/null 2>&1; then
  aws s3api create-bucket \
    --region "$DEPLOY_REGION" \
    --bucket "$LOG_BUCKET" >/dev/null
fi
aws s3api put-public-access-block \
  --bucket "$LOG_BUCKET" \
  --public-access-block-configuration \
    BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
aws s3api put-bucket-encryption \
  --bucket "$LOG_BUCKET" \
  --server-side-encryption-configuration \
    '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"},"BucketKeyEnabled":false}]}'
aws s3api put-bucket-ownership-controls \
  --bucket "$LOG_BUCKET" \
  --ownership-controls 'Rules=[{ObjectOwnership=BucketOwnerEnforced}]'

LIFECYCLE_FILE="${DEPLOY_TEMP_DIR}/lifecycle.json"
jq -n '{
  Rules: [{
    ID: "ExpireCloudFrontRawLogs",
    Status: "Enabled",
    Filter: {Prefix: "AWSLogs/"},
    Expiration: {Days: 90},
    AbortIncompleteMultipartUpload: {DaysAfterInitiation: 7}
  }]
}' >"$LIFECYCLE_FILE"
aws s3api put-bucket-lifecycle-configuration \
  --bucket "$LOG_BUCKET" \
  --lifecycle-configuration "file://${LIFECYCLE_FILE}"

BUCKET_POLICY_FILE="${DEPLOY_TEMP_DIR}/bucket-policy.json"
jq -n \
  --arg bucket "$LOG_BUCKET" \
  --arg account "$ACCOUNT_ID" \
  --arg region "$DEPLOY_REGION" \
  '{
    Version: "2012-10-17",
    Statement: [
      {
        Sid: "AWSLogDeliveryAclCheck",
        Effect: "Allow",
        Principal: {Service: "delivery.logs.amazonaws.com"},
        Action: "s3:GetBucketAcl",
        Resource: ("arn:aws:s3:::" + $bucket),
        Condition: {
          StringEquals: {"aws:SourceAccount": $account},
          ArnLike: {
            "aws:SourceArn":
              ("arn:aws:logs:" + $region + ":" + $account + ":delivery-source:*")
          }
        }
      },
      {
        Sid: "AWSLogDeliveryWrite",
        Effect: "Allow",
        Principal: {Service: "delivery.logs.amazonaws.com"},
        Action: "s3:PutObject",
        Resource: ("arn:aws:s3:::" + $bucket + "/*"),
        Condition: {
          StringEquals: {"aws:SourceAccount": $account},
          ArnLike: {
            "aws:SourceArn":
              ("arn:aws:logs:" + $region + ":" + $account + ":delivery-source:*")
          }
        }
      }
    ]
  }' >"$BUCKET_POLICY_FILE"
aws s3api put-bucket-policy \
  --bucket "$LOG_BUCKET" \
  --policy "file://${BUCKET_POLICY_FILE}"

DLQ_URL="$(
  aws sqs get-queue-url \
    --region "$DEPLOY_REGION" \
    --queue-name "$DLQ_NAME" \
    --query QueueUrl \
    --output text 2>/dev/null \
  || aws sqs create-queue \
    --region "$DEPLOY_REGION" \
    --queue-name "$DLQ_NAME" \
    --attributes MessageRetentionPeriod=1209600 \
    --query QueueUrl \
    --output text
)"
DLQ_ARN="$(
  aws sqs get-queue-attributes \
    --region "$DEPLOY_REGION" \
    --queue-url "$DLQ_URL" \
    --attribute-names QueueArn \
    --query 'Attributes.QueueArn' \
    --output text
)"
REDRIVE_POLICY="$(jq -cn --arg arn "$DLQ_ARN" '{deadLetterTargetArn:$arn,maxReceiveCount:"5"}')"
QUEUE_ATTRIBUTES="$(jq -cn \
  --arg redrive "$REDRIVE_POLICY" \
  '{
    VisibilityTimeout:"360",
    MessageRetentionPeriod:"1209600",
    RedrivePolicy:$redrive
  }')"
QUEUE_URL="$(
  aws sqs get-queue-url \
    --region "$DEPLOY_REGION" \
    --queue-name "$QUEUE_NAME" \
    --query QueueUrl \
    --output text 2>/dev/null \
  || aws sqs create-queue \
    --region "$DEPLOY_REGION" \
    --queue-name "$QUEUE_NAME" \
    --attributes "$QUEUE_ATTRIBUTES" \
    --query QueueUrl \
    --output text
)"
aws sqs set-queue-attributes \
  --region "$DEPLOY_REGION" \
  --queue-url "$QUEUE_URL" \
  --attributes "$QUEUE_ATTRIBUTES"
QUEUE_ARN="$(
  aws sqs get-queue-attributes \
    --region "$DEPLOY_REGION" \
    --queue-url "$QUEUE_URL" \
    --attribute-names QueueArn \
    --query 'Attributes.QueueArn' \
    --output text
)"
QUEUE_POLICY="$(jq -cn \
  --arg queue "$QUEUE_ARN" \
  --arg bucket "arn:aws:s3:::${LOG_BUCKET}" \
  --arg account "$ACCOUNT_ID" \
  '{
    Version:"2012-10-17",
    Statement:[{
      Sid:"AllowTrafficLogBucket",
      Effect:"Allow",
      Principal:{Service:"s3.amazonaws.com"},
      Action:"sqs:SendMessage",
      Resource:$queue,
      Condition:{
        ArnEquals:{"aws:SourceArn":$bucket},
        StringEquals:{"aws:SourceAccount":$account}
      }
    }]
  }')"
QUEUE_POLICY_ATTRIBUTE="$(jq -cn \
  --arg policy "$QUEUE_POLICY" \
  '{Policy:$policy}')"
aws sqs set-queue-attributes \
  --region "$DEPLOY_REGION" \
  --queue-url "$QUEUE_URL" \
  --attributes "$QUEUE_POLICY_ATTRIBUTE"

NOTIFICATION_FILE="${DEPLOY_TEMP_DIR}/notification.json"
jq -n \
  --arg queue "$QUEUE_ARN" \
  '{
    QueueConfigurations: [{
      Id: "TrafficLogAggregation",
      QueueArn: $queue,
      Events: ["s3:ObjectCreated:*"]
    }]
  }' >"$NOTIFICATION_FILE"
aws s3api put-bucket-notification-configuration \
  --bucket "$LOG_BUCKET" \
  --notification-configuration "file://${NOTIFICATION_FILE}"

HASH_SECRET_ARN="$(
  aws secretsmanager describe-secret \
    --region "$DEPLOY_REGION" \
    --secret-id "$HASH_SECRET_NAME" \
    --query ARN \
    --output text 2>/dev/null || true
)"
if [ -z "$HASH_SECRET_ARN" ] || [ "$HASH_SECRET_ARN" = "None" ]; then
  HASH_SALT="$(
    aws secretsmanager get-random-password \
      --region "$DEPLOY_REGION" \
      --password-length 64 \
      --exclude-punctuation \
      --query RandomPassword \
      --output text
  )"
  HASH_SECRET_ARN="$(
    aws secretsmanager create-secret \
      --region "$DEPLOY_REGION" \
      --name "$HASH_SECRET_NAME" \
      --description "HMAC salt for privacy-preserving GEO traffic aggregation" \
      --secret-string "$HASH_SALT" \
      --tags Key=Project,Value=ApertureGEO Key=Environment,Value=Demo \
      --query ARN \
      --output text
  )"
  unset HASH_SALT
fi

if ! aws iam get-role --role-name "$ROLE_NAME" >/dev/null 2>&1; then
  aws iam create-role \
    --role-name "$ROLE_NAME" \
    --assume-role-policy-document \
      "file://${PROJECT_DIR}/infrastructure/aws/lambda-trust-policy.json" \
    --description "Aggregate CloudFront traffic logs into Aurora GEO metrics" \
    --tags Key=Project,Value=ApertureGEO Key=Environment,Value=Demo >/dev/null
fi
ROLE_ARN="$(
  aws iam get-role \
    --role-name "$ROLE_NAME" \
    --query 'Role.Arn' \
    --output text
)"
ROLE_POLICY_FILE="${DEPLOY_TEMP_DIR}/role-policy.json"
jq -n \
  --arg region "$DEPLOY_REGION" \
  --arg account "$ACCOUNT_ID" \
  --arg bucket "$LOG_BUCKET" \
  --arg queue "$QUEUE_ARN" \
  --arg cluster "$AURORA_RESOURCE_ARN" \
  --arg dbSecret "$AURORA_SECRET_ARN" \
  --arg hashSecret "$HASH_SECRET_ARN" \
  '{
    Version: "2012-10-17",
    Statement: [
      {
        Sid: "ReadTrafficLogs",
        Effect: "Allow",
        Action: ["s3:GetObject"],
        Resource: ("arn:aws:s3:::" + $bucket + "/*")
      },
      {
        Sid: "ConsumeTrafficQueue",
        Effect: "Allow",
        Action: [
          "sqs:ReceiveMessage",
          "sqs:DeleteMessage",
          "sqs:GetQueueAttributes"
        ],
        Resource: $queue
      },
      {
        Sid: "WriteAuroraAggregates",
        Effect: "Allow",
        Action: [
          "rds-data:BeginTransaction",
          "rds-data:ExecuteStatement",
          "rds-data:CommitTransaction",
          "rds-data:RollbackTransaction"
        ],
        Resource: $cluster
      },
      {
        Sid: "ReadAggregationSecrets",
        Effect: "Allow",
        Action: ["secretsmanager:GetSecretValue"],
        Resource: [$dbSecret, $hashSecret]
      },
      {
        Sid: "LambdaLogs",
        Effect: "Allow",
        Action: [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents"
        ],
        Resource: ("arn:aws:logs:" + $region + ":" + $account + ":*")
      }
    ]
  }' >"$ROLE_POLICY_FILE"
aws iam put-role-policy \
  --role-name "$ROLE_NAME" \
  --policy-name geo-intelligence-traffic-aggregator \
  --policy-document "file://${ROLE_POLICY_FILE}"
aws iam wait role-exists --role-name "$ROLE_NAME"

cp aws_traffic_aggregator/lambda_function.py "$DEPLOY_TEMP_DIR/lambda_function.py"
cp backend/analytics.py "$DEPLOY_TEMP_DIR/analytics_shared.py"
cp backend/data_api.py "$DEPLOY_TEMP_DIR/data_api.py"
(
  cd "$DEPLOY_TEMP_DIR"
  zip -q traffic-aggregator.zip \
    lambda_function.py analytics_shared.py data_api.py
)
ENVIRONMENT_JSON="$(jq -cn \
  --arg region "$DEPLOY_REGION" \
  --arg cluster "$AURORA_RESOURCE_ARN" \
  --arg secret "$AURORA_SECRET_ARN" \
  --arg database "${AURORA_DATABASE:-geo}" \
  --arg hashSecret "$HASH_SECRET_ARN" \
  '{Variables:{
    AWS_DATA_API:"true",
    AWS_REGION_NAME:$region,
    AURORA_RESOURCE_ARN:$cluster,
    AURORA_SECRET_ARN:$secret,
    AURORA_DATABASE:$database,
    TRAFFIC_HASH_SECRET_ARN:$hashSecret
  }}')"
if aws lambda get-function \
  --region "$DEPLOY_REGION" \
  --function-name "$FUNCTION_NAME" >/dev/null 2>&1; then
  aws lambda update-function-code \
    --region "$DEPLOY_REGION" \
    --function-name "$FUNCTION_NAME" \
    --zip-file "fileb://${DEPLOY_TEMP_DIR}/traffic-aggregator.zip" >/dev/null
  aws lambda wait function-updated-v2 \
    --region "$DEPLOY_REGION" \
    --function-name "$FUNCTION_NAME"
  aws lambda update-function-configuration \
    --region "$DEPLOY_REGION" \
    --function-name "$FUNCTION_NAME" \
    --runtime python3.13 \
    --handler lambda_function.lambda_handler \
    --role "$ROLE_ARN" \
    --timeout 120 \
    --memory-size 256 \
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
      --timeout 120 \
      --memory-size 256 \
      --environment "$ENVIRONMENT_JSON" \
      --zip-file "fileb://${DEPLOY_TEMP_DIR}/traffic-aggregator.zip" \
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
aws lambda put-function-concurrency \
  --region "$DEPLOY_REGION" \
  --function-name "$FUNCTION_NAME" \
  --reserved-concurrent-executions 1 >/dev/null
aws logs create-log-group \
  --region "$DEPLOY_REGION" \
  --log-group-name "/aws/lambda/${FUNCTION_NAME}" 2>/dev/null || true
aws logs put-retention-policy \
  --region "$DEPLOY_REGION" \
  --log-group-name "/aws/lambda/${FUNCTION_NAME}" \
  --retention-in-days 30 2>/dev/null || true

MAPPING_UUID="$(
  aws lambda list-event-source-mappings \
    --region "$DEPLOY_REGION" \
    --function-name "$FUNCTION_NAME" \
    --event-source-arn "$QUEUE_ARN" \
    --query 'EventSourceMappings[0].UUID' \
    --output text
)"
if [ -z "$MAPPING_UUID" ] || [ "$MAPPING_UUID" = "None" ]; then
  aws lambda create-event-source-mapping \
    --region "$DEPLOY_REGION" \
    --function-name "$FUNCTION_NAME" \
    --event-source-arn "$QUEUE_ARN" \
    --batch-size 5 >/dev/null
else
  aws lambda update-event-source-mapping \
    --region "$DEPLOY_REGION" \
    --uuid "$MAPPING_UUID" \
    --batch-size 5 \
    --enabled >/dev/null
fi

aws logs put-delivery-source \
  --region "$DEPLOY_REGION" \
  --name "$DELIVERY_SOURCE_NAME" \
  --resource-arn "$RESOURCE_ARN" \
  --log-type ACCESS_LOGS >/dev/null
DELIVERY_DESTINATION_ARN="$(
  aws logs put-delivery-destination \
    --region "$DEPLOY_REGION" \
    --name "$DELIVERY_DESTINATION_NAME" \
    --output-format json \
    --delivery-destination-type S3 \
    --delivery-destination-configuration \
      "destinationResourceArn=arn:aws:s3:::${LOG_BUCKET}" \
    --query 'deliveryDestination.arn' \
    --output text
)"
DELIVERY_ID="$(
  aws logs describe-deliveries \
    --region "$DEPLOY_REGION" \
    --output json |
  jq -r \
    --arg source "$DELIVERY_SOURCE_NAME" \
    --arg destination "$DELIVERY_DESTINATION_ARN" \
    '.deliveries[]
     | select(
         .deliverySourceName == $source
         and .deliveryDestinationArn == $destination
       )
     | .id' |
  head -1
)"
RECORD_FIELDS=(
  date
  time
  DistributionId
  c-ip
  cs-method
  'cs(Host)'
  cs-uri-stem
  sc-status
  'cs(Referer)'
  'cs(User-Agent)'
  cs-uri-query
  x-edge-result-type
  x-edge-request-id
  x-host-header
  sc-bytes
  time-taken
  c-country
  cache-behavior-path-pattern
)
S3_DELIVERY_CONFIG='{"suffixPath":"cloudfront/{DistributionId}/{yyyy}/{MM}/{dd}/{HH}","enableHiveCompatiblePath":false}'
if [ -z "$DELIVERY_ID" ]; then
  DELIVERY_ID="$(
    aws logs create-delivery \
      --region "$DEPLOY_REGION" \
      --delivery-source-name "$DELIVERY_SOURCE_NAME" \
      --delivery-destination-arn "$DELIVERY_DESTINATION_ARN" \
      --record-fields "${RECORD_FIELDS[@]}" \
      --s3-delivery-configuration "$S3_DELIVERY_CONFIG" \
      --tags Project=ApertureGEO,Environment=Demo \
      --query 'delivery.id' \
      --output text
  )"
fi

aws lambda get-function-configuration \
  --region "$DEPLOY_REGION" \
  --function-name "$FUNCTION_NAME" \
  --query '{function:FunctionName,state:State,lastUpdate:LastUpdateStatus}' \
  --output json
printf 'Traffic log bucket: s3://%s\n' "$LOG_BUCKET"
printf 'Traffic queue: %s\n' "$QUEUE_ARN"
printf 'CloudFront delivery: %s\n' "$DELIVERY_ID"
