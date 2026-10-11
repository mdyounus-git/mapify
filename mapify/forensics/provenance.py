"""What a file declares about its own making: C2PA, IPTC, generator tags.

The only reliable signal of AI generation available to a local tool is
the one the generator chose to leave. Many do: OpenAI, Adobe Firefly and
Google embed C2PA Content Credentials; IPTC's DigitalSourceType has
values for "trainedAlgorithmicMedia"; Stable Diffusion front-ends write
their generation parameters into PNG text chunks.

This reads those declarations and reports them. It deliberately does
not run a pixel-level "AI detector". Those are unreliable and
asymmetric: they miss new generators, flag real photographs, and a
false "this is real" is worse than no answer. So absence of a
declaration is reported as exactly that -- absence, which proves
nothing -- and never as evidence the image is a photograph.
"""

from __future__ import annotations

import re
import struct
from dataclasses import dataclass, field
from typing import Any

__all__ = ["Provenance", "read_provenance"]

_AI_SOURCE_TYPES = ("trainedalgorithmicmedia",
                    "compositewithtrainedalgorithmicmedia",
                    "algorithmicmedia")
_GENERATOR_WORDS = ("dall-e", "dall·e", "dalle", "midjourney", "stable diffusion",
                    "stablediffusion", "firefly", "imagen", "gemini",
                    "novelai", "comfyui", "automatic1111", "invokeai",
                    "leonardo.ai", "ideogram", "flux")
_PNG_GEN_KEYS = ("parameters", "prompt", "workflow", "sd-metadata",
                 "invokeai_metadata", "dream")


@dataclass
class Provenance:
    c2pa: bool = False
    ai_declared: bool = False
    generator: str | None = None
    source_type: str | None = None
    evidence: list[str] = field(default_factory=list)
    # What the C2PA manifest itself says, when c2pa-python is installed.
    c2pa_details: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"c2pa_manifest": self.c2pa, "ai_declared": self.ai_declared,
                "generator": self.generator, "digital_source_type": self.source_type,
                "c2pa": self.c2pa_details, "evidence": self.evidence}


def _read_c2pa(data: bytes) -> dict[str, Any] | None:
    """The active C2PA manifest, read and its signature checked by the
    Content Authenticity Initiative's own library (c2pa-python, MIT or
    Apache-2.0), when it is installed. A manifest a stranger signed proves
    who signed it, not that the photograph is true; the validation state and
    the signer are reported for exactly that reason."""
    try:
        import io
        import json

        import c2pa
    except Exception:              # not installed, or its native library missing
        return None
    if data[:3] == b"\xff\xd8\xff":
        mime = "image/jpeg"
    elif data[:8] == b"\x89PNG\r\n\x1a\n":
        mime = "image/png"
    elif data[8:12] == b"WEBP":
        mime = "image/webp"
    else:
        return None
    try:
        with c2pa.Reader(mime, io.BytesIO(data)) as reader:
            store = json.loads(reader.json())
            state = reader.get_validation_state()
    except Exception:
        return None
    active = (store.get("manifests") or {}).get(store.get("active_manifest") or "", {})
    if not active:
        return None
    generator = active.get("claim_generator")
    if not generator:
        info = active.get("claim_generator_info") or [{}]
        generator = " ".join(str(info[0].get(k, "")) for k in ("name", "version")).strip()
    actions, source = [], None
    for assertion in active.get("assertions") or []:
        if str(assertion.get("label", "")).startswith("c2pa.actions"):
            for act in (assertion.get("data") or {}).get("actions") or []:
                actions.append(act.get("action"))
                source = source or act.get("digitalSourceType")
    return {"generator": generator or None, "title": active.get("title"),
            "actions": [a for a in actions if a], "digital_source_type": source,
            "signer": (active.get("signature_info") or {}).get("issuer"),
            "validation": str(state) if state is not None else None}


def _png_texts(data: bytes) -> dict[str, str]:
    out: dict[str, str] = {}
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        return out
    pos = 8
    while pos + 8 <= len(data):
        n = struct.unpack(">I", data[pos:pos + 4])[0]
        kind = data[pos + 4:pos + 8]
        body = data[pos + 8:pos + 8 + n]
        if kind in (b"tEXt", b"iTXt", b"zTXt"):
            key, _, rest = body.partition(b"\x00")
            text = rest
            if kind == b"zTXt" and rest[:1] == b"\x00":
                import zlib
                try:
                    text = zlib.decompress(rest[1:])
                except zlib.error:
                    text = b""
            elif kind == b"iTXt":
                parts = rest.split(b"\x00", 3)
                text = parts[-1] if parts else b""
            out[key.decode("latin-1", "replace").lower()] = \
                text.decode("utf-8", "replace")[:2000]
        if kind == b"caBX":
            out["__c2pa__"] = "caBX chunk"
        if kind == b"IEND":
            break
        pos += 12 + n
    return out


def read_provenance(data: bytes, exif: Any = None) -> Provenance:
    out = Provenance()
    head = data[: min(len(data), 4 * 1024 * 1024)]
    low = head.lower()

    # C2PA: a JUMBF box labelled "c2pa", in a JPEG APP11 segment or a PNG
    # caBX chunk.
    if b"c2pa" in low and (b"jumb" in low or b"jumd" in low):
        out.c2pa = True
        out.evidence.append("a C2PA Content Credentials manifest is embedded")

    # IPTC DigitalSourceType in the XMP packet.
    m = re.search(rb"digitalsourcetype[^a-z]{0,40}([a-z/:.]*?(trainedalgorithmicmedia"
                  rb"|compositewithtrainedalgorithmicmedia|algorithmicmedia))", low)
    if m:
        out.source_type = m.group(2).decode()
        out.ai_declared = True
        out.evidence.append(f"IPTC DigitalSourceType says {out.source_type}")

    texts = _png_texts(data)
    if "__c2pa__" in texts:
        out.c2pa = True
        out.evidence.append("a C2PA manifest chunk (caBX) is present")

    if out.c2pa:
        details = _read_c2pa(data)
        if details:
            out.c2pa_details = details
            said = [f"made with {details['generator']}" if details["generator"] else "",
                    f"signed by {details['signer']}" if details["signer"] else "",
                    f"signature {details['validation'].lower()}"
                    if details["validation"] else ""]
            out.evidence.append("the C2PA manifest says: "
                                + "; ".join(x for x in said if x))
            kind = (details["digital_source_type"] or "").rsplit("/", 1)[-1].lower()
            if kind in _AI_SOURCE_TYPES:
                out.ai_declared = True
                out.source_type = out.source_type or kind
                out.evidence.append(f"its actions declare the source as {kind}")
    for key in _PNG_GEN_KEYS:
        if key in texts:
            out.ai_declared = True
            out.evidence.append(f"a PNG text chunk '{key}' holds generation "
                                f"settings")
            break

    fields = []
    for attr in ("software",):
        v = getattr(exif, attr, None) if exif is not None else None
        if v:
            fields.append(str(v))
    other = (getattr(exif, "other", {}) or {}) if exif is not None else {}
    fields += [str(other.get(k, "")) for k in ("ImageDescription", "Artist")]
    fields += list(texts.values())
    blob = " ".join(fields).lower()
    xmp_hit = None
    for word in _GENERATOR_WORDS:
        # As a whole word only: "imagen" is inside the camera tag
        # aux:ImageNumber, which Adobe writes into ordinary edited photographs,
        # and was declaring them generated.
        text_pat = re.compile(r"(?<![a-z0-9])" + re.escape(word) + r"(?![a-z0-9])")
        byte_pat = re.compile(rb"(?<![a-z0-9])" + re.escape(word.encode())
                              + rb"(?![a-z0-9])")
        if text_pat.search(blob) or byte_pat.search(low):
            xmp_hit = word
            break
    if xmp_hit:
        out.generator = xmp_hit
        out.ai_declared = True
        out.evidence.append(f"the file names a generator: {xmp_hit}")
    return out
