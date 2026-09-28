"""Local geographic data: buildings, streets, water, and what they rest on.

The 3D ground view needs geometry -- a footprint with a height can be
orbited, a satellite photograph of a rooftop cannot -- and it needs it
without leaning on somebody else's server for every landing. The public
Overpass instances say so explicitly: they are not a backend for
applications. So the data lives here, on the user's own disk, built
from files whose licences allow exactly that:

  OpenStreetMap        ODbL 1.0, via Geofabrik's regional extracts of
                       the planet file. Buildings, heights where mapped,
                       streets, water, parks, named places.
  Google Open Buildings  CC BY 4.0 or ODbL 1.0, the user's choice.
                       Footprints where OpenStreetMap is thin, read by
                       range request from the FlatGeobuf copies so a
                       landing costs megabytes rather than gigabytes.

Every feature keeps its source, and every height says how it was
known. Over 99% of the buildings in the Southern India extract carry no
height at all; a view that drew them at invented heights would look
better and be lying.
"""
