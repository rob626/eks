# EKS Node Re-roll Tool for Karpenter

Safely re-roll EKS worker nodes managed by Karpenter to update to new AMIs with the latest security fixes.

## Quick Reference

```bash
# Quick start (Docker)
make build          # Build Docker image
make dry-run        # Preview changes (always do this first!)
make run            # Execute re-roll

# Or use the convenience script
./run.sh dry-run    # Preview
./run.sh run        # Execute
```

See [EXAMPLES.md](EXAMPLES.md) for detailed use cases and scenarios.

## Overview

This project provides two complementary approaches for keeping your EKS nodes up-to-date:

1. **Automatic drift detection** - Karpenter automatically detects AMI changes and replaces nodes
2. **Manual re-roll script** - Python script to force immediate node updates when needed

## Prerequisites

### Docker (Recommended)
- Docker and Docker Compose
- kubectl configured with access to your EKS cluster (~/.kube/config)
- AWS credentials configured (~/.aws/credentials or environment variables)
- Karpenter v0.32+ (for drift detection feature)
- Appropriate IAM permissions (see below)

### Local Python (Alternative)
- Python 3.8+
- kubectl configured with access to your EKS cluster
- AWS credentials configured
- Karpenter v0.32+ (for drift detection feature)
- Appropriate IAM permissions (see below)

### Required IAM Permissions

The script requires the following AWS IAM permissions for EC2 instance termination:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "ec2:DescribeInstances",
        "ec2:TerminateInstances"
      ],
      "Resource": "*"
    }
  ]
}
```

If you don't have these permissions or prefer not to terminate EC2 instances directly, use the `--skip-ec2-termination` flag.

## Installation

### Option 1: Docker (Recommended)

The easiest way to use this tool is via Docker, which packages all dependencies:

```bash
# Build the Docker image
make build

# Or manually:
docker build -t eks-reroll:latest .

# Verify kubectl access
make check-access
```

### Option 2: Local Python

If you prefer to run locally without Docker:

```bash
# Install Python dependencies
make install

# Or manually:
pip install -r requirements.txt

# Ensure kubectl is configured
kubectl get nodes
```

## Quick Start

### Using Docker (Recommended)

```bash
# Dry run - preview what would happen (always run this first!)
make dry-run

# Re-roll all Karpenter nodes
make run

# Re-roll specific NodePool
make docker-run ARGS='--nodepool my-nodepool'

# Re-roll with verbose logging
make docker-run ARGS='--verbose --dry-run'

# Re-roll nodes with custom labels
make docker-run ARGS='--label env=prod'
```

### Using Local Python

```bash
# Dry run - preview what would happen
python reroll_nodes.py --dry-run

# Re-roll all Karpenter nodes
python reroll_nodes.py

# Re-roll specific NodePool
python reroll_nodes.py --nodepool my-nodepool
```

### Using docker-compose directly

```bash
# Dry run
docker-compose run --rm reroll --dry-run

# Re-roll all nodes
docker-compose run --rm reroll

# Re-roll specific NodePool
docker-compose run --rm reroll --nodepool my-nodepool

# Custom options
docker-compose run --rm reroll --verbose --wait-between 60
```

## Approach 1: Automatic Drift Detection (Recommended)

Karpenter v0.32+ includes drift detection that automatically identifies when node configuration (including AMI) has changed and replaces nodes accordingly.

### Configuration

Update your Karpenter NodePool to enable drift detection:

```yaml
apiVersion: karpenter.sh/v1beta1
kind: NodePool
metadata:
  name: default
spec:
  disruption:
    consolidationPolicy: WhenUnderutilized
    budgets:
      - nodes: "10%"  # Allow up to 10% of nodes to be disrupted at once
```

See `karpenter-nodepool-example.yaml` for a complete example.

### How It Works

1. You update the AMI in your EC2NodeClass (via ArgoCD)
2. Karpenter detects the drift automatically
3. Karpenter replaces nodes according to the disruption budget
4. Replacement happens gradually and safely

### Workflow

```bash
# 1. Update AMI in your config repo
# Update the EC2NodeClass with new AMI ID or AMI selector

# 2. Commit and push
git add .
git commit -m "Update to latest EKS AMI"
git push

# 3. ArgoCD syncs the change to the cluster

# 4. Karpenter automatically detects drift and begins replacing nodes
# Monitor progress:
kubectl get nodes -w
kubectl logs -n karpenter -l app.kubernetes.io/name=karpenter -f
```

### Checking for Drift

You can check if Karpenter has detected drift:

```bash
# Check NodeClaims for drift status
kubectl get nodeclaims -o wide

# Look for drift annotations
kubectl get nodeclaims -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.status.conditions[?(@.type=="Drifted")]}{"\n"}{end}'
```

## Approach 2: Manual Re-roll Script

For situations where you need immediate, forced node updates:

### Usage with Docker

```bash
# Dry run to see what would happen
make dry-run

# Re-roll all Karpenter nodes
make run

# Re-roll nodes from a specific NodePool
make docker-run ARGS='--nodepool my-nodepool'

# Re-roll with custom timing
make docker-run ARGS='--wait-between 60 --drain-timeout 600'

# Re-roll nodes matching custom labels
make docker-run ARGS='--label env=prod'

# Verbose output
make docker-run ARGS='--verbose --dry-run'
```

### Usage with Local Python

```bash
# Dry run to see what would happen
python reroll_nodes.py --dry-run

# Re-roll all Karpenter nodes
python reroll_nodes.py

# Re-roll nodes from a specific NodePool
python reroll_nodes.py --nodepool my-nodepool

# Re-roll with custom timing
python reroll_nodes.py --wait-between 60 --drain-timeout 600

# Re-roll nodes matching custom labels
python reroll_nodes.py --label env=prod
```

### Options

- `--dry-run`: Show what would be done without making changes
- `--max-concurrent N`: Maximum number of nodes to reroll concurrently (default: 1)
- `--drain-timeout N`: Timeout in seconds for draining a node (default: 300)
- `--wait-between N`: Wait time in seconds between node deletions (default: 30)
- `--nodepool NAME`: Only re-roll nodes from this NodePool
- `--label KEY=VALUE`: Additional label selector (can be specified multiple times)
- `--verbose`: Enable verbose logging
- `--skip-ec2-termination`: Skip EC2 instance termination (only delete Kubernetes nodes)

### How the Script Works

For each node:
1. **Cordon** - Mark node as unschedulable
2. **Drain** - Evict all pods (respecting PodDisruptionBudgets)
3. **Delete** - Delete the Kubernetes node
4. **Terminate** - Terminate the EC2 instance (to prevent dangling instances)
5. **Wait** - Wait for Karpenter to provision a replacement
6. **Verify** - Check that new node is ready before proceeding

**Note**: EC2 instance termination is enabled by default. If Karpenter is properly cleaning up instances in your environment, you can disable this with `--skip-ec2-termination`.

### Safety Features

- Checks cluster health before starting (requires at least 2 ready nodes)
- Respects PodDisruptionBudgets during draining
- Waits for replacement nodes before proceeding to next node
- Configurable timeouts and wait periods
- Dry-run mode to preview actions

## Recommended Workflow

Use both approaches together for maximum effectiveness:

### For Regular Updates (Automatic)

1. Enable drift detection in your Karpenter NodePool
2. Update AMI in your config repo
3. Let ArgoCD sync the changes
4. Karpenter automatically replaces nodes over time

### For Immediate Updates (Manual)

When you need to force immediate node replacement:

1. Update AMI in your config repo (if not already done)
2. Sync ArgoCD: `argocd app sync <your-app>`
3. Run the re-roll script:
   ```bash
   # Using Docker (recommended)
   make dry-run  # Preview
   make run      # Execute

   # Or using Python directly
   python reroll_nodes.py --dry-run  # Preview
   python reroll_nodes.py            # Execute
   ```

## Docker Details

### Available Make Commands

The Makefile provides convenient shortcuts for common operations:

```bash
make help          # Show all available commands
make build         # Build Docker image
make dry-run       # Safe preview mode
make run           # Execute re-roll (with confirmation)
make docker-run    # Run with custom ARGS
make shell         # Open bash shell in container
make clean         # Remove Docker image
make check-access  # Verify kubectl access
make show-nodes    # List current Karpenter nodes
make show-version  # Show Karpenter version
```

### Custom Docker Execution

If you need more control, you can use Docker directly:

```bash
# Build the image
docker build -t eks-reroll:latest .

# Run with custom arguments
docker run --rm \
  -v ~/.kube:/root/.kube:ro \
  -v ~/.aws:/root/.aws:ro \
  eks-reroll:latest \
  --nodepool production --verbose

# Interactive shell for debugging
docker run --rm -it \
  -v ~/.kube:/root/.kube:ro \
  --entrypoint /bin/bash \
  eks-reroll:latest
```

### Environment Variables

The Docker setup supports these environment variables:

- `KUBECONFIG`: Path to kubeconfig file (default: /root/.kube/config)
- `AWS_REGION`: AWS region (default: us-east-1)
- `AWS_PROFILE`: AWS profile to use (default: default)

Example with custom settings:

```bash
# Using custom AWS profile
AWS_PROFILE=production docker-compose run --rm reroll --dry-run

# Using custom kubeconfig
KUBECONFIG=/path/to/kubeconfig docker-compose run --rm reroll --dry-run
```

### CI/CD Integration with Docker

The containerized solution is perfect for CI/CD pipelines:

```yaml
# Example GitLab CI
deploy-ami-update:
  stage: deploy
  image: docker:latest
  services:
    - docker:dind
  script:
    - docker build -t eks-reroll:latest .
    - docker run --rm
        -v $KUBECONFIG:/root/.kube/config:ro
        eks-reroll:latest
        --nodepool production
        --verbose
  only:
    - main
```

```yaml
# Example GitHub Actions
- name: Re-roll EKS nodes
  run: |
    docker build -t eks-reroll:latest .
    docker run --rm \
      -v $HOME/.kube/config:/root/.kube/config:ro \
      eks-reroll:latest \
      --nodepool production
```

## Monitoring

### Check Karpenter Logs

```bash
kubectl logs -n karpenter -l app.kubernetes.io/name=karpenter -f
```

### Watch Node Status

```bash
kubectl get nodes -w
```

### Check NodeClaim Status

```bash
kubectl get nodeclaims -o wide
```

### Check Pod Disruption

```bash
kubectl get events --field-selector reason=Evicted -w
```

## Troubleshooting

### Drift Not Detected

If Karpenter isn't detecting AMI changes:

1. Verify you're running Karpenter v0.32+:
   ```bash
   kubectl get deployment -n karpenter karpenter -o jsonpath='{.spec.template.spec.containers[0].image}'
   ```

2. Check if disruption budgets are configured:
   ```bash
   kubectl get nodepool -o yaml | grep -A 5 disruption
   ```

3. Check Karpenter controller logs for errors:
   ```bash
   kubectl logs -n karpenter -l app.kubernetes.io/name=karpenter | grep -i drift
   ```

### Nodes Not Replacing

If nodes aren't being replaced:

1. Check PodDisruptionBudgets:
   ```bash
   kubectl get pdb --all-namespaces
   ```

2. Verify nodes can be drained:
   ```bash
   kubectl drain <node-name> --dry-run=client --ignore-daemonsets
   ```

3. Check for pods that can't be evicted:
   ```bash
   kubectl get pods --all-namespaces --field-selector spec.nodeName=<node-name>
   ```

### Script Failures

If the re-roll script fails:

1. Run with `--verbose` for detailed logs
2. Check kubectl access: `kubectl get nodes`
3. Verify Python version: `python --version` (requires 3.8+)
4. Check for sufficient cluster capacity

## Configuration Examples

### Aggressive Drift Replacement

For faster node updates (use with caution):

```yaml
spec:
  disruption:
    consolidationPolicy: WhenUnderutilized
    budgets:
      - nodes: "25%"  # Allow up to 25% disruption
```

### Conservative Drift Replacement

For production stability:

```yaml
spec:
  disruption:
    consolidationPolicy: WhenUnderutilized
    budgets:
      - nodes: "1"  # Replace only 1 node at a time
      schedule: "0 2 * * *"  # Only during maintenance window
```

### Node Expiry

Automatically expire nodes after 30 days:

```yaml
spec:
  disruption:
    expireAfter: 720h  # 30 days
    budgets:
      - nodes: "10%"
```

## Best Practices

1. **Always test in non-production first** - Validate the process in a dev/staging environment
2. **Use dry-run mode** - Always preview with `--dry-run` before executing
3. **Monitor during re-rolls** - Watch logs and metrics during the process
4. **Adjust disruption budgets** - Balance speed vs. stability based on your needs
5. **Set appropriate timeouts** - Increase drain timeout for workloads with long graceful shutdown
6. **Use node expiry** - Consider setting `expireAfter` to ensure regular node refresh
7. **Tag your AMIs** - Use consistent tagging for easier automation with amiSelectorTerms

## CI/CD Integration

### GitOps with ArgoCD

```yaml
# Example ArgoCD Application
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: karpenter-config
spec:
  source:
    repoURL: https://github.com/your-org/your-repo
    path: karpenter
    targetRevision: main
  destination:
    server: https://kubernetes.default.svc
    namespace: karpenter
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
```

### Automated Re-roll Pipeline

```yaml
# Example GitHub Actions workflow
name: Update EKS AMI
on:
  schedule:
    - cron: '0 0 * * 0'  # Weekly on Sunday
  workflow_dispatch:

jobs:
  update-ami:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3

      - name: Update AMI in config
        run: |
          # Script to fetch latest AMI and update config
          LATEST_AMI=$(aws ec2 describe-images ...)
          # Update EC2NodeClass with new AMI

      - name: Commit and push
        run: |
          git config user.name "GitHub Actions"
          git config user.email "actions@github.com"
          git add .
          git commit -m "Update to AMI: $LATEST_AMI"
          git push

      - name: Wait for ArgoCD sync
        run: |
          argocd app sync karpenter-config
          argocd app wait karpenter-config

      - name: Monitor drift replacement
        run: |
          # Monitor Karpenter logs for drift detection
          # Alert if issues occur
```

## Contributing

Contributions welcome! Please ensure:
- Code follows PEP 8 style guidelines
- All safety checks remain in place
- Changes are tested in a non-production environment

## License

MIT License - See LICENSE file for details
