"""Where the Moon is, and how much of it is lit.

Meeus, *Astronomical Algorithms*, chapter 47, with the largest terms of
the ELP-2000/82 series -- the ones that matter at the level a photograph
can be read to. Longitude comes out within about 0.05 degrees of the
full series, latitude within about 0.02, distance within a few hundred
kilometres: enough to say whether the Moon was above the horizon and how
full it was, which is all a photograph can check.

Topocentric altitude includes the lunar parallax, up to about a degree,
because without it the Moon "rises" noticeably early.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime

from .solar import _core, julian_day

__all__ = ["MoonPosition", "moon_position"]

_RAD = math.pi / 180.0

# (D, M, M', F, coefficient) -- sine terms of longitude, 1e-6 degrees.
_LON = (
    (0, 0, 1, 0, 6288774), (2, 0, -1, 0, 1274027), (2, 0, 0, 0, 658314),
    (0, 0, 2, 0, 213618), (0, 1, 0, 0, -185116), (0, 0, 0, 2, -114332),
    (2, 0, -2, 0, 58793), (2, -1, -1, 0, 57066), (2, 0, 1, 0, 53322),
    (2, -1, 0, 0, 45758), (0, 1, -1, 0, -40923), (1, 0, 0, 0, -34720),
    (0, 1, 1, 0, -30383), (2, 0, 0, -2, 15327), (0, 0, 1, 2, -12528),
    (0, 0, 1, -2, 10980), (4, 0, -1, 0, 10675), (0, 0, 3, 0, 10034),
    (4, 0, -2, 0, 8548), (2, 1, -1, 0, -7888), (2, 1, 0, 0, -6766),
    (1, 0, -1, 0, -5163), (1, 1, 0, 0, 4987), (2, -1, 1, 0, 4036),
    (2, 0, 2, 0, 3994), (4, 0, 0, 0, 3861), (2, 0, -3, 0, 3665),
    (0, 1, -2, 0, -2689), (2, 0, -1, 2, -2602), (2, -1, -2, 0, 2390),
    (1, 0, 1, 0, -2348), (2, -2, 0, 0, 2236), (0, 1, 2, 0, -2120),
    (0, 2, 0, 0, -2069), (2, -2, -1, 0, 2048),
)
# Cosine terms of distance, 1e-3 km.
_DIST = (
    (0, 0, 1, 0, -20905355), (2, 0, -1, 0, -3699111), (2, 0, 0, 0, -2955968),
    (0, 0, 2, 0, -569925), (0, 1, 0, 0, 48888), (0, 0, 0, 2, -3149),
    (2, 0, -2, 0, 246158), (2, -1, -1, 0, -152138), (2, 0, 1, 0, -170733),
    (2, -1, 0, 0, -204586), (0, 1, -1, 0, -129620), (1, 0, 0, 0, 108743),
    (0, 1, 1, 0, 104755), (2, 0, 0, -2, 10321), (0, 0, 1, -2, 79661),
    (4, 0, -1, 0, -34782), (0, 0, 3, 0, -23210), (4, 0, -2, 0, -21636),
    (2, 1, -1, 0, 24208), (2, 1, 0, 0, 30824), (1, 0, -1, 0, -8379),
    (1, 1, 0, 0, -16675), (2, -1, 1, 0, -12831), (2, 0, 2, 0, -10445),
    (4, 0, 0, 0, -11650), (2, 0, -3, 0, 14403),
)
# Sine terms of latitude, 1e-6 degrees.
_LAT = (
    (0, 0, 0, 1, 5128122), (0, 0, 1, 1, 280602), (0, 0, 1, -1, 277693),
    (2, 0, 0, -1, 173237), (2, 0, -1, 1, 55413), (2, 0, -1, -1, 46271),
    (2, 0, 0, 1, 32573), (0, 0, 2, 1, 17198), (2, 0, 1, -1, 9266),
    (0, 0, 2, -1, 8822), (2, -1, 0, -1, 8216), (2, 0, -2, -1, 4324),
    (2, 0, 1, 1, 4200), (2, 1, 0, -1, -3359), (2, -1, -1, 1, 2463),
    (2, -1, 0, 1, 2211), (2, -1, -1, -1, 2065), (0, 1, -1, -1, -1870),
    (4, 0, -1, -1, 1828), (0, 1, 0, 1, -1794),
)


@dataclass(frozen=True)
class MoonPosition:
    altitude: float             # topocentric, degrees
    azimuth: float              # degrees clockwise from true north
    illuminated: float          # 0..1
    waxing: bool
    distance_km: float
    ecliptic_longitude: float
    ecliptic_latitude: float

    @property
    def up(self) -> bool:
        return self.altitude > -0.3

    @property
    def phase_name(self) -> str:
        k = self.illuminated
        if k < 0.03:
            return "new moon"
        if k > 0.97:
            return "full moon"
        if 0.47 <= k <= 0.53:
            return "first quarter" if self.waxing else "last quarter"
        if k < 0.5:
            return "waxing crescent" if self.waxing else "waning crescent"
        return "waxing gibbous" if self.waxing else "waning gibbous"


def _ecliptic(jd: float) -> tuple[float, float, float]:
    t = (jd - 2451545.0) / 36525.0
    lp = 218.3164477 + 481267.88123421 * t - 0.0015786 * t * t
    d = 297.8501921 + 445267.1114034 * t - 0.0018819 * t * t
    m = 357.5291092 + 35999.0502909 * t - 0.0001536 * t * t
    mp = 134.9633964 + 477198.8675055 * t + 0.0087414 * t * t
    f = 93.2720950 + 483202.0175233 * t - 0.0036539 * t * t
    e = 1.0 - 0.002516 * t - 0.0000074 * t * t

    def arg(td, tm, tmp, tf):
        return (td * d + tm * m + tmp * mp + tf * f) * _RAD

    def ecc(tm):
        return e ** abs(tm)

    sl = sum(c * ecc(tm) * math.sin(arg(td, tm, tmp, tf)) for td, tm, tmp, tf, c in _LON)
    sr = sum(c * ecc(tm) * math.cos(arg(td, tm, tmp, tf)) for td, tm, tmp, tf, c in _DIST)
    sb = sum(c * ecc(tm) * math.sin(arg(td, tm, tmp, tf)) for td, tm, tmp, tf, c in _LAT)
    a1 = (119.75 + 131.849 * t) * _RAD
    a2 = (53.09 + 479264.290 * t) * _RAD
    a3 = (313.45 + 481266.484 * t) * _RAD
    sl += 3958 * math.sin(a1) + 1962 * math.sin(lp * _RAD - f * _RAD) + 318 * math.sin(a2)
    sb += (-2235 * math.sin(lp * _RAD) + 382 * math.sin(a3)
           + 175 * math.sin(a1 - f * _RAD) + 175 * math.sin(a1 + f * _RAD)
           + 127 * math.sin(lp * _RAD - mp * _RAD) - 115 * math.sin(lp * _RAD + mp * _RAD))
    lam = (lp + sl / 1e6) % 360.0
    beta = sb / 1e6
    dist = 385000.56 + sr / 1000.0
    return lam, beta, dist


def moon_position(lat: float, lon: float, when: datetime) -> MoonPosition:
    jd = julian_day(when)
    t = (jd - 2451545.0) / 36525.0
    lam, beta, dist = _ecliptic(jd)

    eps = 23.4392911 - 0.0130042 * t
    lr, br, er = lam * _RAD, beta * _RAD, eps * _RAD
    ra = math.atan2(math.sin(lr) * math.cos(er) - math.tan(br) * math.sin(er),
                    math.cos(lr))
    dec = math.asin(math.sin(br) * math.cos(er)
                    + math.cos(br) * math.sin(er) * math.sin(lr))

    gmst = (280.46061837 + 360.98564736629 * (jd - 2451545.0)
            + 0.000387933 * t * t - t ** 3 / 38710000.0) % 360.0
    ha = ((gmst + lon) * _RAD - ra)
    latr = lat * _RAD
    alt = math.asin(math.sin(latr) * math.sin(dec)
                    + math.cos(latr) * math.cos(dec) * math.cos(ha))
    az = math.atan2(math.sin(ha),
                    math.cos(ha) * math.sin(latr) - math.tan(dec) * math.cos(latr))
    az_deg = (math.degrees(az) + 180.0) % 360.0
    # Parallax: seen from the surface rather than the Earth's centre.
    parallax = math.asin(6378.14 / dist)
    alt_topo = math.degrees(alt - parallax * math.cos(alt))

    # Phase, from the Moon-Sun elongation.
    _decl, _eot, sun_lam = _core(jd)
    elong = math.acos(math.cos(br) * math.cos(lr - sun_lam * _RAD))
    r_sun = 149597870.7
    i = math.atan2(r_sun * math.sin(elong), dist - r_sun * math.cos(elong))
    illuminated = (1 + math.cos(i)) / 2.0
    waxing = ((lam - sun_lam) % 360.0) < 180.0
    return MoonPosition(altitude=alt_topo, azimuth=az_deg,
                        illuminated=illuminated, waxing=waxing,
                        distance_km=dist, ecliptic_longitude=lam,
                        ecliptic_latitude=beta)
