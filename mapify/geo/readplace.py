"""Turning text read off a sign into a place.

The case this exists for: someone photographs a building whose board carries
its name, its street, its town and its postcode, and the tool answers
"India". Everything needed was printed in the frame.

The gap was never that read text could not produce a candidate -- it always
could. It was that the only gazetteer it could consult was Natural Earth's
populated-places layer: about 7,300 settlements, no streets, no postcodes, no
buildings. A complete address matches nothing in it, so there was nothing to
put forward.

OpenStreetMap knows every one of those things, and Nominatim's ``/search``
will resolve free-form text against them. So this module does three jobs:

    address_queries()  turn OCR lines into a few strings worth asking about
    resolve()          ask, under the usage policy, within a budget
    to_candidates()    weigh what comes back by how specific it is

The weighing is the part that has to stay honest. A matched *building* is
frequently the strongest evidence in the entire photograph and should be
allowed to outrank a visual guess. A matched *country* is worth almost
nothing -- "India" was already known -- and must never be allowed to look
like an answer just because it came from text.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

from ..models.base import OcrLine, OcrResult, RawCandidate
from .nominatim import NominatimClient, SearchResult

log = logging.getLogger("mapify.readplace")

__all__ = ["ReadPlace", "Query", "address_queries",
           "looks_like_address_line", "resolve", "to_candidates",
           "SPECIFICITY_WEIGHT"]


# How much a match of each kind is worth as a *prior*, expressed as a multiple
# of the best score any visual candidate achieved.
#
# Above 1.0 means the read text outranks the model outright before any other
# evidence is applied. That is deliberate for a building or a street: a
# legible street address is better evidence of location than a neural guess
# about what the scenery looks like, and pretending otherwise is what made
# the original failure possible.
#
# It falls off a cliff below that because the failure mode is different at
# each level. A matched building is almost certainly the place. A matched
# city could be a poster, a bus destination or a shop's head office. A
# matched country is not an answer to any question the user asked.
SPECIFICITY_WEIGHT = {
    "building": 1.85,
    "street": 1.55,
    "neighbourhood": 1.15,
    "settlement": 0.80,
    "region": 0.30,
    "country": 0.10,
}

# Words that mark a line as part of an address rather than a slogan, a
# department list or an opening-hours notice. Deliberately multilingual-ish
# but Latin-script: this is a cheap filter to choose what to *ask* about, and
# a wrong guess costs one cached query, not a wrong answer.
_ADDRESS_WORDS = {
    "road", "rd", "street", "st", "avenue", "ave", "lane", "ln", "highway",
    "hwy", "marg", "salai", "nagar", "colony", "sector", "block", "phase",
    "district", "taluk", "tehsil", "mandal", "post", "po", "village",
    "town", "city", "state", "province", "county", "municipality",
    "strasse", "straße", "calle", "rua", "via", "rue", "platz", "plaza",
    "boulevard", "blvd", "drive", "cross", "main", "layout", "extension",
    "campus", "chowk", "bazar", "bazaar", "market", "junction", "opposite",
    "near", "behind", "beside",
}

# A postcode is the single most useful token on a board, because it pins a
# small area on its own. These cover the common shapes rather than every
# national scheme: six digits with an optional space (India), five digits
# (much of Europe, the US), four digits, and the UK outward+inward form.
_POSTCODE_PATTERNS = (
    re.compile(r"\b\d{3}\s?\d{3}\b"),
    re.compile(r"\b\d{5}(?:-\d{4})?\b"),
    re.compile(r"\b\d{4}\b"),
    re.compile(r"\b[A-Z]{1,2}\d[A-Z\d]?\s?\d[A-Z]{2}\b", re.IGNORECASE),
)

# Words that appear on half the institutions on earth. They must not count
# towards deciding that a search result matches what was read: "Hospital and
# Research Institute" overlaps perfectly with "Medical College Hospital and
# Research Centre" twenty kilometres away, and that near-miss is precisely
# the error this module exists to stop making.
_GENERIC = {
    "hospital", "medical", "college", "university", "institute", "research",
    "centre", "center", "academy", "school", "clinic", "education",
    "sciences", "science", "technology", "engineering", "trust", "society",
    "foundation", "public", "private", "limited", "ltd", "pvt", "company",
    "department", "government", "national", "international", "global",
    "and", "the", "for", "of",
}

# Lines that are almost always noise on a signboard.
_NOISE = re.compile(
    r"^(?:www\.|https?://|tel|ph|phone|fax|email|e-mail|contact)\b",
    re.IGNORECASE,
)


@dataclass
class ReadPlace:
    """A place resolved from text in the image, with why it was believed."""

    result: SearchResult
    query: str
    ocr_confidence: float
    weight: float
    why: str = ""
    overlap: float = 0.0
    corroborated: bool = False
    note: str = ""

    @property
    def strength(self) -> float:
        """This place's prior, as a multiple of the best visual candidate."""
        value = self.weight * min(1.0, self.ocr_confidence + 0.20)
        # Nominatim's importance separates "the" Cambridge from the other
        # eleven. A mild effect on purpose: importance measures fame, and
        # the place someone photographed is often not the famous one.
        value *= 0.85 + 0.30 * min(1.0, self.result.importance)
        return value

    @property
    def rank(self) -> float:
        return self.strength * (1.0 if self.corroborated else 0.45)

    def as_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "why": self.why,
            "ocr_confidence": round(self.ocr_confidence, 3),
            "weight": round(self.weight, 3),
            "strength": round(self.strength, 3),
            "overlap": round(self.overlap, 3),
            "corroborated": self.corroborated,
            "note": self.note,
            "specificity": self.result.specificity,
            "display_name": self.result.display_name,
            "latitude": round(self.result.latitude, 6),
            "longitude": round(self.result.longitude, 6),
            "importance": round(self.result.importance, 4),
            "extent_km": round(self.result.extent_km, 2),
            "from_cache": self.result.from_cache,
        }


def looks_like_address_line(text: str) -> bool:
    """Does this line carry location information, as opposed to being a slogan?"""
    if _NOISE.match(text.strip()):
        return False
    lowered = text.casefold()
    if any(pattern.search(text) for pattern in _POSTCODE_PATTERNS):
        return True
    words = set(re.findall(r"[a-zÀ-ɏ]+", lowered))
    return bool(words & _ADDRESS_WORDS)


def reading_order(lines: list[OcrLine]) -> list[OcrLine]:
    """Sort into reading order and join fragments that share a row.

    OCR engines return lines in whatever order suits them -- EasyOCR sorts
    by confidence -- and a signboard read out of order is unusable. The name
    stops being identifiable as the name, and "Kelambakkam" and "603 103",
    which are one line on the board, arrive separated by three other lines
    so the postcode no longer sits beside the town it belongs to.

    Boxes are normalised, so rows can be recovered: group lines whose
    vertical spans overlap, order the groups downwards, order each group
    left to right, and join each group into the single line it was.
    """
    if not all(line.box for line in lines):
        return list(lines)

    remaining = sorted(lines, key=lambda line: line.box[1])  # type: ignore[index]
    rows: list[list[OcrLine]] = []
    for line in remaining:
        _, top, _, bottom = line.box  # type: ignore[misc]
        height = max(1e-6, bottom - top)
        for row in rows:
            _, r_top, _, r_bottom = row[0].box  # type: ignore[misc]
            overlap = min(bottom, r_bottom) - max(top, r_top)
            if overlap > 0.45 * min(height, max(1e-6, r_bottom - r_top)):
                row.append(line)
                break
        else:
            rows.append([line])

    out: list[OcrLine] = []
    for row in rows:
        row.sort(key=lambda line: line.box[0])  # type: ignore[index]
        if len(row) == 1:
            out.append(row[0])
            continue
        merged = " ".join(line.text.strip() for line in row if line.text.strip())
        out.append(
            OcrLine(
                text=merged,
                confidence=sum(line.confidence for line in row) / len(row),
                box=(
                    min(line.box[0] for line in row),  # type: ignore[index]
                    min(line.box[1] for line in row),  # type: ignore[index]
                    max(line.box[2] for line in row),  # type: ignore[index]
                    max(line.box[3] for line in row),  # type: ignore[index]
                ),
            )
        )
    return out


def _clean(text: str) -> str:
    """Trim OCR debris without destroying the content."""
    text = text.replace("|", " ").replace("_", " ")
    text = re.sub(r"[^\w\s,.\-&/'À-ɏ]", " ", text, flags=re.UNICODE)
    return " ".join(text.split()).strip(" ,.-")


def _postcodes(lines: list[str]) -> list[str]:
    found: list[str] = []
    for line in lines:
        for pattern in _POSTCODE_PATTERNS:
            for match in pattern.findall(line):
                token = " ".join(str(match).split())
                if token not in found:
                    found.append(token)
    return found


def _localities(lines: list[str]) -> list[str]:
    """Words on the board that are plausibly the name of the place.

    The token sitting immediately before a postcode is the single best
    guess -- "Kelambakkam - 603 103" puts the answer right there -- and
    after that, capitalised words on lines that look like an address.
    """
    out: list[str] = []

    def push(value: str) -> None:
        value = value.strip(" ,.-")
        if (2 < len(value) <= 40 and value.casefold() not in _ADDRESS_WORDS
                and value not in out):
            out.append(value)

    for line in lines:
        for pattern in _POSTCODE_PATTERNS:
            match = pattern.search(line)
            if not match:
                continue
            before = line[: match.start()].strip(" ,.-")
            if before:
                push(before.split(",")[-1])
    for line in lines:
        if not looks_like_address_line(line):
            continue
        for part in line.split(","):
            words = part.split()
            if 1 <= len(words) <= 3 and any(w[:1].isupper() for w in words):
                push(part)
    return out[:4]


def _name_head(ordered: list[OcrLine], words: int = 3) -> str:
    """The name of the place, as a short phrase.

    Whoever made the sign already marked which words are the name: they set
    them in the largest type. So the tallest line wins -- not the first one,
    and not the most confidently read one.

    Truncating matters more than it looks. Nominatim's free-text search
    requires *every* term to match, so "Chettinad Academy of Research and
    Education" returns nothing at all while "Chettinad Hospital" returns the
    building. Fewer words, more answers.
    """
    def height(line: OcrLine) -> float:
        return (line.box[3] - line.box[1]) if line.box else 0.0

    usable = [
        line for line in ordered
        if _clean(line.text)
        and not looks_like_address_line(_clean(line.text))
        and not _NOISE.match(_clean(line.text))
    ]
    if not usable:
        usable = [line for line in ordered if _clean(line.text)]
    if not usable:
        return ""

    if any(height(line) > 0 for line in usable):
        tall = height(max(usable, key=height))
        # Everything set at nearly the same size as the tallest line belongs
        # to the same title block, kept in reading order: "CHETTINAD" alone
        # is a weaker query than "CHETTINAD HOSPITAL".
        block = [line for line in usable if height(line) >= 0.72 * tall]
    else:
        block = usable[:2]

    phrase = " ".join(_clean(line.text) for line in block)
    return " ".join(phrase.split()[:words]).strip(" ,.-")


@dataclass
class Query:
    """One way of asking, and what kind of asking it is."""

    text: str = ""
    structured: dict[str, str] = field(default_factory=dict)
    why: str = ""
    # The name the query was built around, kept separately so a result can
    # be checked for it. A result that echoes the town but not the name is
    # not the thing that was read -- it is something else in the same town.
    name: str = ""

    @property
    def label(self) -> str:
        if self.structured:
            return ", ".join(f"{k}={v}" for k, v in self.structured.items())
        return self.text


def address_queries(ocr: OcrResult, min_confidence: float = 0.30,
                    max_queries: int = 3) -> list[Query]:
    """A few ways of asking, best first.

    Ordered by what was measured against the live service rather than by
    what seemed reasonable. The whole board as one free-text string -- the
    obvious first attempt -- is the *worst* query, because Nominatim wants
    every term to match and a board carries slogans, department lists and a
    web address. A short name plus the locality is the best.
    """
    kept = [line for line in ocr.lines if line.confidence >= min_confidence]
    ordered = reading_order(kept)
    ordered = [
        line for line in ordered
        if len(_clean(line.text)) >= 2 and not _NOISE.match(_clean(line.text))
    ]
    if not ordered:
        return []

    lines = [_clean(line.text) for line in ordered]
    postcodes = _postcodes(lines)
    localities = _localities(lines)
    head = _name_head(ordered)
    queries: list[Query] = []
    seen: set[str] = set()

    def add(query: Query) -> None:
        key = query.label.casefold()
        if key and key not in seen and len(query.label) <= 200:
            seen.add(key)
            queries.append(query)

    # 1. The name, shortened, plus where it says it is. The query that
    #    found the institute in testing.
    if head and localities:
        add(Query(text=f"{head} {localities[0]}", name=head,
                  why="the name on the sign, plus the locality beside it"))
    elif head:
        add(Query(text=head, name=head, why="the name on the sign"))

    # 2. Structured. Cannot fail the way free text fails, because each
    #    field is matched against one kind of thing, and it reliably returns
    #    the settlement even when the building is not in OSM.
    if localities or postcodes:
        structured: dict[str, str] = {}
        if localities:
            structured["city"] = localities[0]
        if postcodes:
            structured["postalcode"] = postcodes[0]
        add(Query(structured=structured,
                  why="the locality and postcode, matched field by field"))

    # 3. The address lines as free text, for a board whose name is not in
    #    OpenStreetMap but whose street is.
    address_lines = [line for line in lines if looks_like_address_line(line)]
    if address_lines:
        add(Query(text=", ".join(address_lines[:3]),
                  why="the address lines"))

    # 4. Nothing looked like an address at all -- try the whole thing.
    if not queries:
        add(Query(text=", ".join(lines[:4]), why="the text on the sign"))

    return queries[:max_queries]


def _names_match(name: str, display_name: str) -> bool:
    """Does the result actually carry the name that was read?

    The locality is in the query and in every result from that locality, so
    it confirms nothing. This looks only at what is distinctive about the
    name itself.
    """
    def terms(value: str) -> set[str]:
        return {
            w for w in re.findall(r"[^\W\d_]+", value.casefold(),
                                  flags=re.UNICODE)
            if len(w) > 2 and w not in _GENERIC and w not in _ADDRESS_WORDS
        }

    wanted = terms(name)
    if not wanted:
        # The name was entirely generic, so there is nothing to insist on.
        return True
    return bool(wanted & terms(display_name))


def _overlap(query_text: str, display_name: str) -> float:
    """How much of what was read does this result actually account for?

    The check that separates the institute from the chicken stall. Searching
    "Rajiv Gandhi Salai, Kelambakkam 603103" returned a food stall on that
    road: the right street, the right town, and nothing whatsoever to do
    with the words on the sign. It came back as a building, which without
    this would have outranked everything.

    A result that shares distinctive words with the query is describing the
    thing that was read. One that shares only the street and the town is
    describing something that merely stands there.
    """
    def words(value: str, drop_generic: bool) -> set[str]:
        out = set()
        for w in re.findall(r"[^\W\d_]+", value.casefold(), flags=re.UNICODE):
            if len(w) <= 2 or w in _ADDRESS_WORDS:
                continue
            if drop_generic and w in _GENERIC:
                continue
            out.add(w)
        return out

    # Score on the distinctive words only. "Hospital and Research Institute"
    # overlaps perfectly with "Medical College Hospital and Research Centre"
    # on words that mean nothing, and that near-miss put an answer twenty
    # kilometres from the right one.
    wanted = words(query_text, drop_generic=True)
    if not wanted:
        # Nothing distinctive was read, so there is nothing to confirm. Fall
        # back to the full comparison; the caller treats a weak result as
        # neighbourhood-level evidence rather than a building.
        wanted = words(query_text, drop_generic=False)
        if not wanted:
            return 0.0
        return len(wanted & words(display_name, drop_generic=False)) / len(wanted)

    return len(wanted & words(display_name, drop_generic=True)) / len(wanted)


def resolve(ocr: OcrResult, client: NominatimClient,
            max_queries: int = 3, per_query: int = 3) -> list[ReadPlace]:
    """Ask OpenStreetMap what the text on the sign refers to.

    Stops early once something specific *and corroborated* comes back:
    asking again would spend budget confirming what is already known. A
    specific result that nothing corroborates does not stop it, because
    that is the chicken-stall case -- the right street, the wrong object.
    """
    places: list[ReadPlace] = []
    seen: set[tuple[str, int | None]] = set()

    for query in address_queries(ocr, max_queries=max_queries):
        if client.budget_remaining <= 0:
            log.info("read-place search stopped: lookup budget spent")
            break
        try:
            if query.structured:
                results = client.search_structured(query.structured,
                                                   limit=per_query)
            else:
                results = client.search(query.text, limit=per_query)
        except Exception as exc:  # pragma: no cover - network guard
            log.warning("read-place search failed: %s", exc)
            break
        if not results:
            continue

        confidence = _confidence_for(ocr, query.label)
        for result in results:
            key = (result.osm_type or "", result.osm_id)
            if key in seen:
                continue
            seen.add(key)
            places.append(_weigh(result, query, confidence))

        if any(p.corroborated and p.result.specificity in ("building", "street")
               for p in places):
            break

    # Rank, then Nominatim's own order, then the shorter name. Ties are
    # common -- a hospital, its blood bank and its campus all match the same
    # query equally well -- and the shorter name is the main entity rather
    # than a department of it.
    places.sort(key=lambda p: (-p.rank, -p.result.importance,
                               len(p.result.display_name)))
    return places


def _weigh(result: SearchResult, query: Query, confidence: float) -> ReadPlace:
    """Decide what one result is worth, and be able to say why."""
    specificity = result.specificity
    weight = SPECIFICITY_WEIGHT.get(specificity, 0.1)

    # Structured queries match field by field, so they cannot return
    # something unrelated that merely stands on the right street. There is
    # nothing for an overlap test to check, and treating an empty overlap
    # as a failure would penalise the most reliable query we have.
    if query.structured:
        overlap = 1.0
        note = f"Locality read from the sign, matched as a {specificity}"
    else:
        overlap = _overlap(query.text, result.display_name)
        note = f"Text in the image resolves to a {specificity} in OpenStreetMap"

    # Overlap alone is not enough when the query carried a name. Searching
    # "CHETTINAD HOSPITAL Kelambakkam" and getting a chicken stall in
    # Kelambakkam scores half the distinctive words -- the town is in every
    # result from that town. What confirms a match is the *name*.
    named = _names_match(query.name, result.display_name) if query.name else True
    corroborated = overlap >= 0.34 and named
    if not corroborated and specificity in ("building", "street"):
        # It is on the right road, but nothing says it is the thing that
        # was read. Demote it to what it actually evidences -- the
        # neighbourhood -- rather than throwing away a coordinate that is
        # probably in the right area.
        weight = min(weight, SPECIFICITY_WEIGHT["neighbourhood"])
        note = (
            f"On the street that was read, but the name does not match "
            f"anything on the sign -- treated as a neighbourhood, not a "
            f"{specificity}"
        )

    return ReadPlace(
        result=result,
        query=query.label,
        why=query.why,
        ocr_confidence=confidence,
        weight=weight,
        overlap=overlap,
        corroborated=corroborated,
        note=note,
    )


def _confidence_for(ocr: OcrResult, query: str) -> float:
    """How well was the text behind this query actually read?

    The mean confidence of the lines that contributed, rather than the best
    of them: a query assembled from one crisp line and three guesses is not
    as trustworthy as its crispest line.
    """
    lowered = query.casefold()
    scores = [
        line.confidence for line in ocr.lines
        if line.text.strip() and _clean(line.text).casefold() in lowered
    ]
    if not scores:
        return 0.5
    return sum(scores) / len(scores)


def to_candidates(places: list[ReadPlace], ceiling: float,
                  limit: int = 3) -> list[RawCandidate]:
    """Weigh resolved places against what the visual model managed.

    ``ceiling`` is the best score any locator candidate got. A corroborated
    building match is allowed to exceed it, because a legible address is
    better evidence of location than a guess about scenery -- that
    miscalibration is what let the original failure happen. A country match
    is not, because it answers nothing that was asked.
    """
    out: list[RawCandidate] = []
    for place in places[:limit]:
        out.append(
            RawCandidate(
                latitude=place.result.latitude,
                longitude=place.result.longitude,
                score=max(1e-9, ceiling * place.strength),
                source="read-address",
            )
        )
    return out
