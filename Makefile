.PHONY: help build run dry-run clean install test

# Default target
help:
	@echo "EKS Node Re-roll Tool - Available targets:"
	@echo ""
	@echo "  make build       - Build the Docker image"
	@echo "  make run         - Run node re-roll (WARNING: will modify cluster)"
	@echo "  make dry-run     - Run in dry-run mode (safe, shows what would happen)"
	@echo "  make shell       - Open a shell in the container"
	@echo "  make clean       - Remove Docker image"
	@echo "  make install     - Install Python dependencies locally"
	@echo "  make test        - Run tests (if available)"
	@echo ""
	@echo "Docker-based commands:"
	@echo "  make docker-run ARGS='--nodepool my-pool'  - Run with custom arguments"
	@echo ""
	@echo "Local Python commands:"
	@echo "  make local-dry-run    - Run locally with dry-run"
	@echo "  make local-run        - Run locally (requires Python setup)"
	@echo ""
	@echo "Examples:"
	@echo "  make dry-run                                    # Preview changes"
	@echo "  make docker-run ARGS='--nodepool production'    # Re-roll specific pool"
	@echo "  make docker-run ARGS='--verbose --dry-run'      # Verbose dry-run"

# Build Docker image
build:
	@echo "Building Docker image..."
	docker build -t eks-reroll:latest .
	@echo "Build complete!"

# Run with dry-run (safe)
dry-run: build
	@echo "Running in DRY-RUN mode (no changes will be made)..."
	docker-compose run --rm reroll --dry-run

# Run node re-roll (WARNING: modifies cluster)
run: build
	@echo "WARNING: This will re-roll nodes in your cluster!"
	@echo "Press Ctrl+C to cancel, or wait 5 seconds to continue..."
	@sleep 5
	docker-compose run --rm reroll

# Run with custom arguments
docker-run: build
	docker-compose run --rm reroll $(ARGS)

# Open a shell in the container
shell: build
	docker-compose run --rm --entrypoint /bin/bash reroll

# Clean up Docker image
clean:
	@echo "Removing Docker image..."
	docker rmi eks-reroll:latest || true
	@echo "Cleanup complete!"

# Install Python dependencies locally
install:
	@echo "Installing Python dependencies..."
	pip install -r requirements.txt
	@echo "Installation complete!"

# Run locally with dry-run
local-dry-run: install
	@echo "Running locally in DRY-RUN mode..."
	python reroll_nodes.py --dry-run

# Run locally (WARNING: modifies cluster)
local-run: install
	@echo "WARNING: This will re-roll nodes in your cluster!"
	@echo "Press Ctrl+C to cancel, or wait 5 seconds to continue..."
	@sleep 5
	python reroll_nodes.py

# Run tests (placeholder)
test:
	@echo "No tests defined yet"

# Check kubectl access
check-access:
	@echo "Checking kubectl access..."
	@kubectl get nodes
	@echo "Access confirmed!"

# Show current nodes
show-nodes:
	@echo "Current Karpenter-managed nodes:"
	@kubectl get nodes -l karpenter.sh/nodepool --show-labels

# Show Karpenter version
show-version:
	@echo "Karpenter version:"
	@kubectl get deployment -n karpenter karpenter -o jsonpath='{.spec.template.spec.containers[0].image}'
	@echo ""
