#!/usr/bin/env bash
#
# Interactive CLI chat with the deployed Dinnerguest Cloud Run service.
# Automatically acquires an OIDC identity token and streams conversation in your terminal.
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/deploy_common.sh"
load_deploy_env

SERVICE_URL="${1:-}"
if [[ -z "$SERVICE_URL" ]]; then
    log_info "Fetching Cloud Run service URL for '$AGENT_SERVICE_NAME'..."
    SERVICE_URL=$(gcloud run services describe "$AGENT_SERVICE_NAME" \
        --project="$PROJECT_ID" \
        --region="$REGION" \
        --format="value(status.url)" 2>/dev/null || true)
fi

if [[ -z "$SERVICE_URL" ]]; then
    log_error "Could not determine service URL. Please specify it as an argument or ensure '$AGENT_SERVICE_NAME' is deployed."
    exit 1
fi

echo "=========================================================="
echo " Dinnerguest Interactive Culinary Assistant"
echo " Connected to: $SERVICE_URL"
echo " Type 'exit' or Ctrl+C to quit."
echo "=========================================================="

SESSION_ID="session_$(date +%s)"

while true; do
    echo ""
    read -r -p "You > " USER_INPUT
    if [[ -z "$USER_INPUT" ]]; then
        continue
    fi
    if [[ "$USER_INPUT" == "exit" || "$USER_INPUT" == "quit" ]]; then
        echo "Goodbye! Happy cooking!"
        break
    fi

    # Acquire fresh OIDC Identity Token with Cloud Run audience
    ID_TOKEN=$(gcloud auth print-identity-token --audiences="${SERVICE_URL}")

    # Send POST request to /chat
    RESPONSE=$(curl -s -X POST \
        -H "Authorization: Bearer ${ID_TOKEN}" \
        -H "Content-Type: application/json" \
        -d "{\"message\": $(python3 -c "import json, sys; print(json.dumps(sys.argv[1]))" "$USER_INPUT"), \"session_id\": \"$SESSION_ID\"}" \
        "${SERVICE_URL}/chat" || true)

    if [[ -z "$RESPONSE" ]]; then
        echo "Dinnerguest > [Error: No response received from service]"
        continue
    fi

    # Parse reply using python
    REPLY=$(python3 -c "
import sys, json
try:
    data = json.loads(sys.argv[1])
    print(data.get('reply', data.get('error', sys.argv[1])))
except Exception:
    print(sys.argv[1])
" "$RESPONSE")

    echo ""
    echo "Dinnerguest > $REPLY"
done
