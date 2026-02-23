"""LVM subprocess wrapper with safety controls."""

import asyncio
import json
import shutil
from dataclasses import dataclass, field

from .errors import (
    LvmCommandError,
    LvmCommandNotFoundError,
    LvmPermissionError,
)


# Only these LVM commands are allowed to run
ALLOWED_COMMANDS = frozenset({
    # Read operations (safe, no changes to system)
    "pvs",        # List physical volumes
    "vgs",        # List volume groups
    "lvs",        # List logical volumes
    "pvdisplay",  # Show physical volume details
    "vgdisplay",  # Show volume group details
    "lvdisplay",  # Show logical volume details
    # Write operations (safe, non-destructive)
    "pvcreate",   # Initialize a disk for LVM
    "vgcreate",   # Create a volume group
    "lvcreate",   # Create a logical volume
    "vgextend",   # Add disk to volume group
    "lvextend",   # Grow a logical volume
})

# Patterns in stderr that indicate permission problems
PERMISSION_PATTERNS = (
    "permission denied",
    "operation not permitted",
    "requires root",
    "must be run as root",
    "insufficient privileges",
    "sudo:",
)


@dataclass
class LvmResult:
    """Container for LVM command results.

    Attributes:
        command: The full command that was run (as a string)
        return_code: Exit code (0 = success, non-zero = failure)
        stdout: Standard output from the command
        stderr: Standard error from the command
        parsed: If JSON output was requested, the parsed Python dict/list
    """
    command: str
    return_code: int
    stdout: str
    stderr: str
    parsed: dict | list | None = field(default=None)


class LvmRunner:
    """Runs LVM commands safely with subprocess.

    This class wraps LVM command execution with:
    - Command allowlist (only safe commands)
    - Optional sudo support
    - Timeout handling
    - JSON output parsing
    - Error detection and classification

    Example:
        runner = LvmRunner(use_sudo=True)
        result = await runner.run("pvs", "--reportformat", "json")
        print(result.parsed)  # {'report': [...]}
    """

    def __init__(self, use_sudo: bool = True, timeout: float = 30.0):
        """Initialize the LVM runner.

        Args:
            use_sudo: If True, prepend 'sudo --non-interactive' to commands.
                     The --non-interactive flag prevents password prompts.
            timeout: Maximum seconds to wait for command completion.
        """
        self.use_sudo = use_sudo
        self.timeout = timeout

    async def run(self, command: str, *args: str) -> LvmResult:
        """Run an LVM command and return the result.

        Args:
            command: The LVM command to run (e.g., "pvs", "lvcreate")
            *args: Arguments to pass to the command

        Returns:
            LvmResult with command output and optional parsed JSON

        Raises:
            LvmCommandNotFoundError: If the command binary isn't installed
            LvmPermissionError: If we lack permission to run the command
            LvmCommandError: If the command exits with non-zero status
            ValueError: If the command is not in the allowlist
            asyncio.TimeoutError: If the command exceeds the timeout
        """
        # Security check: only allow known LVM commands
        if command not in ALLOWED_COMMANDS:
            raise ValueError(
                f"Command '{command}' is not allowed. "
                f"Allowed commands: {', '.join(sorted(ALLOWED_COMMANDS))}"
            )

        # Check if the command exists on the system
        command_path = shutil.which(command)
        if command_path is None:
            raise LvmCommandNotFoundError(command)

        # Build the full command list
        cmd_list = self._build_command(command_path, args)

        # Run the command
        result = await self._execute(cmd_list)

        # Check for permission errors in stderr
        self._check_permission_error(command, result)

        # Check for command failure
        if result.return_code != 0:
            raise LvmCommandError(command, result.return_code, result.stderr)

        # Try to parse JSON if requested
        if "--reportformat" in args and "json" in args:
            result.parsed = self._parse_json(result.stdout)

        return result

    def _build_command(self, command_path: str, args: tuple[str, ...]) -> list[str]:
        """Build the command list, optionally with sudo.

        Args:
            command_path: Full path to the LVM command
            args: Arguments for the command

        Returns:
            List of command parts ready for subprocess
        """
        cmd_list = []

        if self.use_sudo:
            # --non-interactive: fail instead of prompting for password
            cmd_list.extend(["sudo", "--non-interactive", "--"])

        cmd_list.append(command_path)
        cmd_list.extend(args)

        return cmd_list

    async def _execute(self, cmd_list: list[str]) -> LvmResult:
        """Execute the command and capture output.

        Args:
            cmd_list: The command and arguments as a list

        Returns:
            LvmResult with captured output
        """
        # Create subprocess WITHOUT shell=True (security)
        process = await asyncio.create_subprocess_exec(
            *cmd_list,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        # Wait for completion with timeout
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            process.communicate(),
            timeout=self.timeout,
        )

        # Decode output
        stdout = stdout_bytes.decode("utf-8", errors="replace")
        stderr = stderr_bytes.decode("utf-8", errors="replace")

        # Build command string for logging/display
        command_str = " ".join(cmd_list)

        return LvmResult(
            command=command_str,
            return_code=process.returncode or 0,
            stdout=stdout,
            stderr=stderr,
        )

    def _check_permission_error(self, command: str, result: LvmResult) -> None:
        """Check stderr for permission-related error messages.

        Args:
            command: The LVM command that was run
            result: The command result to check

        Raises:
            LvmPermissionError: If permission error patterns are found
        """
        stderr_lower = result.stderr.lower()

        for pattern in PERMISSION_PATTERNS:
            if pattern in stderr_lower:
                raise LvmPermissionError(command, result.stderr.strip())

    def _parse_json(self, stdout: str) -> dict | list | None:
        """Parse JSON output from LVM commands.

        LVM commands with --reportformat json return structured data.
        This method attempts to parse that output.

        Args:
            stdout: The command's standard output

        Returns:
            Parsed JSON as dict/list, or None if parsing fails
        """
        try:
            return json.loads(stdout)
        except json.JSONDecodeError:
            # Return None if output isn't valid JSON
            return None
