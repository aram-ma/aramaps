"""
aramaps local server.

Serves static files + DXF upload/conversion API.

Usage:
    python serve.py
"""

import json, os, re, tempfile
from pathlib import Path
from fastapi import FastAPI, UploadFile, File, Form
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
import uvicorn

ROOT = Path(__file__).parent
OVERLAYS = ROOT / "overlays"
OVERLAYS.mkdir(exist_ok=True)

app = FastAPI()


def slugify(name):
    name = Path(name).stem.lower()
    name = re.sub(r'[^a-z0-9]+', '-', name)
    return name.strip('-')


def compute_bounds(features):
    lngs, lats = [], []
    for feat in features:
        geom = feat["geometry"]
        if geom["type"] == "Point":
            lngs.append(geom["coordinates"][0])
            lats.append(geom["coordinates"][1])
        elif geom["type"] == "LineString":
            for c in geom["coordinates"]:
                lngs.append(c[0]); lats.append(c[1])
        elif geom["type"] == "Polygon":
            for ring in geom["coordinates"]:
                for c in ring:
                    lngs.append(c[0]); lats.append(c[1])
    if not lngs:
        return None, None
    bounds = [min(lngs), min(lats), max(lngs), max(lats)]
    center = [(bounds[0] + bounds[2]) / 2, (bounds[1] + bounds[3]) / 2]
    return bounds, center


@app.get("/api/overlays")
def list_overlays():
    overlays = []
    for f in sorted(OVERLAYS.glob("*.geojson")):
        overlays.append({
            "name": f.stem,
            "file": f.name,
            "size_kb": f.stat().st_size // 1024
        })
    return {"overlays": overlays}


@app.post("/api/upload-dxf")
async def upload_dxf(file: UploadFile = File(...), epsg: int = Form(32638)):
    from dxf_to_geojson import convert_dxf

    # Save uploaded DXF to temp file
    content = await file.read()
    tmp = tempfile.NamedTemporaryFile(suffix=".dxf", delete=False)
    tmp.write(content)
    tmp.close()

    try:
        features, skipped, filtered = convert_dxf(tmp.name, epsg)
    finally:
        os.unlink(tmp.name)

    if not features:
        return JSONResponse({"error": "No features found (check EPSG code)"}, status_code=400)

    slug = slugify(file.filename)
    geojson = {"type": "FeatureCollection", "features": features}
    out_path = OVERLAYS / f"{slug}.geojson"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(geojson, f)

    bounds, center = compute_bounds(features)
    return {
        "name": slug,
        "file": f"{slug}.geojson",
        "features": len(features),
        "bounds": bounds,
        "center": center,
        "skipped": skipped,
        "filtered": filtered
    }


# Serve overlay GeoJSON files
@app.get("/overlays/{filename}")
def serve_overlay(filename: str):
    path = OVERLAYS / filename
    if not path.exists():
        return JSONResponse({"error": "not found"}, status_code=404)
    return FileResponse(path, media_type="application/json")


@app.delete("/api/overlays/{name}")
def delete_overlay(name: str):
    path = OVERLAYS / f"{name}.geojson"
    if path.exists():
        path.unlink()
        return {"deleted": name}
    return JSONResponse({"error": "not found"}, status_code=404)


# Serve all other static files (index.html, studio.html, overlay.html, etc.)
app.mount("/", StaticFiles(directory=str(ROOT), html=True), name="static")


if __name__ == "__main__":
    print("aramaps server starting...")
    print("  http://localhost:8000/             — main map")
    print("  http://localhost:8000/studio.html  — studio")
    print("  http://localhost:8000/overlay.html — overlay")
    uvicorn.run(app, host="0.0.0.0", port=8000)
