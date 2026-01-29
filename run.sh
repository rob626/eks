#!/bin/bash
#
# Convenience wrapper for running the EKS node re-roll tool
# Usage: ./run.sh [OPTIONS]
#

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Configuration
IMAGE_NAME="eks-reroll:latest"
DOCKERFILE="Dockerfile"

# Functions
print_info() {
    echo -e "${BLUE}ℹ${NC} $1"
}

print_success() {
    echo -e "${GREEN}✓${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}⚠${NC} $1"
}

print_error() {
    echo -e "${RED}✗${NC} $1"
}

show_help() {
    cat << EOF
EKS Node Re-roll Tool - Convenience Wrapper

Usage: $0 [COMMAND] [OPTIONS]

Commands:
  build         Build the Docker image
  dry-run       Run in dry-run mode (preview only)
  run           Execute node re-roll (with confirmation)
  help          Show this help message

Options (pass after command):
  --nodepool NAME           Re-roll nodes from specific NodePool
  --label KEY=VALUE         Filter by label (can be used multiple times)
  --wait-between SECONDS    Wait time between nodes (default: 30)
  --drain-timeout SECONDS   Drain timeout (default: 300)
  --verbose                 Enable verbose logging
  --max-concurrent N        Max concurrent re-rolls (default: 1)

Examples:
  $0 dry-run                                    # Preview all nodes
  $0 dry-run --nodepool production              # Preview production nodes
  $0 run --nodepool production                  # Re-roll production nodes
  $0 run --verbose --wait-between 60            # Re-roll with custom wait

Environment Variables:
  KUBECONFIG      Path to kubeconfig (default: ~/.kube/config)
  AWS_PROFILE     AWS profile to use
  AWS_REGION      AWS region

EOF
}

check_prerequisites() {
    if ! command -v docker &> /dev/null; then
        print_error "Docker is not installed. Please install Docker first."
        exit 1
    fi

    if ! command -v kubectl &> /dev/null; then
        print_warning "kubectl not found in PATH, but may be available in Docker container"
    fi

    if [ ! -f "$HOME/.kube/config" ]; then
        print_error "Kubeconfig not found at $HOME/.kube/config"
        exit 1
    fi
}

build_image() {
    print_info "Building Docker image: $IMAGE_NAME"

    if [ ! -f "$DOCKERFILE" ]; then
        print_error "Dockerfile not found in current directory"
        exit 1
    fi

    if docker build -t "$IMAGE_NAME" .; then
        print_success "Docker image built successfully"
    else
        print_error "Failed to build Docker image"
        exit 1
    fi
}

check_image_exists() {
    if ! docker image inspect "$IMAGE_NAME" &> /dev/null; then
        print_warning "Docker image not found. Building it now..."
        build_image
    fi
}

run_docker() {
    local args="$@"

    check_image_exists

    # Build docker run command
    local docker_cmd="docker run --rm"

    # Mount kubeconfig
    if [ -n "$KUBECONFIG" ]; then
        docker_cmd="$docker_cmd -v $KUBECONFIG:/root/.kube/config:ro"
    else
        docker_cmd="$docker_cmd -v $HOME/.kube:/root/.kube:ro"
    fi

    # Mount AWS credentials if they exist
    if [ -d "$HOME/.aws" ]; then
        docker_cmd="$docker_cmd -v $HOME/.aws:/root/.aws:ro"
    fi

    # Set environment variables
    if [ -n "$AWS_PROFILE" ]; then
        docker_cmd="$docker_cmd -e AWS_PROFILE=$AWS_PROFILE"
    fi

    if [ -n "$AWS_REGION" ]; then
        docker_cmd="$docker_cmd -e AWS_REGION=$AWS_REGION"
    fi

    # Add image and arguments
    docker_cmd="$docker_cmd $IMAGE_NAME $args"

    print_info "Running: $docker_cmd"
    eval $docker_cmd
}

run_dry_run() {
    print_info "Running in DRY-RUN mode (no changes will be made)"
    run_docker --dry-run "$@"
}

run_reroll() {
    print_warning "This will re-roll nodes in your cluster!"
    print_warning "Make sure you have reviewed the changes with --dry-run first"
    echo ""

    # Check if running in CI or with --yes flag
    if [ -t 0 ] && [[ ! "$@" =~ "--yes" ]]; then
        read -p "Are you sure you want to continue? (yes/no): " -r
        echo
        if [[ ! $REPLY =~ ^[Yy][Ee][Ss]$ ]]; then
            print_info "Cancelled by user"
            exit 0
        fi
    fi

    print_info "Starting node re-roll..."
    run_docker "$@"
}

# Main script logic
main() {
    check_prerequisites

    # Parse command
    local command="${1:-help}"
    shift || true

    case "$command" in
        build)
            build_image
            ;;
        dry-run)
            run_dry_run "$@"
            ;;
        run)
            run_reroll "$@"
            ;;
        help|--help|-h)
            show_help
            ;;
        *)
            print_error "Unknown command: $command"
            echo ""
            show_help
            exit 1
            ;;
    esac
}

# Run main function
main "$@"
