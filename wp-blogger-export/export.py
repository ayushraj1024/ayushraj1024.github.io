#!/usr/bin/env python3
"""Export published WordPress posts as Blogger-ready JSON."""

from __future__ import annotations

import argparse
import html
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from blogger_labels import sanitize_blogger_labels


DEFAULT_SITE = "ayushraj.dev"
DEFAULT_OUTPUT = "blogger-posts.json"
BLOGGER_INSERT_PATH = "/blogger/v3/blogs/{blog_id}/posts"
BLOGGER_PUBLISH_PATH = "/blogger/v3/blogs/{blog_id}/posts/{post_id}/publish"
BLOGGER_API_BASE = "https://www.googleapis.com"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Fetch published WordPress posts and write Blogger API-ready JSON."
        )
    )
    parser.add_argument(
        "--site",
        default=DEFAULT_SITE,
        help=f"WordPress site hostname (default: {DEFAULT_SITE})",
    )
    parser.add_argument(
        "--output",
        "-o",
        default=DEFAULT_OUTPUT,
        help=f"Output JSON file path (default: {DEFAULT_OUTPUT})",
    )
    parser.add_argument(
        "--blog-id",
        default=None,
        help="Target Blogger blog ID (optional; fills request URLs when set)",
    )
    parser.add_argument(
        "--per-page",
        type=int,
        default=100,
        help="WordPress API page size, max 100 (default: 100)",
    )
    parser.add_argument(
        "--draft",
        action="store_true",
        help="Mark posts for Blogger import as drafts (recommended)",
    )
    parser.add_argument(
        "--fetch-images",
        action="store_true",
        default=True,
        help="Set fetchImages=true on Blogger insert requests (default: true)",
    )
    parser.add_argument(
        "--no-fetch-images",
        dest="fetch_images",
        action="store_false",
        help="Set fetchImages=false on Blogger insert requests",
    )
    parser.add_argument(
        "--include-tags",
        action="store_true",
        default=True,
        help="Map WordPress tags to Blogger labels (default: true)",
    )
    parser.add_argument(
        "--no-include-tags",
        dest="include_tags",
        action="store_false",
        help="Only use WordPress categories as Blogger labels",
    )
    parser.add_argument(
        "--featured-image",
        action="store_true",
        default=True,
        help="Prepend featured image HTML when missing from content (default: true)",
    )
    parser.add_argument(
        "--no-featured-image",
        dest="featured_image",
        action="store_false",
        help="Do not prepend featured image HTML",
    )
    parser.add_argument(
        "--incremental",
        action="store_true",
        help="Skip WordPress posts already present in the output file",
    )
    parser.add_argument(
        "--delta-output",
        type=Path,
        default=None,
        help="Write only newly found posts to this file (optional)",
    )
    return parser.parse_args()


def api_get(base_url: str, path: str, params: dict[str, Any] | None = None) -> Any:
    query = urllib.parse.urlencode(params or {}, doseq=True)
    url = f"{base_url.rstrip('/')}{path}"
    if query:
        url = f"{url}?{query}"

    request = urllib.request.Request(
        url,
        headers={"User-Agent": "wp-blogger-export/1.0"},
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code} for {url}: {body}") from exc


def fetch_paginated(
    base_url: str,
    resource: str,
    per_page: int,
    extra_params: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    page = 1
    items: list[dict[str, Any]] = []
    params = {"per_page": min(per_page, 100), **(extra_params or {})}

    while True:
        params["page"] = page
        batch = api_get(base_url, f"/wp-json/wp/v2/{resource}", params)
        if not batch:
            break
        items.extend(batch)
        if len(batch) < params["per_page"]:
            break
        page += 1

    return items


def build_taxonomy_maps(base_url: str) -> tuple[dict[int, str], dict[int, str]]:
    categories = fetch_paginated(base_url, "categories", 100)
    tags = fetch_paginated(base_url, "tags", 100)
    category_map = {item["id"]: item["name"] for item in categories}
    tag_map = {item["id"]: item["name"] for item in tags}
    return category_map, tag_map


def to_rfc3339_utc(date_gmt: str) -> str:
    dt = datetime.fromisoformat(date_gmt)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_labels(
    post: dict[str, Any],
    category_map: dict[int, str],
    tag_map: dict[int, str],
    include_tags: bool,
) -> list[str]:
    labels = [category_map[cid] for cid in post.get("categories", []) if cid in category_map]
    if include_tags:
        labels.extend(tag_map[tid] for tid in post.get("tags", []) if tid in tag_map)
    return sanitize_blogger_labels(labels)


def maybe_prepend_featured_image(content: str, image_url: str | None) -> str:
    if not image_url:
        return content
    if image_url in content:
        return content
    return (
        f'<p><img src="{html.escape(image_url, quote=True)}" alt="" /></p>\n'
        f"{content}"
    )


def blogger_insert_url(blog_id: str | None, is_draft: bool, fetch_images: bool) -> str:
    query = urllib.parse.urlencode(
        {
            "isDraft": str(is_draft).lower(),
            "fetchImages": str(fetch_images).lower(),
        }
    )
    blog_token = blog_id or "{blogId}"
    return f"{BLOGGER_API_BASE}{BLOGGER_INSERT_PATH.format(blog_id=blog_token)}?{query}"


def blogger_publish_url(blog_id: str | None, post_id: str = "{postId}") -> str:
    blog_token = blog_id or "{blogId}"
    return (
        f"{BLOGGER_API_BASE}{BLOGGER_PUBLISH_PATH.format(blog_id=blog_token, post_id=post_id)}"
    )


def transform_post(
    post: dict[str, Any],
    category_map: dict[int, str],
    tag_map: dict[int, str],
    *,
    blog_id: str | None,
    is_draft: bool,
    fetch_images: bool,
    include_tags: bool,
    featured_image: bool,
) -> dict[str, Any]:
    title = html.unescape(post["title"]["rendered"])
    content = post["content"]["rendered"]
    if featured_image:
        content = maybe_prepend_featured_image(
            content,
            post.get("jetpack_featured_media_url"),
        )

    published = to_rfc3339_utc(post["date_gmt"])
    labels = build_labels(post, category_map, tag_map, include_tags)

    body: dict[str, Any] = {
        "kind": "blogger#post",
        "title": title,
        "content": content,
    }
    if labels:
        body["labels"] = labels

    return {
        "wordpress": {
            "id": post["id"],
            "slug": post["slug"],
            "url": post["link"],
            "published": published,
            "modified": to_rfc3339_utc(post["modified_gmt"]),
        },
        "blogger": {
            "insert": {
                "method": "POST",
                "url": blogger_insert_url(blog_id, is_draft, fetch_images),
                "headers": {
                    "Authorization": "Bearer {access_token}",
                    "Content-Type": "application/json",
                },
                "body": body,
            },
            "publish": {
                "method": "POST",
                "url": blogger_publish_url(blog_id),
                "headers": {
                    "Authorization": "Bearer {access_token}",
                },
                "query": {
                    "publishDate": published,
                },
                "note": (
                    "Call after insert when isDraft=true to backdate the post. "
                    "Replace {postId} with the id returned by insert."
                ),
            },
        },
    }


def load_existing_export(output_path: Path) -> tuple[list[dict[str, Any]], set[int]]:
    if not output_path.exists():
        return [], set()

    payload = json.loads(output_path.read_text(encoding="utf-8"))
    posts = payload.get("posts", [])
    known_ids = {int(post["wordpress"]["id"]) for post in posts}
    return posts, known_ids


def write_export(output_path: Path, payload: dict[str, Any]) -> None:
    with open(output_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def export_posts(args: argparse.Namespace) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    base_url = f"https://{args.site.strip('/')}"
    output_path = Path(args.output)

    existing_posts: list[dict[str, Any]] = []
    known_ids: set[int] = set()
    if args.incremental:
        existing_posts, known_ids = load_existing_export(output_path)
        if known_ids:
            print(
                f"Incremental mode: {len(known_ids)} posts already in {output_path}",
                file=sys.stderr,
            )

    print(f"Fetching taxonomies from {base_url}...", file=sys.stderr)
    category_map, tag_map = build_taxonomy_maps(base_url)

    print("Fetching published posts...", file=sys.stderr)
    posts = fetch_paginated(
        base_url,
        "posts",
        args.per_page,
        {"status": "publish", "_embed": "0"},
    )
    posts.sort(key=lambda item: item["date_gmt"])

    if args.incremental:
        new_source_posts = [post for post in posts if post["id"] not in known_ids]
        print(
            f"Found {len(new_source_posts)} new post(s) on WordPress "
            f"(remote total: {len(posts)})",
            file=sys.stderr,
        )
    else:
        new_source_posts = posts

    print(f"Transforming {len(new_source_posts)} post(s)...", file=sys.stderr)
    transformed_new = [
        transform_post(
            post,
            category_map,
            tag_map,
            blog_id=args.blog_id,
            is_draft=args.draft,
            fetch_images=args.fetch_images,
            include_tags=args.include_tags,
            featured_image=args.featured_image,
        )
        for post in new_source_posts
    ]

    if args.incremental:
        merged_posts = existing_posts + transformed_new
        merged_posts.sort(key=lambda item: item["wordpress"]["published"])
    else:
        merged_posts = transformed_new

    payload = {
        "meta": {
            "sourceSite": base_url,
            "exportedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "totalPosts": len(merged_posts),
            "newPostsThisRun": len(transformed_new),
            "bloggerBlogId": args.blog_id,
            "defaults": {
                "isDraft": args.draft,
                "fetchImages": args.fetch_images,
            },
            "oauthScope": "https://www.googleapis.com/auth/blogger",
            "notes": [
                "Each post includes a Blogger insert request body in blogger.insert.body.",
                "Use blogger.publish after insert when importing as drafts with original dates.",
                "Blogger may enforce a daily post creation limit (~100 posts/day).",
                "Replace {access_token}, {blogId}, and {postId} placeholders before calling the API.",
            ],
        },
        "posts": merged_posts,
    }
    return payload, transformed_new


def main() -> int:
    args = parse_args()
    try:
        payload, new_posts = export_posts(args)
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    write_export(Path(args.output), payload)

    if args.delta_output is not None:
        delta_payload = {
            "meta": {
                **payload["meta"],
                "totalPosts": len(new_posts),
                "sourceExport": str(args.output),
            },
            "posts": new_posts,
        }
        write_export(args.delta_output, delta_payload)
        print(
            f"Wrote {len(new_posts)} new post(s) to {args.delta_output}",
            file=sys.stderr,
        )

    print(
        f"Wrote {payload['meta']['totalPosts']} total post(s) to {args.output} "
        f"({payload['meta']['newPostsThisRun']} new this run)",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
