#!/usr/bin/env python3
"""Publish exported WordPress posts to Blogger."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from blogger_auth import (
    BLOGGER_API_BASE,
    blogger_request,
    find_credentials_file,
    get_access_token,
)
from blogger_labels import sanitize_blogger_labels


def parse_args() -> argparse.Namespace:
    root = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(
        description="Upload exported posts from blogger-posts.json to Blogger."
    )
    parser.add_argument(
        "--input",
        "-i",
        type=Path,
        default=root / "blogger-posts.json",
        help="Exported JSON file (default: blogger-posts.json)",
    )
    parser.add_argument(
        "--blog-id",
        required=False,
        help="Target Blogger blog ID (overrides value in export meta)",
    )
    parser.add_argument(
        "--credentials",
        type=Path,
        default=None,
        help="OAuth client JSON path (auto-detected by default)",
    )
    parser.add_argument(
        "--token",
        type=Path,
        default=root / "token.json",
        help="Saved OAuth token file (default: token.json)",
    )
    parser.add_argument(
        "--state",
        type=Path,
        default=root / "posted-state.json",
        help="Track already-uploaded WordPress post IDs",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=90,
        help="Max posts to upload this run (default: 90, under Blogger daily cap)",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=3.0,
        help="Seconds to wait between posts and API steps (default: 3.0)",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=5,
        help="Retries per post when Blogger returns HTTP 429 (default: 5)",
    )
    parser.add_argument(
        "--rate-limit-wait",
        type=float,
        default=90.0,
        help="Base seconds to wait before retrying after HTTP 429 (default: 90)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print actions without calling Blogger",
    )
    parser.add_argument(
        "--publish",
        action="store_true",
        help="Publish drafts immediately with original publishDate",
    )
    return parser.parse_args()


def load_state(state_path: Path) -> dict[str, Any]:
    if not state_path.exists():
        return {"uploaded": {}}
    return json.loads(state_path.read_text(encoding="utf-8"))


def save_state(state_path: Path, state: dict[str, Any]) -> None:
    state_path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")


def query_flags_from_insert_url(url: str) -> dict[str, str]:
    query = parse_qs(urlparse(url).query)
    return {key: values[0] for key, values in query.items() if values}


def insert_post(
    blog_id: str,
    access_token: str,
    body: dict[str, Any],
    *,
    is_draft: bool,
    fetch_images: bool,
) -> dict[str, Any]:
    url = f"{BLOGGER_API_BASE}/blogs/{blog_id}/posts"
    query = {
        "isDraft": str(is_draft).lower(),
        "fetchImages": str(fetch_images).lower(),
    }
    return blogger_request("POST", url, access_token, body=body, query=query)


def publish_post(
    blog_id: str,
    post_id: str,
    access_token: str,
    publish_date: str,
) -> dict[str, Any]:
    url = f"{BLOGGER_API_BASE}/blogs/{blog_id}/posts/{post_id}/publish"
    return blogger_request(
        "POST",
        url,
        access_token,
        query={"publishDate": publish_date},
    )


def is_rate_limit_error(exc: RuntimeError) -> bool:
    message = str(exc)
    return "HTTP 429" in message or "rateLimitExceeded" in message


def is_invalid_argument_error(exc: RuntimeError) -> bool:
    message = str(exc)
    return (
        "HTTP 400" in message
        or "INVALID_ARGUMENT" in message
        or "badRequest" in message
    )


def prepare_post_bodies(body: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    original = dict(body)
    sanitized = dict(body)
    if "labels" in sanitized:
        sanitized["labels"] = sanitize_blogger_labels(sanitized["labels"])

    attempts: list[tuple[str, dict[str, Any]]] = [("original labels", original)]
    if sanitized != original:
        attempts.append(("sanitized labels", sanitized))

    without_labels = {key: value for key, value in body.items() if key != "labels"}
    if without_labels != original:
        attempts.append(("no labels", without_labels))
    return attempts


def call_with_retry(
    action: str,
    callback,
    *,
    max_retries: int,
    rate_limit_wait: float,
) -> dict[str, Any]:
    for attempt in range(max_retries + 1):
        try:
            return callback()
        except RuntimeError as exc:
            if not is_rate_limit_error(exc) or attempt == max_retries:
                raise
            wait_seconds = rate_limit_wait * (2 ** attempt)
            print(
                f"  rate limited during {action}; waiting {wait_seconds:.0f}s "
                f"(retry {attempt + 1}/{max_retries})...",
                file=sys.stderr,
            )
            time.sleep(wait_seconds)


def upload_single_post(
    *,
    blog_id: str,
    access_token: str,
    body: dict[str, Any],
    is_draft: bool,
    fetch_images: bool,
    publish: bool,
    publish_date: str,
    delay: float,
    max_retries: int,
    rate_limit_wait: float,
) -> tuple[str, str | None]:
    last_error: RuntimeError | None = None
    created: dict[str, Any] | None = None
    attempt_name = "insert"

    for attempt_name, attempt_body in prepare_post_bodies(body):
        try:
            created = call_with_retry(
                "insert",
                lambda payload=attempt_body: insert_post(
                    blog_id,
                    access_token,
                    payload,
                    is_draft=is_draft,
                    fetch_images=fetch_images,
                ),
                max_retries=max_retries,
                rate_limit_wait=rate_limit_wait,
            )
            if attempt_name != "original labels":
                print(f"  note: posted using {attempt_name}", file=sys.stderr)
            break
        except RuntimeError as exc:
            last_error = exc
            if is_invalid_argument_error(exc) and attempt_name != "no labels":
                print(f"  invalid request with {attempt_name}; retrying...", file=sys.stderr)
                continue
            raise

    if created is None:
        assert last_error is not None
        raise last_error

    blogger_post_id = created["id"]
    blogger_url = created.get("url")

    if publish and is_draft:
        if delay > 0:
            time.sleep(delay)
        created = call_with_retry(
            "publish",
            lambda: publish_post(
                blog_id,
                blogger_post_id,
                access_token,
                publish_date,
            ),
            max_retries=max_retries,
            rate_limit_wait=rate_limit_wait,
        )
        blogger_url = created.get("url", blogger_url)

    return blogger_post_id, blogger_url


def main() -> int:
    args = parse_args()

    if not args.input.exists():
        print(f"Error: input file not found: {args.input}", file=sys.stderr)
        return 1

    payload = json.loads(args.input.read_text(encoding="utf-8"))
    blog_id = args.blog_id or payload.get("meta", {}).get("bloggerBlogId")
    if not blog_id:
        print(
            "Error: provide --blog-id or export with --blog-id so meta.bloggerBlogId is set.",
            file=sys.stderr,
        )
        return 1

    posts = payload.get("posts", [])
    state = load_state(args.state)
    uploaded = state.setdefault("uploaded", {})

    pending = [
        post
        for post in posts
        if str(post["wordpress"]["id"]) not in uploaded
    ]
    batch = pending[: max(args.limit, 0)]

    if not batch:
        print("Nothing to upload. All posts in the export are already marked uploaded.", file=sys.stderr)
        return 0

    credentials_path = args.credentials or find_credentials_file()
    access_token = None
    if not args.dry_run:
        try:
            access_token = get_access_token(credentials_path, args.token)
        except (FileNotFoundError, RuntimeError, ValueError) as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1

    print(
        f"Uploading {len(batch)} of {len(pending)} pending posts to blog {blog_id}...",
        file=sys.stderr,
    )

    success_count = 0
    for index, post in enumerate(batch, start=1):
        wp_id = str(post["wordpress"]["id"])
        title = post["blogger"]["insert"]["body"]["title"]
        insert_url = post["blogger"]["insert"]["url"]
        flags = query_flags_from_insert_url(insert_url)
        is_draft = flags.get("isDraft", "true").lower() == "true"
        fetch_images = flags.get("fetchImages", "true").lower() == "true"
        body = post["blogger"]["insert"]["body"]
        publish_date = post["wordpress"]["published"]

        print(f"[{index}/{len(batch)}] {title}", file=sys.stderr)

        if args.dry_run:
            mode = "draft" if is_draft else "publish"
            print(f"  dry-run: would insert as {mode}", file=sys.stderr)
            if args.publish and is_draft:
                print(f"  dry-run: would publish with date {publish_date}", file=sys.stderr)
            success_count += 1
            continue

        try:
            blogger_post_id, blogger_url = upload_single_post(
                blog_id=blog_id,
                access_token=access_token,  # type: ignore[arg-type]
                body=body,
                is_draft=is_draft,
                fetch_images=fetch_images,
                publish=args.publish,
                publish_date=publish_date,
                delay=args.delay,
                max_retries=args.max_retries,
                rate_limit_wait=args.rate_limit_wait,
            )

            uploaded[wp_id] = {
                "bloggerPostId": blogger_post_id,
                "url": blogger_url,
                "title": title,
                "uploadedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
            save_state(args.state, state)
            success_count += 1
            print(f"  ok: {blogger_url}", file=sys.stderr)
        except RuntimeError as exc:
            print(f"  failed: {exc}", file=sys.stderr)
            if is_rate_limit_error(exc):
                print(
                    "Rate limit persisted after retries. Wait longer, then rerun the "
                    "same command; completed posts are already saved.",
                    file=sys.stderr,
                )
            elif is_invalid_argument_error(exc):
                print(
                    "Blogger rejected this post even after label/content fallbacks.",
                    file=sys.stderr,
                )
            else:
                print(
                    "Stopping early so you can retry later without duplicating posts.",
                    file=sys.stderr,
                )
            break

        if index < len(batch) and args.delay > 0:
            time.sleep(args.delay)

    print(f"Finished. Uploaded {success_count} post(s). State saved to {args.state}", file=sys.stderr)
    remaining = len(pending) - success_count
    if remaining > 0:
        print(f"{remaining} post(s) still pending.", file=sys.stderr)
    return 0 if success_count else 1


if __name__ == "__main__":
    raise SystemExit(main())
