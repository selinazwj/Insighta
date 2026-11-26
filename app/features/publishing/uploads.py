"""Validate poster contents before exposing them under the static image origin."""
from pathlib import Path
import shutil
import uuid
import warnings

from fastapi import HTTPException
from PIL import Image, UnidentifiedImageError


UPLOAD_DIR = Path("app/static/uploads")
IMAGE_SUFFIXES = {
    "JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp", "GIF": ".gif",
    "BMP": ".bmp", "TIFF": ".tiff", "ICO": ".ico", "AVIF": ".avif",
}


def save_cover_image(upload) -> str:
    """Keep accepted image bytes intact and use a content-derived safe suffix."""
    source = upload.file
    try:
        source.seek(0)
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(source) as picture:
                suffix = IMAGE_SUFFIXES.get(picture.format)
                if suffix is None:
                    raise HTTPException(400, "Upload a valid JPEG, PNG, WebP, GIF, BMP, TIFF, ICO, or AVIF image.")
                picture.verify()
        source.seek(0)
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError,
            Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise HTTPException(400, "Upload a valid image file. HTML, scripts, and SVG documents are not supported.")

    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"{uuid.uuid4()}{suffix}"
    path = UPLOAD_DIR / filename
    # Exclusive creation must succeed before this request owns cleanup. A UUID
    # collision must never remove the image that already occupies this path.
    destination = path.open("xb")
    try:
        with destination:
            shutil.copyfileobj(source, destination)
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return f"/static/uploads/{filename}"
