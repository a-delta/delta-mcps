"""
Authorization Module for MCP Server.

This module provides authentication and authorization functionality for the MCP server,
including token validation against Splunk's authentication endpoints and CUI token exchange.
"""

import base64
import fnmatch
import hashlib
import json
import time
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urljoin

import requests
from crypto import TokenCrypto
from logging_config import get_logger
from settings import MCPSettings
from splunk_api import call_splunk_api

# Module logger
logger = get_logger(__name__)

_CACHE_TTL_SECONDS = 300
_CUI_TOKEN_CACHE: Dict[Tuple[bytes, str], Tuple[str, float]] = {}


def _decode_jwt_no_verify(token: str) -> Dict[str, Any]:
    """
    Decode a JWT payload without verifying the signature.
    """
    parts = token.split(".")
    if len(parts) != 3:
        raise ValueError("Invalid JWT structure")

    payload_segment = parts[1]
    padding = "=" * (-len(payload_segment) % 4)
    payload_bytes = base64.urlsafe_b64decode(payload_segment + padding)
    payload = json.loads(payload_bytes.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Invalid JWT payload")
    return payload


def _decode_jwt_header_payload_no_verify(
    token: str,
) -> Optional[Tuple[Dict[str, Any], Dict[str, Any]]]:
    parts = token.split(".")
    if len(parts) != 3:
        return None

    try:
        header_segment = parts[0]
        payload_segment = parts[1]
        header_padding = "=" * (-len(header_segment) % 4)
        payload_padding = "=" * (-len(payload_segment) % 4)
        header_bytes = base64.urlsafe_b64decode(header_segment + header_padding)
        payload_bytes = base64.urlsafe_b64decode(payload_segment + payload_padding)
        header = json.loads(header_bytes.decode("utf-8"))
        payload = json.loads(payload_bytes.decode("utf-8"))
    except Exception:
        return None

    if not isinstance(header, dict) or not isinstance(payload, dict):
        return None

    return header, payload


def _is_sis_token(token: str) -> bool:
    decoded = _decode_jwt_header_payload_no_verify(token)
    if not decoded:
        return False

    header, payload = decoded
    token_type = header.get("token_type")
    if isinstance(token_type, str) and token_type.lower() == "sis":
        return True

    issuer = payload.get("iss")
    if isinstance(issuer, str) and "/sis" in issuer:
        return True

    return False


def _extract_tenant_id(token: str) -> Optional[str]:
    """
    Decode a JWT without verification and pull out the tenant identifier.

    Returns the value of a common tenant claim if present, otherwise None.
    """
    if not token:
        return None

    try:
        payload = _decode_jwt_no_verify(token)
    except Exception:
        return None

    for key in ("tenant_id", "tenantId", "tid", "tenant", "realm"):
        tenant_val = payload.get(key)
        if tenant_val:
            return str(tenant_val)
    return None


def _is_cui_issuer(issuer: str) -> bool:
    """Check if issuer matches the expected Webex CUI patterns."""
    normalized = issuer.rstrip("/")
    return fnmatch.fnmatch(normalized, "https://idbroker*.webex.com/idb*")


def _hash_token(token: str) -> bytes:
    return hashlib.sha256(token.encode("utf-8")).digest()


def _get_cache_key(token: str, tenant_id: Optional[str]) -> Tuple[bytes, str]:
    return _hash_token(token), tenant_id or ""


def _get_cached_token(cache_key: Tuple[bytes, str]) -> Optional[str]:
    cached = _CUI_TOKEN_CACHE.get(cache_key)
    if not cached:
        return None
    value, expires_at = cached
    if expires_at < time.time():
        _CUI_TOKEN_CACHE.pop(cache_key, None)
        return None
    return value


def _set_cached_token(cache_key: Tuple[bytes, str], value: str) -> None:
    _CUI_TOKEN_CACHE[cache_key] = (value, time.time() + _CACHE_TTL_SECONDS)


def exchange_cui_token(
    auth_token: str, tenant_id: Optional[str] = None
) -> Tuple[bool, Optional[str], Optional[str]]:
    """
    Exchange a Webex CUI JWT for a Splunk access token.

    Returns (success flag, exchanged token if any, error message if any).
    """
    log_extra = {"tenant_id": tenant_id}

    if not auth_token:
        logger.warning("No CUI token provided for exchange", extra=log_extra)
        return False, None, "No bearer token provided"

    try:
        payload = _decode_jwt_no_verify(auth_token)
    except Exception as e:
        # If we cannot decode, treat it as a non-CUI token and let normal auth flow continue
        logger.debug(
            "CUI token decode failed, treating as non-CUI: %s", e, extra=log_extra
        )
        return False, None, "Not a CUI token"

    issuer = str(payload.get("iss", "")).rstrip("/")
    if not _is_cui_issuer(issuer):
        return False, None, "Not a CUI token"

    tenant_id = tenant_id or _extract_tenant_id(auth_token)
    log_extra["tenant_id"] = tenant_id

    client_id = payload.get("client_id")
    if not client_id:
        logger.error("Missing client_id in CUI token", extra=log_extra)
        return False, None, "Missing client_id in CUI token"

    cache_key = _get_cache_key(auth_token, tenant_id)
    cached = _get_cached_token(cache_key)
    if cached:
        return True, cached, None

    settings = MCPSettings.get()
    token_endpoint = urljoin(settings.base_url, "oauth2/v1/token")

    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    data = {
        "client_id": client_id,
        "grant_type": "client_credentials",
        "client_assertion_type": (
            "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"
        ),
        "client_assertion": auth_token,
        "issuer": issuer,
    }

    try:
        response = requests.post(
            token_endpoint,
            headers=headers,
            data=data,
            timeout=settings.timeout,
            verify=settings.ssl_verify,
        )
    except requests.RequestException as e:
        logger.exception("CUI token exchange request failed", extra=log_extra)
        return False, None, f"Token exchange error: {e}"

    if response.status_code != 200:
        logger.error(
            "CUI token exchange failed with status %d: %s",
            response.status_code,
            response.text,
            extra=log_extra,
        )
        return False, None, "Token exchange failed"

    try:
        response_data = response.json()
        exchanged_token = response_data.get("access_token")
    except Exception as e:
        logger.error("Failed to parse token exchange response: %s", e, extra=log_extra)
        return False, None, "Invalid token exchange response"

    if not exchanged_token:
        logger.error("access_token missing in token exchange response", extra=log_extra)
        return False, None, "Token exchange response missing access_token"

    _set_cached_token(cache_key, exchanged_token)
    logger.info("Successfully exchanged CUI token", extra=log_extra)
    return True, exchanged_token, None


class MCPAuthorization:
    """
    Handles MCP server authorization and token validation.

    This class provides methods to validate authentication tokens against
    Splunk's authentication endpoints and manage authorization state.
    """

    @staticmethod
    def validate_token(
        auth_token: str,
    ) -> Tuple[bool, Optional[str], Optional[Dict[str, Any]], Optional[str], bool]:
        """
        Validate an authentication token using Splunk's current-context endpoint.

        This method calls the Splunk REST API endpoint services/authentication/current-context
        to validate the provided session key and retrieve user context information.

        Args:
            auth_token: The authentication token to validate.

        Returns:
            Tuple containing:
                - bool: True if token is valid, False otherwise
                - Optional[str]: Original or decrypted token
                - Optional[Dict[str, Any]]: User context information if successful, None if failed
                - Optional[str]: Error message if validation failed, None if successful
                - bool: True if the token was obtained via CUI exchange
        """
        logger.debug("Validating authentication token")
        tenant_id = _extract_tenant_id(auth_token)
        log_extra = {"tenant_id": tenant_id}
        was_cui_exchange = False

        if not auth_token:
            logger.warning(
                "Empty or None session key provided for validation", extra=log_extra
            )
            return False, None, None, "No bearer token provided", was_cui_exchange

        # If this is a CUI token, exchange it for a Splunk token before validation
        cui_ok, exchanged_token, cui_error = exchange_cui_token(auth_token, tenant_id)
        if cui_ok and exchanged_token:
            auth_token = exchanged_token
            tenant_id = tenant_id or _extract_tenant_id(auth_token)
            log_extra["tenant_id"] = tenant_id
            was_cui_exchange = True
        elif cui_error and cui_error != "Not a CUI token":
            logger.warning("CUI token exchange failed: %s", cui_error, extra=log_extra)
            return (
                False,
                None,
                None,
                f"Authentication failed: {cui_error}",
                was_cui_exchange,
            )

        # Decrypt auth token if needed
        is_sis_token = _is_sis_token(auth_token)
        if not is_sis_token:
            try:
                auth_token = TokenCrypto.get_instance().decrypt(auth_token)
                tenant_id = tenant_id or _extract_tenant_id(auth_token)
                log_extra["tenant_id"] = tenant_id
            except ValueError as e:
                # If encryption is required, reject
                settings = MCPSettings.get()
                if settings.require_encrypted_token:
                    logger.warning(
                        "Encrypted token required but decryption failed: %s",
                        e,
                        extra=log_extra,
                    )
                    return (
                        False,
                        None,
                        None,
                        "Authentication failed: encrypted token required",
                        was_cui_exchange,
                    )

        try:
            # Call Splunk's current-context endpoint to validate the token
            response = call_splunk_api(
                session_key=auth_token,
                method="GET",
                api="services/authentication/current-context",
                params={"output_mode": "json"},
            )

            if response.status_code == 200:
                try:
                    context_data = response.json()

                    # Extract user information from the response
                    entry = context_data.get("entry", [{}])
                    if entry:
                        content = entry[0].get("content", {})
                        user_info = {
                            "username": content.get("username"),
                            "email": content.get("email"),
                            "realname": content.get("realname"),
                            "roles": content.get("roles", []),
                            "capabilities": content.get("capabilities", []),
                        }

                        logger.info(
                            "Token validation successful for user: %s",
                            user_info.get("username", "unknown"),
                        )
                        return True, auth_token, user_info, None, was_cui_exchange
                    else:
                        logger.error(
                            "No entry found in current-context response",
                            extra=log_extra,
                        )
                        return (
                            False,
                            None,
                            None,
                            "Invalid response format from authentication endpoint",
                            was_cui_exchange,
                        )

                except Exception as e:
                    logger.error(
                        "Failed to parse current-context response: %s",
                        e,
                        extra=log_extra,
                    )
                    return (
                        False,
                        None,
                        None,
                        f"Failed to parse authentication response: {e}",
                        was_cui_exchange,
                    )

            elif response.status_code == 401:
                logger.warning(
                    "Token validation failed - unauthorized", extra=log_extra
                )
                return False, None, None, "Invalid or expired token", was_cui_exchange

            elif response.status_code == 403:
                logger.warning("Token validation failed - forbidden", extra=log_extra)
                return False, None, None, "Access denied for token", was_cui_exchange

            else:
                logger.error(
                    "Token validation failed with status %d: %s",
                    response.status_code,
                    response.text,
                    extra=log_extra,
                )
                return (
                    False,
                    None,
                    None,
                    f"Authentication endpoint error: {response.status_code}",
                    was_cui_exchange,
                )

        except Exception as e:
            logger.exception(
                "Unexpected error during token validation: %s", e, extra=log_extra
            )
            return False, None, None, f"Token validation error: {e}", was_cui_exchange

    @staticmethod
    def check_user_capabilities(
        user_info: Dict[str, Any], required_capabilities: list = None
    ) -> Tuple[bool, Optional[str]]:
        """
        Check if a user has required capabilities.

        Args:
            user_info: User information dictionary from validate_token
            required_capabilities: List of required capabilities. If None, only checks if user is valid.

        Returns:
            Tuple containing:
                - bool: True if user has required capabilities, False otherwise
                - Optional[str]: Error message if check failed, None if successful
        """
        if not user_info:
            return False, "No user information provided"

        username = user_info.get("username")
        if not username:
            return False, "No username found in user information"

        if required_capabilities is None:
            # No specific capabilities required, just need valid user
            return True, None

        user_capabilities = user_info.get("capabilities", [])

        for required_cap in required_capabilities:
            if required_cap not in user_capabilities:
                logger.warning(
                    "User %s missing required capability: %s", username, required_cap
                )
                return False, f"Missing required capability: {required_cap}"

        logger.debug(
            "User %s has all required capabilities: %s", username, required_capabilities
        )
        return True, None


# Public API exports
__all__ = [
    "MCPAuthorization",
    "exchange_cui_token",
]
