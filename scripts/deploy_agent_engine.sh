#!/usr/bin/env bash
#
# Deployment script for Dinnerguest Agent Engine / Cloud Run Agent Service.
# Provisions IAM roles, builds the container image via Cloud Build,
# and deploys the ADK Agent service connected to Cloud SQL, Firestore, and GCS.
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/deploy_common.sh"

load_deploy_env
check_prerequisites

log_step "Deploying Dinnerguest Agent Engine / Cloud Run Component"

# 1. Enable Required Cloud APIs
enable_service "aiplatform.googleapis.com"
enable_service "run.googleapis.com"
enable_service "artifactregistry.googleapis.com"
enable_service "cloudbuild.googleapis.com"
enable_service "secretmanager.googleapis.com"
enable_service "cloudtrace.googleapis.com"
enable_service "logging.googleapis.com"
gcloud services enable "telemetry.googleapis.com" "observability.googleapis.com" --project="$PROJECT_ID" --quiet &>/dev/null || true
ensure_db_password_secret

# 2. Setup Dedicated Service Account
log_info "Checking Service Account '$SERVICE_ACCOUNT_EMAIL'..."
if gcloud iam service-accounts describe "$SERVICE_ACCOUNT_EMAIL" --project="$PROJECT_ID" &>/dev/null; then
    log_info "Service account '$SERVICE_ACCOUNT_EMAIL' already exists."
else
    log_step "Creating Service Account '$SERVICE_ACCOUNT_NAME'..."
    gcloud iam service-accounts create "$SERVICE_ACCOUNT_NAME" \
        --project="$PROJECT_ID" \
        --display-name="Dinnerguest Agent Engine Service Account"
    log_success "Created Service Account '$SERVICE_ACCOUNT_NAME'"
fi

# 3. Grant Least-Privilege IAM Roles
ROLES=(
    "roles/aiplatform.user"
    "roles/datastore.user"
    "roles/cloudsql.client"
    "roles/secretmanager.secretAccessor"
    "roles/storage.objectViewer"
    "roles/logging.logWriter"
    "roles/cloudtrace.agent"
    "roles/telemetry.tracesWriter"
)

log_step "Binding IAM roles to $SERVICE_ACCOUNT_EMAIL..."
for role in "${ROLES[@]}"; do
    gcloud projects add-iam-policy-binding "$PROJECT_ID" \
        --member="serviceAccount:$SERVICE_ACCOUNT_EMAIL" \
        --role="$role" \
        --quiet &>/dev/null || true
done
gcloud secrets add-iam-policy-binding "$CLOUD_SQL_PASSWORD_SECRET" \
    --project="$PROJECT_ID" \
    --member="serviceAccount:${SERVICE_ACCOUNT_EMAIL}" \
    --role="roles/secretmanager.secretAccessor" \
    --quiet &>/dev/null || true
log_success "IAM roles and Secret Manager access configured."

# 4. Setup Artifact Registry Repository
DOCKER_REPO="dinnerguest-repo"
log_info "Checking Artifact Registry repository '$DOCKER_REPO' in $REGION..."
if gcloud artifacts repositories describe "$DOCKER_REPO" --location="$REGION" --project="$PROJECT_ID" &>/dev/null; then
    log_info "Artifact Registry repository '$DOCKER_REPO' already exists."
else
    log_step "Creating Artifact Registry Docker repository '$DOCKER_REPO'..."
    gcloud artifacts repositories create "$DOCKER_REPO" \
        --project="$PROJECT_ID" \
        --repository-format=docker \
        --location="$REGION" \
        --description="Docker repository for Dinnerguest agents"
    log_success "Created Artifact Registry repository '$DOCKER_REPO'"
fi

# Ensure Cloud Build service accounts have permission to push images to Artifact Registry
PROJECT_NUMBER=$(gcloud projects describe "$PROJECT_ID" --format="value(projectNumber)" 2>/dev/null || true)
if [[ -n "$PROJECT_NUMBER" ]]; then
    log_info "Configuring Artifact Registry Writer permissions for Cloud Build service accounts..."
    for build_sa in "${PROJECT_NUMBER}@cloudbuild.gserviceaccount.com" "${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"; do
        gcloud artifacts repositories add-iam-policy-binding "$DOCKER_REPO" \
            --location="$REGION" \
            --project="$PROJECT_ID" \
            --member="serviceAccount:${build_sa}" \
            --role="roles/artifactregistry.writer" \
            --quiet &>/dev/null || true
    done
fi

# 5. Build Container Image via Cloud Build
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
IMAGE_URI="${REGION}-docker.pkg.dev/${PROJECT_ID}/${DOCKER_REPO}/${AGENT_SERVICE_NAME}:latest"

log_step "Building and pushing container image to $IMAGE_URI via Cloud Build..."
gcloud builds submit "$REPO_ROOT" \
    --project="$PROJECT_ID" \
    --tag="$IMAGE_URI"
log_success "Container image built and pushed successfully."

# 6. Retrieve Cloud SQL Connection Name (if available)
SQL_CONNECTION=""
if gcloud sql instances describe "$CLOUD_SQL_INSTANCE" --project="$PROJECT_ID" &>/dev/null; then
    SQL_CONNECTION=$(gcloud sql instances describe "$CLOUD_SQL_INSTANCE" --project="$PROJECT_ID" --format="value(connectionName)")
    log_info "Cloud SQL connection attached: $SQL_CONNECTION"
fi

# 7. Deploy to Cloud Run / Vertex AI Agent Engine Host
log_step "Deploying $AGENT_SERVICE_NAME to Cloud Run in $REGION..."

CLOUDSQL_FLAG=()
if [[ -n "$SQL_CONNECTION" ]]; then
    CLOUDSQL_FLAG=("--set-cloudsql-instances=${SQL_CONNECTION}")
fi

gcloud run deploy "$AGENT_SERVICE_NAME" \
    --project="$PROJECT_ID" \
    --image="$IMAGE_URI" \
    --region="$REGION" \
    --platform="managed" \
    --service-account="$SERVICE_ACCOUNT_EMAIL" \
    --set-env-vars="PROJECT_ID=${PROJECT_ID},GOOGLE_CLOUD_PROJECT=${PROJECT_ID},GOOGLE_CLOUD_LOCATION=${REGION},GCS_BUCKET=${GCS_BUCKET},FIRESTORE_DATABASE=${FIRESTORE_DATABASE},USE_FIRESTORE=true,ENABLE_CLOUD_TRACE=true,CLOUD_SQL_INSTANCE=${CLOUD_SQL_INSTANCE},CLOUD_SQL_DB=${CLOUD_SQL_DB},CLOUD_SQL_USER=${CLOUD_SQL_USER},CLOUD_SQL_PASSWORD_SECRET_ID=${CLOUD_SQL_PASSWORD_SECRET},CLOUD_SQL_CONNECTION_NAME=${SQL_CONNECTION},GEN_MODEL=${GEN_MODEL},REASONING_MODEL=${REASONING_MODEL}" \
    --set-secrets="CLOUD_SQL_PASSWORD=${CLOUD_SQL_PASSWORD_SECRET}:latest" \
    --port=8080 \
    --timeout=300 \
    --allow-unauthenticated \
    --memory="1Gi" \
    --cpu="1" \
    --min-instances=0 \
    --max-instances=5 \
    "${CLOUDSQL_FLAG[@]}" \
    --quiet

# Grant invoker permission to active deploying account
ACTIVE_ACCOUNT=$(gcloud config get-value account 2>/dev/null || true)
if [[ -n "$ACTIVE_ACCOUNT" && "$ACTIVE_ACCOUNT" != "(unset)" ]]; then
    gcloud run services add-iam-policy-binding "$AGENT_SERVICE_NAME" \
        --project="$PROJECT_ID" \
        --region="$REGION" \
        --member="user:${ACTIVE_ACCOUNT}" \
        --role="roles/run.invoker" \
        --quiet &>/dev/null || true
fi

if [[ -n "${PROJECT_NUMBER:-}" ]]; then
    SERVICE_URL="https://${AGENT_SERVICE_NAME}-${PROJECT_NUMBER}.${REGION}.run.app"
else
    SERVICE_URL=$(gcloud run services describe "$AGENT_SERVICE_NAME" --project="$PROJECT_ID" --region="$REGION" --format="value(status.url)")
fi

log_success "Agent Engine / Cloud Run service deployed successfully!"
log_info "Service URL: $SERVICE_URL"
log_info "Health Check (authenticated):"
log_info "  curl -H \"Authorization: Bearer \$(gcloud auth print-identity-token)\" ${SERVICE_URL}/health"
log_info "Root Endpoint:"
log_info "  curl -H \"Authorization: Bearer \$(gcloud auth print-identity-token)\" ${SERVICE_URL}/"
