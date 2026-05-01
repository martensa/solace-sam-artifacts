"""Tool: validate_artifact_exists -- check that an artifact filename
referenced by an upstream agent ACTUALLY exists in the S3 store.

This catches LLM hallucinations: when the WebScraperAgent (or any other
upstream LLM-driven agent) emits a plausible-looking filename without
having actually invoked its underlying tool, the artifact does not
exist on S3 and we mark the reference as null + a hallucination
anomaly.

S3 HEAD is O(1) and authoritative -- the LLM cannot fool the storage.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import boto3
from botocore.exceptions import ClientError, EndpointConnectionError

logger = logging.getLogger("procurement-helper-mcp.validate_artifact")

# S3 client cached at module load. Configured from env vars set by the
# SAM agent's secret -- same envs the SAM artifact service uses.
_S3_CLIENT = None
_BUCKET = os.environ.get("S3_BUCKET_NAME")


def _get_client():
    global _S3_CLIENT
    if _S3_CLIENT is None:
        endpoint = os.environ.get("S3_ENDPOINT_URL")
        region = os.environ.get("AWS_REGION", "us-east-1")
        access_key = os.environ.get("AWS_ACCESS_KEY_ID")
        secret_key = os.environ.get("AWS_SECRET_ACCESS_KEY")
        if not (_BUCKET and endpoint and access_key and secret_key):
            raise RuntimeError(
                "S3 client misconfigured: need S3_BUCKET_NAME, "
                "S3_ENDPOINT_URL, AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY"
            )
        _S3_CLIENT = boto3.client(
            "s3",
            endpoint_url=endpoint,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name=region,
        )
    return _S3_CLIENT


def _build_key(app_name: str, user_id: str, session_id: str, filename: str, version: int) -> str:
    """Reproduce the SAM ScopedArtifactServiceWrapper key layout."""
    return f"{app_name}/{user_id}/{session_id}/{filename}/{version}"


def validate_artifact_exists(arguments: dict[str, Any]) -> dict[str, Any]:
    """Verify an artifact actually exists in S3 (catches hallucinations).

    Args:
        arguments: dict with keys:
            filename: str -- the artifact filename emitted by an upstream agent.
            app_name: str (required) -- SAM app/scope (typically "sam-solace-lab").
            user_id: str (required) -- the requesting user id.
            session_id: str (required) -- the workflow session id.
            version: int (default 0) -- artifact version to check.

    Returns:
        dict with keys:
            filename: str (echo).
            exists: bool -- True iff S3 HEAD succeeded.
            size_bytes: int|None -- only set if exists.
            error: str|None -- error message if check failed for non-404 reasons.
    """
    filename = arguments.get("filename")
    app_name = arguments.get("app_name")
    user_id = arguments.get("user_id")
    session_id = arguments.get("session_id")
    version = int(arguments.get("version", 0))

    if not filename:
        return {"filename": None, "exists": False, "size_bytes": None,
                "error": "filename argument is required"}
    if not (app_name and user_id and session_id):
        return {"filename": filename, "exists": False, "size_bytes": None,
                "error": "app_name/user_id/session_id required"}

    bucket = _BUCKET
    if not bucket:
        return {"filename": filename, "exists": False, "size_bytes": None,
                "error": "S3_BUCKET_NAME not configured"}

    key = _build_key(app_name, user_id, session_id, filename, version)
    try:
        client = _get_client()
        resp = client.head_object(Bucket=bucket, Key=key)
        return {
            "filename": filename,
            "exists": True,
            "size_bytes": int(resp.get("ContentLength", 0)),
            "error": None,
        }
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code")
        if code in ("404", "NoSuchKey", "NotFound"):
            logger.info("Artifact does not exist in S3: %s", key)
            return {"filename": filename, "exists": False, "size_bytes": None,
                    "error": None}
        logger.warning("S3 HEAD failed with non-404 error for %s: %s", key, e)
        return {"filename": filename, "exists": False, "size_bytes": None,
                "error": f"s3_error: {code}"}
    except EndpointConnectionError as e:
        logger.error("S3 endpoint unreachable: %s", e)
        return {"filename": filename, "exists": False, "size_bytes": None,
                "error": "s3_endpoint_unreachable"}
    except Exception as e:
        logger.exception("Unexpected error during artifact validation")
        return {"filename": filename, "exists": False, "size_bytes": None,
                "error": f"unexpected: {type(e).__name__}"}
