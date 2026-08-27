"""
MCP Message Handler Module.

This module provides comprehensive handling of MCP (Model Context Protocol) JSON-RPC
messages, including request parsing, method dispatch, and response formatting.
The module ensures proper error handling and logging throughout the message lifecycle.
"""

from __future__ import annotations

import base64
import json
from typing import Any, Dict, Iterable, List, Tuple, Union

from auth import MCPAuthorization
from crypto import TokenCrypto
from logging_config import get_logger, operation_logger, update_log_context
from settings import MCPSettings
from splunk_api import get_installed_apps
from tool_manager import ArgumentType, Tool, ToolArgument, ToolManager

# Type aliases for better code readability
JsonDict = Union[Dict[str, Any], None]
RpcId = Any

# Module logger
logger = get_logger(__name__)


def decode_jwt_no_verify(token: str):
    # JWT is header.payload.signature
    parts = token.split(".")
    if len(parts) != 3:
        raise ValueError("Invalid JWT structure")

    # JWT uses base64url, which may be missing padding
    def b64url_decode(data: str) -> bytes:
        padding = "=" * (-len(data) % 4)
        return base64.urlsafe_b64decode(data + padding)

    header = json.loads(b64url_decode(parts[0]))
    payload = json.loads(b64url_decode(parts[1]))

    return header, payload


def aud_allows_mcp(aud: Any) -> bool:
    if isinstance(aud, str):
        return aud.lower() == "mcp"
    if isinstance(aud, (list, tuple, set)):
        for entry in aud:
            if isinstance(entry, str) and entry.lower() == "mcp":
                return True
    return False


def is_sis_token(header: Dict[str, Any], payload: Dict[str, Any]) -> bool:
    token_type = header.get("token_type")
    if isinstance(token_type, str) and token_type.lower() == "sis":
        return True
    issuer = payload.get("iss")
    if isinstance(issuer, str) and "/sis" in issuer:
        return True
    return False


class MCPMessageHandler:
    """
    Handles MCP JSON-RPC message processing and response generation.

    This class provides a complete implementation of the MCP protocol,
    including request parsing, method routing, and response formatting.
    It supports the standard MCP methods: initialize, tools/list, and tools/call.

    Attributes:
        tool_manager: Instance of ToolManager for handling tool operations.
    """

    def __init__(self, tool_manager: ToolManager) -> None:
        """
        Initialize the MCP message handler.

        Args:
            tool_manager: ToolManager instance for tool operations.
        """
        self.tool_manager = tool_manager

    def parse_request(self, in_string: str) -> Tuple[int, JsonDict]:
        """
        Parse incoming HTTP request and extract RPC payload.

        This method validates the HTTP request structure, extracts the JSON-RPC
        payload, and prepares authentication tokens for subsequent processing.

        Args:
            in_string: Raw HTTP request string containing JSON data.

        Returns:
            Tuple containing:
                - int: HTTP status code (200 for success, 4xx/5xx for errors)
                - JsonDict: Parsed data or error response

        Raises:
            No exceptions are raised; all errors are returned as status codes.
        """
        logger.debug("Parsing incoming request")

        try:
            request = json.loads(in_string)
        except json.JSONDecodeError as e:
            logger.error("Invalid JSON in request: %s", e)
            return 400, {"error": "Invalid JSON format"}

        # Preserve request context for settings (base_url/ssl decisions)
        MCPSettings.set_request_context(request)

        # Validate HTTP method
        http_method = request.get("method", "POST").upper()
        if http_method == "GET":
            logger.warning("GET method not allowed for MCP requests")
            return 405, {"message": "Method not allowed"}
        if http_method != "POST":
            logger.warning("Unsupported HTTP method: %s", http_method)
            return 405, {"message": f"HTTP method {http_method} not allowed"}

        # Extract and parse RPC payload
        raw_payload = request.get("payload", "")
        try:
            rpc_req = (
                json.loads(raw_payload)
                if isinstance(raw_payload, (str, bytes))
                else raw_payload
            )
        except Exception as e:
            logger.error("Failed to parse RPC payload: %s", e)
            return 400, {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": "Parse error"},
            }

        # Get auth token from headers array
        auth_token = ""
        headers = request.get("headers", [])

        # Headers are in array format: [["Header-Name", "Header-Value"], ...]
        if isinstance(headers, list):
            for header_pair in headers:
                if isinstance(header_pair, list) and len(header_pair) >= 2:
                    header_name = header_pair[0].lower()
                    header_value = header_pair[1]
                    if header_name == "authorization":
                        auth_token = header_value
                        break

        # Extract bearer token
        if auth_token.lower().startswith("bearer ") or auth_token.lower().startswith(
            "splunk "
        ):
            auth_token = auth_token[7:].strip()
        else:
            # If auth token is Base64-encoded Basic Auth credentials, obtain token
            if auth_token.lower().startswith("basic "):
                try:
                    from splunk_api import call_splunk_api

                    b64_encoded = auth_token[6:].strip()
                    decoded_bytes = base64.b64decode(b64_encoded)
                    decoded_str = decoded_bytes.decode("utf-8")
                    # Basic auth format is "username:password"
                    parts = decoded_str.split(":", 1)
                    if len(parts) == 2:
                        username, password = parts
                        # Call Splunk /services/auth/login API with output_mode=json using call_splunk_api
                        api = "/services/auth/login"
                        params = {"output_mode": "json"}
                        data = {"username": username, "password": password}
                        response = call_splunk_api(
                            session_key="",  # No token yet
                            method="POST",
                            api=api,
                            headers={"Accept": "application/json"},
                            params=params,
                            data=data,
                        )
                        if response.status_code == 200:
                            resp_json = response.json()
                            auth_token = resp_json.get("sessionKey", "")
                            logger.info("Obtained sessionKey from Splunk login API")
                            if not auth_token:
                                logger.error(
                                    "sessionKey not found in Splunk JSON response"
                                )
                        else:
                            return response.status_code, {
                                "jsonrpc": "2.0",
                                "id": None,
                                "message": f"Authentication failed: {response.text}",
                            }
                except Exception as e:
                    return 401, {
                        "jsonrpc": "2.0",
                        "id": None,
                        "message": f"Authentication failed: {e}",
                    }

        logger.debug("Request parsed successfully, method: %s", rpc_req.get("method"))
        return 200, {
            "rpc_request": rpc_req,
            "auth_token": auth_token,
            "full_request": request,
        }

    @operation_logger("rpc_request")
    def handle_rpc_method(
        self, rpc_req: JsonDict, auth_token: str, system_authtoken: str, endpoint: str
    ) -> Tuple[int, JsonDict]:
        """
        Route and handle specific RPC method calls.

        This method dispatches incoming RPC requests to the appropriate handler
        based on the method name. It provides comprehensive error handling and
        logging for all supported MCP methods.

        Args:
            rpc_req: JSON-RPC request object containing method and parameters.
            auth_token: Authentication token for Splunk API calls.
            system_authtoken: System authentication token from the full request.
            endpoint: The REST endpoint being accessed.

        Returns:
            Tuple containing:
                - int: HTTP status code
                - JsonDict: JSON-RPC response or error object
        """
        rpc_id = rpc_req.get("id", None)
        rpc_method = rpc_req.get("method")
        params = rpc_req.get("params", {}) or {}

        # Update logging context with RPC method information
        update_log_context(rpc_method=rpc_method, rpc_id=rpc_id)

        try:
            # No aud check for ping and list_tools
            # as they are not part of MCP spec

            # Public methods without authentication

            # Ping method for health checks
            # Note: Not part of MCP spec, but useful for diagnostics
            if rpc_method == "ping":
                logger.debug("Processing ping request")
                return 200, {
                    "jsonrpc": "2.0",
                    "id": rpc_id,
                    "result": {"message": "pong"},
                }

            if rpc_method == "get_encryption_config":
                logger.debug("Processing get_public_key request")
                enc_config = TokenCrypto.get_instance().get_encryption_config()
                return 200, {
                    "jsonrpc": "2.0",
                    "id": rpc_id,
                    "result": enc_config,
                }

            # MCP Internal Methods, authentication required, capabilities and aud not checked

            # Validate token using MCPAuthorization, also decrypt token if needed
            (
                is_valid,
                auth_token,
                user_info,
                error_message,
                was_cui_exchange,
            ) = MCPAuthorization.validate_token(auth_token)
            if not is_valid:
                logger.warning("Token validation failed: %s", error_message)
                return 401, {
                    "jsonrpc": "2.0",
                    "id": rpc_id,
                    "error": {
                        "code": -32600,
                        "message": f"Authentication failed: {error_message}",
                    },
                }

            # Update logging context with user information
            if user_info:
                update_log_context(username=user_info.get("username"))
                logger.debug(
                    "Token validated successfully for user: %s",
                    user_info.get("username"),
                )

            # Decode JWT without verification to check 'aud' claim
            # Skip aud check for exchanged CUI tokens and SIS-minted tokens
            if not was_cui_exchange:
                try:
                    header, payload = decode_jwt_no_verify(auth_token)
                    if not is_sis_token(header, payload):
                        aud = payload.get("aud")
                        if not aud_allows_mcp(aud):
                            logger.warning("Invalid token audience: %s", aud)
                            return 403, {
                                "jsonrpc": "2.0",
                                "id": rpc_id,
                                "error": {
                                    "code": -32600,
                                    "message": f"Invalid token audience: {aud}",
                                },
                            }
                except Exception as e:
                    logger.error("Failed to decode bearer token: %s", e)
                    return 403, {
                        "jsonrpc": "2.0",
                        "id": rpc_id,
                        "error": {
                            "code": -32600,
                            "message": "Failed to decode bearer token",
                        },
                    }

            # Check if the user has MCP capability
            if not MCPAuthorization.check_user_capabilities(
                user_info, ["mcp_tool_execute"]
            ):
                logger.warning("User lacks required MCP capabilities")
                return 403, {
                    "jsonrpc": "2.0",
                    "id": rpc_id,
                    "error": {
                        "code": -32600,
                        "message": "User lacks required mcp_tool_execute capability",
                    },
                }

            if rpc_method == "initialize":
                return self._handle_initialize(rpc_id)
            if rpc_method == "tools/list":
                return self._handle_tools_list(rpc_id, auth_token, system_authtoken)
            if rpc_method == "tools/call":
                return self._handle_tools_call(
                    rpc_id, params, auth_token, system_authtoken
                )
            if rpc_method == "notifications/initialized":
                logger.debug("Processing notifications/initialized request")
                return 200, None
            else:
                logger.debug("Unknown RPC method requested: %s", rpc_method)
                return 200, {
                    "jsonrpc": "2.0",
                    "id": rpc_id,
                    "error": {
                        "code": -32601,
                        "message": f"Method '{rpc_method}' not found",
                    },
                }
        except Exception as e:
            logger.exception("Internal error dispatching method %s: %s", rpc_method, e)
            return 500, {
                "jsonrpc": "2.0",
                "id": rpc_id,
                "error": {"code": -32603, "message": "Internal error"},
            }

    def _handle_initialize(self, rpc_id: RpcId) -> Tuple[int, JsonDict]:
        """
        Handle the 'initialize' RPC method.

        This method returns server capabilities and metadata as required
        by the MCP protocol initialization sequence.

        Args:
            rpc_id: JSON-RPC request identifier.

        Returns:
            Tuple containing status code and initialization response.
        """
        logger.debug("Processing initialize request")

        settings = MCPSettings.get()
        content = {
            "jsonrpc": "2.0",
            "id": rpc_id,
            "result": {
                "protocolVersion": "2025-03-26",
                "capabilities": {"tools": {}},
                "serverInfo": {
                    "name": settings.app_name,
                    "version": settings.app_version,
                },
            },
        }

        logger.info("Initialize request completed successfully")
        return 200, content

    def _handle_tools_list(
        self, rpc_id: RpcId, auth_token: str, system_authtoken: str
    ) -> Tuple[int, JsonDict]:
        """
        Handle the 'tools/list' RPC method.

        This method returns a list of all available tools that are currently
        enabled in the system.

        Args:
            rpc_id: JSON-RPC request identifier.

        Returns:
            Tuple containing status code and tools list response.
        """
        logger.debug("Processing tools/list request")

        # Get a list of installed application
        installed_apps = get_installed_apps(auth_token)
        logger.debug(f"Installed apps: {installed_apps}")

        # Ensure enabled tool map is refreshed using the system session key so
        # KV-backed enablement state is honored.
        try:
            self.tool_manager.refresh_tools_for_listing(system_authtoken)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("Failed to refresh tools with system auth token: %s", exc)

        tools = self.tool_manager.list_tools(
            enabled_only=True, installed_apps=set(installed_apps.get("apps", []))
        )
        content = build_call_tool_message(tools, rpc_id=rpc_id)

        logger.info("Tools list request completed, %d tools available", len(tools))
        return 200, content

    def _handle_tools_call(
        self, rpc_id: RpcId, params: JsonDict, auth_token: str, system_authtoken: str
    ) -> Tuple[int, JsonDict]:
        """
        Handle the 'tools/call' RPC method.

        This method executes a specific tool with provided arguments and
        returns the result in the proper MCP format.

        Args:
            rpc_id: JSON-RPC request identifier.
            params: Parameters containing tool name and arguments.
            auth_token: Authentication token for tool execution.

        Returns:
            Tuple containing status code and tool execution response.
        """
        tool_name = params.get("name")
        tool_args = params.get("arguments", {})
        update_log_context(tool_name=tool_name)

        logger.debug("Processing tools/call request for tool: %s", tool_name)

        if not tool_name:
            logger.error("Tool name missing in tools/call request")
            return 400, {
                "jsonrpc": "2.0",
                "id": rpc_id,
                "error": {"code": -32602, "message": "Missing tool name"},
            }

        # Refresh enabled tool map using system session key (lighter than full refresh)
        try:
            self.tool_manager.refresh_enabled_tools(system_authtoken)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning(
                "Failed to refresh enabled tools with system auth token: %s", exc
            )

        tool = self.tool_manager.get_tool(tool_name)
        if not tool:
            logger.error("Tool not found: %s", tool_name)
            return 404, {
                "jsonrpc": "2.0",
                "id": rpc_id,
                "error": {
                    "code": -32004,
                    "message": f"Tool '{tool_name}' not found",
                },
            }

        try:
            result = tool.execute(auth_token, tool_args)
            return self._format_tool_result(rpc_id, result)
        except Exception as e:
            logger.exception("Tool execution failed for %s: %s", tool_name, e)
            return 200, {
                "jsonrpc": "2.0",
                "id": rpc_id,
                "result": {
                    "content": [{"type": "text", "text": str(e)}],
                    "isError": True,
                },
            }

    def _format_tool_result(self, rpc_id: RpcId, result: Any) -> Tuple[int, JsonDict]:
        """
        Format tool execution result into proper MCP response format.

        This method handles both successful results and error conditions,
        ensuring proper JSON serialization and MCP-compliant formatting.

        Args:
            rpc_id: JSON-RPC request identifier.
            result: Raw result from tool execution.

        Returns:
            Tuple containing status code and formatted response.
        """
        if isinstance(result, dict) and "error" in result:
            logger.debug("Formatting error result for RPC %s", rpc_id)
            content = {
                "jsonrpc": "2.0",
                "id": rpc_id,
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": result.get("error", "Tool execution error"),
                        }
                    ],
                    "isError": True,
                },
            }
        else:
            logger.debug("Formatting success result for RPC %s", rpc_id)

            # MCP expects machine-readable payloads in structuredContent.
            # The `content` field is for compact, human-readable text.
            structured = result if isinstance(result, dict) else {"value": result}

            # Provide a small summary rather than duplicating the full JSON payload.
            summary = "Tool executed successfully."
            if isinstance(result, dict):
                if "status_code" in result:
                    summary = (
                        "Tool executed successfully "
                        f"(status_code={result.get('status_code')})."
                    )
                elif "results" in result and isinstance(result.get("results"), list):
                    count = len(result.get("results") or [])
                    noun = "result" if count == 1 else "results"
                    summary = f"Tool executed successfully ({count} {noun})."
            else:
                try:
                    preview = json.dumps(result, separators=(",", ":"))
                except Exception:
                    preview = str(result)
                # Avoid very large content payloads.
                if len(preview) > 256:
                    preview = preview[:256] + "…"
                summary = f"Tool executed successfully. Value preview: {preview}"

            content = {
                "jsonrpc": "2.0",
                "id": rpc_id,
                "result": {
                    "content": [{"type": "text", "text": summary}],
                    "structuredContent": structured,
                },
            }

        return 200, content


def _arg_to_schema(arg: ToolArgument) -> JsonDict:
    """
    Convert a ToolArgument into JSON Schema property definition.

    This function extracts type information, validation rules, default values,
    and other metadata to create a proper JSON Schema property definition
    that can be used in MCP tool specifications.

    Args:
        arg: ToolArgument instance to convert.

    Returns:
        JsonDict containing JSON Schema property definition.
    """
    prop: JsonDict = {"type": arg.type.value}

    # Add description if available
    if arg.description:
        prop["description"] = arg.description

    # Add default value if provided
    if arg.default is not None:
        prop["default"] = arg.default

    # Add numeric bounds for number/integer types
    if arg.type in (ArgumentType.NUMBER, ArgumentType.INTEGER):
        if arg.min is not None:
            prop["minimum"] = arg.min
        if arg.max is not None:
            prop["maximum"] = arg.max

    # Add enumeration values if specified
    if arg.enum is not None:
        enum_vals = list(arg.enum)
        if enum_vals:
            prop["enum"] = enum_vals

    # Add placeholder as example
    if arg.placeholder is not None and str(arg.placeholder).strip():
        examples = prop.get("examples", [])
        examples.append(arg.placeholder)
        prop["examples"] = examples

    # Add validation pattern if specified
    validation = getattr(arg, "validation", None)
    pattern = getattr(validation, "pattern", None) if validation else None
    if pattern:
        prop["pattern"] = pattern
        message = getattr(validation, "message", None)
        if message:
            desc = prop.get("description", "")
            prop["description"] = (
                desc + ("\n" if desc else "") + f"Validation: {message}"
            ).strip()

    return prop


def _tool_to_public_dict(tool: Tool) -> JsonDict:
    """
    Convert internal Tool object to public tool description with inputSchema.

    This function transforms internal tool representations into the format
    required by the MCP protocol, including proper JSON Schema generation
    for tool arguments.

    Args:
        tool: Tool instance or dict to convert.

    Returns:
        JsonDict containing public tool description with input schema.
    """
    # Handle both Tool objects and pre-serialized dicts
    if isinstance(tool, dict):
        name = tool.get("name")
        description = tool.get("description", "")
        args_iter = tool.get("arguments", [])
        args_objects = list(args_iter)
    else:
        name = getattr(tool, "name")
        description = getattr(tool, "description", "")
        args_objects = getattr(tool, "arguments", [])

    properties: JsonDict = {}
    required: List[str] = []

    # Process each argument to build schema properties
    for arg in args_objects:
        if isinstance(arg, dict):
            # Handle dict-style arguments
            arg_name = arg.get("name")
            if not arg_name:
                continue

            # Create temporary object for schema conversion
            fake_arg = type("Arg", (), arg)
            schema = _arg_to_schema(fake_arg)  # type: ignore[arg-type]
            properties[arg_name] = schema

            if arg.get("required"):
                required.append(arg_name)
        else:
            # Handle ToolArgument objects
            arg_name = getattr(arg, "name", None)
            if not arg_name:
                continue

            properties[arg_name] = _arg_to_schema(arg)
            if getattr(arg, "required", False):
                required.append(arg_name)

    # Build complete input schema
    input_schema: JsonDict = {
        "type": "object",
        "properties": properties,
    }
    if required:
        input_schema["required"] = required

    return {
        "name": name,
        "description": description,
        "inputSchema": input_schema,
    }


def build_call_tool_message(tools: Iterable[Tool], rpc_id: RpcId) -> JsonDict:
    """
    Build a JSON-RPC 2.0 response listing available tools.

    This function creates a properly formatted MCP tools/list response
    containing all available tools with their schemas and metadata.

    Args:
        tools: Iterable of Tool objects or pre-serialized dicts.
        rpc_id: JSON-RPC request identifier to echo back.

    Returns:
        JsonDict matching the JSON-RPC 2.0 response format.
    """
    tool_list = [_tool_to_public_dict(tool) for tool in tools]
    logger.debug("Built tool list message with %d tools", len(tool_list))

    return {
        "jsonrpc": "2.0",
        "id": rpc_id,
        "result": {"tools": tool_list},
    }


# Public API exports
__all__ = [
    "MCPMessageHandler",
    "build_call_tool_message",
]
