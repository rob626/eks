#!/bin/bash
#
# Verify that the EKS re-roll tool is properly set up
# This script checks prerequisites and validates the environment
#

set -e

# Colors
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Counters
PASSED=0
FAILED=0
WARNINGS=0

print_header() {
    echo -e "${BLUE}========================================${NC}"
    echo -e "${BLUE}EKS Node Re-roll Tool - Setup Verification${NC}"
    echo -e "${BLUE}========================================${NC}"
    echo ""
}

print_section() {
    echo -e "\n${BLUE}→ $1${NC}"
}

print_pass() {
    echo -e "  ${GREEN}✓${NC} $1"
    ((PASSED++))
}

print_fail() {
    echo -e "  ${RED}✗${NC} $1"
    ((FAILED++))
}

print_warn() {
    echo -e "  ${YELLOW}⚠${NC} $1"
    ((WARNINGS++))
}

print_info() {
    echo -e "  ${BLUE}ℹ${NC} $1"
}

# Check Docker
check_docker() {
    print_section "Checking Docker"

    if command -v docker &> /dev/null; then
        print_pass "Docker is installed"

        if docker ps &> /dev/null; then
            print_pass "Docker daemon is running"

            DOCKER_VERSION=$(docker --version | awk '{print $3}' | sed 's/,//')
            print_info "Docker version: $DOCKER_VERSION"
        else
            print_fail "Docker daemon is not running"
            print_info "Start Docker Desktop or docker daemon"
        fi
    else
        print_fail "Docker is not installed"
        print_info "Install from: https://docs.docker.com/get-docker/"
    fi

    if command -v docker-compose &> /dev/null || docker compose version &> /dev/null; then
        print_pass "Docker Compose is available"
    else
        print_warn "Docker Compose not found (optional but recommended)"
    fi
}

# Check kubectl
check_kubectl() {
    print_section "Checking kubectl"

    if command -v kubectl &> /dev/null; then
        print_pass "kubectl is installed"

        KUBECTL_VERSION=$(kubectl version --client -o json 2>/dev/null | grep -o '"gitVersion":"[^"]*"' | cut -d'"' -f4 || echo "unknown")
        print_info "kubectl version: $KUBECTL_VERSION"

        # Check kubeconfig
        if [ -f "$HOME/.kube/config" ]; then
            print_pass "Kubeconfig file exists at ~/.kube/config"

            # Try to connect to cluster
            if kubectl cluster-info &> /dev/null; then
                print_pass "Successfully connected to Kubernetes cluster"

                # Get cluster info
                CLUSTER_VERSION=$(kubectl version -o json 2>/dev/null | grep -o '"gitVersion":"[^"]*"' | tail -1 | cut -d'"' -f4 || echo "unknown")
                print_info "Cluster version: $CLUSTER_VERSION"

                # Check for Karpenter
                if kubectl get namespace karpenter &> /dev/null; then
                    print_pass "Karpenter namespace found"

                    if kubectl get deployment -n karpenter karpenter &> /dev/null; then
                        print_pass "Karpenter deployment found"

                        KARPENTER_IMAGE=$(kubectl get deployment -n karpenter karpenter -o jsonpath='{.spec.template.spec.containers[0].image}' 2>/dev/null)
                        print_info "Karpenter image: $KARPENTER_IMAGE"

                        # Check Karpenter version
                        if [[ "$KARPENTER_IMAGE" == *"v0.3"* ]] || [[ "$KARPENTER_IMAGE" == *"v0.4"* ]] || [[ "$KARPENTER_IMAGE" == *"v1."* ]]; then
                            print_pass "Karpenter version supports drift detection (v0.32+)"
                        else
                            print_warn "Karpenter version may not support drift detection"
                            print_info "Drift detection requires Karpenter v0.32 or later"
                        fi
                    else
                        print_warn "Karpenter deployment not found"
                    fi

                    # Check for NodePools
                    NODEPOOL_COUNT=$(kubectl get nodepools 2>/dev/null | tail -n +2 | wc -l || echo "0")
                    if [ "$NODEPOOL_COUNT" -gt 0 ]; then
                        print_pass "Found $NODEPOOL_COUNT NodePool(s)"
                    else
                        print_warn "No NodePools found (may be using v1alpha5 Provisioners)"

                        # Check for old-style Provisioners
                        PROV_COUNT=$(kubectl get provisioners 2>/dev/null | tail -n +2 | wc -l || echo "0")
                        if [ "$PROV_COUNT" -gt 0 ]; then
                            print_info "Found $PROV_COUNT Provisioner(s) (v1alpha5)"
                        fi
                    fi

                    # Check for Karpenter-managed nodes
                    NODE_COUNT=$(kubectl get nodes -l karpenter.sh/nodepool 2>/dev/null | tail -n +2 | wc -l || echo "0")
                    if [ "$NODE_COUNT" -gt 0 ]; then
                        print_pass "Found $NODE_COUNT Karpenter-managed node(s)"
                    else
                        # Try old label
                        NODE_COUNT=$(kubectl get nodes -l karpenter.sh/provisioner-name 2>/dev/null | tail -n +2 | wc -l || echo "0")
                        if [ "$NODE_COUNT" -gt 0 ]; then
                            print_pass "Found $NODE_COUNT Karpenter-managed node(s) (v1alpha5)"
                        else
                            print_warn "No Karpenter-managed nodes found"
                        fi
                    fi
                else
                    print_fail "Karpenter namespace not found"
                    print_info "Is Karpenter installed in your cluster?"
                fi
            else
                print_fail "Cannot connect to Kubernetes cluster"
                print_info "Check your kubeconfig and cluster access"
            fi
        else
            print_fail "No kubeconfig file found at ~/.kube/config"
            print_info "Configure kubectl first: kubectl config view"
        fi
    else
        print_warn "kubectl not found (will use kubectl from Docker image)"
    fi
}

# Check Python (optional)
check_python() {
    print_section "Checking Python (optional)"

    if command -v python3 &> /dev/null; then
        print_pass "Python 3 is installed"

        PYTHON_VERSION=$(python3 --version | awk '{print $2}')
        print_info "Python version: $PYTHON_VERSION"

        # Check if version is 3.8+
        MAJOR=$(echo $PYTHON_VERSION | cut -d. -f1)
        MINOR=$(echo $PYTHON_VERSION | cut -d. -f2)

        if [ "$MAJOR" -ge 3 ] && [ "$MINOR" -ge 8 ]; then
            print_pass "Python version is 3.8 or higher"

            # Check for kubernetes library
            if python3 -c "import kubernetes" &> /dev/null; then
                print_pass "Kubernetes Python library is installed"
            else
                print_info "Kubernetes library not installed (optional)"
                print_info "Install with: pip install -r requirements.txt"
            fi
        else
            print_warn "Python version is below 3.8"
        fi
    else
        print_info "Python 3 not found (not required if using Docker)"
    fi
}

# Check project files
check_project_files() {
    print_section "Checking project files"

    local files=(
        "reroll_nodes.py"
        "requirements.txt"
        "Dockerfile"
        "docker-compose.yml"
        "Makefile"
        "run.sh"
        "README.md"
    )

    for file in "${files[@]}"; do
        if [ -f "$file" ]; then
            print_pass "$file exists"
        else
            print_fail "$file is missing"
        fi
    done
}

# Check Docker image
check_docker_image() {
    print_section "Checking Docker image"

    if docker image inspect eks-reroll:latest &> /dev/null; then
        print_pass "Docker image 'eks-reroll:latest' is built"

        IMAGE_SIZE=$(docker image inspect eks-reroll:latest --format='{{.Size}}' | awk '{print $1/1024/1024 " MB"}')
        print_info "Image size: $IMAGE_SIZE"

        IMAGE_CREATED=$(docker image inspect eks-reroll:latest --format='{{.Created}}' | cut -d'T' -f1)
        print_info "Image created: $IMAGE_CREATED"
    else
        print_warn "Docker image not built yet"
        print_info "Build with: make build"
    fi
}

# Check AWS credentials (optional)
check_aws() {
    print_section "Checking AWS credentials (optional)"

    if [ -d "$HOME/.aws" ]; then
        print_pass "AWS credentials directory exists"

        if [ -f "$HOME/.aws/credentials" ]; then
            print_pass "AWS credentials file exists"
        fi

        if [ -f "$HOME/.aws/config" ]; then
            print_pass "AWS config file exists"
        fi
    else
        print_info "No AWS credentials found (may not be needed)"
    fi
}

# Print summary
print_summary() {
    echo ""
    echo -e "${BLUE}========================================${NC}"
    echo -e "${BLUE}Summary${NC}"
    echo -e "${BLUE}========================================${NC}"
    echo ""
    echo -e "  ${GREEN}Passed: ${PASSED}${NC}"
    echo -e "  ${YELLOW}Warnings: ${WARNINGS}${NC}"
    echo -e "  ${RED}Failed: ${FAILED}${NC}"
    echo ""

    if [ $FAILED -eq 0 ]; then
        echo -e "${GREEN}✓ Setup verification complete!${NC}"
        echo ""
        echo "Next steps:"
        echo "  1. Build the Docker image: make build"
        echo "  2. Preview changes: make dry-run"
        echo "  3. Execute re-roll: make run"
        echo ""
        echo "See README.md for detailed documentation"
        return 0
    else
        echo -e "${RED}✗ Some checks failed${NC}"
        echo ""
        echo "Please address the failed checks before proceeding"
        echo "See README.md for installation instructions"
        return 1
    fi
}

# Main execution
main() {
    print_header
    check_docker
    check_kubectl
    check_python
    check_project_files
    check_docker_image
    check_aws
    print_summary
}

main
