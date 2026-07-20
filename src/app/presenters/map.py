"""Текстовая и фото-карта: чистая сборка подписи/HTML."""
from __future__ import annotations

from app.rendering.map_image import MapPhoto, build_map_caption


def render_map_text(
    *,
    title: str,
    day_number: int,
    grid: str,
    footer: str,
) -> str:
    text = f"🗺️ {title} (день {day_number})\n<pre>{grid}</pre>"
    if footer:
        text += f"\n\n{footer}"
    return text


def compose_map_photo(
    *,
    png_bytes: bytes,
    title: str,
    day_number: int,
    footer: str,
    fingerprint: str,
    file_id: str | None,
) -> MapPhoto:
    caption, caption_extra = build_map_caption(
        title=title,
        day_number=day_number,
        footer=footer,
    )
    return MapPhoto(
        png_bytes=png_bytes,
        caption=caption,
        fingerprint=fingerprint,
        file_id=file_id,
        caption_extra=caption_extra,
    )
