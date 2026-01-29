#!/usr/bin/env python3
"""
EKS Node Re-roll Script for Karpenter-managed clusters.

This script safely drains and deletes Karpenter-managed nodes to force
them to be recreated with the latest AMI and configuration.
"""

import argparse
import logging
import sys
import time
from typing import List, Optional, Set

from kubernetes import client, config
from kubernetes.client.rest import ApiException

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class NodeReroller:
    """Handles the safe re-rolling of Karpenter-managed nodes."""

    def __init__(
        self,
        max_concurrent: int = 1,
        drain_timeout: int = 300,
        wait_between_nodes: int = 30,
        dry_run: bool = False,
        selector: Optional[dict] = None
    ):
        """
        Initialize the NodeReroller.

        Args:
            max_concurrent: Maximum number of nodes to reroll concurrently
            drain_timeout: Timeout in seconds for draining a node
            wait_between_nodes: Wait time in seconds between node deletions
            dry_run: If True, only show what would be done
            selector: Label selector for filtering nodes
        """
        self.max_concurrent = max_concurrent
        self.drain_timeout = drain_timeout
        self.wait_between_nodes = wait_between_nodes
        self.dry_run = dry_run
        self.selector = selector or {}

        # Initialize Kubernetes clients
        try:
            config.load_kube_config()
        except Exception:
            config.load_incluster_config()

        self.core_v1 = client.CoreV1Api()
        self.apps_v1 = client.AppsV1Api()

    def get_karpenter_nodes(self) -> List[client.V1Node]:
        """Get all Karpenter-managed nodes matching the selector."""
        try:
            all_nodes = self.core_v1.list_node().items

            # Filter for Karpenter nodes
            karpenter_nodes = []
            for node in all_nodes:
                labels = node.metadata.labels or {}

                # Check if node is managed by Karpenter
                # Karpenter v1beta1 uses karpenter.sh/nodepool
                # Karpenter v1alpha5 uses karpenter.sh/provisioner-name
                if not (labels.get('karpenter.sh/nodepool') or
                       labels.get('karpenter.sh/provisioner-name')):
                    continue

                # Apply additional label selectors if provided
                if self.selector:
                    if all(labels.get(k) == v for k, v in self.selector.items()):
                        karpenter_nodes.append(node)
                else:
                    karpenter_nodes.append(node)

            return karpenter_nodes
        except ApiException as e:
            logger.error(f"Failed to list nodes: {e}")
            sys.exit(1)

    def check_cluster_health(self) -> bool:
        """Check if the cluster has enough capacity before proceeding."""
        try:
            nodes = self.core_v1.list_node().items
            ready_nodes = sum(
                1 for node in nodes
                if any(
                    condition.type == "Ready" and condition.status == "True"
                    for condition in node.status.conditions
                )
            )

            # Require at least 2 ready nodes before proceeding
            if ready_nodes < 2:
                logger.error(f"Only {ready_nodes} ready node(s). Refusing to proceed.")
                return False

            return True
        except ApiException as e:
            logger.error(f"Failed to check cluster health: {e}")
            return False

    def cordon_node(self, node_name: str) -> bool:
        """Cordon a node to prevent new pods from being scheduled."""
        if self.dry_run:
            logger.info(f"[DRY RUN] Would cordon node: {node_name}")
            return True

        try:
            body = {"spec": {"unschedulable": True}}
            self.core_v1.patch_node(node_name, body)
            logger.info(f"Cordoned node: {node_name}")
            return True
        except ApiException as e:
            logger.error(f"Failed to cordon node {node_name}: {e}")
            return False

    def drain_node(self, node_name: str) -> bool:
        """
        Drain a node by evicting all pods.

        This uses the eviction API which respects PodDisruptionBudgets.
        """
        if self.dry_run:
            logger.info(f"[DRY RUN] Would drain node: {node_name}")
            return True

        try:
            # Get all pods on the node
            field_selector = f"spec.nodeName={node_name}"
            pods = self.core_v1.list_pod_for_all_namespaces(
                field_selector=field_selector
            ).items

            # Filter out DaemonSet pods and already terminating pods
            pods_to_evict = []
            for pod in pods:
                # Skip if pod is already terminating
                if pod.metadata.deletion_timestamp:
                    continue

                # Skip DaemonSet pods
                owner_refs = pod.metadata.owner_references or []
                is_daemonset = any(
                    ref.kind == "DaemonSet" for ref in owner_refs
                )
                if is_daemonset:
                    continue

                # Skip mirror pods (static pods)
                if pod.metadata.annotations and \
                   'kubernetes.io/config.mirror' in pod.metadata.annotations:
                    continue

                pods_to_evict.append(pod)

            if not pods_to_evict:
                logger.info(f"No pods to evict on node: {node_name}")
                return True

            logger.info(f"Evicting {len(pods_to_evict)} pod(s) from node: {node_name}")

            # Evict pods
            eviction_body = client.V1Eviction(
                metadata=client.V1ObjectMeta(),
                delete_options=client.V1DeleteOptions()
            )

            for pod in pods_to_evict:
                try:
                    eviction_body.metadata.name = pod.metadata.name
                    eviction_body.metadata.namespace = pod.metadata.namespace

                    self.core_v1.create_namespaced_pod_eviction(
                        name=pod.metadata.name,
                        namespace=pod.metadata.namespace,
                        body=eviction_body
                    )
                    logger.debug(f"Evicted pod: {pod.metadata.namespace}/{pod.metadata.name}")
                except ApiException as e:
                    if e.status == 429:  # Too Many Requests (PDB)
                        logger.warning(
                            f"PodDisruptionBudget prevented eviction of "
                            f"{pod.metadata.namespace}/{pod.metadata.name}"
                        )
                    else:
                        logger.warning(
                            f"Failed to evict pod {pod.metadata.namespace}/{pod.metadata.name}: {e}"
                        )

            # Wait for pods to terminate
            start_time = time.time()
            while time.time() - start_time < self.drain_timeout:
                remaining_pods = self.core_v1.list_pod_for_all_namespaces(
                    field_selector=field_selector
                ).items

                # Filter out DaemonSet and static pods
                remaining_pods = [
                    p for p in remaining_pods
                    if not any(
                        ref.kind == "DaemonSet"
                        for ref in (p.metadata.owner_references or [])
                    ) and not (
                        p.metadata.annotations and
                        'kubernetes.io/config.mirror' in p.metadata.annotations
                    )
                ]

                if not remaining_pods:
                    logger.info(f"Successfully drained node: {node_name}")
                    return True

                logger.debug(
                    f"Waiting for {len(remaining_pods)} pod(s) to terminate on {node_name}"
                )
                time.sleep(5)

            logger.warning(f"Drain timeout reached for node: {node_name}")
            return False

        except ApiException as e:
            logger.error(f"Failed to drain node {node_name}: {e}")
            return False

    def delete_node(self, node_name: str) -> bool:
        """Delete a node, triggering Karpenter to create a replacement."""
        if self.dry_run:
            logger.info(f"[DRY RUN] Would delete node: {node_name}")
            return True

        try:
            self.core_v1.delete_node(node_name)
            logger.info(f"Deleted node: {node_name}")
            return True
        except ApiException as e:
            logger.error(f"Failed to delete node {node_name}: {e}")
            return False

    def wait_for_replacement(self, original_count: int) -> bool:
        """Wait for Karpenter to provision replacement nodes."""
        if self.dry_run:
            logger.info("[DRY RUN] Would wait for replacement node")
            return True

        logger.info("Waiting for replacement node(s) to become ready...")

        max_wait = 300  # 5 minutes
        start_time = time.time()

        while time.time() - start_time < max_wait:
            nodes = self.get_karpenter_nodes()
            ready_nodes = sum(
                1 for node in nodes
                if any(
                    condition.type == "Ready" and condition.status == "True"
                    for condition in node.status.conditions
                )
            )

            if ready_nodes >= original_count:
                logger.info(f"Replacement node(s) ready ({ready_nodes} total)")
                return True

            logger.debug(f"Waiting for replacement nodes ({ready_nodes}/{original_count} ready)")
            time.sleep(10)

        logger.warning("Timeout waiting for replacement nodes")
        return False

    def reroll_node(self, node: client.V1Node, original_count: int) -> bool:
        """
        Reroll a single node: cordon, drain, delete, and wait for replacement.

        Args:
            node: The node to reroll
            original_count: Original number of ready nodes

        Returns:
            True if successful, False otherwise
        """
        node_name = node.metadata.name
        logger.info(f"Starting re-roll of node: {node_name}")

        # Step 1: Cordon
        if not self.cordon_node(node_name):
            return False

        # Step 2: Drain
        if not self.drain_node(node_name):
            logger.error(f"Failed to drain node: {node_name}")
            return False

        # Step 3: Delete
        if not self.delete_node(node_name):
            return False

        # Step 4: Wait for replacement
        if not self.wait_for_replacement(original_count):
            logger.warning("Proceeding despite replacement timeout")

        # Wait between nodes
        if self.wait_between_nodes > 0:
            logger.info(f"Waiting {self.wait_between_nodes}s before next node...")
            time.sleep(self.wait_between_nodes)

        return True

    def run(self) -> int:
        """
        Execute the node re-roll process.

        Returns:
            Exit code (0 for success, 1 for failure)
        """
        logger.info("Starting EKS node re-roll process")

        if self.dry_run:
            logger.info("DRY RUN MODE - No changes will be made")

        # Check cluster health
        if not self.check_cluster_health():
            return 1

        # Get Karpenter nodes
        nodes = self.get_karpenter_nodes()

        if not nodes:
            logger.warning("No Karpenter-managed nodes found matching criteria")
            return 0

        logger.info(f"Found {len(nodes)} Karpenter-managed node(s) to re-roll")

        # Show nodes
        for node in nodes:
            labels = node.metadata.labels or {}
            nodepool = labels.get('karpenter.sh/nodepool') or \
                      labels.get('karpenter.sh/provisioner-name', 'unknown')
            instance_type = labels.get('node.kubernetes.io/instance-type', 'unknown')
            logger.info(f"  - {node.metadata.name} (nodepool={nodepool}, type={instance_type})")

        if self.dry_run:
            logger.info("[DRY RUN] Would re-roll the above nodes")
            return 0

        # Track original count for replacement verification
        original_count = len([
            n for n in nodes
            if any(
                c.type == "Ready" and c.status == "True"
                for c in n.status.conditions
            )
        ])

        # Re-roll nodes
        failed_nodes = []
        for i, node in enumerate(nodes, 1):
            logger.info(f"Processing node {i}/{len(nodes)}")

            if not self.reroll_node(node, original_count - (i - 1)):
                failed_nodes.append(node.metadata.name)
                logger.error(f"Failed to re-roll node: {node.metadata.name}")

                # Ask whether to continue
                if i < len(nodes):
                    logger.warning("Stopping due to failure")
                    break

        # Summary
        logger.info("=" * 60)
        logger.info("Re-roll process completed")
        logger.info(f"  Total nodes: {len(nodes)}")
        logger.info(f"  Successful: {len(nodes) - len(failed_nodes)}")
        logger.info(f"  Failed: {len(failed_nodes)}")

        if failed_nodes:
            logger.error(f"Failed nodes: {', '.join(failed_nodes)}")
            return 1

        logger.info("All nodes re-rolled successfully")
        return 0


def parse_args():
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description='Re-roll Karpenter-managed EKS nodes to update AMI',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Dry run to see what would happen
  python reroll_nodes.py --dry-run

  # Re-roll all Karpenter nodes one at a time
  python reroll_nodes.py

  # Re-roll nodes from a specific NodePool
  python reroll_nodes.py --nodepool my-nodepool

  # Re-roll with custom timing
  python reroll_nodes.py --wait-between 60 --drain-timeout 600

  # Re-roll nodes matching custom labels
  python reroll_nodes.py --label env=prod --label team=platform
        """
    )

    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Show what would be done without making changes'
    )

    parser.add_argument(
        '--max-concurrent',
        type=int,
        default=1,
        help='Maximum number of nodes to reroll concurrently (default: 1)'
    )

    parser.add_argument(
        '--drain-timeout',
        type=int,
        default=300,
        help='Timeout in seconds for draining a node (default: 300)'
    )

    parser.add_argument(
        '--wait-between',
        type=int,
        default=30,
        help='Wait time in seconds between node deletions (default: 30)'
    )

    parser.add_argument(
        '--nodepool',
        type=str,
        help='Only re-roll nodes from this NodePool'
    )

    parser.add_argument(
        '--label',
        action='append',
        help='Additional label selector (can be specified multiple times, format: key=value)'
    )

    parser.add_argument(
        '--verbose',
        action='store_true',
        help='Enable verbose logging'
    )

    return parser.parse_args()


def main():
    """Main entry point."""
    args = parse_args()

    # Set log level
    if args.verbose:
        logger.setLevel(logging.DEBUG)

    # Build label selector
    selector = {}
    if args.nodepool:
        # Try both v1beta1 and v1alpha5 label formats
        selector['karpenter.sh/nodepool'] = args.nodepool

    if args.label:
        for label in args.label:
            if '=' not in label:
                logger.error(f"Invalid label format: {label} (expected key=value)")
                sys.exit(1)
            key, value = label.split('=', 1)
            selector[key] = value

    # Create and run reroller
    reroller = NodeReroller(
        max_concurrent=args.max_concurrent,
        drain_timeout=args.drain_timeout,
        wait_between_nodes=args.wait_between,
        dry_run=args.dry_run,
        selector=selector if selector else None
    )

    sys.exit(reroller.run())


if __name__ == '__main__':
    main()
