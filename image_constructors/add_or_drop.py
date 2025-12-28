#!/usr/bin/env python3

from __future__ import annotations

from pathlib import Path
from typing import Optional, Tuple
import requests
from PIL import Image, ImageDraw, ImageFont, ImageFilter

OUTPUT_PATH = Path("out_add.png")

# Use URL (what you get from ESPN) OR set PLAYER_IMAGE_PATH instead
PLAYER_IMAGE_PATH: Optional[Path] = None  # e.g. Path("player.png")     # your normalized owner photo

# Coordinates (top-left), from your message:
PLAYER_POS = (0, 156.4)
CAPTION1_POS = (573.1, 336.6)  # "TEAM ADDS" (ADDS green)
CAPTION2_POS = (546.7, 399.1)  # "Player Name"
OWNER_POS = (565.5, 28.2)

FONT_PATH_ITALIC = "/System/Library/Fonts/Supplemental/Arial.ttf"
FONT_PATH_BOLD   = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"

# Font sizes (from your message)
CAPTION1_SIZE = 24   
CAPTION2_SIZE = 35 

CAPTION_LEFT_X = 509.4
CAPTION_RIGHT_X = 766.0

# Colors
WHITE = (255, 255, 255, 255)
GREEN = (90, 255, 80, 255)  # tweak to match Canva
SHADOW = (0, 0, 0, 160)

# OPTIONAL: If you want consistent sizing, set slot sizes.
# If None, we paste at natural size (but will auto-downscale if it would overflow canvas).
PLAYER_SLOT: Optional[Tuple[int, int]] = None  # e.g. (900, 900)
OWNER_SLOT: Optional[Tuple[int, int]] = None   # e.g. (300, 300)

# Shadow tuning
TEXT_SHADOW_OFFSET = (3, 3)
TEXT_SHADOW_BLUR = 2

# ----------------------------
# HELPERS
# ----------------------------

def ixy(pos: Tuple[float, float]) -> Tuple[int, int]:
    return (int(round(pos[0])), int(round(pos[1])))

def fetch_image(url: str) -> Image.Image:
    r = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()
    from io import BytesIO
    return Image.open(BytesIO(r.content)).convert("RGBA")

def load_font(font_path: Optional[str], size: int) -> ImageFont.ImageFont:
    candidates = []
    if font_path:
        candidates.append(font_path)

    # Known-good macOS font paths
    candidates += [
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/Library/Fonts/Arial.ttf",
    ]

    for p in candidates:
        try:
            return ImageFont.truetype(p, size=size)
        except Exception:
            continue

    raise RuntimeError(
        "Could not load a scalable .ttf font. "
        "Set FONT_PATH_ITALIC / FONT_PATH_BOLD to a real .ttf file in your project."
    )

def downscale_to_fit(img: Image.Image, max_w: int, max_h: int) -> Image.Image:
    w, h = img.size
    if w <= max_w and h <= max_h:
        return img
    scale = min(max_w / w, max_h / h)
    nw, nh = max(1, int(w * scale)), max(1, int(h * scale))
    return img.resize((nw, nh), Image.Resampling.LANCZOS)

def paste_rgba(base: Image.Image, overlay: Image.Image, xy: Tuple[int, int]) -> None:
    # Use alpha as mask if present
    base.alpha_composite(overlay, dest=xy)

def draw_text_shadowed(draw: ImageDraw.ImageDraw, base: Image.Image, xy: Tuple[int, int], text: str,
                       font: ImageFont.ImageFont, fill, shadow_fill=SHADOW,
                       shadow_offset=TEXT_SHADOW_OFFSET, shadow_blur=TEXT_SHADOW_BLUR,
                       stroke_width: int = 0, stroke_fill=None) -> None:
    # Shadow on separate layer (so blur only affects shadow)
    shadow_layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    sd = ImageDraw.Draw(shadow_layer)
    sx, sy = xy[0] + shadow_offset[0], xy[1] + shadow_offset[1]
    sd.text((sx, sy), text, font=font, fill=shadow_fill, stroke_width=stroke_width, stroke_fill=stroke_fill)
    shadow_layer = shadow_layer.filter(ImageFilter.GaussianBlur(radius=shadow_blur))
    base.alpha_composite(shadow_layer)

    draw.text(xy, text, font=font, fill=fill, stroke_width=stroke_width, stroke_fill=stroke_fill)


def fit_font_to_width(draw: ImageDraw.ImageDraw, text: str, font_path: Optional[str], start_size: int, max_width: float) -> ImageFont.ImageFont:
    size = start_size
    while size > 8:
        f = load_font(font_path, size)
        w = draw.textlength(text, font=f)
        if w <= max_width:
            return f
        size -= 1
    return load_font(font_path, 8)

def centered_x_for_text(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, left_x: float, right_x: float) -> int:
    region_w = right_x - left_x
    text_w = draw.textlength(text, font=font)
    x = left_x + (region_w - text_w) / 2.0
    return int(round(x))


# image construction function for lone adds and drops
# takes in player image, player name, owner nickname and transaction type
def construct_image_adds_or_drops(
    player_name: str,
    player_img_url: str,
    owner_nickname: str,
    transaction_type: str
) -> Image.Image:

    # ensure validity of parameters
    if not player_name or not owner_nickname or not transaction_type:
        raise ValueError("Invalid parameters: missing text fields")
    if not player_img_url:
        raise ValueError("Invalid parameters: missing image urls")

    t = transaction_type.strip().upper()

    # Map transaction -> verb + color
    # (Assumes you already have GREEN defined; if you don't have RED yet, add it.)
    RED = (235, 72, 72, 255)  # fallback red if you don't already have one

    if t in ("ADD", "ADDED"):
        verb = " ADDS"
        verb_color = GREEN
    elif t in ("DROP", "DROPPED"):
        verb = " DROPS"
        verb_color = RED
    else:
        raise ValueError(f"Unknown transaction_type: {transaction_type}")

    # Use args for caption variables
    TEAM_NAME = owner_nickname
    PLAYER_NAME = player_name
    PLAYER_IMAGE_URL = player_img_url
    OWNER_IMAGE_PATH = Path(f"../owner_imgs/{owner_nickname}.jpeg")
    TEMPLATE_PATH = Path("../templates/template_add_or_drop.png")

    base = Image.open(TEMPLATE_PATH).convert("RGBA")
    W, H = base.size
    draw = ImageDraw.Draw(base)

    # ---- load images
    # player
    if PLAYER_IMAGE_PATH is not None:
        player_img = Image.open(PLAYER_IMAGE_PATH).convert("RGBA")
    else:
        player_img = fetch_image(PLAYER_IMAGE_URL)

    # owner
    owner_img = Image.open(OWNER_IMAGE_PATH).convert("RGBA")

    # ---- paste player
    px, py = ixy(PLAYER_POS)
    if PLAYER_SLOT is not None:
        player_img = downscale_to_fit(player_img, PLAYER_SLOT[0], PLAYER_SLOT[1])
    player_img = downscale_to_fit(player_img, W - px, H - py)
    paste_rgba(base, player_img, (px, py))

    # ---- paste owner
    ox, oy = ixy(OWNER_POS)
    if OWNER_SLOT is not None:
        owner_img = downscale_to_fit(owner_img, OWNER_SLOT[0], OWNER_SLOT[1])
    owner_img = downscale_to_fit(owner_img, W - ox, H - oy)
    paste_rgba(base, owner_img, (ox, oy))

    # ---- fonts
    f1 = load_font(FONT_PATH_ITALIC, CAPTION1_SIZE)
    f2 = load_font(FONT_PATH_BOLD, CAPTION2_SIZE)

    # ---- caption bounds
    left_x = CAPTION_LEFT_X
    right_x = CAPTION_RIGHT_X
    max_w = right_x - left_x

    # ---- caption 1: centered "TEAM ADDS/DROPS"
    c1x_raw, c1y = ixy(CAPTION1_POS)
    team_part = TEAM_NAME
    verb_part = verb
    full_line1 = f"{team_part}{verb_part}"

    f1 = fit_font_to_width(draw, full_line1, FONT_PATH_ITALIC, CAPTION1_SIZE, max_w)
    line1_x = centered_x_for_text(draw, full_line1, f1, left_x, right_x)

    # draw TEAM
    draw_text_shadowed(draw, base, (line1_x, c1y), team_part, f1, WHITE, stroke_width=0)

    # draw VERB right after TEAM (keeps total centered)
    team_w = draw.textlength(team_part, font=f1)
    verb_x = int(round(line1_x + team_w))
    draw_text_shadowed(draw, base, (verb_x, c1y), verb_part, f1, verb_color, stroke_width=0)

    # ---- caption 2: centered PLAYER NAME
    c2x_raw, c2y = ixy(CAPTION2_POS)
    f2 = fit_font_to_width(draw, PLAYER_NAME, FONT_PATH_BOLD, CAPTION2_SIZE, max_w)
    line2_x = centered_x_for_text(draw, PLAYER_NAME, f2, left_x, right_x)
    draw_text_shadowed(draw, base, (line2_x, c2y), PLAYER_NAME, f2, WHITE, stroke_width=0)

    base.save(OUTPUT_PATH)
    print(f"✅ wrote: {OUTPUT_PATH.resolve()}  (template size: {W}x{H})")

    return base


# ----------------------------
# MAIN ---- UNCOMMENT THIS TO TEST THIS SCRIPT ALONE
# ----------------------------



def main() -> None:

    # Test parameters
    TEAM_NAME = "moja"
    PLAYER_NAME = "Bucky Irving"
    PLAYER_IMAGE_URL = "https://a.espncdn.com/combiner/i?img=/i/headshots/nfl/players/full/2577417.png"
    img = construct_image_adds_or_drops(
        player_name=PLAYER_NAME,
        player_img_url=PLAYER_IMAGE_URL,
        owner_nickname=TEAM_NAME,
        transaction_type="ADD"
    )
    

'''
    if not TEMPLATE_PATH.exists():
        raise FileNotFoundError(f"Missing template: {TEMPLATE_PATH.resolve()}")
    if not OWNER_IMAGE_PATH.exists():
        raise FileNotFoundError(f"Missing owner image: {OWNER_IMAGE_PATH.resolve()}")

    base = Image.open(TEMPLATE_PATH).convert("RGBA")
    W, H = base.size
    draw = ImageDraw.Draw(base)

    # ---- load images
    if PLAYER_IMAGE_PATH is not None:
        player_img = Image.open(PLAYER_IMAGE_PATH).convert("RGBA")
    else:
        player_img = fetch_image(PLAYER_IMAGE_URL)

    owner_img = Image.open(OWNER_IMAGE_PATH).convert("RGBA")

    # ---- paste player (no forced resize unless slot set; but clamp to canvas bounds)
    px, py = ixy(PLAYER_POS)
    if PLAYER_SLOT is not None:
        # keep aspect ratio but fit into slot
        player_img = downscale_to_fit(player_img, PLAYER_SLOT[0], PLAYER_SLOT[1])
    # clamp so it won’t overflow right/bottom
    player_img = downscale_to_fit(player_img, W - px, H - py)
    paste_rgba(base, player_img, (px, py))

    # ---- paste owner
    ox, oy = ixy(OWNER_POS)
    if OWNER_SLOT is not None:
        owner_img = downscale_to_fit(owner_img, OWNER_SLOT[0], OWNER_SLOT[1])
    owner_img = downscale_to_fit(owner_img, W - ox, H - oy)
    paste_rgba(base, owner_img, (ox, oy))

    # ---- fonts
    f1 = load_font(FONT_PATH_ITALIC, CAPTION1_SIZE)
    f2 = load_font(FONT_PATH_BOLD, CAPTION2_SIZE)

    # ---- caption bounds
    left_x = CAPTION_LEFT_X
    right_x = CAPTION_RIGHT_X
    max_w = right_x - left_x

    # ---- caption 1: centered "TEAM ADDS" (ADDS green), auto-shrink to fit
    c1x_raw, c1y = ixy(CAPTION1_POS)
    team_part = TEAM_NAME
    adds_part = " ADDS"
    full_line1 = f"{team_part}{adds_part}"

    f1 = fit_font_to_width(draw, full_line1, FONT_PATH_ITALIC, CAPTION1_SIZE, max_w)

    line1_x = centered_x_for_text(draw, full_line1, f1, left_x, right_x)

    # draw TEAM
    draw_text_shadowed(draw, base, (line1_x, c1y), team_part, f1, WHITE, stroke_width=0)

    # draw ADDS right after TEAM (still centered as a whole)
    team_w = draw.textlength(team_part, font=f1)
    adds_x = int(round(line1_x + team_w))
    draw_text_shadowed(draw, base, (adds_x, c1y), adds_part, f1, GREEN, stroke_width=0)

    # ---- caption 2: centered PLAYER NAME, auto-shrink to fit
    c2x_raw, c2y = ixy(CAPTION2_POS)
    f2 = fit_font_to_width(draw, PLAYER_NAME, FONT_PATH_BOLD, CAPTION2_SIZE, max_w)

    line2_x = centered_x_for_text(draw, PLAYER_NAME, f2, left_x, right_x)
    draw_text_shadowed(draw, base, (line2_x, c2y), PLAYER_NAME, f2, WHITE, stroke_width=0)

    base.save(OUTPUT_PATH)
    print(f"✅ wrote: {OUTPUT_PATH.resolve()}  (template size: {W}x{H})")
'''

if __name__ == "__main__":
    main()

