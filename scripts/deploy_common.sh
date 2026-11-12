#!/usr/bin/env bash
#
# Common configuration and helper functions for Dinnerguest GCP deployments.
# Sourced by individual deployment scripts and deploy_all.sh.
#
set -euo pipefail

# ANSI Color Codes
BOLD="\033[1m"
GREEN="\033[0;32m"
YELLOW="\033[0;33m"
RED="\033[0;31m"
BLUE="\033[0;34m"
CYAN="\033[0;36m"
NC="\033[0m"

# Logging Helpers
log_info() {
    echo -e "${BLUE}[INFO]${NC} $*"
}

log_step() {
    echo -e "${BOLD}${CYAN}==> $*${NC}"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $*"
}

log_warn() {
    echo -e "${YELLOW}[WARNING]${NC} $*"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $*" >&2
}

# Environment Configuration with Sensible Defaults
load_deploy_env() {
    # Auto-detect or validate Project ID
    PROJECT_ID="${PROJECT_ID:-$(gcloud config get-value project 2>/dev/null || true)}"
    if [[ -z "$PROJECT_ID" || "$PROJECT_ID" == "(unset)" ]]; then
        log_error "PROJECT_ID is not set. Please set export PROJECT_ID='your-project-id' or configure with 'gcloud config set project'."
        exit 1
    fi

    REGION="${REGION:-${GOOGLE_CLOUD_REGION:-us-central1}}"
    ZONE="${ZONE:-${REGION}-a}"

    # Storage & Database Identifiers
    GCS_BUCKET="${GCS_BUCKET:-${PROJECT_ID}-dinnerguest-assets}"
    FIRESTORE_DATABASE="${FIRESTORE_DATABASE:-(default)}"
    
    # Cloud SQL Configuration
    CLOUD_SQL_INSTANCE="${CLOUD_SQL_INSTANCE:-dinnerguest-postgres}"
    CLOUD_SQL_DB="${CLOUD_SQL_DB:-dinnerguest}"
    CLOUD_SQL_USER="${CLOUD_SQL_USER:-dinnerguest_app}"
    CLOUD_SQL_PASSWORD="${CLOUD_SQL_PASSWORD:-DinnerGuest2026!Secure}"
    # Default to cost-effective tier, customizable via CLOUD_SQL_TIER
    CLOUD_SQL_TIER="${CLOUD_SQL_TIER:-db-custom-2-7680}"
    CLOUD_SQL_POSTGRES_VERSION="${CLOUD_SQL_POSTGRES_VERSION:-POSTGRES_15}"

    # Service Account & Agent Engine
    SERVICE_ACCOUNT_NAME="${SERVICE_ACCOUNT_NAME:-dinnerguest-agent-sa}"
    SERVICE_ACCOUNT_EMAIL="${SERVICE_ACCOUNT_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
    AGENT_SERVICE_NAME="${AGENT_SERVICE_NAME:-dinnerguest-agent}"

    export PROJECT_ID REGION ZONE GCS_BUCKET FIRESTORE_DATABASE \
           CLOUD_SQL_INSTANCE CLOUD_SQL_DB CLOUD_SQL_USER CLOUD_SQL_PASSWORD \
           CLOUD_SQL_TIER CLOUD_SQL_POSTGRES_VERSION SERVICE_ACCOUNT_NAME \
           SERVICE_ACCOUNT_EMAIL AGENT_SERVICE_NAME
}

# Verify Prerequisites
check_prerequisites() {
    log_info "Verifying deployment prerequisites..."
    if ! command -v gcloud &> /dev/null; then
        log_error "gcloud CLI is not installed or not in PATH."
        exit 1
    fi

    local active_account
    active_account=$(gcloud auth list --filter=status:ACTIVE --format="value(account)" 2>/dev/null || true)
    if [[ -z "$active_account" ]]; then
        log_error "No active gcloud authentication found. Please run 'gcloud auth login'."
        exit 1
    fi

    log_info "Active Account: $active_account"
    log_info "Target Project: $PROJECT_ID"
    log_info "Target Region : $REGION"
}

# Enable GCP API Service if not already active
enable_service() {
    local service="$1"
    log_info "Checking if API '$service' is enabled..."
    if ! gcloud services list --enabled --filter="config.name:$service" --format="value(config.name)" | grep -q "$service"; then
        log_step "Enabling $service..."
        gcloud services enable "$service" --project="$PROJECT_ID"
        log_success "Enabled $service"
    else
        log_info "API $service is already enabled."
    fi
}
