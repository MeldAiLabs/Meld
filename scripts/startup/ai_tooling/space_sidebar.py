"""Blender sidebar panel for AI Copilot chat interface and thread management."""
import string
import random
import asyncio
import threading
import time  # For debouncing
import textwrap  # Added for text wrapping

import datetime
from typing import Any
import functools
import json
import bpy
from bpy.props import StringProperty, CollectionProperty, BoolProperty, IntProperty
# ignore the following import error E0001
# pylint: disable=E0001
from bpy.types import Panel, Operator, PropertyGroup, UIList

# --- Helper Function for Opening Code (Copied from TEMP.py) ---
from . import api
from . import message_handler
from . import cache_manager  # Import the instance directly
from .logging_utils import log
# --- Added: Import AuthManager ---
from . import markdown_renderer
from .auth_manager import get_auth_manager
# Global state variables
g_is_processing_message = False
g_current_task = None
g_selected_checkpoint_index = -1
g_initial_threads_fetched = False  # Flag for initial thread fetch
g_is_loading_history = False  # Flag for loading history
g_last_thread_fetch_time = 0  # Debounce thread fetching
THREAD_FETCH_DEBOUNCE_SECONDS = 2

# --- Added: Subscription Status State ---
# Stores the *result* of the subscription check (e.g., 'active', 'inactive', 'checking', 'error', None)
g_subscription_status = None
g_is_checking_subscription = False  # Flag to prevent duplicate checks
g_last_checkout_url = None  # --- Added: Cache for checkout URL ---
# --- End Added ---

# Use singleton instance instead of creating a new one
cache_manager_instance = cache_manager.get_cache_manager()

# Add to global variables
g_models_fetched = False
g_models = []


def _open_code_in_editor(context, code_to_write: str, text_block_name_prefix: str = "Copilot_Script"):
    """Switches to Scripting, creates/finds text block, writes code, assigns to editor via timer."""
    log(f"_open_code_in_editor called. Prefix: {text_block_name_prefix}", "DEBUG")
    scripting_workspace_name = "Scripting"
    scripting_ws = bpy.data.workspaces.get(scripting_workspace_name)

    if not scripting_ws:
        log(f"ERROR: Workspace '{scripting_workspace_name}' not found.", "ERROR")
        # Maybe report error via status? For now, just log.
        return False  # Indicate failure

    current_window = context.window

    # Switch workspace if not already active
    try:
        if current_window.workspace != scripting_ws:
            current_window.workspace = scripting_ws
            log(f"Switched to workspace: {scripting_workspace_name}", "INFO")
        else:
            log(f"Already in workspace: {scripting_workspace_name}", "INFO")
    except Exception as e:
        log(f"ERROR: Failed to switch workspace: {e}", "ERROR")
        return False

    # Create the text data-block directly
    try:
        text_block_name = text_block_name_prefix
        # Ensure unique name if it already exists
        count = 1
        base_name = text_block_name
        while bpy.data.texts.get(text_block_name):
            text_block_name = f"{base_name}.{count:03d}"
            count += 1

        new_text = bpy.data.texts.new(name=text_block_name)
        log(f"Created empty text data-block '{new_text.name}'.", "INFO")

        # --- Schedule assigning the text and writing code using a timer ---
        # Define the timer callback directly here or ensure it's accessible
        # Reusing the existing one: assign_text_to_editor_timer_callback
        callback_with_args = functools.partial(
            assign_text_to_editor_timer_callback,
            current_window,
            new_text.name,
            code_to_write
        )

        # Register the timer (no need to track instance here as it's short-lived)
        bpy.app.timers.register(callback_with_args, first_interval=0.05)
        log("Registered assign_text_to_editor_timer_callback timer", "DEBUG")
        return True  # Indicate success

    except Exception as e:
        import traceback
        log(f"ERROR: Failed during text data creation/timer setup: {e}\n{traceback.format_exc()}", "ERROR")
        return False

# --- Timer Function (Copied from TEMP.py) ---


def assign_text_to_editor_timer_callback(window_ref, text_name, code_to_write):
    """
    Timer callback to find a text editor, assign the created text data-block,
    and write the provided code content.
    """
    log(f"Timer executing: assign_text_to_editor_timer_callback for text '{text_name}'", "DEBUG")
    try:
        # --- Get the text data-block ---
        new_text = bpy.data.texts.get(text_name)
        if not new_text:
            log(f"ERROR (timer): Text data-block '{text_name}' not found in bpy.data.", "ERROR")
            return None  # Timer runs once

        # --- Find a text editor area in the specific window ---
        if window_ref is None or not isinstance(window_ref, bpy.types.Window):
            log("ERROR (timer): Invalid window reference passed.", "ERROR")
            return None  # Timer runs once

        current_screen = window_ref.screen
        if not current_screen:
            log("ERROR (timer): Could not get screen from window reference.", "ERROR")
            return None  # Timer runs once

        log(
            f"Timer searching screen '{current_screen.name}' in window '{window_ref.width}x{window_ref.height}' for TEXT_EDITOR...",
            "DEBUG")
        found_editor_space = None
        found_area_for_redraw = None
        for area in current_screen.areas:
            if area.type == 'TEXT_EDITOR':
                log(f"Found TEXT_EDITOR area: {area.ui_type}", "DEBUG")
                if area.spaces:
                    space = area.spaces[0]  # Text editor area usually has one space
                    if space.type == 'TEXT_EDITOR':
                        found_editor_space = space
                        found_area_for_redraw = area
                        log(f"Found TEXT_EDITOR space in area '{area.ui_type}' via timer", "DEBUG")
                        break  # Found the space, exit search
                    # ... (logging for wrong space type) ...
                # ... (logging for no spaces) ...
            # ... (logging for wrong area type) ...

        # --- Assign text and write content ---
        if found_editor_space:
            log(f"Assigning text '{new_text.name}' to found editor space.", "INFO")
            found_editor_space.text = new_text  # Assign the data-block

            try:
                log(
                    f"Attempting to write code (length: {len(code_to_write)}) to text block '{new_text.name}' via timer",
                    "DEBUG")
                found_editor_space.text.write(code_to_write)  # <<< Use the passed code
                log(f"Successfully wrote code to text block '{new_text.name}'", "INFO")

                # Force redraw of the specific area where it was assigned
                if found_area_for_redraw:
                    found_area_for_redraw.tag_redraw()
                    log(f"Tagged area '{found_area_for_redraw.ui_type}' for redraw.", "DEBUG")

            except Exception as e_write:
                log(f"ERROR (timer): Failed writing code to text block '{new_text.name}': {e_write}", "ERROR")
                import traceback
                log(f"Traceback:\n{traceback.format_exc()}", "ERROR")
        else:
            log("ERROR (timer): Could not find a TEXT_EDITOR area/space to assign the text to.", "ERROR")

    except Exception as e_timer:
        log(f"CRITICAL ERROR (timer): General error in callback: {e_timer}", "ERROR")
        import traceback
        log(f"Traceback:\n{traceback.format_exc()}", "ERROR")

    # IMPORTANT: Return None to make the timer run only once
    return None

# Message data structure


class CHAT_UL_message_item(PropertyGroup):
    """Property group for storing chat message data."""
    text: StringProperty(name="Text", description="Message content", default="")
    sender_type: StringProperty(
        name="Sender Type",
        description="Message type (user/assistant/tool_call/tool_result/human/ai/tool)",
        default="")
    checkpoint_id: StringProperty(
        name="Checkpoint ID",
        description="Checkpoint ID for rewinding conversation",
        default="")
    selected: BoolProperty(name="Selected", description="Whether this message is selected for rewinding", default=False)
    tool_call_info: StringProperty(
        name="Tool Call Info",
        description="JSON string of the tool call data if applicable",
        default="")

# Message list UI


class CHAT_UL_messages(UIList):
    """UI List for displaying chat messages."""

    def draw_item(self, _context, layout, _data, item, _icon, _active_data, _active_propname, index):
        """Draw a single message item in the list."""
        row = layout.row(align=True)

        # Map sender_type to display names
        if item.sender_type in ["user", "human"]:
            text_row = row.row()
            text_row.label(text=f"You: {item.text}")
            # Add rewind button for user messages, but not for the first message
            if index > 0:  # Only show rewind button if not the first message
                if item.selected:
                    op = row.operator("chat.deselect_checkpoint", text="", icon='CHECKMARK')
                    op.message_index = index
                else:
                    op = row.operator("chat.select_checkpoint", text="", icon='LOOP_BACK')
                    op.message_index = index
        elif item.sender_type in ["assistant", "ai"]:
            # Handle AI messages that might just be tool calls
            display_text = item.text if item.text else "(Requesting tool execution...)"
            layout.label(text=f"Assistant: {display_text}")
        elif item.sender_type == "tool_call":
            code_to_open = None
            tool_name = "Unknown Tool"
            # Check if this is an execute_blender_code call and extract code
            if item.tool_call_info:
                try:
                    tool_info = json.loads(item.tool_call_info)
                    tool_name = tool_info.get("name", "Unknown Tool")  # Get tool name
                    if tool_name == "execute_blender_code":
                        code_to_open = tool_info.get("args", {}).get("code")
                except json.JSONDecodeError:
                    log(f"Failed to parse tool_call_info JSON for message {index}", "WARNING")
                except Exception as e:
                    log(f"Error processing tool_call_info for message {index}: {e}", "WARNING")

            # Draw label and button if code exists
            row = layout.row(align=True)
            row.label(text=f"🔧 Tool Call: {tool_name}")
            if code_to_open:
                op = row.operator("sidebar.open_specific_code", text="", icon='TEXT')
                op.code_to_open = code_to_open

        elif item.sender_type == "tool_result" or item.sender_type == "tool":
            # Try to parse tool result content for better display
            result_text = item.text
            tool_call_id_ref = ""
            try:
                if item.tool_call_info:  # Check if tool_call_info holds the original call ID
                    tool_info = json.loads(item.tool_call_info)
                    tool_call_id_ref = tool_info.get("id", "")

                # Attempt to parse the content if it's JSON
                content_data = json.loads(item.text)
                if isinstance(content_data, dict):
                    status = content_data.get("status", "")
                    msg = content_data.get("message", "")
                    obj_name = content_data.get("object_name", "")
                    result_text = f"Status: {status}" + \
                        (f", Object: {obj_name}" if obj_name else "") + (f", Info: {msg}" if msg else "")
            except (json.JSONDecodeError, TypeError):
                pass  # Keep original text if not JSON or not dict

            display_label = "📋 Result" + \
                (f" (for {tool_call_id_ref})" if tool_call_id_ref else "") + f": {result_text}"
            layout.label(text=display_label)
        else:
            # Fallback for unknown types
            layout.label(text=f"[{item.sender_type.upper()}] {item.text}")

# Select checkpoint for rewind


class CHAT_OT_select_checkpoint(Operator):
    """Select a message to rewind to"""
    bl_idname = "chat.select_checkpoint"
    bl_label = "Rewind to Message"
    bl_description = "Mark this message for rewinding the conversation"

    message_index: IntProperty(name="Message Index", description="Index of the message to select")

    def execute(self, context):
        """Execute the select checkpoint operator."""
        global g_selected_checkpoint_index

        # Deselect all messages first
        for msg in context.scene.chat_messages:
            msg.selected = False

        # Select this message
        if 0 <= self.message_index < len(context.scene.chat_messages):
            context.scene.chat_messages[self.message_index].selected = True
            g_selected_checkpoint_index = self.message_index

        return {'FINISHED'}


# Deselect checkpoint for rewind
class CHAT_OT_deselect_checkpoint(Operator):
    """Deselect the message for rewind"""
    bl_idname = "chat.deselect_checkpoint"
    bl_label = "Cancel Rewind"
    bl_description = "Cancel rewinding the conversation"

    message_index: IntProperty(name="Message Index", description="Index of the message to deselect")

    def execute(self, context):
        """Execute the deselect checkpoint operator."""
        global g_selected_checkpoint_index

        # Deselect all messages
        for msg in context.scene.chat_messages:
            msg.selected = False

        g_selected_checkpoint_index = -1

        return {'FINISHED'}

# Send message operator


class CHAT_OT_send_message(Operator):
    """Send a message to the AI assistant"""
    bl_idname = "chat.send_message"
    bl_label = "Send"
    bl_description = "Send message to AI assistant"

    _timer = None
    _last_pending_msg_index = -1
    _current_tool_call_info = None  # Store tool call info for UI update
    _base_status_text = ""  # Base text for animation
    _dot_count = 0         # Current dot count for animation
    _is_animating = False  # Flag to control animation within modal timer

    @classmethod
    def poll(cls, _context):
        """Check if the operator can run (no other message is processing)."""
        # Prevent multiple simultaneous requests
        global g_is_processing_message  # pylint: disable=W0602:global-variable-not-assigned
        return not g_is_processing_message

    # Add new method _animate_dots
    def _animate_dots(self, context):
        """Update the last message with animated dots."""
        scene = context.scene
        # Only animate if processing, flag is set, and messages exist
        if not self._is_animating or not g_is_processing_message or not scene.chat_messages:
            self._is_animating = False  # Ensure flag is off if conditions met
            return

        try:
            last_msg_index = len(scene.chat_messages) - 1
            if last_msg_index < 0:
                self._is_animating = False
                return

            # Ensure we are animating the correct message pointed to by _last_pending_msg_index
            if last_msg_index != self._last_pending_msg_index:
                # log(f"Animation index mismatch: last={last_msg_index}, expected={self._last_pending_msg_index}", "DEBUG")
                # Don't stop animating entirely, might just be a race condition before index update
                # Let update_ui_threadsafe correct the index and restart animation if needed.
                # For now, just don't update text if index is wrong.
                return

            last_msg = scene.chat_messages[last_msg_index]

            # Only animate if the last message is an assistant/tool placeholder we set
            if last_msg.sender_type not in ["assistant", "tool_call", "tool_result"]:
                # log(f"Animation stopped: Last message sender type is '{last_msg.sender_type}'", "DEBUG")
                self._is_animating = False  # Stop animating if message type is wrong
                return

            self._dot_count = (self._dot_count + 1) % 6  # 0 to 5 dots
            dots = "." * self._dot_count
            animated_text = f"{self._base_status_text}{dots}"

            # Check if text actually needs updating to avoid unnecessary redraw triggers
            if last_msg.text != animated_text:
                last_msg.text = animated_text
                # No explicit redraw needed here - modal timer loop handles it usually
            bpy.ops.sidebar.refresh()

        except IndexError:
            # This can happen briefly if messages are cleared while timer is running
            log(f"IndexError in _animate_dots (index: {last_msg_index}), stopping animation.", "WARNING")
            self._is_animating = False
        except Exception as e:
            log(f"Error in _animate_dots: {e}", "ERROR")
            self._is_animating = False  # Stop on general error

    def modal(self, context, event):
        """Modal function to handle the timer event"""
        global g_is_processing_message, g_current_task

        if event.type == 'TIMER':
            # --- Add Animation Call ---
            # Call animation regardless of task status, stop condition is inside _animate_dots
            self._animate_dots(context)
            # --- End Add Animation Call ---

            if not g_current_task:
                # --- Stop Animation ---
                if self._is_animating:
                    log("Stopping animation because g_current_task is None.", "DEBUG")
                    self._is_animating = False
                # --- End Stop Animation ---
                self.cancel(context)
                return {'CANCELLED'}

            if g_current_task.done():
                # --- Stop Animation ---
                if self._is_animating:
                    log("Stopping animation because task is done.", "DEBUG")
                    self._is_animating = False
                # --- End Stop Animation ---
                try:
                    # Get the result
                    response = g_current_task.result()
                    log(f"Modal received final result: {response}", "DEBUG")

                    # Update the LAST pending message with final response
                    if self._last_pending_msg_index >= 0 and self._last_pending_msg_index < len(
                            context.scene.chat_messages):
                        final_msg = context.scene.chat_messages[self._last_pending_msg_index]

                        if response.get("type") == "ai_message" and "data" in response:
                            final_msg.text = response["data"]
                            final_msg.sender_type = "assistant"  # Use sender_type

                            # Store checkpoint ID from response if available
                            new_checkpoint_id = response.get("new_checkpoint_id")
                            if new_checkpoint_id:
                                final_msg.checkpoint_id = new_checkpoint_id
                                log(f"Stored checkpoint ID: {new_checkpoint_id}", "INFO")

                            # --- Add to Cache ---
                            active_thread_id = cache_manager_instance.get_active_thread()
                            if active_thread_id:
                                cache_manager_instance.add_message_to_history(active_thread_id, {
                                    "type": "ai",
                                    "content": final_msg.text,
                                    "checkpoint_id": new_checkpoint_id,
                                    "tool_calls": None
                                })
                            # --- End Add to Cache ---

                            log("Successfully received final assistant response", "INFO")
                        elif response.get("type") == "error":
                            error_msg = f"[Error] {response['message']}"
                            final_msg.text = error_msg
                            final_msg.sender_type = "assistant"  # Use sender_type
                            log(error_msg, "ERROR")
                            self.report({'ERROR'}, error_msg)
                        else:
                            error_msg = "[Error] Unexpected response format"
                            final_msg.text = error_msg
                            final_msg.sender_type = "assistant"  # Use sender_type
                            log(error_msg, "ERROR")
                            self.report({'ERROR'}, error_msg)
                    else:
                        log(
                            f"Last pending message index {self._last_pending_msg_index} out of bounds. Cannot update final message.",
                            "WARNING")

                    # Refresh the sidebar to reflect changes
                    bpy.ops.sidebar.refresh()

                except Exception as e:
                    error_msg = f"Error processing result: {str(e)}"
                    log(error_msg, "ERROR")
                    self.report({'ERROR'}, error_msg)

                    # Update assistant message with error
                    if self._last_pending_msg_index >= 0 and self._last_pending_msg_index < len(
                            context.scene.chat_messages):
                        context.scene.chat_messages[self._last_pending_msg_index].text = f"[Error] {str(e)}"
                        # Use sender_type
                        context.scene.chat_messages[self._last_pending_msg_index].sender_type = "assistant"
                    else:
                        log(
                            f"Last pending message index {self._last_pending_msg_index} out of bounds. Cannot update error message.",
                            "WARNING")

                    # Refresh the sidebar to reflect error state
                    bpy.ops.sidebar.refresh()

                # Mark processing as complete
                g_is_processing_message = False
                g_current_task = None

                # Remove the timer
                self.cancel(context)
                return {'FINISHED'}

        return {'PASS_THROUGH'}

    def cancel(self, context):
        """Cancel the timer"""
        # --- Stop Animation ---
        if self._is_animating:
            log("Stopping animation on cancel.", "DEBUG")
            self._is_animating = False
        # --- End Stop Animation ---
        if self._timer:
            context.window_manager.event_timer_remove(self._timer)
            self._timer = None
        return {'CANCELLED'}

    def execute(self, context):
        """Execute the operator"""
        global g_is_processing_message, g_current_task, g_selected_checkpoint_index

        scene = context.scene
        user_message = scene.chat_input.strip()  # Get message before any potential undo
        if not user_message:
            return {'CANCELLED'}

        # Get selected model - READ FROM StringProperty NOW
        model_name = scene.ai_model_selected
        log(f"Using selected model: {model_name}", "DEBUG")

        # --- Get Thread ID from Cache or Create New ---
        active_thread_id = cache_manager_instance.get_active_thread()
        if not active_thread_id:
            # Start a new thread
            active_thread_id = ''.join(random.choices(string.ascii_letters + string.digits, k=20))
            log(f"Creating new thread ID: {active_thread_id}", "INFO")
            cache_manager_instance.set_active_thread(active_thread_id)
            # Add metadata for the new thread immediately
            cache_manager_instance.add_or_update_thread_metadata(active_thread_id)
            # Trigger a refresh of the thread list UI property after adding
            bpy.ops.sidebar.refresh()
        # --- End Get Thread ID ---

        # Check if we need to rewind to a checkpoint
        api_checkpoint_id = None  # This will be the ID sent to the API (base ID)
        messages_to_remove = 0
        if g_selected_checkpoint_index >= 0:
            log(f"Rewind selected. Target user message index: {g_selected_checkpoint_index}", "DEBUG")
            # Find the checkpoint ID for the ASSISTANT message *before* the selected user message
            assistant_index_before = -1
            for i in range(g_selected_checkpoint_index - 1, -1, -1):
                log(f"Checking message index {i} for previous assistant checkpoint.", "DEBUG")
                if context.scene.chat_messages[i].sender_type == "assistant" and context.scene.chat_messages[i].checkpoint_id:
                    assistant_index_before = i
                    log(f"Found previous assistant message at index {i}.", "DEBUG")
                    break

            if assistant_index_before >= 0:
                # Get the base checkpoint ID from the previous assistant message
                base_checkpoint_id = context.scene.chat_messages[assistant_index_before].checkpoint_id
                log(f"Base checkpoint ID from previous assistant: {base_checkpoint_id}", "DEBUG")
                # Strip any existing _before/_after suffix (should ideally be base ID already, but just in case)
                base_checkpoint_id = base_checkpoint_id.replace("_before", "").replace("_after", "")
                log(f"Cleaned base checkpoint ID: {base_checkpoint_id}", "DEBUG")

                # Use the "_before" version associated with THIS checkpoint for the undo operation
                # This checkpoint was pushed right before the request corresponding to the selected user message
                undo_target_checkpoint_id = f"{base_checkpoint_id}_before"
                log(f"Targeting undo to: {undo_target_checkpoint_id}", "INFO")

                # Calculate how many messages to remove after undo
                messages_to_remove = len(context.scene.chat_messages) - g_selected_checkpoint_index
                log(f"Messages to remove from UI: {messages_to_remove}", "DEBUG")

                # Call checkpoint_undo with the target checkpoint ID
                try:
                    bpy.ops.sidebar.checkpoint_undo('INVOKE_DEFAULT', checkpoint_name=undo_target_checkpoint_id)
                    log(f"Called checkpoint_undo with target ID: {undo_target_checkpoint_id}", "INFO")
                except Exception as e:
                    log(f"Error calling checkpoint_undo: {e}", "WARNING")

                # Get fresh context after undo
                context = bpy.context
                scene = context.scene

                # Remove messages after the checkpoint
                log(f"Removing {messages_to_remove} messages from UI starting after index {g_selected_checkpoint_index-1}", "DEBUG")
                # Start removing from the end to avoid index issues
                for _ in range(messages_to_remove):
                    if len(scene.chat_messages) > g_selected_checkpoint_index:
                        remove_idx = len(scene.chat_messages) - 1
                        log(f"Removing message at index {remove_idx}", "DEBUG")
                        scene.chat_messages.remove(remove_idx)
                    else:
                        log("Warning: Attempted to remove message, but index out of bounds.", "WARNING")
                        break

                # --- ADDED: Clear Cache History After Index ---
                last_kept_index = g_selected_checkpoint_index - 1
                if last_kept_index >= 0:
                    log(
                        f"Removing messages from cache for thread {active_thread_id} after index {last_kept_index}",
                        "INFO")
                    cache_manager_instance.remove_messages_after_index(active_thread_id, last_kept_index)
                else:
                    log(
                        f"Clearing all messages from cache for thread {active_thread_id} as rewind target was index 0",
                        "INFO")
                    # Clear all if rewinding to before the first message
                    cache_manager_instance.clear_thread_history(active_thread_id)
                # --- END ADDED ---

                # Reset selection
                g_selected_checkpoint_index = -1

                # Deselect all messages (redundant as we remove them, but safe)
                for msg in scene.chat_messages:
                    msg.selected = False

                # Use the base checkpoint ID (without suffix) for the API request
                # This tells the API to resume from the state AFTER the previous request
                api_checkpoint_id = base_checkpoint_id
                log(f"Using checkpoint ID for API request: {api_checkpoint_id}", "INFO")
            else:
                # This case should not happen if the rewind button isn't shown for index 0
                log("Warning: Rewind selected but no previous assistant checkpoint found.", "WARNING")
                g_selected_checkpoint_index = -1  # Reset selection anyway

        # --- Get base checkpoint ID for the *current* state before this new request ---
        # If we didn't rewind, find the last checkpoint ID from the previous request
        if not api_checkpoint_id and len(scene.chat_messages) > 0:
            log("No rewind occurred. Finding last checkpoint ID for 'before' push.", "DEBUG")
            for i in range(len(scene.chat_messages) - 1, -1, -1):
                msg = scene.chat_messages[i]
                if msg.sender_type == "assistant" and msg.checkpoint_id:
                    # Strip any existing suffix
                    current_base_checkpoint_id = msg.checkpoint_id.replace("_before", "").replace("_after", "")
                    api_checkpoint_id = current_base_checkpoint_id  # This is the ID we are starting from
                    log(f"Found last base checkpoint ID: {api_checkpoint_id}", "DEBUG")
                    break

        # Store base checkpoint ID for API request (this is the checkpoint we are resuming *from*)
        log(f"Final API checkpoint ID set to: {api_checkpoint_id}", "DEBUG")

        # Add user message to history
        new_msg = scene.chat_messages.add()
        new_msg.text = user_message
        new_msg.sender_type = "user"  # Use sender_type

        # --- Add to Cache ---
        cache_manager_instance.add_message_to_history(active_thread_id, {
            "type": "human",
            "content": user_message,
            "checkpoint_id": None
        })
        # --- End Add to Cache ---

        # Clear input
        scene.chat_input = ""

        # Add pending assistant message
        assistant_msg = scene.chat_messages.add()
        # Set initial base text for animation (no dots yet)
        self._base_status_text = "Thinking"
        self._dot_count = 0
        assistant_msg.text = self._base_status_text  # Set initial text immediately
        assistant_msg.sender_type = "assistant"  # Use sender_type
        self._last_pending_msg_index = len(scene.chat_messages) - 1
        log(f"Added initial pending message at index {self._last_pending_msg_index}", "DEBUG")

        # Mark as processing AND START ANIMATING
        g_is_processing_message = True
        self._is_animating = True  # Start animation flag
        self._current_tool_call_info = None  # Reset temporary storage

        # Set initial status and force an immediate UI refresh
        # set_status_message("Sending...")  # Commented out as requested
        wm = context.window_manager
        wm.is_sending_message = True

        # Force an immediate UI refresh to show the "Thinking..." status
        try:
            bpy.ops.sidebar.refresh()
        except Exception as e:
            log(f"Error refreshing UI: {e}", "WARNING")

        # --- Create status update callback with checkpoint handling ---
        def update_status(status: Any):
            """Callback to update the assistant message with current status and handle checkpoints."""
            log(f"Status update received: Type={type(status)}, Value={status}", "INFO")
            # Checkpoint push is now handled in process_message_async and message_handler

            # Continue with normal status update handling...
            sender_type = "assistant"  # Default assumption
            is_new_message = False
            tool_call_info_json = None

            # --- Determine New Base Text ---
            new_base_status_text = "[In Progress]"  # Default base text
            if isinstance(status, dict) and status.get('type') == 'tool_call_start':
                tool_call_data = status.get('data', {})
                tool_name = tool_call_data.get('name', 'unknown')
                new_base_status_text = f"Executing tool: {tool_name}"
                sender_type = "tool_call"  # Update sender type for this specific message
                is_new_message = True
                try:
                    tool_call_info_json = json.dumps(tool_call_data)
                    log(f"Storing tool call info: {tool_call_info_json}", "DEBUG")
                    self._current_tool_call_info = tool_call_info_json  # Store temporarily for UI thread
                except Exception as e:
                    log(f"Error serializing tool call data: {e}", "WARNING")
            elif isinstance(status, str) and "Tool '" in status and "' execution" in status:
                # Handle tool result event (existing logic)
                try:
                    parts = status.split("'")
                    tool_name = parts[1]
                    result = parts[2].split("execution ")[-1]
                    new_base_status_text = f"{tool_name}: {result}"
                    sender_type = "tool_result"
                    is_new_message = True

                    log(f"Detected tool result: {tool_name} - {result}", "INFO")
                except Exception as e:
                    log(f"Error parsing tool result status: {e}", "WARNING")
                    message_text = "[In Progress] Processing results..."
            elif isinstance(status, str):
                # Simple string status update
                new_base_status_text = f"[In Progress] {status}"
                sender_type = "assistant"  # Assume it's still assistant update
                is_new_message = False  # It's just updating the current placeholder
            else:
                log(f"Unknown status update format: {status}", "WARNING")
                new_base_status_text = "[In Progress] Processing..."
                sender_type = "assistant"
                is_new_message = False
            # --- End Determine New Base Text ---

            # Schedule UI update in main thread
            log(
                f"Scheduling UI update: is_new_message={is_new_message}, sender={sender_type}, base_text='{new_base_status_text}'",
                "DEBUG")

            # Use a local variable to capture the current tool_call_info for the timer
            captured_tool_info = self._current_tool_call_info
            # Reset the class variable immediately after capturing it for the timer
            if sender_type == "tool_call" and is_new_message:
                self._current_tool_call_info = None

            def update_ui_threadsafe():
                log("Executing UI update in main thread", "DEBUG")
                try:
                    # --- Update Animation Base Text FIRST ---
                    # This ensures subsequent animation ticks use the new text
                    log(f"Updating animation base text to: '{new_base_status_text}'", "DEBUG")
                    self._base_status_text = new_base_status_text
                    self._dot_count = 0  # Reset dots when base text changes
                    self._is_animating = True  # Ensure animation continues or restarts
                    # --- End Update Animation Base Text ---

                    current_pending_msg = None
                    if self._last_pending_msg_index >= 0 and self._last_pending_msg_index < len(scene.chat_messages):
                        current_pending_msg = scene.chat_messages[self._last_pending_msg_index]
                    else:
                        log(
                            f"Last pending message index {self._last_pending_msg_index} out of bounds. Cannot update.",
                            "WARNING")
                        self._is_animating = False  # Stop animation if state is broken
                        return None  # Stop timer

                    if is_new_message:
                        log(
                            f"Updating message at index {self._last_pending_msg_index} to {sender_type}",
                            "DEBUG")
                        # Update the *current* pending message to be the tool call/result
                        current_pending_msg.text = self._base_status_text  # Update with new base text (no dots yet)
                        current_pending_msg.sender_type = sender_type  # Use determined sender_type
                        # Store tool call info if this is a tool_call start message
                        if sender_type == "tool_call" and captured_tool_info:
                            current_pending_msg.tool_call_info = captured_tool_info
                            log(f"Stored tool_call_info JSON in message item {self._last_pending_msg_index}", "DEBUG")

                        # --- Add Tool Call/Result to Cache ---
                        cache_data = {
                            "type": sender_type,
                            "content": self._base_status_text,
                            "checkpoint_id": api_checkpoint_id,
                            "tool_calls": None
                        }
                        if sender_type == "tool_call" and captured_tool_info:
                            cache_data["tool_call_info"] = captured_tool_info
                            cache_data["type"] = "tool_call"
                            cache_data["content"] = self._base_status_text
                        elif sender_type == "tool_result":
                            cache_data["type"] = "tool_result"
                            cache_data["content"] = self._base_status_text
                            if captured_tool_info:
                                try:
                                    tool_info = json.loads(captured_tool_info)
                                    cache_data["tool_call_id"] = tool_info.get("id")
                                except (json.JSONDecodeError, TypeError):
                                    pass

                        # Remove unused keys before adding
                        cache_data.pop("sender_type", None)
                        cache_data.pop("text", None)

                        cache_manager_instance.add_message_to_history(active_thread_id, cache_data)
                        # --- End Add Tool Call/Result to Cache ---

                        log("Adding new pending message", "DEBUG")
                        # Add a *new* pending message for the next step (AI thinking)
                        new_pending_msg = scene.chat_messages.add()
                        # Set the base text for the *new* message and reset animation state for it
                        self._base_status_text = "Thinking"  # Reset base for the new placeholder
                        self._dot_count = 0
                        new_pending_msg.text = self._base_status_text  # Set initial text
                        new_pending_msg.sender_type = "assistant"  # It's an assistant placeholder
                        self._last_pending_msg_index = len(scene.chat_messages) - 1  # Update index!
                        log(f"New pending message added at index {self._last_pending_msg_index}", "DEBUG")

                    else:
                        # Update the text of the existing pending message (simple status update)
                        log(f"Updating pending message text at index {self._last_pending_msg_index}", "DEBUG")
                        if current_pending_msg:
                            # Update with base text, animation timer will add dots
                            current_pending_msg.text = self._base_status_text
                        else:
                            log("Cannot update text, current_pending_msg is None", "WARNING")
                            self._is_animating = False  # Stop animation if state broken

                    # Refresh the sidebar to show the change immediately
                    log("Refreshing sidebar", "DEBUG")
                    bpy.ops.sidebar.refresh()
                    log("Sidebar refresh complete", "DEBUG")

                except Exception as e:
                    # Catch potential errors if context changes during async operation
                    log(f"Error updating UI: {e}", "ERROR")
                    self._is_animating = False  # Stop animation on UI update error

                return None  # Run timer only once

            # Register the timer to run the update in the main thread
            bpy.app.timers.register(update_ui_threadsafe, first_interval=0.0)

        # Define the background task function
        async def process_message_async():
            try:
                log(f"Processing message for thread {active_thread_id} in background: {user_message[:50]}...", "INFO")

                # Push the "before" checkpoint for the *new* request asynchronously
                if api_checkpoint_id:
                    log(f"Scheduling 'before' checkpoint push for base ID: {api_checkpoint_id}", "DEBUG")
                    asyncio.create_task(message_handler.push_checkpoint(api_checkpoint_id, is_before=True))
                else:
                    # Handle first message case - maybe push a "start_thread" checkpoint?
                    log("No base checkpoint ID found. Skipping 'before' push (likely first message).", "DEBUG")

                return await message_handler.process_message(
                    active_thread_id,  # Use the determined thread ID
                    user_message,
                    on_update=update_status,
                    checkpoint_id=api_checkpoint_id,  # Use the base checkpoint ID without _before/_after
                    model_name=model_name
                )
            except Exception as e:
                log(f"Error in background task: {str(e)}", "ERROR")
                return {"type": "error", "message": str(e)}

        # Start the background task
        loop = asyncio.new_event_loop()

        def run_background_task():
            asyncio.set_event_loop(loop)
            global g_current_task
            g_current_task = asyncio.run_coroutine_threadsafe(process_message_async(), loop)
            loop.run_forever()

        # Start background thread
        bg_thread = threading.Thread(target=run_background_task, daemon=True)
        bg_thread.start()

        # Start modal timer to check for completion (also drives animation)
        wm = context.window_manager
        # Check status frequently enough for smooth animation feel
        self._timer = wm.event_timer_add(0.5, window=context.window)  # Adjust interval if needed (e.g., 0.15s)
        wm.modal_handler_add(self)

        return {'RUNNING_MODAL'}

# Cancel operator


class CHAT_OT_cancel_message(Operator):
    """Cancel the current message processing"""
    bl_idname = "chat.cancel_message"
    bl_label = "Cancel"
    bl_description = "Cancel the current message processing"

    @classmethod
    def poll(cls, _context):
        """Check if the operator can run (a message is currently processing)."""
        global g_is_processing_message  # pylint: disable=W0602:global-variable-not-assigned
        return g_is_processing_message

    def execute(self, context):
        """Execute the cancel operation."""
        global g_is_processing_message, g_current_task

        # Mark as no longer processing
        g_is_processing_message = False

        # Try to cancel the task if it exists
        if g_current_task and not g_current_task.done():
            g_current_task.cancel()

        # Update the pending message if it exists
        for msg in context.scene.chat_messages:
            # Check both potential sender types for pending message
            if msg.sender_type == "assistant" and msg.text.startswith("Thinking") or msg.text.startswith(
                    "[In Progress]") or msg.text.startswith("[Cancelled]"):
                msg.text = "[Cancelled] Request was interrupted"
                break

        return {'FINISHED'}

# Main sidebar panel


class CHAT_PT_sidebar(Panel):
    """Chat interface in the sidebar"""
    bl_label = "AI Copilot"
    bl_idname = "CHAT_PT_sidebar_main"
    bl_space_type = 'SIDEBAR'     # Critical: Use SIDEBAR as the space type
    bl_region_type = 'WINDOW'     # Critical: Use WINDOW as the region type
    bl_category = "AI Copilot"    # Keep your custom category

    # --- Custom Message Drawing Method (Moved Inside) --- #
    def draw_message_group(self, layout, user_msg_item, ai_response_items, context, user_msg_index=None):
        """Draw a group of messages consisting of a user message and AI responses."""
        try:
            msg_box = layout.box()

            # --- Draw user message first ---
            if user_msg_item:
                row = msg_box.row(align=True)
                row.label(text="You:", icon='USER')

                # Add rewind/deselect buttons
                if user_msg_index is not None and user_msg_index > 0:  # Only show if not the first message
                    button_row = row.row(align=True)
                    button_row.alignment = 'RIGHT'
                    if user_msg_item.selected:
                        op = button_row.operator("chat.deselect_checkpoint", text="", icon='CHECKMARK')
                        op.message_index = user_msg_index
                    else:
                        op = button_row.operator("chat.select_checkpoint", text="", icon='LOOP_BACK')
                        op.message_index = user_msg_index

                # Draw user message content (using textwrap)
                max_width = 70  # Fixed width
                # Safely get text, handle potential None
                user_text = getattr(user_msg_item, 'text', "[No content]") or "[No content]"
                wrapped_text = textwrap.wrap(user_text, width=max_width)
                for line in wrapped_text:
                    msg_box.label(text=line)

            # --- Draw AI responses ---
            if ai_response_items:
                ai_box = msg_box.box()
                row = ai_box.row(align=True)
                row.label(text="Assistant:", icon='LIGHT')  # Changed icon for visual distinction

                # Process each AI response item
                for item in ai_response_items:
                    response_type = getattr(item, 'sender_type', 'unknown')
                    response_text = getattr(item, 'text', '') or ""  # Handle None
                    tool_call_info_json = getattr(item, 'tool_call_info', None)

                    if response_type == "assistant":
                        # Final AI response - render as markdown
                        # Check if we are still loading or checking subscription
                        # before attempting to render markdown for the actual content
                        if g_is_checking_subscription:
                            ai_box.label(text="Checking subscription status...", icon='INFO')
                        elif g_subscription_status is None:
                            ai_box.label(text="Checking subscription status...", icon='INFO')
                        elif g_subscription_status not in ['active', 'trialing']:
                            # This case shouldn't happen if draw logic is correct, but as a fallback
                            ai_box.label(text="Subscription required.", icon='ERROR')
                        else:
                            # Proceed with markdown rendering only if status is ok
                            markdown_renderer.draw_markdown(
                                ai_box, response_text if response_text else "(No text content)", width=70)

                    elif response_type == "tool_call":
                        code_to_open = None
                        tool_name = "Unknown Tool"
                        # Check if this is an execute_blender_code call and extract code
                        if tool_call_info_json:
                            try:
                                tool_info = json.loads(tool_call_info_json)
                                tool_name = tool_info.get("name", "Unknown Tool")
                                if tool_name == "execute_blender_code":
                                    code_to_open = tool_info.get("args", {}).get("code")
                            except json.JSONDecodeError:
                                log("Failed to parse tool_call_info JSON for tool_call message", "WARNING")
                            except Exception as e:
                                log(f"Error processing tool_call_info for tool_call message: {e}", "WARNING")

                        # Draw label and button if code exists
                        tool_row = ai_box.row(align=True)
                        tool_row.label(text=f"🔧 Tool Call: {tool_name}")
                        if code_to_open:
                            op = tool_row.operator("sidebar.open_specific_code", text="", icon='TEXT')
                            op.code_to_open = code_to_open

                    elif response_type == "tool_result" or response_type == "tool":
                        # Display tool result
                        result_row = ai_box.row()
                        # Try to parse tool result content for better display
                        result_text = response_text
                        tool_call_id_ref = ""
                        try:
                            if tool_call_info_json:  # Check if tool_call_info holds the original call ID
                                tool_info = json.loads(tool_call_info_json)
                                tool_call_id_ref = tool_info.get("id", "")

                            # Attempt to parse the content if it's JSON
                            content_data = json.loads(response_text)
                            if isinstance(content_data, dict):
                                status = content_data.get("status", "")
                                msg = content_data.get("message", "")
                                obj_name = content_data.get("object_name", "")
                                result_text = f"Status: {status}" + \
                                    (f", Object: {obj_name}" if obj_name else "") + (f", Info: {msg}" if msg else "")
                        except (json.JSONDecodeError, TypeError):
                            pass  # Keep original text if not JSON or not dict

                        display_label = "📋 Result" + \
                            (f" (for {tool_call_id_ref})" if tool_call_id_ref else "") + f": {result_text}"
                        result_row.label(text=display_label, icon='INFO')  # Use standard INFO icon

                    else:
                        # Fallback for unknown types
                        content_box = ai_box.box()
                        max_width = int(context.region.width / 7)  # Approximate width
                        wrapped_text = textwrap.wrap(f"[{response_type.upper()}] {response_text}", width=max_width)
                        for line in wrapped_text:
                            content_box.label(text=line)

        except Exception as e:
            log(f"Error in draw_message_group: {e}", "ERROR")
            layout.label(text=f"Error displaying message group: {str(e)}", icon='ERROR')
    # --- End Custom Message Drawing Method --- #

    def draw(self, context):
        """Draw the sidebar panel UI based on authentication and subscription status."""
        layout = self.layout
        scene = context.scene
        # Correctly declare all globals used in this method here
        global g_initial_threads_fetched, g_is_loading_history, g_last_thread_fetch_time, g_is_processing_message
        global g_subscription_status, g_is_checking_subscription, g_models_fetched, g_models
        # Get AuthManager instance
        auth_manager = get_auth_manager()

        # --- 1. Authentication Status Box (Always Visible) --- #
        auth_box = layout.box()
        auth_header = auth_box.row(align=True)
        is_logged_in = auth_manager.is_authenticated()

        if is_logged_in:
            status_text = "Logged in"
            auth_header.label(text=status_text, icon='USER')
            # Add a button to manually refresh subscription status
            refresh_op = auth_header.operator("auth.check_subscription", text="", icon='FILE_REFRESH')
            refresh_op.show_message = True  # Give feedback on manual refresh
            auth_header.operator("auth.logout", text="", icon='USER')  # Use LOGOUT icon
        else:
            auth_header.label(text="Status: Not Authenticated", icon='GHOST_ENABLED')
            auth_header.operator("auth.login", text="Login", icon='USER')  # Use LOGIN icon
            return  # --- Stop drawing if not logged in --- #

        # --- 2. Subscription Check and Conditional UI (Only if Logged In) --- #

        # Check if status is currently being checked
        if g_is_checking_subscription:
            layout.label(text="Checking subscription status...", icon='INFO')
            return  # --- Stop drawing while checking --- #

        # Check if status is None (initial load or failed check)
        if g_subscription_status is None:
            layout.label(text="Checking subscription status...", icon='INFO')
            # Trigger the check if it's not already running (e.g., after login)
            if not g_is_checking_subscription:
                log("Triggering subscription check from draw because status is None", "INFO")
                check_subscription_status_async()
            return  # --- Stop drawing while checking --- #

        # --- 3. Display UI based on Subscription Status --- #

        # ACTIVE / TRIALING: Show Full Chat UI
        if g_subscription_status in ['active', 'trialing']:
            # --- Initial Thread/Model Fetch (Run only ONCE per session when active) --- #
            if not g_initial_threads_fetched:
                # Prevent triggering during other processing
                if not g_is_loading_history and not g_is_processing_message:
                    g_initial_threads_fetched = True  # Set flag immediately
                    log("Triggering FIRST initial thread list fetch (Subscribed)...", "INFO")
                    threading.Thread(target=fetch_threads_and_update_cache, daemon=True).start()

                    if not g_models_fetched:
                        log("Triggering initial model fetch (Subscribed)...", "INFO")
                        threading.Thread(target=fetch_models, daemon=True).start()

                    # Show loading only if cache is truly empty AND fetch just started
                    if len(cache_manager_instance.get_thread_list_sorted()) == 0:
                        layout.label(text="Loading chat threads and models...", icon='INFO')
                        return  # Don't draw rest until fetch populates

            # --- Thread Selection --- #
            thread_box = layout.box()
            row = thread_box.row(align=True)
            # Ensure thread enum property exists before drawing
            if hasattr(scene, 'ai_active_thread'):
                row.prop(scene, "ai_active_thread", text="")
            else:
                row.label(text="Loading threads...", icon='INFO')

            button_row = thread_box.row(align=True)
            button_row.scale_y = 0.9
            button_row.operator("chat.new_thread", text="New", icon='ADD')
            delete_op = button_row.operator("chat.delete_thread", text="Delete", icon='TRASH')
            if hasattr(scene, 'ai_active_thread'):
                delete_op.thread_id = scene.ai_active_thread
            else:
                delete_op.enabled = False  # Disable if property doesn't exist yet
            button_row.operator("chat.refresh_threads", text="Refresh", icon='FILE_REFRESH')

            # --- Chat History --- #
            history_box = layout.box()
            history_header = history_box.row()
            history_header.label(text="Chat History", icon='BOOKMARKS')

            if g_is_loading_history:
                history_box.label(text="Loading history...", icon='INFO')
            else:
                messages = scene.chat_messages
                if not messages:
                    history_box.label(text="No messages yet. Type below to start.")
                else:
                    # --- Custom Drawing Logic --- #
                    current_group_items = []
                    current_user_item = None
                    current_user_index = None
                    for index, msg_item in enumerate(messages):
                        try:
                            msg_sender_type = msg_item.sender_type
                            if msg_sender_type in ["user", "human"]:
                                if current_group_items:
                                    self.draw_message_group(
                                        history_box, current_user_item, current_group_items, context, current_user_index)
                                    current_group_items = []
                                current_user_item = msg_item
                                current_user_index = index
                                self.draw_message_group(history_box, current_user_item, [], context, current_user_index)
                                current_user_item = None
                            else:
                                current_group_items.append(msg_item)
                            if index == len(messages) - 1 and current_group_items:
                                self.draw_message_group(history_box, None, current_group_items, context, None)
                        except Exception as e:
                            log(f"Error processing message index {index} for custom draw: {e}", "ERROR")
                            import traceback
                            traceback.print_exc()
                            history_box.label(text=f"Error processing message #{index}: {str(e)}", icon='ERROR')
                    # --- End Custom Drawing --- #

            # --- Input and send button --- #
            split = layout.split(factor=0.88, align=True)
            input_col = split.column()
            input_col.prop(scene, "chat_input", text="")
            button_col = split.column(align=True)
            if g_is_processing_message:
                button_col.operator("chat.cancel_message", text="", icon='CANCEL')
            else:
                button_col.enabled = bool(scene.chat_input.strip())
                button_col.operator("chat.send_message", text="", icon='FORWARD')

            # --- Model Selector Menu --- #
            model_row = layout.row()
            # Ensure model property exists before drawing
            if hasattr(scene, 'ai_model_selected'):
                selected_model_display_name = "Select Model"
                for identifier, name, _description in g_models:
                    if identifier == scene.ai_model_selected:
                        selected_model_display_name = name
                        break
                model_row.menu("MODEL_MT_select_menu", text=selected_model_display_name, icon='SETTINGS')
            else:
                model_row.label(text="Loading models...", icon='INFO')

        # OTHER STATUS (INACTIVE, ERROR, ETC.): Show Upgrade Prompt
        else:
            log(f"Drawing upgrade prompt because status is: {g_subscription_status}", "DEBUG")  # <-- ADD LOG
            upgrade_box = layout.box()
            status_message = f"Subscription Status: {g_subscription_status.capitalize()}"
            if g_subscription_status == 'error':
                status_message = "Error checking subscription. Please try again."
            upgrade_box.label(text=status_message, icon='ERROR' if g_subscription_status == 'error' else 'INFO')
            upgrade_box.label(text="Upgrade to Meld Pro to unlock AI Copilot features.")
            upgrade_box.operator(SUBSCRIPTION_OT_upgrade.bl_idname, text="Upgrade Now", icon='SOLO_ON')
            upgrade_box.operator("auth.check_subscription", text="Refresh Status", icon='FILE_REFRESH')

            # --- Added: Display Cached URL ---
            if g_last_checkout_url:
                url_box = upgrade_box.box()  # Use a sub-box for the URL
                url_box.label(text="We lauched a link in your browser. If you need to copy it, you can do so here:")
                url_row = url_box.row(align=True)  # Align row elements
                url_row.enabled = False  # Make the property read-only
                # Ensure property exists before drawing
                if hasattr(scene, "checkout_url_display"):
                    # Use expand=True to allow the property to take available width
                    url_row.prop(scene, "checkout_url_display", text="", expand=True)
                    # Make sure the row itself is enabled for the button
                    button_row = url_box.row(align=True)
                    copy_op = button_row.operator(CLIPBOARD_OT_copy_text.bl_idname, text="Copy Link", icon='COPYDOWN')
                    # Pass the current URL value to the operator
                    copy_op.text_to_copy = scene.checkout_url_display
                else:
                    url_row.label(text="Error: URL Property missing", icon='ERROR')
            # --- End Added ---

            return  # --- Stop drawing after upgrade prompt --- #

# --- Operator for specific code blocks (Copied from TEMP.py) ---


class SIDEBAR_OT_open_specific_code(Operator):
    """Opens a *specific* code snippet in the Text Editor."""
    bl_idname = "sidebar.open_specific_code"
    bl_label = "Open Specific Code"
    bl_description = "Switch to Scripting workspace and load a specific code snippet"

    code_to_open: StringProperty(
        name="Code to Open",
        description="The Python code snippet to open in the editor",
        default="# No code provided to operator."
    )

    def execute(self, context):
        """Execute the open specific code operation."""
        log(f"Opening specific code block (length: {len(self.code_to_open)}).", "INFO")
        success = _open_code_in_editor(context, self.code_to_open, text_block_name_prefix="Copilot_Specific_Script")

        if success:
            # Don't report info here, it gets spammy with many buttons
            # self.report({'INFO'}, "Attempting to open specific code in Text Editor.")
            return {'FINISHED'}
        else:
            self.report({'ERROR'}, "Failed to open specific code in Text Editor.")
            return {'CANCELLED'}


# --- New Operators for Thread Management --- #

class CHAT_OT_new_thread(Operator):
    """Start a new chat thread"""
    bl_idname = "chat.new_thread"
    bl_label = "New Chat"
    bl_description = "Clear current chat and start a new conversation"

    def execute(self, context):
        """Creates a new thread"""
        log("Starting new chat thread", "INFO")
        # 1. Generate new thread ID
        new_thread_id = ''.join(random.choices(string.ascii_letters + string.digits, k=20))
        log(f"Generated new thread ID: {new_thread_id}", "INFO")

        # 2. Add to cache with current timestamp
        cache_manager_instance.add_or_update_thread_metadata(new_thread_id)

        # 3. Set as active thread
        cache_manager_instance.set_active_thread(new_thread_id)

        # 4. Update the Scene property to trigger UI update and history clear
        context.scene.ai_active_thread = new_thread_id

        # 5. Clear UI messages (redundant if update callback handles it, but safe)
        context.scene.chat_messages.clear()
        context.scene.chat_input = ""  # Clear input field

        # 6. Refresh sidebar (might be redundant due to property update, but ensures list updates)
        bpy.ops.sidebar.refresh()
        return {'FINISHED'}


class CHAT_OT_refresh_threads(Operator):
    """Manually refresh the list of chat threads from the backend"""
    bl_idname = "chat.refresh_threads"
    bl_label = "Refresh Threads"
    bl_description = "Fetch the latest list of conversation threads"

    def execute(self, _context):
        """Execute the thread refresh operation."""
        global g_last_thread_fetch_time
        now = time.time()
        if now - g_last_thread_fetch_time > THREAD_FETCH_DEBOUNCE_SECONDS:
            g_last_thread_fetch_time = now
            log("Manual thread list refresh triggered...", "INFO")
            threading.Thread(target=fetch_threads_and_update_cache, daemon=True).start()
        else:
            log("Debouncing manual thread refresh.", "DEBUG")
            self.report(
                {'INFO'},
                f"Please wait {int(THREAD_FETCH_DEBOUNCE_SECONDS - (now - g_last_thread_fetch_time) + 1)}s to refresh again.")
        return {'FINISHED'}

# --- Operator to Delete a Thread --- #


class CHAT_OT_delete_thread(Operator):
    """Delete the selected chat thread locally."""
    bl_idname = "chat.delete_thread"
    bl_label = "Delete Chat"
    bl_description = "Delete the selected chat thread locally."

    thread_id: StringProperty(name="Thread ID", description="ID of the chat thread to delete")

    def execute(self, _context):
        """Execute the delete operation."""
        log(f"Deleting thread: {self.thread_id}", "INFO")
        cache_manager_instance.delete_thread(self.thread_id)
        return {'FINISHED'}

# --- Helper Functions for Async Loading --- #


def fetch_threads_and_update_cache():
    """Fetch thread list from API, update cache, and trigger UI update via property change."""
    try:
        log("Background fetch: Getting threads from API...", "INFO")
        response = api.get_threads()
        threads = response.get("threads", [])
        log(f"Background fetch: Received {len(threads)} threads from API.", "INFO")
        cache_manager_instance.update_thread_list(threads)
        log("Background fetch: Cache updated with thread list.", "INFO")

        # Schedule UI update via timer - NO MORE bpy.ops.sidebar.refresh()
        def update_ui():
            log("Background fetch: Updating Scene property to trigger UI refresh.", "DEBUG")
            try:
                # The EnumProperty's 'items' callback (get_available_threads) will be re-evaluated
                # automatically by Blender when the panel redraws. We just need to ensure the
                # active thread logic runs if the active thread ID might have changed (e.g., deleted).
                current_active = bpy.context.scene.ai_active_thread
                cached_active = cache_manager_instance.get_active_thread()

                target_active_thread = cached_active if cached_active is not None else "_NONE_"
                log(
                    f"Background fetch: Current active = {current_active}, Cached active = {cached_active}, Target = {target_active_thread}",
                    "DEBUG")

                # Load history for the initial active thread (if needed)
                active_thread_id = cache_manager_instance.get_active_thread()
                if active_thread_id and active_thread_id != "_NONE_":
                    # Check if history needs loading (moved logic from fetch_threads_and_update_cache)
                    if not cache_manager_instance.get_thread_history(active_thread_id):
                        log(f"Initial load: Triggering history load for active thread {active_thread_id}", "INFO")
                        load_thread_history(active_thread_id)  # Call history loading
                    else:
                        # If history exists, ensure UI is populated if it wasn't already
                        if not bpy.context.scene.chat_messages:
                            log(f"Initial load: Populating UI from existing cache for {active_thread_id}", "INFO")
                            schedule_populate_ui_from_cache(active_thread_id)

                # If active thread was deleted and reset in cache, update scene property
                if current_active != cached_active:
                    log(
                        f"Active thread changed in cache ('{current_active}' -> '{cached_active}'). Updating scene property.",
                        "INFO")
                    # Use the validated target_active_thread
                    bpy.context.scene.ai_active_thread = target_active_thread  # Triggers update_active_thread callback

            except Exception as e:
                log(f"Error during UI property update/history load trigger: {e}", "ERROR")
            return None  # Timer runs once

        bpy.app.timers.register(update_ui, first_interval=0.0)

    except Exception as e:
        log(f"Error fetching or updating threads in background: {e}", "ERROR")
        # Optionally, schedule UI to show an error message (without forced refresh)

        def show_error_in_ui():
            try:
                # Add status message or similar without forcing full refresh
                pass  # Error logging is likely sufficient
            except Exception:
                pass
            return None
        bpy.app.timers.register(show_error_in_ui, first_interval=0.0)


def load_thread_history(thread_id: str):
    """Decide whether to fetch history from API or use cache, then schedule UI population."""
    global g_is_loading_history
    if not thread_id or thread_id == "_NONE_":  # Added check for placeholder
        log(f"load_thread_history called with invalid thread_id: {thread_id}", "WARNING")
        return

    try:
        # Check cache first
        thread_data = cache_manager_instance.get_thread_data(thread_id)  # Get full thread data
        cached_history = thread_data.get("history", [])
        last_accessed_str = thread_data.get("last_accessed_at")
        needs_api_fetch = True  # Default to fetching

        # Simple check: If history exists, don't fetch unless manually requested?
        # More robust: Check timestamp (Keep existing logic for now)
        if cached_history and last_accessed_str:
            try:
                last_accessed_dt = datetime.datetime.fromisoformat(last_accessed_str.replace('Z', '+00:00'))
                now_utc = datetime.datetime.now(datetime.timezone.utc)
                cache_age = now_utc - last_accessed_dt
                # Shorten cache validity? Maybe 1 minute?
                if cache_age < datetime.timedelta(minutes=1):  # Reduced cache age check
                    log(f"Using cached history for {thread_id} (age: {cache_age}).", "INFO")
                    needs_api_fetch = False
                else:
                    log(f"Cached history for {thread_id} is stale (age: {cache_age}). Fetching from API.", "INFO")
            except (ValueError, TypeError):
                log(
                    f"Could not parse last_accessed_at timestamp '{last_accessed_str}' for {thread_id}. Fetching from API.",
                    "WARNING")
        elif not cached_history:
            log(f"No cached history found for {thread_id}. Fetching from API.", "INFO")
        else:  # History exists but no timestamp
            log(f"Cached history for {thread_id} has no timestamp. Fetching from API.", "INFO")

        # --- Trigger appropriate action --- #
        if needs_api_fetch:
            if not g_is_loading_history:
                g_is_loading_history = True
                # NO bpy.ops.sidebar.refresh() here - let draw method show indicator
                log(f"Starting background API history fetch for {thread_id}...", "INFO")
                # Run API fetch in background
                threading.Thread(target=fetch_history_from_api, args=(thread_id,), daemon=True).start()
            else:
                log("Already fetching history, skipping redundant API call trigger.", "DEBUG")
        else:
            # Use cache: Schedule UI population directly
            log(f"Scheduling UI population from cache for {thread_id}...", "INFO")
            # Ensure loading state is false if we use cache (should be already, but safe)
            if g_is_loading_history:
                g_is_loading_history = False
            # NO bpy.ops.sidebar.refresh() here
            schedule_populate_ui_from_cache(thread_id)

    except Exception as e:
        log(f"Error in load_thread_history for {thread_id}: {e}", "ERROR")
        g_is_loading_history = False  # Ensure loading state is cleared on error
        # NO bpy.ops.sidebar.refresh() here


def fetch_history_from_api(thread_id: str):
    """Fetch history for a thread from API, update cache, and schedule UI population."""
    global g_is_loading_history
    try:
        # 2. Fetch from API
        log(f"DEBUG: Fetching history from API for {thread_id}...", "DEBUG")
        response = api.get_thread_history(thread_id)
        # Log raw response
        log(f"DEBUG: Raw API response for {thread_id}: {json.dumps(response)}", "DEBUG")

        history_data = response.get("history", [])
        log(f"Background API fetch: Received {len(history_data)} messages for {thread_id}.", "INFO")

        # 3. Update Cache
        cache_manager_instance.update_thread_history(thread_id, history_data)
        log(f"Background API fetch: Cache updated for {thread_id}.", "INFO")

        # 4. Schedule UI Update (using the now cached data)
        schedule_populate_ui_from_cache(thread_id)  # This will clear g_is_loading_history

    except Exception as e:
        log(f"Error fetching history from API for {thread_id}: {e}", "ERROR")
        # Schedule UI update to show error?

        def show_error_ui():
            global g_is_loading_history
            log(f"Setting g_is_loading_history to False due to API fetch error for {thread_id}", "DEBUG")
            g_is_loading_history = False  # Clear loading flag on error
            # Add an error message to the chat? Or just log?
            try:
                scene = bpy.context.scene
                # Check if the failed thread is still the active one before showing error
                if cache_manager_instance.get_active_thread() == thread_id:
                    error_msg = scene.chat_messages.add()
                    error_msg.text = f"[Error] Failed to load history for {thread_id[:8]}..."
                    error_msg.sender_type = "assistant"
            except Exception as ui_err:
                log(f"Error trying to show history fetch error in UI: {ui_err}", "ERROR")
            # NO bpy.ops.sidebar.refresh() here
            return None
        bpy.app.timers.register(show_error_ui, first_interval=0.0)

    # finally: # Removed finally block as schedule_populate_ui_from_cache handles the flag now
    #     pass


def schedule_populate_ui_from_cache(thread_id: str):
    """Schedules the populate_ui_from_cache function via Blender timer,
       only if a message is not currently being processed.
       Also clears the g_is_loading_history flag before scheduling.
    """
    global g_is_processing_message, g_is_loading_history

    # Clear loading flag *before* checking if message is processing
    # This ensures the flag is cleared even if population is skipped temporarily
    if g_is_loading_history:
        log(f"Clearing g_is_loading_history before scheduling/skipping populate UI for {thread_id}", "DEBUG")
        g_is_loading_history = False

    if g_is_processing_message:
        log("Skipping UI population from cache: Message processing is active.", "DEBUG")
        # NO bpy.ops.sidebar.refresh() here
        return

    bpy.app.timers.register(lambda: populate_ui_from_cache(thread_id), first_interval=0.0)


def populate_ui_from_cache(thread_id: str):
    """Retrieve history from cache and populate the UI message list."""
    # Removed g_is_loading_history handling from here, moved to schedule function
    log(f"Populating UI from cache for thread {thread_id}", "DEBUG")
    scene = bpy.context.scene

    # --- Safety Check: Ensure this is still the active thread --- #
    # Prevents race condition where user selects another thread while this loads
    if thread_id != cache_manager_instance.get_active_thread():
        log(f"UI population skipped: Active thread changed from {thread_id} during load.", "INFO")
        # Loading flag should already be false from schedule_populate_ui_from_cache
        return None  # Timer runs once

    scene.chat_messages.clear()  # Clear existing UI messages first

    try:
        # --- Get History from Cache --- #
        cached_history = cache_manager_instance.get_thread_history(thread_id)
        log(f"Retrieved {len(cached_history)} messages from cache for UI ({thread_id}).", "DEBUG")

        # --- Populate UI List --- #
        for msg_data in cached_history:
            new_msg = scene.chat_messages.add()
            # Cache uses sender_type, text, checkpoint_id, tool_call_info (INCORRECT ASSUMPTION)
            # Cache *should* use type, content, checkpoint_id, etc. matching API history

            # Log the data being processed from cache
            log(f"DEBUG: Populating UI item from cached data: {json.dumps(msg_data)}", "DEBUG")

            # --- Read fields using API keys --- #
            message_type = msg_data.get("type")  # Read 'type' from cache
            text_content = msg_data.get("content", "")  # Read 'content' from cache
            checkpoint_id = msg_data.get("checkpoint_id", "")
            # Tool call info might be stored differently depending on how cache was updated
            # Let's try to handle both potential structures (from API history vs live add)
            tool_call_info_str = msg_data.get("tool_call_info")  # From live add
            tool_calls_list = msg_data.get("tool_calls")  # From API history
            tool_call_id_ref = msg_data.get("tool_call_id")  # From API history tool result

            # --- Initialize variable --- #
            tool_info_to_store = None

            # Determine the correct sender_type for the UI display
            ui_sender_type = "unknown"  # Default value
            # --- Add Safeguard: Check if message_type is None --- #
            if message_type is None:
                log(f"WARNING: Cached message data has None type! Data: {json.dumps(msg_data)}", "WARNING")
                message_type = "unknown"  # Assign default string

            # --- Map 'type' (from cache) to 'ui_sender_type' --- #
            if message_type in ["user", "human"]:
                ui_sender_type = "user"
            elif message_type == "ai":
                ui_sender_type = "assistant"
                # Check if it was an AI message that *only* contained tool calls
                if not text_content and (tool_calls_list or tool_call_info_str):
                    ui_sender_type = "tool_call"
                    # Attempt to extract tool name for display
                    tool_name = "unknown tool"
                    try:
                        if tool_calls_list and isinstance(tool_calls_list, list) and tool_calls_list:
                            first_tool_call = tool_calls_list[0]
                            tool_name = first_tool_call.get("name", tool_name)
                            # Store standard format in tool_call_info for UI button
                            tool_info_to_store = json.dumps({
                                "id": first_tool_call.get("id"),
                                "name": tool_name,
                                "args": first_tool_call.get("args")
                            })
                        elif tool_call_info_str:  # Handle format from live add
                            tool_call_data = json.loads(tool_call_info_str)
                            tool_name = tool_call_data.get("name", tool_name)
                            tool_info_to_store = tool_call_info_str  # Already serialized

                        text_content = f"Requested tool: {tool_name}"
                    except (json.JSONDecodeError, TypeError, IndexError, KeyError):
                        log(f"Error extracting tool call details for UI: {msg_data}", "WARNING")
                        text_content = "(Requesting tool execution...)"
            elif message_type == "tool_call":  # Explicit tool call step from message_handler (if stored)
                ui_sender_type = "tool_call"
                # text_content should already be set correctly by message_handler status update
                # Ensure tool_call_info_str is populated if possible
                if not tool_call_info_str and text_content.startswith("Executing tool:"):
                    # Try to reconstruct basic info? Difficult.
                    pass
            elif message_type == "tool":  # From API history
                ui_sender_type = "tool_result"
                # text_content should be the result
                # Try to store tool_call_id if available for linking
                if tool_call_id_ref and not tool_call_info_str:
                    try:
                        tool_info_to_store = json.dumps({"id": tool_call_id_ref})
                    except Exception as e:
                        log(f"Error storing tool_call_id ref for UI: {e}", "WARNING")
            elif message_type == "tool_result":  # From live add (should be deprecated)
                ui_sender_type = "tool_result"

            else:
                ui_sender_type = message_type  # Keep original if unknown

            # --- Assign to UI Property (Now safer) --- #
            new_msg.sender_type = ui_sender_type
            new_msg.text = text_content
            new_msg.checkpoint_id = checkpoint_id
            if tool_info_to_store:  # Use the determined tool info string
                new_msg.tool_call_info = tool_info_to_store
            elif tool_call_info_str:  # Fallback if only live add info exists
                new_msg.tool_call_info = tool_call_info_str

        bpy.ops.sidebar.refresh()
        log(f"UI populated with {len(scene.chat_messages)} messages for {thread_id}.", "INFO")

    except Exception as e:
        log(f"Error populating UI from cache for {thread_id}: {e}", "ERROR")
        # Add an error message to the UI?
        scene.chat_messages.clear()
        error_msg = scene.chat_messages.add()
        error_msg.text = f"[Error] Failed display history for {thread_id[:8]}..."
        error_msg.sender_type = "assistant"

    # finally: # Removed finally block
        # NO bpy.ops.sidebar.refresh()

    return None  # Make timer run only once


# --- EnumProperty Callbacks --- #


def get_available_threads(_self, _context):
    """Callback function for the ai_active_thread EnumProperty items."""
    # Use cache manager to get sorted list
    items = cache_manager_instance.get_thread_list_sorted()
    # Prepend a "New Chat" option?
    # items.insert(0, ("_NEW_CHAT_", "+ New Chat", "Start a fresh conversation"))
    if not items:
        return [("_NONE_", "No Chats Available", "No past conversations found or loaded yet")]
    return items


def fetch_models():
    global g_models, g_models_fetched
    try:
        response = api.get_models()
        fetched_model_ids = response.get("models", [])  # Assuming API returns a list of model ID strings
        log(f"Fetched {len(fetched_model_ids)} model IDs: {fetched_model_ids}", "INFO")

        # --- Update g_models for EnumProperty ---
        new_models_list = []
        if fetched_model_ids:
            for model_id in fetched_model_ids:
                # Format: (identifier, name, description)
                # Using model_id for all three for simplicity, adjust if API provides more info
                new_models_list.append((model_id, model_id, f"Use the {model_id} model"))
        else:
            # Provide a default/fallback if fetch fails or returns empty
            new_models_list = [("default", "Default", "No models available or fetch failed")]

        g_models = new_models_list
        g_models_fetched = True
        log(f"Updated g_models: {g_models}", "DEBUG")

        # Update the ai_models property with the fetched models
        def update_ui():
            try:
                scene = bpy.context.scene
                # Check if models were loaded and if the current selection is still the initial default
                if g_models and scene.ai_model_selected == "default":  # Make sure "default" matches the StringProperty default
                    first_model_id = g_models[0][0]  # Get the identifier of the first model
                    scene.ai_model_selected = first_model_id
                    log(f"Defaulting selected model to the first available: {first_model_id}", "INFO")

                # No need to update a collection anymore.
                # Just trigger a general UI refresh so the EnumProperty redraws.
                log("Scheduling UI refresh after fetching models.", "DEBUG")
                bpy.ops.sidebar.refresh()
            except Exception as e:
                log(f"Error triggering UI refresh after fetching models: {e}", "ERROR")
            return None

        bpy.app.timers.register(update_ui, first_interval=0.0)

    except Exception as e:
        log(f"Error fetching models: {e}", "ERROR")
        # Keep a default value in g_models if fetch fails
        g_models = [("default", "Error", "Failed to fetch models")]
        g_models_fetched = True  # Mark as fetched even on error to stop constant loading indicators


def update_active_thread(_self, context):
    """Callback function when the ai_active_thread EnumProperty is updated."""
    global g_is_loading_history
    selected_thread_id = context.scene.ai_active_thread

    # Handle placeholder case if we add one
    if selected_thread_id == "_NONE_":
        # Clear history when no thread is selected
        context.scene.chat_messages.clear()
        context.scene.chat_input = ""
        return

    log(f"Active thread selection changed to: {selected_thread_id}", "INFO")
    cache_manager_instance.set_active_thread(selected_thread_id)  # Update cache

    # Clear UI and load history in background
    context.scene.chat_messages.clear()
    context.scene.chat_input = ""  # Clear input field too

    # --- Check if history is empty in cache --- #
    cached_history = cache_manager_instance.get_thread_history(selected_thread_id)
    if not cached_history:
        log(f"Skipping history load for thread {selected_thread_id}: Cache is empty (likely new thread).", "INFO")
        # Ensure loading indicator is off if it was somehow on
        if g_is_loading_history:
            g_is_loading_history = False
            bpy.ops.sidebar.refresh()
        return  # Skip loading history
    # --- End Check --- #

    if not g_is_loading_history:
        load_thread_history(selected_thread_id)  # Call the refactored loading function
    else:
        log("Skipping history fetch trigger, already loading.", "DEBUG")
# --- Authentication Operators --- #


class AUTH_OT_login(Operator):
    """Initiates the Cognito login process"""
    bl_idname = "auth.login"
    bl_label = "Login"
    bl_description = "Sign in using AWS Cognito"

    _timer = None
    _login_thread = None
    _login_future = None

    @classmethod
    def poll(cls, _context):
        """Check if the operator can be executed (only if not logged in)."""
        # Only allow login if not already authenticated
        auth_manager = get_auth_manager()
        return not auth_manager.is_authenticated()

    def modal(self, context, event):
        """Handle modal events, checking timer for login completion."""
        if event.type == 'TIMER':
            if self._login_future and self._login_future.done():
                try:
                    success = self._login_future.result()
                    if success:
                        self.report({'INFO'}, "Login Successful!")
                    else:
                        self.report({'ERROR'}, "Login failed. Check logs.")
                except Exception as e:
                    log(f"Error getting login result: {e}", "ERROR")
                    self.report({'ERROR'}, "Login failed. Check logs.")
                finally:
                    # Trigger UI refresh
                    bpy.ops.sidebar.refresh()
                    self.cancel(context)
                    # This works for now and it doesnt cause issues. To refactor in the future
                    # pylint: disable=W0134, W0150
                    return {'FINISHED'}
            # Keep modal running if thread is still active
            return {'PASS_THROUGH'}

        return {'PASS_THROUGH'}

    def execute(self, context):
        """Start the background login process and enter modal mode."""
        auth_manager = get_auth_manager()

        # Run initiate_login in a background thread
        loop = asyncio.new_event_loop()

        def run_login_task():
            asyncio.set_event_loop(loop)
            # Directly run the potentially blocking function in the thread
            # Note: initiate_login is synchronous, but involves waiting
            login_result = auth_manager.initiate_login()
            # Use call_soon_threadsafe if initiate_login were async, but it's not
            # Here we just need to get the result back
            loop.call_soon_threadsafe(self._login_future.set_result, login_result)
            # Stop the loop once the task is done
            loop.call_soon_threadsafe(loop.stop)

        self._login_future = asyncio.Future()
        self._login_thread = threading.Thread(target=run_login_task, daemon=True)
        self._login_thread.start()

        # Start modal timer to check for completion
        self._timer = context.window_manager.event_timer_add(0.2, window=context.window)
        context.window_manager.modal_handler_add(self)
        self.report({'INFO'}, "Starting login process... Check browser.")
        return {'RUNNING_MODAL'}

    def cancel(self, context):
        """Clean up the modal timer."""
        if self._timer:
            context.window_manager.event_timer_remove(self._timer)
            self._timer = None
        return {'CANCELLED'}


class AUTH_OT_logout(Operator):
    """Logs the user out"""
    bl_idname = "auth.logout"
    bl_label = "Logout"
    bl_description = "Sign out and clear local session"

    @classmethod
    def poll(cls, _context):
        """Check if the operator can be executed (only if logged in)."""
        # Only allow logout if authenticated
        auth_manager = get_auth_manager()
        return auth_manager.is_authenticated()

    def execute(self, _context):
        """Execute the logout operation."""
        auth_manager = get_auth_manager()
        auth_manager.logout()
        self.report({'INFO'}, "Logged out.")
        # Trigger UI refresh
        bpy.ops.sidebar.refresh()
        return {'FINISHED'}


# --- New Model Selection Operator and Menu --- #
class MODEL_OT_set_selected(bpy.types.Operator):
    """Sets the active AI model ID"""
    bl_idname = "model.set_selected"
    bl_label = "Select AI Model"
    bl_options = {'REGISTER', 'INTERNAL'}  # Internal prevents it showing in search

    model_id: StringProperty(name="Model ID")

    def execute(self, context):
        if self.model_id:
            context.scene.ai_model_selected = self.model_id
            log(f"Set selected model to: {self.model_id}", "INFO")
            bpy.ops.sidebar.refresh()
        return {'FINISHED'}


class MODEL_MT_select_menu(bpy.types.Menu):
    """Menu to select the active AI model"""
    bl_idname = "MODEL_MT_select_menu"
    bl_label = "Select AI Model"

    def draw(self, context):  # Corrected signature (context needed for scene access potentially)
        layout = self.layout
        layout.operator_context = 'INVOKE_DEFAULT'  # Ensures operator properties are set
        scene = context.scene  # Get scene context

        global g_models, g_models_fetched

        if not g_models_fetched:
            layout.label(text="Loading models...")
        elif not g_models:
            layout.label(text="No models available.")
        else:
            # Draw an operator for each model
            for identifier, name, description in g_models:
                # Determine icon based on selection
                icon_value = 'CHECKMARK' if scene.ai_model_selected == identifier else 'NONE'
                # Pass icon directly to layout.operator()
                op = layout.operator(
                    MODEL_OT_set_selected.bl_idname,
                    text=name,
                    icon=icon_value  # Pass icon here
                )
                op.model_id = identifier  # Set operator property
                # Remove setting icon after creation
                # if scene.ai_model_selected == identifier:
                #     op.icon = 'CHECKMARK'
                # else:
                #     op.icon = 'NONE' # Explicitly no icon otherwise


# --- End New Model Selection --- #
# --- Added: Async Subscription Check --- #


def check_subscription_status_async():
    """Initiates the subscription status check in a background thread."""
    global g_subscription_status, g_is_checking_subscription

    if g_is_checking_subscription:
        log("Subscription check already in progress. Skipping.", "DEBUG")
        return

    g_is_checking_subscription = True
    g_subscription_status = 'checking'  # Indicate checking state
    log("Starting background subscription status check...", "INFO")

    async def get_status_async():
        try:
            # Use asyncio.to_thread for the blocking API call
            response = await asyncio.to_thread(api.get_subscription_status)
            log(f"Subscription status API response: {response}", "DEBUG")
            # Default to 'error' if key missing or response is not dict
            if isinstance(response, dict):
                return response.get('status', 'error')
            else:
                log(f"Unexpected API response format for subscription status: {response}", "WARNING")
                return 'error'
        except Exception as e:
            log(f"Error fetching subscription status: {e}", "ERROR")
            return 'error'

    def run_check_task():
        global g_subscription_status, g_is_checking_subscription  # Allow modification
        # Ensure a unique loop for this thread
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        status_result = 'error'  # Default in case of early exit
        try:
            status_result = loop.run_until_complete(get_status_async())
            log(f"Subscription status result from task: {status_result}", "DEBUG")  # Log result

            # --- Directly update global state from background thread ---
            # Validate status
            valid_statuses = ['active', 'trialing', 'inactive', 'canceled', 'past_due', 'error', 'checking', None]
            if status_result not in valid_statuses:
                log(f"Received unexpected subscription status '{status_result}'. Treating as 'error'.", "WARNING")
                status_result = 'error'

            log(f"Updating global subscription status directly to: {status_result}", "INFO")
            g_subscription_status = status_result
            g_is_checking_subscription = False  # Mark check as complete (or failed)
            # --- End state update ---

            # --- Schedule final UI refresh on main thread ---
            bpy.ops.sidebar.refresh()
            # --- End schedule refreh ---

        except Exception as e:
            log(f"Exception running subscription check task: {e}", "ERROR")
            # Ensure state is updated even on task error
            g_subscription_status = 'error'
            g_is_checking_subscription = False
            # Still try to refresh UI to show the error state

            bpy.ops.sidebar.refresh()

        finally:
            # Close the loop when the task is done or fails
            loop.close()
            log("Background check task asyncio loop closed.", "DEBUG")

    threading.Thread(target=run_check_task, daemon=True).start()


class SUBSCRIPTION_OT_upgrade(Operator):
    """Initiates the Stripe Checkout process or opens the cached URL."""
    bl_idname = "subscription.upgrade"
    bl_label = "Upgrade to Pro"
    bl_description = "Open Stripe Checkout to upgrade your subscription"

    _timer = None
    _task_future = None
    # Use a class variable to track API call in progress specifically
    _is_calling_api = False

    @classmethod
    def poll(cls, _context):
        # Only check authentication status now
        auth_manager = get_auth_manager()
        # Allow clicking even if API call is in progress, execute will handle it
        return auth_manager.is_authenticated()

    def modal(self, context, event):
        global g_last_checkout_url  # Allow modification

        if event.type == 'TIMER':
            # Check if the API call future exists and is done
            if self._task_future and self._task_future.done():
                result = None
                try:
                    result = self._task_future.result()
                    log(f"Checkout API call result: {result}", "DEBUG")  # Log the raw result

                    if isinstance(result, dict) and 'url' in result:
                        checkout_url = result['url']
                        # Validate URL format before proceeding
                        if checkout_url and isinstance(checkout_url, str) and checkout_url.startswith("http"):
                            log(f"Received valid checkout URL: {checkout_url}", "INFO")

                            # --- Store the valid URL ---
                            g_last_checkout_url = checkout_url
                            log(f"Stored checkout URL globally: {g_last_checkout_url}", "DEBUG")

                            # --- Set Scene property for UI ---
                            try:
                                if context.scene:
                                    context.scene.checkout_url_display = checkout_url
                                    log(f"Set Scene.checkout_url_display to: {checkout_url}", "DEBUG")
                                else:
                                    log("Context scene not available to set checkout_url_display", "WARNING")
                            except Exception as set_prop_err:
                                log(f"Error setting Scene.checkout_url_display: {set_prop_err}", "ERROR")

                            # --- Attempt to open URL ---
                            try:
                                log(f"Attempting to open URL: {checkout_url}", "INFO")
                                bpy.ops.wm.url_open(url=checkout_url)
                                self.report({'INFO'}, "Checkout page opened in browser.")
                                # Also report that it's cached for next time?
                                # self.report({'INFO'}, "Link cached. Click again to reopen.")
                            except Exception as open_err:
                                log(f"Error opening URL automatically: {open_err}", "ERROR")
                                self.report(
                                    {'ERROR'}, f"Could not open URL automatically. Please copy link. Error: {open_err}")

                            # --- Trigger UI refresh to show the URL ---
                            try:
                                bpy.ops.sidebar.refresh()
                            except Exception as refresh_err:
                                log(f"Error refreshing sidebar after getting URL: {refresh_err}", "WARNING")

                        else:  # Handle invalid URL format received from API
                            log(f"Invalid checkout URL received from API: {checkout_url}", "ERROR")
                            g_last_checkout_url = None  # Clear any potentially bad cached URL
                            self.report({'ERROR'}, "Received invalid checkout URL from server.")
                            try:
                                bpy.ops.sidebar.refresh()  # Refresh to clear potential old URL
                            except Exception:
                                pass

                    else:  # Handle API error or unexpected response structure
                        error_msg = "Failed to create checkout session."
                        if isinstance(result, dict):
                            error_msg = result.get('error', result.get('message', error_msg))
                        log(f"API error during checkout session creation: {error_msg}", "ERROR")
                        g_last_checkout_url = None  # Clear cache on error
                        self.report({'ERROR'}, error_msg)
                        try:
                            bpy.ops.sidebar.refresh()  # Refresh to clear potential old URL
                        except Exception:
                            pass

                except Exception as e:
                    log(f"Error processing checkout session result future: {e}", "ERROR")
                    g_last_checkout_url = None  # Clear cache on general error
                    self.report({'ERROR'}, "An error occurred processing checkout response. Check logs.")
                    try:
                        bpy.ops.sidebar.refresh()
                    except Exception:
                        pass
                finally:
                    # --- Important: Reset API calling flag --- #
                    SUBSCRIPTION_OT_upgrade._is_calling_api = False
                    log("Resetting _is_calling_api flag.", "DEBUG")
                    # Clean up timer and future, exit modal
                    return self.cancel(context)

            # Keep modal running if task not done or timer still active
            return {'PASS_THROUGH'}

        elif event.type in {'RIGHTMOUSE', 'ESC'}:
            log("Checkout API call cancelled by user.", "INFO")
            # Reset API calling flag if cancelled during API call
            if SUBSCRIPTION_OT_upgrade._is_calling_api:
                SUBSCRIPTION_OT_upgrade._is_calling_api = False
                log("Resetting _is_calling_api flag due to user cancel.", "DEBUG")
            return self.cancel(context)

        return {'PASS_THROUGH'}

    def execute(self, context):
        global g_last_checkout_url  # Access global cache

        log(f"Upgrade button execute. Cached URL: {g_last_checkout_url}", "DEBUG")

        # --- 1. Check for cached URL ---
        if g_last_checkout_url and isinstance(g_last_checkout_url, str) and g_last_checkout_url.startswith("http"):
            log(f"Using cached checkout URL: {g_last_checkout_url}", "INFO")
            try:
                bpy.ops.wm.url_open(url=g_last_checkout_url)
                self.report({'INFO'}, "Reopening cached checkout link.")
            except Exception as open_err:
                log(f"Error opening cached URL: {open_err}", "ERROR")
                self.report({'ERROR'}, f"Could not open cached URL. Error: {open_err}")
            # Refresh UI in case it wasn't showing the URL correctly
            try:
                bpy.ops.sidebar.refresh()
            except Exception:
                pass
            return {'FINISHED'}  # Exit immediately, no modal needed

        # --- 2. Check if API call is already in progress ---
        # Prevent starting a *new* API call if one is running
        if SUBSCRIPTION_OT_upgrade._is_calling_api:
            self.report({'INFO'}, "Checkout creation already in progress...")
            log("Skipping new API call, one is already running.", "DEBUG")
            return {'CANCELLED'}  # Don't allow stacking API calls

        # --- 3. Initiate NEW API call ---
        log("No valid cached URL or API call in progress. Starting new API call.", "INFO")
        SUBSCRIPTION_OT_upgrade._is_calling_api = True  # Set flag *before* starting async stuff
        self.report({'INFO'}, "Creating checkout session...")

        # --- Auth Check (redundant with poll, but safe) ---
        auth_manager = get_auth_manager()
        if not auth_manager.is_authenticated():
            self.report({'ERROR'}, "Authentication required.")
            SUBSCRIPTION_OT_upgrade._is_calling_api = False  # Reset flag
            return {'CANCELLED'}
        # --- End Auth Check ---

        async def create_session_async():
            """Calls the API within an async context using to_thread."""
            # ... (async API call logic remains the same) ...
            try:
                log("Calling api.create_checkout_session via asyncio.to_thread", "DEBUG")
                response = await asyncio.to_thread(api.create_checkout_session)
                log(f"API response for checkout session: {response}", "DEBUG")
                return response
            except Exception as e:
                import traceback
                log(f"Exception in create_session_async: {e}\n{traceback.format_exc()}", "ERROR")
                return {"error": f"Failed to communicate with API: {e}"}

        # --- Start Background Task and Modal ---
        loop = asyncio.new_event_loop()
        self._task_future = asyncio.Future(loop=loop)

        def run_task():
            """Runs the asyncio event loop for the background task."""
            asyncio.set_event_loop(loop)
            result = {"error": "Task failed before execution"}  # Default error result
            task_exception = None
            try:
                # Run the async function and wait for its result
                result = loop.run_until_complete(create_session_async())
                log(f"Async task completed. Result: {result}", "DEBUG")

                # --- Set Future and Close Loop on Success (Inside Try) ---
                try:
                    if self._task_future and not self._task_future.done():
                        log(f"Attempting to set future result directly after success: {result}", "DEBUG")
                        self._task_future.set_result(result)
                    elif not self._task_future:
                        log("Future object is None after success.", "WARNING")
                    # else: Future already done? Unlikely here.
                except Exception as set_result_err:
                    log(f"Error calling set_result directly after success: {set_result_err}", "ERROR")
                    # If setting the success result fails, try setting an error result
                    if self._task_future and not self._task_future.done():
                        try:
                            self._task_future.set_result({"error": f"Failed to set success result: {set_result_err}"})
                        except Exception:
                            pass  # Nested error handling

                try:
                    log("Closing the loop after success.", "DEBUG")
                    loop.close()
                    if not loop.is_closed():
                        log("Loop did not report as closed immediately after close().", "WARNING")
                except Exception as loop_close_err:
                    log(f"Error closing loop after success: {loop_close_err}", "WARNING")
                # --- End Set Future and Close Loop ---

            except Exception as e:
                task_exception = e
                log(f"Exception caught during run_until_complete in run_task: {e}", "ERROR")
                # Ensure result reflects the error if the task failed internally
                if not (isinstance(result, dict) and 'error' in result):
                    result = {"error": f"Task execution failed: {e}"}

                # --- Set Future and Close Loop on Exception (Inside Except) ---
                try:
                    if self._task_future and not self._task_future.done():
                        log(f"Attempting to set future error result directly after exception: {result}", "DEBUG")
                        self._task_future.set_result(result)  # Set the error result
                    elif not self._task_future:
                        log("Future object is None after exception.", "WARNING")
                    # else: Future already done?
                except Exception as set_result_err:
                    log(f"Error calling set_result directly after exception: {set_result_err}", "ERROR")
                    # Cannot meaningfully set another error if this fails

                try:
                    log("Closing the loop after exception.", "DEBUG")
                    # Check if loop is running before closing, might not be if setup failed
                    if not loop.is_closed():
                        loop.close()
                        if not loop.is_closed():
                            log("Loop did not report as closed immediately after close() in except block.", "WARNING")
                    else:
                        log("Loop was already closed in except block.", "DEBUG")
                except Exception as loop_close_err:
                    log(f"Error closing loop after exception: {loop_close_err}", "WARNING")
                # --- End Set Future and Close Loop ---

            # --- Removed Finally Block ---
            # finally:
                # ... (rest of the finally logic removed)

            # Log final status
            if task_exception:
                log(f"Task run_task finished, captured exception: {task_exception}", "ERROR")
            else:
                log("Task run_task finished successfully (inside try).", "DEBUG")

        bg_thread = threading.Thread(target=run_task, daemon=True)
        bg_thread.start()

        wm = context.window_manager
        self._timer = wm.event_timer_add(0.2, window=context.window)
        wm.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def cancel(self, context):
        """Cleans up the operator state."""
        # --- Reset API calling flag on cancel --- #
        # Check if this instance was the one making the call before resetting
        # Note: This relies on modal preventing simultaneous instances,
        # but _is_calling_api should be reset reliably when modal exits.
        # If the modal is cancelled *before* the API call finishes, reset the flag.
        if SUBSCRIPTION_OT_upgrade._is_calling_api and self._task_future and not self._task_future.done():
            log("Resetting _is_calling_api flag during cancel (API call likely still running).", "DEBUG")
            SUBSCRIPTION_OT_upgrade._is_calling_api = False

        log("Cancelling SUBSCRIPTION_OT_upgrade modal.", "DEBUG")
        if self._timer:
            try:
                context.window_manager.event_timer_remove(self._timer)
            except Exception as e:
                log(f"Error removing timer: {e}", "WARNING")
            self._timer = None

        # Cancel the future if it's still running
        if self._task_future and not self._task_future.done():
            self._task_future.cancel()
            log("Cancelled checkout creation future.", "DEBUG")
        self._task_future = None
        return {'CANCELLED'}

# --- End Added --- #

# --- Added: Manual Subscription Check Operator --- #


class AUTH_OT_check_subscription(Operator):
    """Manually triggers a check of the user's subscription status."""
    bl_idname = "auth.check_subscription"
    bl_label = "Check Subscription Status"
    bl_description = "Refresh the current subscription status from the backend"

    show_message: BoolProperty(default=False, options={'HIDDEN'})

    @classmethod
    def poll(cls, _context):
        # Only allow if logged in and not already checking
        global g_is_checking_subscription
        auth_manager = get_auth_manager()
        return auth_manager.is_authenticated() and not g_is_checking_subscription

    def execute(self, context):
        log("Manual subscription check triggered.", "INFO")
        if self.show_message:
            self.report({'INFO'}, "Checking subscription status...")
        check_subscription_status_async()
        return {'FINISHED'}


class CLIPBOARD_OT_copy_text(Operator):
    """Copies the provided text to the system clipboard."""
    bl_idname = "clipboard.copy_text"
    bl_label = "Copy Text"
    bl_description = "Copy the specified text to the clipboard"
    bl_options = {'INTERNAL'}  # Hide from search

    text_to_copy: StringProperty(
        name="Text to Copy",
        description="The text value to be placed onto the clipboard",
        default=""
    )

    def execute(self, context):
        if self.text_to_copy:
            context.window_manager.clipboard = self.text_to_copy
            log(f"Copied text to clipboard (length: {len(self.text_to_copy)})", "INFO")
            self.report({'INFO'}, "Copied to clipboard!")
        else:
            log("Copy operator called with empty text.", "WARNING")
            self.report({'WARNING'}, "Nothing to copy.")
        return {'FINISHED'}


# Registration
classes = (
    CHAT_UL_message_item,
    CHAT_UL_messages,
    CHAT_OT_select_checkpoint,
    CHAT_OT_deselect_checkpoint,
    CHAT_OT_send_message,
    CHAT_OT_cancel_message,
    CHAT_PT_sidebar,
    SIDEBAR_OT_open_specific_code,
    CHAT_OT_new_thread,
    CHAT_OT_refresh_threads,
    CHAT_OT_delete_thread,
    MODEL_OT_set_selected,
    MODEL_MT_select_menu,
    AUTH_OT_login,
    AUTH_OT_logout,
    AUTH_OT_check_subscription,
    SUBSCRIPTION_OT_upgrade,
    CLIPBOARD_OT_copy_text,
)


def register():
    """Register all classes and properties for the addon."""
    # Register classes
    for cls in classes:
        try:
            bpy.utils.register_class(cls)
        except ValueError as e:
            # Don't log as warning if it's just already registered
            if "already registered" not in str(e):
                log(f"Error registering class {cls.__name__}: {e}", "WARNING")
            # else: Pass silently if already registered
        except Exception as e:
            log(f"Unexpected error registering class {cls.__name__}: {e}", "ERROR")

    # Register properties
    bpy.types.Scene.chat_messages = CollectionProperty(type=CHAT_UL_message_item)
    bpy.types.Scene.chat_message_index = bpy.props.IntProperty()
    bpy.types.Scene.chat_input = StringProperty(
        name="Message",
        description="Type your message here",
        default=""
    )


def unregister():
    """Unregister all classes and properties and clean up."""
    # Remove any running tasks or timers associated with the addon
    # (This is complex, focus on unregistering classes/props first)
    global g_is_processing_message, g_current_task, g_is_loading_history, g_is_checking_subscription
    # Reset global states
    g_is_processing_message = False
    g_is_loading_history = False
    g_is_checking_subscription = False
    # Attempt to cancel ongoing tasks (best effort)
    if g_current_task and hasattr(g_current_task, 'cancel') and not g_current_task.done():
        try:
            g_current_task.cancel()
            log("Cancelled g_current_task on unregister.", "DEBUG")
        except Exception as e:
            log(f"Error cancelling g_current_task on unregister: {e}", "WARNING")
    g_current_task = None
    # Note: We can't easily cancel background threads/async tasks started for subscription checks etc.
    # Rely on daemon=True for threads and proper loop closing.

    # Unregister classes
    for cls in reversed(classes):
        try:
            # Check if class exists and is registered before unregistering
            if hasattr(bpy.types, cls.__name__) or hasattr(bpy.ops, cls.bl_idname.split('.')[0]):
                if cls.is_registered:
                    bpy.utils.unregister_class(cls)
        except Exception as e:
            log(f"Error unregistering class {cls.__name__}: {e}", "WARNING")

    # Unregister properties - use try-except blocks for safety
    props_to_delete = [
        'chat_messages',
        'chat_message_index',
        'chat_input',
        'ai_active_thread',
        'ai_model_selected',
        'checkout_url_display'
    ]
    for prop_name in props_to_delete:
        try:
            if hasattr(bpy.types.Scene, prop_name):
                delattr(bpy.types.Scene, prop_name)
        except Exception as e:
            log(f"Error unregistering Scene.{prop_name}: {e}", "WARNING")

    log("AI Copilot sidebar unregistered.", "INFO")


if __name__ == "__main__":
    register()
