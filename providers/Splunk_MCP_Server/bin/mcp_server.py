"""
MCP REST Server Module.

This module implements the Splunk persistent REST endpoint for handling
MCP (Model Context Protocol) JSON-RPC requests. It provides the main
entry point for MCP communication within the Splunk environment.
"""

import os
import sys
from typing import Any, Dict, Optional

from splunk.persistconn.application import PersistentServerConnectionApplication

# Ensure the current directory is on sys.path for local imports
_current_dir = os.path.dirname(os.path.abspath(__file__))
if _current_dir not in sys.path:
    sys.path.insert(0, _current_dir)

from logging_config import (
    clear_log_context,
    get_logger,
    update_log_context,
    update_log_context_req_id,
)
from mcp_message import MCPMessageHandler
from settings import MCPSettings
from tool_manager import get_default_manager

# Module logger
logger = get_logger(__name__)
logger.info("Initializing MCP REST Handler")

# Initialize crypto early to catch any errors at startup
from crypto import TokenCrypto


class MCPRestHandler(PersistentServerConnectionApplication):
    """
    Persistent REST endpoint implementing MCP JSON-RPC methods for Splunk.

    This class extends Splunk's PersistentServerConnectionApplication to provide
    a long-running REST endpoint that handles MCP protocol messages. It maintains
    state between requests and provides efficient tool management.

    Attributes:
        message_handler: MCPMessageHandler instance for processing requests.
    """

    def __init__(self, command_line: str, command_arg: str) -> None:
        """
        Initialize the MCP REST handler.

        Args:
            command_line: Command line arguments (required by Splunk).
            command_arg: Additional command arguments (required by Splunk).
        """
        super().__init__()

        # Initialize tool manager WITHOUT reload to maintain persistence
        # Only reload on first initialization, not on every request
        tool_manager = get_default_manager(reload=False)
        self.message_handler = MCPMessageHandler(tool_manager)

    def handle(self, in_string: str) -> Dict[str, Any]:
        """
        Handle incoming HTTP requests for MCP protocol.

        This method processes all incoming MCP requests, parsing them through
        the message handler and returning properly formatted responses.

        Args:
            in_string: Raw HTTP request string containing JSON data.

        Returns:
            Dict containing HTTP response with status, headers, and payload.
        """

        # Generate a request ID for logging context
        update_log_context_req_id()

        try:
            # Parse the incoming request
            status_code, parsed_data = self.message_handler.parse_request(in_string)

            if status_code != 200:
                update_log_context(source_ip="unknown")
                logger.warning(
                    "Request parsing failed with status %d and %s",
                    status_code,
                    str(parsed_data),
                )
                return self._build_response(status_code, parsed_data)

            # Extract the RPC request and auth token
            rpc_req = parsed_data["rpc_request"]
            auth_token = parsed_data["auth_token"]
            full_request = parsed_data["full_request"]
            system_authtoken = (
                full_request.get("system_authtoken")
                or full_request.get("systemAuthtoken")
                or ((full_request.get("session") or {}).get("authtoken"))
            )

            endpoint = parsed_data["full_request"]["restmap"]["conf"]["match"]

            # Initialize the TokenCrypto singleton
            tc = TokenCrypto.get_instance()
            try:
                settings = MCPSettings.get()
                tc.configure_reload_interval(settings.token_key_reload_interval_seconds)
            except Exception:
                # Settings should always load, but crypto init must not hard-fail on config issues.
                pass

            if not tc.initialize(system_authtoken):
                logger.error("Failed to initialize TokenCrypto")
                update_log_context(source_ip="unknown")
                return self._build_response(
                    500,
                    {
                        "jsonrpc": "2.0",
                        "id": rpc_req.get("id", None),
                        "error": {
                            "code": -32603,
                            "message": "Internal server error",
                        },
                    },
                )

            try:
                source_ip = parsed_data["full_request"]["connection"]["src_ip"]
                update_log_context(source_ip=source_ip)
            except (KeyError, TypeError):
                pass

            # Handle the RPC method (timing & status logging handled by decorator)
            status_code, content = self.message_handler.handle_rpc_method(
                rpc_req, auth_token, system_authtoken, endpoint
            )

            return self._build_response(status_code, content)

        except Exception as e:
            update_log_context(source_ip="unknown")
            logger.exception("Unexpected error handling MCP request: %s", e)
            return self._build_response(
                500,
                {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {
                        "code": -32603,
                        "message": "Internal server error",
                    },
                },
            )
        finally:
            # Ensure no context leaks into subsequent requests
            clear_log_context()

    def handleStream(self, handle: Any, in_string: str) -> Optional[Any]:
        """
        Handle streaming requests (not implemented for MCP).

        Args:
            handle: Stream handle (unused).
            in_string: Input string (unused).

        Returns:
            None as streaming is not supported.
        """
        logger.warning("Stream handling requested but not supported")
        return None

    def done(self) -> None:
        """
        Cleanup method called when the handler completes handling a request.
        """
        pass

    def _build_response(self, status: int, payload_obj: Any) -> Dict[str, Any]:
        """
        Build a properly formatted HTTP response for Splunk.

        Args:
            status: HTTP status code.
            payload_obj: Response payload object (will be JSON-encoded by Splunk).

        Returns:
            Dict containing complete HTTP response structure.
        """
        return {
            "status": status,
            "headers": {"Content-Type": "application/json"},
            "payload": payload_obj,  # Splunk will JSON encode dict automatically
        }
