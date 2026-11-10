#!/usr/bin/env python3
"""Generate the Hindi voiceover MP3 with Microsoft Edge TTS (free, no account).

Usage:
    python scripts/make-voiceover.py [script.txt] [out.mp3] [voice]

Defaults: script = pilot-script.txt, out = public/voiceover.mp3,
voice = hi-IN-SwaraNeural (female). Alternative: hi-IN-MadhurNeural (male).
Also writes a .srt word-timing file next to the MP3 (handy for debugging sync;
the Remotion composition does its own caption timing).

Transport note: this talks to the Edge TTS WebSocket directly with the
`websockets` library, reusing edge-tts' own helpers for the Sec-MS-GEC token,
request headers and SSML. Reason: edge-tts' aiohttp transport fails behind
some HTTP egress proxies ("101 Invalid connection header" on the WS upgrade),
while the plain `websockets` client passes the same proxies cleanly. On a
normal open network (e.g. GitHub Actions) both work; this one works in both.
"""
import asyncio
import json
import os
import ssl as ssl_lib
import sys
from html import escape, unescape
from pathlib import Path

from edge_tts import SubMaker
from edge_tts.communicate import (
    TTSConfig,
    connect_id,
    date_to_string,
    get_headers_and_data,
    mkssml,
    remove_incompatible_characters,
    split_text_by_byte_length,
    ssml_headers_plus_data,
)
from edge_tts.constants import SEC_MS_GEC_VERSION, TICKS_PER_SECOND, WSS_HEADERS, WSS_URL
from edge_tts.drm import DRM
from websockets.asyncio.client import connect

ROOT = Path(__file__).resolve().parent.parent
MP3_BITRATE_BPS = 48_000  # edge-tts output format: audio-24khz-48kbitrate-mono-mp3


def _ssl_context() -> ssl_lib.SSLContext:
    # Honour an environment CA bundle (e.g. an egress-proxy CA in SSL_CERT_FILE);
    # edge-tts' own hardcoded certifi context would not trust such a CA.
    cafile = os.environ.get("SSL_CERT_FILE")
    if cafile and Path(cafile).exists():
        return ssl_lib.create_default_context(cafile=cafile)
    return ssl_lib.create_default_context()


def _proxy() -> str | None:
    return (
        os.environ.get("https_proxy")
        or os.environ.get("HTTPS_PROXY")
        or os.environ.get("http_proxy")
        or os.environ.get("HTTP_PROXY")
    )


async def _synthesise(text: str, voice: str, out_path: Path) -> None:
    cfg = TTSConfig(voice, "+0%", "+0%", "+0Hz", "WordBoundary")
    chunks = split_text_by_byte_length(escape(remove_incompatible_characters(text)), 4096)

    url = (
        f"{WSS_URL}&ConnectionId={connect_id()}"
        f"&Sec-MS-GEC={DRM.generate_sec_ms_gec()}"
        f"&Sec-MS-GEC-Version={SEC_MS_GEC_VERSION}"
    )
    submaker = SubMaker()
    audio = bytearray()
    offset_compensation = 0  # ticks; grows with CBR audio bytes across chunks

    # WSS_HEADERS contains "Sec-WebSocket-Version", which the websockets
    # library sends itself — passing it again makes Microsoft reject the
    # handshake with HTTP 400, so drop it here.
    headers = DRM.headers_with_muid(WSS_HEADERS)
    headers.pop("Sec-WebSocket-Version", None)

    async with connect(
        url,
        additional_headers=headers,
        ssl=_ssl_context(),
        proxy=_proxy(),
        open_timeout=30,
        close_timeout=10,
    ) as ws:
        # speech.config: word boundaries on, sentence boundaries off.
        await ws.send(
            f"X-Timestamp:{date_to_string()}\r\n"
            "Content-Type:application/json; charset=utf-8\r\n"
            "Path:speech.config\r\n\r\n"
            '{"context":{"synthesis":{"audio":{"metadataoptions":{'
            '"sentenceBoundaryEnabled":"false","wordBoundaryEnabled":"true"'
            '},"outputFormat":"audio-24khz-48kbitrate-mono-mp3"}}}}\r\n'
        )

        for partial in chunks:
            await ws.send(ssml_headers_plus_data(connect_id(), date_to_string(), mkssml(cfg, partial)))
            chunk_bytes = 0
            while True:
                msg = await asyncio.wait_for(ws.recv(), timeout=60)
                if isinstance(msg, str):
                    raw = msg.encode("utf-8")
                    params, data = get_headers_and_data(raw, raw.find(b"\r\n\r\n"))
                    path = params.get(b"Path")
                    if path == b"audio.metadata":
                        for meta in json.loads(data)["Metadata"]:
                            if meta["Type"] == "WordBoundary":
                                submaker.feed(
                                    {
                                        "type": "WordBoundary",
                                        "offset": meta["Data"]["Offset"] + offset_compensation,
                                        "duration": meta["Data"]["Duration"],
                                        "text": unescape(meta["Data"]["text"]["Text"]),
                                    }
                                )
                    elif path == b"turn.end":
                        break
                else:
                    if len(msg) < 2:
                        continue
                    header_len = int.from_bytes(msg[:2], "big")
                    params, data = get_headers_and_data(msg, header_len)
                    if params.get(b"Path") == b"audio" and data:
                        audio.extend(data)
                        chunk_bytes += len(data)
            # CBR compensation so word timings stay right across chunks.
            offset_compensation += chunk_bytes * 8 * TICKS_PER_SECOND // MP3_BITRATE_BPS

    if not audio:
        raise RuntimeError("No audio received from Edge TTS")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(bytes(audio))
    srt_path = out_path.with_suffix(".srt")
    srt_path.write_text(submaker.get_srt(), encoding="utf-8")
    print(f"voice={voice}")
    print(f"audio={out_path} ({out_path.stat().st_size} bytes)")
    print(f"subs={srt_path}")


async def main() -> None:
    script_path = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "pilot-script.txt"
    out_path = Path(sys.argv[2]) if len(sys.argv) > 2 else ROOT / "public" / "voiceover.mp3"
    voice = sys.argv[3] if len(sys.argv) > 3 else "hi-IN-SwaraNeural"

    text = script_path.read_text(encoding="utf-8").strip()
    # Read paragraphs as continuous prose.
    text = " ".join(line.strip() for line in text.splitlines() if line.strip())
    await _synthesise(text, voice, out_path)


if __name__ == "__main__":
    asyncio.run(main())
