#!/usr/bin/env bash
#
# Deployment script for Google Cloud Storage (GCS).
# Provisions the Dinnerguest asset and recipe staging bucket.
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/deploy_common.sh"

load_deploy_env
check_prerequisites

log_step "Deploying Google Cloud Storage Component"
enable_service "storage.googleapis.com"

# Check if GCS Bucket exists
log_info "Checking bucket gs://${GCS_BUCKET}..."
if gcloud storage buckets describe "gs://${GCS_BUCKET}" --project="$PROJECT_ID" &>/dev/null; then
    log_info "Bucket gs://${GCS_BUCKET} already exists."
else
    log_step "Creating bucket gs://${GCS_BUCKET} in ${REGION}..."
    gcloud storage buckets create "gs://${GCS_BUCKET}" \
        --project="$PROJECT_ID" \
        --location="$REGION" \
        --uniform-bucket-level-access
    log_success "Created bucket gs://${GCS_BUCKET}"
fi

# Sync canonical metadata and sample fixtures to the bucket
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
DATA_DIR="${REPO_ROOT}/src/dinnerguest/data"
FIXTURES_DIR="${REPO_ROOT}/tests/fixtures"

log_step "Synchronizing canonical datasets and taxonomy to gs://${GCS_BUCKET}/metadata/..."
if [[ -d "$DATA_DIR" ]]; then
    gcloud storage cp "${DATA_DIR}"/*.json "gs://${GCS_BUCKET}/metadata/" --recursive || true
fi

if [[ -d "$FIXTURES_DIR" ]]; then
    gcloud storage cp "${FIXTURES_DIR}"/*.json "gs://${GCS_BUCKET}/fixtures/" --recursive || true
fi

log_success "GCS Deployment complete. Asset bucket: gs://${GCS_BUCKET}"
