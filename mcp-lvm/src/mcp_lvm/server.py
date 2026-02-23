"""MCP server for OpenShift LVMS management.

This module defines an MCP server that exposes LVMS operations as tools.
Claude can use these tools to query and manage LVMS storage on OpenShift clusters.
"""

import argparse
import re
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import AsyncIterator

from mcp.server.fastmcp import FastMCP, Context

from .ocp import OcpRunner, OcpError, OcpResult


# =============================================================================
# INPUT VALIDATION
# =============================================================================

# Device path: must start with /dev/ followed by valid path characters
DEVICE_PATTERN = re.compile(r"^/dev/[a-zA-Z0-9/_-]+$")

# VG/LV names: alphanumeric plus a few safe special characters
NAME_PATTERN = re.compile(r"^[a-zA-Z0-9_\-+.]+$")

# Kubernetes resource name pattern (RFC 1123 subdomain)
K8S_NAME_PATTERN = re.compile(r"^[a-z0-9]([-a-z0-9.]*[a-z0-9])?$")

# Kubernetes namespace pattern
K8S_NAMESPACE_PATTERN = re.compile(r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$")


def validate_device(device: str) -> str:
    """Validate a device path."""
    if not DEVICE_PATTERN.match(device):
        raise ValueError(
            f"Invalid device path: '{device}'. "
            "Must start with /dev/ and contain only alphanumeric, /, _, -"
        )
    return device


def validate_name(name: str, label: str = "name") -> str:
    """Validate a resource name."""
    if not NAME_PATTERN.match(name):
        raise ValueError(
            f"Invalid {label}: '{name}'. "
            "Must contain only alphanumeric, _, -, +, ."
        )
    return name


def validate_node_name(node: str) -> str:
    """Validate a Kubernetes node name."""
    if not K8S_NAME_PATTERN.match(node):
        raise ValueError(
            f"Invalid node name: '{node}'. "
            "Must be lowercase alphanumeric with hyphens/dots"
        )
    return node


def validate_namespace(namespace: str) -> str:
    """Validate a Kubernetes namespace."""
    if not K8S_NAMESPACE_PATTERN.match(namespace):
        raise ValueError(
            f"Invalid namespace: '{namespace}'. "
            "Must be lowercase alphanumeric with hyphens"
        )
    return namespace


# =============================================================================
# SERVER CONFIGURATION
# =============================================================================

@dataclass
class AppContext:
    """Application context holding shared resources."""
    ocp_runner: OcpRunner


@asynccontextmanager
async def app_lifespan(server: FastMCP) -> AsyncIterator[AppContext]:
    """Manage application lifecycle."""
    ocp_runner = OcpRunner(kubeconfig=_kubeconfig)
    yield AppContext(ocp_runner=ocp_runner)


# Create the MCP server
mcp = FastMCP("mcp-lvm", lifespan=app_lifespan)


# =============================================================================
# HELPER FUNCTION
# =============================================================================

def format_ocp_result(result: OcpResult) -> dict:
    """Format an OcpResult for returning to Claude."""
    return {
        "command": result.command,
        "return_code": result.return_code,
        "output": result.parsed if result.parsed else result.stdout,
        "stderr": result.stderr if result.stderr else None,
    }


# =============================================================================
# OCP TOOLS
# =============================================================================

@mcp.tool()
async def ocp_list_nodes(ctx: Context) -> dict:
    """List all nodes in the OpenShift cluster.

    Returns node names, status, roles, and version information.
    Use this to see which nodes are available for LVM operations.

    Returns:
        List of cluster nodes with their status
    """
    app: AppContext = ctx.request_context.lifespan_context

    try:
        result = await app.ocp_runner.run(
            "get", "nodes", "-o", "json"
        )
        return format_ocp_result(result)
    except (OcpError, ValueError) as e:
        return {"error": str(e)}


@mcp.tool()
async def ocp_list_storageclasses(ctx: Context) -> dict:
    """List all storage classes in the cluster.

    Storage classes define how storage is provisioned.
    LVMS creates storage classes like 'lvms-vg1' for LVM volumes.

    Returns:
        List of storage classes with their provisioners
    """
    app: AppContext = ctx.request_context.lifespan_context

    try:
        result = await app.ocp_runner.run(
            "get", "storageclasses", "-o", "json"
        )
        return format_ocp_result(result)
    except (OcpError, ValueError) as e:
        return {"error": str(e)}


@mcp.tool()
async def ocp_list_pvcs(
    ctx: Context,
    namespace: str | None = None,
    storageclass: str | None = None,
) -> dict:
    """List PersistentVolumeClaims in the cluster.

    Shows all PVCs, optionally filtered by namespace or storage class.
    Useful for seeing which applications are using LVMS storage.

    Args:
        namespace: Optional namespace to filter by (default: all namespaces)
        storageclass: Optional storage class name to filter by

    Returns:
        List of PVCs with their status and storage class
    """
    app: AppContext = ctx.request_context.lifespan_context

    try:
        args = ["get", "pvc"]

        if namespace:
            validate_namespace(namespace)
            args.extend(["-n", namespace])
        else:
            args.append("-A")

        args.extend(["-o", "json"])

        result = await app.ocp_runner.run(*args)

        # Filter by storageclass if specified
        if storageclass and result.parsed:
            items = result.parsed.get("items", [])
            filtered = [
                item for item in items
                if item.get("spec", {}).get("storageClassName") == storageclass
            ]
            result.parsed["items"] = filtered

        return format_ocp_result(result)
    except (OcpError, ValueError) as e:
        return {"error": str(e)}


@mcp.tool()
async def ocp_list_pvs(ctx: Context, storageclass: str | None = None) -> dict:
    """List PersistentVolumes in the cluster.

    Shows all PVs with their status (Available, Bound, Released, Failed).
    Use this to check if PVs are bound to PVCs.

    Args:
        storageclass: Optional storage class name to filter by

    Returns:
        List of PVs with their status, capacity, and bound PVC info
    """
    app: AppContext = ctx.request_context.lifespan_context

    try:
        result = await app.ocp_runner.run("get", "pv", "-o", "json")

        # Filter by storageclass if specified
        if storageclass and result.parsed:
            items = result.parsed.get("items", [])
            filtered = [
                item for item in items
                if item.get("spec", {}).get("storageClassName") == storageclass
            ]
            result.parsed["items"] = filtered

        return format_ocp_result(result)
    except (OcpError, ValueError) as e:
        return {"error": str(e)}


# =============================================================================
# LVMS TOOLS
# =============================================================================

@mcp.tool()
async def lvms_list_clusters(ctx: Context, namespace: str | None = None) -> dict:
    """List LVMCluster resources in the cluster.

    LVMCluster is the main LVMS resource that defines which devices
    to use and how to configure volume groups.

    Args:
        namespace: Optional namespace (default: all namespaces)

    Returns:
        List of LVMCluster resources with their status
    """
    app: AppContext = ctx.request_context.lifespan_context

    try:
        args = ["get", "lvmcluster"]

        if namespace:
            validate_namespace(namespace)
            args.extend(["-n", namespace])
        else:
            args.append("-A")

        args.extend(["-o", "json"])

        result = await app.ocp_runner.run(*args)
        return format_ocp_result(result)
    except (OcpError, ValueError) as e:
        return {"error": str(e)}


@mcp.tool()
async def lvms_describe_cluster(
    ctx: Context,
    name: str,
    namespace: str = "openshift-lvm-storage",
) -> dict:
    """Get detailed information about an LVMCluster.

    Shows the full configuration and status of an LVMS cluster,
    including device selectors, volume groups, and thin pool settings.

    Args:
        name: Name of the LVMCluster resource
        namespace: Namespace containing the LVMCluster (default: openshift-lvm-storage)

    Returns:
        Detailed LVMCluster configuration and status
    """
    app: AppContext = ctx.request_context.lifespan_context

    try:
        validate_name(name, "LVMCluster name")
        validate_namespace(namespace)

        result = await app.ocp_runner.run(
            "get", "lvmcluster", name,
            "-n", namespace,
            "-o", "json"
        )
        return format_ocp_result(result)
    except (OcpError, ValueError) as e:
        return {"error": str(e)}


@mcp.tool()
async def lvms_list_volumegroups(ctx: Context, namespace: str | None = None) -> dict:
    """List LVMVolumeGroup resources in the cluster.

    LVMVolumeGroups are created by LVMS to represent
    the actual LVM volume groups on nodes.

    Args:
        namespace: Optional namespace (default: all namespaces)

    Returns:
        List of LVMVolumeGroup resources
    """
    app: AppContext = ctx.request_context.lifespan_context

    try:
        args = ["get", "lvmvolumegroup"]

        if namespace:
            validate_namespace(namespace)
            args.extend(["-n", namespace])
        else:
            args.append("-A")

        args.extend(["-o", "json"])

        result = await app.ocp_runner.run(*args)
        return format_ocp_result(result)
    except (OcpError, ValueError) as e:
        return {"error": str(e)}


@mcp.tool()
async def lvms_list_volumegroup_node_status(
    ctx: Context,
    namespace: str | None = None,
) -> dict:
    """List LVMVolumeGroupNodeStatus resources.

    These show the per-node status of LVMS volume groups,
    including available devices and capacity.

    Args:
        namespace: Optional namespace (default: all namespaces)

    Returns:
        Per-node volume group status information
    """
    app: AppContext = ctx.request_context.lifespan_context

    try:
        args = ["get", "lvmvolumegroupnodestatus"]

        if namespace:
            validate_namespace(namespace)
            args.extend(["-n", namespace])
        else:
            args.append("-A")

        args.extend(["-o", "json"])

        result = await app.ocp_runner.run(*args)
        return format_ocp_result(result)
    except (OcpError, ValueError) as e:
        return {"error": str(e)}


# =============================================================================
# LVMS MANAGEMENT TOOLS
# =============================================================================

@mcp.tool()
async def lvms_create_cluster(
    ctx: Context,
    name: str,
    namespace: str = "openshift-lvm-storage",
    device_classes: list[dict] | None = None,
) -> dict:
    """Create an LVMCluster resource.

    Creates an LVMS cluster configuration. If device_classes is not specified,
    uses default settings that auto-select available devices.

    Args:
        name: Name for the LVMCluster resource
        namespace: Namespace to create in (default: openshift-lvm-storage)
        device_classes: Optional list of device class configs, each with:
            - name: Device class name (e.g., "vg1")
            - fstype: Filesystem type (xfs or ext4, default: xfs)
            - device_selector: Optional dict with paths list (e.g., ["/dev/sdb"])
            - thin_pool_config: Optional dict with name, sizePercent, overprovisionRatio

    Returns:
        Command result with success/failure info

    Example device_classes:
        [{"name": "vg1", "fstype": "xfs", "device_selector": {"paths": ["/dev/sdb"]}}]
    """
    app: AppContext = ctx.request_context.lifespan_context

    try:
        validate_name(name, "LVMCluster name")
        validate_namespace(namespace)

        # Build device classes YAML
        if device_classes:
            dc_yaml = "    deviceClasses:\n"
            for dc in device_classes:
                dc_name = dc.get("name", "vg1")
                fs_type = dc.get("fstype", "xfs")
                validate_name(dc_name, "device class name")
                dc_yaml += f"      - name: {dc_name}\n"
                dc_yaml += f"        fstype: {fs_type}\n"
                dc_yaml += "        default: true\n"
                dc_yaml += "        thinPoolConfig:\n"

                tp_config = dc.get("thin_pool_config", {})
                tp_name = tp_config.get("name", "thin-pool-1")
                tp_size = tp_config.get("sizePercent", 90)
                tp_overprovision = tp_config.get("overprovisionRatio", 10)
                dc_yaml += f"          name: {tp_name}\n"
                dc_yaml += f"          sizePercent: {tp_size}\n"
                dc_yaml += f"          overprovisionRatio: {tp_overprovision}\n"
                dc_yaml += "          chunkSizeCalculationPolicy: Static\n"
                dc_yaml += "          metadataSizeCalculationPolicy: Host\n"

                if "device_selector" in dc:
                    paths = dc["device_selector"].get("paths", [])
                    if paths:
                        dc_yaml += "        deviceSelector:\n"
                        dc_yaml += "          paths:\n"
                        for path in paths:
                            validate_device(path)
                            dc_yaml += f"            - {path}\n"
        else:
            # Default: auto-select devices
            dc_yaml = """    deviceClasses:
      - name: vg1
        fstype: xfs
        default: true
        thinPoolConfig:
          name: thin-pool-1
          sizePercent: 90
          overprovisionRatio: 10
          chunkSizeCalculationPolicy: Static
          metadataSizeCalculationPolicy: Host
"""

        yaml_content = f"""apiVersion: lvm.topolvm.io/v1alpha1
kind: LVMCluster
metadata:
  name: {name}
  namespace: {namespace}
spec:
  storage:
{dc_yaml}"""

        result = await app.ocp_runner.create_from_yaml(yaml_content, namespace)
        return format_ocp_result(result)
    except (OcpError, ValueError) as e:
        return {"error": str(e)}


@mcp.tool()
async def lvms_delete_cluster(
    ctx: Context,
    name: str,
    namespace: str = "openshift-lvm-storage",
) -> dict:
    """Delete an LVMCluster resource.

    WARNING: This will remove the LVMS configuration. Ensure no PVCs
    are using storage from this cluster before deleting.

    Args:
        name: Name of the LVMCluster to delete
        namespace: Namespace containing the LVMCluster

    Returns:
        Command result with success/failure info
    """
    app: AppContext = ctx.request_context.lifespan_context

    try:
        validate_name(name, "LVMCluster name")
        validate_namespace(namespace)

        result = await app.ocp_runner.delete_resource("lvmcluster", name, namespace)
        return format_ocp_result(result)
    except (OcpError, ValueError) as e:
        return {"error": str(e)}


@mcp.tool()
async def lvms_add_device_path(
    ctx: Context,
    cluster_name: str,
    device_class: str,
    device_path: str,
    namespace: str = "openshift-lvm-storage",
) -> dict:
    """Add a device path to an existing LVMCluster device class.

    This patches the LVMCluster to add a new device to the device selector.
    The device will be added to the volume group on the next reconciliation.

    Args:
        cluster_name: Name of the LVMCluster
        device_class: Name of the device class to update
        device_path: Device path to add (e.g., /dev/sdc)
        namespace: Namespace containing the LVMCluster

    Returns:
        Command result with success/failure info
    """
    app: AppContext = ctx.request_context.lifespan_context

    try:
        validate_name(cluster_name, "LVMCluster name")
        validate_name(device_class, "device class name")
        validate_device(device_path)
        validate_namespace(namespace)

        # Get current LVMCluster
        get_result = await app.ocp_runner.run(
            "get", "lvmcluster", cluster_name,
            "-n", namespace,
            "-o", "json"
        )

        if not get_result.parsed:
            return {"error": "Failed to get LVMCluster"}

        # Find the device class and add the path
        spec = get_result.parsed.get("spec", {})
        storage = spec.get("storage", {})
        device_classes = storage.get("deviceClasses", [])

        found = False
        for dc in device_classes:
            if dc.get("name") == device_class:
                found = True
                if "deviceSelector" not in dc:
                    dc["deviceSelector"] = {"paths": []}
                if "paths" not in dc["deviceSelector"]:
                    dc["deviceSelector"]["paths"] = []
                if device_path not in dc["deviceSelector"]["paths"]:
                    dc["deviceSelector"]["paths"].append(device_path)
                break

        if not found:
            return {"error": f"Device class '{device_class}' not found"}

        # Apply the updated spec
        import json
        patch = json.dumps({"spec": {"storage": {"deviceClasses": device_classes}}})

        result = await app.ocp_runner.run(
            "patch", "lvmcluster", cluster_name,
            "-n", namespace,
            "--type=merge",
            "-p", patch
        )
        return format_ocp_result(result)
    except (OcpError, ValueError) as e:
        return {"error": str(e)}


@mcp.tool()
async def lvms_create_storageclass(
    ctx: Context,
    name: str,
    device_class: str = "vg1",
    fs_type: str = "xfs",
    volume_binding_mode: str = "WaitForFirstConsumer",
    allow_volume_expansion: bool = True,
) -> dict:
    """Create a StorageClass for LVMS.

    Creates a new StorageClass that provisions volumes using LVMS/TopoLVM.

    Args:
        name: Name for the StorageClass
        device_class: LVMS device class to use (default: vg1)
        fs_type: Filesystem type (xfs or ext4, default: xfs)
        volume_binding_mode: WaitForFirstConsumer or Immediate
        allow_volume_expansion: Allow volume expansion (default: True)

    Returns:
        Command result with success/failure info
    """
    app: AppContext = ctx.request_context.lifespan_context

    try:
        validate_name(name, "StorageClass name")
        validate_name(device_class, "device class name")

        if fs_type not in ("xfs", "ext4"):
            return {"error": "fs_type must be 'xfs' or 'ext4'"}

        yaml_content = f"""apiVersion: storage.k8s.io/v1
kind: StorageClass
metadata:
  name: {name}
provisioner: topolvm.io
parameters:
  topolvm.io/device-class: {device_class}
  csi.storage.k8s.io/fstype: {fs_type}
volumeBindingMode: {volume_binding_mode}
allowVolumeExpansion: {str(allow_volume_expansion).lower()}
reclaimPolicy: Delete
"""

        result = await app.ocp_runner.create_from_yaml(yaml_content)
        return format_ocp_result(result)
    except (OcpError, ValueError) as e:
        return {"error": str(e)}


# =============================================================================
# NODE LVM TOOLS
# =============================================================================
# These tools run LVM commands directly on cluster nodes via oc debug.

@mcp.tool()
async def node_lvm_pvs(ctx: Context, node: str) -> dict:
    """List physical volumes on a specific cluster node.

    Runs 'pvs' directly on the node via oc debug to show
    the actual LVM physical volumes on that node.

    Args:
        node: Node name to query (e.g., worker-0)

    Returns:
        Physical volume information from the node
    """
    app: AppContext = ctx.request_context.lifespan_context

    try:
        validate_node_name(node)

        result = await app.ocp_runner.run_on_node(
            node,
            "pvs",
            "--reportformat", "json", "--nosuffix", "--units", "b"
        )
        return format_ocp_result(result)
    except (OcpError, ValueError) as e:
        return {"error": str(e)}


@mcp.tool()
async def node_lvm_vgs(ctx: Context, node: str) -> dict:
    """List volume groups on a specific cluster node.

    Runs 'vgs' directly on the node via oc debug to show
    the actual LVM volume groups on that node.

    Args:
        node: Node name to query (e.g., worker-0)

    Returns:
        Volume group information from the node
    """
    app: AppContext = ctx.request_context.lifespan_context

    try:
        validate_node_name(node)

        result = await app.ocp_runner.run_on_node(
            node,
            "vgs",
            "--reportformat", "json", "--nosuffix", "--units", "b"
        )
        return format_ocp_result(result)
    except (OcpError, ValueError) as e:
        return {"error": str(e)}


@mcp.tool()
async def node_lvm_lvs(ctx: Context, node: str) -> dict:
    """List logical volumes on a specific cluster node.

    Runs 'lvs' directly on the node via oc debug to show
    the actual LVM logical volumes on that node.

    Args:
        node: Node name to query (e.g., worker-0)

    Returns:
        Logical volume information from the node
    """
    app: AppContext = ctx.request_context.lifespan_context

    try:
        validate_node_name(node)

        result = await app.ocp_runner.run_on_node(
            node,
            "lvs",
            "--reportformat", "json", "--nosuffix", "--units", "b"
        )
        return format_ocp_result(result)
    except (OcpError, ValueError) as e:
        return {"error": str(e)}


@mcp.tool()
async def node_lvm_pvdisplay(ctx: Context, node: str, pv: str) -> dict:
    """Show detailed info about a physical volume on a node.

    Runs 'pvdisplay' directly on the node for a specific device.

    Args:
        node: Node name to query
        pv: Physical volume device path (e.g., /dev/sdb)

    Returns:
        Detailed physical volume information
    """
    app: AppContext = ctx.request_context.lifespan_context

    try:
        validate_node_name(node)
        validate_device(pv)

        result = await app.ocp_runner.run_on_node(
            node,
            "pvdisplay",
            "--columns", "--reportformat", "json", "--nosuffix", "--units", "b",
            pv
        )
        return format_ocp_result(result)
    except (OcpError, ValueError) as e:
        return {"error": str(e)}


@mcp.tool()
async def node_disk_list(ctx: Context, node: str) -> dict:
    """List block devices on a specific cluster node.

    Runs 'lsblk' on the node to show all disks and partitions.
    Useful for identifying devices available for LVM.

    Args:
        node: Node name to query

    Returns:
        Block device information from the node
    """
    app: AppContext = ctx.request_context.lifespan_context

    try:
        validate_node_name(node)

        result = await app.ocp_runner.run_on_node(
            node,
            "lsblk",
            "-J", "-o", "NAME,SIZE,TYPE,MOUNTPOINT,FSTYPE"
        )
        return format_ocp_result(result)
    except (OcpError, ValueError) as e:
        return {"error": str(e)}


# =============================================================================
# NODE LVM WRITE TOOLS
# =============================================================================
# These tools run LVM write commands on cluster nodes via oc debug.
# WARNING: These modify the node's storage configuration.

@mcp.tool()
async def node_lvm_pvcreate(ctx: Context, node: str, device: str) -> dict:
    """Initialize a device as a physical volume on a cluster node.

    WARNING: This writes LVM metadata to the device.

    Args:
        node: Node name where the device exists
        device: Device path to initialize (e.g., /dev/sdb)

    Returns:
        Command result with success/failure info
    """
    app: AppContext = ctx.request_context.lifespan_context

    try:
        validate_node_name(node)
        validate_device(device)

        result = await app.ocp_runner.run_on_node(
            node,
            "pvcreate",
            device
        )
        return format_ocp_result(result)
    except (OcpError, ValueError) as e:
        return {"error": str(e)}


@mcp.tool()
async def node_lvm_vgcreate(
    ctx: Context,
    node: str,
    vg_name: str,
    devices: list[str],
) -> dict:
    """Create a volume group on a cluster node.

    Args:
        node: Node name where to create the VG
        vg_name: Name for the new volume group
        devices: List of PV device paths to include

    Returns:
        Command result with success/failure info
    """
    app: AppContext = ctx.request_context.lifespan_context

    try:
        validate_node_name(node)
        validate_name(vg_name, "VG name")

        if not devices:
            return {"error": "At least one device is required"}

        for device in devices:
            validate_device(device)

        result = await app.ocp_runner.run_on_node(
            node,
            "vgcreate",
            vg_name,
            *devices
        )
        return format_ocp_result(result)
    except (OcpError, ValueError) as e:
        return {"error": str(e)}


@mcp.tool()
async def node_lvm_lvcreate(
    ctx: Context,
    node: str,
    vg_name: str,
    lv_name: str,
    size: str,
) -> dict:
    """Create a logical volume on a cluster node.

    Args:
        node: Node name where to create the LV
        vg_name: Volume group to create the LV in
        lv_name: Name for the new logical volume
        size: Size specification (e.g., 10G, 500M, 100%FREE)

    Returns:
        Command result with success/failure info
    """
    app: AppContext = ctx.request_context.lifespan_context

    try:
        validate_node_name(node)
        validate_name(vg_name, "VG name")
        validate_name(lv_name, "LV name")

        # Determine size flag: -l for percentages, -L for absolute sizes
        if "%" in size:
            size_args = ["-l", size]
        else:
            size_args = ["-L", size]

        result = await app.ocp_runner.run_on_node(
            node,
            "lvcreate",
            "-n", lv_name,
            *size_args,
            vg_name
        )
        return format_ocp_result(result)
    except (OcpError, ValueError) as e:
        return {"error": str(e)}


@mcp.tool()
async def node_lvm_vgextend(
    ctx: Context,
    node: str,
    vg_name: str,
    devices: list[str],
) -> dict:
    """Add physical volumes to an existing volume group on a node.

    Args:
        node: Node name where the VG exists
        vg_name: Volume group to extend
        devices: List of PV device paths to add

    Returns:
        Command result with success/failure info
    """
    app: AppContext = ctx.request_context.lifespan_context

    try:
        validate_node_name(node)
        validate_name(vg_name, "VG name")

        if not devices:
            return {"error": "At least one device is required"}

        for device in devices:
            validate_device(device)

        result = await app.ocp_runner.run_on_node(
            node,
            "vgextend",
            vg_name,
            *devices
        )
        return format_ocp_result(result)
    except (OcpError, ValueError) as e:
        return {"error": str(e)}


@mcp.tool()
async def node_lvm_lvextend(
    ctx: Context,
    node: str,
    lv_path: str,
    size: str,
    resize_fs: bool = False,
) -> dict:
    """Extend a logical volume on a cluster node.

    Args:
        node: Node name where the LV exists
        lv_path: LV path (e.g., /dev/vg_name/lv_name)
        size: New size or size increase (e.g., 20G, +5G, +100%FREE)
        resize_fs: If True, also resize the filesystem

    Returns:
        Command result with success/failure info
    """
    app: AppContext = ctx.request_context.lifespan_context

    try:
        validate_node_name(node)
        validate_device(lv_path)

        # Determine size flag
        if "%" in size:
            size_args = ["-l", size]
        else:
            size_args = ["-L", size]

        args = list(size_args)
        if resize_fs:
            args.append("-r")
        args.append(lv_path)

        result = await app.ocp_runner.run_on_node(
            node,
            "lvextend",
            *args
        )
        return format_ocp_result(result)
    except (OcpError, ValueError) as e:
        return {"error": str(e)}


# =============================================================================
# NODE LVM CLEANUP TOOLS
# =============================================================================
# WARNING: These tools remove LVM structures. Use with caution.

@mcp.tool()
async def node_lvm_lvremove(
    ctx: Context,
    node: str,
    lv_path: str,
    force: bool = False,
) -> dict:
    """Remove a logical volume from a cluster node.

    WARNING: This permanently deletes the logical volume and all data on it.

    Args:
        node: Node name where the LV exists
        lv_path: LV path (e.g., /dev/vg_name/lv_name)
        force: Skip confirmation (required for non-interactive removal)

    Returns:
        Command result with success/failure info
    """
    app: AppContext = ctx.request_context.lifespan_context

    try:
        validate_node_name(node)
        validate_device(lv_path)

        args = ["-y"]  # Always use -y for non-interactive
        if force:
            args.append("-f")
        args.append(lv_path)

        result = await app.ocp_runner.run_on_node(
            node,
            "lvremove",
            *args
        )
        return format_ocp_result(result)
    except (OcpError, ValueError) as e:
        return {"error": str(e)}


@mcp.tool()
async def node_lvm_vgremove(
    ctx: Context,
    node: str,
    vg_name: str,
    force: bool = False,
) -> dict:
    """Remove a volume group from a cluster node.

    WARNING: This permanently removes the volume group.
    All logical volumes in the VG must be removed first.

    Args:
        node: Node name where the VG exists
        vg_name: Name of the volume group to remove
        force: Skip confirmation

    Returns:
        Command result with success/failure info
    """
    app: AppContext = ctx.request_context.lifespan_context

    try:
        validate_node_name(node)
        validate_name(vg_name, "VG name")

        args = ["-y"]
        if force:
            args.append("-f")
        args.append(vg_name)

        result = await app.ocp_runner.run_on_node(
            node,
            "vgremove",
            *args
        )
        return format_ocp_result(result)
    except (OcpError, ValueError) as e:
        return {"error": str(e)}


@mcp.tool()
async def node_lvm_pvremove(
    ctx: Context,
    node: str,
    device: str,
    force: bool = False,
) -> dict:
    """Remove LVM metadata from a physical volume on a cluster node.

    WARNING: This removes LVM metadata from the device.
    The device must not be part of any volume group.

    Args:
        node: Node name where the device exists
        device: Device path (e.g., /dev/sdb)
        force: Force removal even if VG info is missing

    Returns:
        Command result with success/failure info
    """
    app: AppContext = ctx.request_context.lifespan_context

    try:
        validate_node_name(node)
        validate_device(device)

        args = ["-y"]
        if force:
            args.extend(["-f", "-f"])  # Double -f for forced removal
        args.append(device)

        result = await app.ocp_runner.run_on_node(
            node,
            "pvremove",
            *args
        )
        return format_ocp_result(result)
    except (OcpError, ValueError) as e:
        return {"error": str(e)}


@mcp.tool()
async def node_lvm_vgreduce(
    ctx: Context,
    node: str,
    vg_name: str,
    device: str,
) -> dict:
    """Remove a physical volume from a volume group on a cluster node.

    This shrinks the volume group by removing the specified PV.
    The PV must not have any allocated extents (data must be moved first).

    Args:
        node: Node name where the VG exists
        vg_name: Name of the volume group
        device: Device path to remove from the VG (e.g., /dev/sdb)

    Returns:
        Command result with success/failure info
    """
    app: AppContext = ctx.request_context.lifespan_context

    try:
        validate_node_name(node)
        validate_name(vg_name, "VG name")
        validate_device(device)

        result = await app.ocp_runner.run_on_node(
            node,
            "vgreduce",
            vg_name,
            device
        )
        return format_ocp_result(result)
    except (OcpError, ValueError) as e:
        return {"error": str(e)}


# =============================================================================
# KUBERNETES RESOURCE CREATION TOOLS
# =============================================================================

@mcp.tool()
async def ocp_create_pvc(
    ctx: Context,
    name: str,
    namespace: str,
    size: str,
    storage_class: str,
    access_mode: str = "ReadWriteOnce",
) -> dict:
    """Create a PersistentVolumeClaim.

    Args:
        name: Name for the PVC
        namespace: Namespace to create the PVC in
        size: Storage size (e.g., 10Gi, 500Mi)
        storage_class: Storage class to use (e.g., lvms-vg1)
        access_mode: Access mode (ReadWriteOnce, ReadWriteMany, ReadOnlyMany)

    Returns:
        Command result with success/failure info
    """
    app: AppContext = ctx.request_context.lifespan_context

    try:
        validate_name(name, "PVC name")
        validate_namespace(namespace)
        validate_name(storage_class, "storage class")

        yaml_content = f"""apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: {name}
  namespace: {namespace}
spec:
  accessModes:
    - {access_mode}
  resources:
    requests:
      storage: {size}
  storageClassName: {storage_class}
"""

        result = await app.ocp_runner.create_from_yaml(yaml_content, namespace)
        return format_ocp_result(result)
    except (OcpError, ValueError) as e:
        return {"error": str(e)}


@mcp.tool()
async def ocp_create_pod(
    ctx: Context,
    name: str,
    namespace: str,
    image: str,
    pvc_name: str | None = None,
    mount_path: str = "/data",
) -> dict:
    """Create a Pod, optionally with a PVC mounted.

    Args:
        name: Name for the Pod
        namespace: Namespace to create the Pod in
        image: Container image to use
        pvc_name: Optional PVC name to mount
        mount_path: Mount path for the PVC (default: /data)

    Returns:
        Command result with success/failure info
    """
    app: AppContext = ctx.request_context.lifespan_context

    try:
        validate_name(name, "Pod name")
        validate_namespace(namespace)

        if pvc_name:
            validate_name(pvc_name, "PVC name")
            yaml_content = f"""apiVersion: v1
kind: Pod
metadata:
  name: {name}
  namespace: {namespace}
spec:
  containers:
    - name: main
      image: {image}
      command: ["sleep", "infinity"]
      volumeMounts:
        - name: storage
          mountPath: {mount_path}
  volumes:
    - name: storage
      persistentVolumeClaim:
        claimName: {pvc_name}
"""
        else:
            yaml_content = f"""apiVersion: v1
kind: Pod
metadata:
  name: {name}
  namespace: {namespace}
spec:
  containers:
    - name: main
      image: {image}
      command: ["sleep", "infinity"]
"""

        result = await app.ocp_runner.create_from_yaml(yaml_content, namespace)
        return format_ocp_result(result)
    except (OcpError, ValueError) as e:
        return {"error": str(e)}


@mcp.tool()
async def ocp_delete_pvc(ctx: Context, name: str, namespace: str) -> dict:
    """Delete a PersistentVolumeClaim.

    Args:
        name: Name of the PVC to delete
        namespace: Namespace containing the PVC

    Returns:
        Command result with success/failure info
    """
    app: AppContext = ctx.request_context.lifespan_context

    try:
        validate_name(name, "PVC name")
        validate_namespace(namespace)

        result = await app.ocp_runner.delete_resource("pvc", name, namespace)
        return format_ocp_result(result)
    except (OcpError, ValueError) as e:
        return {"error": str(e)}


@mcp.tool()
async def ocp_delete_pod(ctx: Context, name: str, namespace: str) -> dict:
    """Delete a Pod.

    Args:
        name: Name of the Pod to delete
        namespace: Namespace containing the Pod

    Returns:
        Command result with success/failure info
    """
    app: AppContext = ctx.request_context.lifespan_context

    try:
        validate_name(name, "Pod name")
        validate_namespace(namespace)

        result = await app.ocp_runner.delete_resource("pod", name, namespace)
        return format_ocp_result(result)
    except (OcpError, ValueError) as e:
        return {"error": str(e)}


# =============================================================================
# CLI ENTRY POINT
# =============================================================================

def main():
    """Command-line entry point for the MCP server."""
    parser = argparse.ArgumentParser(
        description="MCP server for OpenShift LVMS management",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  mcp-lvm                              Start with stdio transport (default)
  mcp-lvm --transport sse              Start with SSE/HTTP transport
  mcp-lvm --transport sse --port 8080
  mcp-lvm --kubeconfig ~/.kube/config  Use specific kubeconfig
        """,
    )

    parser.add_argument(
        "--transport",
        choices=["stdio", "sse", "streamable-http"],
        default="stdio",
        help="Transport protocol (default: stdio)",
    )

    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Host to bind for HTTP transports (default: 127.0.0.1)",
    )

    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Port for HTTP transports (default: 8000)",
    )

    parser.add_argument(
        "--kubeconfig",
        help="Path to kubeconfig file (default: KUBECONFIG env var)",
    )

    args = parser.parse_args()

    # Configure using module-level flag
    global _kubeconfig
    _kubeconfig = args.kubeconfig

    # Run the server
    if args.transport == "stdio":
        mcp.run()
    else:
        mcp.run(transport=args.transport)


# Module-level config (set by CLI)
_kubeconfig: str | None = None


# Override the lifespan to use the CLI flag
@asynccontextmanager
async def app_lifespan(server: FastMCP) -> AsyncIterator[AppContext]:
    """Manage application lifecycle with CLI-configured options."""
    ocp_runner = OcpRunner(kubeconfig=_kubeconfig)
    yield AppContext(ocp_runner=ocp_runner)


# Re-assign the lifespan
mcp._lifespan = app_lifespan


if __name__ == "__main__":
    main()
