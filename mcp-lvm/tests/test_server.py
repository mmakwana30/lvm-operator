"""Tests for the MCP server validation and tools."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from mcp_lvm.server import (
    validate_device,
    validate_name,
    validate_node_name,
    validate_namespace,
)
from mcp_lvm.ocp import OcpResult


class TestDeviceValidation:
    """Tests for device path validation."""

    def test_valid_simple_device(self):
        assert validate_device("/dev/sda") == "/dev/sda"
        assert validate_device("/dev/sdb1") == "/dev/sdb1"
        assert validate_device("/dev/nvme0n1") == "/dev/nvme0n1"

    def test_valid_mapper_device(self):
        assert validate_device("/dev/mapper/my-volume") == "/dev/mapper/my-volume"

    def test_invalid_not_dev(self):
        with pytest.raises(ValueError):
            validate_device("/etc/passwd")

    def test_invalid_injection(self):
        with pytest.raises(ValueError):
            validate_device("/dev/sda;rm -rf /")


class TestNameValidation:
    """Tests for resource name validation."""

    def test_valid_names(self):
        assert validate_name("myvg") == "myvg"
        assert validate_name("data_vg") == "data_vg"
        assert validate_name("my-vg") == "my-vg"

    def test_invalid_injection(self):
        with pytest.raises(ValueError):
            validate_name("vg;rm -rf /")


class TestNodeNameValidation:
    """Tests for Kubernetes node name validation."""

    def test_valid_node_names(self):
        assert validate_node_name("worker-0") == "worker-0"
        assert validate_node_name("ip-10-0-1-5.ec2.internal") == "ip-10-0-1-5.ec2.internal"
        assert validate_node_name("node1") == "node1"

    def test_invalid_uppercase(self):
        with pytest.raises(ValueError):
            validate_node_name("Worker-0")

    def test_invalid_injection(self):
        with pytest.raises(ValueError):
            validate_node_name("worker;rm -rf /")


class TestNamespaceValidation:
    """Tests for Kubernetes namespace validation."""

    def test_valid_namespaces(self):
        assert validate_namespace("default") == "default"
        assert validate_namespace("openshift-lvm-storage") == "openshift-lvm-storage"
        assert validate_namespace("kube-system") == "kube-system"

    def test_invalid_uppercase(self):
        with pytest.raises(ValueError):
            validate_namespace("Default")

    def test_invalid_injection(self):
        with pytest.raises(ValueError):
            validate_namespace("ns;rm -rf /")


class TestOcpTools:
    """Tests for OCP tool functions."""

    @pytest.fixture
    def mock_context(self):
        """Create a mock MCP context with OcpRunner."""
        mock_runner = AsyncMock()
        mock_runner.run.return_value = OcpResult(
            command="oc get nodes -o json",
            return_code=0,
            stdout='{"items":[]}',
            stderr="",
            parsed={"items": []},
        )
        mock_runner.run_on_node.return_value = OcpResult(
            command="oc debug node/test",
            return_code=0,
            stdout='{"report":[]}',
            stderr="",
            parsed={"report": []},
        )

        mock_ctx = MagicMock()
        mock_ctx.request_context.lifespan_context.ocp_runner = mock_runner

        return mock_ctx, mock_runner

    async def test_ocp_list_nodes(self, mock_context):
        """ocp_list_nodes should call runner correctly."""
        from mcp_lvm.server import ocp_list_nodes

        mock_ctx, mock_runner = mock_context
        result = await ocp_list_nodes(mock_ctx)

        mock_runner.run.assert_called_once()
        call_args = mock_runner.run.call_args[0]
        assert "get" in call_args
        assert "nodes" in call_args

    async def test_ocp_list_storageclasses(self, mock_context):
        """ocp_list_storageclasses should call runner correctly."""
        from mcp_lvm.server import ocp_list_storageclasses

        mock_ctx, mock_runner = mock_context
        result = await ocp_list_storageclasses(mock_ctx)

        mock_runner.run.assert_called_once()
        call_args = mock_runner.run.call_args[0]
        assert "storageclasses" in call_args

    async def test_lvms_list_clusters(self, mock_context):
        """lvms_list_clusters should call runner correctly."""
        from mcp_lvm.server import lvms_list_clusters

        mock_ctx, mock_runner = mock_context
        result = await lvms_list_clusters(mock_ctx)

        mock_runner.run.assert_called_once()
        call_args = mock_runner.run.call_args[0]
        assert "lvmcluster" in call_args

    async def test_node_lvm_pvs(self, mock_context):
        """node_lvm_pvs should call run_on_node correctly."""
        from mcp_lvm.server import node_lvm_pvs

        mock_ctx, mock_runner = mock_context
        result = await node_lvm_pvs(mock_ctx, node="worker-0")

        mock_runner.run_on_node.assert_called_once()
        call_args = mock_runner.run_on_node.call_args
        assert call_args[0][0] == "worker-0"
        assert call_args[0][1] == "pvs"

    async def test_node_lvm_pvs_validates_node(self, mock_context):
        """node_lvm_pvs should validate node name."""
        from mcp_lvm.server import node_lvm_pvs

        mock_ctx, mock_runner = mock_context

        # Invalid node name should return error
        result = await node_lvm_pvs(mock_ctx, node="INVALID;rm -rf /")
        assert "error" in result

    async def test_node_disk_list(self, mock_context):
        """node_disk_list should call run_on_node correctly."""
        from mcp_lvm.server import node_disk_list

        mock_ctx, mock_runner = mock_context
        result = await node_disk_list(mock_ctx, node="worker-0")

        mock_runner.run_on_node.assert_called_once()
        call_args = mock_runner.run_on_node.call_args
        assert "lsblk" in call_args[0]
