"""Shared contracts for the Studio worker and server.

Pydantic models here are the single source of truth for both HTTP APIs (PRD section 6).
The OpenAPI documents in ``contracts/openapi/`` are generated from the apps that use
them; run ``make contracts`` after any change.
"""

API_VERSION = "1.0"
"""Worker API version, ``major.minor``. A server refuses a worker whose major differs."""

__all__ = ["API_VERSION"]
