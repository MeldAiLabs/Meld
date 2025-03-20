"""Manages the local cache for AI Copilot chat threads and messages."""

import json
import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
import base64

import bpy

from .logging_utils import log
from . import config_manager
from . import auth_manager

# --- Configuration (Moved to config.json) --- #
# CACHE_FILENAME = "ai_copilot_cache.json"
# MAX_HISTORY_PER_THREAD = 200


def get_cache_dir() -> Path:
    """Get the directory for storing cache files."""
    # Using Blender's user config path ensures persistence across sessions
    config_path = Path(bpy.utils.user_resource('CONFIG', path="ai_copilot"))
    config_path.mkdir(parents=True, exist_ok=True)
    return config_path


def get_cache_file_path(user_id: Optional[str] = None) -> Path:
    """Get the full path to the cache file using config and user ID.

    Args:
        user_id: Optional user ID for per-user cache files.
               If None, returns the default cache path.
    """
    # Use config manager
    cache_filename = config_manager.get_cache_filename()

    # If user_id is provided, include it in the filename
    if user_id:
        base_name, ext = cache_filename.rsplit('.', 1) if '.' in cache_filename else (cache_filename, 'json')
        cache_filename = f"{base_name}_{user_id}.{ext}"

    return get_cache_dir() / cache_filename


def extract_user_id() -> Optional[str]:
    """Extract user ID from auth tokens by decoding JWT.

    Decodes the ID token from Cognito and extracts the 'sub' claim,
    which is the unique identifier for the user.

    Returns:
        str: User ID if available, None otherwise.
    """
    # Get the auth manager instance
    auth_mgr = auth_manager.get_auth_manager()

    # Get ID token which contains user info
    id_token = auth_mgr.get_id_token()

    # If no token, user is not authenticated
    if not id_token:
        log("No ID token available, using default cache", "DEBUG")
        return None

    try:
        # JWT tokens have three parts: header.payload.signature
        # We need the payload part (index 1)
        token_parts = id_token.split('.')
        if len(token_parts) != 3:
            log("Invalid JWT token format, using default cache", "WARNING")
            return None

        # Fix padding for base64 decoding
        payload = token_parts[1]
        # Add padding if needed
        padding = '=' * (4 - len(payload) % 4) if len(payload) % 4 else ''
        payload += padding

        # Decode and parse JSON
        payload_bytes = base64.urlsafe_b64decode(payload)
        payload_json = json.loads(payload_bytes)

        # Extract user ID (sub claim)
        user_id = payload_json.get('sub')
        if not user_id:
            log("User ID (sub) not found in token, using default cache", "WARNING")
            return None

        log(f"Extracted user ID for cache: {user_id}", "DEBUG")
        # For privacy and filename compatibility, use just first part or hash it
        short_id = user_id.split('-')[0] if '-' in user_id else user_id[:8]
        log(f"Extracted user ID for cache: {short_id}", "DEBUG")
        return short_id

    except Exception as e:
        log(f"Error decoding JWT token: {e}, using default cache", "ERROR")
        return None


class CacheManager:
    """Handles loading, saving, and accessing cached AI Copilot data.

    Manages a dictionary storing thread information, including metadata
    (creation/access times) and message history. Provides methods for
    updating from API responses and adding new messages locally.
    """

    def __init__(self):
        self._cache: Dict[str, Any] = {
            "threads": {},  # thread_id -> { "last_accessed_at": iso_timestamp, "created_at": iso_timestamp, "history": [] }
            "active_thread_id": None,
        }
        # Get max history from config
        self._max_history = config_manager.get_cache_max_history()
        # Get current user ID
        self._user_id = extract_user_id()
        self.load_cache()

    def load_cache(self):
        """Load cache data from the JSON file for the current user."""
        cache_file = get_cache_file_path(self._user_id)
        if cache_file.exists():
            try:
                with open(cache_file, 'r', encoding='utf-8') as f:
                    self._cache = json.load(f)
                    # Ensure essential keys exist
                    if "threads" not in self._cache:
                        self._cache["threads"] = {}
                    if "active_thread_id" not in self._cache:
                        self._cache["active_thread_id"] = None
                    log(f"Cache loaded successfully for user {self._user_id or 'default'}.", "INFO")
            except (json.JSONDecodeError, IOError) as e:
                log(f"Error loading cache file {cache_file}: {e}. Starting with empty cache.", "ERROR")
                self._reset_cache_structure()
        else:
            log(f"Cache file not found for user {self._user_id or 'default'}. Starting with empty cache.", "INFO")
            self._reset_cache_structure()

    def _reset_cache_structure(self):
        """Initialize the cache with default structure."""
        self._cache = {
            "threads": {},
            "active_thread_id": None,
        }

    def save_cache(self):
        """Save the current cache state to the JSON file for the current user."""
        cache_file = get_cache_file_path(self._user_id)
        try:
            # Prune histories before saving
            for thread_id in self._cache.get("threads", {}):
                if "history" in self._cache["threads"][thread_id]:
                    history = self._cache["threads"][thread_id]["history"]
                    # Use configured max history
                    if len(history) > self._max_history:
                        self._cache["threads"][thread_id]["history"] = history[-self._max_history:]
                        log(f"Pruned history for thread {thread_id} to {self._max_history} messages.", "DEBUG")

            with open(cache_file, 'w', encoding='utf-8') as f:
                json.dump(self._cache, f, indent=2)
            # log("Cache saved successfully.", "DEBUG") # Too noisy
        except IOError as e:
            log(f"Error saving cache file {cache_file}: {e}", "ERROR")
        except Exception as e:
            log(f"Unexpected error saving cache: {e}", "ERROR")

    def update_user_id(self, new_user_id: Optional[str] = None):
        """Update the cache manager to use a different user ID.

        This method should be called when the user logs in or out,
        to ensure the cache is using the correct user context.

        Args:
            new_user_id: New user ID to use, or None if logged out
        """
        if self._user_id == new_user_id:
            return  # No change needed

        # Save current cache if needed
        self.save_cache()

        # Update user ID
        self._user_id = new_user_id

        # Load the new user's cache
        self.load_cache()

        log(f"Switched cache to user {new_user_id or 'default'}", "INFO")

    def update_thread_list(self, threads_from_api: List[Dict[str, Any]]):
        """Update the list of known threads from the API data."""
        if not isinstance(self._cache.get("threads"), dict):
            log("Cache 'threads' key is not a dictionary. Resetting.", "WARNING")
            self._cache["threads"] = {}

        updated_ids = set()
        for api_thread in threads_from_api:
            thread_id = api_thread.get("thread_id")
            if not thread_id:
                continue

            updated_ids.add(thread_id)
            if thread_id not in self._cache["threads"]:
                # New thread found
                self._cache["threads"][thread_id] = {
                    "last_accessed_at": api_thread.get("last_accessed_at"),
                    "created_at": api_thread.get("created_at"),
                    "history": []  # Initialize history when first seen
                }
                log(f"Added new thread from API: {thread_id}", "DEBUG")
            else:
                # Update existing thread metadata (newer info from API is preferred)
                self._cache["threads"][thread_id]["last_accessed_at"] = api_thread.get("last_accessed_at")
                # Don't overwrite created_at if it already exists locally
                if "created_at" not in self._cache["threads"][thread_id]:
                    self._cache["threads"][thread_id]["created_at"] = api_thread.get("created_at")
                # Don't overwrite history if it already exists
                if "history" not in self._cache["threads"][thread_id]:
                    self._cache["threads"][thread_id]["history"] = []

        # Auto-cleanup: Remove local-only threads with no history
        current_cached_ids = set(self._cache["threads"].keys())
        removed_ids = current_cached_ids - updated_ids
        deleted_count = 0
        for tid in removed_ids:
            # Check if history exists and is empty
            if not self._cache["threads"][tid].get("history"):
                # Double check we are not deleting the active thread without resetting it
                # This might happen if cache saving fails after delete_thread sets active to None
                if self.get_active_thread() == tid:
                    self.set_active_thread(None)  # Ensure active thread is reset before deleting

                del self._cache["threads"][tid]
                log(f"Auto-removed local-only thread {tid} with empty history from cache.", "INFO")
                deleted_count += 1
            else:
                log(f"Kept local-only thread {tid} because it has cached history.", "DEBUG")

        # Save cache if updates or deletions occurred
        if len(updated_ids) > 0 or deleted_count > 0:
            self.save_cache()

    def get_thread_list_sorted(self) -> List[Tuple[str, str, str]]:
        """Return thread list sorted by last access time (desc) for UI EnumProperty."""
        threads = self._cache.get("threads", {})
        if not isinstance(threads, dict):
            log("Cannot get thread list: cache['threads'] is not a dict.", "ERROR")
            return []

        # Filter out threads without a valid last_accessed_at for sorting robustness
        valid_threads = []
        for tid, data in threads.items():
            # Ensure data is a dict and has the required key
            if isinstance(data, dict) and "last_accessed_at" in data:
                # Check if the timestamp is valid ISO format
                try:
                    datetime.datetime.fromisoformat(data["last_accessed_at"].replace('Z', '+00:00'))
                    valid_threads.append((tid, data))
                except (ValueError, TypeError):
                    log(
                        f"Thread {tid} has invalid timestamp: {data['last_accessed_at']}. Excluding from sort.",
                        "WARNING")
                    # Optionally assign a default old date instead of excluding?
                    # valid_threads.append((tid, {**data, "last_accessed_at": "1970-01-01T00:00:00Z"}))
            elif isinstance(data, dict):
                log(f"Thread {tid} missing 'last_accessed_at'. Excluding from sort.", "WARNING")
                # Optionally assign a default old date?
                # valid_threads.append((tid, {**data, "last_accessed_at": "1970-01-01T00:00:00Z"}))

        try:
            # Sort descending by last_accessed_at timestamp
            sorted_threads = sorted(
                valid_threads,
                key=lambda item: item[1].get("last_accessed_at", "1970-01-01T00:00:00Z"),
                reverse=True
            )
        except Exception as e:
            log(f"Error sorting threads: {e}", "ERROR")
            # Return unsorted valid threads in case of error
            sorted_threads = valid_threads

        # Format for EnumProperty: (identifier, name, description)
        enum_items = []
        for thread_id, data in sorted_threads:
            # Create a user-friendly name (e.g., first user message or date)
            name = f"Chat {thread_id[:8]}..."
            history = data.get("history", [])
            if history and isinstance(history, list) and len(history) > 0:
                first_msg = history[0]
                if isinstance(first_msg, dict) and first_msg.get("sender") == "user" and first_msg.get("text"):
                    name = f"{first_msg['text'][:25]}..."  # Use first 25 chars of first user message

            last_access_str = data.get("last_accessed_at", "Unknown")
            try:
                # Try to format date nicely
                dt_obj = datetime.datetime.fromisoformat(last_access_str.replace('Z', '+00:00'))
                last_access_str = dt_obj.strftime("%Y-%m-%d %H:%M")
            except (ValueError, TypeError):
                pass  # Keep original string if parsing fails

            description = f"Last activity: {last_access_str}"
            enum_items.append((thread_id, name, description))

        return enum_items

    def update_thread_history(self, thread_id: str, history_from_api: List[Dict[str, Any]]):
        """Replace the cached history for a specific thread with fresh data from the API."""
        if thread_id not in self._cache.get("threads", {}):
            log(f"Cannot update history: Thread ID {thread_id} not found in cache.", "WARNING")
            # Optionally create thread entry if missing?
            # self._cache["threads"][thread_id] = {"history": []} # No metadata without /threads call
            return

        if not isinstance(history_from_api, list):
            log(f"Received invalid history data (not a list) for thread {thread_id}", "ERROR")
            return

        # Add detailed logging of received history
        log(f"DEBUG: Updating history for {thread_id}. API data received:", "DEBUG")
        for i, msg in enumerate(history_from_api):
            log(f"  Msg {i}: {json.dumps(msg)}", "DEBUG")

        self._cache["threads"][thread_id]["history"] = history_from_api
        # Update last accessed time when history is explicitly fetched/updated
        self._cache["threads"][thread_id]["last_accessed_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        log(f"Updated history cache for thread {thread_id}", "INFO")
        self.save_cache()

    def get_thread_history(self, thread_id: str) -> List[Dict[str, Any]]:
        """Retrieve the cached history for a thread."""
        thread_data = self._cache.get("threads", {}).get(thread_id, {})
        return thread_data.get("history", [])

    def get_thread_data(self, thread_id: str) -> Dict[str, Any]:
        """Retrieve the full data dictionary for a thread, including history and metadata."""
        return self._cache.get("threads", {}).get(thread_id, {})

    def add_message_to_history(self, thread_id: str, message_data: Dict[str, Any]):
        """Add a new message to the history of a specific thread."""
        if thread_id not in self._cache.get("threads", {}):
            # If thread doesn't exist, create it (e.g., for a new chat)
            log(f"Adding new thread {thread_id} to cache during message add.", "INFO")
            self._cache["threads"][thread_id] = {
                "last_accessed_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "history": []
            }
        elif not isinstance(self._cache["threads"][thread_id].get("history"), list):
            log(f"History for thread {thread_id} is not a list. Initializing.", "WARNING")
            self._cache["threads"][thread_id]["history"] = []

        # Ensure message_data uses standard keys ('type', 'content')
        standardized_message = {
            "type": message_data.get("sender_type") or message_data.get("type", "unknown"),
            "content": message_data.get("text") or message_data.get("content", ""),
            "checkpoint_id": message_data.get("checkpoint_id"),
            # Include other relevant fields, mapping if necessary
            "tool_calls": message_data.get("tool_calls"),  # From API history
            "tool_call_info": message_data.get("tool_call_info"),  # From live add
            "tool_call_id": message_data.get("tool_call_id")  # From API history tool result
        }
        # Remove keys with None values to keep cache clean
        standardized_message = {k: v for k, v in standardized_message.items() if v is not None}

        # Add detailed logging before appending
        log(f"DEBUG: Adding message to {thread_id}. Standardized Data: {json.dumps(standardized_message)}", "DEBUG")

        self._cache["threads"][thread_id]["history"].append(standardized_message)
        # Update last accessed time
        self._cache["threads"][thread_id]["last_accessed_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        # Use 'type' for log
        log(f"Added message to history for thread {thread_id}. Type: {standardized_message.get('type')}", "DEBUG")
        self.save_cache()  # Save after every message add

    def set_active_thread(self, thread_id: Optional[str]):
        """Mark a thread as active in the cache."""
        if thread_id is not None and thread_id not in self._cache.get("threads", {}):
            log(f"Attempted to set non-existent thread {thread_id} as active.", "WARNING")
            # Should we allow setting it anyway? Or force creation? For now, just log.
            # return # Prevent setting non-existent thread as active

        self._cache["active_thread_id"] = thread_id
        log(f"Set active thread to: {thread_id}", "INFO")
        self.save_cache()

    def get_active_thread(self) -> Optional[str]:
        """Get the ID of the active thread."""
        active_id = self._cache.get("active_thread_id")
        # Verify the active thread still exists in the threads list
        if active_id and active_id not in self._cache.get("threads", {}):
            log(f"Active thread ID '{active_id}' no longer exists in cache. Resetting active thread.", "WARNING")
            self.set_active_thread(None)  # Also saves cache
            return None
        return active_id

    def add_or_update_thread_metadata(
            self,
            thread_id: str,
            created_at: Optional[str] = None,
            last_accessed_at: Optional[str] = None):
        """Adds a new thread entry or updates metadata if it exists."""
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        if thread_id not in self._cache.get("threads", {}):
            self._cache["threads"][thread_id] = {
                "last_accessed_at": last_accessed_at or now_iso,
                "created_at": created_at or now_iso,
                "history": []
            }
            log(f"Added new thread {thread_id} metadata to cache.", "INFO")
        else:
            if last_accessed_at:
                self._cache["threads"][thread_id]["last_accessed_at"] = last_accessed_at
            if created_at and "created_at" not in self._cache["threads"][thread_id]:  # Only set created_at if missing
                self._cache["threads"][thread_id]["created_at"] = created_at
        self.save_cache()

    def delete_thread(self, thread_id: str):
        """Delete a thread and its history from the local cache."""
        if thread_id in self._cache.get("threads", {}):
            del self._cache["threads"][thread_id]
            log(f"Deleted thread {thread_id} from local cache.", "INFO")

            # If the deleted thread was the active one, reset active thread
            if self.get_active_thread() == thread_id:
                self.set_active_thread(None)  # This also saves the cache
            else:
                self.save_cache()  # Save cache if active thread wasn't reset
        else:
            log(f"Attempted to delete non-existent thread {thread_id} from cache.", "WARNING")

    def clear_thread_history(self, thread_id: str):
        """Clears the message history for a specific thread."""
        if thread_id in self._cache["threads"]:
            self._cache["threads"][thread_id]["history"] = []
            self._cache["threads"][thread_id]["last_updated_at"] = datetime.datetime.now(
                datetime.timezone.utc).isoformat()
            log(f"Cleared history cache for thread {thread_id}", "INFO")
            self.save_cache()
        else:
            log(f"Attempted to clear history for non-existent thread {thread_id}", "WARNING")

    def remove_messages_after_index(self, thread_id: str, index: int):
        """Removes messages in the cached history after the specified index (inclusive)."""
        if thread_id in self._cache["threads"]:
            history = self._cache["threads"][thread_id].get("history", [])
            if index < 0 or index >= len(history):
                log(f"Invalid index {index} for removing messages in thread {thread_id} (history length {len(history)}).", "WARNING")
                return

            # Keep elements from the beginning up to 'index' (inclusive)
            truncated_history = history[:index + 1]
            removed_count = len(history) - len(truncated_history)

            self._cache["threads"][thread_id]["history"] = truncated_history
            self._cache["threads"][thread_id]["last_updated_at"] = datetime.datetime.now(
                datetime.timezone.utc).isoformat()
            log(f"Removed {removed_count} messages from cache for thread {thread_id} after index {index}.", "INFO")
            self.save_cache()
        else:
            log(f"Attempted to remove messages for non-existent thread {thread_id}", "WARNING")

    def get_most_recent_thread_id(self) -> Optional[str]:
        """Gets the ID of the most recently accessed thread."""
        threads = self._cache.get("threads", {})
        if not threads:
            return None

        # Sort threads by last_accessed_at timestamp (descending)
        sorted_threads = sorted(threads.items(), key=lambda item:
                                item[1].get("last_accessed_at", ""), reverse=True)

        return sorted_threads[0][0] if sorted_threads else None


# Create a singleton instance
_cache_manager_instance = None


def get_cache_manager() -> CacheManager:
    """Get the singleton instance of the CacheManager."""
    global _cache_manager_instance
    if _cache_manager_instance is None:
        log("Initializing CacheManager instance.", "INFO")
        _cache_manager_instance = CacheManager()
        # Register for auth events
        auth_manager.add_auth_event_listener(_handle_auth_event)
    return _cache_manager_instance


def _handle_auth_event(event: str):
    """Handle authentication events.

    Args:
        event: The auth event ('login' or 'logout')
    """
    global _cache_manager_instance
    if _cache_manager_instance is None:
        return

    if event == "login":
        # User logged in, switch to their cache
        new_user_id = extract_user_id()
        log(f"Login detected, switching to user cache: {new_user_id}", "INFO")
        _cache_manager_instance.update_user_id(new_user_id)
    elif event == "logout":
        # User logged out, switch to default cache
        log("Logout detected, switching to default cache", "INFO")
        _cache_manager_instance.update_user_id(None)


def register():
    """Register the cache manager when the addon is loaded."""
    # Initialize the cache manager to ensure it's ready
    get_cache_manager()
    log("CacheManager registered.", "INFO")


def unregister():
    """Unregister the cache manager when the addon is unloaded."""
    global _cache_manager_instance

    # Remove the auth event listener
    if _cache_manager_instance is not None:
        auth_manager.remove_auth_event_listener(_handle_auth_event)
        log("Removed auth event listener.", "INFO")

    _cache_manager_instance = None
    log("CacheManager unregistered.", "INFO")
