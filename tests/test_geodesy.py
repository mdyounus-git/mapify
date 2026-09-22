"""Spherical geometry.

These are the numbers every distance, uncertainty radius and marker circle in
the application is built on, so they are checked against known values rather
than against themselves.
"""

from __future__ import annotations

import math

import pytest

from mapify.geo.geodesy import (
    bearing_deg,
    circle_polygon,
    compass_point,
    containment_radius_km,
    destination,
    haversine_km,
    weighted_centroid,
)


class TestHaversine:
    @pytest.mark.parametrize(
        "a, b, expected_km, tol",
        [
            # London Heathrow to JFK: a textbook great-circle distance.
            ((51.4700, -0.4543), (40.6413, -73.7781), 5555.0, 25.0),
            # One degree of latitude at the equator.
            ((0.0, 0.0), (1.0, 0.0), 111.19, 0.5),
            # One degree of longitude at the equator.
            ((0.0, 0.0), (0.0, 1.0), 111.19, 0.5),
            # A degree of longitude shrinks with the cosine of latitude.
            ((60.0, 0.0), (60.0, 1.0), 55.6, 0.5),
            # Antipodes: half the circumference.
            ((0.0, 0.0), (0.0, 180.0), 20015.0, 5.0),
        ],
    )
    def test_known_distances(self, a, b, expected_km, tol):
        assert haversine_km(*a, *b) == pytest.approx(expected_km, abs=tol)

    def test_identity_is_zero(self):
        assert haversine_km(12.97, 77.59, 12.97, 77.59) == 0.0

    def test_symmetric(self):
        d1 = haversine_km(35.68, 139.69, -33.87, 151.21)
        d2 = haversine_km(-33.87, 151.21, 35.68, 139.69)
        assert d1 == pytest.approx(d2, rel=1e-12)

    def test_crossing_the_antimeridian_is_short(self):
        """179E to 179W is two degrees apart, not 358."""
        d = haversine_km(0.0, 179.0, 0.0, -179.0)
        assert d == pytest.approx(222.4, abs=1.0)


class TestBearing:
    def test_due_north(self):
        assert bearing_deg(0, 0, 10, 0) == pytest.approx(0.0, abs=1e-6)

    def test_due_east(self):
        assert bearing_deg(0, 0, 0, 10) == pytest.approx(90.0, abs=1e-6)

    def test_due_south(self):
        assert bearing_deg(10, 0, 0, 0) == pytest.approx(180.0, abs=1e-6)

    def test_due_west(self):
        assert bearing_deg(0, 10, 0, 0) == pytest.approx(270.0, abs=1e-6)

    @pytest.mark.parametrize(
        "bearing, name",
        [(0, "N"), (45, "NE"), (90, "E"), (180, "S"), (270, "W"), (359, "N")],
    )
    def test_compass_names(self, bearing, name):
        assert compass_point(bearing) == name


class TestDestination:
    def test_round_trip(self):
        """Walking out and measuring back gives the distance walked."""
        lat, lon = destination(48.8584, 2.2945, 37.0, 250.0)
        assert haversine_km(48.8584, 2.2945, lat, lon) == pytest.approx(250.0, abs=0.5)

    def test_longitude_stays_in_range(self):
        _, lon = destination(0.0, 179.5, 90.0, 400.0)
        assert -180.0 <= lon <= 180.0


class TestWeightedCentroid:
    def test_single_point_is_itself(self):
        lat, lon = weighted_centroid([(12.5, 77.5, 1.0)])
        assert (lat, lon) == pytest.approx((12.5, 77.5))

    def test_midpoint_of_two_equal_weights(self):
        lat, lon = weighted_centroid([(0.0, 0.0, 1.0), (0.0, 10.0, 1.0)])
        assert lat == pytest.approx(0.0, abs=1e-9)
        assert lon == pytest.approx(5.0, abs=1e-9)

    def test_weights_pull_the_answer(self):
        lat, lon = weighted_centroid([(0.0, 0.0, 9.0), (0.0, 10.0, 1.0)])
        assert lon == pytest.approx(1.0, abs=0.05)

    def test_survives_the_antimeridian(self):
        """Averaging degrees would give 0; averaging vectors gives 180."""
        lat, lon = weighted_centroid([(0.0, 179.0, 1.0), (0.0, -179.0, 1.0)])
        assert lat == pytest.approx(0.0, abs=1e-6)
        assert abs(abs(lon) - 180.0) < 1e-6

    def test_rejects_empty(self):
        with pytest.raises(ValueError):
            weighted_centroid([])

    def test_zero_weights_fall_back(self):
        lat, lon = weighted_centroid([(5.0, 5.0, 0.0), (6.0, 6.0, 0.0)])
        assert (lat, lon) == (5.0, 5.0)


class TestContainmentRadius:
    def test_coincident_points_have_no_spread(self):
        pts = [(10.0, 10.0, 0.5), (10.0, 10.0, 0.5)]
        assert containment_radius_km(pts, (10.0, 10.0)) == pytest.approx(0.0)

    def test_covers_the_requested_mass(self):
        # 90% of the weight is at the centre; the 68% radius must not reach
        # the distant outlier.
        pts = [(0.0, 0.0, 0.9), (0.0, 10.0, 0.1)]
        r = containment_radius_km(pts, (0.0, 0.0), mass=0.68)
        assert r < 1.0

    def test_stretches_when_mass_is_spread(self):
        pts = [(0.0, 0.0, 0.5), (0.0, 10.0, 0.5)]
        r = containment_radius_km(pts, (0.0, 5.0), mass=0.68)
        assert r == pytest.approx(556.0, abs=5.0)

    def test_empty_is_zero(self):
        assert containment_radius_km([], (0.0, 0.0)) == 0.0


class TestCirclePolygon:
    def test_ring_is_closed(self):
        ring = circle_polygon(48.0, 2.0, 100.0, segments=36)
        assert len(ring) == 37
        assert ring[0] == pytest.approx(ring[-1])

    def test_every_vertex_sits_on_the_radius(self):
        ring = circle_polygon(48.0, 2.0, 100.0, segments=24)
        for lon, lat in ring:
            assert haversine_km(48.0, 2.0, lat, lon) == pytest.approx(100.0, abs=0.5)

    def test_works_at_the_pole(self):
        ring = circle_polygon(89.5, 0.0, 200.0, segments=24)
        assert all(-90.0 <= lat <= 90.0 for _, lat in ring)
        assert all(-180.0 <= lon <= 180.0 for lon, _ in ring)
