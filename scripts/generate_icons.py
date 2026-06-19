#!/usr/bin/env python3
"""Generate PNG icons of various sizes for the Microsoft Word add-in manifest."""

from PIL import Image, ImageDraw
from pathlib import Path

def generate_icons():
    public_dir = Path(__file__).resolve().parent.parent / "web-app" / "public"
    public_dir.mkdir(parents=True, exist_ok=True)
    
    sizes = [16, 32, 64, 80, 128]
    
    for size in sizes:
        # Create an image with a light-blue background (#bae6fd)
        image = Image.new("RGBA", (size, size), "#bae6fd")
        draw = ImageDraw.Draw(image)
        
        # Draw a book cover outline in accent-text blue (#0284c7)
        margin = max(2, int(size * 0.15))
        draw.rectangle(
            [margin, margin, size - margin, size - margin],
            outline="#0284c7",
            width=max(1, int(size * 0.06)),
            fill="#ffffff"
        )
        
        # Draw center spine line
        mid = size // 2
        draw.line(
            [mid, margin, mid, size - margin],
            fill="#0284c7",
            width=max(1, int(size * 0.06))
        )
        
        # Draw decorative text lines on left and right pages
        line_margin = margin + max(1, int(size * 0.08))
        draw.line(
            [line_margin, margin + max(2, int(size * 0.25)), mid - max(1, int(size * 0.05)), margin + max(2, int(size * 0.25))],
            fill="#475569",
            width=max(1, int(size * 0.04))
        )
        draw.line(
            [line_margin, margin + max(2, int(size * 0.5)), mid - max(1, int(size * 0.05)), margin + max(2, int(size * 0.5))],
            fill="#475569",
            width=max(1, int(size * 0.04))
        )
        draw.line(
            [mid + max(1, int(size * 0.05)), margin + max(2, int(size * 0.25)), size - line_margin, margin + max(2, int(size * 0.25))],
            fill="#475569",
            width=max(1, int(size * 0.04))
        )
        draw.line(
            [mid + max(1, int(size * 0.05)), margin + max(2, int(size * 0.5)), size - line_margin, margin + max(2, int(size * 0.5))],
            fill="#475569",
            width=max(1, int(size * 0.04))
        )
        
        dest_path = public_dir / f"icon-{size}.png"
        image.save(dest_path, "PNG")
        print(f"Generated {dest_path}")

if __name__ == "__main__":
    generate_icons()
