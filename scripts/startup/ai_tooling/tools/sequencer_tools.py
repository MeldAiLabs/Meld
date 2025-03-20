"""
Tools for interacting with the Blender Video Sequence Editor (VSE).
"""

import os
import threading
import traceback
import bpy
from langchain_core.tools import tool
from ..logging_utils import log


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
    if isinstance(obj, (bpy.types.bpy_prop_collection, list, tuple)):  # Check standard lists/tuples too
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
                # Recurse into collection items
                # Pass a *copy* of visited set down to avoid false cycle detection across siblings
                results.extend(
                    dump_properties_recursive(
                        item,
                        next_indent,
                        max_depth,
                        current_depth + 1,
                        visited.copy()))
            if item_count > 10:
                results.append(f"{indent}  ... ({item_count - 10} more items not shown)")
        except Exception as coll_e:
            results.append(f"{indent}<Error processing collection: {coll_e}>")

        # If it's specifically a bpy_prop_collection, it might *also* have properties
        if not is_bpy_collection:
            # Return early for standard lists/tuples
            # Remove from visited *before* returning from this branch
            if 'obj_id' in locals() and obj_id in visited:
                visited.remove(obj_id)
            return results

    # --- Handle Single bpy_struct Objects (and potentially others) ---
    # Define properties/methods to skip during introspection
    skip_list = {
        'rna_type', 'bl_rna', '__doc__', '__module__', '__slots__',
        'path_resolve', 'evaluated_get', 'copy', 'driver_add', 'driver_remove',
        'keyframe_delete', 'keyframe_insert', 'is_property_hidden', 'is_property_set',
        'is_property_readonly', 'path_from_id', 'poll', 'internal_data'  # Add internal_data
    }

    # Get properties, handling potential errors if dir() fails
    try:
        prop_names = sorted(dir(obj))
    except Exception as e:
        results.append(f"{indent}<Error getting dir(): {e}>")
        if 'obj_id' in locals() and obj_id in visited:
            visited.remove(obj_id)
        return results  # Cannot proceed if dir() fails

    for prop_name in prop_names:
        if prop_name.startswith('_') or prop_name in skip_list:
            continue

        try:
            value = getattr(obj, prop_name)

            # Skip methods/callables
            if callable(value):
                continue

            value_repr = ""
            should_recurse = False

            # Determine if recursion is needed based on type
            if isinstance(value, bpy.types.bpy_struct):
                value_repr = f"({type(value).__name__})"
                should_recurse = True
            elif isinstance(value, bpy.types.bpy_prop_collection):
                value_repr = f"(Collection, {len(value)} items)"
                should_recurse = True
            else:
                # For simple types or types we don't recurse into, just show repr
                try:
                    value_repr = repr(value)
                    # Limit length of representation for readability
                    if len(value_repr) > 150:
                        value_repr = value_repr[:147] + '...'
                except Exception as repr_e:
                    value_repr = f"<Error representing value: {repr_e}>"

            results.append(f"{indent}{prop_name}: {value_repr}")

            # Perform recursion if needed
            if should_recurse:
                # Pass a copy of the visited set for this branch
                results.extend(
                    dump_properties_recursive(
                        value,
                        next_indent,
                        max_depth,
                        current_depth + 1,
                        visited.copy()))

        except AttributeError:
            results.append(f"{indent}{prop_name}: <AttributeError on access>")
        except ReferenceError:
            results.append(f"{indent}{prop_name}: <ReferenceError: Object potentially deleted>")
        except Exception as get_e:
            results.append(f"{indent}{prop_name}: <Error accessing value: {type(get_e).__name__} - {get_e}>")

    # Remove self from visited *after* processing all properties
    if 'obj_id' in locals() and obj_id in visited:
        visited.remove(obj_id)

    return results


def _get_full_sequencer_data_recursive_impl(max_depth=3) -> str:
    """Internal implementation for recursive VSE data fetch."""
    # This function runs in the main thread, called by the tool wrapper
    import bpy  # Import bpy here
    scene = bpy.context.scene
    seq_editor = scene.sequence_editor
    result = []

    # --- General Scene/Render Settings ---
    result.append(f"--- Sequencer Information for Scene: '{scene.name}' ---")
    result.append(f"Inspecting at Frame: {scene.frame_current}")
    result.append(
        f"Frame Range: {scene.frame_start} - {scene.frame_end} (Duration: {scene.frame_end - scene.frame_start + 1} frames)")
    fps = scene.render.fps / scene.render.fps_base if scene.render.fps_base != 0 else scene.render.fps
    result.append(f"FPS: {scene.render.fps}/{scene.render.fps_base} ({fps:.3f})")
    result.append(
        f"Resolution: {scene.render.resolution_x}x{scene.render.resolution_y} ({scene.render.resolution_percentage}%)")
    try:
        render_path = bpy.path.abspath(scene.render.filepath)
        result.append(f"Render Output Path: {render_path}")
    except Exception:
        result.append(f"Render Output Path: {scene.render.filepath} (Could not resolve absolute path)")
    result.append(f"Sequencer Output Enabled in Render: {scene.render.use_sequencer}")
    result.append("-" * 40)

    # --- Strip Information (Recursive) ---
    sequences = seq_editor.sequences if seq_editor else []
    sequences_all = seq_editor.sequences_all if seq_editor else []

    if not sequences:
        result.append("No top-level strips found in the sequencer.")
    else:
        result.append(f"Total Strips (including nested): {len(sequences_all)}")
        result.append(f"Top-Level Strips: {len(sequences)}")
        result.append("-" * 40)

        sorted_strips = sorted(sequences, key=lambda s: (s.frame_final_start, s.channel))

        for i, strip in enumerate(sorted_strips):
            result.append(f"\n[{i+1}] Strip: '{strip.name}' (Type: {strip.type})")
            result.append("    --- Recursive Properties ---")
            try:
                # Start recursion for each strip
                strip_properties = dump_properties_recursive(
                    strip, indent="    ", max_depth=max_depth, current_depth=1, visited=set())
                result.extend(strip_properties)
            except Exception as e:
                log(f"Error dumping properties for strip '{strip.name}': {e}\n{traceback.format_exc()}", "ERROR")
                result.append(f"    <Error dumping properties for this strip: {e}>")

            result.append("-" * 20)  # Separator between strips

    result.append("\n--- End of Sequencer Information ---")
    return "\n".join(result)


@tool
def get_full_sequencer_data() -> str:
    """
    Get comprehensive information about the current state of the Blender Video Sequence Editor (VSE).
    Returns detailed data about all strips (clips), channels, scene settings, render settings relevant to the sequencer.
    Use this to understand the current VSE setup before making modifications via the 'execute_blender_code' tool.
    Returns:
        str: A formatted string containing detailed information about the VSE, or an error message starting with '[ERROR]'.
    """
    log("--- TOOL: get_full_sequencer_data called ---", "INFO")
    result = []
    try:
        # !!! IMPORTANT: bpy must be imported *inside* this function
        # because this function runs in Blender's main thread context.
        import bpy

        # Get the current scene
        scene = bpy.context.scene
        if not scene:
            return "[ERROR] Cannot access Blender context or scene."

        # Ensure sequence editor exists
        if not scene.sequence_editor:
            try:
                scene.sequence_editor_create()
                log("Created new Sequence Editor for the scene.", "DEBUG")
                # Refresh reference after creation
                seq_editor = scene.sequence_editor
                if not seq_editor:  # Check again
                    return "[ERROR] Failed to create or access Sequence Editor."
            except Exception as e:
                return f"[ERROR] No sequence editor exists and couldn't create one: {str(e)}"
        else:
            seq_editor = scene.sequence_editor

        sequences = seq_editor.sequences
        sequences_all = seq_editor.sequences_all  # Includes nested strips

        # --- General Scene/Render Settings relevant to VSE ---
        result.append(f"--- Sequencer Information for Scene: '{scene.name}' ---")
        result.append(f"Current Frame: {scene.frame_current}")
        result.append(
            f"Frame Range: {scene.frame_start} - {scene.frame_end} (Duration: {scene.frame_end - scene.frame_start + 1} frames)")
        fps = scene.render.fps / scene.render.fps_base if scene.render.fps_base != 0 else scene.render.fps
        result.append(f"FPS: {scene.render.fps}/{scene.render.fps_base} ({fps:.3f})")
        result.append(
            f"Resolution: {scene.render.resolution_x}x{scene.render.resolution_y} ({scene.render.resolution_percentage}%)")
        try:
            # Use abspath to get full path if it's relative
            render_path = bpy.path.abspath(scene.render.filepath)
            result.append(f"Render Output Path: {render_path}")
        except Exception:
            result.append(f"Render Output Path: {scene.render.filepath} (Could not resolve absolute path)")
        result.append(f"Sequencer Output Enabled in Render: {scene.render.use_sequencer}")
        result.append("-" * 40)

        # --- Strip Information ---
        if not sequences:
            result.append("No top-level strips found in the sequencer.")
        else:
            result.append(f"Total Strips (including nested): {len(sequences_all)}")
            result.append(f"Top-Level Strips: {len(sequences)}")
            result.append("-" * 40)

            # Sort strips primarily by frame_start, secondarily by channel
            sorted_strips = sorted(sequences, key=lambda s: (s.frame_final_start, s.channel))  # Sort by final_start

            for i, strip in enumerate(sorted_strips):
                result.append(f"\n[{i+1}] Strip: '{strip.name}' (Type: {strip.type})")
                result.append(f"    Channel: {strip.channel}")
                # Use final frames which account for effects, speed, etc.
                result.append(f"    Frame Start (Final): {strip.frame_final_start}")
                result.append(f"    Frame End (Final, Exclusive): {strip.frame_final_end}")
                result.append(f"    Duration (Final): {strip.frame_final_duration}")
                result.append(f"    Muted: {strip.mute}")
                result.append(f"    Locked: {strip.lock}")
                result.append(f"    Selected: {strip.select}")
                result.append(f"    Blend Mode: {strip.blend_type}")
                result.append(f"    Opacity: {strip.blend_alpha:.3f}")

                # --- Type-Specific Details ---
                try:
                    if strip.type in ['IMAGE', 'MOVIE', 'MOVIECLIP']:  # Movie Clip added
                        if strip.type == 'MOVIECLIP':
                            result.append(f"    Clip: '{strip.clip.name}'" if strip.clip else "Clip: None")
                            if strip.clip and strip.clip.source == 'MOVIE':
                                try:
                                    clip_path = bpy.path.abspath(strip.clip.filepath)
                                    result.append(f"    Source Path: {clip_path}")
                                except Exception:
                                    result.append(
                                        f"    Source Path: {strip.clip.filepath} (Could not resolve absolute path)")
                        else:  # IMAGE or MOVIE
                            if hasattr(strip, 'filepath') and strip.filepath:
                                try:
                                    strip_path = bpy.path.abspath(strip.filepath)
                                    result.append(f"    File Path: {strip_path}")
                                    result.append(f"    File Name: {os.path.basename(strip_path)}")
                                except Exception:
                                    result.append(f"    File Path: {strip.filepath} (Could not resolve absolute path)")
                                    result.append(f"    File Name: {os.path.basename(strip.filepath)}")
                            else:
                                result.append("    File Path: Not Set")

                        if strip.type == 'IMAGE' and hasattr(strip, 'directory') and strip.directory:
                            try:
                                dir_path = bpy.path.abspath(strip.directory)
                                result.append(f"    Directory: {dir_path}")
                            except Exception:
                                result.append(f"    Directory: {strip.directory} (Could not resolve absolute path)")
                            if hasattr(strip, 'elements') and strip.elements:
                                result.append(
                                    f"    Image Files: {len(strip.elements)} (e.g., {strip.elements[0].filename})")

                    elif strip.type == 'SOUND':
                        if strip.sound:
                            if strip.sound.filepath:
                                try:
                                    sound_path = bpy.path.abspath(strip.sound.filepath)
                                    result.append(f"    Sound File Path: {sound_path}")
                                    result.append(f"    Sound File Name: {os.path.basename(sound_path)}")
                                except Exception:
                                    result.append(
                                        f"    Sound File Path: {strip.sound.filepath} (Could not resolve absolute path)")
                                    result.append(f"    Sound File Name: {os.path.basename(strip.sound.filepath)}")

                            else:
                                result.append("    Sound File Path: Not Set")
                            result.append(f"    Volume: {strip.volume:.2f}")
                            result.append(f"    Pan (L/R): {strip.pan:.2f}")
                            result.append(f"    Show Waveform: {strip.show_waveform}")
                        else:
                            result.append("    Sound Data: None / Missing")

                    elif strip.type == 'SCENE':
                        result.append(f"    Linked Scene: '{strip.scene.name}'" if strip.scene else "None (Missing?)")
                        result.append(
                            f"    Override Camera: '{strip.scene_camera.name}'" if strip.scene_camera else "None (Uses Linked Scene's Active Camera)")
                        # Does it use the linked scene's sequencer?
                        result.append(f"    Use Sequence: {strip.use_sequence}")

                    elif strip.type == 'TEXT':
                        result.append(f"    Text Content: '{strip.text}'")
                        result.append(f"    Font Size: {strip.font_size}")
                        result.append(f"    Location (X, Y): ({strip.location[0]:.2f}, {strip.location[1]:.2f})")
                        result.append(f"    Wrap Width (0=off): {strip.wrap_width:.1f}")
                        result.append(f"    Alignment (X,Y): {strip.align_x}, {strip.align_y}")
                        if strip.font:
                            result.append(f"    Font: '{strip.font.name}'")

                    elif strip.type == 'COLOR':
                        col = strip.color
                        # Opacity is separate
                        result.append(f"    Color (RGB): R={col[0]:.3f}, G={col[1]:.3f}, B={col[2]:.3f}")

                    elif strip.type == 'META':
                        result.append(f"    Contains {len(strip.sequences)} nested strips.")
                        # Optionally list nested strips (can get long)
                        # for j, nested in enumerate(sorted(strip.sequences, key=lambda s: (s.frame_final_start, s.channel))):
                        #    result.append(f"        - Nested [{j+1}]: '{nested.name}' (Type: {nested.type}, Ch: {nested.channel})")

                    elif strip.type.startswith('TRANSITION'):  # Covers WIPE, CROSS, etc.
                        result.append(f"    Transition Type: {strip.transition_type}")
                        result.append(
                            f"    Input 1: '{strip.input_1.name}' (Ch {strip.input_1.channel})" if strip.input_1 else "Input 1: None")
                        result.append(
                            f"    Input 2: '{strip.input_2.name}' (Ch {strip.input_2.channel})" if
                            strip.input_2 else "Input 2: None")

                    elif strip.type == 'EFFECT':
                        # More specific effect types often subclass this or have their own type name
                        # Try to find specific properties for common effects
                        effect_name = type(strip).__name__  # e.g., 'SpeedControlSequence'
                        result.append(f"    Effect Type Name: {effect_name}")
                        if hasattr(strip, 'input_count'):
                            for k in range(strip.input_count):
                                input_strip = getattr(strip, f"input_{k+1}", None)
                                result.append(
                                    f"    Input {k+1}: '{input_strip.name}' (Ch {input_strip.channel})" if
                                    input_strip else f"Input {k+1}: None")

                        # Add specific common effect properties
                        if strip.bl_rna.identifier == 'SpeedControlSequence':  # Check internal type identifier
                            result.append(f"    Speed Factor: {strip.speed_factor:.3f}")
                            result.append(f"    Multiply Speed: {strip.multiply_speed}")
                        elif strip.bl_rna.identifier == 'ColorBalanceSequence':
                            result.append(f"    Correction Method: {strip.correction_method}")
                            result.append(
                                f"    Lift (RGB): ({strip.lift.r:.3f}, {strip.lift.g:.3f}, {strip.lift.b:.3f})")
                            result.append(
                                f"    Gamma (RGB): ({strip.gamma.r:.3f}, {strip.gamma.g:.3f}, {strip.gamma.b:.3f})")
                            result.append(
                                f"    Gain (RGB): ({strip.gain.r:.3f}, {strip.gain.g:.3f}, {strip.gain.b:.3f})")
                        elif strip.bl_rna.identifier == 'GaussianBlurSequence':
                            result.append(f"    Size X: {strip.size_x:.1f}")
                            result.append(f"    Size Y: {strip.size_y:.1f}")
                        # Add more specific effect checks as needed...

                    elif strip.type == 'ADJUSTMENT':  # Adjustment Layer
                        result.append("    (Adjustment Layer: Applies modifiers/effects to strips below)")

                    # Modifiers (Applicable to most strip types)
                    if strip.modifiers:
                        result.append(f"    Modifiers ({len(strip.modifiers)}):")
                        for mod in strip.modifiers:
                            mod_type = getattr(mod, 'type', 'UNKNOWN')  # Handle potential lack of type attr
                            result.append(f"        - Name: '{mod.name}', Type: {mod_type}, Muted: {mod.mute}")
                            # Add modifier-specific details if important (e.g., mask input)
                            if hasattr(mod, 'mask_input_type') and mod.mask_input_type != 'NONE':
                                mask_info = f"Mask Type: {mod.mask_input_type}"
                                if mod.mask_input_type == 'STRIP' and mod.mask_strip:
                                    mask_info += f" (Strip: '{mod.mask_strip.name}')"
                                elif mod.mask_input_type == 'ID' and mod.mask_id:
                                    mask_info += f" (Mask ID: '{mod.mask_id.name}')"
                                result.append(f"          {mask_info}")

                except AttributeError as ae:
                    log(f"Attribute Error accessing details for strip '{strip.name}': {ae}", "WARNING")
                    result.append(f"    Attribute Error accessing details: {ae}")
                except Exception as detail_e:
                    log(f"Error getting details for strip '{strip.name}': {detail_e}", "ERROR")
                    result.append(f"    Error getting details for this strip: {detail_e}")

                result.append("-" * 20)  # Separator between strips

        result.append("\n--- End of Sequencer Information ---")
        return "\n".join(result)

    except ImportError:
        # This occurs if get_full_sequencer_data is called outside Blender context
        err_msg = "[ERROR] Failed to import 'bpy'. This tool must be run within Blender."
        log(err_msg, "CRITICAL")
        return err_msg
    except Exception as e:
        error_msg = f"[ERROR] Unexpected error getting sequencer data: {str(e)}\n{traceback.format_exc()}"
        log(error_msg, "ERROR")
        return error_msg


@tool
def inspect_sequencer_at_frame(target_frame: int, max_depth: int = 3) -> str:
    """
    Inspects the Blender Video Sequence Editor (VSE) state at a specific frame.
    Temporarily sets the scene's current frame, gathers detailed, recursive information
    about all sequencer strips and their properties at that frame, then restores the original frame.
    Use this to verify the results of an action or understand the VSE state at a crucial point in time.

    Args:
        target_frame (int): The frame number to inspect the VSE state at.
        max_depth (int, optional): How many levels deep to recurse into nested objects/properties
                         during inspection. Defaults to 3. Higher values give more detail but take longer
                         and produce more output.

    Returns:
        str: A formatted string containing detailed VSE information at the target frame,
             or an error message starting with '[ERROR]'.
    """
    log(f"--- TOOL: inspect_sequencer_at_frame called (target_frame={target_frame}, max_depth={max_depth}) ---", "INFO")

    result_container = {"output": "[ERROR] Tool did not complete."}
    execution_complete = threading.Event()
    original_frame = -1

    def inspection_runner():
        nonlocal result_container, original_frame
        import bpy  # Needs to be imported in the main thread context

        try:
            scene = bpy.context.scene
            if not scene:
                result_container["output"] = "[ERROR] Cannot access Blender context or scene."
                return None  # Stop timer

            original_frame = scene.frame_current

            # Ensure sequence editor exists
            if not scene.sequence_editor:
                try:
                    scene.sequence_editor_create()
                    log("Created new Sequence Editor for the scene.", "DEBUG")
                    if not scene.sequence_editor:  # Check again
                        result_container["output"] = "[ERROR] Failed to create or access Sequence Editor."
                        return None
                except Exception as e:
                    result_container["output"] = f"[ERROR] No sequence editor exists and couldn't create one: {str(e)}"
                    return None

            log(f"Original frame was {original_frame}. Setting to {target_frame} for inspection.", "DEBUG")
            try:
                scene.frame_set(target_frame)
                if scene.frame_current != target_frame:
                    log(f"Warning: Attempted frame {target_frame}, but current is {scene.frame_current}", "WARNING")
            except Exception as e:
                result_container["output"] = f"[ERROR] Failed to set frame to {target_frame}: {e}"
                # Attempt restore even if set failed
                if original_frame != -1:
                    try:
                        scene.frame_set(original_frame)
                    except Exception as restore_e:
                        log(f"Error restoring frame after set failure: {restore_e}", "ERROR")
                return None  # Stop timer

            # --- Perform Inspection ---
            result_container["output"] = _get_full_sequencer_data_recursive_impl(max_depth=max_depth)

        except Exception as e:
            result_container["output"] = f"[ERROR] Unexpected error during inspection: {str(e)}\n{traceback.format_exc()}"
            log(result_container["output"], "ERROR")
        finally:
            # --- Restore Original Frame ---
            if original_frame != -1 and 'bpy' in locals() and 'scene' in locals() and scene.frame_current != original_frame:
                try:
                    log(f"Restoring original frame to {original_frame}.", "DEBUG")
                    scene.frame_set(original_frame)
                except Exception as e:
                    restore_err = f"[ERROR] Failed to restore original frame ({original_frame}): {e}"
                    log(restore_err, "ERROR")
                    # Append error to output if inspection also failed
                    if result_container["output"].startswith("[ERROR]"):
                        result_container["output"] += "\n" + restore_err
            elif original_frame == -1:
                log("Could not restore frame (original not captured).", "WARNING")

            execution_complete.set()  # Signal completion
        return None  # Stop timer

    try:
        import bpy
        if bpy and hasattr(bpy, 'app') and hasattr(bpy.app, 'timers'):
            bpy.app.timers.register(inspection_runner, first_interval=0.0)
        else:
            raise RuntimeError("bpy.app.timers not available.")

        timeout_seconds = 45.0  # Allow a bit more time for potentially deep inspections
        log(f"Waiting for inspection completion (max {timeout_seconds}s)...", "DEBUG")
        success = execution_complete.wait(timeout=timeout_seconds)

        if success:
            log("Inspection finished.", "DEBUG")
            return result_container["output"]
        else:
            log("--- TOOL: VSE Inspection TIMED OUT! ---", "ERROR")
            return f"[ERROR] VSE inspection timed out after {timeout_seconds} seconds."

    except ImportError:
        err_msg = "[ERROR] Failed to import 'bpy'. This tool must be run within Blender."
        log(err_msg, "CRITICAL")
        return err_msg
    except RuntimeError as rte:
        err_msg = f"[ERROR] Failed to schedule inspection in Blender: {rte}"
        log(err_msg, "ERROR")
        return err_msg
    except Exception as e:
        error_msg = f"[ERROR] Error setting up or waiting for inspection: {type(e).__name__}: {str(e)}\n{traceback.format_exc()}"
        log(error_msg, "ERROR")
        return error_msg


__all__ = [
    'get_full_sequencer_data',
    'inspect_sequencer_at_frame'
]
