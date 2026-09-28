"""Take a case somewhere else: QGIS, as a GeoPackage; kepler.gl, as a map.

Mapify is a tool for reaching an answer. QGIS and kepler.gl are tools
for working with one -- measuring, overlaying other data, filtering,
presenting. Neither is embedded here: QGIS is a desktop GIS under the
GPL, kepler.gl a full web application, and the right relationship with
both is that Mapify writes files they open, in formats they document.

GeoPackage (OGC 12-128r18) is SQLite with a defined layout, so it is
written with the standard library alone. It carries QGIS layer styles in
the ``layer_styles`` table QGIS reads on load, including a 2.5D renderer
that draws buildings extruded by their height field.

Licence notices travel with the data
------------------------------------

An export that contains OpenStreetMap buildings is a derivative database
under ODbL 1.0; one containing Google Open Buildings owes CC BY 4.0
attribution. So every export carries a ``notice`` table (GeoPackage) or
``info.description`` (kepler.gl) naming each source and licence. The
obligation belongs to the data, and the file is where the data goes.
"""

from __future__ import annotations

import json
import math
import sqlite3
import struct
import time
from pathlib import Path
from typing import Any

__all__ = ["build_gpkg", "build_kepler", "notices_for"]


# --------------------------------------------------------------- notices

def notices_for(around: dict[str, Any] | None) -> list[dict[str, str]]:
    out = [{"source": "Mapify",
            "licence": "MIT",
            "notice": ("Candidate locations are estimates produced by Mapify. "
                       "The confidence band says how far to trust them.")}]
    for s in (around or {}).get("sources", []):
        out.append({"source": s["name"], "licence": s["licence"],
                    "notice": s["attribution"] + (
                        f" ({s['link']})" if s.get("link") else "")})
    return out


# ------------------------------------------------------------------ WKB

def _wkb_point(x: float, y: float) -> bytes:
    return struct.pack("<BIdd", 1, 1, x, y)


def _wkb_line(coords: list[list[float]]) -> bytes:
    out = struct.pack("<BII", 1, 2, len(coords))
    return out + b"".join(struct.pack("<dd", c[0], c[1]) for c in coords)


def _wkb_polygon(rings: list[list[list[float]]]) -> bytes:
    out = struct.pack("<BII", 1, 3, len(rings))
    for ring in rings:
        pts = list(ring)
        if pts and pts[0] != pts[-1]:
            pts.append(pts[0])                 # WKB rings are closed
        out += struct.pack("<I", len(pts))
        out += b"".join(struct.pack("<dd", p[0], p[1]) for p in pts)
    return out


def _gpb(wkb: bytes, xs: list[float], ys: list[float]) -> bytes:
    """GeoPackageBinary: header with an envelope, then little-endian WKB."""
    flags = (1 << 1) | 1                        # envelope [x,x,y,y]; LE
    return (b"GP" + bytes([0, flags]) + struct.pack("<i", 4326)
            + struct.pack("<dddd", min(xs), max(xs), min(ys), max(ys)) + wkb)


def _circle(lon: float, lat: float, radius_m: float, n: int = 72
            ) -> list[list[float]]:
    dlat = radius_m / 111_320.0
    dlon = radius_m / (111_320.0 * max(0.05, math.cos(math.radians(lat))))
    return [[lon + dlon * math.cos(2 * math.pi * k / n),
             lat + dlat * math.sin(2 * math.pi * k / n)] for k in range(n + 1)]


# ------------------------------------------------------------ GeoPackage

_GPKG_CORE = """
PRAGMA application_id = 1196444487;
PRAGMA user_version = 10300;
CREATE TABLE gpkg_spatial_ref_sys (
  srs_name TEXT NOT NULL, srs_id INTEGER PRIMARY KEY,
  organization TEXT NOT NULL, organization_coordsys_id INTEGER NOT NULL,
  definition TEXT NOT NULL, description TEXT);
CREATE TABLE gpkg_contents (
  table_name TEXT NOT NULL PRIMARY KEY, data_type TEXT NOT NULL,
  identifier TEXT UNIQUE, description TEXT DEFAULT '',
  last_change DATETIME NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  min_x DOUBLE, min_y DOUBLE, max_x DOUBLE, max_y DOUBLE, srs_id INTEGER,
  CONSTRAINT fk_gc_r_srs_id FOREIGN KEY (srs_id)
    REFERENCES gpkg_spatial_ref_sys(srs_id));
CREATE TABLE gpkg_geometry_columns (
  table_name TEXT NOT NULL, column_name TEXT NOT NULL,
  geometry_type_name TEXT NOT NULL, srs_id INTEGER NOT NULL,
  z TINYINT NOT NULL, m TINYINT NOT NULL,
  CONSTRAINT pk_geom_cols PRIMARY KEY (table_name, column_name));
CREATE TABLE layer_styles (
  id INTEGER PRIMARY KEY AUTOINCREMENT, f_table_catalog TEXT,
  f_table_schema TEXT, f_table_name TEXT, f_geometry_column TEXT,
  styleName TEXT, styleQML TEXT, styleSLD TEXT, useAsDefault BOOLEAN,
  description TEXT, owner TEXT, ui TEXT, update_time DATETIME DEFAULT
  CURRENT_TIMESTAMP);
"""

_WGS84 = ('GEOGCS["WGS 84",DATUM["WGS_1984",SPHEROID["WGS 84",6378137,'
          '298.257223563,AUTHORITY["EPSG","7030"]],AUTHORITY["EPSG","6326"]],'
          'PRIMEM["Greenwich",0,AUTHORITY["EPSG","8901"]],UNIT["degree",'
          '0.0174532925199433,AUTHORITY["EPSG","9122"]],AUTHORITY["EPSG",'
          '"4326"]]')


def _qml_fill_by_height_source() -> str:
    def sym(name: str, rgba: str) -> str:
        return f"""<symbol type="fill" name="{name}" alpha="0.9" clip_to_extent="1" force_rhr="0">
  <layer class="SimpleFill" enabled="1" locked="0" pass="0"><Option type="Map">
    <Option type="QString" name="color" value="{rgba}"/>
    <Option type="QString" name="outline_color" value="35,39,47,255"/>
    <Option type="QString" name="outline_width" value="0.15"/>
    <Option type="QString" name="style" value="solid"/>
  </Option></layer></symbol>"""
    return f"""<!DOCTYPE qgis PUBLIC 'http://mrcc.com/qgis.dtd' 'SYSTEM'>
<qgis version="3.34" styleCategories="Symbology|Symbology3D">
<renderer-v2 type="categorizedSymbol" attr="height_source" symbollevels="0" enableorderby="0" forceraster="0">
<categories>
  <category value="measured" symbol="0" label="Height measured" render="true"/>
  <category value="levels" symbol="1" label="Height from floor count" render="true"/>
  <category value="assumed" symbol="2" label="Height not recorded (nominal)" render="true"/>
</categories>
<symbols>
{sym("0", "242,236,224,255")}
{sym("1", "208,218,232,255")}
{sym("2", "120,134,152,255")}
</symbols></renderer-v2>
<renderer-3d layer="buildings" type="vector">
  <symbol type="polygon" material_type="phong">
    <data alt-clamping="relative" alt-binding="centroid" height="0" extrusion-height="4" culling-mode="no-culling" rendered-facade="3" add-back-faces="0" invert-normals="0"/>
    <material diffuse="200,205,214,255" ambient="90,95,105,255" specular="30,30,30,255" shininess="0"/>
    <data-defined-properties><Option type="Map">
      <Option type="QString" name="name" value=""/>
      <Option name="properties" type="Map">
        <Option name="extrusionHeight" type="Map">
          <Option type="bool" name="active" value="true"/>
          <Option type="QString" name="field" value="height_m"/>
          <Option type="int" name="type" value="2"/>
        </Option>
      </Option>
      <Option type="QString" name="type" value="collection"/>
    </Option></data-defined-properties>
  </symbol>
</renderer-3d>
</qgis>"""


def _qml_points(rgba: str, size: str = "3.2") -> str:
    return f"""<!DOCTYPE qgis PUBLIC 'http://mrcc.com/qgis.dtd' 'SYSTEM'>
<qgis version="3.34" styleCategories="Symbology">
<renderer-v2 type="singleSymbol" symbollevels="0" enableorderby="0" forceraster="0">
<symbols><symbol type="marker" name="0" alpha="1" clip_to_extent="1" force_rhr="0">
  <layer class="SimpleMarker" enabled="1" locked="0" pass="0"><Option type="Map">
    <Option type="QString" name="color" value="{rgba}"/>
    <Option type="QString" name="outline_color" value="10,14,20,255"/>
    <Option type="QString" name="size" value="{size}"/>
    <Option type="QString" name="name" value="circle"/>
  </Option></layer></symbol></symbols></renderer-v2></qgis>"""


class _Gpkg:
    def __init__(self, path: Path):
        if path.exists():
            path.unlink()
        self.db = sqlite3.connect(str(path))
        self.db.executescript(_GPKG_CORE)
        self.db.executemany(
            "INSERT INTO gpkg_spatial_ref_sys VALUES (?,?,?,?,?,?)", [
                ("Undefined cartesian SRS", -1, "NONE", -1, "undefined", None),
                ("Undefined geographic SRS", 0, "NONE", 0, "undefined", None),
                ("WGS 84 geodetic", 4326, "EPSG", 4326, _WGS84,
                 "longitude/latitude coordinates in decimal degrees on the "
                 "WGS 84 spheroid"),
            ])

    def layer(self, name: str, geom_type: str, columns: dict[str, str],
              rows: list[tuple[bytes, list[float], list[float], tuple]],
              description: str, qml: str | None = None) -> None:
        if not rows:
            return
        cols = ", ".join(f'"{c}" {t}' for c, t in columns.items())
        self.db.execute(f'CREATE TABLE "{name}" (fid INTEGER PRIMARY KEY '
                        f'AUTOINCREMENT, geom {geom_type}, {cols})')
        marks = ", ".join("?" for _ in range(len(columns) + 1))
        names = ", ".join(['geom'] + [f'"{c}"' for c in columns])
        xs_all, ys_all = [], []
        for wkb, xs, ys, attrs in rows:
            self.db.execute(f'INSERT INTO "{name}" ({names}) VALUES ({marks})',
                            (_gpb(wkb, xs, ys), *attrs))
            xs_all += [min(xs), max(xs)]
            ys_all += [min(ys), max(ys)]
        self.db.execute(
            "INSERT INTO gpkg_contents (table_name, data_type, identifier, "
            "description, min_x, min_y, max_x, max_y, srs_id) VALUES "
            "(?, 'features', ?, ?, ?, ?, ?, ?, 4326)",
            (name, name, description, min(xs_all), min(ys_all), max(xs_all),
             max(ys_all)))
        self.db.execute("INSERT INTO gpkg_geometry_columns VALUES "
                        "(?, 'geom', ?, 4326, 0, 0)", (name, geom_type))
        if qml:
            self.db.execute(
                "INSERT INTO layer_styles (f_table_catalog, f_table_schema, "
                "f_table_name, f_geometry_column, styleName, styleQML, "
                "styleSLD, useAsDefault, description, owner, ui) VALUES "
                "('', '', ?, 'geom', ?, ?, '', 1, 'Mapify default', '', '')",
                (name, f"{name} (Mapify)", qml))

    def attributes(self, name: str, columns: dict[str, str],
                   rows: list[tuple], description: str) -> None:
        cols = ", ".join(f'"{c}" {t}' for c, t in columns.items())
        self.db.execute(f'CREATE TABLE "{name}" (fid INTEGER PRIMARY KEY '
                        f'AUTOINCREMENT, {cols})')
        marks = ", ".join("?" for _ in columns)
        names = ", ".join(f'"{c}"' for c in columns)
        self.db.executemany(f'INSERT INTO "{name}" ({names}) VALUES ({marks})',
                            rows)
        self.db.execute(
            "INSERT INTO gpkg_contents (table_name, data_type, identifier, "
            "description) VALUES (?, 'attributes', ?, ?)",
            (name, name, description))

    def close(self) -> None:
        self.db.commit()
        self.db.close()


def build_gpkg(report: dict[str, Any], around: dict[str, Any] | None,
               path: str | Path) -> Path:
    """Write one case as a GeoPackage QGIS opens styled."""
    path = Path(path)
    g = _Gpkg(path)
    pred = report.get("prediction") or {}

    cand_rows = []
    for i, c in enumerate(report.get("candidates") or []):
        x, y = c["longitude"], c["latitude"]
        loc = c.get("location") or {}
        name = ((c.get("nominatim") or {}).get("city")
                or (loc.get("nearest_places") or [{}])[0].get("name")
                or (loc.get("country") or {}).get("name") or "")
        cand_rows.append((_wkb_point(x, y), [x], [y],
                          (i + 1, round(c.get("probability", 0.0), 5), name,
                           c.get("provenance") or "model")))
    g.layer("candidates", "POINT",
            {"rank": "INTEGER", "probability": "REAL", "place": "TEXT",
             "provenance": "TEXT"}, cand_rows,
            "Candidate locations Mapify considered, best first. Estimates, "
            "not findings -- see the confidence band.",
            _qml_points("127,180,214,255"))

    est = pred.get("estimated")
    if pred.get("determinate") and est:
        x, y = est["longitude"], est["latitude"]
        g.layer("estimate", "POINT",
                {"confidence": "TEXT", "uncertainty_km": "REAL",
                 "precision_km": "REAL", "provenance": "TEXT"},
                [(_wkb_point(x, y), [x], [y],
                  (pred.get("confidence"), pred.get("uncertainty_km"),
                   pred.get("precision_km"), est.get("provenance")))],
                "The location Mapify reported, with its confidence band.",
                _qml_points("90,184,240,255", "4.5"))
        unc = pred.get("uncertainty_km")
        if unc and 0 < unc <= 500:
            ring = _circle(x, y, unc * 1000.0)
            g.layer("uncertainty", "POLYGON", {"radius_km": "REAL"},
                    [(_wkb_polygon([ring]), [p[0] for p in ring],
                      [p[1] for p in ring], (unc,))],
                    "How far apart the candidates were: the spread of the "
                    "estimate, not the precision of the coordinate.")

    if around:
        rows = []
        for b in around.get("buildings", []):
            ring = b["polygon"][0]
            rows.append((_wkb_polygon(b["polygon"]), [p[0] for p in ring],
                         [p[1] for p in ring],
                         (b.get("name"), b.get("type"), b.get("height"),
                          b.get("base"), b.get("height_source"),
                          b.get("source"), b.get("confidence"))))
        g.layer("buildings", "POLYGON",
                {"name": "TEXT", "type": "TEXT", "height_m": "REAL",
                 "base_m": "REAL", "height_source": "TEXT", "source": "TEXT",
                 "detection_confidence": "REAL"}, rows,
                "Buildings around the estimate. height_source says whether "
                "the height is measured, from a floor count, or assumed.",
                _qml_fill_by_height_source())
        rows = [(_wkb_line(r["path"]), [p[0] for p in r["path"]],
                 [p[1] for p in r["path"]], (r.get("name"), r.get("class")))
                for r in around.get("roads", []) if len(r["path"]) >= 2]
        g.layer("roads", "LINESTRING", {"name": "TEXT", "class": "TEXT"},
                rows, "Streets from OpenStreetMap.")
        rows = [(_wkb_polygon(w["polygon"]), [p[0] for p in w["polygon"][0]],
                 [p[1] for p in w["polygon"][0]], (w.get("name"),))
                for w in around.get("water", [])]
        g.layer("water", "POLYGON", {"name": "TEXT"}, rows,
                "Water from OpenStreetMap.")
        rows = [(_wkb_point(*p["position"]), [p["position"][0]],
                 [p["position"][1]], (p.get("name"), p.get("kind"),
                                      p.get("distance_m")))
                for p in around.get("pois", [])]
        g.layer("places", "POINT",
                {"name": "TEXT", "kind": "TEXT", "distance_m": "INTEGER"},
                rows, "Named places from OpenStreetMap.")

    g.attributes("notice", {"source": "TEXT", "licence": "TEXT",
                            "notice": "TEXT"},
                 [(n["source"], n["licence"], n["notice"])
                  for n in notices_for(around)],
                 "Sources and licences for everything in this file. Keep "
                 "this table with the data if you share it.")
    g.close()
    return path


# --------------------------------------------------------------- kepler.gl

def build_kepler(report: dict[str, Any], around: dict[str, Any] | None
                 ) -> dict[str, Any]:
    """A kepler.gl map file: datasets, layer config and map state.

    Drop it onto kepler.gl (or load it with ``KeplerGl.addDataToMap``).
    Layer settings not given here fall back to kepler.gl's defaults.
    """
    pred = report.get("prediction") or {}
    datasets, layers = [], []

    rows = []
    for i, c in enumerate(report.get("candidates") or []):
        rows.append([c["latitude"], c["longitude"], i + 1,
                     round(c.get("probability", 0.0), 5),
                     c.get("provenance") or "model"])
    if rows:
        datasets.append({"version": "v1", "data": {
            "id": "candidates", "label": "Candidate locations",
            "color": [127, 180, 214], "allData": rows,
            "fields": [{"name": "lat", "type": "real"},
                       {"name": "lng", "type": "real"},
                       {"name": "rank", "type": "integer"},
                       {"name": "probability", "type": "real"},
                       {"name": "provenance", "type": "string"}]}})
        layers.append({"id": "candidates", "type": "point", "config": {
            "dataId": "candidates", "label": "Candidates",
            "color": [90, 184, 240], "isVisible": True,
            "columns": {"lat": "lat", "lng": "lng", "altitude": None},
            "visConfig": {"radius": 12, "opacity": 0.9, "filled": True}},
            "visualChannels": {"sizeField": {"name": "probability",
                                             "type": "real"},
                               "sizeScale": "sqrt"}})

    if around and around.get("buildings"):
        rows = []
        for b in around["buildings"]:
            rows.append([{"type": "Polygon", "coordinates": b["polygon"]},
                         b.get("height") or 0.0, b.get("height_source"),
                         b.get("source"), b.get("name") or ""])
        datasets.append({"version": "v1", "data": {
            "id": "buildings", "label": "Buildings", "color": [200, 205, 214],
            "allData": rows,
            "fields": [{"name": "_geojson", "type": "geojson"},
                       {"name": "height_m", "type": "real"},
                       {"name": "height_source", "type": "string"},
                       {"name": "source", "type": "string"},
                       {"name": "name", "type": "string"}]}})
        layers.append({"id": "buildings", "type": "geojson", "config": {
            "dataId": "buildings", "label": "Buildings (3D)",
            "color": [200, 205, 214], "isVisible": True,
            "columns": {"geojson": "_geojson"},
            "visConfig": {"opacity": 0.85, "filled": True, "stroked": False,
                          "enable3d": True, "elevationScale": 1,
                          "wireframe": False}},
            "visualChannels": {
                "colorField": {"name": "height_source", "type": "string"},
                "colorScale": "ordinal",
                "heightField": {"name": "height_m", "type": "real"},
                "heightScale": "linear"}})

    centre = (pred.get("estimated") or {}) if pred.get("determinate") else {}
    lat = centre.get("latitude") or (around or {}).get("center", [0, 0])[1]
    lon = centre.get("longitude") or (around or {}).get("center", [0, 0])[0]
    notice = "; ".join(f"{n['source']}: {n['licence']} — {n['notice']}"
                       for n in notices_for(around))
    return {
        "datasets": datasets,
        "config": {"version": "v1", "config": {
            "visState": {"filters": [], "layers": layers,
                         "interactionConfig": {"tooltip": {"enabled": True}},
                         "layerBlending": "normal"},
            "mapState": {"latitude": lat, "longitude": lon,
                         "zoom": 15.5 if around else 5, "pitch": 50,
                         "bearing": -20, "dragRotate": True},
        }},
        "info": {"app": "kepler.gl", "source": "Mapify",
                 "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                 "title": "Mapify case", "description": notice},
    }
