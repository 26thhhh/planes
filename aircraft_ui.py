#!/usr/bin/env python3
import curses
import sqlite3
import time
import sys
from datetime import datetime
from pathlib import Path

from location import ensure_location, handle_location_command

DATABASE_PATH = Path.home() / ".local/share/kanslor-aircraft/history.sqlite3"
FILTERS = (("24H", 86400), ("7D", 604800), ("30D", 2592000), ("ALL", None))


def format_time(timestamp):
    return datetime.fromtimestamp(timestamp).strftime("%b %d %H:%M")


def summaries(connection, since, classification_filter):
    clauses = []
    params = []
    if since:
        clauses.append("s.seen_at >= ?")
        params.append(since)
    if classification_filter:
        clauses.append("s.hex IN (SELECT hex FROM aircraft_metadata WHERE classification = ?)")
        params.append(classification_filter)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    return connection.execute(f"""
        SELECT s.hex,
               COALESCE(NULLIF(s.callsign, ''), NULLIF(s.registration, ''), s.hex) AS label,
               s.registration,
               COALESCE((SELECT model FROM aircraft_metadata m WHERE m.hex = s.hex), s.aircraft_type) AS model,
               (SELECT classification FROM aircraft_metadata m WHERE m.hex = s.hex) AS classification,
               MIN(s.seen_at) AS first_seen, MAX(s.seen_at) AS last_seen,
               COUNT(*) AS sightings, MIN(s.distance_miles) AS closest,
               (SELECT direction FROM sightings x WHERE x.hex = s.hex ORDER BY x.seen_at DESC LIMIT 1) AS direction,
               (SELECT track FROM sightings x WHERE x.hex = s.hex ORDER BY x.seen_at DESC LIMIT 1) AS track
        FROM sightings s {where}
        GROUP BY s.hex
        ORDER BY last_seen DESC
    """, params).fetchall()


def draw_line(screen, row, text, selected=False, color=0):
    height, width = screen.getmaxyx()
    if row >= height:
        return
    try:
        screen.addnstr(row, 0, text.ljust(max(width - 1, 1)), max(width - 1, 1), curses.A_REVERSE if selected else color)
    except curses.error:
        pass


def summary_view(screen, connection, filter_index, classification_filter, selected):
    screen.erase()
    now = int(time.time())
    label, duration = FILTERS[filter_index]
    rows = summaries(connection, now - duration if duration else None, classification_filter)
    class_label = classification_filter or "ALL"
    draw_line(screen, 0, f"K A N S L O R  //  AIRCRAFT WATCH   [{label} | {class_label}]", color=curses.color_pair(1))
    draw_line(screen, 1, "UP/DOWN select  ENTER detail  1/7/3/A range  M military  L law enforcement  C clear filter  ESC exit")
    draw_line(screen, 3, "CALLSIGN / REG          TYPE    LAST SEEN        CLOSEST   POSITION / HEADING", color=curses.color_pair(2))
    height, _ = screen.getmaxyx()
    visible = max(height - 5, 1)
    start = max(0, selected - visible + 1)
    for index, row in enumerate(rows[start:start + visible], start):
        hex_code, label_text, registration, model, classification, first_seen, last_seen, sightings, closest, direction, track = row
        identity = label_text if not registration or registration == label_text else f"{label_text} / {registration}"
        if classification:
            identity = f"[{classification}] {identity}"
        heading = f"{track:.0f} deg" if track is not None else "--"
        text = f"{identity:<23.23} {model or '--':<7.7} {format_time(last_seen):<16} {closest:>5.2f} mi  {direction or '--':<2} / {heading}"
        color = curses.color_pair(3) if classification == "MIL" else curses.color_pair(4) if classification == "LE" else 0
        draw_line(screen, index - start + 4, text, index == selected, color)
    if not rows:
        draw_line(screen, 5, "No sightings stored yet. The collector records aircraft inside five miles once per minute.")
    screen.refresh()
    return rows


def detail_view(screen, connection, hex_code):
    row = connection.execute("""
        SELECT hex, callsign, registration, aircraft_type,
               (SELECT model FROM aircraft_metadata m WHERE m.hex = sightings.hex),
                (SELECT manufacturer FROM aircraft_metadata m WHERE m.hex = sightings.hex),
                (SELECT owner FROM aircraft_metadata m WHERE m.hex = sightings.hex),
                (SELECT operator FROM aircraft_metadata m WHERE m.hex = sightings.hex),
                (SELECT classification FROM aircraft_metadata m WHERE m.hex = sightings.hex),
                MIN(seen_at), MAX(seen_at), COUNT(*), MIN(distance_miles)
        FROM sightings WHERE hex = ?
    """, (hex_code,)).fetchone()
    sightings = connection.execute("""
        SELECT seen_at, direction, bearing, track, altitude, groundspeed, distance_miles
        FROM sightings WHERE hex = ? ORDER BY seen_at DESC LIMIT 14
    """, (hex_code,)).fetchall()
    while True:
        screen.erase()
        identity = row[1] or row[2] or row[0]
        draw_line(screen, 0, f"AIRCRAFT DETAIL // {identity}", color=curses.color_pair(1))
        draw_line(screen, 1, "ESC return to history")
        draw_line(screen, 3, f"ICAO: {row[0]}   REG: {row[2] or '--'}   TYPE: {row[3] or '--'}")
        draw_line(screen, 4, f"MODEL: {row[4] or '--'}   MAKER: {row[5] or '--'}")
        draw_line(screen, 5, f"CLASS: {row[8] or 'UNCLASSIFIED'}   OWNER: {row[6] or '--'}")
        draw_line(screen, 6, f"OPERATOR: {row[7] or '--'}")
        draw_line(screen, 7, f"FIRST: {format_time(row[9])}   LAST: {format_time(row[10])}   SIGHTINGS: {row[11]}   CLOSEST: {row[12]:.2f} mi")
        draw_line(screen, 9, "TIME             POSITION      HEADING    ALTITUDE   SPEED     DISTANCE", color=curses.color_pair(2))
        for index, sighting in enumerate(sightings, 10):
            seen_at, direction, bearing, track, altitude, groundspeed, distance = sighting
            heading = f"{track:.0f} deg" if track is not None else "--"
            speed = f"{groundspeed:.0f} kt" if groundspeed is not None else "--"
            draw_line(screen, index, f"{format_time(seen_at):<16} {direction} ({bearing:03d})     {heading:<10} {altitude:<10} {speed:<9} {distance:.2f} mi")
        screen.refresh()
        key = screen.getch()
        if key in (27, ord("q"), ord("Q")):
            return


def app(screen):
    curses.curs_set(0)
    curses.start_color()
    curses.use_default_colors()
    curses.init_pair(1, curses.COLOR_YELLOW, -1)
    curses.init_pair(2, curses.COLOR_CYAN, -1)
    curses.init_pair(3, curses.COLOR_RED, -1)
    curses.init_pair(4, curses.COLOR_BLUE, -1)
    filter_index = 2
    classification_filter = None
    selected = 0
    while True:
        if not DATABASE_PATH.exists():
            screen.erase()
            draw_line(screen, 0, "K A N S L O R  //  AIRCRAFT WATCH", color=curses.color_pair(1))
            draw_line(screen, 2, "No database yet. The collector will create it on its next one-minute run.")
            draw_line(screen, 4, "ESC exit")
            screen.refresh()
            if screen.getch() == 27:
                return
            continue
        with sqlite3.connect(DATABASE_PATH) as connection:
            rows = summary_view(screen, connection, filter_index, classification_filter, selected)
            key = screen.getch()
            if key in (27, ord("q"), ord("Q")):
                return
            if key in (curses.KEY_DOWN, ord("j")) and selected < len(rows) - 1:
                selected += 1
            elif key in (curses.KEY_UP, ord("k")) and selected > 0:
                selected -= 1
            elif key in (10, 13, curses.KEY_ENTER) and rows:
                detail_view(screen, connection, rows[selected][0])
            elif key == ord("1"):
                filter_index, selected = 0, 0
            elif key == ord("7"):
                filter_index, selected = 1, 0
            elif key == ord("3"):
                filter_index, selected = 2, 0
            elif key in (ord("a"), ord("A")):
                filter_index, classification_filter, selected = 3, None, 0
            elif key in (ord("m"), ord("M")):
                classification_filter, selected = "MIL", 0
            elif key in (ord("l"), ord("L")):
                classification_filter, selected = "LE", 0
            elif key in (ord("c"), ord("C")):
                classification_filter, selected = None, 0


if __name__ == "__main__":
    if not handle_location_command(sys.argv[1:]):
        ensure_location(interactive=True)
        curses.wrapper(app)
