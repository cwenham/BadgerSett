#!/usr/bin/env python3
"""Turn a photo into a 1-bit PNG the Badger 2040 can draw.

    python3 tools/make_photo.py me.jpg
    python3 tools/make_photo.py me.jpg --size 96x128 --contrast 1.4 --preview

The badge panel is pure black and white — no greys. A photo that looks
fine on screen usually turns to mud, so this crops to the badge's aspect
ratio, stretches the contrast, and applies Floyd-Steinberg dithering,
which is what makes a face still read as a face at 96 pixels wide.

Requires Pillow:  pip3 install Pillow
"""

import argparse
import os
import sys

try:
    from PIL import Image, ImageEnhance, ImageOps
except ImportError:
    sys.exit("Pillow is needed: pip3 install Pillow")

PANEL = (296, 128)


def parse_size(text):
    try:
        w, h = (int(part) for part in text.lower().split("x"))
    except ValueError:
        raise argparse.ArgumentTypeError("size must look like 96x128")
    if w > PANEL[0] or h > PANEL[1]:
        raise argparse.ArgumentTypeError(
            "%dx%d is bigger than the %dx%d panel" % (w, h, PANEL[0], PANEL[1]))
    return w, h


def parse_centering(text):
    try:
        x, y = (float(p) for p in text.split(","))
    except ValueError:
        raise argparse.ArgumentTypeError("centering must look like 0.5,0.35")
    if not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0):
        raise argparse.ArgumentTypeError("centering values must be between 0 and 1")
    return x, y


def build(path, size, contrast, brightness, invert, equalize, dither,
          centering=(0.5, 0.35), autocontrast=True):
    image = Image.open(path)
    if image.mode in ("RGBA", "LA", "P"):
        # Flatten transparency onto white, or it dithers into noise.
        image = image.convert("RGBA")
        backdrop = Image.new("RGBA", image.size, (255, 255, 255, 255))
        image = Image.alpha_composite(backdrop, image)
    image = image.convert("L")

    image = ImageOps.exif_transpose(image)
    image = ImageOps.fit(image, size, method=Image.LANCZOS, centering=centering)

    if equalize:
        image = ImageOps.equalize(image)
    elif autocontrast:
        image = ImageOps.autocontrast(image, cutoff=2)
    if contrast != 1.0:
        image = ImageEnhance.Contrast(image).enhance(contrast)
    if brightness != 1.0:
        image = ImageEnhance.Brightness(image).enhance(brightness)
    if invert:
        image = ImageOps.invert(image)

    return image.convert("1", dither=Image.FLOYDSTEINBERG if dither else Image.NONE)


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("source", help="input image (jpg, png, heic via Pillow plugins...)")
    parser.add_argument("-o", "--output", default="photo.png")
    parser.add_argument("--size", type=parse_size, default=(96, 128),
                        help="WxH, default 96x128 (the badge view's photo slot)")
    parser.add_argument("--centering", type=parse_centering, default=(0.5, 0.35),
                        metavar="X,Y",
                        help="crop bias, 0,0 = top-left. Default 0.5,0.35 keeps a "
                             "normal portrait's eyes high. Raise Y (e.g. 0.5,0.6) "
                             "if the crop is cutting off the chin")
    parser.add_argument("--contrast", type=float, default=1.35)
    parser.add_argument("--no-autocontrast", dest="autocontrast", action="store_false",
                        help="skip the automatic level stretch — use when faces come "
                             "out as a blown-white forehead and black eye sockets")
    parser.add_argument("--brightness", type=float, default=1.0)
    parser.add_argument("--equalize", action="store_true",
                        help="histogram equalisation: rescues flat, murky photos")
    parser.add_argument("--invert", action="store_true")
    parser.add_argument("--no-dither", dest="dither", action="store_false",
                        help="hard threshold instead — good for logos and line art")
    parser.add_argument("--preview", action="store_true",
                        help="also write a 4x scaled preview you can actually see")
    args = parser.parse_args()

    if not os.path.exists(args.source):
        sys.exit("no such file: %s" % args.source)

    source_size = Image.open(args.source).size
    if min(source_size) < 200:
        print("note: source is only %dx%d. There is very little detail to dither;"
              % source_size)
        print("      a photo at least 600px tall will read far better on the badge.")

    image = build(args.source, args.size, args.contrast, args.brightness,
                  args.invert, args.equalize, args.dither,
                  args.centering, args.autocontrast)
    image.save(args.output, "PNG", optimize=True, bits=1)

    size = os.path.getsize(args.output)
    print("wrote %s  %dx%d  %d bytes" % (args.output, image.width, image.height, size))
    if size > 20000:
        print("warning: that is large for the badge's flash; try a smaller --size")

    if args.preview:
        preview = args.output.rsplit(".", 1)[0] + "_preview.png"
        image.resize((image.width * 4, image.height * 4), Image.NEAREST).save(preview)
        print("wrote %s (4x, for checking it reads well)" % preview)

    print("\nNow set PHOTO = \"%s\" in config.py and upload it alongside main.py."
          % os.path.basename(args.output))


if __name__ == "__main__":
    main()
