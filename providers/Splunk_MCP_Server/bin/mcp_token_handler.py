import json
import os
import sys
from typing import Any, Dict
from urllib.parse import parse_qs

from splunk.persistconn.application import PersistentServerConnectionApplication

# Ensure the current directory is on sys.path for local imports
_current_dir = os.path.dirname(os.path.abspath(__file__))
if _current_dir not in sys.path:
    sys.path.insert(0, _current_dir)

from crypto import TokenCrypto
from logging_config import get_logger
from settings import MCPSettings
from splunk_api import call_splunk_api

# Module logger
logger = get_logger(__name__)


class MCPTokenHandler(PersistentServerConnectionApplication):
    """
    REST endpoint for creating encrypted MCP tokens.

    This handler provides a simple GET endpoint that creates a Splunk JWT token
    with audience 'mcp', encrypts it, and returns it to the caller.
    """

    def __init__(self, command_line: str, command_arg: str) -> None:
        """Initialize the MCP token handler."""
        super().__init__()
        self.settings = MCPSettings.get()

    def handle(self, in_string: str) -> Dict[str, Any]:
        """
        Handle incoming HTTP requests.

        Supports GET requests with query parameters:
            - username: (required) The user to create the token for
            - expires_on: Token expiration time (e.g., "+30d", "+1h", or ISO datetime)
            - not_before: Token not valid before time (e.g., "+0d", or ISO datetime)

        Args:
            in_string: Raw HTTP request string containing JSON data.

        Returns:
            Dict containing HTTP response with status, headers, and payload.
        """
        logger.info("Received MCP token request")

        try:
            request = json.loads(in_string)
        except json.JSONDecodeError as e:
            logger.error("Invalid JSON in request: %s", e)
            return self._build_response(400, {"error": "Invalid JSON format"})

        method = request.get("method", "GET").upper()

        # Preserve request context for settings (base_url/ssl decisions)
        MCPSettings.set_request_context(request)

        # Get session token (from requireAuthentication=true and passSession=true)
        session = request.get("session") or {}
        auth_token = session.get("authtoken", "")

        if not auth_token:
            logger.error("No session token available")
            return self._build_response(401, {"error": "Authentication required"})

        # Get system auth token for initializing crypto
        system_authtoken = (
            request.get("system_authtoken")
            or request.get("systemAuthtoken")
            or auth_token
        )

        # Parse query parameters
        query_params = self._parse_query_params(request.get("query", []))
        form_params = self._parse_query_params(request.get("form", {}))

        if method == "POST":
            payload = self._parse_payload(request.get("payload"))
            action = None
            if isinstance(payload, dict):
                action = payload.get("action")
            action = action or form_params.get("action") or query_params.get("action")

            if action != "rotate":
                return self._build_response(
                    400,
                    {"error": "Unsupported action. Use action=rotate."},
                )

            key_size_raw = query_params.get("key_size") or form_params.get("key_size")
            if isinstance(payload, dict) and "key_size" in payload:
                key_size_raw = payload.get("key_size")
            key_size = 2048
            if key_size_raw:
                try:
                    key_size = int(key_size_raw)
                except (TypeError, ValueError):
                    return self._build_response(
                        400, {"error": "key_size must be an integer"}
                    )
            if key_size not in (2048, 4096):
                return self._build_response(
                    400, {"error": "key_size must be 2048 or 4096"}
                )

            rotated_at = TokenCrypto.get_instance().rotate_keys(
                system_authtoken, key_size=key_size
            )
            fingerprint = TokenCrypto.get_instance().get_public_key_fingerprint()
            return self._build_response(
                200,
                {
                    "status": "rotated",
                    "key_size": key_size,
                    "rotated_at": rotated_at,
                    "public_key_fingerprint": fingerprint,
                },
            )

        # Initialize TokenCrypto for token minting
        tc = TokenCrypto.get_instance()
        try:
            tc.configure_reload_interval(
                self.settings.token_key_reload_interval_seconds
            )
        except Exception:
            pass

        if not tc.initialize(system_authtoken):
            logger.error("Failed to initialize TokenCrypto")
            return self._build_response(500, {"error": "Internal server error"})

        # Username is required
        username = query_params.get("username", "").strip()
        if not username:
            logger.error("Username missing in request")
            return self._build_response(400, {"error": "Missing username parameter"})

        # Build token payload
        token_payload: Dict[str, Any] = {
            "name": username,
            "audience": "mcp",
        }

        # Add optional parameters if provided
        if "expires_on" in query_params:
            token_payload["expires_on"] = query_params["expires_on"]
        if "not_before" in query_params:
            token_payload["not_before"] = query_params["not_before"]

        # Create token via Splunk REST API
        try:
            response = call_splunk_api(
                session_key=auth_token,
                method="POST",
                api="services/authorization/tokens",
                params={"output_mode": "json"},
                data=token_payload,
            )
        except Exception as exc:
            logger.exception("Failed to call Splunk token API: %s", exc)
            return self._build_response(500, {"error": "Token creation failed"})

        if response.status_code not in (200, 201):
            logger.error(
                "Token creation failed: status %d body %s",
                response.status_code,
                response.text,
            )
            return self._build_response(
                response.status_code,
                {"error": f"Token creation failed: {response.text}"},
            )

        # Extract token from response
        try:
            body = response.json()
            token_entries = body.get("entry", [])
            token_value = (
                token_entries[0].get("content", {}).get("token")
                if token_entries
                else None
            )
        except Exception as exc:
            logger.error("Failed to parse token response: %s", exc)
            token_value = None

        if not token_value:
            logger.error("Token value missing from Splunk response")
            return self._build_response(500, {"error": "Token not returned by Splunk"})

        # Encrypt the token
        try:
            encrypted_token = TokenCrypto.get_instance().encrypt(token_value)
        except Exception as exc:
            logger.exception("Failed to encrypt token: %s", exc)
            return self._build_response(500, {"error": "Token encryption failed"})

        logger.info("Encrypted token created for user %s with audience mcp", username)
        return self._build_response(200, {"token": encrypted_token})

    def _parse_query_params(self, query: Any) -> Dict[str, str]:
        """
        Parse query parameters from Splunk's format.

        Args:
            query: List of query parameter pairs [["key", "value"], ...]

        Returns:
            Dict of parameter names to values.
        """
        params = {}
        if isinstance(query, dict):
            return {str(k): v for k, v in query.items()}
        if isinstance(query, list):
            for pair in query:
                if isinstance(pair, list) and len(pair) >= 2:
                    params[str(pair[0])] = pair[1]
        elif isinstance(query, str):
            parsed = parse_qs(query, keep_blank_values=True)
            for key, values in parsed.items():
                if values:
                    params[key] = values[0]
        return params

    def _parse_payload(self, payload: Any) -> Any:
        if payload is None or isinstance(payload, (dict, list)):
            return payload
        if isinstance(payload, str):
            payload = payload.strip()
            if not payload:
                return None
            try:
                return json.loads(payload)
            except json.JSONDecodeError:
                parsed = parse_qs(payload, keep_blank_values=True)
                if parsed:
                    return {k: v[0] if v else "" for k, v in parsed.items()}
                return payload
        return payload

    def _build_response(self, status: int, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Build HTTP response."""
        return {
            "status": status,
            "headers": {"Content-Type": "application/json"},
            "payload": payload,
        }
