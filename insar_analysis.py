"""Upload and descriptive analysis for already-processed InSAR displacement.

This is not a Sentinel-1 SLC interferometric processing chain or a geotechnical
stability assessment. CSV/GeoJSON are dependency-light; GeoTIFF needs rasterio.
"""
from io import BytesIO
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

MAX_UPLOAD_BYTES = 50 * 1024 * 1024
REQUIRED_COLUMNS = {"longitude", "latitude", "displacement_mm"}


class InSARInputError(ValueError):
    """Unsupported or invalid user-supplied InSAR product."""


def _clean_points(frame):
    frame = frame.copy()
    missing = REQUIRED_COLUMNS - set(frame.columns)
    if missing:
        raise InSARInputError("Missing required columns: " + ", ".join(sorted(missing)))
    for col in ("longitude", "latitude", "displacement_mm"):
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
    frame = frame.dropna(subset=["longitude", "latitude", "displacement_mm"])
    frame = frame[
        frame["longitude"].between(-180, 180) & frame["latitude"].between(-90, 90)
    ]
    if frame.empty:
        raise InSARInputError("No valid georeferenced displacement points were found")
    if "date" in frame.columns:
        frame["date"] = pd.to_datetime(frame["date"], utc=True, errors="coerce")
    return frame.reset_index(drop=True)


def _read_geojson(data: bytes):
    try:
        document = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InSARInputError(f"Invalid GeoJSON: {exc}") from exc
    features = document.get("features") if isinstance(document, dict) else None
    if not isinstance(features, list):
        raise InSARInputError("GeoJSON must be a FeatureCollection of Point features")
    rows = []
    for feature in features:
        geometry = feature.get("geometry") or {}
        if geometry.get("type") != "Point":
            continue
        coords = geometry.get("coordinates") or []
        if len(coords) < 2:
            continue
        props = feature.get("properties") or {}
        displacement = props.get("displacement_mm")
        if displacement is None:
            displacement = props.get("displacement")
        rows.append({
            "longitude": coords[0], "latitude": coords[1],
            "displacement_mm": displacement, "date": props.get("date"),
            "zone_id": props.get("zone_id"),
        })
    if not rows:
        raise InSARInputError("No Point features with displacement properties were found")
    return _clean_points(pd.DataFrame(rows))


def _read_geotiff(data: bytes):
    try:
        import rasterio
        from rasterio.io import MemoryFile
        from rasterio.warp import transform as transform_coords
    except ImportError as exc:
        raise InSARInputError("GeoTIFF support needs rasterio; install requirements-insar.txt") from exc
    try:
        with MemoryFile(data) as memory_file:
            with memory_file.open() as src:
                if src.crs is None:
                    raise InSARInputError("GeoTIFF needs a defined coordinate reference system (CRS)")
                band = src.read(1, masked=True)
                arr = np.asarray(band.astype("float32").filled(np.nan), dtype=float)
                finite = np.isfinite(arr)
                if not finite.any():
                    raise InSARInputError("GeoTIFF band has no valid displacement pixels")
                stride = max(1, int(math.ceil(math.sqrt(arr.size / 10000))))
                rows, cols = np.where(finite[::stride, ::stride])
                rows = rows * stride
                cols = cols * stride
                xs, ys = rasterio.transform.xy(src.transform, rows, cols, offset="center")
                if src.crs.to_epsg() == 4326:
                    lon, lat = xs, ys
                else:
                    lon, lat = transform_coords(src.crs, "EPSG:4326", xs, ys)
                values = arr[rows, cols]
                frame = pd.DataFrame({
                    "longitude": lon, "latitude": lat, "displacement_mm": values,
                })
                # Preserve every valid pixel for summary even though the map is sampled.
                summary_values = arr[finite]
                return _clean_points(frame), summary_values
    except InSARInputError:
        raise
    except Exception as exc:
        raise InSARInputError(f"Could not read GeoTIFF: {exc}") from exc


def load_insar_upload(filename: str, data: bytes):
    """Return normalized point data plus summary values from a processed product."""
    if len(data) > MAX_UPLOAD_BYTES:
        raise InSARInputError("Upload exceeds 50 MB prototype limit")
    suffix = Path(filename).suffix.lower()
    if suffix == ".csv":
        try:
            frame = pd.read_csv(BytesIO(data))
        except Exception as exc:
            raise InSARInputError(f"Could not read CSV: {exc}") from exc
        clean = _clean_points(frame)
        values = clean["displacement_mm"].to_numpy(dtype=float)
    elif suffix in {".geojson", ".json"}:
        clean = _read_geojson(data)
        values = clean["displacement_mm"].to_numpy(dtype=float)
    elif suffix in {".tif", ".tiff"}:
        clean, values = _read_geotiff(data)
    else:
        raise InSARInputError("Use a processed CSV, Point GeoJSON, or georeferenced displacement GeoTIFF")
    return clean, np.asarray(values, dtype=float)


def render_insar_upload(key_prefix: str):
    """Streamlit upload widget, descriptive results, map and optional time-series."""
    import streamlit as st

    st.markdown("#### User-uploaded InSAR displacement product")
    uploaded = st.file_uploader(
        "Upload processed displacement CSV, Point GeoJSON, or georeferenced GeoTIFF",
        type=["csv", "geojson", "json", "tif", "tiff"],
        key=f"{key_prefix}_upload",
    )
    st.caption(
        "Processed products only. CSV/GeoJSON columns: longitude, latitude, displacement_mm; optional date and zone_id. "
        "This view describes the uploaded values; it does not process raw Sentinel-1 SLC, set hazard thresholds, or assess ground stability."
    )
    if uploaded is None:
        return
    try:
        frame, summary_values = load_insar_upload(uploaded.name, uploaded.getvalue())
    except InSARInputError as exc:
        st.error(str(exc))
        return
    unit = st.selectbox("Displacement unit in uploaded file", ["mm", "cm", "m"], key=f"{key_prefix}_unit")
    scale = {"mm": 1.0, "cm": 10.0, "m": 1000.0}[unit]
    values_mm = summary_values * scale
    frame = frame.copy()
    frame["displacement_mm"] = pd.to_numeric(frame["displacement_mm"], errors="coerce") * scale
    a, b, c, d = st.columns(4)
    a.metric("Valid map points", f"{len(frame):,}")
    b.metric("Median displacement", f"{float(np.nanmedian(values_mm)):.2f} mm")
    c.metric("Minimum / maximum", f"{float(np.nanmin(values_mm)):.2f} / {float(np.nanmax(values_mm)):.2f} mm")
    d.metric("Uploaded file", uploaded.name)
    latest = frame.dropna(subset=["longitude", "latitude", "displacement_mm"])
    if "date" in latest.columns and latest["date"].notna().any():
        latest = latest.sort_values("date").drop_duplicates(["longitude", "latitude"], keep="last")
    st.map(latest.rename(columns={"latitude": "lat", "longitude": "lon"}), size=10)
    if "date" in frame.columns and frame["date"].notna().any():
        trend = frame.dropna(subset=["date"]).groupby("date")["displacement_mm"].agg("median").sort_index()
        if len(trend):
            st.markdown("**Median displacement by acquisition date**")
            st.line_chart(trend)
    st.dataframe(frame.head(1000), hide_index=True)
    st.info("InSAR descriptive visualization only—not an evacuation trigger, forecast, or engineering/geotechnical conclusion.")
