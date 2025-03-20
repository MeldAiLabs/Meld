"""Manages authentication with AWS Cognito using OAuth2 Authorization Code Grant with PKCE."""

import webbrowser
import threading
import http.server
import socketserver
import urllib.parse
import secrets
import hashlib
import base64
import time
import json
from pathlib import Path
from typing import Optional, Dict, Any, Tuple, List, Callable
import bpy
import requests

from .logging_utils import log
from . import config_manager

# Track login/logout event listeners
_auth_event_listeners: List[Callable[[str], None]] = []


def add_auth_event_listener(listener: Callable[[str], None]):
    """Add a listener function to be called on auth events.

    The listener will be called with event name ('login', 'logout').

    Args:
        listener: Function that takes an event name string.
    """
    global _auth_event_listeners
    if listener not in _auth_event_listeners:
        _auth_event_listeners.append(listener)
        log(f"Added auth event listener: {listener.__qualname__}", "DEBUG")


def remove_auth_event_listener(listener: Callable[[str], None]):
    """Remove a previously added listener function.

    Args:
        listener: Function to remove from listeners.
    """
    global _auth_event_listeners
    if listener in _auth_event_listeners:
        _auth_event_listeners.remove(listener)
        log(f"Removed auth event listener: {listener.__qualname__}", "DEBUG")


def _notify_listeners(event: str):
    """Notify all listeners of an auth event.

    Args:
        event: Event name ('login', 'logout').
    """
    global _auth_event_listeners
    for listener in _auth_event_listeners:
        try:
            listener(event)
            log(f"Notified listener {listener.__qualname__} of {event} event", "DEBUG")
        except Exception as e:
            log(f"Error notifying listener {listener.__qualname__}: {e}", "ERROR")


def get_config_dir() -> Path:
    """Get the directory for storing configuration files."""
    # Using Blender's user config path ensures persistence across sessions
    config_path = Path(bpy.utils.user_resource('CONFIG', path="ai_copilot"))
    config_path.mkdir(parents=True, exist_ok=True)
    return config_path


def get_token_file_path() -> Path:
    """Get the full path to the token storage file using config."""
    # Use config manager
    token_filename = config_manager.get_auth_token_filename()
    return get_config_dir() / token_filename


# --- PKCE Helper ---
def make_pkce_pair() -> Tuple[str, str]:
    """Generate a PKCE code verifier and challenge."""
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode()
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    log("Generated PKCE pair", "DEBUG")
    return verifier, challenge


# --- HTTP Callback Server ---
class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    """Handles the redirect from Cognito, capturing the authorization code."""
    server_version = "BlenderCognitoCallback/0.1"

    def do_GET(self):
        """Handle GET requests, specifically looking for the /callback path."""
        log(f"_CallbackHandler.do_GET received request: {self.path}", "INFO")

        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != "/callback":
            self.send_error(404, "Not Found")
            return

        params = urllib.parse.parse_qs(parsed.query)
        code = params.get("code", [None])[0]
        error = params.get("error", [None])[0]

        if error:
            log(f"Cognito callback received error: {error}", "ERROR")
            # Store error details if needed
            self.server.auth_code = None
            self.server.auth_error = error
            self.send_response(400)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            error_html = f"<html><body><h1>Authentication Failed</h1><p>Error: {error}.</p>" \
                         f"You can close this window.</body></html>"
            self.wfile.write(error_html.encode('utf-8'))
        elif code:
            log("Received authorization code from Cognito callback", "INFO")
            self.server.auth_code = code
            self.server.auth_error = None
            # Respond to the browser
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<html><body><h1>Authentication successful!</h1>"
                             b"<p>Received authorization code. Please return to Meld.</p>"
                             b"<p>You can close this window now.</p></body></html>")
        else:
            log("Cognito callback received without code or error", "WARNING")
            self.send_error(400, "Bad Request: Missing code")

        # Signal that the callback has been handled (even if it's an error)
        if hasattr(self.server, 'callback_received_event'):
            self.server.callback_received_event.set()  # Signal the waiting thread


class AuthManager:
    """Manages the AWS Cognito authentication lifecycle."""

    def __init__(self):
        self._tokens: Optional[Dict[str, Any]] = None
        self._httpd: Optional[socketserver.TCPServer] = None
        self._server_thread: Optional[threading.Thread] = None
        self._callback_received_event: Optional[threading.Event] = None
        self.load_tokens()  # Load existing tokens on initialization

    def _start_callback_server(self) -> Optional[socketserver.TCPServer]:
        """Starts the local HTTP server to listen for the Cognito callback."""
        # Get port from config
        cognito_config = config_manager.get_cognito_config()
        local_port = cognito_config.get("local_port")
        if not local_port or not isinstance(local_port, int):
            log("Invalid or missing 'local_port' in Cognito config. Cannot start server.", "ERROR")
            return None

        if self._httpd:
            log("Callback server already running.", "WARNING")
            return self._httpd  # Avoid starting multiple servers

        try:
            # Allow reusing the address quickly after shutdown
            socketserver.TCPServer.allow_reuse_address = True
            httpd = socketserver.TCPServer(("localhost", local_port), _CallbackHandler)
            httpd.auth_code = None  # Store received code here
            httpd.auth_error = None  # Store any error here
            self._callback_received_event = threading.Event()
            httpd.callback_received_event = self._callback_received_event  # Pass event to handler via server

            self._server_thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            self._server_thread.start()
            log(f"Callback server started on http://localhost:{local_port}", "INFO")
            self._httpd = httpd
            return httpd
        except OSError as e:
            log(f"Error starting callback server on port {local_port}: {e}. "
                "Check if the port is already in use.", "ERROR")
            self._httpd = None
            self._server_thread = None
            return None
        except Exception as e:
            log(f"Unexpected error starting callback server: {e}", "ERROR")
            self._httpd = None
            self._server_thread = None
            return None

    def _stop_callback_server(self):
        """Stops the local HTTP server."""
        if self._httpd:
            log("Shutting down callback server...", "INFO")
            self._httpd.shutdown()
            self._httpd.server_close()  # Release the port
            if self._server_thread:
                self._server_thread.join(timeout=2)  # Wait briefly for thread to finish
                if self._server_thread.is_alive():
                    log("Server thread did not shut down cleanly.", "WARNING")
            self._httpd = None
            self._server_thread = None
            self._callback_received_event = None  # Clear the event
            log("Callback server stopped.", "INFO")

    def _exchange_code_for_tokens(self, auth_code: str, code_verifier: str) -> Optional[Dict[str, Any]]:
        """Exchange the authorization code for tokens at the Cognito endpoint."""
        # Get config values
        cognito_config = config_manager.get_cognito_config()
        cognito_domain = cognito_config.get("domain")
        client_id = cognito_config.get("client_id")
        redirect_uri = cognito_config.get("redirect_uri")

        if not cognito_domain or not client_id or not redirect_uri:
            log("Missing Cognito domain, client_id, or redirect_uri in config. Cannot exchange code.", "ERROR")
            return None

        token_url = f"{cognito_domain}/oauth2/token"

        # Client Authentication for Public Clients (No Secret)
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
        }

        payload = {
            "grant_type": "authorization_code",
            "client_id": client_id,
            "code": auth_code,
            "redirect_uri": redirect_uri,
            "code_verifier": code_verifier,
        }

        log("Exchanging authorization code for tokens (public client)...", "INFO")
        try:
            response = requests.post(token_url, data=payload, headers=headers, timeout=15)
            response.raise_for_status()  # Check for HTTP errors
            tokens = response.json()
            log("Successfully received tokens from Cognito.", "INFO")

            # Add received timestamp for expiry checks (optional but useful)
            tokens['received_at'] = time.time()

            return tokens
        except requests.exceptions.HTTPError as e:
            log(f"HTTPError exchanging code for tokens: {e.response.status_code} - {e.response.text}", "ERROR")
            return None
        except requests.exceptions.RequestException as e:
            log(f"RequestException exchanging code for tokens: {e}", "ERROR")
            return None
        except Exception as e:
            log(f"Unexpected error exchanging code for tokens: {e}", "ERROR")
            return None

    def initiate_login(self) -> bool:
        """Starts the Cognito login flow."""
        # Get config values
        cognito_config = config_manager.get_cognito_config()
        cognito_domain = cognito_config.get("domain")
        client_id = cognito_config.get("client_id")
        redirect_uri = cognito_config.get("redirect_uri")
        oauth_scopes = cognito_config.get("scopes")

        if not cognito_domain or not client_id or not redirect_uri or not oauth_scopes:
            log("Cognito config incomplete (domain, client_id, redirect_uri, scopes). Cannot initiate login.", "ERROR")
            return False

        # 1. Start local listener
        server = self._start_callback_server()
        if not server:
            log("Failed to start callback server. Aborting login.", "ERROR")
            return False  # Failed to start server

        # Ensure the event object exists
        if not self._callback_received_event:
            log("Callback event not initialized. Aborting login.", "ERROR")
            self._stop_callback_server()
            return False

        # 2. Create PKCE pair
        code_verifier, code_challenge = make_pkce_pair()

        # 3. Build the authorize URL
        auth_url = (
            f"{cognito_domain}/oauth2/authorize"
            f"?response_type=code"
            f"&client_id={client_id}"
            f"&redirect_uri={urllib.parse.quote_plus(redirect_uri)}"
            f"&scope={urllib.parse.quote(oauth_scopes)}"
            f"&code_challenge_method=S256"
            f"&code_challenge={code_challenge}"
        )

        # 4. Open the browser
        log(f"Opening browser to: {auth_url}", "INFO")
        webbrowser.open(auth_url)

        # 5. Wait for the callback (with timeout)
        log("Waiting for Cognito callback...", "INFO")
        # Wait for the event to be set by the handler, with a timeout
        callback_timed_out = not self._callback_received_event.wait(timeout=180.0)  # 3 minutes timeout

        auth_code = getattr(server, 'auth_code', None)
        auth_error = getattr(server, 'auth_error', None)

        # Always stop the server once the callback is handled or times out
        self._stop_callback_server()

        if callback_timed_out:
            log("Timeout waiting for Cognito callback.", "ERROR")
            return False
        if auth_error:
            log(f"Cognito authentication failed with error: {auth_error}", "ERROR")
            return False
        if not auth_code:
            log("Callback received but no authorization code found.", "ERROR")
            return False

        # 6. Exchange code for tokens
        tokens = self._exchange_code_for_tokens(auth_code, code_verifier)

        if tokens:
            self._tokens = tokens
            self.save_tokens()
            log("Authentication successful. Tokens stored.", "INFO")
            # Notify listeners of successful login
            _notify_listeners("login")
            return True
        else:
            log("Failed to exchange code for tokens.", "ERROR")
            self._tokens = None  # Clear any potentially old tokens
            self.save_tokens()  # Save the cleared state
            return False

    def save_tokens(self):
        """Save the current tokens to a file."""
        token_file = get_token_file_path()
        try:
            with open(token_file, 'w', encoding='utf-8') as f:
                if self._tokens:
                    json.dump(self._tokens, f)
                    log(f"Tokens saved to {token_file}", "DEBUG")
                else:
                    # Write empty content or {} if tokens are None (cleared)
                    f.write("")  # Indicate no tokens / logged out
                    log(f"Tokens cleared in {token_file}", "DEBUG")
        except IOError as e:
            log(f"Error saving tokens to {token_file}: {e}", "ERROR")
        except Exception as e:
            log(f"Unexpected error saving tokens: {e}", "ERROR")

    def load_tokens(self):
        """Load tokens from the storage file."""
        token_file = get_token_file_path()
        if token_file.exists():
            try:
                with open(token_file, 'r', encoding='utf-8') as f:
                    content = f.read()
                    if content:
                        self._tokens = json.loads(content)
                        log(f"Tokens loaded from {token_file}", "INFO")
                    else:
                        self._tokens = None
                        log(f"Token file {token_file} is empty. No tokens loaded.", "INFO")

            except json.JSONDecodeError:
                log(f"Error decoding JSON from token file {token_file}. Discarding.", "ERROR")
                self._tokens = None
                self._clear_token_file()  # Clear corrupted file
            except IOError as e:
                log(f"Error loading tokens from {token_file}: {e}", "ERROR")
                self._tokens = None
            except Exception as e:
                log(f"Unexpected error loading tokens: {e}", "ERROR")
                self._tokens = None
        else:
            log("Token file not found. No tokens loaded.", "INFO")
            self._tokens = None

    def _clear_token_file(self):
        """Utility to delete the token file, e.g., if corrupt."""
        token_file = get_token_file_path()
        try:
            if token_file.exists():
                token_file.unlink()
                log(f"Removed token file: {token_file}", "INFO")
        except OSError as e:
            log(f"Error removing token file {token_file}: {e}", "ERROR")

    def logout(self):
        """Clear current session tokens and log the user out."""
        log("Logging out and clearing tokens.", "INFO")
        self._tokens = None
        self.save_tokens()  # Persist the logged-out state
        # Notify listeners of logout
        _notify_listeners("logout")
        # Optionally revoke tokens with Cognito endpoint if available/needed

    def is_authenticated(self) -> bool:
        """Check if the user is currently considered authenticated (has tokens)."""
        # Basic check: Do we have tokens?
        return self._tokens is not None and "access_token" in self._tokens

    def get_access_token(self) -> Optional[str]:
        """Get the current access token, attempting refresh if expired."""
        if not self._tokens or "access_token" not in self._tokens:
            log("No access token available.", "DEBUG")
            return None

        # Check expiry (requires 'expires_in' and 'received_at' in stored tokens)
        expires_in = self._tokens.get('expires_in')
        received_at = self._tokens.get('received_at')

        if expires_in and received_at:
            # Add a small buffer (e.g., 60 seconds) to refresh before actual expiry
            expiry_time = received_at + expires_in - 60
            if time.time() > expiry_time:
                log("Access token expired or nearing expiry. Attempting refresh...", "INFO")
                if self.refresh_tokens():
                    log("Token refresh successful during access token retrieval.", "INFO")
                    # Return the *new* access token
                    return self._tokens.get("access_token") if self._tokens else None
                else:
                    log("Token refresh failed. User might need to log in again.", "ERROR")
                    self.logout()  # Log out if refresh fails
                    return None
            else:
                # Token is still valid
                return self._tokens["access_token"]
        else:
            # Cannot determine expiry, return current token
            log("Access token expiry information incomplete. Returning current token.", "WARNING")
            return self._tokens["access_token"]

    def get_id_token(self) -> Optional[str]:
        """Get the current ID token, attempting refresh if the related access token seems expired."""
        if not self._tokens or "id_token" not in self._tokens:
            log("No ID token available.", "DEBUG")
            return None

        # NOTE: This checks expiry based on the ACCESS token's lifetime.
        # A proper check would involve decoding the ID token JWT and checking its 'exp' claim.
        # For now, we assume the ID token is valid if the access token is valid.
        expires_in = self._tokens.get('expires_in')
        received_at = self._tokens.get('received_at')

        if expires_in and received_at:
            expiry_time = received_at + expires_in - 60  # 60s buffer
            if time.time() > expiry_time:
                log("Access token (and assumed ID token) expired or nearing expiry. Attempting refresh...", "INFO")
                if self.refresh_tokens():
                    log("Token refresh successful during ID token retrieval.", "INFO")
                    # Return the *new* ID token if available after refresh
                    return self._tokens.get("id_token") if self._tokens else None
                else:
                    log("Token refresh failed. User might need to log in again.", "ERROR")
                    self.logout()  # Log out if refresh fails
                    return None
            else:
                # Token is assumed valid
                return self._tokens["id_token"]
        else:
            # Cannot determine expiry, return current token
            log("ID token expiry information incomplete (based on access token). Returning current ID token.", "WARNING")
            return self._tokens["id_token"]

    def refresh_tokens(self) -> bool:
        """Attempt to refresh the access token using the refresh token."""
        if not self._tokens or "refresh_token" not in self._tokens:
            log("No refresh token available. Cannot refresh.", "WARNING")
            return False

        # Get config values
        cognito_config = config_manager.get_cognito_config()
        cognito_domain = cognito_config.get("domain")
        client_id = cognito_config.get("client_id")

        if not cognito_domain or not client_id:
            log("Cognito Domain or Client ID not configured. Cannot refresh.", "ERROR")
            return False

        token_url = f"{cognito_domain}/oauth2/token"
        payload = {
            "grant_type": "refresh_token",
            "client_id": client_id,
            "refresh_token": self._tokens["refresh_token"],
        }
        headers = {"Content-Type": "application/x-www-form-urlencoded"}

        log("Attempting to refresh tokens...", "INFO")
        try:
            response = requests.post(token_url, data=payload, headers=headers, timeout=15)
            response.raise_for_status()
            new_tokens = response.json()
            log("Successfully refreshed tokens.", "INFO")

            # Cognito refresh response typically includes new access_token, id_token,
            # and expires_in, but *not* a new refresh_token unless configured.
            # Update existing tokens, preserving the refresh token if not included.
            self._tokens["access_token"] = new_tokens["access_token"]
            self._tokens["id_token"] = new_tokens.get(
                "id_token", self._tokens.get("id_token"))  # Keep old ID if not resent
            self._tokens["expires_in"] = new_tokens["expires_in"]
            self._tokens['received_at'] = time.time()  # Update timestamp

            self.save_tokens()
            return True
        except requests.exceptions.HTTPError as e:
            log(f"HTTPError refreshing tokens: {e.response.status_code} - {e.response.text}", "ERROR")
            # If refresh fails (e.g., refresh token expired/revoked), log out
            if e.response.status_code in [
                    400, 401]:  # Bad request or Unauthorized usually means bad/expired refresh token
                log("Refresh token likely invalid or expired. Logging out.", "WARNING")
                # Check if error response indicates invalid grant (e.g., refresh token revoked)
                # Cognito might return error="invalid_grant"
                try:
                    error_data = e.response.json()
                    if error_data.get("error") == "invalid_grant":
                        log("Cognito indicated invalid_grant during refresh. Forcing logout.", "WARNING")
                    else:
                        log(f"Received {e.response.status_code} during refresh, error: {error_data.get('error')}", "WARNING")
                except Exception:
                    log(f"Received {e.response.status_code} during refresh, but couldn't parse error body.", "WARNING")

                self.logout()
            return False
        except requests.exceptions.RequestException as e:
            log(f"RequestException refreshing tokens: {e}", "ERROR")
            return False
        except Exception as e:
            log(f"Unexpected error refreshing tokens: {e}", "ERROR")
            return None

    def cleanup(self):
        """Clean up resources, specifically stopping the callback server if running."""
        if self._httpd:
            log("AuthManager cleaning up callback server...", "INFO")
            self._stop_callback_server()
        else:
            log("AuthManager cleanup: No callback server was running.", "DEBUG")


# --- Singleton Instance ---
_auth_manager_instance = None


def get_auth_manager() -> AuthManager:
    """Get the singleton instance of the AuthManager."""
    global _auth_manager_instance
    if _auth_manager_instance is None:
        log("Initializing AuthManager instance.", "INFO")
        _auth_manager_instance = AuthManager()
    return _auth_manager_instance


# --- Cleanup ---
# It's good practice to ensure the server stops if Blender closes unexpectedly
# or the addon is disabled.
_server_instance_for_cleanup = None  # Keep track if server was started


def register():
    """Called when the Blender addon is enabled."""
    # You might want to initialize the AuthManager here if needed immediately
    # get_auth_manager()
    # pass


def unregister():
    """Called when the Blender addon is disabled. Cleans up the AuthManager."""
    # Attempt to clean up the server if the addon is unregistered
    global _auth_manager_instance

    if _auth_manager_instance:
        log("Cleaning up AuthManager during addon unregistration.", "INFO")
        _auth_manager_instance.cleanup()  # Call the public cleanup method

    _auth_manager_instance = None  # Clear the instance
    log("AuthManager unregistered.", "INFO")

# Example usage (for testing within Blender Python console):
# from . import auth_manager
# am = auth_manager.get_auth_manager()
# am.initiate_login() # Kicks off the browser flow
# am.is_authenticated()
# am.get_access_token()
# am.logout()
