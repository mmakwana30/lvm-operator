"""OCP (OpenShift) command wrapper for oc CLI operations."""

import asyncio
import json
import os
import shutil
from dataclasses import dataclass, field


# Allowed oc subcommands
ALLOWED_OC_COMMANDS = frozenset({
    "get",        # Get resources
    "describe",   # Describe resources
    "whoami",     # Current user
    "debug",      # Debug node (for running commands on nodes)
    "version",    # Cluster version
    "create",     # Create resources
    "apply",      # Apply resources
    "delete",     # Delete resources
    "patch",      # Patch resources
})

# Allowed LVM commands to run on nodes via oc debug
ALLOWED_NODE_LVM_COMMANDS = frozenset({
    # Read commands
    "pvs",
    "vgs",
    "lvs",
    "pvdisplay",
    "vgdisplay",
    "lvdisplay",
    "lsblk",
    # Write commands
    "pvcreate",
    "vgcreate",
    "lvcreate",
    "vgextend",
    "lvextend",
    # Cleanup commands
    "lvremove",
    "vgremove",
    "pvremove",
    "vgreduce",
})

# Resource types we allow querying
ALLOWED_RESOURCES = frozenset({
    "nodes",
    "node",
    "lvmcluster",
    "lvmclusters",
    "lvmvolumegroup",
    "lvmvolumegroups",
    "lvmvolumegroupnodestatus",
    "storageclass",
    "storageclasses",
    "sc",
    "pvc",
    "pv",
    "persistentvolumeclaim",
    "persistentvolumeclaims",
    "persistentvolume",
    "persistentvolumes",
    "pod",
    "pods",
})


class OcpError(Exception):
    """Base exception for OCP operations."""
    pass


class OcpCommandNotFoundError(OcpError):
    """Raised when oc CLI is not found."""

    def __init__(self):
        super().__init__(
            "oc CLI not found. Install OpenShift CLI: "
            "https://docs.openshift.com/container-platform/latest/cli_reference/openshift_cli/getting-started-cli.html"
        )


class OcpNotLoggedInError(OcpError):
    """Raised when not logged into a cluster."""

    def __init__(self, message: str):
        self.message = message
        super().__init__(f"Not logged into cluster: {message}")


class OcpCommandError(OcpError):
    """Raised when an oc command fails."""

    def __init__(self, command: str, return_code: int, stderr: str):
        self.command = command
        self.return_code = return_code
        self.stderr = stderr
        super().__init__(f"oc command failed ({return_code}): {stderr}")


@dataclass
class OcpResult:
    """Container for oc command results.

    Attributes:
        command: The full command that was run
        return_code: Exit code (0 = success)
        stdout: Standard output
        stderr: Standard error
        parsed: Parsed JSON if -o json was used
    """
    command: str
    return_code: int
    stdout: str
    stderr: str
    parsed: dict | list | None = field(default=None)


class OcpRunner:
    """Runs oc commands safely with subprocess.

    This class wraps oc CLI execution with:
    - Command allowlist (read-only operations)
    - JSON output parsing
    - Timeout handling
    - Error detection

    Example:
        runner = OcpRunner()
        result = await runner.run("get", "nodes", "-o", "json")
        print(result.parsed)  # {'items': [...]}
    """

    def __init__(self, kubeconfig: str | None = None, timeout: float = 60.0):
        """Initialize the OCP runner.

        Args:
            kubeconfig: Path to kubeconfig file (uses KUBECONFIG env var if not set)
            timeout: Maximum seconds to wait for command completion
        """
        self.kubeconfig = kubeconfig
        self.timeout = timeout

    async def run(self, subcommand: str, *args: str) -> OcpResult:
        """Run an oc command and return the result.

        Args:
            subcommand: The oc subcommand (e.g., "get", "describe")
            *args: Arguments to pass to the command

        Returns:
            OcpResult with command output and optional parsed JSON

        Raises:
            OcpCommandNotFoundError: If oc CLI isn't installed
            OcpNotLoggedInError: If not logged into a cluster
            OcpCommandError: If the command fails
            ValueError: If the subcommand is not allowed
            asyncio.TimeoutError: If the command exceeds timeout
        """
        # Security check: only allow known subcommands
        if subcommand not in ALLOWED_OC_COMMANDS:
            raise ValueError(
                f"Subcommand '{subcommand}' is not allowed. "
                f"Allowed: {', '.join(sorted(ALLOWED_OC_COMMANDS))}"
            )

        # Check if oc exists
        oc_path = shutil.which("oc")
        if oc_path is None:
            raise OcpCommandNotFoundError()

        # Build command
        cmd_list = self._build_command(oc_path, subcommand, args)

        # Execute
        result = await self._execute(cmd_list)

        # Check for login errors
        self._check_login_error(result)

        # Check for command failure
        if result.return_code != 0:
            raise OcpCommandError(
                " ".join(cmd_list),
                result.return_code,
                result.stderr
            )

        # Parse JSON if requested
        if "-o" in args and "json" in args:
            result.parsed = self._parse_json(result.stdout)

        return result

    async def run_on_node(
        self,
        node: str,
        command: str,
        *args: str,
        timeout: float | None = None,
    ) -> OcpResult:
        """Run a command on a specific node via oc debug.

        Args:
            node: Node name to run command on
            command: Command to run (e.g., "pvs", "vgs")
            *args: Arguments for the command
            timeout: Override default timeout (node debug can be slow)

        Returns:
            OcpResult with command output

        Raises:
            Same as run()
            ValueError: If the command is not in ALLOWED_NODE_LVM_COMMANDS
        """
        # Security check: only allow known LVM commands on nodes
        if command not in ALLOWED_NODE_LVM_COMMANDS:
            raise ValueError(
                f"Command '{command}' is not allowed on nodes. "
                f"Allowed: {', '.join(sorted(ALLOWED_NODE_LVM_COMMANDS))}"
            )

        # Build the chroot command to run on the node
        # oc debug node/X runs in a container, so we use chroot /host
        # to access the actual node filesystem
        node_cmd = f"chroot /host {command} {' '.join(args)}"

        # Use longer timeout for node debug (it can be slow to start)
        old_timeout = self.timeout
        if timeout:
            self.timeout = timeout
        else:
            self.timeout = max(self.timeout, 120.0)

        try:
            result = await self.run(
                "debug",
                f"node/{node}",
                "--",
                "bash", "-c", node_cmd
            )
            return result
        finally:
            self.timeout = old_timeout

    async def create_from_yaml(self, yaml_content: str, namespace: str | None = None) -> OcpResult:
        """Create a resource from YAML content.

        Args:
            yaml_content: YAML definition of the resource
            namespace: Optional namespace to create in

        Returns:
            OcpResult with creation output
        """
        import tempfile

        # Write YAML to temp file
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            f.write(yaml_content)
            yaml_file = f.name

        try:
            args = ["create", "-f", yaml_file]
            if namespace:
                args.extend(["-n", namespace])

            result = await self.run(*args)
            return result
        finally:
            # Clean up temp file
            import os
            os.unlink(yaml_file)

    async def delete_resource(
        self,
        resource_type: str,
        name: str,
        namespace: str | None = None,
    ) -> OcpResult:
        """Delete a resource.

        Args:
            resource_type: Type of resource (pvc, pod, etc.)
            name: Name of the resource
            namespace: Optional namespace

        Returns:
            OcpResult with deletion output
        """
        args = ["delete", resource_type, name]
        if namespace:
            args.extend(["-n", namespace])

        result = await self.run(*args)
        return result

    def _build_command(
        self,
        oc_path: str,
        subcommand: str,
        args: tuple[str, ...]
    ) -> list[str]:
        """Build the command list with optional kubeconfig.

        Args:
            oc_path: Path to oc binary
            subcommand: The oc subcommand
            args: Command arguments

        Returns:
            Command list ready for subprocess
        """
        cmd_list = [oc_path]

        # Add kubeconfig if specified
        kubeconfig = self.kubeconfig or os.environ.get("KUBECONFIG")
        if kubeconfig:
            cmd_list.extend(["--kubeconfig", kubeconfig])

        cmd_list.append(subcommand)
        cmd_list.extend(args)

        return cmd_list

    async def _execute(self, cmd_list: list[str]) -> OcpResult:
        """Execute the command and capture output.

        Args:
            cmd_list: Command and arguments as a list

        Returns:
            OcpResult with captured output
        """
        # Get environment with KUBECONFIG if set
        env = os.environ.copy()
        if self.kubeconfig:
            env["KUBECONFIG"] = self.kubeconfig

        process = await asyncio.create_subprocess_exec(
            *cmd_list,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )

        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            process.communicate(),
            timeout=self.timeout,
        )

        stdout = stdout_bytes.decode("utf-8", errors="replace")
        stderr = stderr_bytes.decode("utf-8", errors="replace")

        return OcpResult(
            command=" ".join(cmd_list),
            return_code=process.returncode or 0,
            stdout=stdout,
            stderr=stderr,
        )

    def _check_login_error(self, result: OcpResult) -> None:
        """Check for cluster login errors.

        Args:
            result: Command result to check

        Raises:
            OcpNotLoggedInError: If not logged in
        """
        stderr_lower = result.stderr.lower()
        login_indicators = [
            "please log in",
            "unauthorized",
            "must be logged in",
            "missing or incomplete configuration",
            "no configuration has been provided",
        ]

        for indicator in login_indicators:
            if indicator in stderr_lower:
                raise OcpNotLoggedInError(result.stderr.strip())

    def _parse_json(self, stdout: str) -> dict | list | None:
        """Parse JSON output from oc commands.

        Args:
            stdout: Command output

        Returns:
            Parsed JSON or None if parsing fails
        """
        try:
            return json.loads(stdout)
        except json.JSONDecodeError:
            return None
