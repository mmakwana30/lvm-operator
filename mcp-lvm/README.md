# mcp-lvm

An MCP (Model Context Protocol) server for OpenShift LVMS management. This allows AI assistants like Claude to query and manage LVMS storage on OpenShift clusters.

## What is This?

```
Claude <---> MCP Protocol <---> mcp-lvm server <---> oc CLI <---> OpenShift
```

When connected, Claude can:
- List LVMS clusters, volume groups, and node status
- Query storage classes and PVCs
- Run LVM commands directly on cluster nodes via `oc debug`

## Prerequisites

- **Python 3.10+**
- **oc CLI** installed and configured
- **KUBECONFIG** pointing to your OpenShift cluster
- **LVMS** installed on your cluster

## Installation

```bash
# Clone and install
git clone https://github.com/yourusername/mcp-lvm.git
cd mcp-lvm
uv sync

# Verify
uv run mcp-lvm --help
```

## Usage

```bash
# Set your kubeconfig
export KUBECONFIG=/path/to/kubeconfig

# Start the server
uv run mcp-lvm
```

Or specify kubeconfig directly:

```bash
uv run mcp-lvm --kubeconfig /path/to/kubeconfig
```

### Claude Desktop Configuration

Add to `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "lvm": {
      "command": "uv",
      "args": ["--directory", "/path/to/mcp-lvm", "run", "mcp-lvm"],
      "env": {
        "KUBECONFIG": "/path/to/kubeconfig"
      }
    }
  }
}
```

## Available Tools (30)

### OCP Read Tools (4)

| Tool | Description |
|------|-------------|
| `ocp_list_nodes` | List cluster nodes |
| `ocp_list_storageclasses` | List storage classes |
| `ocp_list_pvcs` | List PVCs (filter by namespace/storageclass) |
| `ocp_list_pvs` | List PVs and check bound status |

### LVMS Read Tools (4)

| Tool | Description |
|------|-------------|
| `lvms_list_clusters` | List LVMCluster resources |
| `lvms_describe_cluster` | Get detailed LVMCluster info |
| `lvms_list_volumegroups` | List LVMVolumeGroup resources |
| `lvms_list_volumegroup_node_status` | Per-node VG status |

### LVMS Management Tools (4)

| Tool | Description |
|------|-------------|
| `lvms_create_cluster` | Create LVMCluster resource |
| `lvms_delete_cluster` | Delete LVMCluster resource |
| `lvms_add_device_path` | Add device to LVMCluster |
| `lvms_create_storageclass` | Create LVMS StorageClass |

### Node LVM Read Tools (5)

Run LVM commands directly on cluster nodes via `oc debug`:

| Tool | Description |
|------|-------------|
| `node_lvm_pvs` | Run `pvs` on a node |
| `node_lvm_vgs` | Run `vgs` on a node |
| `node_lvm_lvs` | Run `lvs` on a node |
| `node_lvm_pvdisplay` | Run `pvdisplay` on a node |
| `node_disk_list` | Run `lsblk` on a node |

### Node LVM Write Tools (5)

| Tool | Description |
|------|-------------|
| `node_lvm_pvcreate` | Initialize device as PV |
| `node_lvm_vgcreate` | Create volume group |
| `node_lvm_lvcreate` | Create logical volume |
| `node_lvm_vgextend` | Extend volume group |
| `node_lvm_lvextend` | Extend logical volume |

### Node LVM Cleanup Tools (4)

| Tool | Description |
|------|-------------|
| `node_lvm_lvremove` | Remove logical volume |
| `node_lvm_vgremove` | Remove volume group |
| `node_lvm_pvremove` | Remove physical volume |
| `node_lvm_vgreduce` | Remove PV from VG |

### OCP Write Tools (4)

| Tool | Description |
|------|-------------|
| `ocp_create_pvc` | Create PersistentVolumeClaim |
| `ocp_create_pod` | Create Pod with optional PVC |
| `ocp_delete_pvc` | Delete PersistentVolumeClaim |
| `ocp_delete_pod` | Delete Pod |

## Example Conversations

> "Show me the LVMS configuration"

Claude uses `lvms_list_clusters` and `lvms_describe_cluster`.

> "What PVCs are using LVMS storage?"

Claude uses `ocp_list_pvcs` filtered by storage class.

> "Show LVM status on the worker node"

Claude uses `node_lvm_vgs` and `node_lvm_lvs`.

## Testing

```bash
# Run unit tests
uv run pytest tests/ -v

# Test against real cluster
export KUBECONFIG=/path/to/kubeconfig
python test_all_tools.py
```

## Security

- Allowlisted oc commands: `get`, `describe`, `create`, `delete`, `apply`, `patch`, `debug`
- Allowlisted LVM commands on nodes: `pvs`, `vgs`, `lvs`, `lsblk`, `pvcreate`, `vgcreate`, `lvcreate`, etc.
- Input validation prevents command injection
- No shell execution (uses `subprocess.exec`)
- Device paths validated against `/dev/` prefix

## License

MIT
