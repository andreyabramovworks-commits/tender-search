"""One-time OAuth consent: creates a server-only Google Drive refresh token."""

import argparse
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("client_secret", type=Path)
    parser.add_argument("--output", type=Path, default=Path(".secrets/google-drive-oauth.json"))
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    flow = InstalledAppFlow.from_client_secrets_file(
        str(args.client_secret), scopes=["https://www.googleapis.com/auth/drive"]
    )
    credentials = flow.run_local_server(host="localhost", port=0, open_browser=True)
    args.output.write_text(credentials.to_json(), encoding="utf-8")
    print(f"OAuth token written to {args.output}")


if __name__ == "__main__":
    main()
