"""Generate QALAU's backdrop: uv run --with pillow scripts/make_background.py."""
import argparse
import base64
from io import BytesIO
import os
from pathlib import Path

from dotenv import load_dotenv
from openai import APIStatusError, OpenAI
from PIL import Image, ImageFilter, PngImagePlugin

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "web/qalau/assets"
MODELS = ("gpt-image-2.5-flare", "gpt-image-2.5-sunburst", "gpt-image-2")
PROMPT = """Create a premium abstract background for QALAU, a quiet professional
interface in the OpenAI visual style. Wide landscape, deep calm charcoal and
warm graphite, with subtly luminous blue-violet and muted sage highlights.
Broad soft abstract light and flare shapes drift through the edges, with a
gentle atmospheric gradient and an open, dark center. Beautiful restrained
light diffusion, smooth tonal transitions, barely perceptible fine grain.
Very low contrast across the entire frame: white and muted light-gray interface
text must remain readable everywhere under a dark overlay. No bright white
spots, sharp edges, saturated neon, text, letters, objects, people, logos,
symbols, borders, or watermarks. An abstract field of light only."""


def save_webp(image, path, limit, quality=88):
    """Keep dimensions where possible; enforce the transfer-size budget."""
    while True:
        buffer = BytesIO()
        image.save(buffer, format="WEBP", quality=quality, method=6)
        if buffer.tell() <= limit:
            path.write_bytes(buffer.getvalue())
            return
        if quality > 40:
            quality -= 8
        else:
            image = image.resize((max(1, image.width * 4 // 5),
                                  max(1, image.height * 4 // 5)), Image.Resampling.LANCZOS)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true", help="Regenerate the source PNG")
    args = parser.parse_args()
    png = ASSETS / "bg-hero.png"
    print(f"Prompt:\n{PROMPT}", flush=True)
    ASSETS.mkdir(parents=True, exist_ok=True)
    if png.exists() and not args.force:
        print("PNG exists; skipping API generation (use --force to replace).", flush=True)
    else:
        load_dotenv(ROOT / ".env")
        if not os.getenv("OPENAI_API_KEY"):
            raise SystemExit("Set OPENAI_API_KEY in the environment or project .env")
        client = OpenAI(timeout=600, max_retries=0)
        for model in MODELS:
            print(f"Generating: model={model}, size=1536x1024, quality=high, n=1", flush=True)
            try:
                result = client.images.generate(model=model, prompt=PROMPT,
                                                size="1536x1024", quality="high", n=1)
            except APIStatusError as exc:
                # Authentication, rate limits and server failures are not model fallbacks.
                if exc.status_code not in (400, 403, 404, 422):
                    raise
                print(f"Model unavailable/rejected (HTTP {exc.status_code}): {model}", flush=True)
                continue
            if not result.data or not result.data[0].b64_json:
                raise RuntimeError("Images API returned no base64 image")
            source = Image.open(BytesIO(base64.b64decode(result.data[0].b64_json)))
            metadata = PngImagePlugin.PngInfo()
            metadata.add_text("model", model)
            metadata.add_text("prompt", PROMPT)
            source.convert("RGB").save(png, pnginfo=metadata)
            break
        else:
            raise SystemExit("All requested image models rejected the generation request")
    with Image.open(png) as source:
        print(f"Source model: {source.info.get('model', 'unknown (existing PNG)')}", flush=True)
        image = source.convert("RGB")
        save_webp(image, ASSETS / "bg-hero.webp", 600_000)
        image.thumbnail((384, 256), Image.Resampling.LANCZOS)
        save_webp(image.filter(ImageFilter.GaussianBlur(10)),
                  ASSETS / "bg-hero-blur.webp", 20_000, quality=80)
    for path in (png, ASSETS / "bg-hero.webp", ASSETS / "bg-hero-blur.webp"):
        print(f"{path.relative_to(ROOT)}: {path.stat().st_size:,} bytes", flush=True)


if __name__ == "__main__":
    main()
