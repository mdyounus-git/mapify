"""GeoNames as the gazetteer's fallback: built from allCountries.zip, asked
only when Natural Earth knows no place by a name, and only when switched on.

A tiny allCountries file is written here; nothing touches the network.
"""

from __future__ import annotations

import zipfile

from mapify.geo.geonames import MIN_POPULATION, GeoNames, build


def row(gid, name, ascii_, alts, lat, lon, fclass, fcode, cc, pop):
    cols = [str(gid), name, ascii_, alts, str(lat), str(lon), fclass, fcode, cc, "",
            "01", "", "", "", str(pop), "", "0", "Europe/Paris", "2026-01-01"]
    return "\t".join(cols) + "\n"


def make_zip(path):
    lines = [
        row(1, "Saint-Étienne", "Saint-Etienne", "Sent-Etiève,Сент-Этьен", 45.43, 4.39,
            "P", "PPLA2", "FR", 171000),
        row(2, "Hameau", "Hameau", "", 45.0, 4.0, "P", "PPL", "FR", 40),     # too small
        row(3, "Station", "Station", "", 10.0, 10.0, "S", "RSTN", "NG", 0),  # not a place
        row(4, "Île-de-France", "Ile-de-France", "", 48.5, 2.5, "A", "ADM1", "FR", 0),
        row(7, "Centre", "Centre", "Centre Region", 12.4, -1.5, "A", "ADM1", "BF", 3000000),
        row(5, "Springfield", "Springfield", "", 39.8, -89.6, "P", "PPLA", "US", 114000),
        row(6, "Springfield", "Springfield", "", 42.1, -72.6, "P", "PPL", "US", 155000),
    ]
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("allCountries.txt", "".join(lines))


def test_only_real_places_are_kept_and_every_spelling_finds_them(tmp_path):
    src, db = tmp_path / "allCountries.zip", tmp_path / "g.sqlite"
    make_zip(src)
    assert build(src, db) == 5             # the hamlet and the station left out
    g = GeoNames(db)
    for spelling in ("SAINT-ETIENNE", "saint-étienne", "Сент-Этьен"):
        hits = g.find(spelling)
        assert [h.name for h in hits] == ["Saint-Étienne"], spelling
    hit = g.find("Saint-Etienne")[0]
    assert hit.matched_field == "GEONAMES" and hit.population == 171000
    assert hit.admin0 == "France"
    assert g.find("Hameau") == [] and g.find("Station") == []
    assert g.find("Ile-de-France")[0].name == "Île-de-France"
    assert MIN_POPULATION > 40


def test_the_bigger_of_two_same_named_places_comes_first(tmp_path):
    src, db = tmp_path / "allCountries.zip", tmp_path / "g.sqlite"
    make_zip(src)
    build(src, db)
    assert [h.population for h in GeoNames(db).find("Springfield")] == [155000, 114000]


def test_without_an_index_nothing_is_found(tmp_path):
    assert GeoNames(tmp_path / "missing.sqlite").find("Paris") == []


def test_the_gazetteer_asks_geonames_only_when_switched_on(tmp_path, monkeypatch):
    from mapify.geo import offline

    src, db = tmp_path / "allCountries.zip", tmp_path / "g.sqlite"
    make_zip(src)
    build(src, db)
    geo = offline.OfflineGeocoder(tmp_path / "no-natural-earth")
    monkeypatch.setattr(offline.OfflineGeocoder, "_ensure_places",
                        lambda self: (_ for _ in ()).throw(offline.GeodataMissing("x")))
    assert geo.find_place("Saint-Etienne") == []          # off by default
    geo.use_geonames = True
    geo._geonames = GeoNames(db)
    assert [m.name for m in geo.find_place("Saint-Etienne")] == ["Saint-Étienne"]


def test_a_region_is_not_found_by_a_word_signs_say_for_other_reasons(tmp_path):
    """A shop's "CENTRE" found Burkina Faso's Centre region."""
    src, db = tmp_path / "allCountries.zip", tmp_path / "g.sqlite"
    make_zip(src)
    build(src, db)
    g = GeoNames(db)
    assert g.find("Centre") == []
    assert [h.name for h in g.find("Centre Region")] == ["Centre"]
