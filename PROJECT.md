# EKS Node Re-roll Project

## Project Structure

```
eks-reroll/
├── README.md                           # Main documentation
├── EXAMPLES.md                         # Real-world examples and use cases
├── PROJECT.md                          # This file - project overview
│
├── reroll_nodes.py                     # Main Python script
├── requirements.txt                    # Python dependencies
│
├── Dockerfile                          # Docker image definition
├── docker-compose.yml                  # Docker Compose configuration
├── .dockerignore                       # Docker build exclusions
│
├── Makefile                            # Convenient make targets
├── run.sh                              # Shell script wrapper
│
├── karpenter-nodepool-example.yaml     # Example Karpenter configuration
└── .gitignore                          # Git exclusions
```

## What This Project Does

This project solves a common problem with Karpenter-managed EKS clusters: when you update the AMI in your configuration, Karpenter doesn't automatically replace existing healthy nodes. This tool provides two complementary solutions:

1. **Automatic drift detection** - Configuration for Karpenter to automatically detect and replace nodes when the AMI changes
2. **Manual re-roll tool** - A Python script packaged in Docker to force immediate node updates when needed

## Key Features

### Safety First
- Cluster health checks before starting
- Respects PodDisruptionBudgets during draining
- Waits for replacement nodes before proceeding
- Dry-run mode for safe previewing
- Configurable timeouts and wait periods

### Flexibility
- Filter by NodePool name
- Filter by custom labels
- Adjustable concurrency
- Customizable drain timeouts
- Verbose logging option

### Easy to Use
- Docker containerization (no Python setup needed)
- Simple `make` commands
- Convenience shell script wrapper
- Works with any kubeconfig

### Production Ready
- Comprehensive error handling
- Detailed logging
- CI/CD integration examples
- Real-world usage examples

## Files Overview

### Core Functionality

**reroll_nodes.py**
- Main Python script (490 lines)
- Handles cordon, drain, delete, and replacement verification
- Full error handling and logging
- Respects PodDisruptionBudgets
- Supports label-based filtering

**requirements.txt**
- Single dependency: kubernetes>=28.1.0

### Docker Implementation

**Dockerfile**
- Based on python:3.11-slim
- Includes kubectl installation
- Minimal image size
- Ready to run in any environment

**docker-compose.yml**
- Simple configuration for local use
- Mounts kubeconfig and AWS credentials
- Environment variable support
- Usage examples in comments

**.dockerignore**
- Optimizes Docker build
- Excludes unnecessary files

### Convenience Tools

**Makefile**
- 15+ useful targets
- Common operations simplified
- Docker build and run automation
- Cluster access verification
- Help documentation

**run.sh**
- Bash wrapper for Docker commands
- Colored output for better UX
- Prerequisite checking
- Confirmation prompts for safety
- Environment variable support

### Documentation

**README.md**
- Comprehensive main documentation
- Installation instructions (Docker and Python)
- Usage examples
- Troubleshooting guide
- Best practices
- CI/CD integration examples

**EXAMPLES.md**
- 12 detailed real-world scenarios
- Production use cases
- Troubleshooting scenarios
- Advanced filtering examples
- Complete CI/CD pipeline examples
- Tips and best practices

**PROJECT.md** (this file)
- Project structure overview
- Quick reference
- Design decisions

### Configuration Examples

**karpenter-nodepool-example.yaml**
- Complete NodePool configuration
- Drift detection enabled
- EC2NodeClass with AMI selection
- Best practice settings
- Inline documentation

**.gitignore**
- Python artifacts
- Virtual environments
- IDE files
- Kubernetes configs
- Sensitive files

## Quick Start

### For First-Time Users

1. Build the Docker image:
   ```bash
   make build
   ```

2. Preview what would happen:
   ```bash
   make dry-run
   ```

3. If everything looks good, execute:
   ```bash
   make run
   ```

### For Shell Script Fans

```bash
./run.sh dry-run    # Preview
./run.sh run        # Execute
```

### For Python Developers

```bash
pip install -r requirements.txt
python reroll_nodes.py --dry-run
python reroll_nodes.py
```

## Design Decisions

### Why Docker?

- **Portability**: Works anywhere Docker runs
- **No dependencies**: No need to install Python or kubectl locally
- **CI/CD friendly**: Easy to integrate into pipelines
- **Consistency**: Same environment everywhere
- **Isolation**: Doesn't affect local Python environment

### Why Python?

- **Kubernetes client**: Excellent official Kubernetes client library
- **Error handling**: Better than shell scripts for complex logic
- **Maintainability**: Easier to read and modify than bash
- **Testing**: Can be easily unit tested (future enhancement)

### Why Both Automatic and Manual?

- **Automatic (drift detection)**: Best for routine maintenance
  - Gradual rollout
  - Respects disruption budgets
  - Minimal operator intervention
  - Ideal for scheduled updates

- **Manual (script)**: Best for urgent situations
  - Immediate updates
  - Full control over timing
  - Override automatic scheduling
  - Emergency security patches

### Safety Features

1. **Cluster health check**: Won't proceed with fewer than 2 ready nodes
2. **PodDisruptionBudget respect**: Uses eviction API, not force deletion
3. **Replacement verification**: Waits for new nodes before proceeding
4. **Dry-run mode**: Always available to preview actions
5. **Configurable wait times**: Tune for your cluster's needs

## Common Workflows

### Weekly Maintenance

```bash
# Sunday night, update dev environment
make dry-run
make run

# Verify everything works
# Monday, update staging
make dry-run
make run

# Tuesday during maintenance window, update production
make docker-run ARGS='--wait-between 120 --drain-timeout 600'
```

### Emergency Security Patch

```bash
# Update AMI in config repo
git add . && git commit -m "Emergency AMI update" && git push

# Sync ArgoCD
argocd app sync karpenter-config

# Force immediate re-roll with safety
make docker-run ARGS='--verbose --drain-timeout 600'
```

### Targeted Update

```bash
# Update only production compute nodes
make docker-run ARGS='--nodepool compute --label env=prod --dry-run'
make docker-run ARGS='--nodepool compute --label env=prod'
```

## Integration Points

### ArgoCD
- Manages Karpenter configuration
- Syncs AMI updates to cluster
- Can be automated via CLI

### Karpenter
- Provisions replacement nodes automatically
- Respects NodePool disruption budgets
- Drift detection for automatic updates (v0.32+)

### CI/CD Pipelines
- GitLab CI examples in EXAMPLES.md
- GitHub Actions examples in EXAMPLES.md
- Can run as Kubernetes CronJob

### Monitoring
- Integrates with Prometheus/Grafana
- Alert examples in EXAMPLES.md
- Logs to stdout for easy capture

## Future Enhancements

Potential improvements (not implemented):

- Unit tests for Python code
- Integration tests with kind/minikube
- Prometheus metrics export
- Slack/webhook notifications
- Web UI for management
- Multi-cluster support in single run
- Rollback capability
- Pre/post-hook scripts

## Support and Troubleshooting

See [README.md](README.md) for:
- Detailed troubleshooting guide
- Common issues and solutions
- How to check Karpenter logs
- Verifying drift detection

See [EXAMPLES.md](EXAMPLES.md) for:
- Real-world troubleshooting scenarios
- Step-by-step solutions
- Production use cases

## Version Information

- Python: 3.8+ (uses Python 3.11 in Docker)
- Kubernetes Client: 28.1.0+
- Karpenter: v0.32+ recommended (for drift detection)
- Docker: Any recent version
- kubectl: Installed in Docker image

## License

MIT License - Feel free to use and modify for your needs.

## Contributing

This is a tool for your internal use. Customize as needed:

- Modify wait times in Makefile
- Add custom labels for filtering
- Adjust safety thresholds
- Add notification hooks
- Integrate with your monitoring

The code is well-commented and structured for easy modification.

## Credits

Built with:
- Python Kubernetes Client
- Docker
- kubectl
- Karpenter (by AWS)
- ArgoCD

## Getting Help

1. Check [README.md](README.md) for documentation
2. Check [EXAMPLES.md](EXAMPLES.md) for use cases
3. Run with `--verbose` flag for detailed logs
4. Check Karpenter logs: `kubectl logs -n karpenter -l app.kubernetes.io/name=karpenter -f`
5. Verify cluster state: `kubectl get nodes`, `kubectl get nodeclaims`

## Summary

This project provides a complete solution for keeping your EKS nodes up-to-date:

✅ Automatic drift detection for hands-off updates
✅ Manual script for urgent updates
✅ Docker containerization for easy deployment
✅ Comprehensive safety features
✅ Production-ready with real-world examples
✅ CI/CD integration ready
✅ Well documented and easy to use

Start with `make dry-run` and you'll be safely updating nodes in minutes!
