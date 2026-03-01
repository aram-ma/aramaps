"""
DXF → GeoJSON converter for araMaps.

Converts AutoCAD DXF files to GeoJSON with coordinate reprojection.
Supports: LINE, LWPOLYLINE, POLYLINE, CIRCLE, ARC, POINT, TEXT, MTEXT.

Usage:
    python dxf_to_geojson.py <input.dxf> <output.geojson> --epsg <source_epsg>

Example:
    python dxf_to_geojson.py site.dxf site.geojson --epsg 32638
"""

import argparse, json, math, sys
import ezdxf
from pyproj import Transformer


def make_transformer(source_epsg):
    """Create a coordinate transformer from source CRS to WGS84."""
    return Transformer.from_crs(
        f"EPSG:{source_epsg}", "EPSG:4326", always_xy=True
    )


def reproject(transformer, x, y):
    """Reproject a single point. Returns [lng, lat]."""
    lng, lat = transformer.transform(x, y)
    return [round(lng, 7), round(lat, 7)]


def circle_to_polygon(cx, cy, radius, transformer, segments=64):
    """Convert a circle to a GeoJSON polygon (ring of points)."""
    coords = []
    for i in range(segments + 1):
        angle = 2 * math.pi * i / segments
        x = cx + radius * math.cos(angle)
        y = cy + radius * math.sin(angle)
        coords.append(reproject(transformer, x, y))
    return coords


def arc_to_linestring(cx, cy, radius, start_angle, end_angle, transformer, segments=32):
    """Convert an arc to a GeoJSON linestring."""
    # Normalize angles to radians
    sa = math.radians(start_angle)
    ea = math.radians(end_angle)
    if ea <= sa:
        ea += 2 * math.pi

    coords = []
    for i in range(segments + 1):
        angle = sa + (ea - sa) * i / segments
        x = cx + radius * math.cos(angle)
        y = cy + radius * math.sin(angle)
        coords.append(reproject(transformer, x, y))
    return coords


def entity_color(entity):
    """Get the DXF color index as a string."""
    try:
        c = entity.dxf.color
        if c and c != 256:  # 256 = BYLAYER
            return str(c)
    except Exception:
        pass
    return None


def in_utm_range(x, y):
    """Check if coordinates are plausible UTM values for Iraq (not local/paper space).
    Iraq UTM zones 37N-39N: Easting 150000-850000, Northing 3100000-4300000."""
    return 150000 < x < 850000 and 3100000 < y < 4300000


def to_wcs(entity, point):
    """Convert a point from OCS (Object Coordinate System) to WCS (World Coordinate System).
    Entities with non-default extrusion vectors store coordinates in a tilted space."""
    ocs = entity.ocs()
    wcs = ocs.to_wcs(point)
    return wcs.x, wcs.y


def convert_dxf(input_path, source_epsg):
    """Convert a DXF file to a list of GeoJSON features."""
    doc = ezdxf.readfile(input_path)
    msp = doc.modelspace()
    transformer = make_transformer(source_epsg)

    features = []
    skipped = {}
    filtered = 0

    for entity in msp:
        dxftype = entity.dxftype()
        layer = entity.dxf.layer if hasattr(entity.dxf, 'layer') else "0"
        props = {"layer": layer, "type": dxftype}
        color = entity_color(entity)
        if color:
            props["color"] = color

        try:
            if dxftype == "LINE":
                sx, sy = to_wcs(entity, entity.dxf.start)
                ex, ey = to_wcs(entity, entity.dxf.end)
                if not in_utm_range(sx, sy) or not in_utm_range(ex, ey):
                    filtered += 1; continue
                coords = [
                    reproject(transformer, sx, sy),
                    reproject(transformer, ex, ey),
                ]
                features.append(make_feature("LineString", coords, props))

            elif dxftype == "LWPOLYLINE":
                raw_points = list(entity.get_points(format="xy"))
                if len(raw_points) < 2:
                    continue
                from ezdxf.math import Vec3
                points = [to_wcs(entity, Vec3(x, y, 0)) for x, y in raw_points]
                points = [(x, y) for x, y in points if in_utm_range(x, y)]
                if len(points) < 2:
                    filtered += 1; continue
                coords = [reproject(transformer, x, y) for x, y in points]
                if entity.closed:
                    coords.append(coords[0])
                    features.append(make_feature("Polygon", [coords], props))
                else:
                    features.append(make_feature("LineString", coords, props))

            elif dxftype == "POLYLINE":
                points = [to_wcs(entity, v.dxf.location) for v in entity.vertices]
                if len(points) < 2:
                    continue
                points = [(x, y) for x, y in points if in_utm_range(x, y)]
                if len(points) < 2:
                    filtered += 1; continue
                coords = [reproject(transformer, x, y) for x, y in points]
                if entity.is_closed:
                    coords.append(coords[0])
                    features.append(make_feature("Polygon", [coords], props))
                else:
                    features.append(make_feature("LineString", coords, props))

            elif dxftype == "CIRCLE":
                cx, cy = to_wcs(entity, entity.dxf.center)
                if not in_utm_range(cx, cy):
                    filtered += 1; continue
                radius = entity.dxf.radius
                coords = circle_to_polygon(cx, cy, radius, transformer)
                features.append(make_feature("Polygon", [coords], props))

            elif dxftype == "ARC":
                cx, cy = to_wcs(entity, entity.dxf.center)
                if not in_utm_range(cx, cy):
                    filtered += 1; continue
                coords = arc_to_linestring(
                    cx, cy, entity.dxf.radius,
                    entity.dxf.start_angle, entity.dxf.end_angle,
                    transformer
                )
                if len(coords) >= 2:
                    features.append(make_feature("LineString", coords, props))

            elif dxftype == "POINT":
                px, py = to_wcs(entity, entity.dxf.location)
                if not in_utm_range(px, py):
                    filtered += 1; continue
                coord = reproject(transformer, px, py)
                features.append(make_feature("Point", coord, props))

            elif dxftype == "TEXT":
                halign = entity.dxf.get('halign', 0)
                valign = entity.dxf.get('valign', 0)
                if halign != 0 or valign != 0:
                    pt = entity.dxf.get('align_point', entity.dxf.insert)
                else:
                    pt = entity.dxf.insert
                tx, ty = to_wcs(entity, pt)
                if not in_utm_range(tx, ty):
                    filtered += 1; continue
                coord = reproject(transformer, tx, ty)
                try:
                    props["text"] = entity.dxf.text
                except Exception:
                    props["text"] = ""
                features.append(make_feature("Point", coord, props))

            elif dxftype == "MTEXT":
                mx, my = to_wcs(entity, entity.dxf.insert)
                if not in_utm_range(mx, my):
                    filtered += 1; continue
                coord = reproject(transformer, mx, my)
                try:
                    props["text"] = entity.text
                except Exception:
                    props["text"] = ""
                features.append(make_feature("Point", coord, props))

            elif dxftype == "INSERT":
                ix, iy = to_wcs(entity, entity.dxf.insert)
                if not in_utm_range(ix, iy):
                    filtered += 1; continue
                coord = reproject(transformer, ix, iy)
                try:
                    props["block"] = entity.dxf.name
                except Exception:
                    pass
                features.append(make_feature("Point", coord, props))

            elif dxftype == "DIMENSION":
                try:
                    d1x, d1y = to_wcs(entity, entity.dxf.defpoint)
                    d2x, d2y = to_wcs(entity, entity.dxf.defpoint2)
                    if not in_utm_range(d1x, d1y) or not in_utm_range(d2x, d2y):
                        filtered += 1; continue
                    coords = [
                        reproject(transformer, d1x, d1y),
                        reproject(transformer, d2x, d2y),
                    ]
                    features.append(make_feature("LineString", coords, props))
                except Exception:
                    pass

            else:
                skipped[dxftype] = skipped.get(dxftype, 0) + 1

        except Exception as e:
            skipped[dxftype] = skipped.get(dxftype, 0) + 1

    return features, skipped, filtered


def make_feature(geom_type, coordinates, properties):
    """Create a GeoJSON Feature."""
    return {
        "type": "Feature",
        "geometry": {"type": geom_type, "coordinates": coordinates},
        "properties": properties,
    }


def main():
    parser = argparse.ArgumentParser(description="Convert DXF to GeoJSON")
    parser.add_argument("input", help="Input DXF file")
    parser.add_argument("output", help="Output GeoJSON file")
    parser.add_argument("--epsg", type=int, required=True,
                        help="Source EPSG code (e.g., 32638 for UTM Zone 38N)")
    args = parser.parse_args()

    print(f"Reading: {args.input}")
    features, skipped, filtered = convert_dxf(args.input, args.epsg)

    geojson = {
        "type": "FeatureCollection",
        "features": features,
    }

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(geojson, f)

    print(f"Written: {args.output}")
    print(f"  Features: {len(features)}")
    if filtered:
        print(f"  Filtered (out-of-range coords): {filtered}")
    if skipped:
        print(f"  Skipped entity types: {skipped}")

    # Print bounding box
    lngs, lats = [], []
    for feat in features:
        geom = feat["geometry"]
        if geom["type"] == "Point":
            lngs.append(geom["coordinates"][0])
            lats.append(geom["coordinates"][1])
        elif geom["type"] == "LineString":
            for c in geom["coordinates"]:
                lngs.append(c[0])
                lats.append(c[1])
        elif geom["type"] == "Polygon":
            for ring in geom["coordinates"]:
                for c in ring:
                    lngs.append(c[0])
                    lats.append(c[1])
    if lngs:
        print(f"  Bounds: [{min(lngs):.6f}, {min(lats):.6f}] to [{max(lngs):.6f}, {max(lats):.6f}]")
        print(f"  Center: [{(min(lngs)+max(lngs))/2:.6f}, {(min(lats)+max(lats))/2:.6f}]")


if __name__ == "__main__":
    main()
