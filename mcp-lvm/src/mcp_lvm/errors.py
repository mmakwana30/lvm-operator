"""Custom exceptions for LVM operations."""


class LvmError(Exception):
    """Base exception for all LVM-related errors.

    This is the parent class - you can catch this to handle
    any LVM error, or catch specific subclasses for finer control.
    """
    pass


class LvmCommandNotFoundError(LvmError):
    """Raised when an LVM binary (pvs, vgs, etc.) is not found.

    This usually means LVM2 tools are not installed on the system.
    On Debian/Ubuntu: apt install lvm2
    On RHEL/Fedora: dnf install lvm2
    """

    def __init__(self, command: str):
        self.command = command
        super().__init__(f"LVM command not found: {command}")


class LvmPermissionError(LvmError):
    """Raised when we lack permission to run LVM commands.

    Most LVM operations require root/sudo access.
    This is detected by checking stderr for permission-related messages.
    """

    def __init__(self, command: str, message: str):
        self.command = command
        self.message = message
        super().__init__(f"Permission denied running '{command}': {message}")


class LvmCommandError(LvmError):
    """Raised when an LVM command exits with a non-zero status.

    This covers general failures like:
    - Device doesn't exist
    - Volume group not found
    - Invalid size specification
    - etc.
    """

    def __init__(self, command: str, return_code: int, stderr: str):
        self.command = command
        self.return_code = return_code
        self.stderr = stderr
        super().__init__(
            f"LVM command '{command}' failed with code {return_code}: {stderr}"
        )
