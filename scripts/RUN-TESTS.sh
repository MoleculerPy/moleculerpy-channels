#!/bin/bash
#
# MoleculerPy Channels - Test Runner
#
# Usage:
#   ./RUN-TESTS.sh unit         # Unit tests only (fast, no Docker)
#   ./RUN-TESTS.sh integration  # Integration tests (requires Docker)
#   ./RUN-TESTS.sh e2e          # End-to-end tests (requires Docker)
#   ./RUN-TESTS.sh all          # Full suite (requires Docker)
#   ./RUN-TESTS.sh coverage     # With coverage report (requires Docker)
#

set -e

# Auto-detect project root from script location
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$SCRIPT_DIR"

# Use venv python if available, otherwise system python
if [ -f "$PROJECT_ROOT/.venv/bin/python" ]; then
    PYTHON="$PROJECT_ROOT/.venv/bin/python"
elif [ -n "$VIRTUAL_ENV" ]; then
    PYTHON="$VIRTUAL_ENV/bin/python"
else
    PYTHON="python3"
fi
PYTEST="$PYTHON -m pytest"

# Docker container names (matching docker-compose.yml)
REDIS_CONTAINER="moleculerpy-redis"
NATS_CONTAINER="moleculerpy-nats"

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BLUE='\033[0;34m'
NC='\033[0m'

# Helper functions
success() { echo -e "${GREEN}[OK] $1${NC}"; }
warning() { echo -e "${YELLOW}[WARN] $1${NC}"; }
error()   { echo -e "${RED}[ERR] $1${NC}"; }
info()    { echo -e "${BLUE}[INFO] $1${NC}"; }

# Check Docker services
check_docker() {
    info "Checking Docker services..."

    if ! docker ps --format '{{.Names}}' | grep -q "$REDIS_CONTAINER"; then
        error "Redis not running! Start with: docker compose up -d (from MoleculerPy root)"
        exit 1
    fi
    success "Redis running"

    if ! docker ps --format '{{.Names}}' | grep -q "$NATS_CONTAINER"; then
        error "NATS not running! Start with: docker compose up -d (from MoleculerPy root)"
        exit 1
    fi
    success "NATS running"
}

# Test health
test_health() {
    info "Testing connections..."

    if docker exec "$REDIS_CONTAINER" redis-cli ping 2>/dev/null | grep -q "PONG"; then
        success "Redis responding"
    else
        error "Redis not responding"
        exit 1
    fi

    if docker exec "$NATS_CONTAINER" nats-server --help >/dev/null 2>&1 || \
       timeout 2 bash -c "echo > /dev/tcp/localhost/4222" 2>/dev/null; then
        success "NATS responding"
    else
        warning "NATS health check inconclusive, proceeding anyway"
    fi
}

# Run tests
run_unit() {
    echo -e "\n${YELLOW}Running unit tests...${NC}"
    cd "$PROJECT_ROOT"
    $PYTEST tests/unit/ -v --tb=short
}

run_integration() {
    echo -e "\n${YELLOW}Running integration tests...${NC}"
    check_docker
    test_health
    cd "$PROJECT_ROOT"
    $PYTEST tests/integration/ -v --tb=short -m integration
}

run_e2e() {
    echo -e "\n${YELLOW}Running e2e tests...${NC}"
    check_docker
    test_health
    cd "$PROJECT_ROOT"
    $PYTEST tests/ -v --tb=short -m e2e
}

run_all() {
    echo -e "\n${YELLOW}Running all tests...${NC}"
    check_docker
    test_health
    cd "$PROJECT_ROOT"
    $PYTEST tests/ -v --tb=short
}

run_coverage() {
    echo -e "\n${YELLOW}Running tests with coverage...${NC}"
    check_docker
    test_health
    cd "$PROJECT_ROOT"
    $PYTEST tests/ -v \
        --cov=moleculerpy_channels \
        --cov-report=html \
        --cov-report=term-missing
    echo -e "\n${GREEN}Coverage report: $PROJECT_ROOT/htmlcov/index.html${NC}"
}

# Main
case "${1:-help}" in
    unit)
        run_unit
        ;;
    integration)
        run_integration
        ;;
    e2e)
        run_e2e
        ;;
    all)
        run_all
        ;;
    coverage)
        run_coverage
        ;;
    *)
        echo "Usage: $0 {unit|integration|e2e|all|coverage}"
        echo ""
        echo "  unit         Unit tests only (no Docker needed)"
        echo "  integration  Integration tests (Redis + NATS required)"
        echo "  e2e          End-to-end tests (Redis + NATS required)"
        echo "  all          Full test suite"
        echo "  coverage     Full suite with coverage report"
        exit 0
        ;;
esac

success "Done!"
