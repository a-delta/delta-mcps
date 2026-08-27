"""
Splunk API Client Module.

This module provides a comprehensive interface for interacting with Splunk's REST API,
including SPL query execution, safety validation, and response processing. It handles
authentication, error management, and data format conversion for MCP integration.
"""

from __future__ import annotations

import http
import json
import re
import time
from functools import lru_cache
from typing import Any, Dict, List, Optional, Tuple, Union
from urllib.parse import urljoin

import requests
import urllib3
from logging_config import get_logger
from requests import Response
from settings import MCPSettings

# Suppress SSL warnings when verification is disabled
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Module logger
logger = get_logger(__name__)


def normalize_search_command(spl: str, max_row_limit: Optional[int] = None) -> str:
    """
    Normalize SPL search command to ensure proper format and add row limit.

    This function standardizes SPL queries by ensuring they start with appropriate
    search commands and adds row limiting to prevent excessive resource usage.

    Args:
        spl: The SPL query string to normalize.
        max_row_limit: Maximum number of rows to return. If None, uses settings.

    Returns:
        Normalized SPL query string with head limit applied.
    """
    if max_row_limit is None:
        settings = MCPSettings.get()
        max_row_limit = settings.max_row_limit

    # Remove leading/trailing whitespace
    spl = spl.strip()
    if not spl:
        logger.warning("Empty SPL query provided")
        return ""

    logger.debug("Normalizing SPL query: %s", spl)

    # Check if already starts with 'search' or pipe
    if spl.lower().startswith("search ") or spl.startswith("|"):
        normalized = f"{spl} | head {max_row_limit + 1}"
        logger.debug("Query already properly formatted, added head limit")
        return normalized

    # Tokenize to get first meaningful word
    tokens = spl.split(maxsplit=1)
    if not tokens:
        return ""

    first_word = tokens[0].lower()

    # Get generating commands from settings
    settings = MCPSettings.get()
    if first_word in settings.generating_commands:
        normalized = f"{spl} | head {max_row_limit + 1}"
        logger.debug("Query starts with generating command, added head limit")
        return normalized

    # Otherwise, prepend 'search' and add head limit
    normalized = f"search {spl} | head {max_row_limit + 1}"
    logger.debug("Prepended 'search' and added head limit")
    return normalized


def check_spl_safe(
    settings: MCPSettings, session_key: str, spl_query: str
) -> Tuple[bool, str]:
    """
    Validate that a Splunk query is safe to execute, including recursive subsearch validation.

    This function uses Splunk's parser API to analyze the query structure
    and ensures all commands are in the allowed safe commands list.

    Args:
        settings: MCPSettings instance containing configuration.
        session_key: Splunk session key for authentication.
        spl_query: The Splunk query to validate.

    Returns:
        Tuple containing:
            - bool: True if query is safe, False otherwise
            - str: Status message or error description
    """
    logger.info("Validating SPL query safety: %s", spl_query)

    try:
        # Parse the query using Splunk API
        response = call_splunk_api(
            session_key=session_key,
            method="POST",
            api="services/search/parser",
            data={
                "q": spl_query,
                "expand_macros": "0",
                "output_mode": "json",
                "parse_only": "1",
            },
        )

        if response.status_code != 200:
            logger.error(
                "Parser API returned error %d: %s", response.status_code, response.text
            )
            return False, f"Error parsing Splunk query: {response.text}"

        # Parse the response
        query_tree = response.json()
        parsed_commands = query_tree.get("commands", [])

        # Check each command against safe list
        for cmd in parsed_commands:
            cmd_name = cmd.get("command", "").strip().lower()
            if cmd_name and cmd_name not in settings.safe_spl_commands:
                logger.warning("Forbidden command detected: %s", cmd_name)
                return False, f"Forbidden command found: {cmd_name}"

            # Recursively check subsearches if defined
            if cmd_name in settings.sub_search_arg_cmd:
                subsearch_args = settings.sub_search_arg_cmd[cmd_name]
                cmd_args = cmd.get("args", {})

                for arg_name in subsearch_args:
                    if arg_name == "args":
                        raw_args = cmd.get("rawargs", "")
                        if raw_args:
                            subsearch_matches = re.findall(r"\[([^]]+)]", raw_args)
                            for subsearch in subsearch_matches:
                                is_safe, message = check_spl_safe(
                                    settings, session_key, subsearch.strip()
                                )
                                if not is_safe:
                                    return (
                                        False,
                                        f"Unsafe subsearch in {cmd_name}: {message}",
                                    )
                    else:
                        values_to_check: List[str] = []
                        if isinstance(cmd_args, dict):
                            if arg_name in cmd_args:
                                arg_value = cmd_args[arg_name]
                                if isinstance(arg_value, str):
                                    values_to_check.append(arg_value)
                        elif isinstance(cmd_args, list):
                            for item in cmd_args:
                                if isinstance(item, dict) and arg_name in item:
                                    item_value = item[arg_name]
                                    if isinstance(item_value, str):
                                        values_to_check.append(item_value)
                                elif isinstance(item, str):
                                    values_to_check.append(item)
                        elif isinstance(cmd_args, str):
                            values_to_check.append(cmd_args)

                        for value in values_to_check:
                            start = 0
                            while True:
                                open_idx = value.find("[", start)
                                if open_idx == -1:
                                    break
                                close_idx = value.find("]", open_idx + 1)
                                if close_idx == -1:
                                    break
                                subsearch = value[open_idx + 1 : close_idx].strip()
                                if subsearch:
                                    is_safe, message = check_spl_safe(
                                        settings, session_key, subsearch
                                    )
                                    if not is_safe:
                                        return (
                                            False,
                                            f"Unsafe subsearch in {cmd_name} {arg_name}: {message}",
                                        )
                                start = close_idx + 1

        logger.info("SPL query validation passed")
        return True, "Query is safe to run."

    except (json.JSONDecodeError, KeyError) as e:
        logger.error("Failed to parse query validation response: %s", e)
        return False, f"Error parsing Splunk query response: {e}"
    except Exception as e:
        logger.exception("Unexpected error during SPL validation: %s", e)
        return False, f"Validation error: {e}"


def convert_ndjson_to_dict(ndjson: str) -> List[Any]:
    """
    Convert NDJSON (Newline Delimited JSON) string to structured data.

    This function processes Splunk's NDJSON export format, extracting result
    objects and filtering out metadata fields like preview, offset, and lastrow.

    Args:
        ndjson: The NDJSON string to convert.

    Returns:
        Dictionary with 'results' key containing list of parsed result objects.
    """
    logger.debug("Converting NDJSON response to structured data")

    valid_results = []
    lines_processed = 0

    for line in ndjson.splitlines():
        line = line.strip()
        if not line:
            continue

        lines_processed += 1

        try:
            json_obj = json.loads(line)
            if not isinstance(json_obj, dict):
                continue

            # Extract result
            if "result" in json_obj:
                result_data = json_obj["result"]
            else:
                result_data = {}

            # If result_data is a dict remove metadata fields
            if isinstance(result_data, dict):
                # Remove Splunk metadata fields
                metadata_fields = ["preview", "offset", "lastrow"]
                for field in metadata_fields:
                    result_data.pop(field, None)

            # Only include non-empty results
            if result_data:
                valid_results.append(result_data)

        except json.JSONDecodeError as e:
            logger.warning("Failed to parse NDJSON line %d: %s", lines_processed, e)
            continue

    return valid_results


def _build_error_response(status: int, detail: str) -> Response:
    """
    Create a synthetic Response object for error conditions.

    This helper function creates a properly formatted Response object
    for cases where network requests fail or timeout.

    Args:
        status: HTTP status code.
        detail: Error description.

    Returns:
        Response object with error information.
    """
    response = Response()
    response.status_code = status
    response._content = json.dumps({"detail": detail}).encode("utf-8")
    response.headers["Content-Type"] = "application/json"

    logger.debug("Created error response: %d - %s", status, detail)
    return response


def call_splunk_api(
    session_key: str,
    method: str,
    api: str,
    headers: Optional[Dict[str, str]] = None,
    params: Optional[Dict[str, Any]] = None,
    data: Union[Dict, str, bytes, None] = None,
) -> Response:
    """
    Make authenticated HTTP requests to Splunk's REST API.

    This function handles all aspects of Splunk API communication including
    authentication, request formatting, timeout handling, and error management.

    Args:
        session_key: Splunk session key for authentication.
        method: HTTP method (GET, POST, etc.).
        api: API endpoint path relative to base_url.
        headers: Optional additional headers.
        params: Optional query parameters.
        data: Optional request body data.

    Returns:
        Response object from the API call.
    """
    settings = MCPSettings.get()
    url = urljoin(settings.base_url, api)

    logger.debug("Making Splunk API call: %s %s", method.upper(), api)

    # Build request headers
    req_headers: Dict[str, str] = {
        "User-Agent": f"{settings.app_name}/{settings.app_version}",
        "Accept": "application/json",
    }

    # Add authentication if not already present
    if session_key and "Authorization" not in (headers or {}):
        req_headers["Authorization"] = f"Bearer {session_key}"

    # Merge additional headers
    if headers:
        req_headers.update(headers)

    try:
        logger.debug("Request URL: %s, Params: %s", url, params)

        response = requests.request(
            method=method.upper(),
            url=url,
            headers=req_headers,
            params=params,
            data=data,
            timeout=settings.timeout,
            verify=settings.ssl_verify,
        )

        logger.debug("API call completed with status %d", response.status_code)
        return response

    except requests.Timeout as e:
        logger.error("Splunk API call timed out after %ds: %s", settings.timeout, e)
        return _build_error_response(
            int(http.HTTPStatus.GATEWAY_TIMEOUT), "Splunk API call timed out"
        )
    except requests.RequestException as e:
        logger.error("Splunk API request failed: %s", e)
        return _build_error_response(
            int(http.HTTPStatus.INTERNAL_SERVER_ERROR),
            f"Splunk API request failed: {e}",
        )


def run_splunk_query_internal(
    settings: MCPSettings,
    session_key: str,
    query: str,
    earliest_time: str = "-24h",
    latest_time: str = "now",
    row_limit: int = 100,
) -> Dict[str, Any]:
    """
    Execute an SPL query against Splunk's export search endpoint.

    This function provides direct query execution with automatic query normalization
    and result formatting. It is intended for internal use by trusted components
    and does NOT perform safety validation - callers must validate queries first.

    The ``preview`` parameter is explicitly set to False when calling Splunk's
    export endpoint. Splunk defaults preview to True, which streams intermediate
    result snapshots during query execution. This causes two problems:

    1. Performance: Preview snapshots add processing overhead and increase response time.
    2. Correctness: All snapshots are returned as separate NDJSON lines. A query like
       ``stats count`` that should return 1 row may return multiple rows (intermediate
       previews plus the final result).

    Security Note:
        This function performs NO safety validation beyond adding row limits.
        Callers accepting untrusted input MUST validate SPL queries using
        check_spl_safe() before calling this function.

    Args:
        settings: MCPSettings containing API configuration.
        session_key: Splunk session key for authentication.
        query: Raw SPL query (will be normalized and limited).
        earliest_time: Search time window start (Splunk format).
        latest_time: Search time window end (Splunk format).
        row_limit: Number of results to return.

    Returns:
        Dictionary containing either:
            - {"results": [<event_dict>, ...], "truncated": bool, "total_rows": int} on success
            - {"results": [<event_dict>, ...], "truncated": bool, "approx_total": str} when truncated
            - {"status_code": int, "content": <error>} on failure
    """

    logger.info("Executing SPL query: %s", query)

    # Submit query to export endpoint
    response = call_splunk_api(
        session_key=session_key,
        method="POST",
        api="services/search/jobs/export",
        data={
            "search": query,
            "output_mode": "json",
            "earliest_time": earliest_time,
            "latest_time": latest_time,
            "preview": False,
        },
    )

    if response.status_code != 200:
        logger.error("Query execution failed with status %d", response.status_code)
        try:
            error_content = response.json()
        except Exception:
            error_content = response.text

        return {"status_code": response.status_code, "content": error_content}

    logger.debug("Query results: %s", response.text[:500])  # Log first 500 chars

    # Convert NDJSON response to structured data
    results = convert_ndjson_to_dict(response.text)

    truncated = len(results) > row_limit
    result_out: Dict[str, Any] = {
        "results": results[:row_limit],
        "truncated": truncated,
    }

    # Add count information - use consistent integer types where possible
    if len(results) == settings.max_row_limit + 1:
        # When we hit the server limit, we can't know the exact total
        result_out["approx_total"] = f"{settings.max_row_limit}+"
    else:
        # We have the exact count
        result_out["total_rows"] = len(results)

    return result_out


def ttl_cache(ttl_seconds=60):
    def decorator(fn):
        cached_fn = lru_cache(maxsize=None)(fn)
        cached_fn.expiration = 0

        def wrapped(*args, **kwargs):
            if time.time() > cached_fn.expiration:
                cached_fn.cache_clear()
                cached_fn.expiration = time.time() + ttl_seconds
            return cached_fn(*args, **kwargs)

        return wrapped

    return decorator


@ttl_cache(ttl_seconds=60)
def get_installed_apps(
    session_key: str,
) -> Dict[str, Any]:
    """
    Get a list of enabled Splunk application names.

    This function retrieves all locally installed and enabled applications from Splunk using
    the REST API endpoint /services/apps/local. It returns only the names of enabled apps.

    Args:
        settings: MCPSettings containing API configuration.
        session_key: Splunk session key for authentication.

    Returns:
        Dictionary containing either:
            - {"apps": [<app_name_string>, ...]} on success
            - {"error": str, "status_code": int} on failure
    """
    logger.info("Retrieving enabled Splunk application names")

    try:
        # Call the Splunk REST API to get installed apps
        response = call_splunk_api(
            session_key=session_key,
            method="GET",
            api="services/apps/local",
            params={"count": "0", "output_mode": "json"},  # Get all apps
        )

        if response.status_code != 200:
            logger.error(
                "Apps API returned error %d: %s", response.status_code, response.text
            )
            return {
                "error": f"Failed to retrieve apps: {response.text}",
                "status_code": response.status_code,
            }

        # Parse the response
        apps_data = response.json()
        enabled_app_names = []

        # Extract app entries from the response
        entries = apps_data.get("entry", [])

        for entry in entries:
            content = entry.get("content", {})
            # Only include enabled apps
            if not content.get("disabled", False):
                app_name = entry.get("name", "")
                if app_name:
                    enabled_app_names.append(app_name)

        logger.info(
            "Successfully retrieved %d enabled applications", len(enabled_app_names)
        )
        return {"apps": enabled_app_names}

    except json.JSONDecodeError as e:
        logger.error("Failed to parse apps API response: %s", e)
        return {"error": f"Error parsing apps response: {e}", "status_code": 500}
    except Exception as e:
        logger.exception("Unexpected error retrieving apps: %s", e)
        return {"error": f"Unexpected error: {e}", "status_code": 500}


# Public API exports
__all__ = [
    "normalize_search_command",
    "check_spl_safe",
    "convert_ndjson_to_dict",
    "call_splunk_api",
    "run_splunk_query_internal",
    "get_installed_apps",
]
