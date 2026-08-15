"""YouTube OAuth 2.0 credential handling for the uploader agent."""

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger("uploader.auth")

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]


def get_credentials(config_dir: str) -> "object":
    """Return valid YouTube OAuth credentials from the config directory.

    Looks for ``token.json`` (preferred), refreshing it with the refresh
    token when expired. If no valid token exists but ``client_secret.json``
    is present, runs the interactive local-server OAuth flow (requires a
    browser) and stores the resulting token. Raises if no credentials can
    be obtained.
    """
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow

    config = Path(config_dir)
    token_path = config / "token.json"
    secret_path = config / "client_secret.json"

    creds = None
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
    if creds and creds.valid:
        return creds
    if creds and creds.expired and creds.refresh_token:
        logger.info("refreshing expired YouTube token")
        creds.refresh(Request())
        token_path.write_text(creds.to_json())
        return creds
    if secret_path.exists():
        logger.info("running interactive OAuth flow (needs a browser)")
        flow = InstalledAppFlow.from_client_secrets_file(
            str(secret_path), SCOPES)
        creds = flow.run_local_server(port=0)
        token_path.write_text(creds.to_json())
        return creds
    raise RuntimeError(
        f"No YouTube credentials in {config_dir}: need client_secret.json "
        "and a token.json (run the one-time auth flow first).")
