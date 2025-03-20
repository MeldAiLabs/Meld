"""
Centralized logging function for the AI tooling package.
"""

# Global debug configuration
DEBUG_MODE = False


def set_debug_mode(enabled=False):
    """Set the global debug mode flag."""
    global DEBUG_MODE
    DEBUG_MODE = enabled
    log(f"Debug mode {'enabled' if enabled else 'disabled'}")


def log(message: str, level: str = "INFO") -> None:
    """
    Centralized logging function for the AI tooling package.

    Args:
        message: The message to log
        level: Log level (INFO, DEBUG, WARNING, ERROR, CRITICAL)
    """
    if level == "DEBUG" and not DEBUG_MODE:
        return
    print(f"[{level}] {message}")
