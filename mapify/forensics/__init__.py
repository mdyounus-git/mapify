"""Questions about the image file itself, rather than about the place.

Before a photograph can say where it was taken it has to be worth
believing. These modules look at the artefact: its pixels, its
compression history, and the ways it disagrees with its own metadata.

Nothing here proves an image is genuine. That is not a thing pixel
analysis can do, and a tool that claimed it would be lying. What it can
do is notice specific, nameable disagreements -- a file whose EXIF says
one size and whose pixels say another, a JPEG saved at a quality no
camera uses -- and put them in front of a reader who can weigh them.
"""

from .pixels import PixelReport, inspect_pixels

__all__ = ["PixelReport", "inspect_pixels"]
