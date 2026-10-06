"""Pinpoint: the exact spot, by finding the same scene in a geotagged photograph.

The locator says roughly where; this tries to say exactly where. It
looks for photographs taken near the estimate whose content is the same
physical scene as the query -- the same facade, the same corner, the
same landmark -- and when one is found, the query was taken where that
photograph was, give or take the distance between two people
photographing the same thing.

"The same scene" is decided by geometry, not by resemblance. Thousands
of local features (SIFT) are matched between the two images and a
fundamental matrix is fitted to them with MAGSAC: only matches that one
consistent pair of camera positions explains survive. Two photographs of
different streets that merely look alike do not produce that geometry;
the threshold for "match" is set well above the best that unrelated
photographs reach (scripts/calibrate_pinpoint.py), and a match below it
is reported as unconfirmed, not as a location.

References come from Wikimedia Commons (freely licensed, geotagged by the
photographer), and from Mapillary street-level imagery if the user has
configured their own free Mapillary token. Only the candidate
coordinates go out -- to ask which photographs exist near them; the
query image never leaves the machine. Reference thumbnails and their
features are cached under the data folder, and every match shows its
photograph's author and licence.
"""
