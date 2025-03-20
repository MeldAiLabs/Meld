"""
Blender AI Copilot Addon Package.

This package provides AI-powered assistance within Blender through a chat interface.
It includes modules for package management, LLM integration, and UI components.
The addon adds a sidebar panel for interacting with AI models and managing configurations.
"""

# Standard library imports
import importlib
import bpy

from .logging_utils import log, DEBUG_MODE, set_debug_mode


# Use relative imports within the addon package
try:
    from . import package_manager
    # init package manager first to prevent issues
    package_manager.ensure_packages()
    from . import space_sidebar
except ImportError as e:
    # If imports fail here, the addon cannot load. Provide clear error.
    log(f"CRITICAL IMPORT ERROR in AI Copilot __init__.py: {e}", "ERROR")
    raise e


# Keep track of registered properties to safely delete them
_scene_props_registered = [
    "ai_api_key",
    "chat_input",
    "chat_messages",
    "chat_message_index",
    "ai_active_thread",
    "ai_model_selected",  # Use a StringProperty
]
_wm_props_registered = [
    "is_sending_message",
    "chat_status_message",
]


def register():
    """Register the addon and all its components.

    This function handles:
    - Package dependency installation
    - Property registration
    - UI class registration
    - Keymap registration
    """

    # Ensure submodules are available after potential reload
    if 'package_manager' not in globals() or 'space_sidebar' not in globals():
        log("ERROR: Submodules not loaded correctly during registration. Aborting.", "ERROR")
        return

    # --- Register Properties ---
    # Scene properties
    try:
        log("Registering Scene properties...", "DEBUG")

        # Use a StringProperty to store the selected model ID
        bpy.types.Scene.ai_model_selected = bpy.props.StringProperty(
            name="Selected AI Model",
            description="Identifier of the currently selected AI model",
            default="default"  # Start with a default value
        )

        bpy.types.Scene.ai_api_key = bpy.props.StringProperty(
            name="API Key", description="API key for selected model", subtype='PASSWORD'
        )
        bpy.types.Scene.chat_input = bpy.props.StringProperty(
            name="Chat Input", description="Type message", default=""
        )
        bpy.types.Scene.checkout_url_display = bpy.props.StringProperty(
            name="Checkout URL",
            description="Displays the generated Stripe Checkout URL",
            default=""
        )

        # Add EnumProperty for thread selection (using callbacks from space_sidebar)
        # Ensure space_sidebar is imported and callbacks are accessible
        if hasattr(space_sidebar, 'get_available_threads') and hasattr(space_sidebar, 'update_active_thread'):
            log("Registering EnumProperty: ai_active_thread", "DEBUG")
            bpy.types.Scene.ai_active_thread = bpy.props.EnumProperty(
                name="Active Thread",
                description="Select the conversation thread to continue or view",
                items=space_sidebar.get_available_threads,
                update=space_sidebar.update_active_thread
            )
        else:
            log("ERROR: space_sidebar callbacks for ai_active_thread not found! Cannot register.", "ERROR")
            if "ai_active_thread" not in _scene_props_registered:
                _scene_props_registered.append("ai_active_thread")  # Still track for potential del

        # *** CRITICAL ORDER: Register PropertyGroup class BEFORE CollectionProperty ***
        # Check if CHAT_UL_message_item exists before trying to register/use it
        if hasattr(space_sidebar, 'CHAT_UL_message_item'):
            PropertyGroupClass = space_sidebar.CHAT_UL_message_item
            if not PropertyGroupClass.is_registered:
                log(f"Registering PropertyGroup: {PropertyGroupClass.__name__}", "DEBUG")
                bpy.utils.register_class(PropertyGroupClass)
            else:
                log(f"PropertyGroup {PropertyGroupClass.__name__} already registered. Skipping.", "DEBUG")

            # Now register the CollectionProperty using the registered type
            log("Registering CollectionProperty: chat_messages", "DEBUG")
            bpy.types.Scene.chat_messages = bpy.props.CollectionProperty(
                type=PropertyGroupClass,
            )
        else:
            log("ERROR: space_sidebar.CHAT_UL_message_item not found! Cannot register chat_messages.", "ERROR")
            # Add a placeholder to avoid errors trying to delete it later if needed
            if "chat_messages" not in _scene_props_registered:
                _scene_props_registered.append("chat_messages")

        bpy.types.Scene.chat_message_index = bpy.props.IntProperty(
            name="Active Message Index", description="Selected message index"
        )
        log("Scene properties registered.", "DEBUG")
    except Exception as e:
        log(f"ERROR registering Scene properties: {e}", "ERROR")
        # If props fail, panel poll might fail, preventing draw.

    # Window Manager properties (for status)
    try:
        log("Registering WindowManager properties...", "DEBUG")
        if not hasattr(bpy.types.WindowManager, 'is_sending_message'):
            bpy.types.WindowManager.is_sending_message = bpy.props.BoolProperty(default=False)
        if not hasattr(bpy.types.WindowManager, 'chat_status_message'):
            bpy.types.WindowManager.chat_status_message = bpy.props.StringProperty(default="")
        log("WindowManager properties registered.", "DEBUG")
    except Exception as e:
        log(f"ERROR registering WindowManager properties: {e}", "ERROR")

    # --- Register UI Classes ---
    # Access the classes tuple from the imported space_sidebar module
    if hasattr(space_sidebar, 'classes'):
        log(f"Registering UI classes from space_sidebar ({len(space_sidebar.classes)} items)...", "DEBUG")
        # Filter out the PropertyGroup if we already registered it manually above
        classes_to_register = [cls for cls in space_sidebar.classes if cls != space_sidebar.CHAT_UL_message_item]

        for cls in classes_to_register:
            if not cls.is_registered:
                log(f"  Registering: {cls.__name__}", "DEBUG")
                bpy.utils.register_class(cls)
            else:
                log(f"  Skipping registration (already registered): {cls.__name__}", "DEBUG")
    else:
        log("ERROR: space_sidebar.classes not found!", "ERROR")

    # --- Register Keymap ---
    # Call the keymap registration function from space_sidebar
    if hasattr(space_sidebar, "register_keymap"):
        try:
            log("Calling space_sidebar.register_keymap()...", "DEBUG")
            space_sidebar.register_keymap()
        except Exception as e:
            log(f"ERROR calling space_sidebar.register_keymap(): {e}", "ERROR")


def unregister():
    """Unregister the addon and clean up all its components.

    This function handles:
    - Keymap unregistration
    - UI class unregistration
    - Property cleanup
    """

    # Ensure submodules are available
    if 'space_sidebar' not in globals():
        log("ERROR: space_sidebar module not loaded during unregistration.", "ERROR")
        # Attempt to cleanup properties anyway
    else:
        # --- Unregister Keymap FIRST ---
        if hasattr(space_sidebar, "unregister_keymap"):
            try:
                log("Calling space_sidebar.unregister_keymap()...", "DEBUG")
                space_sidebar.unregister_keymap()
            except Exception as e:
                log(f"ERROR calling space_sidebar.unregister_keymap(): {e}", "ERROR")

        # --- Unregister UI Classes SECOND ---
        # Unregister in reverse order
        if hasattr(space_sidebar, 'classes'):
            log(f"Unregistering UI classes from space_sidebar ({len(space_sidebar.classes)} items)...", "DEBUG")
            for cls in reversed(space_sidebar.classes):
                try:
                    # Check if it was actually registered before trying to unregister
                    if cls.is_registered:
                        log(f"  Unregistering: {cls.__name__}", "DEBUG")
                        bpy.utils.unregister_class(cls)
                    else:
                        log(f"  Skipping unregister (not registered): {cls.__name__}", "DEBUG")
                except Exception as e:
                    # Log error but continue trying to unregister others
                    log(f"  ERROR unregistering {cls.__name__}: {e}", "ERROR")
        else:
            log("ERROR: space_sidebar.classes not found during unregistration!", "ERROR")

    # --- Delete Properties LAST ---
    # Delete Window Manager properties
    log("Deleting WindowManager properties...", "DEBUG")
    for prop_name in _wm_props_registered:
        if hasattr(bpy.types.WindowManager, prop_name):
            try:
                log(f"  Deleting WM.{prop_name}", "DEBUG")
                delattr(bpy.types.WindowManager, prop_name)
            except Exception as e:
                log(f"  ERROR deleting WM.{prop_name}: {e}", "ERROR")

    # Delete Scene properties
    log("Deleting Scene properties...", "DEBUG")
    for prop_name in _scene_props_registered:
        if hasattr(bpy.types.Scene, prop_name):
            try:
                log(f"  Deleting Scene.{prop_name}", "DEBUG")
                delattr(bpy.types.Scene, prop_name)
            except Exception as e:
                log(f"  ERROR deleting Scene.{prop_name}: {e}", "ERROR")


# Standard Blender entry point check (keep for testing registration directly)
# if __name__ == "__main__":
#     try: unregister()
#     except Exception as e: print(f"Pre-unregistration error: {e}")
#     register()
