"""Run CPU OCR without opening the application database."""

from __future__ import annotations

import argparse
from importlib.metadata import version
from pathlib import Path
from tempfile import TemporaryDirectory


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify Shici OCR without database writes")
    parser.add_argument("--image", type=Path, help="Optional real vocabulary page")
    parser.add_argument("--language", choices=("ch", "en"), default="ch")
    args = parser.parse_args()
    if args.image and not args.image.is_file():
        parser.error("--image must name a readable existing image")

    from PIL import Image, ImageDraw, ImageFont

    from app.config import get_settings
    from app.services.ocr import configure_paddle_environment, get_paddle_provider

    configure_paddle_environment()
    print(f"PaddlePaddle: {version('paddlepaddle')}")
    print(f"PaddleOCR: {version('paddleocr')}")
    provider = get_paddle_provider(language=args.language, use_gpu=False)
    with TemporaryDirectory(prefix="verify-ocr-", dir=get_settings().ocr_temp_dir) as scratch:
        image_path = args.image
        if image_path is None:
            image_path = Path(scratch) / "sample.png"
            image = Image.new("RGB", (1000, 240), "white")
            draw = ImageDraw.Draw(image)
            draw.text(
                (40, 80),
                "hello world vocabulary",
                fill="black",
                font=ImageFont.load_default(size=48),
            )
            image.save(image_path)
        document = provider.extract(image_path)

    if not document.text.strip():
        raise SystemExit("FAIL: no text recognized")
    if args.image is None and "hello" not in document.text.lower():
        raise SystemExit(f"FAIL: synthetic sample was not recognized correctly: {document.text}")
    print(document.text)
    status = Path("/proc/self/status")
    if status.exists():
        for line in status.read_text().splitlines():
            if line.startswith(("VmRSS:", "VmHWM:")):
                print(line)
    print(f"PASS: OCR recognized {len(document.lines)} lines; no database was opened")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
