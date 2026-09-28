"""Is this a photograph of a place at all?

Every other stage of the pipeline assumes the answer is yes. GeoCLIP is
a softmax over a gallery of 100,000 GPS points, and a softmax sums to
one, so it cannot decline: hand it a poster about cyber-security and it
returns ranked coordinates with the same composure as it does for a
photograph of a street. Measured on drawn security posters, the locator
committed 15-30% of its probability to its shortlist -- more than the
threshold for a *high*-confidence call -- about an image of nowhere.

So the question has to be asked separately, before any coordinate is
allowed out, and it has to be asked of evidence the locator did not
produce. Three signals, chosen because each fails differently:

  image kind     the zero-shot reading: photograph of a street, an
                 interior, an object -- or a poster, a chart, a slide, a
                 screenshot. Strong, but fooled by designed-looking
                 photographs and by posters that contain a photograph.

  pixels         flat fills and small palettes, measured directly. Fooled
                 by other things: a white wall, a heavily compressed sky.

  text coverage  how much of the frame is recognised text. A street with
                 signs is a few percent; a slide is most of it.

Where they agree, the answer is withheld: no coordinate, no candidates,
no pin on the globe. Not a low-confidence answer -- none. An answer
about nowhere is worse than an inaccurate one, because an inaccurate
answer is at least about somewhere.

The thresholds are not intuition. They were fitted by
``scripts/fit_locatability.py`` on images that are and are not
photographs of places, under one hard constraint: **none** of the 163
benchmark photographs may be refused. A gate that swallows real
photographs has broken the tool to fix a poster.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .evidence import prompts as P

__all__ = ["Locatability", "LocatabilityRule", "assess", "DEFAULT_RULE"]


@dataclass(frozen=True)
class LocatabilityRule:
    """The fitted thresholds. See the module docstring for their origin.

    Nothing is refused on the zero-shot reading alone. That was the first
    design, and measurement killed it: a choir photographed in a tent in
    Harare read as 91% non-photograph, a conference in Quito 91%, a
    protester's cardboard sign in Cairo 90%. What separates those from a
    poster is the pixels -- 5 to 18% flat colour against 40 to 77% for the
    drawn images -- so a refusal needs both.
    """

    # Fitted by scripts/fit_locatability.py on 159 photographs of places,
    # 87 non-photographs and 45 photographs of objects and interiors, then
    # moved to the conservative side of the fit: the aggressive optimum
    # refused 8 more non-photographs but let five real photographs pass
    # the pixel test, one of them 0.27 from refusal on the reading. These
    # values let one real photograph pass it, 0.68 away. Result: no
    # photograph of a place refused, no photograph of an object refused,
    # 59 of 87 non-photographs refused, all 23 drawn security posters.
    #
    # The reading must say "not a photograph" at least this strongly...
    weak_not_photo: float = 0.80
    # ...and the pixels must look drawn, by one of two routes. Flat
    # mid-tone fills from a small palette -- a poster, a cartoon:
    drawn_flat: float = 0.35
    drawn_palette: float = 0.60
    # -- or a page: this much pure white, with flat fill and a small
    # palette. Charts, slides, documents and diagrams sit on white, which
    # the mid-tone route deliberately ignores, because a black night sky
    # or a blown-out white window is flat in a photograph too.
    drawn_white: float = 0.40
    drawn_white_flat: float = 0.35
    # Above this reading an image is doubtful even when the pixels do not
    # confirm it: not refused -- the candidates are still listed -- but no
    # location is given. The most poster-like photograph of a place in
    # the benchmark reads 0.912; this stays clear of it, and withholds the
    # location from 14 of the 17 non-photographs that got past the refusal
    # with a location shown. At 0.90 it would have withheld a correct
    # 4.2 km answer for a street photograph in Cairo.
    doubtful_not_photo: float = 0.95
    # Reported alongside, never decisive on its own: a street full of
    # signs can be mostly text, as the Cairo photograph is.
    texty_coverage: float = 0.12
    # A file naming the camera that took it is a photograph of something;
    # it needs a surer reading. A phone photograph of a page filling the
    # frame is still not of a place.
    camera_not_photo: float = 0.90
    # The object-close-up verdict: a real photograph with nothing around
    # the subject to place it by.
    no_scenery: float = 0.60
    place_floor: float = 0.25


DEFAULT_RULE = LocatabilityRule()


@dataclass
class Locatability:
    # "place" | "no_scenery" | "doubtful" | "not_a_photo"
    verdict: str
    kind: str = ""               # the most likely image kind
    kind_label: str = ""
    kind_confidence: float = 0.0
    p_not_photo: float = 0.0
    p_no_scenery: float = 0.0
    p_place: float = 0.0
    reasons: list[str] = field(default_factory=list)
    signals: dict[str, Any] = field(default_factory=dict)

    @property
    def locatable(self) -> bool:
        """May candidates exist at all? False only for a refusal."""
        return self.verdict != "not_a_photo"

    @property
    def withhold(self) -> bool:
        """Must the location be withheld, even if candidates are listed?

        A close-up of an object is withheld too. Measured: at the fitted
        thresholds no photograph of a place reads as one, and 8 of the 29
        object photographs that were being given a location are not. The
        nearest real photograph (0.58, a street in Copenhagen) was 888 km
        wrong, so withholding at 0.60 costs no answer that was right.
        """
        return self.verdict in ("not_a_photo", "doubtful", "no_scenery")

    def headline(self) -> str:
        if self.verdict == "not_a_photo":
            return (f"This is not a photograph of a place. It reads as "
                    f"{_article(self.kind_label.lower())}.")
        if self.verdict == "doubtful":
            return (f"This may not be a photograph of a place: it reads as "
                    f"{_article(self.kind_label.lower())} at "
                    f"{self.p_not_photo:.0%}. No location is given.")
        if self.verdict == "no_scenery":
            return ("This is a photograph of an object, with nothing around "
                    "it to place it by. No location is given.")
        return "A photograph of a place."

    def as_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict,
            "locatable": self.locatable,
            "kind": self.kind,
            "kind_label": self.kind_label,
            "kind_confidence": round(self.kind_confidence, 4),
            "p_not_photo": round(self.p_not_photo, 4),
            "p_no_scenery": round(self.p_no_scenery, 4),
            "p_place": round(self.p_place, 4),
            "withhold": self.withhold,
            "headline": self.headline(),
            "reasons": self.reasons,
            "signals": self.signals,
        }


def _article(noun: str) -> str:
    return ("an " if noun[:1] in "aeiou" else "a ") + noun


def _labels() -> dict[str, str]:
    group = P.GROUPS_BY_KEY["image_kind"]
    return {o.key: o.label for o in group.options}


def assess(kinds: dict[str, float], graphic: Any = None,
           text_cov: float = 0.0, exif: Any = None,
           rule: LocatabilityRule = DEFAULT_RULE) -> Locatability:
    """Decide whether the question "where was this taken" applies.

    ``kinds`` is the full image-kind distribution (every option, not a
    top five). With no reading at all -- the encoder failed -- the image
    is treated as a place: withholding an answer needs evidence, and an
    absent reading is not evidence of anything.
    """
    if not kinds:
        return Locatability(verdict="place",
                            reasons=["no image-kind reading was available"])

    labels = _labels()
    p_np = sum(kinds.get(k, 0.0) for k in P.KIND_NOT_A_PHOTO)
    p_obj = sum(kinds.get(k, 0.0) for k in P.KIND_NO_SCENERY)
    p_place = sum(kinds.get(k, 0.0) for k in P.KIND_PLACE)
    top = max(kinds.items(), key=lambda kv: kv[1])

    # Mid-tone flatness when the stats carry it: black and white are flat
    # in photographs too (see GraphicStats.flat_mid_fraction).
    flat = getattr(graphic, "flat_mid_fraction", None)
    if flat is None:
        flat = getattr(graphic, "flat_fraction", 0.0)
    flat = float(flat or 0.0)
    palette = float(getattr(graphic, "palette_share", 0.0) or 0.0)
    white = float(getattr(graphic, "white_fraction", 0.0) or 0.0)
    flat_all = float(getattr(graphic, "flat_fraction", 0.0) or 0.0)
    drawn_fill = flat >= rule.drawn_flat and palette >= rule.drawn_palette
    drawn_page = (white >= rule.drawn_white and flat_all >= rule.drawn_white_flat
                  and palette >= rule.drawn_palette)
    drawn = drawn_fill or drawn_page
    texty = text_cov >= rule.texty_coverage
    camera = " ".join(x for x in (getattr(exif, "camera_make", None),
                                  getattr(exif, "camera_model", None)) if x)

    out = Locatability(
        verdict="place", kind=top[0], kind_label=labels.get(top[0], top[0]),
        kind_confidence=top[1], p_not_photo=p_np, p_no_scenery=p_obj,
        p_place=p_place,
        signals={"flat_mid_fraction": round(flat, 4),
                 "flat_fraction": round(flat_all, 4),
                 "white_fraction": round(white, 4),
                 "palette_share": round(palette, 4),
                 "text_coverage": round(text_cov, 4),
                 "drawn": drawn, "texty": texty,
                 "camera": camera or None},
    )

    # Not a photograph: the reading and the pixels have to agree. Either
    # alone refuses real photographs (see LocatabilityRule).
    needed = rule.camera_not_photo if camera else rule.weak_not_photo
    refuse = drawn and p_np >= needed
    if refuse:
        out.reasons.append(
            f"the image reads as {_article(out.kind_label.lower())} "
            f"(non-photograph at {p_np:.0%})")
        if drawn_fill:
            out.reasons.append(
                f"{flat:.0%} of it is perfectly flat colour and eight colours "
                f"cover {palette:.0%} of it, which a camera sensor does not "
                f"produce")
        else:
            out.reasons.append(
                f"{white:.0%} of it is pure white and {flat_all:.0%} perfectly "
                f"flat, from a palette of a few colours: a page, not a scene")
        if camera:
            out.reasons.append(
                f"the file names a camera ({camera}), so this is likely a "
                f"photograph of a page or a screen rather than of a place")
        if texty:
            out.reasons.append(
                f"recognised text covers {text_cov:.0%} of the frame")

    if refuse:
        out.verdict = "not_a_photo"
        return out

    # Doubtful: the reading alone is very sure, the pixels do not agree.
    # Not refused, because photographs do reach 0.9 -- a choir in a tent,
    # a conference hall -- but at this height no location is claimed.
    if p_np >= rule.doubtful_not_photo and not camera:
        out.verdict = "doubtful"
        out.reasons.append(
            f"the image reads as {_article(out.kind_label.lower())} "
            f"(non-photograph at {p_np:.0%}), though its pixels do not look "
            f"drawn; the candidates are listed as what the model considered, "
            f"not as a location")
        return out

    if p_obj >= rule.no_scenery and p_place < rule.place_floor:
        out.verdict = "no_scenery"
        out.reasons.append(
            f"the image reads as a close-up of an object at {p_obj:.0%}, "
            f"and as a scene at only {p_place:.0%}")
        return out

    out.reasons.append(
        f"the image reads as a photograph of a scene at {p_place:.0%}")
    return out
