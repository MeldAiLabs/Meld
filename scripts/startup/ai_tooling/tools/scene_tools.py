"""
Tools for interacting with Blender scenes and objects.
"""

import threading
import traceback
import bpy
from langchain_core.tools import tool
from ..logging_utils import log


def _run_in_main_thread(func):
    """Decorator to run a function in Blender's main thread."""
    def wrapper(*args, **kwargs):
        result_container = {"output": "[ERROR] Tool did not complete."}
        execution_complete = threading.Event()

        def runner():
            nonlocal result_container
            import bpy  # Import in main thread context
            try:
                result_container["output"] = func(*args, **kwargs)
            except Exception as e:
                error_msg = f"[ERROR] Error during tool execution: {type(e).__name__}: {str(e)}\n{traceback.format_exc()}"
                result_container["output"] = error_msg
                log(f"--- TOOL FAILED: {func.__name__} ---", "ERROR")
                log(error_msg, "DEBUG")
            finally:
                execution_complete.set()
            return None  # Timer runs once

        try:
            import bpy
            if bpy and hasattr(bpy, 'app') and hasattr(bpy.app, 'timers'):
                bpy.app.timers.register(runner, first_interval=0.0)
            else:
                raise RuntimeError("bpy.app.timers not available.")

            timeout_seconds = 30.0
            log(f"Waiting for {func.__name__} completion (max {timeout_seconds}s)...", "DEBUG")
            success = execution_complete.wait(timeout=timeout_seconds)

            if success:
                log(f"Tool {func.__name__} finished.", "DEBUG")
                return result_container["output"]
            else:
                log(f"--- TOOL: {func.__name__} TIMED OUT! ---", "ERROR")
                return f"[ERROR] Tool {func.__name__} timed out after {timeout_seconds} seconds."

        except ImportError:
            err_msg = "[ERROR] Failed to import 'bpy'. This tool must be run within Blender."
            log(err_msg, "CRITICAL")
            return err_msg
        except RuntimeError as rte:
            err_msg = f"[ERROR] Failed to schedule tool execution in Blender: {rte}"
            log(err_msg, "ERROR")
            return err_msg
        except Exception as e:
            error_msg = f"[ERROR] Error setting up or waiting for tool execution: {type(e).__name__}: {str(e)}\n{traceback.format_exc()}"
            log(error_msg, "ERROR")
            return error_msg

    # Preserve original function signature for Langchain/tool usage
    import functools
    functools.update_wrapper(wrapper, func)
    return wrapper


def dump_properties_recursive(obj, indent="    ", max_depth=3, current_depth=0, visited=None):
    """
    Recursively dumps properties of a Blender object (bpy_struct, collection).
    Handles cycles and limits depth.
    """
    # Initialize visited set for the top-level call only
    if visited is None:
        visited = set()

    if current_depth > max_depth:
        return [f"{indent}<Max depth ({max_depth}) reached>"]
    if obj is None:
        return [f"{indent}<None>"]

    # Prevent infinite loops for cyclic references using object ID
    try:
        obj_id = id(obj)
        if obj_id in visited:
            obj_repr = repr(obj)
            if len(obj_repr) > 80:
                obj_repr = obj_repr[:77] + '...'
            return [f"{indent}<Visited cycle: {obj_repr}>"]
        visited.add(obj_id)
    except TypeError:
        # Some objects might not be hashable or have a stable id - skip cycle check for these
        pass

    results = []
    next_indent = indent + "  "

    # --- Handle Collections ---
    if isinstance(obj, (bpy.types.bpy_prop_collection, list, tuple)):
        is_bpy_collection = isinstance(obj, bpy.types.bpy_prop_collection)
        collection_type = "bpy_prop_collection" if is_bpy_collection else type(obj).__name__
        try:
            item_count = len(obj)
            results.append(f"{indent}{collection_type} ({item_count} items):")
            items_to_show = list(obj)[:10]  # Show first 10 items
            for i, item in enumerate(items_to_show):
                item_repr = repr(item)
                if hasattr(item, 'name'):
                    item_repr = f"'{item.name}' ({type(item).__name__})"
                elif len(item_repr) > 80:
                    item_repr = item_repr[:77] + '...'

                results.append(f"{indent}- [{i}] {item_repr}")
                # Recurse into collection items if depth allows
                if current_depth + 1 <= max_depth:
                    results.extend(
                        dump_properties_recursive(
                            item,
                            next_indent,
                            max_depth,
                            current_depth + 1,
                            visited.copy()))
                else:
                    results.append(f"{next_indent}<Max depth reached for item>")
            if item_count > 10:
                results.append(f"{indent}  ... ({item_count - 10} more items not shown)")
        except Exception as coll_e:
            results.append(f"{indent}<Error processing collection: {coll_e}>")

        if not is_bpy_collection:
            if 'obj_id' in locals() and obj_id in visited:
                visited.remove(obj_id)
            return results

    # --- Handle Single bpy_struct Objects (and potentially others) ---
    skip_list = {
        'rna_type', 'bl_rna', '__doc__', '__module__', '__slots__',
        'path_resolve', 'evaluated_get', 'copy', 'driver_add', 'driver_remove',
        'keyframe_delete', 'keyframe_insert', 'is_property_hidden', 'is_property_set',
        'is_property_readonly', 'path_from_id', 'poll', 'internal_data'
    }

    try:
        prop_names = sorted(dir(obj))
    except Exception as e:
        results.append(f"{indent}<Error getting dir(): {e}>")
        if 'obj_id' in locals() and obj_id in visited:
            visited.remove(obj_id)
        return results

    for prop_name in prop_names:
        if prop_name.startswith('_') or prop_name in skip_list:
            continue

        try:
            value = getattr(obj, prop_name)
            if callable(value):
                continue

            value_repr = ""
            should_recurse = False

            if isinstance(value, (bpy.types.bpy_struct, bpy.types.bpy_prop_collection, list, tuple)):
                type_name = type(value).__name__
                item_count_str = f", {len(value)} items" if hasattr(value, '__len__') else ""
                value_repr = f"({type_name}{item_count_str})"
                should_recurse = True
            else:
                try:
                    value_repr = repr(value)
                    if len(value_repr) > 150:
                        value_repr = value_repr[:147] + '...'
                except Exception as repr_e:
                    value_repr = f"<Error representing value: {repr_e}>"

            results.append(f"{indent}{prop_name}: {value_repr}")

            if should_recurse and current_depth + 1 <= max_depth:
                results.extend(
                    dump_properties_recursive(
                        value,
                        next_indent,
                        max_depth,
                        current_depth + 1,
                        visited.copy()))
            elif should_recurse:
                results.append(f"{next_indent}<Max depth reached for property>")

        except AttributeError:
            results.append(f"{indent}{prop_name}: <AttributeError on access>")
        except ReferenceError:
            results.append(f"{indent}{prop_name}: <ReferenceError: Object potentially deleted>")
        except Exception as get_e:
            results.append(f"{indent}{prop_name}: <Error accessing value: {type(get_e).__name__} - {get_e}>")

    if 'obj_id' in locals() and obj_id in visited:
        visited.remove(obj_id)

    return results


@tool
@_run_in_main_thread
def get_scene_info() -> str:
    """
    Get detailed information about the current Blender scene, including objects, settings, and frame range.

    Returns:
        str: A formatted string containing scene information, or an error message.
    """
    log("--- TOOL: get_scene_info called ---", "INFO")
    import bpy
    scene = bpy.context.scene
    if not scene:
        return "[ERROR] Cannot access Blender context or scene."

    results = []
    results.append(f"--- Scene Information: '{scene.name}' ---")
    results.append(f"Current Frame: {scene.frame_current}")
    results.append(
        f"Frame Range: {scene.frame_start} - {scene.frame_end} (Duration: {scene.frame_end - scene.frame_start + 1})")
    fps = scene.render.fps / scene.render.fps_base if scene.render.fps_base != 0 else scene.render.fps
    results.append(f"FPS: {scene.render.fps}/{scene.render.fps_base} ({fps:.3f})")
    results.append(
        f"Resolution: {scene.render.resolution_x}x{scene.render.resolution_y} ({scene.render.resolution_percentage}%)")
    results.append(f"Active Camera: {scene.camera.name if scene.camera else 'None'}")
    results.append(
        f"Active Object: {bpy.context.view_layer.objects.active.name if bpy.context.view_layer.objects.active else 'None'}")
    results.append(f"Selected Objects: {len(bpy.context.selected_objects)}")
    results.append(f"Visible Objects: {len(bpy.context.visible_objects)}")
    results.append(f"Total Objects in Scene: {len(scene.objects)}")

    results.append("\n--- Objects in Scene ---")
    if not scene.objects:
        results.append("No objects in the scene.")
    else:
        # Sort objects by name for consistent output
        sorted_objects = sorted(scene.objects, key=lambda o: o.name)
        for i, obj in enumerate(sorted_objects):
            results.append(f"[{i+1}] '{obj.name}' (Type: {obj.type})")
            # Add brief details like visibility or selection state if desired
            # results.append(f"    Visible: {obj.visible_get()}, Selected: {obj.select_get()}")

    # Add more scene details as needed (e.g., world settings, units)
    results.append("\n--- End of Scene Information ---")
    return "\n".join(results)


@tool
@_run_in_main_thread
def get_object_info(object_name: str, max_depth: int = 2) -> str:
    """
    Get detailed information about a specific object in the Blender scene using recursive property dumping.

    Args:
        object_name (str): The exact name of the object to inspect.
        max_depth (int, optional): How many levels deep to recurse into nested properties.
                                 Defaults to 2. Increase for more detail.

    Returns:
        str: A formatted string with detailed object properties, or an error message.
    """
    log(f"--- TOOL: get_object_info called (object='{object_name}', max_depth={max_depth}) ---", "INFO")
    import bpy
    scene = bpy.context.scene
    if not scene:
        return "[ERROR] Cannot access Blender context or scene."

    obj = bpy.data.objects.get(object_name)
    if obj is None:
        return f"[ERROR] Object '{object_name}' not found in the current scene."

    results = []
    results.append(f"--- Object Information: '{obj.name}' (Type: {obj.type}) ---")

    try:
        # Use dump_properties_recursive starting at depth 0 for the object itself
        obj_properties = dump_properties_recursive(
            obj, indent="  ", max_depth=max_depth, current_depth=0, visited=set()
        )
        results.extend(obj_properties)
    except Exception as e:
        log(f"Error dumping properties for object '{obj.name}': {e}\n{traceback.format_exc()}", "ERROR")
        results.append(f"  <Error dumping properties for this object: {e}>")

    results.append("\n--- End of Object Information ---")
    return "\n".join(results)


__all__ = [
    'get_scene_info',
    'get_object_info'
]
