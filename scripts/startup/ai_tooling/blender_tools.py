"""
Collection of Langchain tools specifically designed for interacting with Blender.
These tools can be bound to an LLM to allow it to perform actions within Blender.
"""

# Note: This file is now primarily for aggregation or legacy/uncategorized tools.
# Specific tools are organized into submodules within the 'tools' directory.

# Example: To import all tools from the new modules:
# from .tools.main_tools import *
# from .tools.sequencer_tools import *
# from .tools.scene_tools import *

# Or, manage the list explicitly:
from .tools.main_tools import execute_blender_code
from .tools.sequencer_tools import get_full_sequencer_data, inspect_sequencer_at_frame
from .tools.scene_tools import get_scene_info, get_object_info
from .tools.polyhaven_tools import get_polyhaven_categories, search_polyhaven_assets, download_polyhaven_asset, set_polyhaven_texture


# Export the tools that should be available to llm_manager.py
available_tools = [
    execute_blender_code,
    get_scene_info,
    get_object_info,
    get_full_sequencer_data,
    inspect_sequencer_at_frame,
    get_polyhaven_categories,
    search_polyhaven_assets,
    download_polyhaven_asset,
    set_polyhaven_texture,
    # Add other tools imported here or defined below
]

__all__ = [
    # List individual tool names for export if needed, or just 'available_tools'
    'get_scene_info',
    'get_object_info',
    'get_full_sequencer_data',
    'inspect_sequencer_at_frame',
    'get_polyhaven_categories',
    'search_polyhaven_assets',
    'download_polyhaven_asset',
    'set_polyhaven_texture',
    'available_tools'
]

# Any remaining tools or functions specific to this top-level file can stay here.
# For example, if there were complex setup functions or tools that don't fit
# into the other categories.
