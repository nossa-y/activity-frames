import ctypes
from ctypes import wintypes
import psutil
import time
from datetime import datetime, timezone
import sqlite3
from pathlib import Path


# ============================================================
# WINDOWS API
# ============================================================

user32 = ctypes.windll.user32


# ============================================================
# DATABASE LOCATION
# ============================================================

# recorder.py:
# activity-frames/src/activity_frames/windows_recorder/recorder.py
#
# parents[3] = activity-frames project root
PROJECT_ROOT = Path(__file__).resolve().parents[3]

DATA_DIR = PROJECT_ROOT / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = DATA_DIR / "activity.db"


# ============================================================
# DATABASE SETUP
# ============================================================

def setup_database():

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS frames (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TIMESTAMP NOT NULL,
            app_name TEXT,
            window_name TEXT,
            focused BOOLEAN,
            browser_url TEXT,
            document_path TEXT,
            device_name TEXT NOT NULL DEFAULT 'monitor_1'
        )
    """)

    conn.commit()
    conn.close()


# ============================================================
# SAVE ACTIVITY
# ============================================================

def save_activity(timestamp, application, window_title):

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute("""
        INSERT INTO frames (
            timestamp,
            app_name,
            window_name,
            focused,
            browser_url,
            document_path,
            device_name
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        timestamp,
        application,
        window_title,
        1,
        None,
        None,
        "monitor_1"
    ))

    conn.commit()
    conn.close()


# ============================================================
# GET ACTIVE WINDOW
# ============================================================

def get_active_window_info():

    hwnd = user32.GetForegroundWindow()

    if not hwnd:
        return "Unknown", ""

    # --------------------------------------------------------
    # Get window title
    # --------------------------------------------------------

    length = user32.GetWindowTextLengthW(hwnd)

    title_buffer = ctypes.create_unicode_buffer(length + 1)

    user32.GetWindowTextW(
        hwnd,
        title_buffer,
        length + 1
    )

    window_title = title_buffer.value


    # --------------------------------------------------------
    # Get process ID
    # --------------------------------------------------------

    process_id = wintypes.DWORD()

    user32.GetWindowThreadProcessId(
        hwnd,
        ctypes.byref(process_id)
    )


    # --------------------------------------------------------
    # Get application/process name
    # --------------------------------------------------------

    try:

        process = psutil.Process(process_id.value)

        app_name = process.name()

    except (
        psutil.NoSuchProcess,
        psutil.AccessDenied,
        psutil.ZombieProcess
    ):

        app_name = "Unknown"


    return app_name, window_title


# ============================================================
# UTC TIMESTAMP
# ============================================================

def get_utc_timestamp():

    """
    Activity Frames expects frame timestamps to be UTC.

    Example:
    India 23:30
    becomes approximately
    UTC   18:00
    """

    return datetime.now(timezone.utc).strftime(
        "%Y-%m-%d %H:%M:%S"
    )


# ============================================================
# MAIN RECORDER
# ============================================================

def main():

    setup_database()

    print("=" * 60)
    print("Windows Activity Recorder Started")
    print("=" * 60)

    print(f"Database: {DB_PATH}")

    print("Timestamp mode: UTC")

    print("Press Ctrl+C to stop.")

    print("=" * 60)
    print()


    last_activity = None


    try:

        while True:

            app_name, window_title = get_active_window_info()

            current_activity = (
                app_name,
                window_title
            )


            # Save only when active window changes
            if current_activity != last_activity:

                timestamp = get_utc_timestamp()


                print(f"[{timestamp} UTC]")

                print(
                    f"Application: {app_name}"
                )

                print(
                    f"Window Title: {window_title}"
                )

                print("-" * 60)


                save_activity(
                    timestamp,
                    app_name,
                    window_title
                )


                last_activity = current_activity


            time.sleep(1)


    except KeyboardInterrupt:

        print()
        print("=" * 60)
        print("Recorder stopped.")
        print("=" * 60)


# ============================================================
# START PROGRAM
# ============================================================

if __name__ == "__main__":
    main()