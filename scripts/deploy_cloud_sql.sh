#!/usr/bin/env bash
#
# Deployment script for Google Cloud SQL (PostgreSQL with pgvector).
# Provisions the relational + vector database instance for Dinnerguest RAG and recipe search.
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/deploy_common.sh"

load_deploy_env
check_prerequisites

log_step "Deploying Google Cloud SQL Component (PostgreSQL + pgvector)"
enable_service "sqladmin.googleapis.com"
enable_service "servicenetworking.googleapis.com"

# 1. Check or Create Cloud SQL Instance
log_info "Checking Cloud SQL instance '$CLOUD_SQL_INSTANCE'..."
if gcloud sql instances describe "$CLOUD_SQL_INSTANCE" --project="$PROJECT_ID" &>/dev/null; then
    log_info "Cloud SQL instance '$CLOUD_SQL_INSTANCE' already exists."
else
    log_step "Creating Cloud SQL PostgreSQL instance '$CLOUD_SQL_INSTANCE' in $REGION ($CLOUD_SQL_TIER)..."
    log_info "This may take 4-7 minutes on Google Cloud..."
    gcloud sql instances create "$CLOUD_SQL_INSTANCE" \
        --project="$PROJECT_ID" \
        --database-version="$CLOUD_SQL_POSTGRES_VERSION" \
        --tier="$CLOUD_SQL_TIER" \
        --region="$REGION" \
        --database-flags="cloudsql.enable_pgvector=on" \
        --storage-type="SSD" \
        --storage-size="20GB" \
        --storage-auto-increase
    log_success "Created Cloud SQL instance '$CLOUD_SQL_INSTANCE'"
fi

# 2. Check or Create Database
log_info "Checking database '$CLOUD_SQL_DB' on instance '$CLOUD_SQL_INSTANCE'..."
if gcloud sql databases list --instance="$CLOUD_SQL_INSTANCE" --project="$PROJECT_ID" --format="value(name)" | grep -w -q "$CLOUD_SQL_DB"; then
    log_info "Database '$CLOUD_SQL_DB' already exists."
else
    log_step "Creating database '$CLOUD_SQL_DB'..."
    gcloud sql databases create "$CLOUD_SQL_DB" \
        --instance="$CLOUD_SQL_INSTANCE" \
        --project="$PROJECT_ID"
    log_success "Created database '$CLOUD_SQL_DB'"
fi

# 3. Check or Create Database User
log_info "Checking database user '$CLOUD_SQL_USER'..."
if gcloud sql users list --instance="$CLOUD_SQL_INSTANCE" --project="$PROJECT_ID" --format="value(name)" | grep -w -q "$CLOUD_SQL_USER"; then
    log_info "User '$CLOUD_SQL_USER' already exists."
else
    log_step "Creating database user '$CLOUD_SQL_USER'..."
    gcloud sql users create "$CLOUD_SQL_USER" \
        --instance="$CLOUD_SQL_INSTANCE" \
        --password="$CLOUD_SQL_PASSWORD" \
        --project="$PROJECT_ID"
    log_success "Created database user '$CLOUD_SQL_USER'"
fi

# 4. Initialize Database Schema with pgvector
SCHEMA_FILE="${SCRIPT_DIR}/sql/init_schema.sql"
if [[ -f "$SCHEMA_FILE" ]]; then
    log_step "Applying database schema & pgvector extension from $SCHEMA_FILE..."
    
    # Upload schema to GCS for Cloud SQL automated import
    GCS_SCHEMA_URI="gs://${GCS_BUCKET}/sql/init_schema.sql"
    log_info "Uploading schema to $GCS_SCHEMA_URI..."
    gcloud storage cp "$SCHEMA_FILE" "$GCS_SCHEMA_URI"
    
    # Grant Cloud SQL Service Account read access to the schema in GCS
    SQL_SA=$(gcloud sql instances describe "$CLOUD_SQL_INSTANCE" --project="$PROJECT_ID" --format="value(serviceAccountEmailAddress)")
    if [[ -n "$SQL_SA" ]]; then
        log_info "Granting Cloud SQL service account ($SQL_SA) read access to $GCS_SCHEMA_URI..."
        gcloud storage objects add-iam-policy-binding "$GCS_SCHEMA_URI" \
            --member="serviceAccount:${SQL_SA}" \
            --role="roles/storage.objectViewer" &>/dev/null || true
    fi

    log_step "Importing schema into '$CLOUD_SQL_DB'..."
    gcloud sql import sql "$CLOUD_SQL_INSTANCE" "$GCS_SCHEMA_URI" \
        --database="$CLOUD_SQL_DB" \
        --project="$PROJECT_ID" \
        --quiet
    log_success "Successfully initialized Cloud SQL schema with pgvector & tables."
fi

CONNECTION_NAME=$(gcloud sql instances describe "$CLOUD_SQL_INSTANCE" --project="$PROJECT_ID" --format="value(connectionName)")
log_success "Cloud SQL Deployment complete."
log_info "Instance Connection Name: $CONNECTION_NAME"
log_info "Database: $CLOUD_SQL_DB"
log_info "User    : $CLOUD_SQL_USER"
