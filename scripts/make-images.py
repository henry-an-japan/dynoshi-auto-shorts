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

Resilience (added after Pollinations' anonymous endpoint started metering
requests and returning HTTP 402 Payment Required mid-run):

* PROVIDER CHAIN per scene — Pollinations is tried with model=turbo first,
  then model=flux. Each variant gets up to 3 attempts with backoff
  (5s, then 15s); any failure (including 402/429 rate-limit responses and
  non-JPEG bodies) counts as a failed attempt and moves the chain along.
* POLITE SPACING — ~6s is left between successful scene downloads so a
  multi-scene run does not hammer the anonymous quota back to back.
* PARTIAL FALLBACK — a scene that fails on every variant no longer kills
  the run: a local cinematic gradient JPEG is generated with Pillow in its
  place (with a loud WARNING). The script exits 0 as long as at least ONE
  real image downloaded, and exits 1 only if EVERY scene fell back (total
  provider failure), so a fully dead provider still fails the step loudly.
"""
import json
import random
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
MODELS = ("turbo", "flux")  # provider chain, in order
MAX_ATTEMPTS = 3
BACKOFF_SECONDS = (5, 15)  # wait before attempt 2 and attempt 3
SPACING_SECONDS = 6  # polite delay between successful scene downloads
TIMEOUT_SECONDS = 180
WIDTH, HEIGHT = 1080, 1920


def build_url(prompt: str, index: int, model: str) -> str:
    seed = index * 7 + 3
    encoded = urllib.parse.quote(prompt, safe="")
    return (
        f"{API_BASE}{encoded}?width={WIDTH}&height={HEIGHT}"
        f"&nologo=true&seed={seed}&model={model}"
    )


def fetch(url: str) -> bytes:
    """One download attempt. Returns JPEG bytes or raises."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        data = response.read()
    if not data.startswith(JPEG_MAGIC):
        raise ValueError(f"response is not a JPEG (first bytes: {data[:8]!r})")
    return data


def download_scene(prompt: str, index: int, dest: Path) -> int | None:
    """Try the provider chain for one scene. Returns byte size or None."""
    for model in MODELS:
        url = build_url(prompt, index, model)
        last_error: Exception | None = None
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                data = fetch(url)
                dest.write_bytes(data)
                return len(data)
            except (urllib.error.URLError, ValueError, OSError) as exc:
                # HTTPError is a URLError subclass: 402 (metered) and 429
                # (rate limited) land here and count as failed attempts.
                last_error = exc
                if attempt < MAX_ATTEMPTS:
                    wait = BACKOFF_SECONDS[attempt - 1]
                    print(
                        f"  model={model} attempt {attempt} failed ({exc}); "
                        f"retrying in {wait}s",
                        file=sys.stderr,
                    )
                    time.sleep(wait)
        print(
            f"  model={model} exhausted after {MAX_ATTEMPTS} attempts "
            f"({last_error}); moving to next variant",
            file=sys.stderr,
        )
    return None


def make_fallback(dest: Path, index: int) -> int:
    """Generate a local cinematic gradient JPEG with Pillow."""
    from PIL import Image, ImageDraw, ImageFilter

    rng = random.Random(index * 7 + 3)
    top = (26, 18, 68)  # deep indigo
    bottom = (2, 2, 6)  # near black

    # Vertical gradient.
    img = Image.new("RGB", (WIDTH, HEIGHT))
    draw = ImageDraw.Draw(img)
    for y in range(HEIGHT):
        t = y / (HEIGHT - 1)
        color = tuple(round(top[c] + (bottom[c] - top[c]) * t) for c in range(3))
        draw.line([(0, y), (WIDTH, y)], fill=color)

    # Soft radial glow (off-centre, position varies per scene).
    glow = Image.new("RGB", (WIDTH, HEIGHT), (0, 0, 0))
    glow_draw = ImageDraw.Draw(glow)
    gx = WIDTH // 2 + rng.randint(-260, 260)
    gy = HEIGHT // 3 + rng.randint(-300, 300)
    gr = rng.randint(420, 640)
    glow_draw.ellipse(
        [gx - gr, gy - gr, gx + gr, gy + gr], fill=(58, 44, 128)
    )
    glow = glow.filter(ImageFilter.GaussianBlur(radius=gr // 2))
    img = _add_light(img, glow)

    # A few large translucent circles for depth.
    overlay = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    overlay_draw = ImageDraw.Draw(overlay)
    circle_tints = [(96, 80, 190), (52, 120, 200), (140, 70, 180), (40, 60, 160)]
    for _ in range(5):
        cx = rng.randint(-200, WIDTH)
        cy = rng.randint(0, HEIGHT)
        r = rng.randint(180, 460)
        tint = circle_tints[rng.randrange(len(circle_tints))]
        overlay_draw.ellipse(
            [cx - r, cy - r, cx + r, cy + r], fill=(*tint, rng.randint(18, 42))
        )
    overlay = overlay.filter(ImageFilter.GaussianBlur(radius=40))
    img = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")

    img.save(dest, "JPEG", quality=88)
    return dest.stat().st_size


def _add_light(base, glow):
    """Screen-blend the glow layer onto the base image (no numpy needed)."""
    from PIL import ImageChops

    return ImageChops.screen(base, glow)


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

    real_count = 0
    fallback_count = 0
    for index, prompt in enumerate(prompts):
        dest = out_dir / f"scene-{index + 1}.jpg"
        print(f"[{index + 1}/{len(prompts)}] {dest.name} (seed={index * 7 + 3})")
        size = download_scene(prompt, index, dest)
        if size is not None:
            real_count += 1
            print(f"  ok — real image ({size} bytes)")
            if index < len(prompts) - 1:
                print(f"  spacing {SPACING_SECONDS}s before next scene (quota)")
                time.sleep(SPACING_SECONDS)
            continue
        # All provider variants failed for this scene: fall back locally.
        print(
            f"WARNING: scene {index + 1} failed on all provider variants "
            f"({', '.join(MODELS)}); generating a local fallback image instead",
            file=sys.stderr,
        )
        try:
            size = make_fallback(dest, index)
        except Exception as exc:  # Pillow missing/broken: count and continue.
            print(f"ERROR: fallback generation for scene {index + 1} failed: {exc}", file=sys.stderr)
            continue
        fallback_count += 1
        print(f"  fallback image written ({size} bytes)")

    print(
        f"Done: {real_count} real + {fallback_count} fallback "
        f"scene image(s) in {out_dir}"
    )
    if real_count == 0:
        print(
            "ERROR: every scene fell back — total provider failure, "
            "failing the step instead of rendering without real images",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
