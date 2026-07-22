"""T07 - instrument card: capture is NOT free.

VALIDATES the honesty of the cost accounting: the continuous recorder's real
footprint (GB/day, frames/day, accessibility-tree coverage that bounds replay
grounding, OCR duty). These are the terms the zero-token claim does NOT cover.
"""
from _lib import Test, run_script


def main():
    t = Test("t07_instrument_card", "capture overhead (the cost the compile path does not carry)")
    d = run_script("measure_corpus.py")
    card = d["instrument_card"]
    t.check("accessibility-tree coverage % (bounds replay grounding)",
            card["pct_frames_with_accessibility_tree"], lo=10, hi=100)
    t.check("OCR duty % (on-device model cost)", card["pct_frames_with_ocr_text"], lo=10, hi=100)
    t.check("GB per active day (storage cost)", card["gb_per_active_day"], lo=0.0, hi=5.0)
    t.check("active days", card["active_days"], lo=1)
    return t.finish({"instrument_card": card})


if __name__ == "__main__":
    main()
