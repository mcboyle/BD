#!/home/mboyle/BulkDownloader/venv/bin/python3
import os
os.environ.setdefault("DISPLAY", ":99")             # headless VNC display
os.environ.setdefault("XDG_SESSION_TYPE", "x11")    # lets pyautogui screenshot via scrot
import pyautogui
import time
import signal
import sys
import logging

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
)
logger = logging.getLogger(__name__)

REFERENCE_FRAME = os.path.expanduser('~/.local/bin/vnc_ref_frame.png')
CONFIDENCE = 0.8
POLL_INTERVAL = 2
INITIAL_WAIT = 10
POST_CLICK_WAIT = 5

_running = True


def _handle_signal(signum, frame):
    global _running
    logger.info("Received signal %s, shutting down…", signal.Signals(signum).name)
    _running = False


def sync_display_buffer():
    if not os.path.exists(REFERENCE_FRAME):
        logger.warning("Reference frame not found at %s — waiting %ds", REFERENCE_FRAME, INITIAL_WAIT)
        time.sleep(INITIAL_WAIT)
        return

    try:
        location = pyautogui.locateCenterOnScreen(REFERENCE_FRAME, confidence=CONFIDENCE)
        if location:
            logger.info("Match found at (%d, %d) — clicking", location.x, location.y)
            pyautogui.click(location)
            pyautogui.moveTo(10, 10)
            time.sleep(POST_CLICK_WAIT)
    except pyautogui.ImageNotFoundException:
        pass  # Normal — reference image not on screen yet
    except Exception:
        logger.exception("Unexpected error during screen scan")


if __name__ == "__main__":
    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    logger.info("Starting vnc_display_sync daemon (ref=%s, confidence=%.2f)", REFERENCE_FRAME, CONFIDENCE)

    while _running:
        sync_display_buffer()
        time.sleep(POLL_INTERVAL)

    logger.info("Daemon stopped.")
