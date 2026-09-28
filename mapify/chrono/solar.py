"""Where the sun is: the NOAA solar position algorithm.

The "General Solar Position Calculations" of the NOAA Global Monitoring
Laboratory, after Meeus, *Astronomical Algorithms*. Good to about 0.01
degrees in elevation between 1800 and 2100 -- far finer than anything a
photograph can be measured to, which is the point: when the photograph
and this disagree, the photograph (or the claim about it) is wrong.

All times are UTC. Azimuth is degrees clockwise from true north.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

__all__ = ["SunPosition", "SunTimes", "sun_position", "sun_times",
           "julian_day"]

_RAD = math.pi / 180.0


def julian_day(when: datetime) -> float:
    """Julian Day for an aware datetime (naive is taken as UTC)."""
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    when = when.astimezone(timezone.utc)
    epoch = datetime(2000, 1, 1, 12, tzinfo=timezone.utc)
    return 2451545.0 + (when - epoch).total_seconds() / 86400.0


def _core(jd: float) -> tuple[float, float, float]:
    """(declination deg, equation of time minutes, apparent longitude deg)."""
    t = (jd - 2451545.0) / 36525.0
    l0 = (280.46646 + t * (36000.76983 + t * 0.0003032)) % 360.0
    m = 357.52911 + t * (35999.05029 - 0.0001537 * t)
    e = 0.016708634 - t * (0.000042037 + 0.0000001267 * t)
    mr = m * _RAD
    c = (math.sin(mr) * (1.914602 - t * (0.004817 + 0.000014 * t))
         + math.sin(2 * mr) * (0.019993 - 0.000101 * t)
         + math.sin(3 * mr) * 0.000289)
    true_long = l0 + c
    omega = 125.04 - 1934.136 * t
    app_long = true_long - 0.00569 - 0.00478 * math.sin(omega * _RAD)
    eps0 = 23.0 + (26.0 + (21.448 - t * (46.815 + t * (0.00059 - t * 0.001813)))
                   / 60.0) / 60.0
    eps = eps0 + 0.00256 * math.cos(omega * _RAD)
    decl = math.degrees(math.asin(math.sin(eps * _RAD) * math.sin(app_long * _RAD)))
    y = math.tan(eps * _RAD / 2.0) ** 2
    l0r = l0 * _RAD
    eot = 4.0 * math.degrees(
        y * math.sin(2 * l0r) - 2 * e * math.sin(mr)
        + 4 * e * y * math.sin(mr) * math.cos(2 * l0r)
        - 0.5 * y * y * math.sin(4 * l0r) - 1.25 * e * e * math.sin(2 * mr))
    return decl, eot, app_long


def _refraction(elev: float) -> float:
    """Atmospheric refraction in degrees, NOAA's piecewise approximation."""
    if elev > 85.0:
        return 0.0
    te = math.tan(elev * _RAD)
    if elev > 5.0:
        arcsec = 58.1 / te - 0.07 / te ** 3 + 0.000086 / te ** 5
    elif elev > -0.575:
        arcsec = 1735.0 + elev * (-518.2 + elev * (103.4 + elev * (-12.79
                                                                    + elev * 0.711)))
    else:
        arcsec = -20.774 / te
    return arcsec / 3600.0


@dataclass(frozen=True)
class SunPosition:
    elevation: float           # degrees above the horizon, refraction included
    azimuth: float             # degrees clockwise from true north
    declination: float
    equation_of_time_min: float
    hour_angle: float

    @property
    def up(self) -> bool:
        return self.elevation > -0.833

    @property
    def phase(self) -> str:
        """Night, twilight, low sun or day, by the usual thresholds."""
        e = self.elevation
        if e < -18:
            return "night"
        if e < -6:
            return "astronomical/nautical twilight"
        if e < -0.833:
            return "civil twilight"
        if e < 10:
            return "low sun"
        return "day"

    def shadow_ratio(self) -> float | None:
        """Shadow length per unit of object height, or None below the horizon."""
        if self.elevation <= 0.5:
            return None
        return 1.0 / math.tan(self.elevation * _RAD)


def sun_position(lat: float, lon: float, when: datetime) -> SunPosition:
    jd = julian_day(when)
    decl, eot, _ = _core(jd)
    utc = when.astimezone(timezone.utc) if when.tzinfo else when
    minutes = utc.hour * 60.0 + utc.minute + utc.second / 60.0
    tst = (minutes + eot + 4.0 * lon) % 1440.0
    ha = tst / 4.0 - 180.0
    latr, decr, har = lat * _RAD, decl * _RAD, ha * _RAD
    cos_zen = (math.sin(latr) * math.sin(decr)
               + math.cos(latr) * math.cos(decr) * math.cos(har))
    zen = math.degrees(math.acos(max(-1.0, min(1.0, cos_zen))))
    elev = 90.0 - zen
    elev += _refraction(elev)
    # Azimuth, measured from north through east.
    denom = math.cos(latr) * math.sin(zen * _RAD)
    if abs(denom) < 1e-9:
        az = 180.0 if lat > decl else 0.0
    else:
        cos_az = (math.sin(latr) * math.cos(zen * _RAD) - math.sin(decr)) / denom
        az = math.degrees(math.acos(max(-1.0, min(1.0, cos_az))))
        az = (az + 180.0) % 360.0 if ha > 0 else (540.0 - az) % 360.0
    return SunPosition(elevation=elev, azimuth=az, declination=decl,
                       equation_of_time_min=eot, hour_angle=ha)


@dataclass(frozen=True)
class SunTimes:
    sunrise: datetime | None       # UTC; None for polar day or night
    solar_noon: datetime
    sunset: datetime | None
    polar: str | None = None       # "day" | "night" when the sun never sets/rises

    def as_dict(self) -> dict:
        f = (lambda d: d.isoformat() if d else None)
        return {"sunrise_utc": f(self.sunrise), "solar_noon_utc": f(self.solar_noon),
                "sunset_utc": f(self.sunset), "polar": self.polar}


def sun_times(lat: float, lon: float, day: datetime) -> SunTimes:
    """Sunrise, solar noon and sunset (UTC) for the UTC date of ``day``.

    Iterated once on the noon estimate, which brings sunrise and sunset
    to within about a minute of the full algorithm.
    """
    base = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)

    def noon_for(jd_guess: float) -> float:
        _, eot, _ = _core(jd_guess)
        return 720.0 - 4.0 * lon - eot                 # minutes UTC

    noon = noon_for(julian_day(base + timedelta(hours=12)))
    noon = noon_for(julian_day(base + timedelta(minutes=noon)))
    decl, _, _ = _core(julian_day(base + timedelta(minutes=noon)))
    latr, decr = lat * _RAD, decl * _RAD
    cos_ha = (math.cos(90.833 * _RAD) / (math.cos(latr) * math.cos(decr))
              - math.tan(latr) * math.tan(decr))
    solar_noon = base + timedelta(minutes=noon)
    if cos_ha > 1:
        return SunTimes(None, solar_noon, None, polar="night")
    if cos_ha < -1:
        return SunTimes(None, solar_noon, None, polar="day")
    ha = math.degrees(math.acos(cos_ha))
    return SunTimes(sunrise=base + timedelta(minutes=noon - 4 * ha),
                    solar_noon=solar_noon,
                    sunset=base + timedelta(minutes=noon + 4 * ha))
