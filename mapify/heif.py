"""HEIC/HEIF, the iPhone's default photo format, read when a decoder is installed.

``pi-heif`` teaches Pillow to open these files. Its code is BSD-3-Clause and
its wheels bundle libheif and libde265 (both LGPL-3.0) and no encoder -- unlike
``pillow-heif``, whose wheels carry the GPL x265 encoder. It is optional rather
than a hard dependency because HEVC, the codec inside, is covered by patents
in some countries, so whether to install it is the user's call.

Without it a HEIC file is recognised by its header and refused with
instructions, rather than with "could not decode".
"""

from __future__ import annotations

import functools

# The major brand of the ISO-BMFF 'ftyp' box at the start of a HEIF file.
_BRANDS = frozenset({b"heic", b"heix", b"heim", b"heis",
                     b"hevc", b"hevx", b"mif1", b"msf1"})

MISSING = (
    "This is a HEIC photo, the iPhone's default format. Reading it needs the "
    "optional decoder (pip install pi-heif), or export the photo as JPG."
)


def looks_like_heif(data: bytes) -> bool:
    """True when the bytes start like a HEIF file, whatever the extension."""
    return data[4:8] == b"ftyp" and data[8:12] in _BRANDS


@functools.cache
def enable() -> bool:
    """Register the decoder with Pillow, once. False when it is not installed."""
    try:
        from pi_heif import register_heif_opener
    except ImportError:
        return False
    register_heif_opener()
    return True
