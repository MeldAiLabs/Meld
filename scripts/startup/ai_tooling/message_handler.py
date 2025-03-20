"""
Message handler module for processing API responses and executing tools.
Handles the flow of messages between the API and tool execution.
"""

import json
import asyncio
from typing import Dict, Any, Optional
from . import api
from . import blender_tools
from .logging_utils import log
from .tools.main_tools import execute_blender_code as execute_blender_code_direct


class MessageHandler:
    """Helps route internally the response from the backend api"""

    def __init__(self):
        """Initialize the message handler."""
        self.api_client = api.BlenderAIAPI()
        self.available_tools = blender_tools.available_tools
        self._tool_map = {tool.name: tool for tool in self.available_tools}

    async def _execute_undo_push(self, checkpoint_id: str) -> None:
        """Execute an undo push operation in a background thread.

        Args:
            checkpoint_id: The checkpoint ID to use for the undo push
        """
        try:
            code = f"import bpy; bpy.ops.ed.undo_push(message='{checkpoint_id}')"
            log(f"Executing undo push with checkpoint ID: {checkpoint_id}", "INFO")

            # Execute in a thread to avoid blocking
            exec_result = await asyncio.to_thread(execute_blender_code_direct, {"code": code})

            if isinstance(exec_result, str) and "[ERROR]" in exec_result:
                log(f"Warning: Undo push failed: {exec_result}", "WARNING")
            else:
                log(f"Successfully pushed undo checkpoint: {checkpoint_id}", "INFO")
        except Exception as e:
            log(f"Error executing undo push: {e}", "ERROR")

    async def push_checkpoint(self, base_checkpoint_id: str, is_before: bool = True) -> None:
        """Push a checkpoint to Blender's undo stack.

        Args:
            base_checkpoint_id: The base checkpoint ID from the backend
            is_before: Whether this is a "before" or "after" checkpoint
        """
        suffix = "_before" if is_before else "_after"
        checkpoint_id = f"{base_checkpoint_id}{suffix}"
        await self._execute_undo_push(checkpoint_id)

    async def _execute_tool(self, tool_call: Dict[str, Any]) -> Dict[str, Any]:
        """
        Execute a single tool call and return the result.

        Args:
            tool_call: Dictionary containing tool call information

        Returns:
            Dictionary with tool execution result
        """
        tool_id = tool_call.get("id", "unknown")
        function_info = tool_call.get("function", {})
        if not function_info and "name" in tool_call:
            # Handle direct format where tool info is at root level
            tool_name = tool_call.get("name")
            tool_args = tool_call.get("args", {})
            if isinstance(tool_args, str):
                try:
                    tool_args = json.loads(tool_args)
                except json.JSONDecodeError:
                    log(f"Failed to parse tool args as JSON: {tool_args}", "ERROR")
                    tool_args = {}
        else:
            tool_name = function_info.get("name")
            tool_args = json.loads(function_info.get("arguments", "{}"))

        log(f"Executing tool call {tool_id} for tool {tool_name}", "INFO")
        log(f"Tool call structure: {json.dumps(tool_call, indent=2)}", "DEBUG")

        if not tool_name:
            error_msg = f"[ERROR] Tool call {tool_id} missing tool name"
            log(error_msg, "ERROR")
            return {"tool_call_id": tool_id, "output": error_msg}

        tool = self._tool_map.get(tool_name)
        if not tool:
            error_msg = f"[ERROR] Tool '{tool_name}' not found"
            log(error_msg, "ERROR")
            return {"tool_call_id": tool_id, "output": error_msg}

        try:
            # Execute the tool
            log(f"Tool '{tool_name}' executing with args: {json.dumps(tool_args, indent=2)}", "INFO")
            exec_result_raw = await asyncio.to_thread(tool.invoke, tool_args)
            # execute the tool without asyncio
            # exec_result_raw = tool.invoke(tool_args)
            log(f"Raw execution result for {tool_name}: {exec_result_raw}", "DEBUG")

            # Convert result to string and check for errors
            tool_result_content = str(exec_result_raw)
            if isinstance(exec_result_raw, str) and "[ERROR]" in exec_result_raw:
                error_msg = tool_result_content.replace("[ERROR]", "").strip()
                log(f"Tool '{tool_name}' execution failed: {error_msg}", "ERROR")
                return {
                    "tool_call_id": tool_id,
                    "output": f"[ERROR] {error_msg}"
                }
            else:
                log(f"Tool '{tool_name}' execution successful", "INFO")
                return {
                    "tool_call_id": tool_id,
                    "output": tool_result_content
                }

        except Exception as e:
            error_msg = f"[ERROR] Failed to execute tool '{tool_name}': {str(e)}"
            log(error_msg, "ERROR")
            return {
                "tool_call_id": tool_id,
                "output": error_msg
            }

    def _unwrap_response(self, response: Dict[str, Any]) -> Dict[str, Any]:
        """
        Unwrap the response from any nested structure and normalize it.

        Args:
            response: The raw API response

        Returns:
            Normalized response dictionary
        """
        # Handle nested response structure
        if "response" in response:
            response = response["response"]
            log("Unwrapped nested response structure", "INFO")

        log(f"Normalized response: {json.dumps(response, indent=2)}", "DEBUG")
        return response

    async def process_message(self, thread_id: str, message: str, model_name: str,
                              on_update: Optional[callable] = None,
                              checkpoint_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Process a message through the API and handle any tool calls.

        Args:
            thread_id: The conversation thread ID
            message: The user's message
            on_update: Optional callback for progress updates
            checkpoint_id: Optional checkpoint ID to resume conversation from

        Returns:
            Final response from the API
        """
        try:
            log(f"Processing message for thread {thread_id}", "INFO")
            if checkpoint_id:
                log(f"Using checkpoint ID: {checkpoint_id}", "INFO")

            # Initial API call
            if on_update:
                on_update("Sending message to AI...")

            if model_name == "default":
                model_name = "gpt-4.1-mini"
                log("NO_MODEL_NAME_SPECIFIED: Using default model: gpt-4.1-mini", "INFO")

            log("Making initial API call...", "INFO")
            raw_response = await asyncio.to_thread(
                self.api_client.process_message,
                thread_id,
                message,
                model_name,
                checkpoint_id
            )
            log(f"Raw API response: {json.dumps(raw_response, indent=2)}", "INFO")

            # Unwrap and normalize the response
            response = self._unwrap_response(raw_response)
            log(f"Initial API response type: {response.get('type')}", "INFO")

            # Store the current checkpoint from the response if available
            current_checkpoint_id = None
            if "new_checkpoint_id" in response:
                current_checkpoint_id = response["new_checkpoint_id"]
                log(f"Got new checkpoint ID from response: {current_checkpoint_id}", "INFO")

            # Handle different response formats
            if "type" in response:
                if response["type"] == "assistant" and "message" in response:
                    log("Received direct assistant response", "INFO")
                    # Push "after" checkpoint if we have a new checkpoint ID
                    if current_checkpoint_id:
                        await self.push_checkpoint(current_checkpoint_id, is_before=False)
                    return response
                elif response["type"] == "tool_calls":
                    # Convert new format to old format for compatibility
                    response = {
                        "type": "tool_call",
                        "tool_calls": response.get("data", []),
                        "new_checkpoint_id": current_checkpoint_id
                    }
                    log(f"Converted tool_calls response: {json.dumps(response, indent=2)}", "DEBUG")

            # Handle tool calls
            while response.get("type") in ["tool_call", "tool_calls"]:
                if on_update:
                    # Status update moved to within the loop before execution
                    pass

                tool_calls = response.get("tool_calls", [])
                if not tool_calls and "data" in response:
                    tool_calls = response["data"]

                log(f"Processing {len(tool_calls)} tool calls", "INFO")
                log(f"Tool calls structure: {json.dumps(tool_calls, indent=2)}", "DEBUG")

                tool_results = []
                execution_error = None

                # Execute each tool call
                for tool_call in tool_calls:
                    tool_id = tool_call.get('id')
                    tool_name = tool_call.get('name')

                    # Update status BEFORE executing - Pass full tool call info
                    if on_update:
                        on_update({"type": "tool_call_start", "data": tool_call})

                    result = await self._execute_tool(tool_call)
                    tool_results.append(result)

                    # Check for errors in the result and update status AFTER executing
                    result_status = "successful"
                    if "[ERROR]" in result.get("output", ""):
                        error_msg = result["output"].replace("[ERROR]", "").strip()
                        result_status = "failed"
                        if not execution_error:  # Store first error
                            execution_error = error_msg

                    if on_update:
                        on_update(f"Tool '{tool_name}' execution {result_status}")

                    log(f"Tool result for {tool_id}: {json.dumps(result, indent=2)}", "DEBUG")

                # Send tool results back to API
                if on_update:
                    on_update("Processing tool results...")

                tool_response_data = {
                    "thread_id": thread_id,
                    "tool_outputs": tool_results
                }

                # Include checkpoint ID if we have it
                if current_checkpoint_id:
                    tool_response_data["checkpoint_id"] = current_checkpoint_id
                    log(f"Including checkpoint ID in tool results: {current_checkpoint_id}", "INFO")

                log("Sending tool results back to API...", "INFO")
                log(f"Tool response data: {json.dumps(tool_response_data, indent=2)}", "DEBUG")

                # Get next response from API
                raw_response = await asyncio.to_thread(
                    self.api_client.process_message,
                    thread_id,
                    json.dumps(tool_response_data),
                    model_name,
                    current_checkpoint_id
                )
                log(f"Next raw API response: {json.dumps(raw_response, indent=2)}", "DEBUG")

                # Unwrap and normalize the response
                response = self._unwrap_response(raw_response)
                log(f"API response after tool execution: {response.get('type')}", "INFO")

                # Update checkpoint ID if a new one is provided
                if "new_checkpoint_id" in response:
                    current_checkpoint_id = response["new_checkpoint_id"]
                    log(f"Updated checkpoint ID: {current_checkpoint_id}", "INFO")

            # Push "after" checkpoint if we have a final checkpoint ID
            if current_checkpoint_id:
                await self.push_checkpoint(current_checkpoint_id, is_before=False)

            return response

        except Exception as e:
            error_msg = f"Error processing message: {str(e)}"
            log(error_msg, "ERROR")
            return {
                "type": "error",
                "message": error_msg
            }


# Create a global instance
message_handler = MessageHandler()


async def process_message(thread_id: str, message: str, model_name: str,
                          on_update: Optional[callable] = None,
                          checkpoint_id: Optional[str] = None,) -> Dict[str, Any]:
    """
    Convenience function to process a message using the global handler instance.
    """
    return await message_handler.process_message(thread_id, message, model_name, on_update, checkpoint_id)


async def push_checkpoint(base_checkpoint_id: str, is_before: bool = True) -> None:
    """Push a checkpoint to Blender's undo stack.

    Args:
        base_checkpoint_id: The base checkpoint ID from the backend

    """
    return await message_handler.push_checkpoint(base_checkpoint_id, is_before)
