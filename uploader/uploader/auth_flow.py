"""One-time helper: perform the YouTube OAuth consent flow and save token.json.

Run locally (needs a browser) from the directory that contains
``client_secret.json``::

    python -m uploader.auth_flow

The resulting ``token.json`` is written next to ``client_secret.json`` and
is what the uploader agent reads at runtime.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .auth import SCOPES


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config-dir", default=".",
                        help="directory containing client_secret.json "
                             "(default: current directory)")
    args = parser.parse_args(argv)

    from google_auth_oauthlib.flow import InstalledAppFlow

    config = Path(args.config_dir)
    secret_path = config / "client_secret.json"
    if not secret_path.exists():
        print(f"ERROR: {secret_path} not found. Download the OAuth Desktop "
              "client secret from Google Cloud and place it here.",
              file=sys.stderr)
        return 1

    flow = InstalledAppFlow.from_client_secrets_file(
        str(secret_path), SCOPES)
    creds = flow.run_local_server(port=0)
    token_path = config / "token.json"
    token_path.write_text(creds.to_json())
    print(f"Saved credentials to {token_path}")
    print("Mount this directory (with both client_secret.json and "
          "token.json) at the uploader's --config-dir.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
