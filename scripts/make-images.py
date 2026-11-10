#!/usr/bin/env python3
"""Download AI scene images for a Short from Pollinations (free, no key).

Usage:
    python scripts/make-images.py <prompts.json> <output-dir>

<prompts.json> is a JSON list of prompt strings. One image is downloaded per
prompt from https://image.pollinations.ai/prompt/<url-encoded prompt> at
1080x1920 (vertical, nologo) with a deterministic per-index seed, and saved
as scene-1.jpg, scene-2.jpg, ... inside <output-dir> (Remotion serves it
from public/, e.g. public/scenes/). Prompts are used exactly as given —
the caller (Make/Claude step in the workflow) supplies anime-style prompts;
no studio frames or copyrighted stills are ever fetched here.

Each download is retried up to 3 times with exponential backoff and the
result is verified to really be a JPEG (magic bytes) before being saved.
If any image still fails after its retries the script exits non-zero with
a clear message, so the GitHub Actions step fails loudly instead of
rendering a video with missing scenes.
"""
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

API_BASE = "https://image.pollinations.ai/prompt/"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
JPEG_MAGIC = b"\xff\xd8\xff"
MAX_ATTEMPTS = 3
TIMEOUT_SECONDS = 180


def build_url(prompt: str, index: int) -> str:
    seed = index * 7 + 3
    encoded = urllib.parse.quote(prompt, safe="")
    return f"{API_BASE}{encoded}?width=1080&height=1920&nologo=true&seed={seed}"


def download(url: str, dest: Path) -> int:
    """Download one image, retrying with backoff. Returns the byte size."""
    last_error: Exception | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
                data = response.read()
            if not data.startswith(JPEG_MAGIC):
                raise ValueError(f"response is not a JPEG (first bytes: {data[:8]!r})")
            dest.write_bytes(data)
            return len(data)
        except (urllib.error.URLError, ValueError, OSError) as exc:
            last_error = exc
            if attempt < MAX_ATTEMPTS:
                wait = 5 * (2 ** (attempt - 1))
                print(f"  attempt {attempt} failed ({exc}); retrying in {wait}s", file=sys.stderr)
                time.sleep(wait)
    raise RuntimeError(f"download failed after {MAX_ATTEMPTS} attempts: {last_error}")


def main() -> int:
    if len(sys.argv) < 3:
        print("Usage: python scripts/make-images.py <prompts.json> <output-dir>", file=sys.stderr)
        return 2

    prompts_path = Path(sys.argv[1])
    out_dir = Path(sys.argv[2])

    try:
        prompts = json.loads(prompts_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"ERROR: could not read prompts JSON {prompts_path}: {exc}", file=sys.stderr)
        return 1
    if not isinstance(prompts, list) or not prompts:
        print(f"ERROR: {prompts_path} must contain a non-empty JSON list of prompt strings", file=sys.stderr)
        return 1
    prompts = [str(p) for p in prompts]

    out_dir.mkdir(parents=True, exist_ok=True)

    for index, prompt in enumerate(prompts):
        dest = out_dir / f"scene-{index + 1}.jpg"
        print(f"[{index + 1}/{len(prompts)}] {dest.name} (seed={index * 7 + 3})")
        try:
            size = download(build_url(prompt, index), dest)
        except RuntimeError as exc:
            print(
                f"ERROR: scene {index + 1} could not be downloaded after "
                f"{MAX_ATTEMPTS} retries: {exc}",
                file=sys.stderr,
            )
            return 1
        print(f"  ok ({size} bytes)")

    print(f"Done: {len(prompts)} scene image(s) in {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
