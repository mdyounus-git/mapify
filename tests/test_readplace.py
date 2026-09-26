"""Reading the address off a sign.

The failure that produced this file: a photograph of a medical institute,
with the institute's own board legible in frame — name, street, town,
postcode — came back as a scatter across Tamil Nadu and Hungary and settled
on "India".

Every number and threshold here was set by running the real thing against
the real service and watching what came back, not by reasoning about what
ought to work. The tests record what was learned, including two near-misses
that looked like successes.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from mapify.geo import readplace
from mapify.geo.nominatim import SearchResult
from mapify.models.base import OcrLine, OcrResult

# The board, exactly as EasyOCR returned it: ordered by confidence rather
# than by position, and with the town and its postcode split apart.
BOARD_LINES = [
    OcrLine("Rajiv Gandhi Salai (OMR)", 0.98, (0.349, 0.389, 0.653, 0.439)),
    OcrLine("603 103", 0.97, (0.548, 0.446, 0.646, 0.491)),
    OcrLine("HOSPITAL AND RESEARCH INSTITUTE", 0.96, (0.228, 0.306, 0.773, 0.356)),
    OcrLine("CHETTINAD", 0.92, (0.343, 0.211, 0.659, 0.296)),
    OcrLine("Kelambakkam", 0.78, (0.356, 0.449, 0.533, 0.494)),
    OcrLine("Chengalpattu District, Tamil Nadu", 0.66, (0.309, 0.506, 0.693, 0.554)),
]


def board() -> OcrResult:
    return OcrResult(lines=list(BOARD_LINES), engine="easyocr")


class TestReadingOrder:
    def test_lines_come_back_in_the_order_a_person_reads_them(self):
        """EasyOCR sorts by confidence, which scrambles a signboard."""
        texts = [line.text for line in readplace.reading_order(BOARD_LINES)]
        assert texts[0] == "CHETTINAD"
        assert texts[1] == "HOSPITAL AND RESEARCH INSTITUTE"

    def test_fragments_sharing_a_row_are_rejoined(self):
        """"Kelambakkam" and "603 103" are one line on the board.

        Split apart, the postcode no longer sits beside the town it belongs
        to, and the heuristic that reads the locality from beside a postcode
        finds nothing.
        """
        texts = [line.text for line in readplace.reading_order(BOARD_LINES)]
        assert "Kelambakkam 603 103" in texts

    def test_lines_without_boxes_are_left_alone(self):
        plain = [OcrLine("one", 0.9), OcrLine("two", 0.8)]
        assert [line.text for line in readplace.reading_order(plain)] == ["one", "two"]

    def test_it_survives_an_empty_read(self):
        assert readplace.reading_order([]) == []


class TestQueries:
    def test_the_first_query_is_the_name_plus_the_locality(self):
        """Measured, not assumed.

        Against the live service, "Chettinad Hospital Kelambakkam" returns
        the building. The whole board as one string returns nothing at all,
        because Nominatim requires every term to match and a board carries
        slogans and a web address.
        """
        queries = readplace.address_queries(board())
        assert queries, "no queries built from a legible board"
        first = queries[0].text
        assert "CHETTINAD" in first
        assert "Kelambakkam" in first
        assert "RESEARCH" not in first, "the query is too long to match"

    def test_a_structured_query_is_offered_as_a_fallback(self):
        """It cannot fail the way free text fails, and always finds the town."""
        structured = [q for q in readplace.address_queries(board()) if q.structured]
        assert structured, "no structured fallback"
        fields = structured[0].structured
        assert fields.get("city") == "Kelambakkam"
        assert fields.get("postalcode", "").replace(" ", "") == "603103"

    def test_the_budget_is_never_exceeded(self):
        assert len(readplace.address_queries(board(), max_queries=3)) <= 3
        assert len(readplace.address_queries(board(), max_queries=1)) == 1

    def test_a_web_address_is_not_treated_as_a_place(self):
        noisy = board()
        noisy.lines.append(OcrLine("www.example.edu.in", 0.99, (0.3, 0.6, 0.7, 0.64)))
        for query in readplace.address_queries(noisy):
            assert "www." not in query.label

    def test_nothing_legible_produces_nothing(self):
        assert readplace.address_queries(OcrResult()) == []
        faint = OcrResult(lines=[OcrLine("something", 0.05)])
        assert readplace.address_queries(faint) == []

    def test_a_postcode_is_recognised_in_several_shapes(self):
        for text in ("Kelambakkam - 603 103", "Berlin 10115",
                     "London SW1A 1AA", "Sydney NSW 2000"):
            assert readplace.looks_like_address_line(text), text

    def test_a_one_digit_postcode_is_deliberately_not_matched(self):
        """Dublin's postal districts are one or two digits, and so is
        everything else on a signboard. A pattern loose enough to catch
        "Dublin 4" also catches "Block 4", "Gate 2" and "Phase 1", and a
        wrong locality is worse than a missing one -- it sends the query
        somewhere else entirely.
        """
        assert not readplace.looks_like_address_line("Dublin 4")
        # It is still recognised by the address vocabulary when the line
        # carries one, which is the usual case.
        assert readplace.looks_like_address_line("Grafton Street, Dublin 4")


class TestOverlapRejectsNearMisses:
    """The check that separates the right hospital from a similar one.

    Two real near-misses, both of which scored as confident building matches
    before this existed:

      * "Rajiv Gandhi Salai, Kelambakkam 603103" returned a chicken and
        mutton stall on that road — right street, right town, nothing to do
        with the sign;
      * "HOSPITAL AND RESEARCH Chengalpattu District" returned SRM Medical
        College Hospital and Research Centre, twenty kilometres away, on a
        perfect overlap of words that every hospital on earth shares.
    """

    QUERY = "CHETTINAD HOSPITAL Kelambakkam"

    def test_the_right_building_scores_high(self):
        score = readplace._overlap(
            self.QUERY,
            "Chettinad Hospital, Veeranam Road, Padur, Kelambakkam, Tamil Nadu",
        )
        assert score >= 0.9

    def test_a_different_hospital_in_the_same_district_scores_zero(self):
        score = readplace._overlap(
            self.QUERY,
            "Srm Medical College Hospital And Research Centre, Potheri, "
            "Chengalpattu, Tamil Nadu",
        )
        assert score == 0.0, "generic words alone must not confirm a match"

    def test_matching_only_the_town_does_not_confirm_a_building(self):
        """The chicken stall scores half the distinctive words, on the town.

        Overlap alone cannot reject this and should not try: "Kelambakkam"
        genuinely is in both strings. What rejects it is that the *name*
        read off the sign appears nowhere in the result. Every result from
        that town echoes the town; only the right one echoes the name.
        """
        stall = ("Tamil Nadu Jeeva Chicken and mutton stall, "
                 "Rajiv Gandhi Salai, Padur, Kelambakkam")
        assert readplace._overlap(self.QUERY, stall) >= 0.34
        assert readplace._names_match("CHETTINAD", stall) is False
        assert readplace._names_match(
            "CHETTINAD",
            "Chettinad Hospital, Veeranam Road, Padur, Kelambakkam",
        ) is True


def _result(name: str, lat: float, lon: float, rank: int,
            importance: float = 0.1) -> SearchResult:
    return SearchResult(
        display_name=name, latitude=lat, longitude=lon,
        place_rank=rank, importance=importance,
        bounding_box=(lat - 0.01, lat + 0.01, lon - 0.01, lon + 0.01),
    )


@dataclass
class FakeClient:
    """A Nominatim that answers from a script and counts what it is asked."""

    free_text: dict[str, list[SearchResult]]
    structured: list[SearchResult]
    max_lookups: int = 6

    def __post_init__(self):
        self.calls: list[str] = []
        self.last_error = None

    @property
    def budget_remaining(self) -> int:
        return max(0, self.max_lookups - len(self.calls))

    def search(self, query, limit=3):
        self.calls.append(query)
        for needle, results in self.free_text.items():
            if needle.casefold() in query.casefold():
                return results[:limit]
        return []

    def search_structured(self, fields, limit=3):
        self.calls.append(", ".join(f"{k}={v}" for k, v in fields.items()))
        return self.structured[:limit]


class TestResolve:
    def test_a_matched_building_stops_the_search(self):
        """Confirming what is already known is not worth another request."""
        client = FakeClient(
            free_text={"CHETTINAD": [
                _result("Chettinad Hospital, Padur, Kelambakkam, Tamil Nadu",
                        12.7972, 80.2176, rank=30),
            ]},
            structured=[],
        )
        places = readplace.resolve(board(), client)
        assert len(client.calls) == 1, f"spent {len(client.calls)} requests"
        assert places[0].result.specificity == "building"
        assert places[0].corroborated

    def test_it_stops_when_the_budget_is_gone(self):
        client = FakeClient(free_text={}, structured=[], max_lookups=1)
        readplace.resolve(board(), client)
        assert len(client.calls) <= 1

    def test_an_uncorroborated_building_is_demoted_not_trusted(self):
        """The chicken stall. Right street, wrong object, so: neighbourhood."""
        client = FakeClient(
            free_text={"CHETTINAD": [
                _result("Jeeva Chicken and mutton stall, Rajiv Gandhi Salai, "
                        "Padur", 12.8054, 80.2240, rank=30),
            ]},
            structured=[],
        )
        places = readplace.resolve(board(), client)
        assert places[0].corroborated is False
        assert places[0].weight <= readplace.SPECIFICITY_WEIGHT["neighbourhood"]
        assert "does not match" in places[0].note

    def test_a_structured_result_needs_no_overlap(self):
        """Field-by-field matching cannot return something unrelated."""
        client = FakeClient(
            free_text={},
            structured=[_result("Kelambakkam, Chengalpattu, Tamil Nadu, India",
                                12.787, 80.220, rank=18, importance=0.449)],
        )
        places = readplace.resolve(board(), client)
        assert places, "structured fallback produced nothing"
        assert places[0].corroborated is True
        assert places[0].result.specificity == "settlement"

    def test_nothing_found_is_not_an_error(self):
        client = FakeClient(free_text={}, structured=[])
        assert readplace.resolve(board(), client) == []

    def test_a_failing_service_does_not_break_the_analysis(self):
        class Broken(FakeClient):
            def search(self, query, limit=3):
                raise RuntimeError("connection reset")

        client = Broken(free_text={}, structured=[])
        assert readplace.resolve(board(), client) == []


class TestWeighting:
    def test_a_read_building_may_outrank_the_visual_model(self):
        """The miscalibration that allowed the original failure.

        A legible street address is better evidence of where a photograph
        was taken than a neural guess about what the scenery resembles. The
        injected candidate has to be able to say so.
        """
        place = readplace.ReadPlace(
            result=_result("Chettinad Hospital", 12.8, 80.2, rank=30),
            query="q", ocr_confidence=0.9, weight=readplace.SPECIFICITY_WEIGHT["building"],
            corroborated=True, overlap=1.0,
        )
        [candidate] = readplace.to_candidates([place], ceiling=0.2)
        assert candidate.score > 0.2
        assert candidate.source == "read-address"

    def test_a_read_country_may_not(self):
        """"India" was already known and answers nothing that was asked."""
        place = readplace.ReadPlace(
            result=_result("India", 22.0, 79.0, rank=4, importance=0.9),
            query="q", ocr_confidence=0.9,
            weight=readplace.SPECIFICITY_WEIGHT["country"],
            corroborated=True, overlap=1.0,
        )
        [candidate] = readplace.to_candidates([place], ceiling=0.2)
        assert candidate.score < 0.2

    def test_specificity_weights_are_ordered(self):
        weights = readplace.SPECIFICITY_WEIGHT
        order = ["building", "street", "neighbourhood", "settlement",
                 "region", "country"]
        values = [weights[k] for k in order]
        assert values == sorted(values, reverse=True)

    def test_an_uncorroborated_place_ranks_below_a_corroborated_one(self):
        def make(corroborated: bool) -> readplace.ReadPlace:
            return readplace.ReadPlace(
                result=_result("X", 1.0, 1.0, rank=30),
                query="q", ocr_confidence=0.9,
                weight=readplace.SPECIFICITY_WEIGHT["building"],
                corroborated=corroborated,
            )

        assert make(False).rank < make(True).rank


class TestSpecificity:
    @pytest.mark.parametrize("rank,expected", [
        (30, "building"), (26, "street"), (19, "neighbourhood"),
        (16, "settlement"), (8, "region"), (4, "country"),
    ])
    def test_place_rank_maps_to_words_a_person_can_read(self, rank, expected):
        assert _result("x", 0, 0, rank).specificity == expected

    def test_extent_is_measured_from_the_bounding_box(self):
        india = SearchResult(display_name="India", latitude=22, longitude=79,
                             place_rank=4,
                             bounding_box=(6.0, 35.5, 68.0, 97.4))
        assert india.extent_km > 3000
