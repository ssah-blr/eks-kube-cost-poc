import os
import time
import threading
import requests
from threading import Lock
from concurrent.futures import ThreadPoolExecutor
from kubernetes import client, config
from kubernetes.client.rest import ApiException
from prometheus_client import start_http_server, Gauge, CollectorRegistry

semaphore = threading.Semaphore(3)

# Initialize Prometheus metrics
registry = CollectorRegistry()

# Thread safety lock
metrics_lock = threading.Lock()

POD_USAGE_COST = Gauge(
    "pod_usage_cost",
    "Running cost related to pods",
    [
        "eks_cluster_name",
        "pod_namespace",
        "deployment_name",
        "pod_name",
    ],
    registry=registry,
)

POD_WASTAGE_COST = Gauge(
    "pod_wastage_cost",
    "Wastage cost related to pods",
    [
        "eks_cluster_name",
        "pod_namespace",
        "deployment_name",
        "pod_name",
    ],
    registry=registry,
)

NODE_USAGE_COST = Gauge(
    "node_usage_cost",
    "Cost related to nodes",
    [
        "eks_cluster_name",
        "node_name",
        "instance_type",
        "hourly_cost",
    ],
    registry=registry,
)

NODE_WASTAGE_COST = Gauge(
    "node_wastage_cost",
    "Cost Unused metrics related to nodes",
    [
        "eks_cluster_name",
        "node_name",
        "instance_type",
        "hourly_cost",
    ],
    registry=registry,
)

NODE_ACTUAL_COST = Gauge(
    "node_actual_cost",
    "Cost Unused metrics related to nodes",
    [
        "eks_cluster_name",
        "node_name",
        "instance_type",
    ],
    registry=registry,
)

# Cache and lock for thread safety
cost_cache = {}
cache_lock = Lock()
CACHE_EXPIRY_SECONDS = 300  # Cache expires after 5 minutes

def scrape_metrics():
    """
    Collect metrics for pods and nodes in a thread-safe manner.
    """
    with metrics_lock:
        gather_pod_metadata()
        gather_node_metadata()

def convert_to_millicores(cpu_value):
    """Convert CPU usage/request values to millicores."""
    if cpu_value.endswith("n"):  # Nanocores
        return int(cpu_value[:-1]) / 1_000_000  # Convert nanocores to millicores
    elif cpu_value.endswith("m"):  # Millicores
        return int(cpu_value[:-1])
    return float(cpu_value) * 1000  # Convert cores to millicores

def convert_to_megabytes(memory_value):
    """Convert memory usage/request values to megabytes."""
    if memory_value.endswith("Ki"):  # Kibibytes
        return int(memory_value[:-2]) / 1024  # Convert KiB to MiB
    elif memory_value.endswith("Mi"):  # Mebibytes
        return int(memory_value[:-2])
    elif memory_value.endswith("Gi"):  # Gibibytes
        return int(memory_value[:-2]) * 1024  # Convert GiB to MiB
    return int(memory_value)  # Assume it's in MiB if no unit is provided

def get_node_metadata(core_v1, node_name):
    """Fetch region, instance type, capacity type, and operating system for a node."""
    try:
        node = core_v1.read_node(node_name)
        labels = node.metadata.labels
        region = labels.get("topology.kubernetes.io/region", "Unknown")
        instance_type = labels.get("node.kubernetes.io/instance-type", "Unknown")
        capacity_type = labels.get("eks.amazonaws.com/capacityType", "Unknown")
        operating_system = labels.get("kubernetes.io/os", "Unknown")  # Get OS from label
        return region, instance_type, capacity_type, operating_system
    except ApiException as e:
        print(f"Error fetching metadata for node {node_name}: {e}")
        return "Unknown", "Unknown", "Unknown", "Unknown"

def get_cost_per_vcpu_per_hour(region, instance_type, operating_system):
    """Fetch cost per vCPU per hour for a specific region, instance type, and operating system, with caching."""
    global cost_cache

    # Generate cache key
    cache_key = (region, instance_type, operating_system)

    # Check cache
    with cache_lock:
        if cache_key in cost_cache:
            cached_value, timestamp = cost_cache[cache_key]
            if time.time() - timestamp < CACHE_EXPIRY_SECONDS:
                # Return cached value if not expired
                return cached_value
            else:
                # Remove expired entry
                del cost_cache[cache_key]

    # Fetch fresh data
    try:
        namespace = "costapp"
        service_name = "eks-kube-resource-server-service"
        service_port = 80
        endpoint = f"http://{service_name}.{namespace}.svc.cluster.local:{service_port}/api/pricing"

        payload = {
            "region": region,
            "instance_type": instance_type,
            "operating_system": operating_system
        }

        response = requests.post(endpoint, json=payload, headers={"Content-Type": "application/json"}, timeout=5)
        response.raise_for_status()

        cost_data = response.json()
        cost_per_vcpu_per_hour = cost_data.get("cost_per_vcpu_per_hour", "Unknown")

        # Update cache
        with cache_lock:
            cost_cache[cache_key] = (cost_per_vcpu_per_hour, time.time())

        return cost_per_vcpu_per_hour

    except requests.exceptions.RequestException as e:
        print(f"Error fetching cost data for {region}, {instance_type}, {operating_system}: {e}")
        return "Unknown"
    

def get_deployment_name(pod):
    """Retrieve the deployment name for a given pod."""
    owner_references = pod.metadata.owner_references
    if owner_references:
        for owner in owner_references:
            if owner.kind == "ReplicaSet":
                # Deployment name is the name of the ReplicaSet without the last suffix (e.g., '-abc123').
                return owner.name.rsplit("-", 1)[0]
    return "Unknown"

def gather_pod_metadata():
    with semaphore:
        try:
            config.load_incluster_config()
            core_v1 = client.CoreV1Api()
            custom_api = client.CustomObjectsApi()

            current_namespace = os.getenv("POD_NAMESPACE", "default")
            eks_cluster_name = os.getenv("EKS_CLUSTER_NAME", "Unknown")
            allowed_namespaces = {"costapp", "loadapp"}

            ignore_namespaces = {"kube-system"}

            pods = core_v1.list_pod_for_all_namespaces(watch=False)

            print(f"{'Identifier':<20}{'EKS-Cluster':<20}{'PodNamespace':<15}{'Pod-Name':<30}{'Deployment-Name':<30}{'Region':<15}{'CPU-Usage':<15}{'Memory-Usage':<15}{'CPU-Request':<15}{'Memory-Request':<15}{'Unused-CPU':<15}{'Usage-Cost':<15}{'Wastage-Cost':<15}")

            for pod in pods.items:
                pod_namespace = pod.metadata.namespace
                # if namespace not in allowed_namespaces:
                #     continue

                if pod_namespace in ignore_namespaces:
                    continue
                
                pod_name = pod.metadata.name
                node_name = pod.spec.node_name
                region, instance_type, capacity_type, operating_system = get_node_metadata(core_v1, node_name)
                deployment_name = get_deployment_name(pod)

                cpu_request, memory_request, cpu_usage = 0, 0, 0
                unused_cpu = "-"

                containers = pod.spec.containers
                if containers and containers[0].resources.requests:
                    cpu_request = convert_to_millicores(containers[0].resources.requests.get('cpu', '0'))
                    memory_request = convert_to_megabytes(containers[0].resources.requests.get('memory', '0'))

                try:
                    metrics = custom_api.list_namespaced_custom_object(
                        group="metrics.k8s.io",
                        version="v1beta1",
                        namespace=pod_namespace,
                        plural="pods",
                    )
                    pod_metrics = next((p for p in metrics["items"] if p["metadata"]["name"] == pod_name), None)
                    if pod_metrics:
                        cpu_usage = convert_to_millicores(pod_metrics["containers"][0]["usage"].get("cpu", "0"))
                        memory_usage = convert_to_megabytes(pod_metrics["containers"][0]["usage"].get("memory", "0"))
                except ApiException:
                    cpu_usage, memory_usage = 0, 0

                if cpu_request > 0:
                    unused_cpu = cpu_request - cpu_usage

                cpu_usage = round(cpu_usage, 2)
                memory_usage = round(memory_usage, 2)
                cpu_request = round(cpu_request, 2)
                unused_cpu = round(unused_cpu, 2) if isinstance(unused_cpu, (int, float)) else unused_cpu

                cost_per_vcpu_per_hour = get_cost_per_vcpu_per_hour(region, instance_type, operating_system)

                if cost_per_vcpu_per_hour > 0:
                    # Calculate usage cost even if no CPU request is defined
                    usage_cost = round((cpu_usage / 1000) * cost_per_vcpu_per_hour, 8)
                    wastage_cost = round((unused_cpu / 1000) * cost_per_vcpu_per_hour, 8) if cpu_request > 0 else 0

                # Since Prometheus is Srcaping the data every minute
                # pod_usage_cost = round((usage_cost / 60), 8)
                # pod_wastage_cost = round((wastage_cost / 60), 8)
                pod_usage_cost = round((usage_cost), 8)
                pod_wastage_cost = round((wastage_cost), 8)

                print(f"{'Workload':<20} {eks_cluster_name:<20} {pod_namespace:<15} {pod_name:<30} {deployment_name:<30} {region:<15}{cpu_usage:<15}{memory_usage:<15}{cpu_request:<15}{memory_request:<15}{unused_cpu:<15}{usage_cost:<15.8f}{wastage_cost:<15.8f}")

                POD_USAGE_COST.labels(eks_cluster_name=eks_cluster_name,pod_namespace=pod_namespace,deployment_name=deployment_name,pod_name=pod_name).set(pod_usage_cost)
                POD_WASTAGE_COST.labels(eks_cluster_name=eks_cluster_name,pod_namespace=pod_namespace,deployment_name=deployment_name,pod_name=pod_name).set(pod_wastage_cost)
                ###

        except Exception as e:
            print(f"Error occurred: {e}")

def gather_node_metadata():
    """Fetch and display detailed node-level resource and cost metadata."""
    with semaphore:
        try:
            config.load_incluster_config()
            core_v1 = client.CoreV1Api()
            custom_api = client.CustomObjectsApi()

            eks_cluster_name = os.getenv("EKS_CLUSTER_NAME", "Unknown")

            print(
                f"{'Identifier':<20}{'EKS-Cluster':<20}{'Node-Name':<50}{'Current-CPU-Usage':<20}{'Current-Memory-Usage':<25}{'Total-CPU':<20}{'Total-Memory':<25}{'Instance-Type':<20}{'Hourly-Cost':<15}{'FiveMin-Cost':<15}{'Usage-Cost':<15}{'Wastage-Cost':<15}"
            )

            nodes = core_v1.list_node()

            for node in nodes.items:
                node_name = node.metadata.name
                labels = node.metadata.labels
                region = labels.get("topology.kubernetes.io/region", "Unknown")
                instance_type = labels.get("node.kubernetes.io/instance-type", "Unknown")
                operating_system = labels.get("kubernetes.io/os", "Unknown")

                total_cpu = convert_to_millicores(node.status.capacity.get("cpu", "0"))
                total_memory = convert_to_megabytes(node.status.capacity.get("memory", "0"))
                current_cpu_usage = 0
                current_memory_usage = 0
                hourly_cost = "Unknown"
                usage_cost = 0
                wastage_cost = 0

                # Fetch current resource usage
                try:
                    metrics = custom_api.list_cluster_custom_object(
                        group="metrics.k8s.io",
                        version="v1beta1",
                        plural="nodes",
                    )
                    node_metrics = next((n for n in metrics["items"] if n["metadata"]["name"] == node_name), None)
                    if node_metrics:
                        current_cpu_usage = convert_to_millicores(node_metrics["usage"].get("cpu", "0"))
                        current_memory_usage = convert_to_megabytes(node_metrics["usage"].get("memory", "0"))
                except ApiException as e:
                    print(f"Error fetching metrics for node {node_name}: {e}")

                # Fetch cost data
                try:
                    cost_per_vcpu_per_hour = get_cost_per_vcpu_per_hour(region, instance_type, operating_system)
                    if cost_per_vcpu_per_hour != "Unknown" and total_cpu > 0:
                        hourly_cost = float(cost_per_vcpu_per_hour) * (total_cpu / 1000)
                        hourly_cost = round(hourly_cost, 8)  # 8 decimal places
                        usage_cost = (current_cpu_usage / total_cpu) * hourly_cost
                        wastage_cost = ((total_cpu - current_cpu_usage) / total_cpu) * hourly_cost
                        usage_cost = round(usage_cost, 8)
                        wastage_cost = round(wastage_cost, 8)
                except Exception as e:
                    print(f"Error calculating cost for node {node_name}: {e}")

                # Since Prometheus is Srcaping the data every minute
                hourly_cost2 = round((hourly_cost / 12), 8)   ### Per 5 minute cost for testing Grafana Dashboard
                # usage_cost = round((usage_cost / 60), 8)
                # wastage_cost = round((wastage_cost / 60), 8)

                node_usage_cost = usage_cost
                node_wastage_cost = wastage_cost
                node_actual_cost = hourly_cost
                print(
                    f"{'Nodelevel':<20} {eks_cluster_name:<20} {node_name:<50} {current_cpu_usage:<20} {current_memory_usage:<25} {total_cpu:<20}{total_memory:<25}{instance_type:<20}{hourly_cost:<15}{hourly_cost2:<15}{usage_cost:<15}{wastage_cost:<15}"
                )

                NODE_USAGE_COST.labels(eks_cluster_name=eks_cluster_name,node_name=node_name,instance_type=instance_type,hourly_cost=hourly_cost).set(node_usage_cost)
                NODE_WASTAGE_COST.labels(eks_cluster_name=eks_cluster_name,node_name=node_name,instance_type=instance_type,hourly_cost=hourly_cost).set(node_wastage_cost)
                NODE_ACTUAL_COST.labels(eks_cluster_name=eks_cluster_name,node_name=node_name,instance_type=instance_type).set(node_actual_cost)

        except Exception as e:
            print(f"Error occurred in gather_node_metadata: {e}")

def run_scrape():
    """
    Continuously run metric scrapes in a thread-safe manner.
    """
    while True:
        scrape_metrics()
        time.sleep(300)  # Scrape interval - 5 min

if __name__ == "__main__":
    # Start Prometheus metrics server
    port = int(os.getenv("METRICS_PORT", "8000"))
    start_http_server(port, registry=registry)
    print(f"Prometheus metrics server started on port {port}...")

    # Start metrics collection in a separate thread
    threading.Thread(target=run_scrape, daemon=True).start()

    # Keep the main thread alive
    while True:
        time.sleep(1)
