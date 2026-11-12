#!/usr/bin/env bash
#
# Deployment script for Google Cloud Firestore.
# Provisions the Firestore database in Native mode for Dinnerguest persistent event menus.
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/deploy_common.sh"

load_deploy_env
check_prerequisites

log_step "Deploying Google Cloud Firestore Component"
enable_service "firestore.googleapis.com"

log_info "Checking Firestore database '${FIRESTORE_DATABASE}' in project '${PROJECT_ID}'..."
EXISTING_DB=$(gcloud firestore databases list --project="$PROJECT_ID" --format="value(name)" 2>/dev/null | grep -E "(databases/${FIRESTORE_DATABASE}$|^${FIRESTORE_DATABASE}$)" || true)

if [[ -n "$EXISTING_DB" ]]; then
    log_info "Firestore database '${FIRESTORE_DATABASE}' already exists."
else
    log_step "Creating Firestore database '${FIRESTORE_DATABASE}' in ${REGION} (Native mode)..."
    if [[ "$FIRESTORE_DATABASE" == "(default)" ]]; then
        gcloud firestore databases create \
            --project="$PROJECT_ID" \
            --location="$REGION" \
            --type=firestore-native
    else
        gcloud firestore databases create \
            --project="$PROJECT_ID" \
            --database="$FIRESTORE_DATABASE" \
            --location="$REGION" \
            --type=firestore-native
    fi
    log_success "Created Firestore Native database '${FIRESTORE_DATABASE}'"
fi

log_info "Firestore collections used by Dinnerguest: 'saved_menus'"
log_success "Firestore Deployment complete."
