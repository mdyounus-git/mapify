"""The exported HTML report.

A report leaves the application and gets sent to someone who was not there
when it was produced. Three properties have to survive that journey, and they
are what this file tests:

  * it opens with no network, forever;
  * an uncertain result says so as loudly as a certain one states its place;
  * the OpenStreetMap attribution travels with the data it describes, because
    ODbL says it must.
"""

from __future__ import annotations

import re

import pytest

from conftest import make_jpeg
from mapify.report import build_batch, build_single, locator_svg

ATTRIBUTION = [
    "Country, region and gazetteer data: Natural Earth (public domain)",
    "Geolocation model: GeoCLIP (MIT) on OpenAI CLIP ViT-L/14 (MIT)",
    "Reverse geocoding: Nominatim / OpenStreetMap contributors, ODbL",
]


def confident_report() -> dict:
    return {
        "ok": True,
        "elapsed_s": 2.7,
        "image": {"filename": "photo.jpg", "format": "JPEG",
                  "width": 1600, "height": 1200, "bytes": 402_000},
        "exif": {"has_exif": True, "camera_make": "Apple",
                 "camera_model": "iPhone 15", "trust_warnings": []},
        "prediction": {
            "confidence": "high",
            "confidence_reason": "The locator put 31% of its distribution into "
                                 "a shortlist that agrees to within 4 km.",
            "determinate": True,
            "uncertainty_km": 4.2,
            "diagnostics": {"locator_mass": 0.31, "concentration": 1.0,
                            "dispersion_km": 3.9, "evidence_support": 0.82},
            "estimated": {
                "latitude": 48.858377, "longitude": 2.294569,
                "probability": 1.0,
                "place": {"city": "Paris", "region": "Paris",
                          "country": "France", "country_code": "FR",
                          "postcode": "75007", "source": "nominatim"},
            },
            "embedded_gps": None,
            "exif_vs_estimate": None,
            "notes": [],
        },
        "candidates": [{
            "latitude": 48.858377, "longitude": 2.294569, "probability": 1.0,
            "location": {"country": {"name": "France"},
                         "nearest_places": [{"name": "Paris"}]},
            "timezone": {"iana_name": "Europe/Paris", "utc_offset_hours": 2},
            "adjustments": [
                {"source": "country_prior", "detail": "CLIP favours France",
                 "delta": 0.48, "agreement": 0.9, "direction": "supports"},
            ],
        }],
        "evidence": {"readings": [
            {"label": "Western Europe", "title": "World region",
             "confidence": 0.69, "called": True, "runners_up": []},
        ]},
        "ocr": {"line_count": 0, "text": ""},
        "toponyms": [],
        "warnings": [],
        "attribution": ATTRIBUTION,
    }


def uncertain_report() -> dict:
    r = confident_report()
    r["prediction"] = {
        "confidence": "insufficient",
        "confidence_reason": "Candidates are scattered over 6,003 km, which is "
                             "most of a hemisphere. The image cannot be placed.",
        "determinate": False,
        "uncertainty_km": 6003.0,
        "diagnostics": {"locator_mass": 0.019, "concentration": 0.19,
                        "dispersion_km": 6003.0, "evidence_support": 0.1},
        "estimated": None,
        "embedded_gps": None,
        "exif_vs_estimate": None,
        "notes": ["No location is reported. The candidates below are what the "
                  "model produced, not a finding."],
    }
    return r


class TestSelfContained:
    @staticmethod
    def _fetchable(html: str) -> str:
        """The document minus the one URL that is never fetched.

        xmlns="http://www.w3.org/2000/svg" is a namespace identifier, not an
        address; no browser resolves it. Stripping it keeps the check honest
        about what would actually cause a network request.
        """
        return html.replace('xmlns="http://www.w3.org/2000/svg"', "")

    def test_nothing_in_it_would_cause_a_network_request(self):
        """It has to open on a machine with no network, years from now."""
        html = self._fetchable(build_single(confident_report(), make_jpeg()))
        for pattern in ("http://", "https://", "//cdn", "<script", "@import",
                        "url(http"):
            assert pattern not in html, f"report reaches outside for {pattern!r}"

    def test_no_src_or_href_except_inline_data(self):
        html = build_single(confident_report(), make_jpeg())
        for ref in re.findall(r'(?:src|href)="([^"]*)"', html):
            assert ref.startswith("data:"), f"external reference: {ref[:60]}"

    def test_the_image_is_embedded(self):
        image = make_jpeg(colour=(12, 34, 56))
        html = build_single(confident_report(), image)
        assert "data:image/jpeg;base64," in html

    def test_it_works_without_an_image(self):
        html = build_single(confident_report(), None)
        assert "data:image" not in html
        assert "Paris" in html

    def test_the_locator_is_inline_svg(self):
        html = build_single(confident_report(), None)
        assert "<svg" in html and 'class="locator"' in html


class TestUncertaintyIsNotBuried:
    def test_an_insufficient_result_says_so_in_the_headline(self):
        html = build_single(uncertain_report(), make_jpeg())
        headline = html[html.index('class="place"'):][:200]
        assert "No location determined" in headline

    def test_it_states_no_coordinate_it_does_not_have(self):
        html = build_single(uncertain_report(), make_jpeg())
        assert "48.858377" not in html
        assert "Insufficient evidence" in html

    def test_the_reason_is_present_not_summarised_away(self):
        html = build_single(uncertain_report(), make_jpeg())
        assert "most of a hemisphere" in html

    def test_the_band_carries_what_it_was_measured_to_mean(self):
        for band, phrase in (
            ("high", "all 20 high-confidence calls"),
            ("insufficient", "only 16% within 1500 km"),
        ):
            r = confident_report() if band == "high" else uncertain_report()
            html = build_single(r, None)
            assert phrase in html, f"{band} band states no measurement"

    def test_every_report_carries_the_estimate_caveat(self):
        for r in (confident_report(), uncertain_report()):
            html = build_single(r, None)
            assert "not a record of fact" in html


class TestAttributionTravels:
    def test_osm_attribution_is_present_when_osm_data_is(self):
        html = build_single(confident_report(), None)
        assert "OpenStreetMap" in html
        assert "ODbL" in html

    def test_every_attribution_line_is_reproduced(self):
        html = build_single(confident_report(), None)
        for line in ATTRIBUTION:
            assert line in html, f"attribution dropped: {line}"

    def test_the_share_alike_obligation_is_stated(self):
        """A reader must be told that a database of these is subject to ODbL."""
        html = build_single(confident_report(), None)
        assert "database" in html.lower()

    def test_a_batch_report_carries_it_too(self):
        html = build_batch(batch_summary(), ATTRIBUTION)
        assert "OpenStreetMap" in html and "ODbL" in html


class TestNoFalseAuthority:
    @pytest.mark.parametrize(
        "forbidden",
        ["CONFIDENTIAL", "OFFICIAL", "Case No", "Case Number", "Exhibit",
         "Certified", "SECRET"],
    )
    def test_it_does_not_dress_itself_as_a_record(self, forbidden):
        """Making a model output look official is the worst thing it could do."""
        html = build_single(confident_report(), make_jpeg())
        assert forbidden.lower() not in html.lower()


class TestEmbeddedGpsStaysApart:
    def test_a_disagreeing_gps_tag_is_shown_and_labelled(self):
        r = confident_report()
        r["prediction"]["embedded_gps"] = {
            "latitude": 35.6762, "longitude": 139.6503,
            "altitude_m": None, "timestamp_utc": None,
            "processing_method": None,
            "place": {"country": {"name": "Japan"},
                      "nearest_places": [{"name": "Tokyo"}]},
            "trust_warnings": ["GPS is present but there is no camera make."],
        }
        r["prediction"]["exif_vs_estimate"] = {
            "distance_km": 9712.0, "consistent": False,
            "note": "One of them is wrong, and the tag is the easier to fake.",
        }
        html = build_single(r, None)
        assert "a claim, not proof" in html
        assert "Disagreement" in html
        assert "9,712 km" in html or "9712" in html
        assert "no camera make" in html


def batch_summary() -> dict:
    return {
        "type": "complete", "total": 5, "analysed": 4, "failed": 1,
        "skipped": 0, "cancelled": False, "geocode_requests": 2,
        "elapsed_s": 9.4, "seconds_per_image": 2.35,
        "clusters": [
            {"index": 0, "latitude": 35.69, "longitude": 139.75,
             "members": [0, 1], "size": 2, "place": "Tokyo, Tokyo, Japan",
             "radius_km": 10.5, "naming": {}},
            {"index": 1, "latitude": 48.8584, "longitude": 2.2945,
             "members": [2], "size": 1, "place": "Paris, Paris, France",
             "radius_km": 0.0, "naming": {}},
        ],
        "items": [
            {"name": "a.jpg", "state": "done", "latitude": 35.71,
             "longitude": 139.79, "confidence": "high",
             "place": "Tokyo, Tokyo, Japan", "cluster": 0, "error": None},
            {"name": "b.jpg", "state": "done", "latitude": 35.66,
             "longitude": 139.70, "confidence": "low",
             "place": "Tokyo, Tokyo, Japan", "cluster": 0, "error": None},
            {"name": "c.jpg", "state": "done", "latitude": 48.8584,
             "longitude": 2.2945, "confidence": "medium",
             "place": "Paris, Paris, France", "cluster": 1, "error": None},
            {"name": "d.jpg", "state": "failed", "latitude": None,
             "longitude": None, "confidence": None, "place": None,
             "cluster": None, "error": "Could not decode this file."},
        ],
    }


class TestBatchReport:
    def test_it_lists_every_place_and_every_image(self):
        html = build_batch(batch_summary(), ATTRIBUTION)
        assert "2 distinct places" in html
        for name in ("a.jpg", "b.jpg", "c.jpg", "d.jpg"):
            assert name in html
        assert "Tokyo, Tokyo, Japan" in html

    def test_a_failed_image_shows_its_error(self):
        html = build_batch(batch_summary(), ATTRIBUTION)
        assert "Could not decode this file." in html

    def test_it_states_the_geocoding_economy(self):
        html = build_batch(batch_summary(), ATTRIBUTION)
        assert "2 requests" in html or "2 request" in html

    def test_a_cancelled_batch_says_so_prominently(self):
        s = batch_summary()
        s["cancelled"] = True
        html = build_batch(s, ATTRIBUTION)
        assert "stopped before it finished" in html

    def test_it_is_self_contained(self):
        html = build_batch(batch_summary(), ATTRIBUTION).replace(
            'xmlns="http://www.w3.org/2000/svg"', ""
        )
        for pattern in ("http://", "https://", "<script", "@import"):
            assert pattern not in html


class TestLocator:
    def test_it_renders_for_any_coordinate(self):
        for lat, lon in [(0, 0), (90, 0), (-90, 0), (0, 180), (51.5, -0.1),
                         (-33.9, 151.2)]:
            svg = locator_svg(lat, lon)
            assert svg.startswith("<svg") and svg.endswith("</svg>")

    def test_it_contains_no_nan(self):
        svg = locator_svg(64.1466, -21.9426)
        assert "nan" not in svg.lower()


class TestEscaping:
    def test_a_hostile_place_name_cannot_inject_markup(self):
        r = confident_report()
        r["prediction"]["estimated"]["place"]["city"] = (
            '<script>alert(1)</script>'
        )
        html = build_single(r, None)
        assert "<script>alert" not in html
        assert "&lt;script&gt;" in html

    def test_a_hostile_filename_cannot_open_a_tag(self):
        """The payload survives as *text*, which is the point of escaping.

        Asserting the substring is absent would be wrong: "onerror=alert(1)"
        is harmless once the angle brackets are entities. What must not
        survive is an actual tag, or a quote that breaks out of an attribute.
        """
        r = confident_report()
        r["image"]["filename"] = '"><img src=x onerror=alert(1)>'
        html = build_single(r, None)
        assert "<img src=x" not in html
        assert "&lt;img src=x" in html
        assert "&quot;&gt;" in html
