"""Authorised HTTP transport for googleapiclient with a timeout on every call.

Without a timeout a stalled Google connection blocks the calling worker forever;
with it the call fails, is retried by the job, and the worker is never lost.
"""

from __future__ import annotations

import google_auth_httplib2
import httplib2

from app.core.config import get_settings


def authorized_http(credentials) -> google_auth_httplib2.AuthorizedHttp:
    timeout = get_settings().google_call_timeout_seconds
    return google_auth_httplib2.AuthorizedHttp(credentials, http=httplib2.Http(timeout=timeout))
