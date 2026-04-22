#!/bin/bash
# Quick deploy script for AppSignalsMcp-Dev
# Usage: ./deploy.sh
# Rebuilds Lambda zip from local source and updates the function (~10 seconds)

set -e

ACCOUNT="140023401067"
REGION="us-east-1"
BUCKET="appsignals-mcp-deploy-${ACCOUNT}"
FUNCTION="AppSignalsMcp-Dev"
S3_KEY="dev-lambda.zip"
BASE_ZIP="/tmp/appsignals-dev-build/base-deps.zip"
DEV_ZIP="/tmp/appsignals-dev-build/dev-lambda.zip"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# One-time: create base deps zip if it doesn't exist
if [ ! -f "$BASE_ZIP" ]; then
    echo "Creating base dependencies zip (one-time)..."
    cp /tmp/appsignals-mcp-capture/lambda-updated.zip "$BASE_ZIP"
    # Remove the pypi MCP server from base so only local source is used
    echo "Base deps zip created at $BASE_ZIP"
fi

echo "Building dev Lambda zip..."
cp "$BASE_ZIP" "$DEV_ZIP"

# Overlay local MCP server source
pushd "$SCRIPT_DIR" > /dev/null
zip -r -q "$DEV_ZIP" awslabs/
popd > /dev/null

echo "Uploading to S3..."
aws s3 cp "$DEV_ZIP" "s3://${BUCKET}/${S3_KEY}" --region "$REGION" --quiet

echo "Updating Lambda function..."
aws lambda update-function-code \
    --function-name "$FUNCTION" \
    --s3-bucket "$BUCKET" \
    --s3-key "$S3_KEY" \
    --region "$REGION" \
    --query '{Status: LastUpdateStatus}' \
    --output table

echo "Waiting for update to complete..."
aws lambda wait function-updated --function-name "$FUNCTION" --region "$REGION"

echo ""
echo "Done! MCP endpoint: https://tqlda4v9vf.execute-api.us-east-1.amazonaws.com/dev/mcp"
echo "Health check:       https://tqlda4v9vf.execute-api.us-east-1.amazonaws.com/dev/health"
