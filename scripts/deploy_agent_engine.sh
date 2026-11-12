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
    "roles/storage.objectViewer"
    "roles/logging.logWriter"
)

log_step "Binding IAM roles to $SERVICE_ACCOUNT_EMAIL..."
for role in "${ROLES[@]}"; do
    gcloud projects add-iam-policy-binding "$PROJECT_ID" \
        --member="serviceAccount:$SERVICE_ACCOUNT_EMAIL" \
        --role="$role" \
        --quiet &>/dev/null || true
done
log_success "IAM roles configured."

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
    --set-env-vars="PROJECT_ID=${PROJECT_ID},GOOGLE_CLOUD_PROJECT=${PROJECT_ID},GOOGLE_CLOUD_LOCATION=${REGION},GCS_BUCKET=${GCS_BUCKET},FIRESTORE_DATABASE=${FIRESTORE_DATABASE},CLOUD_SQL_INSTANCE=${CLOUD_SQL_INSTANCE},CLOUD_SQL_DB=${CLOUD_SQL_DB},CLOUD_SQL_USER=${CLOUD_SQL_USER},CLOUD_SQL_CONNECTION_NAME=${SQL_CONNECTION}" \
    --allow-unauthenticated \
    --memory="1Gi" \
    --cpu="1" \
    --min-instances=0 \
    --max-instances=5 \
    "${CLOUDSQL_FLAG[@]}" \
    --quiet

SERVICE_URL=$(gcloud run services describe "$AGENT_SERVICE_NAME" --project="$PROJECT_ID" --region="$REGION" --format="value(status.url)")

log_success "Agent Engine / Cloud Run service deployed successfully!"
log_info "Service URL: $SERVICE_URL"
log_info "Health Check: ${SERVICE_URL}/healthz"
