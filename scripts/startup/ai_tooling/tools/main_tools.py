"""
Main set of Langchain tools for basic Blender operations.
Includes code execution and the prompt-to-code execution wrapper.
"""
import traceback
from typing import Any
from enum import Enum
import bpy
from pydantic import BaseModel, Field

from langchain_core.tools import tool
from langchain_core.messages import SystemMessage, HumanMessage


from ..logging_utils import log


class ModelType(Enum):
    """Enumeration of supported LLM model types."""
    OPENAI = "openai"
    GEMINI = "gemini"  # Listed first to make it the default
    ANTHROPIC = "anthropic"

# --- Pydantic Models for Tool Arguments ---


class ExecuteBlenderCodeArgs(BaseModel):
    """Input schema for execute_blender_code."""
    code: str = Field(..., description="The Python code string to execute in Blender's context.")


# --- Tool Implementations ---

# This is the ORIGINAL direct code execution tool.
# It will be used INTERNALLY by the new wrapper tool, but NOT exposed directly to the main agent anymore.
# Use the version provided by the user which utilizes bpy.app.timers
@tool
def execute_blender_code(code: str) -> str:
    """
    Executes a Python code string within the Blender environment using bpy.

    Use this tool to manipulate Blender scenes, objects, materials, or other Blender data.

    Args:
        code (str): A string containing valid Blender Python code.

    Returns:
        str: A message indicating success '[SUCCESS]' or failure '[ERROR]' with details.
    """
    log("--- TOOL: execute_blender_code called ---", "INFO")
    log(f"Code:\n{code}", "DEBUG")
    import threading
    result_container = {"output": None}
    execution_complete = threading.Event()

    def code_runner():
        """Runs code in Blender's main thread."""
        nonlocal result_container
        import bpy  # Import in main thread context
        try:
            # Prepare execution namespace
            namespace = {'bpy': bpy}
            try:
                import mathutils
                namespace["mathutils"] = mathutils
            except ImportError:
                pass  # Ignore if mathutils isn't used/available

            log("Executing code in main thread...", "DEBUG")
            compiled_code = compile(code, '<string>', 'exec')
            exec(compiled_code, namespace)
            result_container["output"] = "[SUCCESS] Code executed successfully."
            log("--- TOOL: Code executed successfully ---", "INFO")

        except Exception as e:
            error_msg = f"[ERROR] Error during code execution: {type(e).__name__}: {str(e)}\n{traceback.format_exc()}"
            result_container["output"] = error_msg
            log("--- TOOL: Code execution FAILED ---", "ERROR")
            log(error_msg, "DEBUG")
        finally:
            execution_complete.set()
        return None  # Run timer only once

    try:
        import bpy
        import threading  # Ensure threading is imported
        if bpy and hasattr(bpy, 'app') and hasattr(bpy.app, 'timers'):
            bpy.app.timers.register(code_runner, first_interval=0.0)
        else:
            raise RuntimeError("bpy.app.timers not available.")

        timeout_seconds = 30.0
        log(f"Waiting for execution completion (max {timeout_seconds}s)...", "DEBUG")
        success = execution_complete.wait(timeout=timeout_seconds)

        if success:
            log("Execution finished.", "DEBUG")
            result = result_container["output"]
            if result is None:
                result = "[ERROR] Execution finished but no output was captured."
                log(result, "ERROR")
            return result
        else:
            log("--- TOOL: Code execution TIMED OUT! ---", "ERROR")
            return f"[ERROR] Code execution timed out after {timeout_seconds} seconds. The operation might be too complex or stuck."

    except ImportError:
        err_msg = "[ERROR] Failed to import 'bpy'. This tool must be run within Blender."
        log(err_msg, "CRITICAL")
        return err_msg
    except RuntimeError as rte:
        err_msg = f"[ERROR] Failed to schedule code execution in Blender: {rte}"
        log(err_msg, "ERROR")
        return err_msg
    except Exception as e:
        error_msg = f"[ERROR] Error setting up or waiting for code execution: {type(e).__name__}: {str(e)}\n{traceback.format_exc()}"
        log(error_msg, "ERROR")
        return error_msg


# --- Internal Helper for Single Attempt ---


def _generate_and_execute_code_once(
        llm_with_tool: Any,
        system_message: SystemMessage,
        current_human_message_content: str,
        attempt_number: int,
        original_prompt: str) -> str:
    """Performs one attempt to generate and execute code for the given prompt."""
    log(f"-- Starting attempt {attempt_number} --", "INFO")
    messages = [
        system_message,
        HumanMessage(content=current_human_message_content)
    ]

    try:
        log(f"Invoking sub-LLM for code generation (Attempt {attempt_number})...", "INFO")
        log(f"Human message for attempt {attempt_number}: {current_human_message_content[:200]}...", "DEBUG")
        response = llm_with_tool.invoke(messages)
        log(
            f"Sub-LLM response received (Attempt {attempt_number}). Content: {str(response.content)[:100]}... Tool Calls: {len(response.tool_calls) if response.tool_calls else 0}",
            "DEBUG")

        # --- Process Response ---
        if not response.tool_calls:
            error_detail = response.content if response.content else "No tool call was made by sub-LLM."
            log(f"Sub-LLM failed to call tool on attempt {attempt_number}. Response: {error_detail}", "ERROR")
            # Provide context for retry
            return f"[ERROR] Code generation LLM failed to call the required tool. Detail: {error_detail}"

        if len(response.tool_calls) > 1:
            log(
                f"Warning: Sub-LLM generated multiple tool calls ({len(response.tool_calls)}) on attempt {attempt_number}, using the first.",
                "WARNING")

        tool_call = response.tool_calls[0]
        tool_name = tool_call.get("name")
        tool_args = tool_call.get("args", {})

        if tool_name != "execute_blender_code":
            error_detail = f"Sub-LLM called incorrect tool: '{tool_name}'. Expected 'execute_blender_code'."
            log(error_detail + f" on attempt {attempt_number}", "ERROR")
            return f"[ERROR] {error_detail}"

        if "code" not in tool_args:
            error_detail = "Sub-LLM tool call missing 'code' argument."
            log(error_detail + f" on attempt {attempt_number}", "ERROR")
            return f"[ERROR] {error_detail}"

        code_to_execute = tool_args["code"]

        # Check if the code itself is an error message generated directly by the LLM
        if isinstance(code_to_execute, str) and code_to_execute.strip().startswith("[ERROR]"):
            log(
                f"Code generation LLM returned an error directly on attempt {attempt_number}: {code_to_execute}",
                "WARNING")
            return code_to_execute  # Propagate the LLM's explicit error

        # --- Execute Code ---
        log(f"Sub-LLM generated code (Attempt {attempt_number}, length {len(code_to_execute)}). Executing...", "INFO")
        log(f"Code to execute (Attempt {attempt_number}):\n```python\n{code_to_execute}\n```", "DEBUG")
        execution_result = execute_blender_code.invoke({"code": code_to_execute})
        log(f"Code execution result (Attempt {attempt_number}): {execution_result}", "INFO")
        return execution_result  # Return success or error from execution

    except Exception as e:
        # Catch unexpected errors during LLM call or processing
        log(f"Exception occurred during attempt {attempt_number}: {e}", "ERROR")
        log(traceback.format_exc(), "DEBUG")
        return f"[ERROR] Unexpected exception during generation/execution attempt {attempt_number}: {e}"


__all__ = ['execute_blender_code']
