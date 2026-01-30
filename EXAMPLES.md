# Examples and Use Cases

This document provides real-world examples and use cases for the EKS node re-roll tool.

## Table of Contents

- [Basic Usage](#basic-usage)
- [Production Scenarios](#production-scenarios)
- [Troubleshooting Scenarios](#troubleshooting-scenarios)
- [Advanced Use Cases](#advanced-use-cases)
- [Integration Examples](#integration-examples)

## Basic Usage

### Example 1: First Time Setup

```bash
# Clone or navigate to the project
cd eks-reroll

# Build the Docker image
make build

# Check cluster access
make check-access

# View current Karpenter nodes
make show-nodes

# Preview what would happen (always do this first!)
make dry-run
```

### Example 2: Simple Re-roll

```bash
# Preview changes (shows EC2 instance IDs that would be terminated)
make dry-run

# If everything looks good, execute
make run

# Monitor progress in another terminal
kubectl get nodes -w
```

### Example 3: Re-roll Without EC2 Termination

If Karpenter is properly cleaning up EC2 instances or you don't have EC2 termination permissions:

```bash
# Preview without EC2 termination
make docker-run ARGS='--skip-ec2-termination --dry-run'

# Execute re-roll (Kubernetes nodes only)
make docker-run ARGS='--skip-ec2-termination --verbose'
```

## Production Scenarios

### Scenario 1: Updating Production Nodes During Maintenance Window

You have a maintenance window on Sunday at 2 AM to update your production nodes with the latest security patches.

```bash
# 1. Update AMI in your config repo (do this beforehand)
# Edit your karpenter-config/ec2nodeclass.yaml
# Update the AMI ID or AMI selector

# 2. At maintenance window time, sync ArgoCD
argocd app sync karpenter-config --prune

# 3. Verify the config was updated
kubectl get ec2nodeclass -o yaml | grep -A 5 amiSelectorTerms

# 4. Preview the re-roll
make dry-run

# 5. Execute the re-roll with extended timeouts for production safety
make docker-run ARGS='--wait-between 120 --drain-timeout 600 --verbose'

# 6. Monitor in another terminal
kubectl logs -n karpenter -l app.kubernetes.io/name=karpenter -f
```

### Scenario 2: Re-rolling Specific NodePools

You have separate NodePools for different workloads and want to update them independently.

```bash
# Re-roll compute-optimized nodes
make docker-run ARGS='--nodepool compute --dry-run'
make docker-run ARGS='--nodepool compute'

# Wait and verify
kubectl get nodes -l karpenter.sh/nodepool=compute

# Then re-roll general-purpose nodes
make docker-run ARGS='--nodepool general --dry-run'
make docker-run ARGS='--nodepool general'
```

### Scenario 3: Gradual Rollout with Custom Timing

For large clusters, you want to be extra cautious and roll out slowly.

```bash
# Very conservative: 1 node at a time, 5 minutes between nodes
make docker-run ARGS='--wait-between 300 --drain-timeout 600 --verbose'

# Or use the shell script with confirmation
./run.sh run --wait-between 300 --drain-timeout 600 --verbose
```

## Troubleshooting Scenarios

### Scenario 4: Nodes Not Updating After AMI Change

Karpenter isn't automatically updating nodes after you changed the AMI.

```bash
# 1. Check if drift is detected
kubectl get nodeclaims -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.status.conditions[?(@.type=="Drifted")]}{"\n"}{end}'

# 2. Check Karpenter version (need v0.32+ for drift)
make show-version

# 3. Check if disruption budgets are configured
kubectl get nodepool -o yaml | grep -A 10 disruption

# 4. If drift isn't working, manually force re-roll
make dry-run
make run
```

### Scenario 5: Stuck Pods Blocking Drain

Some pods have aggressive PodDisruptionBudgets or are stuck in terminating state.

```bash
# 1. Identify problematic pods
kubectl get pods --all-namespaces --field-selector status.phase=Running | grep -v Running

# 2. Check PodDisruptionBudgets
kubectl get pdb --all-namespaces

# 3. Run with extended timeout
make docker-run ARGS='--drain-timeout 900 --verbose'

# 4. If still stuck, investigate specific pods
kubectl describe pod <stuck-pod> -n <namespace>

# 5. May need to temporarily scale down problematic PDBs
kubectl patch pdb <pdb-name> -n <namespace> -p '{"spec":{"minAvailable":0}}'

# 6. Then retry
make run
```

### Scenario 6: Emergency Security Patch

Critical CVE requires immediate node updates across all environments.

```bash
# 1. Update AMI in all environment configs simultaneously
# Use sed or script to update across multiple files

# 2. Force ArgoCD sync
argocd app sync --all

# 3. For each cluster/environment, run aggressive re-roll
# Dev environment (faster, with EC2 termination to ensure cleanup)
make docker-run ARGS='--wait-between 30 --verbose'

# Staging environment (moderate)
make docker-run ARGS='--wait-between 60 --verbose'

# Production (still aggressive but safer)
make docker-run ARGS='--wait-between 90 --drain-timeout 600 --verbose'

# Note: EC2 instances are terminated by default to prevent dangling instances
# Use --skip-ec2-termination if you want to rely on Karpenter's cleanup
```

### Scenario 7: Troubleshooting Dangling EC2 Instances

If you notice EC2 instances remain after node deletion:

```bash
# 1. Check for dangling instances in AWS Console or CLI
aws ec2 describe-instances \
  --filters "Name=tag:karpenter.sh/nodepool,Values=*" \
  --query 'Reservations[*].Instances[*].[InstanceId,State.Name,Tags[?Key==`Name`].Value|[0]]' \
  --output table

# 2. Run re-roll with verbose output to see EC2 termination details
make docker-run ARGS='--verbose --dry-run'

# 3. Execute re-roll (EC2 termination is enabled by default)
make run

# 4. Verify instances are terminating
aws ec2 describe-instances \
  --instance-ids i-xxxxx \
  --query 'Reservations[*].Instances[*].[InstanceId,State.Name]' \
  --output table
```

## Advanced Use Cases

### Scenario 8: Multi-Environment Automation

You manage multiple EKS clusters and want to automate the re-roll process.

```bash
#!/bin/bash
# reroll-all-clusters.sh

CLUSTERS=("dev" "staging" "prod")
WAIT_TIMES=(30 60 120)  # Different wait times per environment

for i in "${!CLUSTERS[@]}"; do
    cluster="${CLUSTERS[$i]}"
    wait_time="${WAIT_TIMES[$i]}"

    echo "Processing cluster: $cluster"

    # Switch context
    kubectl config use-context "$cluster"

    # Update kubeconfig for Docker
    export KUBECONFIG="$HOME/.kube/${cluster}-config"

    # Dry run first
    echo "Dry run for $cluster"
    make dry-run

    # Ask for confirmation
    read -p "Proceed with $cluster? (yes/no): " confirm
    if [[ $confirm == "yes" ]]; then
        make docker-run ARGS="--wait-between $wait_time --verbose"
    fi

    echo "Completed: $cluster"
    echo "---"
done
```

### Scenario 9: Filtering by Multiple Labels

Re-roll only nodes that match specific criteria.

```bash
# Re-roll only production nodes in us-east-1
make docker-run ARGS='--label env=prod --label topology.kubernetes.io/zone=us-east-1a --dry-run'

# Re-roll nodes owned by a specific team
make docker-run ARGS='--label team=platform --label env=prod'

# Re-roll spot instances only
make docker-run ARGS='--label karpenter.sh/capacity-type=spot'
```

### Scenario 10: Testing in Development

Before rolling out to production, test the process in dev.

```bash
# 1. Switch to dev cluster
kubectl config use-context dev-cluster

# 2. Run with verbose logging to understand the process
make docker-run ARGS='--verbose --dry-run'

# 3. Execute on a small subset first
make docker-run ARGS='--label env=dev --label app=test-app --verbose'

# 4. Verify everything worked
kubectl get nodes -l app=test-app
kubectl get pods -A -o wide | grep test-app

# 5. If successful, proceed with full dev cluster
make run
```

## Integration Examples

### Example 11: GitLab CI/CD Pipeline

```yaml
# .gitlab-ci.yml
stages:
  - update-config
  - sync
  - reroll-dev
  - reroll-staging
  - reroll-prod

update-ami-config:
  stage: update-config
  script:
    - |
      # Fetch latest EKS optimized AMI
      LATEST_AMI=$(aws ec2 describe-images \
        --owners amazon \
        --filters "Name=name,Values=amazon-eks-node-1.28-*" \
        --query 'sort_by(Images, &CreationDate)[-1].ImageId' \
        --output text)

      # Update EC2NodeClass configurations
      for env in dev staging prod; do
        sed -i "s/ami-[a-z0-9]*/AMI_ID/g" "environments/$env/ec2nodeclass.yaml"
      done

      # Commit changes
      git add .
      git commit -m "Update to AMI: $LATEST_AMI"
      git push

sync-argocd:
  stage: sync
  script:
    - argocd login $ARGOCD_SERVER --username admin --password $ARGOCD_PASSWORD
    - argocd app sync karpenter-dev
    - argocd app sync karpenter-staging
    - argocd app sync karpenter-prod

reroll-dev:
  stage: reroll-dev
  image: docker:latest
  services:
    - docker:dind
  script:
    - cd tools/eks-reroll
    - docker build -t eks-reroll:latest .
    - |
      docker run --rm \
        -v $DEV_KUBECONFIG:/root/.kube/config:ro \
        eks-reroll:latest \
        --wait-between 30 --verbose
  when: manual

reroll-staging:
  stage: reroll-staging
  image: docker:latest
  services:
    - docker:dind
  script:
    - cd tools/eks-reroll
    - docker build -t eks-reroll:latest .
    - |
      docker run --rm \
        -v $STAGING_KUBECONFIG:/root/.kube/config:ro \
        eks-reroll:latest \
        --wait-between 60 --verbose
  when: manual
  dependencies:
    - reroll-dev

reroll-prod:
  stage: reroll-prod
  image: docker:latest
  services:
    - docker:dind
  script:
    - cd tools/eks-reroll
    - docker build -t eks-reroll:latest .
    - |
      docker run --rm \
        -v $PROD_KUBECONFIG:/root/.kube/config:ro \
        eks-reroll:latest \
        --wait-between 120 --drain-timeout 600 --verbose
  when: manual
  dependencies:
    - reroll-staging
  only:
    - main
```

### Example 12: GitHub Actions Workflow

```yaml
# .github/workflows/update-eks-nodes.yml
name: Update EKS Node AMI

on:
  schedule:
    - cron: '0 2 * * 0'  # Weekly on Sunday at 2 AM
  workflow_dispatch:      # Allow manual trigger
    inputs:
      environment:
        description: 'Environment to update'
        required: true
        type: choice
        options:
          - dev
          - staging
          - prod

jobs:
  update-ami:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3

      - name: Configure AWS credentials
        uses: aws-actions/configure-aws-credentials@v2
        with:
          aws-access-key-id: ${{ secrets.AWS_ACCESS_KEY_ID }}
          aws-secret-access-key: ${{ secrets.AWS_SECRET_ACCESS_KEY }}
          aws-region: us-east-1

      - name: Get latest EKS AMI
        id: get-ami
        run: |
          LATEST_AMI=$(aws ec2 describe-images \
            --owners amazon \
            --filters "Name=name,Values=amazon-eks-node-1.28-*" \
            --query 'sort_by(Images, &CreationDate)[-1].ImageId' \
            --output text)
          echo "ami=$LATEST_AMI" >> $GITHUB_OUTPUT

      - name: Update configuration
        run: |
          sed -i "s/ami-[a-z0-9]*/${{ steps.get-ami.outputs.ami }}/g" \
            karpenter-config/ec2nodeclass.yaml

      - name: Commit and push
        run: |
          git config user.name "GitHub Actions"
          git config user.email "actions@github.com"
          git add .
          git commit -m "Update AMI to ${{ steps.get-ami.outputs.ami }}"
          git push

  sync-argocd:
    needs: update-ami
    runs-on: ubuntu-latest
    steps:
      - name: Sync ArgoCD
        run: |
          argocd app sync karpenter-${{ github.event.inputs.environment || 'dev' }}

  reroll-nodes:
    needs: sync-argocd
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3

      - name: Setup kubectl
        uses: azure/setup-kubectl@v3

      - name: Configure kubeconfig
        run: |
          echo "${{ secrets.KUBECONFIG }}" | base64 -d > $HOME/.kube/config

      - name: Build Docker image
        run: |
          cd tools/eks-reroll
          docker build -t eks-reroll:latest .

      - name: Re-roll nodes (dry-run)
        run: |
          docker run --rm \
            -v $HOME/.kube/config:/root/.kube/config:ro \
            eks-reroll:latest \
            --dry-run --verbose

      - name: Re-roll nodes
        if: github.event.inputs.environment != 'prod' || github.ref == 'refs/heads/main'
        run: |
          docker run --rm \
            -v $HOME/.kube/config:/root/.kube/config:ro \
            eks-reroll:latest \
            --wait-between 90 --verbose
```

### Example 13: Kubernetes CronJob for Automated Maintenance

Deploy the re-roll tool as a Kubernetes CronJob for scheduled maintenance.

```yaml
# cronjob-reroll.yaml
apiVersion: v1
kind: ServiceAccount
metadata:
  name: node-reroller
  namespace: karpenter
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: node-reroller
rules:
  - apiGroups: [""]
    resources: ["nodes"]
    verbs: ["get", "list", "delete", "patch"]
  - apiGroups: [""]
    resources: ["pods"]
    verbs: ["get", "list", "delete"]
  - apiGroups: [""]
    resources: ["pods/eviction"]
    verbs: ["create"]
  - apiGroups: ["apps"]
    resources: ["daemonsets", "statefulsets", "deployments"]
    verbs: ["get", "list"]
  - apiGroups: ["karpenter.sh"]
    resources: ["nodepools", "nodeclaims"]
    verbs: ["get", "list"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: node-reroller
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: node-reroller
subjects:
  - kind: ServiceAccount
    name: node-reroller
    namespace: karpenter
---
apiVersion: batch/v1
kind: CronJob
metadata:
  name: node-reroll
  namespace: karpenter
spec:
  schedule: "0 2 * * 0"  # Weekly on Sunday at 2 AM
  successfulJobsHistoryLimit: 3
  failedJobsHistoryLimit: 3
  jobTemplate:
    spec:
      template:
        spec:
          serviceAccountName: node-reroller
          restartPolicy: OnFailure
          containers:
            - name: reroller
              image: eks-reroll:latest
              args:
                - "--wait-between"
                - "120"
                - "--drain-timeout"
                - "600"
                - "--verbose"
              env:
                - name: KUBECONFIG
                  value: ""  # Use in-cluster config
```

## Tips and Best Practices

### Tip 1: Always Use Dry-Run First

```bash
# ALWAYS do this first
make dry-run

# Review the output carefully
# Then execute
make run
```

### Tip 2: Monitor During Re-roll

Open multiple terminal windows to monitor:

```bash
# Terminal 1: Run the re-roll
make run

# Terminal 2: Watch nodes
kubectl get nodes -w

# Terminal 3: Watch Karpenter logs
kubectl logs -n karpenter -l app.kubernetes.io/name=karpenter -f

# Terminal 4: Watch pod events
kubectl get events -A -w | grep -i evict
```

### Tip 3: Test with Small Subset First

```bash
# Test with a single test app first
make docker-run ARGS='--label app=test-app --verbose'

# Verify everything worked
kubectl get pods -l app=test-app -A

# Then proceed with full re-roll
make run
```

### Tip 4: Document Your Process

Create a runbook specific to your environment:

```bash
# my-runbook.sh
#!/bin/bash

echo "EKS Node Re-roll Runbook for Production"
echo "========================================"
echo ""
echo "1. Update AMI in config repo"
echo "2. Run: make dry-run"
echo "3. Review output"
echo "4. Run: make run"
echo "5. Monitor logs"
echo ""
read -p "Continue? (yes/no): " confirm

if [[ $confirm == "yes" ]]; then
    make dry-run
    read -p "Looks good? Continue with re-roll? (yes/no): " confirm2
    if [[ $confirm2 == "yes" ]]; then
        make run
    fi
fi
```

### Tip 5: Set Up Alerts

Configure alerts for node re-roll operations:

```yaml
# prometheus-alert-rules.yaml
groups:
  - name: node-reroll
    interval: 30s
    rules:
      - alert: NodeRerollInProgress
        expr: kube_node_status_condition{condition="Ready",status="Unknown"} > 0
        for: 5m
        annotations:
          summary: "Node re-roll may be in progress"
          description: "{{ $value }} nodes are in Unknown state"

      - alert: HighPodEvictionRate
        expr: rate(kube_pod_evicted[5m]) > 10
        annotations:
          summary: "High pod eviction rate detected"
          description: "Pods are being evicted at {{ $value }} per second"
```
