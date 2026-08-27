"""
Configuration Settings Module.

This module provides centralized configuration management for the MCP Splunk Server
application. It handles loading settings from configuration files, environment
variables, and provides a singleton pattern for consistent access across the application.
"""

from __future__ import annotations

import configparser
import json
import os
import socket
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, ClassVar, Dict, Optional, Set, Tuple, Union

from logging_config import get_logger

# Module logger
logger = get_logger(__name__)


def _as_bool(val: str, default: bool = False) -> bool:
    """
    Convert a configuration string to boolean value.

    This function provides robust parsing of common boolean representations
    found in configuration files, with fallback to a default value.

    Args:
        val: String value to parse as boolean.
        default: Default value to return if parsing fails.

    Returns:
        Boolean representation of the input value.
    """
    if val is None:
        return default

    normalized_val = str(val).strip().lower()

    # Define truthy and falsy values
    truthy_values = {"1", "true", "yes", "on", "enabled"}
    falsy_values = {"0", "false", "no", "off", "disabled"}

    if normalized_val in truthy_values:
        return True
    if normalized_val in falsy_values:
        return False

    logger.warning("Unrecognized boolean value '%s', using default %s", val, default)
    return default


def find_file_in_app_dirs(
    filename: str, processor: Optional[Callable[[Path], Any]] = None
) -> Any:
    """
    Search for a file in application directories with local override support.

    This function implements Splunk's standard configuration file precedence,
    searching in local/ directory first (for user customizations) then falling
    back to default/ directory (for application defaults).

    Args:
        filename: Name of the file to search for.
        processor: Optional function to process the file when found.
                  Should return the processed result or None if processing fails.

    Returns:
        The result of the processor function if provided and successful,
        the Path object if no processor is provided,
        or None if file not found or processing fails.
    """
    app_path = Path(__file__).parent.parent
    search_directories = ["local", "default"]
    found_files = []

    logger.debug("Searching for file '%s' in app directories", filename)

    for subdir in search_directories:
        file_path = app_path / subdir / filename
        logger.debug("Checking path: %s", file_path)

        if file_path.exists():
            found_files.append(file_path)
            logger.debug("Found file at: %s", file_path)

            if processor:
                try:
                    result = processor(file_path)
                    if result is not None:
                        logger.debug("Successfully processed file: %s", file_path)
                        return result
                except Exception as e:
                    logger.warning("Failed to process file %s: %s", file_path, e)
                    continue
            else:
                return file_path

    # Provide more specific error messages
    if found_files:
        if processor:
            logger.debug(
                "File '%s' found at %s but requested configuration not available",
                filename,
                [str(f) for f in found_files],
            )
        else:
            # This shouldn't happen since we return early when no processor is provided
            logger.warning("File '%s' found but not returned", filename)
    else:
        logger.warning("File '%s' not found in any app directory", filename)

    return None


def get_splunk_mgmt_port() -> str:
    """
    Retrieve Splunk management port from server configuration.

    This function reads the Splunk server.conf file to determine the
    management port, which is used for API communications.

    Returns:
        String representation of the management port (default: "8089").
    """
    splunk_home = os.environ.get("SPLUNK_HOME", "/opt/splunk")
    conf_path = os.path.join(splunk_home, "etc", "system", "local", "server.conf")

    logger.debug("Reading Splunk management port from: %s", conf_path)

    config = configparser.ConfigParser()
    try:
        config.read(conf_path)
        port = config.get("httpServer", "mgmtPort", fallback="8089")
        logger.info("Retrieved Splunk management port: %s", port)
        return port
    except Exception as e:
        logger.warning("Failed to read management port, using default: %s", e)
        return "8089"


def get_config_value(config_filename: str, stanza: str, key: str, default: str) -> str:
    """
    Retrieve a configuration value with local/default precedence.

    This function searches for a configuration value in the specified file,
    checking local/ directory first, then default/ directory, following
    Splunk's standard configuration file precedence rules.

    Args:
        config_filename: Name of the configuration file.
        stanza: Configuration stanza name.
        key: Configuration key name.
        default: Default value if not found.

    Returns:
        Configuration value as string, or default if not found.
    """
    logger.debug("Getting config value: [%s] %s from %s", stanza, key, config_filename)

    def process_config_file(file_path: Path) -> Optional[str]:
        """Process a configuration file and extract the requested value."""
        config = configparser.ConfigParser()
        try:
            config.read(file_path)
            if config.has_section(stanza) and config.has_option(stanza, key):
                value = config.get(stanza, key)
                logger.debug("Found config value in %s: %s", file_path, value)
                return value
        except Exception as e:
            logger.warning("Error reading config file %s: %s", file_path, e)
        return None

    result = find_file_in_app_dirs(config_filename, process_config_file)
    final_value = result if result is not None else str(default)

    logger.debug("Final config value for [%s] %s: %s", stanza, key, final_value)
    return final_value


# Singleton instance storage
_singleton_instance: Optional["MCPSettings"] = None


def _determine_ssl_setting(raw: Optional[str]) -> Union[bool, str, None]:
    if raw is None:
        return None  # signal to fall back
    value = str(raw).strip()
    if value.lower() in {"", "none"}:
        return None
    # Boolean parsing attempt
    normalized = value.lower()
    truthy_values = {"1", "true", "yes", "on", "enabled"}
    falsy_values = {"0", "false", "no", "off", "disabled"}
    if normalized in truthy_values:
        return True
    if normalized in falsy_values:
        return False
    # Treat as potential path
    expanded = os.path.expanduser(os.path.expandvars(value))
    if os.path.isfile(expanded):
        logger.info("Using custom CA bundle for ssl_verify: %s", expanded)
        return expanded
    else:
        # If argument looks like a path but not found, warn and still return path (requests will raise if invalid)
        if any(sep in value for sep in ("/", "\\")) or value.lower().endswith(
            (".pem", ".crt", ".cer")
        ):
            logger.warning(
                "ssl_verify path specified but file not found: %s (expanded: %s)",
                value,
                expanded,
            )
            return expanded
        # Unrecognized token -> warn and default True
        logger.warning(
            "Unrecognized ssl_verify value '%s'; defaulting to True (set true/false or path)",
            value,
        )
        return True


def _resolve_default_base_url(mgmt_port: str) -> str:
    """Determine default base_url.

    Strategy:
    1. Use socket.getfqdn() for candidate host.
    2. Attempt a quick TCP connection to host:mgmt_port (timeout ~2s).
    3. If connection fails, fall back to localhost.

    Returns:
        A base URL string using reachable host.
    """
    fqdn = socket.getfqdn().strip() or "localhost"
    port_int = int(mgmt_port) if mgmt_port.isdigit() else 8089

    candidate = f"https://{fqdn}:{mgmt_port}/"

    # If FQDN already looks like localhost variants, skip test
    if fqdn in {"localhost", "127.0.0.1", "::1"}:
        logger.debug("FQDN is localhost variant; using candidate directly")
        return candidate

    try:
        logger.debug("Testing reachability of %s:%s", fqdn, mgmt_port)
        socket.getaddrinfo(fqdn, port_int, proto=socket.IPPROTO_TCP)
        with socket.create_connection((fqdn, port_int), timeout=2):
            logger.info(
                "Successfully connected to %s:%s; using FQDN in base_url",
                fqdn,
                mgmt_port,
            )
        return candidate
    except Exception as e:
        logger.warning(
            "Could not reach %s:%s (%s). Falling back to localhost for base_url.",
            fqdn,
            mgmt_port,
            e,
        )
        return f"https://localhost:{mgmt_port}/"


def _is_clustered_splunk() -> bool:
    """Best-effort detection of whether Splunk is running in a clustered deployment.

    We avoid network calls here and rely on local config files under $SPLUNK_HOME.
    If detection fails, we default to non-clustered behavior.
    """

    splunk_home = os.environ.get("SPLUNK_HOME", "/opt/splunk")

    # Common config locations for clustering.
    candidates = [
        os.path.join(splunk_home, "etc", "system", "local", "server.conf"),
        os.path.join(splunk_home, "etc", "system", "default", "server.conf"),
        os.path.join(splunk_home, "etc", "system", "local", "indexes.conf"),
        os.path.join(splunk_home, "etc", "system", "default", "indexes.conf"),
    ]

    config = configparser.ConfigParser()
    for path in candidates:
        try:
            if not os.path.isfile(path):
                continue
            config.read(path)

            # Indexer clustering / manager setup usually appears under [clustering].
            if config.has_section("clustering"):
                # If clustering stanza exists, it's very likely a clustered environment.
                return True

            # Search head clustering appears under [shclustering] / [shclustering:...].
            if config.has_section("shclustering"):
                return True
            for section in config.sections():
                if str(section).lower().startswith("shclustering"):
                    return True
        except Exception:
            # Keep best-effort; ignore parse errors.
            continue

    return False


@dataclass
class MCPSettings:
    """
    Configuration settings for the MCP Splunk Server application.

    This class provides a centralized configuration management system with
    singleton pattern for consistent access across the application. It loads
    settings from configuration files and provides defaults for all values.

    Attributes:
        base_url: Base URL for Splunk API calls.
        timeout: Request timeout in seconds.
        ssl_verify: SSL verification setting passed to requests. Can be:
            - True (default) to verify using system CA bundle
            - False to disable verification (NOT recommended for production)
            - Path to a CA bundle / self-signed certificate file (PEM / CRT)
        safe_spl_commands: Set of allowed SPL commands.
        generating_commands: Set of SPL commands that generate data.
        app_name: Application name.
        app_version: Application version.
        max_row_limit: Maximum number of rows to return in queries.
        default_row_limit: Default number of rows to return in queries.
    """

    base_url: Optional[str] = None
    timeout: float = 60.0
    ssl_verify: Union[bool, str] = True
    safe_spl_commands: Set[str] = field(default_factory=set)
    generating_commands: Set[str] = field(default_factory=set)
    safe_spl_exclude_tools: Set[str] = field(default_factory=set)
    app_name: str = "Splunk_MCP_Server"
    app_version: str = "0.1.0"
    max_row_limit: int = 1000
    default_row_limit: int = 100
    sub_search_arg_cmd: dict = field(default_factory=dict)
    require_encrypted_token: bool = False
    token_key_reload_interval_seconds: float = 0.0
    _request_context: ClassVar[Optional[Dict[str, Any]]] = None

    @classmethod
    def set_request_context(cls, request_context: Optional[Dict[str, Any]]) -> None:
        """
        Store the latest client request for deriving connection info.

        If a settings instance already exists, update base_url/ssl_verify
        based on the new request data.
        """
        cls._request_context = request_context
        if _singleton_instance and not _singleton_instance.base_url:
            base_url, hostname = cls._build_base_url_from_request()
            if base_url:
                _singleton_instance.base_url = base_url
                if hostname and hostname.lower() in ["localhost", "127.0.0.1"]:
                    _singleton_instance.ssl_verify = False
                    logger.info(
                        "ssl_verify disabled for localhost or 127.0.0.1 request hostname"
                    )
                logger.info("Updated base_url from latest client request: %s", base_url)

    @staticmethod
    def _normalize_headers(headers: Any) -> Dict[str, str]:
        """Normalize headers to a lowercase-keyed dict."""
        normalized: Dict[str, str] = {}
        if isinstance(headers, dict):
            normalized = {str(k).lower(): str(v) for k, v in headers.items()}
        elif isinstance(headers, list):
            for item in headers:
                if isinstance(item, list) and len(item) >= 2:
                    normalized[str(item[0]).lower()] = str(item[1])
        return normalized

    @classmethod
    def _build_base_url_from_request(cls) -> Tuple[Optional[str], Optional[str]]:
        """
        Build a base URL from the stored request context.

        Returns:
            Tuple of (base_url, hostname) where base_url may be None if
            insufficient data is present.
        """
        request_ctx = cls._request_context
        if not request_ctx:
            return None, None

        headers = cls._normalize_headers(request_ctx.get("headers"))
        host_header = headers.get("host")
        port: Optional[str] = None

        if host_header:
            host_header = str(host_header)
            if ":" in host_header:
                host_part, port_part = host_header.rsplit(":", 1)
                hostname = host_part.strip("[] ").strip()
                port = port_part.strip()
            else:
                hostname = host_header.strip()
        else:
            # Default to 127.0.0.1 and port 8089 if host header is missing
            hostname = "127.0.0.1"
            port = "8089"
            base_url = f"https://{hostname}:{port}/"
            return base_url, hostname

        connection_info = request_ctx.get("connection") or {}
        server_info = request_ctx.get("server") or {}

        hostname = (
            hostname
            or server_info.get("server_name")
            or connection_info.get("server_name")
        )
        hostname = hostname or server_info.get("name") or connection_info.get("name")

        port_value = (
            port
            or server_info.get("server_port")
            or connection_info.get("server_port")
            or server_info.get("port")
            or connection_info.get("port")
        )
        if port_value is not None and port_value != "":
            port = str(port_value)

        scheme_candidates = [
            server_info.get("protocol") if isinstance(server_info, dict) else None,
            (
                connection_info.get("protocol")
                if isinstance(connection_info, dict)
                else None
            ),
            headers.get("x-forwarded-proto"),
            headers.get("x-forwarded-protocol"),
            request_ctx.get("scheme"),
        ]
        scheme = next((str(s).lower() for s in scheme_candidates if s), "https")
        if scheme not in {"http", "https"}:
            scheme = "https"

        if not hostname:
            logger.warning("Client request missing hostname; cannot build base_url")
            return None, None

        if not port:
            port = "443" if scheme == "https" else "80"

        if hostname and port:
            try:
                logger.debug("Testing reachability of %s:%s", hostname, port)
                socket.getaddrinfo(hostname, port, proto=socket.IPPROTO_TCP)
                with socket.create_connection((hostname, int(port)), timeout=2):
                    logger.info(
                        "Successfully connected to %s:%s",
                        hostname,
                        port,
                    )
            except Exception as exc:
                logger.warning(
                    "Falling back to localhost:8089; could not connect to %s:%s (%s)",
                    hostname,
                    port,
                    exc,
                )
                hostname = "127.0.0.1"
                port = "8089"
                base_url = f"https://{hostname}:{port}/"
                return base_url, hostname

        base_url = f"{scheme}://{hostname}:{port}/"
        return base_url, hostname

    @classmethod
    def get(cls) -> "MCPSettings":
        """
        Get the singleton instance of MCPSettings.

        This method implements the singleton pattern, creating and configuring
        the settings instance on first access and returning the cached instance
        on subsequent calls.

        Returns:
            MCPSettings instance with loaded configuration.
        """
        global _singleton_instance

        if _singleton_instance is None:
            logger.info("Creating new MCPSettings instance")
            _singleton_instance = cls._create_instance()
            logger.info("MCPSettings instance created successfully")

        return _singleton_instance

    @classmethod
    def _create_instance(cls) -> "MCPSettings":
        """
        Create and configure a new MCPSettings instance.

        This method loads all configuration values from files and environment
        variables, applying appropriate defaults and validation.

        Returns:
            Fully configured MCPSettings instance.
        """
        logger.debug("Loading configuration from files")

        # Detect clustered environment once and log it.
        is_clustered = _is_clustered_splunk()
        logger.info(
            "Environment clustering detection: %s",
            "clustered" if is_clustered else "non-clustered",
        )

        # Load base URL configuration
        configured_base_url = get_config_value("mcp.conf", "server", "base_url", "")
        base_url: Optional[str] = configured_base_url.strip() or None
        request_hostname: Optional[str] = None

        if base_url:
            logger.info("Using base URL from mcp.conf: %s", base_url)
        else:
            base_url_from_request, request_hostname = cls._build_base_url_from_request()
            if base_url_from_request:
                base_url = base_url_from_request
                logger.info("Using base URL from client request: %s", base_url)
            else:
                mgmt_port = get_splunk_mgmt_port()
                base_url = _resolve_default_base_url(mgmt_port)
                logger.info("Using auto-detected base URL: %s", base_url)

        # SSL verification handling
        # Order of precedence:
        # 1. mcp.conf [server] ssl_verify if provided (bool-like or path)
        # 2. PYTHONHTTPSVERIFY environment variable (bool only)
        # 3. True (default)
        raw_ssl_value = get_config_value("mcp.conf", "server", "ssl_verify", "")
        ssl_verify: Union[bool, str] = True

        conf_value = _determine_ssl_setting(raw_ssl_value)
        if conf_value is not None:
            ssl_verify = conf_value
            logger.info("ssl_verify set from mcp.conf: %s", ssl_verify)
        else:
            pyhttps = os.environ.get("PYTHONHTTPSVERIFY")
            if pyhttps is not None:
                ssl_verify = pyhttps.lower() not in ("0", "false", "")
                logger.info("ssl_verify set from PYTHONHTTPSVERIFY: %s", ssl_verify)
            else:
                logger.info("default ssl_verify: %s", ssl_verify)
        if request_hostname and request_hostname.lower() == "localhost":
            ssl_verify = False
            logger.info("ssl_verify disabled because request hostname is localhost")

        # Load timeout setting
        try:
            timeout = float(get_config_value("mcp.conf", "server", "timeout", "60.0"))
        except ValueError as e:
            logger.warning("Invalid timeout value, using default: %s", e)
            timeout = 60.0

        # Load row limit settings
        try:
            max_row_limit = int(
                get_config_value("mcp.conf", "server", "max_row_limit", "1000")
            )
        except ValueError as e:
            logger.warning("Invalid max_row_limit, using default: %s", e)
            max_row_limit = 1000

        try:
            default_row_limit = int(
                get_config_value("mcp.conf", "server", "default_row_limit", "100")
            )
        except ValueError as e:
            logger.warning("Invalid default_row_limit, using default: %s", e)
            default_row_limit = 100

        # Load require_encrypted_token
        require_encrypted_token = _as_bool(
            get_config_value("mcp.conf", "server", "require_encrypted_token", "false"),
            default=False,
        )

        # Load TokenCrypto key reload interval
        # If not explicitly configured, default to 0 for standalone and 300 for clustered.
        raw_token_key_reload = get_config_value(
            "mcp.conf",
            "server",
            "token_key_reload_interval_seconds",
            "__MISSING__",
        )
        token_key_reload_is_explicit = raw_token_key_reload != "__MISSING__"

        if not token_key_reload_is_explicit:
            # Apply environment-specific default.
            token_key_reload_interval_seconds = 300.0 if is_clustered else 0.0
        else:
            try:
                token_key_reload_interval_seconds = float(raw_token_key_reload)
            except ValueError as e:
                logger.warning(
                    "Invalid token_key_reload_interval_seconds, using default: %s", e
                )
                token_key_reload_interval_seconds = 300.0 if is_clustered else 0.0

        if token_key_reload_interval_seconds < 0:
            logger.warning(
                "token_key_reload_interval_seconds cannot be negative; using 0"
            )
            token_key_reload_interval_seconds = 0.0

        # Load safe SPL configuration (commands + exclusions + subsearch args)
        safe_spl_commands, safe_spl_exclude_tools, sub_search_arg_cmd = (
            cls._load_safe_spl_config()
        )
        # Load generating commands
        generating_commands = cls._load_generating_commands()

        # Load application metadata
        app_name, app_version = cls._load_app_metadata()

        return cls(
            base_url=base_url,
            timeout=timeout,
            ssl_verify=ssl_verify,
            safe_spl_commands=safe_spl_commands,
            generating_commands=generating_commands,
            safe_spl_exclude_tools=safe_spl_exclude_tools,
            app_name=app_name,
            app_version=app_version,
            max_row_limit=max_row_limit,
            default_row_limit=default_row_limit,
            sub_search_arg_cmd=sub_search_arg_cmd,
            require_encrypted_token=require_encrypted_token,
            token_key_reload_interval_seconds=token_key_reload_interval_seconds,
        )

    @classmethod
    def _load_safe_spl_config(cls) -> Tuple[Set, Set, Dict]:
        """
        Load safe SPL configuration from JSON, supporting the new safe_spl.json format.

        Returns:
            Tuple of (safe_spl_commands, exclude_tools, sub_search_arg_cmd)
        """
        logger.debug("Loading safe SPL configuration")

        def process_json_file(file_path: Path) -> Optional[Tuple[Set, Set, Dict]]:
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                commands = set(data.get("safe_spl_commands", []))
                exclude_tools = set(data.get("exclude_tools", []))
                sub_search_arg_cmd = data.get("sub_search_arg_cmd", {})
                logger.info(
                    "Loaded %d safe SPL commands, %d exclusions, and sub_search_arg_cmd from %s",
                    len(commands),
                    len(exclude_tools),
                    file_path,
                )
                return commands, exclude_tools, sub_search_arg_cmd
            except Exception as e:
                logger.error("Failed to load safe SPL config from %s: %s", file_path, e)
                return None

        result = find_file_in_app_dirs("safe_spl.json", process_json_file)
        if result:
            commands, exclude_tools, sub_search_arg_cmd = result
            return commands, exclude_tools, sub_search_arg_cmd

        logger.warning(
            "No safe SPL configuration found; defaulting to empty sets and dict"
        )
        return set(), set(), {}

    @classmethod
    def _load_generating_commands(cls) -> Set[str]:
        """
        Load the set of SPL generating commands from configuration.

        Returns:
            Set of generating command names.
        """
        logger.debug("Loading generating commands")

        def process_json_file(file_path: Path) -> Optional[Set[str]]:
            """Process JSON file containing generating commands."""
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    commands = set(data.get("generating_commands", []))
                    logger.info(
                        "Loaded %d generating commands from %s",
                        len(commands),
                        file_path,
                    )
                    return commands
            except Exception as e:
                logger.error(
                    "Failed to load generating commands from %s: %s", file_path, e
                )
                return None

        commands = find_file_in_app_dirs("generating_commands.json", process_json_file)
        return commands if commands is not None else set()

    @classmethod
    def _load_app_metadata(cls) -> Tuple[str, str]:
        """
        Load application name and version from app.conf.

        Returns:
            Tuple containing (app_name, app_version).
        """
        logger.debug("Loading application metadata")

        def process_app_conf(file_path: Path) -> Optional[Tuple[str, str]]:
            """Process app.conf file to extract metadata."""
            config = configparser.ConfigParser()
            try:
                config.read(file_path)

                # Prefer app id for REST namespace; fallback to launcher title.
                name = config.get("id", "name", fallback=None)
                if not name:
                    name = config.get("package", "id", fallback=None)
                if not name:
                    name = config.get("launcher", "title", fallback="Splunk_MCP_Server")

                version = config.get("id", "version", fallback=None)
                if not version:
                    version = config.get("launcher", "version", fallback="0.1.0")

                logger.info(
                    "Loaded app metadata from %s: %s v%s", file_path, name, version
                )
                return name, version
            except Exception as e:
                logger.warning("Failed to load app metadata from %s: %s", file_path, e)
                return None

        metadata = find_file_in_app_dirs("app.conf", process_app_conf)
        return metadata if metadata is not None else ("Splunk_MCP_Server", "0.1.0")

    @classmethod
    def reset_singleton(cls) -> None:
        """
        Reset the singleton instance for testing purposes.

        This method clears the cached singleton instance, forcing
        re-initialization on the next call to get().
        """
        global _singleton_instance
        logger.info("Resetting MCPSettings singleton instance")
        _singleton_instance = None

    def __str__(self) -> str:
        """
        Return string representation of settings.

        Returns:
            Human-readable string containing key configuration values.
        """
        # Show path or bool meaningfully
        ssl_repr = (
            self.ssl_verify
            if isinstance(self.ssl_verify, bool)
            else f"path:{self.ssl_verify}"
        )
        return (
            f"MCPSettings(base_url={self.base_url}, timeout={self.timeout}, "
            f"ssl_verify={ssl_repr}, safe_commands={len(self.safe_spl_commands)}, "
            f"generating_commands={len(self.generating_commands)}, "
            f"require_encrypted_token={self.require_encrypted_token}, "
            f"token_key_reload_interval_seconds={self.token_key_reload_interval_seconds})"
        )


# Public API exports
__all__ = [
    "MCPSettings",
    "get_config_value",
    "find_file_in_app_dirs",
    "get_splunk_mgmt_port",
]
