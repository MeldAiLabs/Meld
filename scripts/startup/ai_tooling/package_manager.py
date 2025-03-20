"""
Package Manager module for Blender AI tooling.

This module provides functionality for managing Python package dependencies required by the AI tooling system.
It handles package installation, verification, and platform-specific operations to ensure all required
dependencies are available in Blender's Python environment.
"""

import sys
import subprocess
import os
import json

# Track if packages have been checked in this session
_packages_checked = False


def is_windows() -> bool:
    """Check if the current platform is Windows.

    Returns:
        bool: True if running on Windows, False otherwise.
    """
    return sys.platform == 'win32'


def is_macos() -> bool:
    """Check if the current platform is macOS.

    Returns:
        bool: True if running on macOS, False otherwise.
    """
    return sys.platform == 'darwin'


def is_linux() -> bool:
    """Check if the current platform is Linux.

    Returns:
        bool: True if running on Linux, False otherwise.
    """
    return sys.platform == 'linux'


# --- Python Executable ---


def get_python_executable() -> str:
    """Get the path to Blender's Python executable.

    Returns:
        str: Absolute path to Blender's Python executable, or None if not found.
    """
    # sys.executable should point to Blender's Python
    python_exe = sys.executable
    if not python_exe:
        print("ERROR: Could not determine Blender's Python executable path from sys.executable.")
        # Fallback or raise error? For now, print error and return None
        return None
    return os.path.abspath(python_exe)


# --- Module Installation ---


def _add_site_packages_to_path(python_exe: str):
    """Determines the site-packages dir for the given python and adds it to sys.path."""
    try:
        # Try getting user site-packages first, as pip might install there
        # Use json to handle potential path escaping issues
        cmd = [python_exe, "-c", "import site, json; print(json.dumps(site.getusersitepackages()))"]
        result = subprocess.run(cmd, capture_output=True, text=True, check=True, encoding='utf-8')
        # Decode stdout explicitly if it's bytes
        # stdout_text = result.stdout.decode('utf-8') if isinstance(result.stdout, bytes) else result.stdout
        # user_site_packages = json.loads(stdout_text.strip())
        # Need simpler parsing if json.dumps is used
        user_site_packages = result.stdout.strip().strip('"')  # remove potential quotes from json output

        # print(f"DEBUG: User site packages raw output: {result.stdout.strip()}")
        # print(f"DEBUG: User site packages path found: {user_site_packages}")

        if (
            user_site_packages
            and user_site_packages != "None"
            and os.path.isdir(user_site_packages)
            and user_site_packages not in sys.path
        ):
            print(f"Adding user site-packages directory to sys.path: {user_site_packages}")
            sys.path.append(user_site_packages)
            # Need to re-run site.main() for .pth files to be processed? Maybe not required just for imports.
            # import site
            # site.main() # This might have side effects, try without first

        # Also check system site-packages (might be needed if not installing with --user)
        cmd = [python_exe, "-c", "import site, json; print(json.dumps(site.getsitepackages()))"]
        result = subprocess.run(cmd, capture_output=True, text=True, check=True, encoding='utf-8')
        # stdout_text = result.stdout.decode('utf-8') if isinstance(result.stdout, bytes) else result.stdout
        # system_site_packages = json.loads(stdout_text.strip())
        # Parse the list output from json.dumps
        system_site_packages = json.loads(result.stdout.strip())

        # print(f"DEBUG: System site packages raw output: {result.stdout.strip()}")
        # print(f"DEBUG: System site packages paths found: {system_site_packages}")

        for path in system_site_packages:
            if path and os.path.isdir(path) and path not in sys.path:
                print(f"Adding system site-packages directory to sys.path: {path}")
                sys.path.append(path)

    except subprocess.CalledProcessError as e:
        print(f"Warning: Could not determine site-packages directory using '{python_exe}'. Paths might not be updated.")
        print(f"Command failed: {' '.join(e.cmd)}")
        # Avoid printing stderr directly if it might contain sensitive info or be too verbose
        # print(f"Stderr: {e.stderr}")
        print(f"Stderr: {e.stderr.strip() if e.stderr else 'N/A'}")
    except json.JSONDecodeError as e:
        print(f"Warning: Could not parse site-packages output from python command: {e}")
        # print(f"Raw output: {stdout_text}") # Be careful printing raw output
    except Exception as e:
        print(f"Warning: An unexpected error occurred while adding site-packages to path: {e}")


def install_module(package_name: str, version: str = None) -> bool:
    """Install a Python package in Blender's Python environment.

    Attempts to install the specified package using pip. Includes verification
    of the installation through import checking.

    Args:
        package_name (str): Name of the package to install.
        version (str, optional): Specific version to install. Defaults to None.

    Returns:
        bool: True if installation was successful, False otherwise.
    """
    python_exe = get_python_executable()
    if not python_exe:
        print(f"Skipping installation of {package_name} due to missing Python executable.")
        return False

    # Module name for import check might differ from package name (e.g., google-generativeai -> google.generativeai)
    module_name = package_name.replace('-', '_')
    if module_name == "google_generativeai":
        module_name = "google.generativeai"  # Special case for import check
    # Add other special cases if needed

    print(f"Checking installation status for: {package_name} (import check: {module_name})")
    print(f"Using Python executable: {python_exe}")

    try:
        # Check if already installed using importlib
        subprocess.check_call(
            [python_exe, "-m", "pip", "show", package_name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        print(f"Package '{package_name}' seems already installed.")
        _add_site_packages_to_path(python_exe)
        # Optional: Check version compatibility here if needed
        return True
    except subprocess.CalledProcessError:
        print(f"Package '{package_name}' not found. Attempting installation...")
    except FileNotFoundError:
        print(f"Warning: 'pip' command not found using '{python_exe}'. Cannot install {package_name}.")
        return False  # Cannot proceed without pip

    try:
        # Ensure pip is available
        subprocess.check_call(
            [python_exe, "-m", "ensurepip", "--upgrade"], stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )  # Use PIPE to hide output unless error

        # Upgrade pip
        subprocess.check_call(
            [python_exe, "-m", "pip", "install", "--upgrade", "pip"], stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )

        # Install the requested package
        # For version ranges or complex specifications, don't add == prefix
        if version and ('>=' in version or '<' in version or ',' in version):
            package_spec = f"{package_name}{version}"
        else:
            package_spec = f"{package_name}{f'=={version}' if version else ''}"
        print(f"Running: {python_exe} -m pip install {package_spec}")
        install_command = [python_exe, "-m", "pip", "install", package_spec]
        # Add '--user' if running into permission issues, but try without first
        # install_command.insert(3, "--user")
        subprocess.check_call(install_command)

        # Verify installation via import
        print(f"Verifying installation by trying to import '{module_name}'...")
        try:
            subprocess.check_call(
                [python_exe, "-c", f"import {module_name}"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
            print(f"Successfully installed and verified {package_name}")
            _add_site_packages_to_path(python_exe)
            return True
        except subprocess.CalledProcessError as import_err:
            print(
                f"ERROR: Installed {package_name}, but failed to import '{module_name}'. Check package integrity or dependencies."
            )
            print(f"Import check error: {import_err}")
            return False  # Installation might be incomplete or broken

    except subprocess.CalledProcessError as e:
        print(f"Failed to install or verify {package_name}.")
        print(f"Command failed: {' '.join(e.cmd)}")
        # Consider printing stdout/stderr from the exception if available and helpful
        # print(f"Output: {e.output}")
        # print(f"Error Output: {e.stderr}")
        return False
    except Exception as e:
        print(f"An unexpected error occurred during installation of {package_name}: {str(e)}")
        return False


def ensure_packages():
    """Checks and installs all required Python packages."""
    global _packages_checked

    # Skip if already checked in this session
    if _packages_checked:
        print("Packages already checked in this session, skipping...")
        return True

    # Updated list with specific LangChain components
    required_packages = {
        "requests": None,
        "pydantic": ">=2.0.0,<3.0.0",  # Require Pydantic V2
        "langchain-core": None,
    }

    print("--- Starting Package Installation Check ---")
    all_successful = True
    for package, version in required_packages.items():
        if not install_module(package, version):
            print(f"WARNING: Failed to install or verify package: {package}")
            all_successful = False
            # Depending on criticality, you might want to stop here
            # break

    if all_successful:
        print("--- All required packages are installed or verified. ---")
    else:
        print("--- Some packages failed to install. AI features might be limited or non-functional. ---")

    # Mark as checked to avoid redundant checks
    _packages_checked = True
    return all_successful
