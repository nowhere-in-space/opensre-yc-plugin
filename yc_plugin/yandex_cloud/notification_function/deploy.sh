#!/usr/bin/env bash
#
# Deploy the notification function that hands a Yandex Monitoring alert to OpenSRE.
#
#   OPENSRE_URL=https://opensre.internal:8000 \
#   OPENSRE_TOKEN=<the gateway's OPENSRE_ALERT_LISTENER_TOKEN> \
#   ./deploy.sh
#
# Then attach it in the console: Monitoring → Notification channels → Create,
# type "Cloud Functions", pick this function. Add that channel to every alert
# that should start an investigation.
#
# The `yc` CLI is used here because a human is deploying. The agent itself never
# shells out to it — it reads Yandex Cloud over the REST API.

set -euo pipefail

FUNCTION_NAME="${FUNCTION_NAME:-opensre-alert-bridge}"
SERVICE_ACCOUNT_ID="${SERVICE_ACCOUNT_ID:-}"
# An investigation runs about a minute. A function that times out first returns
# an error to Monitoring and the report is lost, so leave plenty of headroom.
EXECUTION_TIMEOUT="${EXECUTION_TIMEOUT:-600s}"
MEMORY="${MEMORY:-256m}"

if [[ -z "${OPENSRE_URL:-}" ]]; then
    echo "set OPENSRE_URL to the gateway base URL, e.g. https://opensre.internal:8000" >&2
    exit 1
fi
if [[ -z "${OPENSRE_TOKEN:-}" ]]; then
    echo "warning: OPENSRE_TOKEN is empty — the gateway only accepts unauthenticated" >&2
    echo "         callers from loopback, which a Cloud Function is not." >&2
fi

cd "$(dirname "$0")"

yc serverless function create --name "$FUNCTION_NAME" 2>/dev/null \
    || echo "function $FUNCTION_NAME already exists, adding a version"

yc serverless function version create \
    --function-name "$FUNCTION_NAME" \
    --runtime python312 \
    --entrypoint handler.handler \
    --memory "$MEMORY" \
    --execution-timeout "$EXECUTION_TIMEOUT" \
    --source-path ./handler.py \
    ${SERVICE_ACCOUNT_ID:+--service-account-id "$SERVICE_ACCOUNT_ID"} \
    --environment OPENSRE_URL="$OPENSRE_URL" \
    ${OPENSRE_TOKEN:+--environment OPENSRE_TOKEN="$OPENSRE_TOKEN"}

echo
echo "Deployed. Attach it as a notification channel:"
echo "  console → Monitoring → Notification channels → Create → Cloud Functions → $FUNCTION_NAME"
echo
echo "Check it end to end before relying on it:"
echo "  yc serverless function invoke --name $FUNCTION_NAME \\"
echo "    --data '{\"alert_id\":\"test\",\"alert_name\":\"smoke test\",\"status\":\"ALARM\",\"folder_id\":\"<folder>\"}'"
