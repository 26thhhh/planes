#!/usr/bin/env python3
import json
import math
import sqlite3
import time
import urllib.request
from pathlib import Path
import sys

from location import ensure_location, handle_location_command

HOME = Path.home()
DATA_DIR = HOME / ".local/share/kanslor-aircraft"
DATABASE_PATH = DATA_DIR / "history.sqlite3"
CLASSIFICATION_PATH = HOME / ".config/kanslor-aircraft/classifications.json"


def distance_and_bearing(latitude, longitude, center_latitude, center_longitude):
    earth_radius_miles = 3958.7613
    lat1, lon1, lat2, lon2 = map(math.radians, (center_latitude, center_longitude, latitude, longitude))
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    distance = earth_radius_miles * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    bearing = (math.degrees(math.atan2(math.sin(dlon) * math.cos(lat2), math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlon))) + 360) % 360
    directions = ("N", "NE", "E", "SE", "S", "SW", "W", "NW")
    return distance, round(bearing), directions[round(bearing / 45) % 8]


def initialize_database(connection):
    connection.execute("""
        CREATE TABLE IF NOT EXISTS sightings (
            id INTEGER PRIMARY KEY,
            seen_at INTEGER NOT NULL,
            hex TEXT NOT NULL,
            callsign TEXT,
            registration TEXT,
            aircraft_type TEXT,
            model TEXT,
            manufacturer TEXT,
            owner TEXT,
            operator TEXT,
            classification TEXT,
            latitude REAL NOT NULL,
            longitude REAL NOT NULL,
            altitude TEXT,
            groundspeed REAL,
            track REAL,
            distance_miles REAL NOT NULL,
            bearing INTEGER NOT NULL,
            direction TEXT NOT NULL
        )
    """)
    columns = {row[1] for row in connection.execute("PRAGMA table_info(sightings)")}
    if "model" not in columns:
        connection.execute("ALTER TABLE sightings ADD COLUMN model TEXT")
    if "manufacturer" not in columns:
        connection.execute("ALTER TABLE sightings ADD COLUMN manufacturer TEXT")
    connection.execute("""
        CREATE TABLE IF NOT EXISTS aircraft_metadata (
            hex TEXT PRIMARY KEY,
            model TEXT,
            manufacturer TEXT,
            owner TEXT,
            operator TEXT,
            classification TEXT,
            fetched_at INTEGER NOT NULL
        )
    """)
    metadata_columns = {row[1] for row in connection.execute("PRAGMA table_info(aircraft_metadata)")}
    for column in ("owner", "operator", "classification"):
        if column not in metadata_columns:
            connection.execute(f"ALTER TABLE aircraft_metadata ADD COLUMN {column} TEXT")
    connection.execute("CREATE INDEX IF NOT EXISTS sightings_seen_at ON sightings(seen_at)")
    connection.execute("CREATE INDEX IF NOT EXISTS sightings_hex_seen_at ON sightings(hex, seen_at)")


def classify_aircraft(owner, operator):
    identity = f"{owner} {operator}".upper()
    military_terms = (
        "AIR FORCE", "USAF", "UNITED STATES ARMY", "US ARMY", "UNITED STATES NAVY",
        "US NAVY", "MARINE CORPS", "COAST GUARD", "NATIONAL GUARD", "DEPARTMENT OF DEFENSE",
    )
    law_enforcement_terms = (
        "POLICE", "SHERIFF", "DEPARTMENT OF PUBLIC SAFETY", "HIGHWAY PATROL", "STATE PATROL", "TEXAS DPS",
    )
    if any(term in identity for term in military_terms):
        return "MIL"
    if any(term in identity for term in law_enforcement_terms):
        return "LE"
    return None


def aircraft_metadata(connection, hex_code, overrides):
    cached = connection.execute(
        "SELECT model, manufacturer, owner, operator, classification, fetched_at FROM aircraft_metadata WHERE hex = ?", (hex_code,)
    ).fetchone()
    if cached and cached[2] is not None and cached[5] > time.time() - 30 * 86400:
        return cached[:5]

    model = manufacturer = None
    owner = operator = ""
    try:
        request = urllib.request.Request(
            f"https://api.adsbdb.com/v0/aircraft/{hex_code.lower()}",
            headers={"User-Agent": "kanslor-aircraft-watch/1.0"},
        )
        with urllib.request.urlopen(request, timeout=15) as response:
            aircraft = json.load(response).get("response", {}).get("aircraft", {})
        model = aircraft.get("type") or aircraft.get("icao_type")
        manufacturer = aircraft.get("manufacturer")
        owner = aircraft.get("registered_owner") or ""
        operator = aircraft.get("registered_owner_operator_flag_code") or ""
    except (OSError, TimeoutError, json.JSONDecodeError):
        pass

    classification = overrides.get(hex_code) or classify_aircraft(owner, operator)
    connection.execute(
        "INSERT INTO aircraft_metadata(hex, model, manufacturer, owner, operator, classification, fetched_at) VALUES (?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(hex) DO UPDATE SET model = excluded.model, manufacturer = excluded.manufacturer, owner = excluded.owner, operator = excluded.operator, classification = excluded.classification, fetched_at = excluded.fetched_at",
        (hex_code, model, manufacturer, owner, operator, classification, int(time.time())),
    )
    return model, manufacturer, owner, operator, classification


def fetch_aircraft(config):
    radius_nm = config["radius_miles"] / 1.15078
    url = f"https://api.adsb.lol/v2/lat/{config['latitude']}/lon/{config['longitude']}/dist/{radius_nm:.3f}"
    request = urllib.request.Request(url, headers={"User-Agent": "kanslor-aircraft-watch/1.0"})
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.load(response).get("ac", [])


def main():
    if handle_location_command(sys.argv[1:]):
        return
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    config = ensure_location(interactive=sys.stdin.isatty())
    overrides = json.loads(CLASSIFICATION_PATH.read_text(encoding="utf-8")).get("overrides", {}) if CLASSIFICATION_PATH.exists() else {}
    seen_at = int(time.time())
    with sqlite3.connect(DATABASE_PATH, timeout=30) as connection:
        initialize_database(connection)
        rows = []
        for aircraft in fetch_aircraft(config):
            latitude = aircraft.get("lat")
            longitude = aircraft.get("lon")
            hex_code = aircraft.get("hex")
            if latitude is None or longitude is None or not hex_code:
                continue
            hex_code = hex_code.upper()
            distance, bearing, direction = distance_and_bearing(latitude, longitude, config["latitude"], config["longitude"])
            if distance > config["radius_miles"]:
                continue
            model, manufacturer, owner, operator, classification = aircraft_metadata(connection, hex_code, overrides)
            rows.append((
                seen_at,
                hex_code,
                (aircraft.get("flight") or "").strip() or None,
                (aircraft.get("r") or "").strip() or None,
                (aircraft.get("t") or "").strip() or None,
                model,
                manufacturer,
                latitude,
                longitude,
                str(aircraft.get("alt_baro") if aircraft.get("alt_baro") is not None else "N/A"),
                aircraft.get("gs"),
                aircraft.get("track"),
                round(distance, 2),
                bearing,
                direction,
            ))
        connection.executemany("""
            INSERT INTO sightings (
                seen_at, hex, callsign, registration, aircraft_type, model, manufacturer,
                latitude, longitude, altitude, groundspeed, track, distance_miles, bearing, direction
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, rows)

        for (hex_code,) in connection.execute(
            "SELECT DISTINCT hex FROM sightings WHERE hex NOT IN (SELECT hex FROM aircraft_metadata WHERE owner IS NOT NULL) LIMIT 10"
        ):
            aircraft_metadata(connection, hex_code, overrides)
        connection.execute("""
            UPDATE sightings
            SET model = (SELECT model FROM aircraft_metadata WHERE aircraft_metadata.hex = sightings.hex),
                manufacturer = (SELECT manufacturer FROM aircraft_metadata WHERE aircraft_metadata.hex = sightings.hex)
            WHERE model IS NULL
        """)
    (DATA_DIR / "last_success").write_text(str(int(time.time())), encoding="utf-8")


if __name__ == "__main__":
    main()
