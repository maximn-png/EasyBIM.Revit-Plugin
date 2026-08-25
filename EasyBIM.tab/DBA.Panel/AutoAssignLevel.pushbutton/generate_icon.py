# -*- coding: utf-8 -*-
"""
Generates icon.png for the Auto Assign Level pushbutton.

Draws the same design as icon.svg (an element box snapping down an arrow
from a dashed "wrong" level line to the solid, correct floor baseline)
directly with Pillow, supersampled at 8x and downscaled with LANCZOS so
the strokes stay crisp at the final 32x32 button size.

Run with: python generate_icon.py
"""

from PIL import Image, ImageDraw

SCALE = 8
SIZE = 32 * SCALE
COLOR = (42, 75, 124, 255)  # #2A4B7C, matches the other EasyBIM pushbutton icons


def s(v):
    return int(round(v * SCALE))


def main():
    img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    thin = s(2.1)
    thick = s(2.6)

    # element box, currently sitting at the wrong (upper) level
    draw.rounded_rectangle(
        [s(12), s(3), s(20), s(9)],
        radius=s(1),
        outline=COLOR,
        width=thin,
    )

    # dashed line = the level above (not the true baseline), broken where
    # the box sits so the box reads as resting on it
    draw.line([(s(4), s(9)), (s(11), s(9))], fill=COLOR, width=thin)
    draw.line([(s(21), s(9)), (s(28), s(9))], fill=COLOR, width=thin)

    # arrow snapping the element straight down to its real floor level
    draw.line([(s(16), s(10)), (s(16), s(22))], fill=COLOR, width=thin)
    draw.line([(s(11.5), s(18)), (s(16), s(23.2))], fill=COLOR, width=thin, joint="curve")
    draw.line([(s(16), s(23.2)), (s(20.5), s(18))], fill=COLOR, width=thin, joint="curve")

    # Pillow's line() draws butt caps, so round every exposed end/joint
    # explicitly with a small filled circle to match the SVG's round caps.
    def round_cap(x, y, w):
        r = w / 2.0
        draw.ellipse([s(x) - r, s(y) - r, s(x) + r, s(y) + r], fill=COLOR)

    for x, y in [(4, 9), (11, 9), (21, 9), (28, 9), (16, 10), (16, 22), (11.5, 18), (16, 23.2), (20.5, 18)]:
        round_cap(x, y, thin)

    # solid baseline = the correct floor/level
    draw.line([(s(3), s(27)), (s(29), s(27))], fill=COLOR, width=thick)
    for x, y in [(3, 27), (29, 27)]:
        round_cap(x, y, thick)

    img = img.resize((32, 32), Image.LANCZOS)
    img.save("icon.png")
    print("Wrote icon.png (32x32)")


if __name__ == "__main__":
    main()
