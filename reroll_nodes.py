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
import boto3
from botocore.exceptions import ClientError, NoCredentialsError
import urllib3

# Disable SSL warnings
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

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
        selector: Optional[dict] = None,
        skip_ec2_termination: bool = False,
        verbose: bool = False
    ):
        """
        Initialize the NodeReroller.

        Args:
            max_concurrent: Maximum number of nodes to reroll concurrently
            drain_timeout: Timeout in seconds for draining a node
            wait_between_nodes: Wait time in seconds between node deletions
            dry_run: If True, only show what would be done
            selector: Label selector for filtering nodes
            skip_ec2_termination: If True, skip EC2 instance termination
            verbose: If True, enable verbose logging
        """
        self.max_concurrent = max_concurrent
        self.drain_timeout = drain_timeout
        self.wait_between_nodes = wait_between_nodes
        self.dry_run = dry_run
        self.selector = selector or {}
        self.skip_ec2_termination = skip_ec2_termination
        self.verbose = verbose

        # Initialize Kubernetes clients
        try:
            config.load_kube_config()
        except Exception:
            config.load_incluster_config()

        # Disable SSL verification for Kubernetes client
        k8s_config = client.Configuration.get_default_copy()
        k8s_config.verify_ssl = False
        client.Configuration.set_default(k8s_config)

        self.core_v1 = client.CoreV1Api()
        self.apps_v1 = client.AppsV1Api()

        # Initialize EC2 client (only if not skipping termination)
        self.ec2_client = None
        if not skip_ec2_termination:
            try:
                self.ec2_client = boto3.client('ec2', verify=False)  # Auto-detects region
                logger.info(f"Initialized EC2 client for region: {self.ec2_client.meta.region_name}")
            except NoCredentialsError:
                logger.warning("AWS credentials not found. EC2 termination will be skipped.")
            except Exception as e:
                logger.warning(f"Failed to initialize EC2 client: {e}")

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
                nodepool_v1beta1 = labels.get('karpenter.sh/nodepool', '').strip()
                nodepool_v1alpha5 = labels.get('karpenter.sh/provisioner-name', '').strip()

                if not (nodepool_v1beta1 or nodepool_v1alpha5):
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

    def _get_nodepool_name(self, node: client.V1Node) -> str:
        """
        Extract nodepool name from node labels with proper fallback logic.

        Args:
            node: Kubernetes node object

        Returns:
            Nodepool name or 'unknown' if not found
        """
        labels = node.metadata.labels or {}

        # Try v1beta1 label first
        nodepool = labels.get('karpenter.sh/nodepool', '').strip()
        if nodepool:
            return nodepool

        # Try v1alpha5 label
        nodepool = labels.get('karpenter.sh/provisioner-name', '').strip()
        if nodepool:
            return nodepool

        # Log warning if we couldn't find nodepool
        node_name = node.metadata.name
        logger.warning(
            f"Node {node_name} has no valid nodepool label. "
            f"Labels: karpenter.sh/nodepool={labels.get('karpenter.sh/nodepool')}, "
            f"karpenter.sh/provisioner-name={labels.get('karpenter.sh/provisioner-name')}"
        )

        return 'unknown'

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

    def get_instance_id_from_node(self, node: client.V1Node) -> Optional[str]:
        """
        Extract EC2 instance ID from Kubernetes node.

        Args:
            node: Kubernetes node object

        Returns:
            EC2 instance ID or None if not found
        """
        node_name = node.metadata.name

        # 1. Try spec.providerID
        if node.spec.provider_id and node.spec.provider_id.startswith('aws://'):
            parts = node.spec.provider_id.split('/')
            if len(parts) >= 2 and parts[-1].startswith('i-'):
                logger.debug(f"Found instance ID from providerID: {parts[-1]}")
                return parts[-1]

        # 2. Try annotations
        if node.metadata.annotations:
            for key in ['karpenter.sh/instance-id', 'node.kubernetes.io/instance-id']:
                if key in node.metadata.annotations:
                    value = node.metadata.annotations[key]
                    if value.startswith('i-'):
                        logger.debug(f"Found instance ID from annotation {key}: {value}")
                        return value

        # 3. Lookup by private IP
        if self.ec2_client and node.status.addresses:
            for address in node.status.addresses:
                if address.type == "InternalIP":
                    try:
                        logger.debug(f"Looking up EC2 instance by IP: {address.address}")
                        response = self.ec2_client.describe_instances(
                            Filters=[
                                {'Name': 'private-ip-address', 'Values': [address.address]},
                                {'Name': 'instance-state-name', 'Values': ['running', 'pending']}
                            ]
                        )
                        for reservation in response.get('Reservations', []):
                            for instance in reservation.get('Instances', []):
                                instance_id = instance['InstanceId']
                                logger.debug(f"Found instance ID from IP lookup: {instance_id}")
                                return instance_id
                    except Exception as e:
                        logger.warning(f"EC2 lookup by IP failed: {e}")
                    break

        logger.warning(f"Could not determine EC2 instance ID for node: {node_name}")
        return None

    def terminate_ec2_instance(self, instance_id: str, node_name: str) -> bool:
        """
        Terminate EC2 instance via boto3.

        Args:
            instance_id: EC2 instance ID
            node_name: Kubernetes node name (for logging)

        Returns:
            True if successful, False otherwise
        """
        if not self.ec2_client:
            return False

        if self.dry_run:
            logger.info(f"[DRY RUN] Would terminate EC2 instance: {instance_id} (node: {node_name})")
            return True

        try:
            response = self.ec2_client.terminate_instances(InstanceIds=[instance_id])

            if response['TerminatingInstances']:
                terminating = response['TerminatingInstances'][0]
                prev_state = terminating['PreviousState']['Name']
                curr_state = terminating['CurrentState']['Name']
                logger.info(f"Terminated EC2 instance: {instance_id} (node: {node_name}) [{prev_state} → {curr_state}]")
                return True
            return False

        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            if error_code == 'InvalidInstanceID.NotFound':
                logger.warning(f"EC2 instance {instance_id} not found (may already be terminated)")
            elif error_code == 'UnauthorizedOperation':
                logger.error(f"Not authorized to terminate instance {instance_id}. Check IAM permissions.")
            else:
                logger.error(f"Failed to terminate instance {instance_id}: {e}")
            return False
        except Exception as e:
            logger.error(f"Unexpected error terminating instance {instance_id}: {e}")
            return False

    def delete_node(self, node: client.V1Node) -> bool:
        """Delete a node and its EC2 instance, triggering Karpenter to create a replacement."""
        node_name = node.metadata.name

        if self.dry_run:
            instance_id = self.get_instance_id_from_node(node) if not self.skip_ec2_termination else None
            if instance_id:
                logger.info(f"[DRY RUN] Would delete node: {node_name} (EC2 instance: {instance_id})")
            else:
                logger.info(f"[DRY RUN] Would delete node: {node_name}")
            return True

        # Delete K8s node first
        try:
            self.core_v1.delete_node(node_name)
            logger.info(f"Deleted node: {node_name}")
        except ApiException as e:
            logger.error(f"Failed to delete node {node_name}: {e}")
            return False

        # Terminate EC2 instance
        if not self.skip_ec2_termination and self.ec2_client:
            instance_id = self.get_instance_id_from_node(node)
            if instance_id:
                self.terminate_ec2_instance(instance_id, node_name)
            else:
                logger.warning(f"EC2 instance ID not found for {node_name}, skipping EC2 termination")

        return True

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

    def reroll_node(self, node: client.V1Node, original_count: int, skip_wait: bool = False) -> bool:
        """
        Reroll a single node: cordon, drain, delete, and wait for replacement.

        Args:
            node: The node to reroll
            original_count: Original number of ready nodes
            skip_wait: If True, skip the wait between nodes (for retry attempts)

        Returns:
            True if successful, False otherwise
        """
        node_name = node.metadata.name
        logger.info(f"Starting re-roll of node: {node_name}")

        # Step 1: Cordon
        if not self.cordon_node(node_name):
            logger.warning(f"Failed to cordon node: {node_name}, continuing anyway...")

        # Step 2: Drain
        if not self.drain_node(node_name):
            logger.warning(f"Failed to drain node: {node_name}")
            return False

        # Step 3: Delete
        if not self.delete_node(node):
            logger.warning(f"Failed to delete node: {node_name}")
            return False

        # Step 4: Wait for replacement
        if not self.wait_for_replacement(original_count):
            logger.warning("Proceeding despite replacement timeout")

        # Wait between nodes
        if not skip_wait and self.wait_between_nodes > 0:
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
            nodepool = self._get_nodepool_name(node)
            instance_type = labels.get('node.kubernetes.io/instance-type', 'unknown')
            logger.info(f"  - {node.metadata.name} (nodepool={nodepool}, type={instance_type})")

            # Show verbose label details for debugging
            if self.verbose or self.dry_run:
                nodepool_v1beta1 = labels.get('karpenter.sh/nodepool', 'not set')
                nodepool_v1alpha5 = labels.get('karpenter.sh/provisioner-name', 'not set')
                logger.debug(
                    f"    Labels: karpenter.sh/nodepool={nodepool_v1beta1}, "
                    f"karpenter.sh/provisioner-name={nodepool_v1alpha5}"
                )

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

        # Re-roll nodes (first pass)
        failed_nodes = []
        for i, node in enumerate(nodes, 1):
            logger.info(f"Processing node {i}/{len(nodes)}")

            if not self.reroll_node(node, original_count - (i - 1)):
                failed_nodes.append(node)
                logger.warning(f"Failed to re-roll node: {node.metadata.name}, will retry later")

        # Retry failed nodes
        if failed_nodes:
            logger.info("=" * 60)
            logger.info(f"Retrying {len(failed_nodes)} failed node(s)...")
            retry_failed = []

            for i, node in enumerate(failed_nodes, 1):
                logger.info(f"Retry attempt {i}/{len(failed_nodes)} for node: {node.metadata.name}")

                if not self.reroll_node(node, original_count, skip_wait=(i == len(failed_nodes))):
                    retry_failed.append(node.metadata.name)
                    logger.error(f"Retry failed for node: {node.metadata.name}")

            failed_nodes = retry_failed

        # Summary
        logger.info("=" * 60)
        logger.info("Re-roll process completed")
        logger.info(f"  Total nodes: {len(nodes)}")
        logger.info(f"  Successful: {len(nodes) - len(failed_nodes)}")
        logger.info(f"  Failed: {len(failed_nodes)}")

        if failed_nodes:
            logger.error(f"Failed nodes after retry: {', '.join(failed_nodes)}")
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

    parser.add_argument(
        '--skip-ec2-termination',
        action='store_true',
        help='Skip EC2 instance termination (only delete Kubernetes nodes)'
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
        selector=selector if selector else None,
        skip_ec2_termination=args.skip_ec2_termination,
        verbose=args.verbose
    )

    sys.exit(reroller.run())


if __name__ == '__main__':
    main()
