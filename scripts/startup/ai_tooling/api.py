"""Handles communication with the AI Copilot backend API."""

from typing import Dict, Any, Optional
from urllib.parse import urljoin

import requests

from .auth_manager import get_auth_manager, AuthManager
from .logging_utils import log
from . import config_manager


class BlenderAIAPI:
    """A client class for interacting with the Blender AI Copilot backend API.

    Handles sending messages, fetching threads, and retrieving history,
    including managing authentication via AuthManager.
    """

    def __init__(self):
        self.base_url = config_manager.get_api_base_url()
        if not self.base_url:
            log("API base URL not found in config. API calls may fail.", "ERROR")
        else:
            log(f"API client initialized with base URL: {self.base_url}", "INFO")

        self._auth_manager: AuthManager = get_auth_manager()

    def _get_headers(self) -> Dict[str, str]:
        """Get headers for API requests including auth token when available."""
        headers = {
            'Content-Type': 'application/json',
            'Accept': 'application/json',  # Good practice to specify accept header
        }
        # Get token from AuthManager
        # access_token = self._auth_manager.get_access_token()  # This handles expiry checks/refresh
        id_token = self._auth_manager.get_id_token()  # Use ID token instead
        # if access_token:
        if id_token:
            log("Adding Authorization header to API request.", "DEBUG")
            # print("Access token", access_token) # Keep debug print if needed, but update label
            print("ID token for Auth header:", id_token)  # Print truncated ID token
            # headers['Authorization'] = f'Bearer {access_token}'
            headers['Authorization'] = f'Bearer {id_token}'
        else:
            log("No ID token available for API request.", "DEBUG")
        return headers

    def _make_request(self,
                      method: str,
                      endpoint: str,
                      data: Optional[Dict] = None,
                      attempt_refresh: bool = True) -> Dict[str,
                                                            Any]:
        """Make HTTP request to the API endpoint, handling auth refresh."""
        url = urljoin(self.base_url, endpoint)
        headers = self._get_headers()  # Get headers with current token (or none)

        try:
            log(f"Making API request: {method} {url}", "DEBUG")
            response = requests.request(
                method=method,
                url=url,
                headers=headers,
                json=data,
                timeout=30
            )
            response.raise_for_status()  # Raise HTTPError for bad responses (4xx or 5xx)
            # Handle potential empty response body for non-JSON success cases (e.g., 204 No Content)
            if response.status_code == 204:
                return {}  # Return empty dict for No Content
            if 'application/json' in response.headers.get('Content-Type', ''):
                return response.json()
            else:
                log(
                    f"API response is not JSON (Content-Type: {response.headers.get('Content-Type')}). Returning raw text.",
                    "WARNING")
                # Or handle differently based on expected non-JSON responses
                return {"raw_content": response.text}

        except requests.exceptions.HTTPError as e:
            log(f"HTTPError during API request to {url}: {e}", "ERROR")
            # Handle 401 Unauthorized
            if e.response.status_code == 401 and attempt_refresh:
                log("Received 401 Unauthorized. Attempting token refresh...", "INFO")
                # Let refresh_tokens() handle the check internally
                refresh_successful = self._auth_manager.refresh_tokens()
                if refresh_successful:
                    log("Token refresh successful. Retrying API request.", "INFO")
                    # Retry the request *once* without allowing further refresh attempts
                    return self._make_request(method, endpoint, data, attempt_refresh=False)
                else:
                    log("Token refresh failed. Raising original 401 error.", "ERROR")
                    # Optionally trigger logout or prompt user to re-authenticate here
                    self._auth_manager.logout()  # Force logout if refresh fails after 401
                    raise e  # Re-raise the original 401 error
            else:
                # Re-raise other HTTP errors or 401 if refresh wasn't attempted/possible
                raise e
        except requests.exceptions.RequestException as e:
            log(f"RequestException during API request to {url}: {e}", "ERROR")
            # Handle connection errors, timeouts, etc.
            # For now, just re-raise
            raise e
        except Exception as e:
            log(f"Unexpected error during API request to {url}: {e}", "ERROR")
            raise e  # Re-raise unexpected errors

    def process_message(self, thread_id: str, message: str, model_name: str,
                        checkpoint_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Send a message to the AI backend for processing.

        Args:
            thread_id: Unique identifier for the conversation thread
            message: The user's message content
            checkpoint_id: Optional checkpoint ID to resume conversation from

        Returns:
            Dict containing either an assistant message or tool calls based on the schema
        """
        # _make_request handles auth
        data = {
            "thread_id": thread_id,
            "message": message,
            "model_name": model_name
        }
        if checkpoint_id:
            data["checkpoint_id"] = checkpoint_id

        return self._make_request("POST", "process-message", data)

    def get_threads(self) -> Dict[str, Any]:
        """
        Fetch the list of conversation threads.

        Returns:
            Dict containing the list of threads based on the schema
        """
        # _make_request handles auth
        return self._make_request("GET", "threads")

    def get_thread_history(self, thread_id: str) -> Dict[str, Any]:
        """
        Fetch the conversation history for a specific thread.

        Args:
            thread_id: Unique identifier for the conversation thread

        Returns:
            Dict containing the history for the specified thread
        """
        # _make_request handles auth
        endpoint = f"threads/{thread_id}/history"
        return self._make_request("GET", endpoint)

    def get_models(self) -> Dict[str, Any]:
        """
        Fetch the list of available models.

        Returns:
            Dict containing the list of models based on the schema
        """
        # _make_request handles auth
        endpoint = "models"
        return self._make_request("GET", endpoint)

    def create_checkout_session(self) -> Dict[str, Any]:
        """
        Request the backend to create a Stripe Checkout session.

        Returns:
            Dict containing the checkout URL based on the schema
        """
        endpoint = "create-checkout-session"
        # This endpoint typically doesn't require a request body
        return self._make_request("POST", endpoint)

    def get_subscription_status(self) -> Dict[str, Any]:
        """
        Fetch the current subscription status for the authenticated user.

        Returns:
            Dict containing the subscription status based on the schema
        """
        endpoint = "subscription/status"
        return self._make_request("GET", endpoint)

    # Method to explicitly set auth manager if needed (e.g., for testing)
    def set_auth_manager(self, auth_manager: AuthManager):
        """Set the authentication manager instance."""
        self._auth_manager = auth_manager


# --- Global Instance and Convenience Functions ---
# Keep these for ease of use, they now implicitly use the auth-aware instance

api_client = BlenderAIAPI()


def process_message(thread_id: str, message: str, model_name: str,
                    checkpoint_id: Optional[str] = None) -> Dict[str, Any]:
    """
    Convenience function to process a message using the global API client.
    """
    return api_client.process_message(thread_id, message, model_name, checkpoint_id)


def get_threads() -> Dict[str, Any]:
    """
    Convenience function to get threads using the global API client.
    """
    return api_client.get_threads()


def get_thread_history(thread_id: str) -> Dict[str, Any]:
    """
    Convenience function to get thread history using the global API client.
    """
    return api_client.get_thread_history(thread_id)


def get_models() -> Dict[str, Any]:
    """
    Convenience function to get models using the global API client.
    """
    return api_client.get_models()


def create_checkout_session() -> Dict[str, Any]:
    """
    Convenience function to create a checkout session using the global API client.
    """
    return api_client.create_checkout_session()


def get_subscription_status() -> Dict[str, Any]:
    """
    Convenience function to get subscription status using the global API client.
    """
    return api_client.get_subscription_status()
