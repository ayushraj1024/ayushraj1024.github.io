#!/usr/bin/env python3
"""Authorize the CLI with Google and save a refreshable token."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from blogger_auth import find_credentials_file, list_blogs, run_oauth_flow


def parse_args() -> argparse.Namespace:
    root = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description="Authorize WP to Blogger CLI with Google OAuth.")
    parser.add_argument(
        "--credentials",
        type=Path,
        default=None,
        help="Path to Desktop OAuth client JSON (auto-detected by default)",
    )
    parser.add_argument(
        "--token",
        type=Path,
        default=root / "token.json",
        help="Where to save OAuth tokens (default: token.json)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8080,
        help="Local OAuth redirect port (default: 8080)",
    )
    parser.add_argument(
        "--list-blogs",
        action="store_true",
        help="After auth, print Blogger blogs for this Google account",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    credentials_path = args.credentials or find_credentials_file()

    try:
        token = run_oauth_flow(credentials_path, args.token, port=args.port)
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print(f"Saved OAuth token to {args.token}", file=sys.stderr)

    if args.list_blogs:
        blogs = list_blogs(token.access_token)
        if not blogs:
            print("No Blogger blogs found for this account.", file=sys.stderr)
            return 0
        print("\nBlogger blogs:", file=sys.stderr)
        for blog in blogs:
            print(
                f"  - {blog.get('name')}  id={blog.get('id')}  url={blog.get('url')}",
                file=sys.stderr,
            )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
