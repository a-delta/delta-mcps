"""
Tool Manager Module.

This module provides comprehensive management of MCP tools, including tool discovery,
validation, execution, and argument processing. It handles both built-in tools and
custom tool definitions loaded from configuration files.
"""

import http
import json
import re
import threading
import time
from copy import deepcopy
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple, Union

from kvstore_manager import KVStoreManager
from logging_config import get_logger
from settings import MCPSettings


def _coerce_bool(value: Any) -> bool:
    """Coerce various representations into a boolean."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes"}:
            return True
        if lowered in {"false", "0", "no"}:
            return False
        return False
    if isinstance(value, (int, float)):
        return value != 0
    return False


from splunk_api import (
    call_splunk_api,
    check_spl_safe,
    normalize_search_command,
    run_splunk_query_internal,
)

# Module logger
logger = get_logger(__name__)

# Thread-safe singleton lock
_manager_lock = threading.Lock()
_default_manager: Optional["ToolManager"] = None
_ENABLED_SEED_MARKER_KEY = "__mcp_tools_seeded__"


class ArgumentType(Enum):
    """
    Enumeration of supported argument types for tool parameters.

    This enum defines the types that can be used for tool arguments,
    ensuring type safety and proper validation.
    """

    STRING = "string"
    INTEGER = "integer"
    NUMBER = "number"
    BOOLEAN = "boolean"

    @classmethod
    def from_string(cls, value: str) -> "ArgumentType":
        """
        Convert string representation to ArgumentType enum.

        Args:
            value: String value to convert.

        Returns:
            ArgumentType enum value, defaults to STRING if not recognized.
        """
        if not value:
            return cls.STRING

        normalized_value = value.lower().strip()

        type_mapping = {
            "string": cls.STRING,
            "integer": cls.INTEGER,
            "number": cls.NUMBER,
            "boolean": cls.BOOLEAN,
        }

        result = type_mapping.get(normalized_value, cls.STRING)
        if result == cls.STRING and normalized_value not in type_mapping:
            logger.warning("Unknown argument type '%s', defaulting to STRING", value)

        return result


@dataclass
class ToolArgumentValidation:
    """
    Validation rules for tool arguments.

    This class encapsulates regex patterns and error messages
    for validating tool argument values.

    Attributes:
        pattern: Optional regex pattern for validation.
        message: Optional custom error message for validation failures.
    """

    pattern: Optional[str] = None
    message: Optional[str] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ToolArgumentValidation":
        """
        Create validation instance from dictionary data.

        Args:
            data: Dictionary containing validation configuration.

        Returns:
            ToolArgumentValidation instance.
        """
        if not data:
            return cls()

        return cls(
            pattern=data.get("pattern"),
            message=data.get("message"),
        )


@dataclass
class ToolArgument:
    """
    Definition of a single tool argument.

    This class represents a complete argument specification including
    type information, validation rules, and metadata for UI generation.

    Attributes:
        name: Argument name.
        type: Argument type from ArgumentType enum.
        description: Human-readable description.
        required: Whether the argument is mandatory.
        placeholder: Example value for UI hints.
        default: Default value if not provided.
        group: Logical grouping for UI organization.
        display_order: Ordering hint for UI display.
        min: Minimum value for numeric types.
        max: Maximum value for numeric types.
        enum: List or dict of allowed values.
        quoted: Whether string values should be quoted in SPL.
        validation: Validation rules for the argument.
    """

    name: str
    type: ArgumentType = ArgumentType.STRING
    description: str = ""
    required: bool = False
    placeholder: Optional[str] = None
    default: Optional[Any] = None
    group: Optional[str] = None
    display_order: Optional[int] = None
    min: Optional[float] = None
    max: Optional[float] = None
    enum: Optional[Union[List[Any], Dict[str, Any]]] = None
    quoted: bool = True
    validation: ToolArgumentValidation = field(default_factory=ToolArgumentValidation)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ToolArgument":
        """
        Create ToolArgument from dictionary configuration.

        Args:
            data: Dictionary containing argument configuration.

        Returns:
            ToolArgument instance.

        Raises:
            KeyError: If required 'name' field is missing.
        """
        if "name" not in data:
            raise KeyError("Tool argument must have 'name' field")

        # Extract needs_quoting from _meta.formatting.needs_quoting if present
        arg_type = ArgumentType.from_string(data.get("type", "string"))
        quoted = arg_type == ArgumentType.STRING
        meta = data.get("_meta", {})
        formatting = meta.get("formatting", {}) if isinstance(meta, dict) else {}
        if "needs_quoting" in formatting:
            quoted = formatting["needs_quoting"]

        return cls(
            name=data["name"],
            type=ArgumentType.from_string(data.get("type", "string")),
            description=data.get("description", ""),
            required=data.get("required", False),
            placeholder=data.get("placeholder"),
            default=data.get("default"),
            group=data.get("group"),
            display_order=data.get("display_order"),
            min=data.get("min"),
            max=data.get("max"),
            enum=data.get("enum"),
            quoted=quoted,
            validation=ToolArgumentValidation.from_dict(data.get("validation", {})),
        )


@dataclass
class ToolExample:
    """
    Example usage for a tool.

    This class provides example invocations and expected outcomes
    for tools, useful for documentation and testing.

    Attributes:
        name: Example name/title.
        description: Description of what the example demonstrates.
        arguments: Example argument values.
        expected_use: Description of expected use case.
    """

    name: str
    description: str = ""
    arguments: Dict[str, Any] = field(default_factory=dict)
    expected_use: Optional[str] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ToolExample":
        """
        Create ToolExample from dictionary configuration.

        Args:
            data: Dictionary containing example configuration.

        Returns:
            ToolExample instance.
        """
        return cls(
            name=data.get("name", ""),
            description=data.get("description", ""),
            arguments=data.get("arguments", {}),
            expected_use=data.get("expected_use"),
        )

    def to_dict(self) -> Dict[str, Any]:
        """Serialize the example back to a dictionary."""
        result: Dict[str, Any] = {
            "name": self.name,
            "description": self.description,
            "arguments": self.arguments or {},
        }
        if self.expected_use is not None:
            result["expected_use"] = self.expected_use
        return result


@dataclass
class Tool:
    """
    Complete tool definition with execution capabilities.

    This class represents a complete MCP tool including metadata,
    argument specifications, SPL query template, and execution logic.

    Attributes:
        name: Unique tool identifier.
        description: Human-readable description.
        tags: List of descriptive tags.
        spl: SPL query template for execution.
        arguments: List of tool arguments.
        examples: List of usage examples.
        raw: Original configuration data.
        row_limiter: Whether tool adds row limiting in the argument.
        time_range: Whether tool supports time range arguments.
        required_app: Name of Splunk app required for this tool to function (optional).
    """

    name: str
    description: Optional[str]
    tags: List[str]
    _key: str
    spl: str = ""
    arguments: List[ToolArgument] = field(default_factory=list)
    examples: List[ToolExample] = field(default_factory=list)
    raw: Dict[str, Any] = field(default_factory=dict)
    row_limiter: bool = True
    time_range: bool = True
    required_app: Optional[str] = None
    external_app: Optional[str] = None
    built_in: bool = False
    title: Optional[str] = None
    guardrails: bool = False
    schema_format: str = "legacy"
    execution_type: str = "spl"
    api_method: Optional[str] = None
    api_endpoint: Optional[str] = None
    api_headers: Dict[str, Any] = field(default_factory=dict)
    api_params: Dict[str, Any] = field(default_factory=dict)
    api_body: Optional[Any] = None
    saved_search: Optional[Dict[str, Any]] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Tool":
        """
        Create Tool instance from dictionary configuration.

        Args:
            data: Dictionary containing tool configuration.

        Returns:
            Tool instance with injected standard arguments.

        Raises:
            ValueError: If required fields are missing.
        """
        schema_format = "legacy"
        raw_source = data
        if "inputSchema" in data and "_meta" in data:
            raw_source = data
            data = cls._convert_from_new_schema(data)
            schema_format = "new"

        # Validate required fields for internal representation
        execution_type = (data.get("execution_type") or "spl").lower()
        required_fields = ["name"]
        if execution_type == "spl":
            required_fields.append("spl")
        missing_fields = [field for field in required_fields if field not in data]
        if missing_fields:
            raise ValueError(
                f"Tool definition missing required fields {missing_fields}"
            )

        logger.debug("Creating tool from configuration: %s", data.get("name"))
        key = data.get("_key")
        if not isinstance(key, str) or not key.strip():
            raise ValueError(
                f"Tool definition '{data.get('name', 'unknown')}' is missing a valid '_key'."
            )
        key = key.strip()

        tool = cls(
            name=data["name"],
            title=data.get("title"),
            description=data.get("description"),
            tags=data.get("tags", []),
            spl=data["spl"],
            arguments=[
                ToolArgument.from_dict(arg) for arg in data.get("arguments", [])
            ],
            examples=[ToolExample.from_dict(ex) for ex in data.get("examples", [])],
            raw=raw_source,
            row_limiter=data.get("row_limiter", True),
            time_range=data.get("time_range", True),
            required_app=data.get("required_app"),
            external_app=data.get("external_app"),
            built_in=_coerce_bool(data.get("built_in", False)),
            _key=key,
            guardrails=_coerce_bool(data.get("guardrails", False)),
            schema_format=schema_format,
            execution_type=execution_type,
            api_method=data.get("api_method"),
            api_endpoint=data.get("api_endpoint"),
            api_headers=data.get("api_headers") or {},
            api_params=data.get("api_params") or {},
            api_body=data.get("api_body"),
            saved_search=data.get("saved_search"),
        )

        tool._inject_standard_arguments()
        logger.debug(
            "Created tool '%s' with %d arguments", tool.name, len(tool.arguments)
        )
        return tool

    @classmethod
    def _convert_from_new_schema(cls, data: Dict[str, Any]) -> Dict[str, Any]:
        """Convert new external schema payload into internal representation."""
        meta = data.get("_meta") or {}
        execution = meta.get("execution") or {}
        external_app_id = meta.get("external_app_id")
        if not external_app_id:
            raise ValueError("Tool definition missing '_meta.external_app_id'.")

        exec_type = (execution.get("type") or "spl").lower()

        properties = (data.get("inputSchema") or {}).get("properties", {})
        required_args = set((data.get("inputSchema") or {}).get("required", []) or [])
        arguments: List[Dict[str, Any]] = []
        for arg_name, prop in properties.items():
            if not isinstance(prop, dict):
                continue
            arg: Dict[str, Any] = {
                "name": arg_name,
                "type": prop.get("type", "string"),
                "description": prop.get("description", ""),
                "required": arg_name in required_args,
                "_meta": prop.get("_meta", {}),
            }
            if "default" in prop:
                arg["default"] = prop.get("default")
            if "enum" in prop:
                arg["enum"] = prop.get("enum")
            if "minimum" in prop:
                arg["min"] = prop.get("minimum")
            if "maximum" in prop:
                arg["max"] = prop.get("maximum")

            pattern = prop.get("pattern")
            validation_message = prop.get("validation_message")
            validation: Dict[str, Any] = {}
            if pattern:
                validation["pattern"] = pattern
            if validation_message:
                validation["message"] = validation_message
            if validation:
                arg["validation"] = validation
            arguments.append(arg)

        # Prefix the tool name with the configured name prefix for uniqueness.
        name_prefix = meta.get("name_prefix")
        if not isinstance(name_prefix, str) or not name_prefix.strip():
            name_prefix = external_app_id
        else:
            name_prefix = name_prefix.strip()

        orig_name = data.get("name")
        if (
            orig_name
            and isinstance(orig_name, str)
            and name_prefix
            and orig_name.startswith(f"{name_prefix}_")
        ):
            unique_name = orig_name
        else:
            unique_name = f"{name_prefix}_{orig_name}" if name_prefix else orig_name

        converted: Dict[str, Any] = {
            "name": unique_name,
            "title": data.get("title"),
            "description": data.get("description"),
            "tags": meta.get("tags", []),
            "required_app": meta.get("required_app"),
            "external_app": external_app_id,
            "arguments": arguments,
            "examples": meta.get("examples", []),
            "built_in": _coerce_bool(meta.get("built_in", False)),
            "execution_type": exec_type,
        }

        # Preserve saved_search metadata if present
        saved_search = meta.get("saved_search")
        if isinstance(saved_search, dict):
            converted["saved_search"] = deepcopy(saved_search)

        if exec_type == "spl":
            template = execution.get("template")
            if not isinstance(template, str) or not template.strip():
                raise ValueError("Execution template is required for SPL tools.")
            if any(
                execution.get(field)
                for field in ("method", "endpoint", "headers", "params", "body")
            ):
                raise ValueError("SPL tools cannot define API execution fields.")
            converted["spl"] = template.strip()
            converted["row_limiter"] = _coerce_bool(execution.get("row_limiter", True))
            converted["time_range"] = _coerce_bool(execution.get("time_range", True))
            converted["guardrails"] = _coerce_bool(execution.get("guardrails", False))
        elif exec_type == "api":
            method = execution.get("method")
            endpoint = execution.get("endpoint")
            if not isinstance(method, str) or not method.strip():
                raise ValueError("API tools require a HTTP 'method'.")
            if not isinstance(endpoint, str) or not endpoint.strip():
                raise ValueError("API tools require an 'endpoint'.")
            if execution.get("template"):
                raise ValueError("API tools cannot define SPL templates.")
            converted["spl"] = ""
            converted["row_limiter"] = False
            converted["time_range"] = False
            converted["guardrails"] = False
            converted["api_method"] = method.strip().upper()
            converted["api_endpoint"] = endpoint.strip()
            headers = execution.get("headers") or {}
            params = execution.get("params") or {}
            if headers and not isinstance(headers, dict):
                raise ValueError("API headers must be an object.")
            if params and not isinstance(params, dict):
                raise ValueError("API params must be an object.")
            converted["api_headers"] = (
                deepcopy(headers) if isinstance(headers, dict) else {}
            )
            converted["api_params"] = (
                deepcopy(params) if isinstance(params, dict) else {}
            )
            if "body" in execution:
                converted["api_body"] = deepcopy(execution.get("body"))
            else:
                converted["api_body"] = None
        else:
            raise ValueError("Unsupported execution type. Allowed: spl, api.")

        if "_key" in data:
            converted["_key"] = data["_key"]
        return converted

    def _inject_standard_arguments(self) -> None:
        """
        This method adds common arguments like earliest_time, latest_time,
        and row_limit to tools that don't already define them.
        """
        if self.execution_type != "spl":
            return

        existing_args = {arg.name for arg in self.arguments}
        settings = MCPSettings.get()
        standard_args: List[ToolArgument] = []

        # Add time range arguments if enabled
        if self.time_range:
            if "earliest_time" not in existing_args:
                standard_args.append(
                    ToolArgument(
                        name="earliest_time",
                        type=ArgumentType.STRING,
                        description="Start time for search (e.g., -24h, -1d)",
                        required=False,
                        default="-24h",
                        placeholder="-24h",
                        quoted=False,
                    )
                )

            if "latest_time" not in existing_args:
                standard_args.append(
                    ToolArgument(
                        name="latest_time",
                        type=ArgumentType.STRING,
                        description="End time for search (e.g., now, -1h)",
                        required=False,
                        default="now",
                        placeholder="now",
                        quoted=False,
                    )
                )

        # Add row limit argument
        if self.row_limiter and "row_limit" not in existing_args:
            standard_args.append(
                ToolArgument(
                    name="row_limit",
                    type=ArgumentType.INTEGER,
                    description="Maximum number of rows to return",
                    required=False,
                    default=settings.default_row_limit,
                    min=1,
                    max=settings.max_row_limit,
                )
            )

        if standard_args:
            self.arguments.extend(standard_args)
            logger.debug(
                "Injected %d standard arguments for tool: %s",
                len(standard_args),
                self.name,
            )

    def execute(self, session_key: str, args: Dict[str, Any]) -> Dict[str, Any]:
        """
        Execute the tool with provided arguments.

        This method validates arguments, builds the SPL query, and executes
        it against Splunk, returning the formatted results.

        Args:
            session_key: Splunk authentication token.
            args: Dictionary of argument values.

        Returns:
            Dictionary containing execution results or error information.

        Raises:
            Exception: For validation errors or execution failures.
        """
        logger.info("Executing tool '%s' with args: %s", self.name, args)

        # Validate arguments
        validation_error = self._validate_arguments(args)
        if validation_error:
            logger.error(
                "Argument validation failed for tool '%s': %s",
                self.name,
                validation_error,
            )
            raise ValueError(validation_error)

        # Normalize and prepare arguments
        normalized_args = self._normalize_arguments(args)
        logger.debug("Normalized arguments: %s", normalized_args)

        normalized_args = self._normalize_arguments(args)
        logger.debug("Normalized arguments: %s", normalized_args)

        if self.execution_type == "api":
            return self._execute_api(session_key, normalized_args)

        # Build SPL query
        try:
            spl_query = self._build_spl_query(normalized_args)
            logger.debug("Built SPL query: %s", spl_query)
        except Exception as e:
            logger.error("Failed to build SPL query for tool '%s': %s", self.name, e)
            raise

        settings = MCPSettings.get()

        # Normalize the query to ensure proper format and limits
        normalized_query = normalize_search_command(spl_query, settings.max_row_limit)
        logger.debug("Normalized query: %s", normalized_query)

        # Safety check unless excluded
        try:
            excluded = self.name in settings.safe_spl_exclude_tools
            if not excluded:
                is_safe, message = check_spl_safe(
                    settings, session_key, normalized_query
                )
                if not is_safe:
                    logger.error("SPL safety validation failed: %s", message)
                    return {
                        "status_code": int(http.HTTPStatus.BAD_REQUEST),
                        "content": message,
                    }
                logger.info("SPL validation passed: %s", message)
        except Exception as e:
            logger.exception("Error during SPL safety validation: %s", e)
            return {
                "status_code": int(http.HTTPStatus.INTERNAL_SERVER_ERROR),
                "content": f"Safety validation error: {e}",
            }

        # Execute query
        try:
            start_time = time.time()  # Start time for execution duration
            result = run_splunk_query_internal(
                settings=settings,
                session_key=session_key,
                query=normalized_query,
                earliest_time=normalized_args.get("earliest_time", "-24h"),
                latest_time=normalized_args.get("latest_time", "now"),
                row_limit=normalized_args.get("row_limit", settings.default_row_limit),
            )
            end_time = time.time()  # End time for execution duration
            execution_time = end_time - start_time

            logger.info(
                "Tool '%s' executed successfully",
                self.name,
                extra={"execution_time_seconds": round(execution_time, 2)},
            )
            return result

        except Exception as e:
            logger.exception("Tool execution failed for '%s': %s", self.name, e)
            raise

    def _validate_arguments(self, args: Dict[str, Any]) -> Optional[str]:
        """
        Validate provided arguments against tool specification.

        Args:
            args: Arguments to validate.

        Returns:
            Error message if validation fails, None if successful.
        """
        # Check required arguments
        error = self._validate_required_arguments(args)
        if error:
            return error

        # Check argument patterns
        error = self._validate_argument_patterns(args)
        if error:
            return error

        return None

    def _validate_required_arguments(self, args: Dict[str, Any]) -> Optional[str]:
        """Validate that all required arguments are present."""
        for arg_spec in self.arguments:
            if arg_spec.required and arg_spec.name not in args:
                return f"Missing required argument: {arg_spec.name}"
        return None

    def _validate_argument_patterns(self, args: Dict[str, Any]) -> Optional[str]:
        """Validate arguments against regex patterns."""
        for arg_spec in self.arguments:
            if not arg_spec.validation or not arg_spec.validation.pattern:
                continue

            if arg_spec.name not in args:
                continue

            try:
                value_str = str(args[arg_spec.name])
                if not re.fullmatch(arg_spec.validation.pattern, value_str):
                    return (
                        arg_spec.validation.message
                        or f"Value for '{arg_spec.name}' failed validation"
                    )
            except re.error as e:
                logger.warning(
                    "Invalid regex pattern for %s.%s: %s", self.name, arg_spec.name, e
                )

        return None

    def _normalize_arguments(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """
        Normalize and validate argument values according to their types.

        Args:
            args: Raw argument values.

        Returns:
            Dictionary of normalized argument values.

        Raises:
            ValueError: If argument normalization fails.
        """
        normalized = {}

        for arg_spec in self.arguments:
            if arg_spec.name not in args:
                # Use default value if available
                if arg_spec.default is not None:
                    normalized[arg_spec.name] = arg_spec.default
                continue

            value = args[arg_spec.name]

            try:
                if arg_spec.type == ArgumentType.STRING:
                    normalized[arg_spec.name] = self._normalize_string_arg(
                        arg_spec, value
                    )
                elif arg_spec.type == ArgumentType.INTEGER:
                    normalized[arg_spec.name] = self._normalize_integer_arg(
                        arg_spec, value
                    )
                elif arg_spec.type == ArgumentType.NUMBER:
                    normalized[arg_spec.name] = self._normalize_number_arg(
                        arg_spec, value
                    )
                elif arg_spec.type == ArgumentType.BOOLEAN:
                    normalized[arg_spec.name] = self._normalize_boolean_arg(
                        arg_spec, value
                    )
                else:
                    normalized[arg_spec.name] = value

            except Exception as e:
                raise ValueError(f"Failed to normalize argument '{arg_spec.name}': {e}")

        return normalized

    def _normalize_string_arg(self, spec: ToolArgument, value: Any) -> str:
        """Normalize string argument with enum validation."""
        str_value = str(value)

        if spec.enum:
            if isinstance(spec.enum, dict):
                if str_value not in spec.enum:
                    raise ValueError(
                        f"Value '{str_value}' not in allowed keys {list(spec.enum.keys())}"
                    )
                # For dictionary-style enums, return the value corresponding to the key
                return spec.enum.get(str_value, "")
            if isinstance(spec.enum, list):
                if str_value not in spec.enum:
                    raise ValueError(
                        f"Value '{str_value}' not in allowed values {spec.enum}"
                    )

        if spec.quoted:
            # Use shlex.quote to properly escape and quote the string for SPL
            return json.dumps(str_value)

        return str_value

    def _normalize_integer_arg(self, spec: ToolArgument, value: Any) -> int:
        """Normalize integer argument with range validation."""
        if isinstance(value, bool):
            raise ValueError("Boolean provided where integer expected")

        try:
            int_value = int(value)
        except (ValueError, TypeError):
            raise ValueError(f"Invalid integer value: {value}")

        if spec.min is not None and int_value < spec.min:
            raise ValueError(f"Value {int_value} below minimum {spec.min}")
        if spec.max is not None and int_value > spec.max:
            raise ValueError(f"Value {int_value} above maximum {spec.max}")

        if spec.enum and int_value not in spec.enum:
            raise ValueError(f"Value {int_value} not in allowed values {spec.enum}")

        return int_value

    def _normalize_number_arg(self, spec: ToolArgument, value: Any) -> float:
        """Normalize number argument with range validation."""
        try:
            float_value = float(value)
        except (ValueError, TypeError):
            raise ValueError(f"Invalid number value: {value}")

        if spec.min is not None and float_value < spec.min:
            raise ValueError(f"Value {float_value} below minimum {spec.min}")
        if spec.max is not None and float_value > spec.max:
            raise ValueError(f"Value {float_value} above maximum {spec.max}")

        return float_value

    def _normalize_boolean_arg(self, spec: ToolArgument, value: Any) -> bool:
        """Normalize boolean argument."""
        if isinstance(value, bool):
            return value

        str_value = str(value).lower().strip()
        if str_value in {"true", "1", "yes", "on"}:
            return True
        if str_value in {"false", "0", "no", "off"}:
            return False

        raise ValueError(f"Invalid boolean value: {value}")

    def _build_spl_query(self, args: Dict[str, Any]) -> str:
        """
        Build SPL query from template and arguments.

        Args:
            args: Normalized argument values.

        Returns:
            Complete SPL query string.
        """
        try:
            # Simple template substitution
            query = self.spl
            for key, value in args.items():
                placeholder = f"${key}$"
                if placeholder in query:
                    query = query.replace(placeholder, str(value))

            logger.debug("Built SPL query for tool '%s': %s", self.name, query)
            return query

        except Exception as e:
            logger.error("Failed to build SPL query for tool '%s': %s", self.name, e)
            raise

    def _build_api_request(self, args: Dict[str, Any]) -> Dict[str, Any]:
        if not self.api_method or not self.api_endpoint:
            raise ValueError(f"API tool '{self.name}' is missing method or endpoint.")

        endpoint = self._substitute_placeholders(self.api_endpoint, args)
        if not isinstance(endpoint, str) or not endpoint.strip():
            raise ValueError("API endpoint is required after placeholder substitution.")

        headers = self._substitute_placeholders(
            deepcopy(self.api_headers) if self.api_headers else {}, args
        )
        params = self._substitute_placeholders(
            deepcopy(self.api_params) if self.api_params else {}, args
        )
        body = self._substitute_placeholders(deepcopy(self.api_body), args)

        return {
            "method": self.api_method,
            "endpoint": endpoint,
            "headers": headers if headers else None,
            "params": params if params else None,
            "body": body,
        }

    def _execute_api(self, session_key: str, args: Dict[str, Any]) -> Dict[str, Any]:
        request_details = self._build_api_request(args)
        body = request_details["body"]
        data = None
        if isinstance(body, (dict, list)):
            data = json.dumps(body)
        elif body is not None:
            data = str(body)

        response = call_splunk_api(
            session_key=session_key,
            method=request_details["method"],
            api=request_details["endpoint"],
            headers=request_details["headers"],
            params=request_details["params"],
            data=data,
        )

        try:
            content = response.json()
        except ValueError:
            content = response.text

        return {
            "status_code": response.status_code,
            "content": content,
        }

    @staticmethod
    def _substitute_placeholders(value: Any, args: Dict[str, Any]) -> Any:
        if value is None:
            return None
        if isinstance(value, str):
            result = value
            for key, arg_value in args.items():
                placeholder = f"${key}$"
                if placeholder in result:
                    result = result.replace(placeholder, str(arg_value))
            return result
        if isinstance(value, dict):
            return {k: Tool._substitute_placeholders(v, args) for k, v in value.items()}
        if isinstance(value, list):
            return [Tool._substitute_placeholders(item, args) for item in value]
        return value

    def to_dict(self) -> Dict[str, Any]:
        """
        Convert tool to dictionary representation.

        Returns:
            Dictionary containing complete tool configuration.
        """
        return {
            "_key": self._key,
            "name": self.name,
            "title": self.title,
            "description": self.description,
            "tags": self.tags,
            "spl": self.spl,
            "row_limiter": self.row_limiter,
            "time_range": self.time_range,
            "required_app": self.required_app,
            "external_app": self.external_app,
            "built_in": self.built_in,
            "guardrails": self.guardrails,
            "execution_type": self.execution_type,
            "api_method": self.api_method,
            "api_endpoint": self.api_endpoint,
            "api_headers": self.api_headers,
            "api_params": self.api_params,
            "api_body": self.api_body,
            "arguments": [
                {
                    "name": arg.name,
                    "type": arg.type.value,
                    "description": arg.description,
                    "required": arg.required,
                    "placeholder": arg.placeholder,
                    "default": arg.default,
                    "group": arg.group,
                    "display_order": arg.display_order,
                    "min": arg.min,
                    "max": arg.max,
                    "enum": arg.enum,
                    "quoted": arg.quoted,
                    "validation": {
                        "pattern": arg.validation.pattern,
                        "message": arg.validation.message,
                    },
                }
                for arg in self.arguments
            ],
            "examples": [
                {
                    "name": ex.name,
                    "description": ex.description,
                    "arguments": ex.arguments,
                    "expected_use": ex.expected_use,
                }
                for ex in self.examples
            ],
        }

    def to_external_dict(self) -> Dict[str, Any]:
        """Return the tool definition in the new external schema format."""
        properties: Dict[str, Any] = {}
        required: List[str] = []

        for arg in self.arguments:
            properties[arg.name] = self._argument_to_schema_property(arg)
            if arg.required:
                required.append(arg.name)

        input_schema: Dict[str, Any] = {"type": "object", "properties": properties}
        if required:
            input_schema["required"] = required

        if self.execution_type == "api":
            execution: Dict[str, Any] = {
                "type": "api",
                "method": self.api_method,
                "endpoint": self.api_endpoint,
            }
            if self.api_headers:
                execution["headers"] = self.api_headers
            if self.api_params:
                execution["params"] = self.api_params
            if self.api_body is not None:
                execution["body"] = self.api_body
        else:
            execution = {
                "type": "spl",
                "template": self.spl,
                "row_limiter": self.row_limiter,
                "time_range": self.time_range,
            }
            if self.guardrails:
                execution["guardrails"] = self.guardrails

        meta: Dict[str, Any] = {
            "tags": self.tags,
            "examples": [ex.to_dict() for ex in self.examples],
            "execution": execution,
        }
        if self.external_app:
            meta["external_app_id"] = self.external_app
        if self.required_app:
            meta["required_app"] = self.required_app
        if self.built_in:
            meta["built_in"] = True
        if self.saved_search:
            meta["saved_search"] = self.saved_search

        payload = {
            "tool_id": self._key,
            "name": self.name,
            "title": self.title or self.name,
            "description": self.description,
            "inputSchema": input_schema,
            "_meta": meta,
        }
        return payload

    @staticmethod
    def _argument_to_schema_property(arg: ToolArgument) -> Dict[str, Any]:
        """Convert a ToolArgument into a JSON schema property."""
        prop: Dict[str, Any] = {"type": arg.type.value}
        if arg.description:
            prop["description"] = arg.description
        if arg.default is not None:
            prop["default"] = arg.default
        if arg.enum is not None:
            prop["enum"] = arg.enum
        if arg.min is not None:
            prop["minimum"] = arg.min
        if arg.max is not None:
            prop["maximum"] = arg.max
        if arg.validation and arg.validation.pattern:
            prop["pattern"] = arg.validation.pattern

        if arg.validation and arg.validation.message:
            prop["validation_message"] = arg.validation.message

        return prop


class ToolManager:
    """
    Manager for MCP tools with discovery and execution capabilities.

    This class provides centralized management of all available tools,
    including loading from configuration files, validation, and execution.

    Attributes:
        tools: Dictionary mapping tool IDs (_key) to Tool instances.
    """

    def __init__(self) -> None:
        """Initialize the tool manager."""
        self.tools: Dict[str, Tool] = {}
        self._builtin_tools: Dict[str, Tool] = {}
        self.enabled_tool_ids: Dict[str, str] = {}
        logger.info("ToolManager initialized")

    def load_tools_from_file(
        self, file_path: str, mark_as_builtin: bool = False
    ) -> None:
        """
        Load tools from a JSON configuration file.

        Args:
            file_path: Path to the JSON file containing tool definitions.
            mark_as_builtin: If True, loaded tools are marked as built-in tools.

        Raises:
            Exception: If file loading or parsing fails.
        """
        logger.info("Loading tools from file: %s", file_path)

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            tools_data = data.get("tools", [])
            loaded_count = 0

            for tool_data in tools_data:
                try:
                    tool = Tool.from_dict(tool_data)
                    self._register_tool(tool, mark_as_builtin=mark_as_builtin)
                    loaded_count += 1
                    logger.debug("Loaded tool: %s", tool.name)
                except Exception as e:
                    logger.error(
                        "Failed to load tool from %s: %s",
                        tool_data.get("name", "unknown"),
                        e,
                    )

            logger.info("Successfully loaded %d tools from %s", loaded_count, file_path)

        except Exception as e:
            logger.error("Failed to load tools from file %s: %s", file_path, e)
            raise

    def _register_tool(self, tool: Tool, mark_as_builtin: bool = False) -> None:
        """
        Register a tool in the manager, optionally tracking it as built-in.
        """
        if not tool._key:
            raise ValueError(f"Tool '{tool.name}' is missing required '_key'.")
        self.tools[tool._key] = tool
        if mark_as_builtin:
            self._builtin_tools[tool._key] = tool

    def _reset_to_builtin_tools(self) -> None:
        """
        Reset the in-memory tool list to only the built-in tools.
        """
        self.tools.clear()
        self.tools.update(self._builtin_tools)

    def _load_tools_from_kvstore(self, session_key: str) -> None:
        """
        Load custom tools from KV Store and add them to the manager.
        """
        kv_manager = KVStoreManager(
            session_key=session_key,
            collection="mcp_tools",
            owner="nobody",
        )

        try:
            response = kv_manager.query(output_mode="json", count="0")
        except Exception as e:
            logger.error("KV Store query for custom tools failed: %s", e)
            return

        if response.status_code != 200:
            logger.error(
                "KV Store returned status %s when loading tools: %s",
                response.status_code,
                getattr(response, "text", ""),
            )
            return

        try:
            records = response.json()
        except json.JSONDecodeError as e:
            logger.error("Failed to decode KV Store response: %s", e)
            return

        if not isinstance(records, list):
            logger.error("Unexpected KV Store response format: %s", records)
            return

        loaded_count = 0
        for tool_data in records:
            if not isinstance(tool_data, dict):
                continue
            try:
                tool = Tool.from_dict(tool_data)
                tool.built_in = bool(tool_data.get("built_in", False))
                self._register_tool(tool, mark_as_builtin=False)
                loaded_count += 1
            except Exception as e:
                logger.error(
                    "Failed to load custom tool '%s' from KV Store: %s",
                    tool_data.get("name", "unknown"),
                    e,
                )

        logger.info("Loaded %d custom tools from KV Store", loaded_count)

    def _load_enabled_tool_map(self, session_key: str) -> None:
        """
        Load enabled tool mappings from KV Store.
        """
        logger.info("Loading enabled tool map from KV Store...")
        kv_manager = KVStoreManager(
            session_key=session_key,
            collection="mcp_tools_enabled",
            owner="nobody",
        )

        try:
            response = kv_manager.query(output_mode="json", count="0")
        except Exception as e:
            logger.error("KV Store query for enabled tools failed: %s", e)
            return

        if response.status_code != 200:
            logger.error(
                "KV Store returned status %s when loading enabled tools: %s",
                response.status_code,
                getattr(response, "text", ""),
            )
            return

        try:
            records = response.json()
        except json.JSONDecodeError as e:
            logger.error("Failed to decode enabled tools response: %s", e)
            return

        if not isinstance(records, list):
            logger.error("Unexpected enabled tools response format: %s", records)
            return

        has_marker = False
        enabled_records: List[Dict[str, str]] = []
        for record in records:
            if not isinstance(record, dict):
                continue
            if record.get("_key") == _ENABLED_SEED_MARKER_KEY:
                has_marker = True
                continue
            name = record.get("_key")
            tool_id = record.get("tool_id")
            if isinstance(name, str) and isinstance(tool_id, str):
                enabled_records.append({"tool_name": name, "tool_id": tool_id})

        if not enabled_records and not has_marker:
            seeded = self.seed_builtin_tools_enabled(session_key, kv_manager=kv_manager)
            if seeded:
                logger.info(
                    "Seeded %d built-in tools as enabled defaults",
                    len(seeded),
                )
            return

        logger.info("Found %d enabled tool records in KV Store", len(enabled_records))
        self.enabled_tool_ids.clear()
        for record in enabled_records:
            if not isinstance(record, dict):
                continue
            name = record.get("tool_name")
            tool_id = record.get("tool_id")
            if isinstance(name, str) and isinstance(tool_id, str):
                self.enabled_tool_ids[name] = tool_id
                logger.debug("Loaded enabled tool: %s -> %s", name, tool_id)

        logger.info("Finished loading enabled tool map: %s", self.enabled_tool_ids)

    def seed_builtin_tools_enabled(
        self,
        session_key: str,
        kv_manager: Optional[KVStoreManager] = None,
    ) -> List[Dict[str, str]]:
        """
        Seed the enabled tools KV store with all built-in tools if empty.

        Returns:
            List of enabled tool records that were written.
        """
        if not self._builtin_tools:
            logger.info("No built-in tools available to seed enablement")
            return []

        kv_manager = kv_manager or KVStoreManager(
            session_key=session_key,
            collection="mcp_tools_enabled",
            owner="nobody",
        )

        enabled_records: List[Dict[str, str]] = []
        seen_names: Dict[str, str] = {}

        for tool in self._builtin_tools.values():
            tool_name = tool.name
            tool_id = tool._key
            if not tool_name or not tool_id:
                continue
            if tool_name in seen_names:
                logger.warning(
                    "Skipping duplicate built-in tool name '%s' while seeding enablement",
                    tool_name,
                )
                continue
            seen_names[tool_name] = tool_id
            enabled_records.append({"_key": tool_name, "tool_id": tool_id})

        if not enabled_records:
            return []

        for record in enabled_records:
            tool_name = record["_key"]
            tool_id = record["tool_id"]
            update_response = kv_manager.replace(tool_name, {"tool_id": tool_id})
            if update_response.status_code == 404:
                insert_response = kv_manager.insert(
                    {"_key": tool_name, "tool_id": tool_id}
                )
                if insert_response.status_code not in (200, 201):
                    logger.error(
                        "KV insert failed while seeding enabled tools (%s): %s",
                        insert_response.status_code,
                        getattr(insert_response, "text", ""),
                    )
                    continue
            elif update_response.status_code not in (200, 201):
                logger.error(
                    "KV replace failed while seeding enabled tools (%s): %s",
                    update_response.status_code,
                    getattr(update_response, "text", ""),
                )
                continue
            self.enabled_tool_ids[tool_name] = tool_id

        marker_response = kv_manager.replace(_ENABLED_SEED_MARKER_KEY, {"seeded": True})
        if marker_response.status_code == 404:
            marker_insert = kv_manager.insert(
                {"_key": _ENABLED_SEED_MARKER_KEY, "seeded": True}
            )
            if marker_insert.status_code not in (200, 201):
                logger.warning(
                    "Failed to create enablement seed marker (%s): %s",
                    marker_insert.status_code,
                    getattr(marker_insert, "text", ""),
                )
        elif marker_response.status_code not in (200, 201):
            logger.warning(
                "Failed to update enablement seed marker (%s): %s",
                marker_response.status_code,
                getattr(marker_response, "text", ""),
            )

        logger.info("Seeded enabled tool map: %s", self.enabled_tool_ids)
        return [
            {"tool_name": name, "tool_id": tool_id}
            for name, tool_id in self.enabled_tool_ids.items()
        ]

    def get_tool(self, name: str) -> Optional[Tool]:
        """
        Get a tool by name, only if it's enabled.

        Args:
            name: Tool name to retrieve.

        Returns:
            Tool instance if found and enabled, None otherwise.
        """
        # First check if the tool is enabled
        enabled_tool_id = self.enabled_tool_ids.get(name)
        if not enabled_tool_id:
            logger.debug("Tool not enabled: %s", name)
            return None

        # Get the tool by its key (much more efficient than searching by name)
        tool = self.tools.get(enabled_tool_id)
        if tool:
            logger.debug("Retrieved enabled tool: %s (key=%s)", name, enabled_tool_id)
        else:
            logger.warning(
                "Enabled tool not found in tools dict: %s (key=%s)",
                name,
                enabled_tool_id,
            )
        return tool

    def refresh_custom_tools(self, session_key: Optional[str]) -> None:
        """
        Reload custom tools from KV Store using the provided session key.
        """
        if not session_key:
            logger.warning(
                "Session key not provided; skipping custom tool refresh from KV Store"
            )
            return

        logger.info(
            "Starting refresh_custom_tools - before reset, have %d tools",
            len(self.tools),
        )
        self._reset_to_builtin_tools()
        logger.info("After reset to builtin, have %d tools", len(self.tools))
        self._load_tools_from_kvstore(session_key)
        logger.info("After loading from kvstore, have %d tools", len(self.tools))
        self._load_enabled_tool_map(session_key)
        logger.info(
            "After loading enabled map, have %d enabled tools",
            len(self.enabled_tool_ids),
        )

    def refresh_enabled_tools(self, session_key: Optional[str]) -> None:
        """
        Reload only the enabled tools mapping from KV Store.
        """
        if not session_key:
            logger.warning(
                "Session key not provided; skipping enabled tools refresh from KV Store"
            )
            return
        self._load_enabled_tool_map(session_key)

    def refresh_tools_for_listing(self, session_key: Optional[str]) -> None:
        """
        Refresh both custom tools and the enabled tools mapping using a single session key.

        This ensures KV-backed tool definitions and enablement state are loaded together,
        avoiding stale enabled maps when only custom tools are refreshed.
        """
        self.refresh_custom_tools(session_key)
        self.refresh_enabled_tools(session_key)

    def disable_tool(
        self, tool_name: str, tool_id: str, session_key: str
    ) -> Tuple[int, Optional[str]]:
        """
        Disable a tool by removing its enablement mapping.

        Returns:
            Tuple of (status_code, error_detail_if_any)
        """
        enabled_kv_manager = KVStoreManager(
            session_key=session_key,
            collection="mcp_tools_enabled",
            owner="nobody",
        )

        delete_response = enabled_kv_manager.delete(tool_name)
        if delete_response.status_code not in (200, 204, 404):
            logger.error(
                "KV delete failed (%s): %s",
                delete_response.status_code,
                getattr(delete_response, "text", ""),
            )
            return delete_response.status_code, getattr(delete_response, "text", "")

        # Update in-memory enablement map without full refresh
        self.enabled_tool_ids.pop(tool_name, None)

        logger.info("Disabled tool '%s'", tool_name)
        return 200, None

    def enable_tool(
        self,
        tool_name: str,
        tool_id: str,
        session_key: str,
        override: bool = False,
    ) -> Tuple[int, Optional[Dict[str, Any]]]:
        """
        Enable a tool by writing to the enablement KV store.

        Returns:
            Tuple of (status_code, error_payload_if_any)
        """
        enabled_kv_manager = KVStoreManager(
            session_key=session_key,
            collection="mcp_tools_enabled",
            owner="nobody",
        )

        if not override:
            self.refresh_custom_tools(session_key)
            existing_tool_id = self.enabled_tool_ids.get(tool_name)
            if existing_tool_id:
                if existing_tool_id == tool_id:
                    logger.info(
                        "Tool '%s' already enabled with id '%s'",
                        tool_name,
                        tool_id,
                    )
                    return 200, None
                existing_tool = self.tools.get(existing_tool_id)
                if existing_tool:
                    return (
                        409,
                        {
                            "error": "Another tool with the same name is already enabled.",
                            "conflict_tool": {
                                "tool_id": existing_tool._key,
                                "external_app": existing_tool.external_app,
                                "name": existing_tool.name,
                            },
                        },
                    )

        update_response = enabled_kv_manager.replace(tool_name, {"tool_id": tool_id})
        if update_response.status_code == 404:
            insert_response = enabled_kv_manager.insert(
                {"_key": tool_name, "tool_id": tool_id}
            )
            if insert_response.status_code not in (200, 201):
                logger.error(
                    "KV insert failed (%s): %s",
                    insert_response.status_code,
                    getattr(insert_response, "text", ""),
                )
                return (
                    insert_response.status_code,
                    {
                        "error": True,
                        "code": "kvstore_error",
                        "message": "Failed to enable tool.",
                    },
                )
        elif update_response.status_code not in (200, 201):
            logger.error(
                "KV replace failed (%s): %s",
                update_response.status_code,
                getattr(update_response, "text", ""),
            )
            return (
                update_response.status_code,
                {
                    "error": True,
                    "code": "kvstore_error",
                    "message": "Failed to enable tool.",
                },
            )

        # Update in-memory enablement map without full refresh
        self.enabled_tool_ids[tool_name] = tool_id

        logger.info("Enabled tool '%s' with id '%s'", tool_name, tool_id)
        return 200, None

    def list_tools(self, enabled_only: bool = True, installed_apps=None) -> List[Tool]:
        """
        List all available tools.

        Args:
            enabled_only: Deprecated parameter retained for compatibility.
            installed_apps: List of installed app names.

        Returns:
            List of Tool instances.
        """
        if installed_apps is None:
            installed_apps = set()
        # Filter tools based on required_app

        logger.info(
            "list_tools called - have %d total tools and %d enabled mappings",
            len(self.tools),
            len(self.enabled_tool_ids),
        )
        logger.debug("Enabled tool mappings: %s", self.enabled_tool_ids)

        enabled_map = self.enabled_tool_ids
        enabled_ids = set(enabled_map.values())
        tools: List[Tool] = []
        for tool in self.tools.values():
            logger.debug("Checking tool %s (key=%s)", tool.name, tool._key)
            if tool.required_app and tool.required_app not in installed_apps:
                logger.debug(
                    "Tool %s skipped - required_app %s not in installed apps",
                    tool.name,
                    tool.required_app,
                )
                continue
            enabled_tool_id = enabled_map.get(tool.name)
            logger.debug(
                "Tool %s: enabled_tool_id=%s, tool._key=%s",
                tool.name,
                enabled_tool_id,
                tool._key,
            )
            if tool._key not in enabled_ids:
                logger.debug("Tool %s skipped - not enabled", tool.name)
                continue
            tools.append(tool)
            logger.debug("Tool %s included in results", tool.name)

        logger.debug("Listed %d tools (enabled_only=%s)", len(tools), enabled_only)
        return tools

    def reload_tools(self, session_key: Optional[str] = None) -> None:
        """
        Reload all tools from configuration files.

        This method clears existing tools and reloads from the default
        built-in tools configuration file.
        """
        logger.info("Reloading all tools")

        self.tools.clear()
        self._builtin_tools.clear()
        self.enabled_tool_ids.clear()

        # Compute base path once
        import os

        base_path = os.path.abspath(os.path.dirname(__file__))

        # Load built-in tools from default/builtin_tools.json
        default_dir = os.path.join(base_path, "../default")
        builtin_file = os.path.join(default_dir, "builtin_tools.json")

        if os.path.exists(builtin_file):
            self.load_tools_from_file(builtin_file, mark_as_builtin=True)
        else:
            logger.warning("Built-in tools file not found at %s", builtin_file)

        # Load local tools and override defaults if present
        local_dir = os.path.join(base_path, "../local")
        local_file = os.path.join(local_dir, "builtin_tools.json")
        if os.path.exists(local_file):
            logger.info("Merging local tools from: %s", local_file)
            self.load_tools_from_file(local_file)

        if session_key:
            self._load_tools_from_kvstore(session_key)

    def validate_tool_config(self, tool_data: Dict[str, Any]) -> List[str]:
        """
        Validate tool configuration without loading it.

        Args:
            tool_data: Tool configuration dictionary.

        Returns:
            List of validation error messages (empty if valid).
        """
        errors = []

        try:
            # Basic validation by attempting to create Tool
            Tool.from_dict(tool_data)

            # Additional custom validations
            errors.extend(self._validate_quoted_enum_constraint(tool_data))

        except Exception as e:
            errors.append(str(e))

        return errors

    def _validate_quoted_enum_constraint(self, tool_data: Dict[str, Any]) -> List[str]:
        """
        Validate that quoted arguments do not have enum constraints.

        Quoted arguments are meant for free-form text input and should not be
        restricted to predefined enum values, as this creates a logical conflict.

        Args:
            tool_data: Tool configuration dictionary.

        Returns:
            List of validation error messages.
        """
        errors = []
        tool_name = tool_data.get("name", "unknown")
        arguments = tool_data.get("arguments", [])

        for arg in arguments:
            arg_name = arg.get("name", "unknown")
            is_quoted = arg.get("quoted", True)  # Default is True
            has_enum = arg.get("enum") is not None

            if is_quoted and has_enum:
                errors.append(
                    f"Tool '{tool_name}', argument '{arg_name}': "
                    f"Quoted arguments cannot have enum constraints. "
                    f"Remove either 'quoted: true' or the 'enum' property."
                )

        return errors


def get_default_manager(reload: bool = False) -> ToolManager:
    """
    Get the default singleton ToolManager instance.

    Args:
        reload: If True, reload tools from configuration files.

    Returns:
        ToolManager instance.
    """
    global _default_manager

    with _manager_lock:
        if _default_manager is None:
            logger.info("Creating default ToolManager instance")
            _default_manager = ToolManager()
            reload = True  # Force reload on first creation

        if reload:
            _default_manager.reload_tools()

    return _default_manager


# Public API exports
__all__ = [
    "ArgumentType",
    "ToolArgument",
    "ToolExample",
    "Tool",
    "ToolManager",
    "get_default_manager",
]
