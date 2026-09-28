"""Chronolocation: what the light in a photograph says about when.

Everything else in Mapify asks where. This asks when -- and, turned
around, uses "when" to test "where": the sun is in exactly one place in
the sky for a given instant and location, so a photograph's light, its
shadows and its capture time either agree with a candidate location or
they do not.

What it can honestly do
-----------------------

  * compute the sun and Moon for any place and instant (``solar``,
    ``moon``), to well under a degree;
  * resolve a photograph's capture time into a real instant when the
    file allows it, and say how much that depends on assumptions
    (``capture_time``);
  * check a photograph's light, season and sky against astronomy for a
    candidate place, and report agreement or contradiction.

What it cannot do, and will not pretend to
------------------------------------------

Recover a time of day from an arbitrary photograph. That needs the sun
or a clean shadow, a known camera direction and a known object height,
and most photographs have none of them. When they are missing the output
is "not determinable" -- never a guess dressed as a finding.
"""
