/*
Copyright © 2023 Red Hat, Inc.

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
*/

package e2e

import (
	"os/exec"
	"strings"
	"testing"
)

// TestLVMWipeFeature tests the LVM Operator's -y flag usage
// This test backs up LVMCluster, deletes it, checks cleanup, then restores it
func TestLVMWipeFeature(t *testing.T) {
	t.Log("Testing LVM Operator's -y flag usage...")
	
	// Step 1: Backup LVMCluster CR
	t.Log("Step 1: Backing up LVMCluster CR...")
	err := backupLVMCluster()
	if err != nil {
		t.Fatalf("Failed to backup LVMCluster: %v", err)
	}
	t.Log("✓ LVMCluster CR backed up successfully")
	
	// Step 2: Delete LVMCluster to trigger cleanup
	t.Log("Step 2: Deleting LVMCluster to trigger cleanup...")
	err = deleteLVMCluster()
	if err != nil {
		t.Fatalf("Failed to delete LVMCluster: %v", err)
	}
	t.Log("✓ LVMCluster deleted successfully")
	
	// Step 3: Check if VG1 is still present after cleanup
	t.Log("Step 3: Checking if VG1 was cleaned up...")
	vgExists := checkVGExists("vg1")
	
	if vgExists {
		// VG1 still exists - LVMCluster failed to clean up (test should FAIL)
		t.Log("❌ VG1 still exists - LVMCluster failed to clean up")
		t.Log("❌ LVMCluster failed - VG1 was not cleaned up properly")
		t.Log("❌ -y flag did NOT work - cleanup failed")
		t.Fail()
	} else {
		// VG1 was cleaned up - LVMCluster passed (test should PASS)
		t.Log("✓ VG1 does not exist - LVMCluster cleaned up properly")
		t.Log("✓ LVMCluster passed - VG1 was properly cleaned up")
		t.Log("✓ -y flag feature is working correctly")
	}
	
	// Step 4: Restore LVMCluster CR
	t.Log("Step 4: Restoring LVMCluster CR...")
	err = restoreLVMCluster()
	if err != nil {
		t.Fatalf("Failed to restore LVMCluster: %v", err)
	}
	t.Log("✓ LVMCluster CR restored successfully")
}

// backupLVMCluster backs up the LVMCluster CR to a file
func backupLVMCluster() error {
	cmd := exec.Command("sh", "-c", "oc get lvmcluster -n openshift-lvm-storage -o yaml > lvmcluster-backup.yaml")
	return cmd.Run()
}

// deleteLVMCluster deletes the LVMCluster using oc command
func deleteLVMCluster() error {
	cmd := exec.Command("oc", "delete", "lvmcluster", "--all", "-n", "openshift-lvm-storage")
	return cmd.Run()
}

// restoreLVMCluster restores the LVMCluster CR from backup file
func restoreLVMCluster() error {
	cmd := exec.Command("oc", "apply", "-f", "lvmcluster-backup.yaml")
	return cmd.Run()
}

// checkVGExists checks if a volume group exists using vgs command
// Returns true if VG exists (cleanup failed), false if VG doesn't exist (cleanup succeeded)
func checkVGExists(vgName string) bool {
	cmd := exec.Command("vgs", vgName, "--noheadings", "--nosuffix")
	output, err := cmd.Output()
	if err != nil {
		return false // VG doesn't exist (cleanup succeeded)
	}
	
	// If output contains the VG name, it exists (cleanup failed)
	return strings.Contains(string(output), vgName)
}

