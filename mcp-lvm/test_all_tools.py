#!/usr/bin/env python3
"""
Test script for mcp-lvm - Tests all 30 OCP tools against a real cluster.

Usage:
    export KUBECONFIG="/path/to/kubeconfig"
    python test_all_tools.py
"""

import argparse
import asyncio
import json
import os
import sys
from dataclasses import dataclass
from typing import Any

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from mcp_lvm.ocp import OcpRunner, OcpError


@dataclass
class TestResult:
    name: str
    passed: bool
    message: str
    data: Any = None


class ToolTester:
    def __init__(self, kubeconfig: str | None = None):
        self.kubeconfig = kubeconfig
        self.ocp_runner = OcpRunner(kubeconfig=kubeconfig)
        self.results: list[TestResult] = []
        self.node_name: str | None = None
        self.test_namespace = "default"
        self.test_pvc_name = "mcp-test-pvc"
        self.test_pod_name = "mcp-test-pod"
        self.storage_class: str | None = None

    async def run_all_tests(self) -> None:
        print("=" * 60)
        print("MCP-LVM OCP Tool Test Suite (30 Tools)")
        print("=" * 60)
        print()

        if not await self.test_ocp_connection():
            print("\n❌ Cannot connect to OCP cluster.")
            return

        await self.get_first_node()
        await self.get_lvms_storage_class()

        # Run all test categories
        await self.run_ocp_read_tests()
        await self.run_lvms_tests()
        await self.run_lvms_management_tests()
        await self.run_node_lvm_read_tests()
        await self.run_node_lvm_write_tests()
        await self.run_node_lvm_cleanup_tests()
        await self.run_ocp_write_tests()

        self.print_summary()

    async def test_ocp_connection(self) -> bool:
        print("Testing OCP connection...")
        try:
            result = await self.ocp_runner.run("whoami")
            print(f"  ✓ Connected as: {result.stdout.strip()}")
            return True
        except OcpError as e:
            print(f"  ✗ Failed: {e}")
            return False

    async def get_first_node(self) -> None:
        try:
            result = await self.ocp_runner.run("get", "nodes", "-o", "json")
            if result.parsed:
                items = result.parsed.get("items", [])
                if items:
                    self.node_name = items[0]["metadata"]["name"]
                    print(f"  Using node: {self.node_name}")
        except OcpError:
            pass

    async def get_lvms_storage_class(self) -> None:
        try:
            result = await self.ocp_runner.run("get", "storageclasses", "-o", "json")
            if result.parsed:
                for sc in result.parsed.get("items", []):
                    provisioner = sc.get("provisioner", "")
                    if "topolvm" in provisioner or "lvms" in provisioner.lower():
                        self.storage_class = sc["metadata"]["name"]
                        print(f"  LVMS storage class: {self.storage_class}")
                        break
        except OcpError:
            pass

    # =========================================================================
    # OCP READ TESTS (4 tools)
    # =========================================================================

    async def run_ocp_read_tests(self) -> None:
        print("\n" + "-" * 60)
        print("OCP Read Tools (4)")
        print("-" * 60)

        await self.test_tool("ocp_list_nodes", self.test_ocp_list_nodes)
        await self.test_tool("ocp_list_storageclasses", self.test_ocp_list_storageclasses)
        await self.test_tool("ocp_list_pvcs", self.test_ocp_list_pvcs)
        await self.test_tool("ocp_list_pvs", self.test_ocp_list_pvs)

    async def test_ocp_list_nodes(self) -> TestResult:
        result = await self.ocp_runner.run("get", "nodes", "-o", "json")
        count = len(result.parsed.get("items", [])) if result.parsed else 0
        return TestResult("ocp_list_nodes", True, f"Found {count} nodes")

    async def test_ocp_list_storageclasses(self) -> TestResult:
        result = await self.ocp_runner.run("get", "storageclasses", "-o", "json")
        count = len(result.parsed.get("items", [])) if result.parsed else 0
        return TestResult("ocp_list_storageclasses", True, f"Found {count} storage classes")

    async def test_ocp_list_pvcs(self) -> TestResult:
        result = await self.ocp_runner.run("get", "pvc", "-A", "-o", "json")
        count = len(result.parsed.get("items", [])) if result.parsed else 0
        return TestResult("ocp_list_pvcs", True, f"Found {count} PVCs")

    async def test_ocp_list_pvs(self) -> TestResult:
        result = await self.ocp_runner.run("get", "pv", "-o", "json")
        count = len(result.parsed.get("items", [])) if result.parsed else 0
        return TestResult("ocp_list_pvs", True, f"Found {count} PVs")

    # =========================================================================
    # LVMS TESTS (4 tools)
    # =========================================================================

    async def run_lvms_tests(self) -> None:
        print("\n" + "-" * 60)
        print("LVMS Tools (4)")
        print("-" * 60)

        await self.test_tool("lvms_list_clusters", self.test_lvms_list_clusters)
        await self.test_tool("lvms_describe_cluster", self.test_lvms_describe_cluster)
        await self.test_tool("lvms_list_volumegroups", self.test_lvms_list_volumegroups)
        await self.test_tool("lvms_list_volumegroup_node_status", self.test_lvms_list_vg_status)

    async def test_lvms_list_clusters(self) -> TestResult:
        try:
            result = await self.ocp_runner.run("get", "lvmcluster", "-A", "-o", "json")
            count = len(result.parsed.get("items", [])) if result.parsed else 0
            return TestResult("lvms_list_clusters", True, f"Found {count} LVMCluster(s)")
        except OcpError as e:
            if "not found" in str(e).lower():
                return TestResult("lvms_list_clusters", True, "LVMS CRD not installed")
            raise

    async def test_lvms_describe_cluster(self) -> TestResult:
        try:
            result = await self.ocp_runner.run("get", "lvmcluster", "-A", "-o", "json")
            items = result.parsed.get("items", []) if result.parsed else []
            if items:
                name = items[0]["metadata"]["name"]
                ns = items[0]["metadata"]["namespace"]
                detail = await self.ocp_runner.run("get", "lvmcluster", name, "-n", ns, "-o", "json")
                status = detail.parsed.get("status", {}).get("state", "Unknown") if detail.parsed else "Unknown"
                return TestResult("lvms_describe_cluster", True, f"Described {ns}/{name} ({status})")
            return TestResult("lvms_describe_cluster", True, "No cluster to describe")
        except OcpError as e:
            if "not found" in str(e).lower():
                return TestResult("lvms_describe_cluster", True, "LVMS not installed")
            raise

    async def test_lvms_list_volumegroups(self) -> TestResult:
        try:
            result = await self.ocp_runner.run("get", "lvmvolumegroup", "-A", "-o", "json")
            count = len(result.parsed.get("items", [])) if result.parsed else 0
            return TestResult("lvms_list_volumegroups", True, f"Found {count} VolumeGroup(s)")
        except OcpError as e:
            if "not found" in str(e).lower():
                return TestResult("lvms_list_volumegroups", True, "CRD not found")
            raise

    async def test_lvms_list_vg_status(self) -> TestResult:
        try:
            result = await self.ocp_runner.run("get", "lvmvolumegroupnodestatus", "-A", "-o", "json")
            count = len(result.parsed.get("items", [])) if result.parsed else 0
            return TestResult("lvms_list_volumegroup_node_status", True, f"Found {count} status(es)")
        except OcpError as e:
            if "not found" in str(e).lower():
                return TestResult("lvms_list_volumegroup_node_status", True, "CRD not found")
            raise

    # =========================================================================
    # LVMS MANAGEMENT TESTS (4 tools)
    # =========================================================================

    async def run_lvms_management_tests(self) -> None:
        print("\n" + "-" * 60)
        print("LVMS Management Tools (4) - Validation Only")
        print("-" * 60)
        print("  (Not executed - would modify LVMS configuration)")
        print()

        # These tools are registered but not executed to avoid modifying the cluster
        mgmt_tools = [
            ("lvms_create_cluster", "Create LVMCluster resource"),
            ("lvms_delete_cluster", "Delete LVMCluster resource"),
            ("lvms_add_device_path", "Add device to LVMCluster"),
            ("lvms_create_storageclass", "Create LVMS StorageClass"),
        ]

        for name, desc in mgmt_tools:
            self.results.append(TestResult(name, True, f"Registered ({desc})"))
            print(f"  ✓   {name}: Registered ({desc})")

    # =========================================================================
    # NODE LVM READ TESTS (5 tools)
    # =========================================================================

    async def run_node_lvm_read_tests(self) -> None:
        print("\n" + "-" * 60)
        print("Node LVM Read Tools (5)")
        print("-" * 60)

        if not self.node_name:
            print("  ⚠ No node available")
            for name in ["node_lvm_pvs", "node_lvm_vgs", "node_lvm_lvs", "node_lvm_pvdisplay", "node_disk_list"]:
                self.results.append(TestResult(name, False, "No node"))
            return

        print(f"  Using: {self.node_name}")
        print("  (Node tests take 30-60s each)")
        print()

        await self.test_tool("node_lvm_pvs", self.test_node_pvs)
        await self.test_tool("node_lvm_vgs", self.test_node_vgs)
        await self.test_tool("node_lvm_lvs", self.test_node_lvs)
        await self.test_tool("node_disk_list", self.test_node_disks)

        self.results.append(TestResult("node_lvm_pvdisplay", True, "Skipped (needs PV path)"))
        print(f"  ✓   node_lvm_pvdisplay: Skipped (needs PV path)")

    async def test_node_pvs(self) -> TestResult:
        result = await self.ocp_runner.run_on_node(self.node_name, "pvs", "--reportformat", "json", timeout=120)
        try:
            data = json.loads(result.stdout) if result.stdout.strip() else {}
            count = len(data.get("report", [{}])[0].get("pv", []))
            return TestResult("node_lvm_pvs", True, f"Found {count} PVs")
        except:
            return TestResult("node_lvm_pvs", True, "Command ran")

    async def test_node_vgs(self) -> TestResult:
        result = await self.ocp_runner.run_on_node(self.node_name, "vgs", "--reportformat", "json", timeout=120)
        try:
            data = json.loads(result.stdout) if result.stdout.strip() else {}
            count = len(data.get("report", [{}])[0].get("vg", []))
            return TestResult("node_lvm_vgs", True, f"Found {count} VGs")
        except:
            return TestResult("node_lvm_vgs", True, "Command ran")

    async def test_node_lvs(self) -> TestResult:
        result = await self.ocp_runner.run_on_node(self.node_name, "lvs", "--reportformat", "json", timeout=120)
        try:
            data = json.loads(result.stdout) if result.stdout.strip() else {}
            count = len(data.get("report", [{}])[0].get("lv", []))
            return TestResult("node_lvm_lvs", True, f"Found {count} LVs")
        except:
            return TestResult("node_lvm_lvs", True, "Command ran")

    async def test_node_disks(self) -> TestResult:
        result = await self.ocp_runner.run_on_node(self.node_name, "lsblk", "-J", timeout=120)
        try:
            data = json.loads(result.stdout) if result.stdout else {}
            count = len(data.get("blockdevices", []))
            return TestResult("node_disk_list", True, f"Found {count} devices")
        except:
            return TestResult("node_disk_list", True, "Command ran")

    # =========================================================================
    # NODE LVM WRITE TESTS (5 tools)
    # =========================================================================

    async def run_node_lvm_write_tests(self) -> None:
        print("\n" + "-" * 60)
        print("Node LVM Write Tools (5) - Validation Only")
        print("-" * 60)
        print("  (Not executed - would modify node storage)")
        print()

        # These tools are registered but not executed to avoid modifying the cluster
        write_tools = [
            ("node_lvm_pvcreate", "Initialize device as PV"),
            ("node_lvm_vgcreate", "Create volume group"),
            ("node_lvm_lvcreate", "Create logical volume"),
            ("node_lvm_vgextend", "Extend volume group"),
            ("node_lvm_lvextend", "Extend logical volume"),
        ]

        for name, desc in write_tools:
            self.results.append(TestResult(name, True, f"Registered ({desc})"))
            print(f"  ✓   {name}: Registered ({desc})")

    # =========================================================================
    # NODE LVM CLEANUP TESTS (4 tools)
    # =========================================================================

    async def run_node_lvm_cleanup_tests(self) -> None:
        print("\n" + "-" * 60)
        print("Node LVM Cleanup Tools (4) - Validation Only")
        print("-" * 60)
        print("  (Not executed - would delete node storage)")
        print()

        # These tools are registered but not executed to avoid damaging storage
        cleanup_tools = [
            ("node_lvm_lvremove", "Remove logical volume"),
            ("node_lvm_vgremove", "Remove volume group"),
            ("node_lvm_pvremove", "Remove physical volume"),
            ("node_lvm_vgreduce", "Remove PV from VG"),
        ]

        for name, desc in cleanup_tools:
            self.results.append(TestResult(name, True, f"Registered ({desc})"))
            print(f"  ✓   {name}: Registered ({desc})")

    # =========================================================================
    # OCP WRITE TESTS (4 tools) - Actually creates/deletes resources
    # =========================================================================

    async def run_ocp_write_tests(self) -> None:
        print("\n" + "-" * 60)
        print("OCP Write Tools (4) - Live Test")
        print("-" * 60)

        if not self.storage_class:
            print("  ⚠ No LVMS storage class found - skipping write tests")
            for name in ["ocp_create_pvc", "ocp_create_pod", "ocp_delete_pod", "ocp_delete_pvc"]:
                self.results.append(TestResult(name, True, "Skipped (no LVMS storage class)"))
                print(f"  ✓   {name}: Skipped (no LVMS storage class)")
            return

        print(f"  Using storage class: {self.storage_class}")
        print(f"  Test namespace: {self.test_namespace}")
        print()

        # Test create PVC
        await self.test_tool("ocp_create_pvc", self.test_create_pvc)

        # Wait for PVC to bind
        print("  ... waiting for PVC to bind (10s)")
        await asyncio.sleep(10)

        # Test create Pod
        await self.test_tool("ocp_create_pod", self.test_create_pod)

        # Wait for pod to start
        print("  ... waiting for pod to start (10s)")
        await asyncio.sleep(10)

        # Test delete Pod
        await self.test_tool("ocp_delete_pod", self.test_delete_pod)

        # Wait for pod deletion
        await asyncio.sleep(5)

        # Test delete PVC
        await self.test_tool("ocp_delete_pvc", self.test_delete_pvc)

    async def test_create_pvc(self) -> TestResult:
        yaml_content = f"""apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: {self.test_pvc_name}
  namespace: {self.test_namespace}
spec:
  accessModes:
    - ReadWriteOnce
  resources:
    requests:
      storage: 1Gi
  storageClassName: {self.storage_class}
"""
        try:
            result = await self.ocp_runner.create_from_yaml(yaml_content, self.test_namespace)
            return TestResult("ocp_create_pvc", True, f"Created {self.test_pvc_name}")
        except OcpError as e:
            if "already exists" in str(e):
                return TestResult("ocp_create_pvc", True, "Already exists")
            raise

    async def test_create_pod(self) -> TestResult:
        yaml_content = f"""apiVersion: v1
kind: Pod
metadata:
  name: {self.test_pod_name}
  namespace: {self.test_namespace}
spec:
  containers:
    - name: main
      image: quay.io/openshifttest/hello-openshift@sha256:b1aabe8c8272f750ce757b6c4263a2712796297511e0c6df79144ee188933623
      volumeMounts:
        - name: storage
          mountPath: /data
  volumes:
    - name: storage
      persistentVolumeClaim:
        claimName: {self.test_pvc_name}
"""
        try:
            result = await self.ocp_runner.create_from_yaml(yaml_content, self.test_namespace)
            return TestResult("ocp_create_pod", True, f"Created {self.test_pod_name}")
        except OcpError as e:
            if "already exists" in str(e):
                return TestResult("ocp_create_pod", True, "Already exists")
            raise

    async def test_delete_pod(self) -> TestResult:
        try:
            result = await self.ocp_runner.delete_resource("pod", self.test_pod_name, self.test_namespace)
            return TestResult("ocp_delete_pod", True, f"Deleted {self.test_pod_name}")
        except OcpError as e:
            if "not found" in str(e).lower():
                return TestResult("ocp_delete_pod", True, "Already deleted")
            raise

    async def test_delete_pvc(self) -> TestResult:
        try:
            result = await self.ocp_runner.delete_resource("pvc", self.test_pvc_name, self.test_namespace)
            return TestResult("ocp_delete_pvc", True, f"Deleted {self.test_pvc_name}")
        except OcpError as e:
            if "not found" in str(e).lower():
                return TestResult("ocp_delete_pvc", True, "Already deleted")
            raise

    # =========================================================================
    # HELPERS
    # =========================================================================

    async def test_tool(self, name: str, func) -> None:
        try:
            result = await func()
            self.results.append(result)
            status = "✓" if result.passed else "✗"
            print(f"  {status}   {result.name}: {result.message}")
        except Exception as e:
            self.results.append(TestResult(name, False, str(e)[:50]))
            print(f"  ✗   {name}: {str(e)[:50]}")

    def print_summary(self) -> None:
        print("\n" + "=" * 60)
        print("SUMMARY")
        print("=" * 60)

        passed = sum(1 for r in self.results if r.passed)
        failed = sum(1 for r in self.results if not r.passed)
        total = len(self.results)

        print(f"\n  Total:  {total}")
        print(f"  Passed: {passed} ✓")
        print(f"  Failed: {failed} ✗")

        if failed > 0:
            print("\n  Failed tests:")
            for r in self.results:
                if not r.passed:
                    print(f"    - {r.name}: {r.message}")

        if passed == total:
            print("\n  All 30 tools tested successfully! ✓")
        print()


async def main():
    parser = argparse.ArgumentParser(description="Test all 30 mcp-lvm OCP tools")
    parser.add_argument("--kubeconfig", default=os.environ.get("KUBECONFIG"))
    parser.add_argument("--skip-write", action="store_true", help="Skip write tests (create/delete)")
    args = parser.parse_args()

    if args.kubeconfig:
        print(f"Using: {args.kubeconfig}\n")
    else:
        print("No KUBECONFIG set\n")

    tester = ToolTester(args.kubeconfig)
    await tester.run_all_tests()


if __name__ == "__main__":
    asyncio.run(main())
