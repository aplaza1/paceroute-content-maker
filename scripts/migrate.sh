#!/usr/bin/env bash
# scripts/migrate.sh
# One-time migration: sync local DB + output files to EFS via S3.
#
# Prerequisites:
#   - AWS CLI configured with appropriate credentials
#   - S3 bucket created: paceroute-migration (private, same region as EFS)
#   - CDK stack already deployed (cluster + task definition exist)
#   - Fill in SUBNET_ID and SECURITY_GROUP_ID below before running
#
# Usage:
#   bash scripts/migrate.sh

set -euo pipefail

BUCKET="paceroute-migration"
CLUSTER="paceroute-pipeline"
# Update these values after CDK deploy (check CloudFormation Outputs):
SUBNET_ID="${SUBNET_ID:-subnet-REPLACE_ME}"
SECURITY_GROUP_ID="${SECURITY_GROUP_ID:-sg-REPLACE_ME}"
REGION="${AWS_REGION:-us-east-1}"

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"

echo "=== Step 1: Upload local files to S3 ==="

# Sync DB
if [[ -f "$REPO_DIR/pipeline.db" ]]; then
    aws s3 cp "$REPO_DIR/pipeline.db" "s3://$BUCKET/pipeline.db"
    echo "  Uploaded pipeline.db"
else
    echo "  WARNING: pipeline.db not found at $REPO_DIR/pipeline.db"
fi

# Sync output directory
if [[ -d "$REPO_DIR/output" ]]; then
    aws s3 sync "$REPO_DIR/output/" "s3://$BUCKET/output/"
    echo "  Uploaded output/"
else
    echo "  WARNING: output/ not found"
fi

echo ""
echo "=== Step 2: Fetch task definition ARN ==="
TASK_DEF_ARN=$(aws ecs list-task-definitions \
    --family-prefix paceroute-pipeline \
    --sort DESC \
    --query "taskDefinitionArns[0]" \
    --output text \
    --region "$REGION")
echo "  Using task definition: $TASK_DEF_ARN"

echo ""
echo "=== Step 3: Run ECS task to copy S3 → EFS ==="
# The container image includes awscli via pip; if not, add it to requirements.txt.
aws ecs run-task \
    --cluster "$CLUSTER" \
    --task-definition "$TASK_DEF_ARN" \
    --launch-type FARGATE \
    --network-configuration "awsvpcConfiguration={subnets=[$SUBNET_ID],securityGroups=[$SECURITY_GROUP_ID],assignPublicIp=ENABLED}" \
    --overrides "{
        \"containerOverrides\": [{
            \"name\": \"pipeline\",
            \"command\": [\"aws\", \"s3\", \"sync\", \"s3://$BUCKET/\", \"/mnt/efs/\"]
        }]
    }" \
    --region "$REGION"

echo ""
echo "=== Migration task launched ==="
echo "Monitor progress in CloudWatch Logs: /ecs/paceroute-pipeline"
echo ""
echo "After confirming success, delete the S3 bucket:"
echo "  aws s3 rb s3://$BUCKET --force"
