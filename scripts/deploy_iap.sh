#!/usr/bin/env bash
#
# Deployment script for Identity-Aware Proxy (IAP) and External HTTPS Load Balancer
# Secures Dinnerguest Agent Engine behind Google Cloud IAP for authorized users.
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/deploy_common.sh"

load_deploy_env
check_prerequisites

log_step "Deploying Dinnerguest Identity-Aware Proxy (IAP) & Load Balancer"

# Configuration variables
DOMAIN_NAME="${DOMAIN_NAME:-dinnerguest.jwalcik.demo.altostrat.com}"
IAP_USER="${IAP_USER:-jwalcik@jwalcik.altostrat.com}"
DNS_PROJECT_ID="${DNS_PROJECT_ID:-}"
DNS_ZONE_NAME="${DNS_ZONE_NAME:-}"

STATIC_IP_NAME="${STATIC_IP_NAME:-dinnerguest-lb-ip}"
NEG_NAME="${NEG_NAME:-dinnerguest-agent-neg}"
SSL_CERT_NAME="${SSL_CERT_NAME:-dinnerguest-ssl-cert}"
BACKEND_SERVICE_NAME="${BACKEND_SERVICE_NAME:-dinnerguest-backend}"
URL_MAP_NAME="${URL_MAP_NAME:-dinnerguest-url-map}"
HTTPS_PROXY_NAME="${HTTPS_PROXY_NAME:-dinnerguest-https-proxy}"
FORWARDING_RULE_NAME="${FORWARDING_RULE_NAME:-dinnerguest-fwd-rule-https}"
HTTP_REDIRECT_MAP_NAME="${HTTP_REDIRECT_MAP_NAME:-dinnerguest-http-redirect}"
HTTP_PROXY_NAME="${HTTP_PROXY_NAME:-dinnerguest-http-proxy}"
HTTP_FORWARDING_RULE_NAME="${HTTP_FORWARDING_RULE_NAME:-dinnerguest-fwd-rule-http}"

# 1. Enable Required Cloud APIs
enable_service "compute.googleapis.com"
enable_service "iap.googleapis.com"
enable_service "secretmanager.googleapis.com"

# 2. Check / Guide OAuth Credentials for IAP
log_info "Verifying OAuth 2.0 Credentials for Identity-Aware Proxy..."
IAP_CLIENT_ID="${IAP_CLIENT_ID:-}"
IAP_CLIENT_SECRET="${IAP_CLIENT_SECRET:-}"

# Check Secret Manager if environment variables are not set
if [[ -z "$IAP_CLIENT_ID" ]]; then
    if gcloud secrets describe "dinnerguest-iap-client-id" --project="$PROJECT_ID" --quiet < /dev/null &>/dev/null; then
        IAP_CLIENT_ID=$(gcloud secrets versions access latest --secret="dinnerguest-iap-client-id" --project="$PROJECT_ID" --quiet < /dev/null 2>/dev/null || true)
        if [[ -n "$IAP_CLIENT_ID" ]]; then
            log_info "Loaded IAP_CLIENT_ID from Secret Manager."
        fi
    fi
fi

if [[ -z "$IAP_CLIENT_SECRET" ]]; then
    if gcloud secrets describe "dinnerguest-iap-client-secret" --project="$PROJECT_ID" --quiet < /dev/null &>/dev/null; then
        IAP_CLIENT_SECRET=$(gcloud secrets versions access latest --secret="dinnerguest-iap-client-secret" --project="$PROJECT_ID" --quiet < /dev/null 2>/dev/null || true)
        if [[ -n "$IAP_CLIENT_SECRET" ]]; then
            log_info "Loaded IAP_CLIENT_SECRET from Secret Manager."
        fi
    fi
fi

# Guide creation if still missing
if [[ -z "$IAP_CLIENT_ID" || -z "$IAP_CLIENT_SECRET" ]]; then
    echo ""
    log_warn "Identity-Aware Proxy (IAP) requires an OAuth 2.0 Client ID and Client Secret."
    log_info "Please configure the OAuth Consent Screen and create credentials if you haven't already:"
    log_info "1. Configure OAuth Consent Screen (Internal):"
    log_info "   https://console.cloud.google.com/apis/credentials/consent?project=${PROJECT_ID}"
    log_info "2. Create OAuth 2.0 Client ID (Application type: 'Web application'):"
    log_info "   https://console.cloud.google.com/apis/credentials?project=${PROJECT_ID}"
    log_info "   - Step A: Set Name to 'Dinnerguest IAP Client' and leave Authorized redirect URIs blank. Click 'Create'."
    log_info "   - Step B: Copy the generated Client ID and Client Secret."
    log_info "   - Step C: Click Edit (pencil icon) on the newly created client, click '+ Add URI', and paste:"
    log_info "             https://iap.googleapis.com/v1/oauth/clientIds/<YOUR_CLIENT_ID>:handleRedirect"
    log_info "             (replacing <YOUR_CLIENT_ID> with the actual Client ID from Step B), then click 'Save'."
    echo ""
    
    if [ -t 0 ]; then
        read -rp "Enter IAP OAuth Client ID: " IAP_CLIENT_ID
        read -rsp "Enter IAP OAuth Client Secret: " IAP_CLIENT_SECRET
        echo ""
    fi

    if [[ -z "$IAP_CLIENT_ID" || -z "$IAP_CLIENT_SECRET" ]]; then
        log_error "IAP_CLIENT_ID or IAP_CLIENT_SECRET not provided."
        log_error "You can set them in your environment and re-run:"
        log_error "  export IAP_CLIENT_ID='your-client-id.apps.googleusercontent.com'"
        log_error "  export IAP_CLIENT_SECRET='your-client-secret'"
        log_error "  ./scripts/deploy_iap.sh"
        exit 1
    fi
fi

# 3. Reserve Global Static External IPv4 Address
log_step "Reserving Global External Static IP Address '$STATIC_IP_NAME'..."
if gcloud compute addresses describe "$STATIC_IP_NAME" --global --project="$PROJECT_ID" --quiet &>/dev/null; then
    log_info "Static IP '$STATIC_IP_NAME' already exists."
else
    gcloud compute addresses create "$STATIC_IP_NAME" \
        --network-tier=PREMIUM \
        --ip-version=IPV4 \
        --global \
        --project="$PROJECT_ID" \
        --quiet
    log_success "Created static IP '$STATIC_IP_NAME'"
fi

LB_IP=$(gcloud compute addresses describe "$STATIC_IP_NAME" --global --project="$PROJECT_ID" --format="value(address)")
log_info "Load Balancer Static IP: ${BOLD}${GREEN}${LB_IP}${NC}"

# 4. Serverless Network Endpoint Group (NEG) for Cloud Run
log_step "Configuring Serverless NEG for $AGENT_SERVICE_NAME..."
if gcloud compute network-endpoint-groups describe "$NEG_NAME" --region="$REGION" --project="$PROJECT_ID" --quiet &>/dev/null; then
    log_info "Serverless NEG '$NEG_NAME' already exists."
else
    gcloud compute network-endpoint-groups create "$NEG_NAME" \
        --region="$REGION" \
        --network-endpoint-type=SERVERLESS \
        --cloud-run-service="$AGENT_SERVICE_NAME" \
        --project="$PROJECT_ID" \
        --quiet
    log_success "Created Serverless NEG '$NEG_NAME'"
fi

# 5. Google-Managed SSL Certificate
log_step "Configuring Google-Managed SSL Certificate for domain '$DOMAIN_NAME'..."
if gcloud compute ssl-certificates describe "$SSL_CERT_NAME" --global --project="$PROJECT_ID" --quiet &>/dev/null; then
    log_info "SSL Certificate '$SSL_CERT_NAME' already exists."
else
    gcloud compute ssl-certificates create "$SSL_CERT_NAME" \
        --domains="$DOMAIN_NAME" \
        --global \
        --project="$PROJECT_ID" \
        --quiet
    log_success "Created Google-managed SSL Certificate '$SSL_CERT_NAME'"
fi

# 6. Global Backend Service with Serverless NEG
log_step "Configuring Backend Service '$BACKEND_SERVICE_NAME'..."
if gcloud compute backend-services describe "$BACKEND_SERVICE_NAME" --global --project="$PROJECT_ID" --quiet &>/dev/null; then
    EXISTING_PORT_NAME=$(gcloud compute backend-services describe "$BACKEND_SERVICE_NAME" --global --project="$PROJECT_ID" --format="value(portName)" 2>/dev/null || true)
    if [[ -n "$EXISTING_PORT_NAME" ]]; then
        log_warn "Existing Backend Service '$BACKEND_SERVICE_NAME' has portName='$EXISTING_PORT_NAME', which is incompatible with Serverless NEGs. Recreating..."
        gcloud compute backend-services delete "$BACKEND_SERVICE_NAME" --global --project="$PROJECT_ID" --quiet
    else
        log_info "Backend service '$BACKEND_SERVICE_NAME' already exists."
    fi
fi

if ! gcloud compute backend-services describe "$BACKEND_SERVICE_NAME" --global --project="$PROJECT_ID" --quiet &>/dev/null; then
    gcloud compute backend-services create "$BACKEND_SERVICE_NAME" \
        --load-balancing-scheme=EXTERNAL_MANAGED \
        --global \
        --project="$PROJECT_ID" \
        --quiet
    log_success "Created Backend Service '$BACKEND_SERVICE_NAME'"
fi

# Attach Serverless NEG to Backend Service if not already attached
if ! gcloud compute backend-services describe "$BACKEND_SERVICE_NAME" --global --project="$PROJECT_ID" --format="value(backends[].group)" 2>/dev/null | grep -q "$NEG_NAME"; then
    log_step "Attaching Serverless NEG '$NEG_NAME' to Backend Service '$BACKEND_SERVICE_NAME'..."
    gcloud compute backend-services add-backend "$BACKEND_SERVICE_NAME" \
        --global \
        --network-endpoint-group="$NEG_NAME" \
        --network-endpoint-group-region="$REGION" \
        --project="$PROJECT_ID" \
        --quiet
    log_success "Attached NEG to Backend Service."
else
    log_info "NEG is already attached to Backend Service."
fi

# 7. Enable Identity-Aware Proxy (IAP) on Backend Service
log_step "Enabling Identity-Aware Proxy on '$BACKEND_SERVICE_NAME'..."
gcloud compute backend-services update "$BACKEND_SERVICE_NAME" \
    --global \
    --iap=enabled,oauth2-client-id="${IAP_CLIENT_ID}",oauth2-client-secret="${IAP_CLIENT_SECRET}" \
    --project="$PROJECT_ID"
log_success "IAP enabled on '$BACKEND_SERVICE_NAME'"

# 8. Grant IAP Access to Authorized User
log_step "Granting IAP Web Access to user '${IAP_USER}'..."
gcloud iap web add-iam-policy-binding \
    --resource-type=backend-services \
    --service="$BACKEND_SERVICE_NAME" \
    --member="user:${IAP_USER}" \
    --role="roles/iap.httpsResourceAccessor" \
    --project="$PROJECT_ID" \
    --quiet
log_success "Granted 'roles/iap.httpsResourceAccessor' to ${IAP_USER}"

# 9. Provision IAP Service Identity and Grant Cloud Run Invoker Permissions
log_step "Provisioning Google Cloud IAP service identity..."
gcloud beta services identity create --service=iap.googleapis.com --project="$PROJECT_ID" --quiet 2>/dev/null || true

PROJECT_NUMBER=$(gcloud projects describe "$PROJECT_ID" --format="value(projectNumber)" 2>/dev/null || true)
if [[ -n "$PROJECT_NUMBER" ]]; then
    log_step "Granting Cloud Run Invoker role to Google Cloud IAP service agent..."
    IAP_SA="service-${PROJECT_NUMBER}@gcp-sa-iap.iam.gserviceaccount.com"
    gcloud run services add-iam-policy-binding "$AGENT_SERVICE_NAME" \
        --region="$REGION" \
        --project="$PROJECT_ID" \
        --member="serviceAccount:${IAP_SA}" \
        --role="roles/run.invoker" \
        --quiet

    log_step "Granting Cloud Run Invoker role to Serverless Robot service agent..."
    SERVERLESS_SA="service-${PROJECT_NUMBER}@serverless-robot-prod.iam.gserviceaccount.com"
    gcloud run services add-iam-policy-binding "$AGENT_SERVICE_NAME" \
        --region="$REGION" \
        --project="$PROJECT_ID" \
        --member="serviceAccount:${SERVERLESS_SA}" \
        --role="roles/run.invoker" \
        --quiet &>/dev/null || true
fi

# 10. URL Map for HTTPS Routing
log_step "Configuring URL Map '$URL_MAP_NAME'..."
if gcloud compute url-maps describe "$URL_MAP_NAME" --global --project="$PROJECT_ID" --quiet &>/dev/null; then
    log_info "URL Map '$URL_MAP_NAME' already exists."
else
    gcloud compute url-maps create "$URL_MAP_NAME" \
        --default-service="$BACKEND_SERVICE_NAME" \
        --global \
        --project="$PROJECT_ID" \
        --quiet
    log_success "Created URL Map '$URL_MAP_NAME'"
fi

# 11. Target HTTPS Proxy
log_step "Configuring Target HTTPS Proxy '$HTTPS_PROXY_NAME'..."
if gcloud compute target-https-proxies describe "$HTTPS_PROXY_NAME" --global --project="$PROJECT_ID" --quiet &>/dev/null; then
    log_info "Target HTTPS Proxy '$HTTPS_PROXY_NAME' already exists."
else
    gcloud compute target-https-proxies create "$HTTPS_PROXY_NAME" \
        --ssl-certificates="$SSL_CERT_NAME" \
        --url-map="$URL_MAP_NAME" \
        --global \
        --project="$PROJECT_ID" \
        --quiet
    log_success "Created Target HTTPS Proxy '$HTTPS_PROXY_NAME'"
fi

# 12. Global HTTPS Forwarding Rule (Port 443)
log_step "Configuring Global HTTPS Forwarding Rule '$FORWARDING_RULE_NAME'..."
if gcloud compute forwarding-rules describe "$FORWARDING_RULE_NAME" --global --project="$PROJECT_ID" --quiet &>/dev/null; then
    log_info "Forwarding Rule '$FORWARDING_RULE_NAME' already exists."
else
    gcloud compute forwarding-rules create "$FORWARDING_RULE_NAME" \
        --load-balancing-scheme=EXTERNAL_MANAGED \
        --network-tier=PREMIUM \
        --address="$STATIC_IP_NAME" \
        --target-https-proxy="$HTTPS_PROXY_NAME" \
        --ports=443 \
        --global \
        --project="$PROJECT_ID" \
        --quiet
    log_success "Created Global HTTPS Forwarding Rule '$FORWARDING_RULE_NAME'"
fi

# 13. HTTP to HTTPS Redirect (Port 80)
log_step "Configuring HTTP to HTTPS Redirect..."
if ! gcloud compute url-maps describe "$HTTP_REDIRECT_MAP_NAME" --global --project="$PROJECT_ID" --quiet &>/dev/null; then
    cat <<EOF | gcloud compute url-maps import "$HTTP_REDIRECT_MAP_NAME" --global --project="$PROJECT_ID" --quiet
name: ${HTTP_REDIRECT_MAP_NAME}
defaultUrlRedirect:
  httpsRedirect: true
  stripQuery: false
EOF
    log_success "Created HTTP redirect URL Map '$HTTP_REDIRECT_MAP_NAME'"
fi

if ! gcloud compute target-http-proxies describe "$HTTP_PROXY_NAME" --global --project="$PROJECT_ID" --quiet &>/dev/null; then
    gcloud compute target-http-proxies create "$HTTP_PROXY_NAME" \
        --url-map="$HTTP_REDIRECT_MAP_NAME" \
        --global \
        --project="$PROJECT_ID" \
        --quiet
    log_success "Created Target HTTP Proxy '$HTTP_PROXY_NAME'"
fi

if ! gcloud compute forwarding-rules describe "$HTTP_FORWARDING_RULE_NAME" --global --project="$PROJECT_ID" --quiet &>/dev/null; then
    gcloud compute forwarding-rules create "$HTTP_FORWARDING_RULE_NAME" \
        --load-balancing-scheme=EXTERNAL_MANAGED \
        --network-tier=PREMIUM \
        --address="$STATIC_IP_NAME" \
        --target-http-proxy="$HTTP_PROXY_NAME" \
        --ports=80 \
        --global \
        --project="$PROJECT_ID" \
        --quiet
    log_success "Created Global HTTP Forwarding Rule '$HTTP_FORWARDING_RULE_NAME'"
fi

# 14. Lock Down Cloud Run Ingress to Load Balancer / Internal Only
log_step "Enforcing Cloud Run ingress to internal-and-cloud-load-balancing..."
gcloud run services update "$AGENT_SERVICE_NAME" \
    --region="$REGION" \
    --project="$PROJECT_ID" \
    --ingress=internal-and-cloud-load-balancing \
    --quiet
log_success "Cloud Run service '$AGENT_SERVICE_NAME' ingress locked down to Load Balancer."

# 15. Cross-Project Cloud DNS Automation & Guidance
echo ""
log_step "=========================================================="
log_step "DNS Configuration for ${DOMAIN_NAME}"
log_step "=========================================================="
log_info "Allocated Load Balancer IP Address: ${BOLD}${GREEN}${LB_IP}${NC}"

DNS_UPDATED=false
if [[ -n "$DNS_PROJECT_ID" && -n "$DNS_ZONE_NAME" ]]; then
    log_info "Attempting automated Cloud DNS update in project '${DNS_PROJECT_ID}' (zone '${DNS_ZONE_NAME}')..."
    if gcloud dns record-sets describe "${DOMAIN_NAME}." --zone="$DNS_ZONE_NAME" --type="A" --project="$DNS_PROJECT_ID" --quiet &>/dev/null; then
        log_info "Record for '${DOMAIN_NAME}.' already exists, updating rrdatas..."
        if gcloud dns record-sets update "${DOMAIN_NAME}." \
            --zone="$DNS_ZONE_NAME" \
            --type="A" \
            --ttl=300 \
            --rrdatas="$LB_IP" \
            --project="$DNS_PROJECT_ID" --quiet; then
            DNS_UPDATED=true
            log_success "Cloud DNS record successfully updated in ${DNS_PROJECT_ID}!"
        fi
    else
        log_info "Creating new DNS A-record in zone '${DNS_ZONE_NAME}'..."
        if gcloud dns record-sets create "${DOMAIN_NAME}." \
            --zone="$DNS_ZONE_NAME" \
            --type="A" \
            --ttl=300 \
            --rrdatas="$LB_IP" \
            --project="$DNS_PROJECT_ID" --quiet; then
            DNS_UPDATED=true
            log_success "Cloud DNS record successfully created in ${DNS_PROJECT_ID}!"
        fi
    fi
fi

if [[ "$DNS_UPDATED" = false ]]; then
    echo ""
    log_warn "If your Cloud DNS zone is hosted in another GCP project, execute the following command in that project:"
    echo ""
    echo -e "  ${CYAN}gcloud dns record-sets create \"${DOMAIN_NAME}.\" \\${NC}"
    echo -e "  ${CYAN}    --project=\"<YOUR_DNS_PROJECT_ID>\" \\${NC}"
    echo -e "  ${CYAN}    --zone=\"<YOUR_DNS_ZONE_NAME>\" \\${NC}"
    echo -e "  ${CYAN}    --type=\"A\" \\${NC}"
    echo -e "  ${CYAN}    --ttl=300 \\${NC}"
    echo -e "  ${CYAN}    --rrdatas=\"${LB_IP}\"${NC}"
    echo ""
    log_info "Alternatively, export DNS_PROJECT_ID and DNS_ZONE_NAME before running this script to automate it:"
    log_info "  export DNS_PROJECT_ID='corp-dns-project'"
    log_info "  export DNS_ZONE_NAME='altostrat-demo-zone'"
    log_info "  ./scripts/deploy_iap.sh"
fi

echo ""
log_success "=========================================================="
log_success "Identity-Aware Proxy (IAP) & Load Balancer Deployment Complete!"
log_success "=========================================================="
log_info "Target URL        : https://${DOMAIN_NAME}"
log_info "Authorized User   : ${IAP_USER}"
log_info "Load Balancer IP  : ${LB_IP}"
log_info "Managed SSL State : Checking certificate provisioning status..."
gcloud compute ssl-certificates describe "$SSL_CERT_NAME" --global --project="$PROJECT_ID" --format="table(name,managed.status,managed.domainStatus)" 2>/dev/null || true
log_info "Note: Google-managed SSL certificate provisioning typically completes within 10-20 minutes after DNS points to ${LB_IP}."
