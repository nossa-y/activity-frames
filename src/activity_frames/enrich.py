"""Deterministic event enrichment (port of Nocta's EnrichmentEngine.swift).

Raw `ui_events` rows have three reliability problems this module fixes
with plain code:

1. Stale app attribution: the recorder sometimes tags an event with the
   previously focused app. Fix: attribute each event to the nearest
   frame in time (frames carry authoritative app/window/url).
2. Keyboard-layout mismatch: some capture setups record physical key
   positions interpreted as QWERTY while the user types another layout
   (e.g. AZERTY). Fix: an optional, explicit translation map. Off by
   default; never guessed.
3. Anonymous clicks: many click events carry no element name. Fix:
   resolve the click's coordinates against the frame's accessibility /
   OCR element tree (exact containment, then tolerance, then a coarse
   screen-zone). Every resolution is tagged with how it was obtained.

Everything is confidence-tagged. Nothing is invented: an unresolvable
click stays unresolved.
"""
from __future__ import annotations

from bisect import bisect_left
from dataclasses import dataclass

from ._time import parse_epoch
from .db import Database

# ---- Keyboard layout translation --------------------------------------

# Maps are "recorded char -> actually typed char". The azerty map covers
# the mismatched positions between AZERTY hardware and QWERTY decoding;
# characters not in the map pass through unchanged.
LAYOUTS: dict[str, dict[str, str]] = {
    "azerty": {
        "q": "a", "a": "q", "w": "z", "z": "w",
        "Q": "A", "A": "Q", "W": "Z", "Z": "W",
        ";": "m", "m": ",", ",": ";",
    },
}


def decode_text(text: str, layout: str | dict[str, str] | None) -> str:
    """Translate recorded text through a layout map. None = identity."""
    if not text or layout is None:
        return text
    mapping = LAYOUTS[layout] if isinstance(layout, str) else layout
    return "".join(mapping.get(ch, ch) for ch in text)


# ---- Enriched events ---------------------------------------------------

@dataclass
class EnrichedEvent:
    epoch: float
    event_type: str           # click | key | text | app_switch | clipboard
    app: str                  # authoritative (nearest frame), falls back to event's
    window: str | None
    url: str | None
    frame_id: int | None
    frame_dt_ms: int | None   # distance to the attributing frame
    label: str | None         # element name (native or resolved)
    resolution: str           # native | exact | tolerance | zone | none
    text: str | None          # decoded text content (None unless requested)
    confidence: str           # high | medium | low


# Primary-monitor logical resolution used to normalize click coordinates.
# The capture engine stores element bounds normalized to 0..1; clicks are
# absolute points. Detected from the main display when possible (macOS
# CoreGraphics via ctypes, no dependency); override with $AFRAMES_SCREEN
# ("1728x1117") or the enrich_events() arguments. The fallback constant
# matches a 16-inch MacBook Pro.
def _detect_screen() -> tuple[float, float]:
    import os

    env = os.environ.get("AFRAMES_SCREEN", "")
    if "x" in env:
        try:
            w, h = env.lower().split("x", 1)
            return float(w), float(h)
        except ValueError:
            pass
    try:  # macOS main display, logical points
        import ctypes

        cg = ctypes.CDLL(
            "/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics"
        )
        cg.CGMainDisplayID.restype = ctypes.c_uint32
        cg.CGDisplayPixelsWide.restype = ctypes.c_size_t
        cg.CGDisplayPixelsHigh.restype = ctypes.c_size_t
        did = cg.CGMainDisplayID()
        w, h = cg.CGDisplayPixelsWide(did), cg.CGDisplayPixelsHigh(did)
        if w > 0 and h > 0:
            return float(w), float(h)
    except Exception:
        pass
    return 1728.0, 1117.0


DEFAULT_SCREEN_W, DEFAULT_SCREEN_H = _detect_screen()
_TOL_PX = 40.0  # tolerance ring in logical pixels


def nearest_index(sorted_epochs: list[float], target: float) -> int | None:
    """Index of the value nearest to target in a sorted list."""
    if not sorted_epochs:
        return None
    lo = bisect_left(sorted_epochs, target)
    if lo >= len(sorted_epochs):
        return len(sorted_epochs) - 1
    if lo > 0 and abs(sorted_epochs[lo - 1] - target) <= abs(sorted_epochs[lo] - target):
        return lo - 1
    return lo


# A click on a frame with no recorded element tree can often be resolved
# against a neighboring frame captured moments earlier/later that DOES have
# one (capture is event-driven; the tree is not extracted on every frame).
ELEMENT_RESCUE_WINDOW_S = 5.0


def enrich_events(
    db: Database,
    start_utc: str,
    end_utc: str,
    *,
    layout: str | dict[str, str] | None = None,
    include_text: bool = False,
    resolve_clicks: bool = True,
    screen_w: float = DEFAULT_SCREEN_W,
    screen_h: float = DEFAULT_SCREEN_H,
    element_rescue_window: float = ELEMENT_RESCUE_WINDOW_S,
) -> list[EnrichedEvent]:
    """Enrich all ui_events in [start_utc, end_utc). In-memory, read-only."""
    if not db.table_exists("ui_events"):
        return []

    frame_rows = db.rows(
        """
        SELECT id, timestamp, app_name, window_name, browser_url FROM frames
        WHERE timestamp >= ? AND timestamp < ?
          AND app_name IS NOT NULL AND app_name != ''
        ORDER BY timestamp ASC
        """,
        (start_utc, end_utc),
    )
    frames = [
        (int(r[0]), e, r[2] or "", r[3], r[4])
        for r in frame_rows
        if (e := parse_epoch(r[1])) > 0  # malformed rows would unsort the bisect
    ]
    frame_epochs = [f[1] for f in frames]

    # Sorted (epoch, frame_id) of frames that actually have an element tree,
    # used to rescue click resolution when the nearest frame has none.
    elem_frames: list[tuple[float, int]] = []
    if resolve_clicks and frames and db.table_exists("elements"):
        lo_id = min(f[0] for f in frames)
        hi_id = max(f[0] for f in frames)
        with_elements = {
            int(r[0])
            for r in db.rows(
                "SELECT DISTINCT frame_id FROM elements"
                " WHERE frame_id >= ? AND frame_id <= ?",
                (lo_id, hi_id),
            )
        }
        elem_frames = sorted(
            (f[1], f[0]) for f in frames if f[0] in with_elements
        )
    elem_epochs = [e for e, _ in elem_frames]

    event_rows = db.rows(
        """
        SELECT timestamp, event_type, x, y, element_name, element_role,
               text_content, app_name
        FROM ui_events
        WHERE timestamp >= ? AND timestamp < ?
        ORDER BY timestamp ASC
        """,
        (start_utc, end_utc),
    )

    elem_cache: dict[int, list[tuple[str, float, float, float, float]]] = {}
    out: list[EnrichedEvent] = []

    for ts, etype, x, y, elem_name, elem_role, text_content, event_app in event_rows:
        epoch = parse_epoch(ts or "")
        if epoch <= 0:
            # Same guard the frame rows get above. The SQL window is a string
            # comparison, so a partly-malformed stamp ("2026-07-04T99:99:99")
            # passes it and lands here as 0.0 -- and nearest_index would then
            # attribute the event to the window's FIRST frame: a real frame,
            # wrong answer, reported at whatever confidence the label implies.
            # An event we cannot place in time has no honest home.
            continue
        etype = etype or ""

        # 1. Authoritative context from the nearest frame.
        app = event_app or ""
        window = url = None
        frame_id = frame_dt_ms = None
        idx = nearest_index(frame_epochs, epoch)
        if idx is not None:
            fid, fepoch, fapp, fwindow, furl = frames[idx]
            frame_id = fid
            frame_dt_ms = int(abs(epoch - fepoch) * 1000)
            if fapp:
                app = fapp
            window, url = fwindow, furl

        # 3. Click-label resolution. Resolve against the nearest frame that
        # HAS an element tree (within the rescue window); the truly nearest
        # frame often has none because tree extraction is event-driven.
        native = (elem_name or "").strip()
        label: str | None = native or None
        resolution = "native" if native else "none"
        if (
            not native
            and resolve_clicks
            and etype == "click"
            and x is not None
            and y is not None
            and frame_id is not None
        ):
            res_frame_id = frame_id
            res_dt_ms = frame_dt_ms
            ei = nearest_index(elem_epochs, epoch)
            if ei is not None and abs(elem_epochs[ei] - epoch) <= element_rescue_window:
                res_frame_id = elem_frames[ei][1]
                res_dt_ms = int(abs(epoch - elem_epochs[ei]) * 1000)
            label, resolution = _resolve_click(
                db, res_frame_id, float(x), float(y), elem_cache, screen_w, screen_h
            )
            if resolution != "none":
                # Confidence should reflect distance to the frame we actually
                # resolved against, not the attribution frame.
                frame_dt_for_conf = res_dt_ms
            else:
                frame_dt_for_conf = frame_dt_ms
        else:
            frame_dt_for_conf = frame_dt_ms

        # 2. Layout decode (opt-in).
        text = None
        if text_content and include_text:
            text = decode_text(text_content, layout)

        out.append(
            EnrichedEvent(
                epoch=epoch,
                event_type=etype,
                app=app,
                window=window,
                url=url,
                frame_id=frame_id,
                frame_dt_ms=frame_dt_ms,
                label=label,
                resolution=resolution,
                text=text,
                confidence=_confidence(etype, resolution, frame_dt_for_conf),
            )
        )
    return out


def _resolve_click(
    db: Database,
    frame_id: int,
    x: float,
    y: float,
    cache: dict,
    screen_w: float,
    screen_h: float,
) -> tuple[str | None, str]:
    # Only the primary monitor is calibrated; off-screen coords stay unresolved.
    if not (0 <= x <= screen_w and 0 <= y <= screen_h):
        return None, "none"
    if not db.table_exists("elements"):
        return None, "none"
    xn, yn = x / screen_w, y / screen_h

    elems = cache.get(frame_id)
    if elems is None:
        rows = db.rows(
            """
            SELECT text, left_bound, top_bound, width_bound, height_bound
            FROM elements WHERE frame_id = ? AND text IS NOT NULL AND text != ''
            """,
            (frame_id,),
        )
        elems = [
            (r[0], float(r[1]), float(r[2]), float(r[3]), float(r[4]))
            for r in rows
            if r[1] is not None and r[2] is not None and r[3] is not None and r[4] is not None
        ]
        cache[frame_id] = elems
    if not elems:
        return None, "none"

    # Exact containment; smallest area wins (most specific element).
    best = None
    for text, l, t, w, h in elems:
        if l <= xn <= l + w and t <= yn <= t + h:
            area = w * h
            if best is None or area < best[1]:
                best = (text, area)
    if best:
        return _clean(best[0]), "exact"

    # Tolerance: expanded boxes (ring in logical pixels, normalized here).
    tol_n = _TOL_PX / screen_w
    tol = None
    for text, l, t, w, h in elems:
        if l - tol_n <= xn <= l + w + tol_n and t - tol_n <= yn <= t + h + tol_n:
            area = w * h
            if tol is None or area < tol[1]:
                tol = (text, area)
    if tol:
        return _clean(tol[0]), "tolerance"

    # Coarse screen zone (low confidence, still honest).
    if yn < 0.04:
        return "menu bar", "zone"
    if yn < 0.08:
        return "browser chrome", "zone"
    if xn < 0.15:
        return "left sidebar", "zone"
    return "main content", "zone"


def _clean(s: str) -> str:
    t = s.strip()
    return t[:80] + "..." if len(t) > 80 else t


def _confidence(etype: str, resolution: str, frame_dt_ms: int | None) -> str:
    if etype in ("text", "app_switch", "clipboard"):
        return "high"
    if etype == "click":
        if resolution == "native":
            return "high"
        if resolution == "exact":
            return "high" if (frame_dt_ms or 9999) < 2000 else "medium"
        if resolution == "tolerance":
            return "medium"
        return "low"
    return "low"
