#!/usr/bin/env bash
#
# Unified Deployment Orchestrator for Dinnerguest on Google Cloud.
# Executes all component deployments sequentially by default, or allows
# targeted deployment/re-deployment of individual services.
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/deploy_common.sh"

load_deploy_env

show_help() {
    cat << EOF
Dinnerguest Cloud Deployment Orchestrator

Usage:
  ./scripts/deploy_all.sh [OPTIONS]
  ./scripts/deploy_all.sh [SERVICE_NAME]

Options:
  --all                   Deploy all components sequentially (Default)
  --service <name>        Deploy or re-deploy only the specified component
                          Supported: gcs, firestore, cloud-sql, agent-engine
  --skip <name>           Skip a specific component during deployment (can specify multiple)
  --help, -h              Show this help menu

Direct Service Shortcuts:
  ./scripts/deploy_all.sh gcs           Deploy only Cloud Storage
  ./scripts/deploy_all.sh firestore     Deploy only Firestore
  ./scripts/deploy_all.sh cloud-sql     Deploy only Cloud SQL (PostgreSQL + pgvector)
  ./scripts/deploy_all.sh agent-engine  Deploy only Agent Engine / Cloud Run

Note:
  Each individual script under scripts/deploy_*.sh can also be executed
  directly and independently with full idempotence.
EOF
}

TARGET_SERVICE="all"
SKIP_SERVICES=()

while [[ $# -gt 0 ]]; do
    case "$1" in
        --help|-h)
            show_help
            exit 0
            ;;
        --all)
            TARGET_SERVICE="all"
            shift
            ;;
        --service)
            if [[ -z "${2:-}" ]]; then
                log_error "Missing value for --service."
                exit 1
            fi
            TARGET_SERVICE="$2"
            shift 2
            ;;
        --skip)
            if [[ -z "${2:-}" ]]; then
                log_error "Missing value for --skip."
                exit 1
            fi
            SKIP_SERVICES+=("$2")
            shift 2
            ;;
        gcs|storage)
            TARGET_SERVICE="gcs"
            shift
            ;;
        firestore|db)
            TARGET_SERVICE="firestore"
            shift
            ;;
        cloud-sql|cloudsql|postgres|sql)
            TARGET_SERVICE="cloud-sql"
            shift
            ;;
        agent-engine|agent|cloud-run|run)
            TARGET_SERVICE="agent-engine"
            shift
            ;;
        *)
            log_error "Unknown option or service: $1"
            show_help
            exit 1
            ;;
    esac
done

should_skip() {
    local svc="$1"
    for skip in "${SKIP_SERVICES[@]}"; do
        if [[ "$skip" == "$svc" ]]; then
            return 0
        fi
    done
    return 1
}

run_component() {
    local name="$1"
    local script="$2"
    
    if should_skip "$name"; then
        log_warn "Skipping component '$name' as requested."
        return 0
    fi

    echo ""
    log_step "=========================================================="
    log_step "Component: $name"
    log_step "=========================================================="
    
    local start_time
    start_time=$(date +%s)
    
    bash "${SCRIPT_DIR}/${script}"
    
    local end_time
    end_time=$(date +%s)
    local duration=$((end_time - start_time))
    log_success "Finished '$name' in ${duration}s."
}

# Main Execution Flow
check_prerequisites

echo ""
echo -e "${BOLD}${CYAN}==========================================================${NC}"
echo -e "${BOLD}${CYAN}      Dinnerguest Google Cloud Deployment Pipeline        ${NC}"
echo -e "${BOLD}${CYAN}==========================================================${NC}"
echo -e "Project ID : ${GREEN}${PROJECT_ID}${NC}"
echo -e "Region     : ${GREEN}${REGION}${NC}"
echo -e "Target     : ${GREEN}${TARGET_SERVICE}${NC}"
echo ""

TOTAL_START=$(date +%s)

case "$TARGET_SERVICE" in
    gcs)
        run_component "Google Cloud Storage" "deploy_gcs.sh"
        ;;
    firestore)
        run_component "Google Cloud Firestore" "deploy_firestore.sh"
        ;;
    cloud-sql)
        run_component "Cloud SQL (PostgreSQL + pgvector)" "deploy_cloud_sql.sh"
        ;;
    agent-engine)
        run_component "Dinnerguest Agent Engine / Cloud Run" "deploy_agent_engine.sh"
        ;;
    all)
        log_info "Initiating full sequential deployment of all components..."
        run_component "gcs" "deploy_gcs.sh"
        run_component "firestore" "deploy_firestore.sh"
        run_component "cloud-sql" "deploy_cloud_sql.sh"
        run_component "agent-engine" "deploy_agent_engine.sh"
        ;;
    *)
        log_error "Unrecognized target service: $TARGET_SERVICE"
        exit 1
        ;;
esac

TOTAL_END=$(date +%s)
TOTAL_DURATION=$((TOTAL_END - TOTAL_START))

echo ""
echo -e "${BOLD}${GREEN}==========================================================${NC}"
echo -e "${BOLD}${GREEN}     Dinnerguest Deployment Completed in ${TOTAL_DURATION}s!         ${NC}"
echo -e "${BOLD}${GREEN}==========================================================${NC}"
echo -e "For individual service updates in the future, run:"
echo -e "  ${CYAN}./scripts/deploy_all.sh --service <gcs|firestore|cloud-sql|agent-engine>${NC}"
echo -e "Or run the dedicated script directly, e.g.:"
echo -e "  ${CYAN}./scripts/deploy_cloud_sql.sh${NC}"
echo ""
