"""
PosturFix - Modern Desktop Application with System Tray & Sedentary Reminder
100% Offline & Privacy-First Posture Monitor.

Runs quietly in the system tray by default with zero network calls and RAM-only video processing.
Includes:
- MediaPipe Pose Tracking (Head/neck drops, shoulder/head tilts, screen hunches)
- Background System Tray execution with right-click context menu
- Sedentary Reminder (prolonged sitting alert) with native OS notifications & auto-reset
- Scale-invariant calibration baseline
"""

import os
import sys
import time
import math
import threading
import queue
import tkinter as tk
from typing import Optional

import cv2
import numpy as np
from PIL import Image, ImageTk, ImageDraw
import customtkinter as ctk
import pystray

import json
import urllib.request
import webbrowser

from config import PostureConfig
from posture_engine import PostureEngine, PostureState, PostureMetrics
import license_manager

# Application Versioning & Global Metadata
APP_VERSION = "1.0.0"
APP_COPYRIGHT = "© 2026 PosturFix. All rights reserved."
UPDATE_CHECK_URL = "https://raw.githubusercontent.com/andregooner/posturfix/main/version.json"


def parse_version(v_str: str) -> tuple:
    """Parses version strings like '1.0.0' or 'v1.1.0' into numeric tuples for safe comparison."""
    clean = v_str.strip().lstrip("vV")
    parts = []
    for chunk in clean.split("."):
        try:
            parts.append(int(chunk))
        except ValueError:
            parts.append(0)
    return tuple(parts)


def resource_path(relative_path: str) -> str:
    """
    Get absolute path to resource, works for dev and for PyInstaller bundle.
    When bundled via PyInstaller, sys._MEIPASS holds the path to the temporary extraction directory.
    """
    try:
        base_path = sys._MEIPASS
    except AttributeError:
        base_path = os.path.dirname(os.path.abspath(__file__))
    return os.path.normpath(os.path.join(base_path, relative_path))


def get_or_create_placeholder_icon(icon_size: int = 64) -> Image.Image:
    """
    Safely retrieves the application icon.
    If assets/icon.png does not exist, programmatically generates an aesthetic
    placeholder icon so the application never crashes.
    """
    assets_dir = resource_path("assets")
    png_path = resource_path(os.path.join("assets", "icon.png"))
    ico_path = resource_path(os.path.join("assets", "icon.ico"))

    if os.path.exists(png_path):
        try:
            return Image.open(png_path)
        except Exception:
            pass

    # Programmatically generate high-DPI placeholder icon
    os.makedirs(assets_dir, exist_ok=True)
    img = Image.new("RGBA", (icon_size, icon_size), color=(0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Emerald shield background
    draw.ellipse((4, 4, icon_size - 4, icon_size - 4), fill="#10B981", outline="#059669", width=2)
    # Upright spine line
    draw.line((icon_size // 2, 16, icon_size // 2, icon_size - 16), fill="#FFFFFF", width=4)
    # Vertebrae nodes
    mid = icon_size // 2
    draw.ellipse((mid - 4, 14, mid + 4, 22), fill="#FFFFFF")
    draw.ellipse((mid - 4, mid - 4, mid + 4, mid + 4), fill="#FFFFFF")
    draw.ellipse((mid - 4, icon_size - 22, mid + 4, icon_size - 14), fill="#FFFFFF")

    try:
        img.save(png_path, format="PNG")
        img.save(ico_path, format="ICO")
    except Exception:
        pass

    return img


class IdleDetector:
    """
    Monitors global mouse and keyboard activity for Deep Sleep power saving.
    Privacy Guarantee:
    - Strictly detects presence of physical user input events.
    - NEVER logs, stores, or transmits keystrokes or mouse coordinates.
    - Uses OS-level Windows GetLastInputInfo API (zero-overhead, zero-hooks)
      with pynput listener fallback for seamless cross-platform support.
    """

    def __init__(self):
        self._last_activity_time = time.time()
        self._is_windows = sys.platform == "win32"
        self._pynput_listeners = []

        try:
            from pynput import mouse, keyboard

            def _on_input(*args, **kwargs):
                self._last_activity_time = time.time()

            m_listener = mouse.Listener(
                on_move=_on_input,
                on_click=_on_input,
                on_scroll=_on_input,
            )
            k_listener = keyboard.Listener(
                on_press=_on_input,
                on_release=_on_input,
            )
            m_listener.daemon = True
            k_listener.daemon = True
            m_listener.start()
            k_listener.start()
            self._pynput_listeners = [m_listener, k_listener]
        except Exception:
            pass

    def mark_active(self):
        """Manually registers an activity event (e.g. tray menu click)."""
        self._last_activity_time = time.time()

    def get_idle_seconds(self) -> float:
        """
        Returns the number of elapsed seconds since the last global mouse or keyboard input.
        """
        now = time.time()
        pynput_idle = max(0.0, now - self._last_activity_time)

        if self._is_windows:
            try:
                import ctypes

                class LASTINPUTINFO(ctypes.Structure):
                    _fields_ = [
                        ("cbSize", ctypes.c_uint),
                        ("dwTime", ctypes.c_uint),
                    ]

                info = LASTINPUTINFO()
                info.cbSize = ctypes.sizeof(LASTINPUTINFO)
                if ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
                    millis = ctypes.windll.kernel32.GetTickCount() - info.dwTime
                    win_idle = max(0.0, millis / 1000.0)
                    return min(win_idle, pynput_idle)
            except Exception:
                pass

        return pynput_idle

    def stop(self):
        """Stops background input listeners cleanly."""
        for listener in self._pynput_listeners:
            try:
                listener.stop()
            except Exception:
                pass
        self._pynput_listeners.clear()


class MascotWidget(ctk.CTkFrame):
    """
    Expressive 2D Mascot Widget.
    Renders an adaptive vector mascot based on posture state.
    Also supports custom user-provided image assets in assets/mascot/ folder.
    """

    def __init__(self, master, **kwargs):
        super().__init__(master, **kwargs)
        self.configure(fg_color=("gray92", "#1E1E24"), corner_radius=12)

        self.canvas_size = 140
        self.canvas = tk.Canvas(
            self,
            width=self.canvas_size,
            height=self.canvas_size,
            bg=self._get_canvas_bg(),
            highlightthickness=0,
        )
        self.canvas.pack(pady=(12, 4), padx=12)

        self.speech_label = ctk.CTkLabel(
            self,
            text="I'm PosturFix Pal! Sit tall!",
            font=ctk.CTkFont(size=12, weight="bold"),
            wraplength=170,
        )
        self.speech_label.pack(pady=(0, 10), padx=10)

        # Asset directory check for custom user sprites
        self.assets_dir = resource_path(os.path.join("assets", "mascot"))
        self._current_state = None
        self.draw_mascot(PostureState.NO_PERSON)

    def _get_canvas_bg(self) -> str:
        mode = ctk.get_appearance_mode()
        return "#1E1E24" if mode == "Dark" else "#E5E5EA"

    def update_state(self, state: PostureState, slouch_reason: str = ""):
        if self._current_state == state:
            return
        self._current_state = state
        self.draw_mascot(state, slouch_reason)

    def draw_mascot(self, state: PostureState, slouch_reason: str = ""):
        self.canvas.delete("all")
        bg_color = self._get_canvas_bg()
        self.canvas.configure(bg=bg_color)

        # 1. Check if user provided custom sprite image files
        custom_file_map = {
            PostureState.GOOD: "good.png",
            PostureState.WARNING: "warning.png",
            PostureState.SLOUCHING: "slouch.png",
            PostureState.CALIBRATING: "calibrating.png",
            PostureState.NO_PERSON: "sleep.png",
            PostureState.UNCALIBRATED: "neutral.png",
        }
        custom_path = os.path.join(self.assets_dir, custom_file_map.get(state, ""))
        if os.path.exists(custom_path):
            try:
                img = Image.open(custom_path).resize((self.canvas_size - 10, self.canvas_size - 10))
                self._photo_ref = ImageTk.PhotoImage(img)
                self.canvas.create_image(self.canvas_size // 2, self.canvas_size // 2, image=self._photo_ref)
                self._update_speech(state, slouch_reason)
                return
            except Exception:
                pass

        # 2. Programmatic 2D Vector Mascot
        cx, cy = self.canvas_size // 2, self.canvas_size // 2
        r = 46

        if state == PostureState.GOOD:
            body_color = "#34C759"     # Emerald Green
            eye_color = "#FFFFFF"
            aura_color = "#E8F8ED" if ctk.get_appearance_mode() == "Light" else "#1A3823"
            self.canvas.create_oval(cx - r - 6, cy - r - 6, cx + r + 6, cy + r + 6, fill=aura_color, outline="")
            self.canvas.create_oval(cx - r, cy - r, cx + r, cy + r, fill=body_color, outline="#28A745", width=2)
            self.canvas.create_arc(cx - 24, cy - 14, cx - 8, cy - 2, start=0, extent=180, style=tk.ARC, width=3, outline=eye_color)
            self.canvas.create_arc(cx + 8, cy - 14, cx + 24, cy - 2, start=0, extent=180, style=tk.ARC, width=3, outline=eye_color)
            self.canvas.create_arc(cx - 16, cy - 8, cx + 16, cy + 22, start=200, extent=140, style=tk.ARC, width=3, outline=eye_color)
            self.canvas.create_oval(cx - 32, cy + 2, cx - 22, cy + 10, fill="#FF8BA7", outline="")
            self.canvas.create_oval(cx + 22, cy + 2, cx + 32, cy + 10, fill="#FF8BA7", outline="")
            self.canvas.create_line(cx, cy - r - 2, cx, cy - r - 8, fill="#34C759", width=3)

        elif state == PostureState.WARNING:
            body_color = "#FF9500"     # Amber / Orange
            eye_color = "#FFFFFF"
            self.canvas.create_oval(cx - r, cy - r + 4, cx + r, cy + r + 4, fill=body_color, outline="#D97706", width=2)
            self.canvas.create_oval(cx - 22, cy - 12, cx - 10, cy, fill=eye_color, outline="")
            self.canvas.create_oval(cx - 18, cy - 8, cx - 14, cy - 4, fill="#1E1E24", outline="")
            self.canvas.create_oval(cx + 10, cy - 10, cx + 20, cy, fill=eye_color, outline="")
            self.canvas.create_oval(cx + 13, cy - 7, cx + 17, cy - 3, fill="#1E1E24", outline="")
            self.canvas.create_line(cx - 12, cy + 14, cx + 12, cy + 14, fill=eye_color, width=3)

        elif state == PostureState.SLOUCHING:
            body_color = "#FF3B30"     # Alert Red
            aura_color = "#FDE8E7" if ctk.get_appearance_mode() == "Light" else "#3B1B1B"
            self.canvas.create_oval(cx - r - 6, cy - r + 4, cx + r + 6, cy + r + 14, fill=aura_color, outline="")
            self.canvas.create_oval(cx - r - 4, cy - r + 8, cx + r + 4, cy + r + 14, fill=body_color, outline="#C53030", width=2)
            self.canvas.create_line(cx - 24, cy - 6, cx - 8, cy - 10, fill="#FFFFFF", width=3)
            self.canvas.create_line(cx + 8, cy - 10, cx + 24, cy - 6, fill="#FFFFFF", width=3)
            self.canvas.create_arc(cx - 16, cy + 10, cx + 16, cy + 30, start=20, extent=140, style=tk.ARC, width=3, outline="#FFFFFF")
            self.canvas.create_oval(cx + 28, cy - 2, cx + 34, cy + 6, fill="#5AC8FA", outline="")

        elif state == PostureState.CALIBRATING:
            body_color = "#007AFF"     # Sky Blue
            self.canvas.create_oval(cx - r, cy - r, cx + r, cy + r, fill=body_color, outline="#0056B3", width=2)
            self.canvas.create_oval(cx - 20, cy - 10, cx - 8, cy + 2, fill="#FFFFFF", outline="")
            self.canvas.create_oval(cx + 8, cy - 10, cx + 20, cy + 2, fill="#FFFFFF", outline="")
            self.canvas.create_oval(cx - 16, cy - 6, cx - 12, cy - 2, fill="#007AFF", outline="")
            self.canvas.create_oval(cx + 12, cy - 6, cx + 16, cy - 2, fill="#007AFF", outline="")
            self.canvas.create_oval(cx - 5, cy + 10, cx + 5, cy + 20, outline="#FFFFFF", width=2)

        elif state == PostureState.UNCALIBRATED:
            body_color = "#8E8E93"     # Slate Gray
            self.canvas.create_oval(cx - r, cy - r, cx + r, cy + r, fill=body_color, outline="#636366", width=2)
            self.canvas.create_oval(cx - 20, cy - 8, cx - 10, cy + 2, fill="#FFFFFF", outline="")
            self.canvas.create_oval(cx + 10, cy - 8, cx + 20, cy + 2, fill="#FFFFFF", outline="")
            self.canvas.create_line(cx - 10, cy + 14, cx + 10, cy + 14, fill="#FFFFFF", width=2)

        else:  # NO_PERSON
            body_color = "#636366"
            self.canvas.create_oval(cx - r, cy - r, cx + r, cy + r, fill=body_color, outline="#48484A", width=2)
            self.canvas.create_line(cx - 22, cy - 2, cx - 10, cy - 2, fill="#D1D1D6", width=3)
            self.canvas.create_line(cx + 10, cy - 2, cx + 22, cy - 2, fill="#D1D1D6", width=3)
            self.canvas.create_text(cx + 26, cy - 24, text="Z", font=("Arial", 11, "bold"), fill="#8E8E93")
            self.canvas.create_text(cx + 34, cy - 34, text="z", font=("Arial", 9, "bold"), fill="#8E8E93")

        self._update_speech(state, slouch_reason)

    def _update_speech(self, state: PostureState, slouch_reason: str):
        if "Screen Too Close" in slouch_reason:
            if state == PostureState.SLOUCHING:
                self.speech_label.configure(text="Too close to the screen! Please lean back to protect your eyes!")
                return
            elif state == PostureState.WARNING:
                self.speech_label.configure(text="Screen Too Close / Lean Back to prevent eye strain!")
                return

        dialogues = {
            PostureState.GOOD: "Awesome posture! Looking confident and energized!",
            PostureState.WARNING: "Heads up! Slouching detected, straighten up!",
            PostureState.SLOUCHING: f"Ouch! {slouch_reason or 'Slouching sustained'}! Please sit straight!",
            PostureState.CALIBRATING: "Hold still... Measuring your perfect upright baseline!",
            PostureState.UNCALIBRATED: "Please sit upright and click 'Calibrate Posture'.",
            PostureState.NO_PERSON: "Looking for you... Ensure your upper body is in view.",
        }
        self.speech_label.configure(text=dialogues.get(state, "Sit tall and stay healthy!"))


class UpdateDialog(ctk.CTkToplevel):
    """
    Modern dark-themed update notification modal dialog.
    Notifies user when a new release is available and allows 1-click download.
    """

    def __init__(
        self,
        parent: ctk.CTk,
        current_version: str,
        latest_version: str,
        download_url: str,
        release_notes: str = "",
    ):
        super().__init__(parent)
        self.parent = parent
        self.current_version = current_version
        self.latest_version = latest_version
        self.download_url = download_url
        self.release_notes = release_notes

        self.title("PosturFix Update Available")
        self.geometry("460x340")
        self.resizable(False, False)
        self.attributes("-topmost", True)

        self._center_window(460, 340)
        self._build_ui()

    def _center_window(self, width: int, height: int):
        self.update_idletasks()
        screen_w = self.winfo_screenwidth()
        screen_h = self.winfo_screenheight()
        x = max(0, (screen_w - width) // 2)
        y = max(0, (screen_h - height) // 2)
        self.geometry(f"{width}x{height}+{x}+{y}")

    def _build_ui(self):
        container = ctk.CTkFrame(self, corner_radius=12, fg_color=("gray95", "#18181B"))
        container.pack(fill="both", expand=True, padx=16, pady=16)

        # Header Icon Badge
        badge = ctk.CTkLabel(container, text="🚀", font=ctk.CTkFont(size=36))
        badge.pack(pady=(12, 4))

        title_lbl = ctk.CTkLabel(
            container,
            text="New Version Available!",
            font=ctk.CTkFont(size=18, weight="bold"),
        )
        title_lbl.pack(pady=(0, 4))

        version_info = ctk.CTkLabel(
            container,
            text=f"Current: v{self.current_version}  ➔  Latest: v{self.latest_version}",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color="#10B981",
        )
        version_info.pack(pady=(0, 8))

        prompt_text = (
            f"A new version (v{self.latest_version}) is available!\n"
            "Would you like to download it?"
        )
        prompt_lbl = ctk.CTkLabel(
            container,
            text=prompt_text,
            font=ctk.CTkFont(size=12),
            text_color="gray80",
            wraplength=380,
            justify="center",
        )
        prompt_lbl.pack(pady=(0, 8), padx=20)

        if self.release_notes:
            notes_frame = ctk.CTkFrame(container, fg_color=("gray90", "#27272A"), corner_radius=8)
            notes_frame.pack(fill="x", padx=20, pady=(0, 14))
            notes_lbl = ctk.CTkLabel(
                notes_frame,
                text=self.release_notes,
                font=ctk.CTkFont(size=11),
                text_color="gray75",
                wraplength=360,
                justify="center",
            )
            notes_lbl.pack(padx=10, pady=8)

        # Button row: Later vs Download Now
        btn_frame = ctk.CTkFrame(container, fg_color="transparent")
        btn_frame.pack(fill="x", padx=20, pady=(6, 10))

        later_btn = ctk.CTkButton(
            btn_frame,
            text="Later",
            width=120,
            height=36,
            fg_color=("gray75", "#3F3F46"),
            hover_color=("gray65", "#52525B"),
            command=self.destroy,
        )
        later_btn.pack(side="left", expand=True, padx=(0, 8))

        download_btn = ctk.CTkButton(
            btn_frame,
            text="Download Now",
            width=160,
            height=36,
            fg_color="#3B82F6",
            hover_color="#2563EB",
            font=ctk.CTkFont(weight="bold"),
            command=self._on_download,
        )
        download_btn.pack(side="right", expand=True, padx=(8, 0))

    def _on_download(self):
        try:
            if self.download_url:
                webbrowser.open(self.download_url)
        except Exception:
            pass
        self.destroy()


class PostureApp(ctk.CTk):
    """
    Main Desktop Window for PosturFix with System Tray & Sedentary Reminder.
    """

    def __init__(self, start_hidden: bool = True):
        super().__init__()

        # Appearance configuration
        ctk.set_appearance_mode("Dark")
        ctk.set_default_color_theme("blue")

        self.title("PosturFix • 100% Offline & Private")
        self.geometry("1020x700")
        self.minsize(920, 640)

        # Core Engine Initialization
        self.config = PostureConfig()
        self.engine = PostureEngine(self.config)

        # Deep Sleep & Idle Activity Detector
        self.idle_detector = IdleDetector()
        self.is_deep_sleeping: bool = False

        # Snooze / Pause State
        self.snooze_until: float = 0.0
        self._snooze_event = threading.Event()
        self._camera_retry_event = threading.Event()

        # Camera & State Variables
        self.cap: Optional[cv2.VideoCapture] = None
        self.camera_running = False
        self._camera_thread: Optional[threading.Thread] = None
        self._frame_queue: queue.Queue = queue.Queue(maxsize=2)
        self._ui_poll_started: bool = False
        self.is_window_visible: bool = not start_hidden
        self.privacy_mode = self.config.privacy_mode_default
        self.current_metrics: PostureMetrics = PostureMetrics()
        self._has_shown_tray_hint = False

        # Sedentary (Break) Timer Variables
        self.sitting_seconds: float = 0.0
        self._last_sitting_tick: float = time.time()
        self._away_start_time: Optional[float] = None
        self._last_posture_check: float = 0.0

        # Visual Pop-out Toast Notification Cooldown Tracking (Isolated Systems)
        self._last_slouch_toast_time: float = 0.0
        self._slouch_toast_lock = threading.Lock()
        self._last_eye_toast_time: float = 0.0
        self._eye_toast_lock = threading.Lock()

        # Load Icon
        self.app_icon = get_or_create_placeholder_icon()
        try:
            ico_path = resource_path(os.path.join("assets", "icon.ico"))
            if os.path.exists(ico_path):
                self.iconbitmap(ico_path)
        except Exception:
            pass

        self._has_notified_camera_conflict: bool = False

        # Gatekeeper: 100% Offline License Verification
        self.is_licensed = license_manager.is_license_valid()
        self._activation_window: Optional[license_manager.ActivationWindow] = None

        # Build UI Layout
        self._build_header()
        self._build_body()
        self._build_footer()

        # Intercept Window Close ('X' button) to minimize to tray
        self.protocol("WM_DELETE_WINDOW", self.hide_to_tray)

        if not self.is_licensed:
            # Block main UI & camera processing until valid license key is entered
            self.withdraw()
            self._activation_window = license_manager.ActivationWindow(
                parent=self,
                on_success_callback=self._on_activation_success,
                on_close_callback=self.quit_application,
            )
        else:
            # Valid local validation token exists -> Bypass activation and run 100% offline
            self._setup_system_tray()
            self.start_camera()
            if start_hidden:
                self.withdraw()

    def _on_activation_success(self, key: str):
        """Callback executed when first-time license activation succeeds."""
        self.is_licensed = True
        if hasattr(self, "privacy_badge"):
            self.privacy_badge.configure(
                text=" [100% OFFLINE • LICENSED] ",
                text_color="#10B981",
                fg_color=("#D1FAE5", "#064E3B"),
            )
        self._setup_system_tray()
        self.start_camera()
        self.show_window()

    def _setup_system_tray(self):
        """Initializes the background system tray icon and context menu."""
        menu = pystray.Menu(
            pystray.MenuItem("PosturFix", None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Show Window", self._on_tray_show_window, default=True),
            pystray.MenuItem("Quick Calibrate", self._on_tray_quick_calibrate),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                "Settings (Pause/Toggle Alerts)",
                pystray.Menu(
                    pystray.MenuItem("Pause for 30 Mins", self._on_tray_pause_30),
                    pystray.MenuItem("Pause for 1 Hour", self._on_tray_pause_60),
                    pystray.MenuItem("Resume Monitoring", self._on_tray_resume, enabled=lambda item: self.is_snoozed()),
                    pystray.Menu.SEPARATOR,
                    pystray.MenuItem("Sound Alert", self._on_tray_toggle_audio, checked=lambda item: self.config.audio_alert_enabled),
                    pystray.MenuItem("Desktop Toast Notifications", self._on_tray_toggle_toast, checked=lambda item: self.config.toast_notification_enabled),
                    pystray.MenuItem("Eye Distance Alert (50cm)", self._on_tray_toggle_eye_alert, checked=lambda item: self.config.eye_distance_warning_enabled),
                    pystray.MenuItem("Smart Break Reminder", self._on_tray_toggle_break_reminder, checked=lambda item: self.config.sedentary_reminder_enabled),
                    pystray.Menu.SEPARATOR,
                    pystray.MenuItem("Start on System Startup", self._on_tray_toggle_autostart, checked=lambda item: self.is_autostart_active()),
                ),
            ),
            pystray.MenuItem("Reset Break Timer", self._on_tray_reset_break_timer),
            pystray.MenuItem("Check for Updates", self._on_tray_check_updates),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit/Exit", self._on_tray_quit_app),
        )

        self.tray_icon = pystray.Icon(
            name="PosturFix",
            icon=self.app_icon,
            title="PosturFix • Running in Background",
            menu=menu,
        )

        # Start pystray on a background thread so Tkinter mainloop is unaffected
        self.tray_icon.run_detached()

    # ----------------- SYSTEM TRAY & TIMER CALLBACKS -----------------

    def _on_tray_show_window(self, icon=None, item=None):
        """Thread-safe callback to restore the window from the tray."""
        self.after(0, self.show_window)

    def _on_tray_quick_calibrate(self, icon=None, item=None):
        """Thread-safe callback to trigger calibration directly from the tray."""
        self.after(0, self.quick_calibrate)

    def _on_tray_pause_30(self, icon=None, item=None):
        """Thread-safe callback to pause monitoring for 30 minutes."""
        self.after(0, lambda: self.snooze(30))

    def _on_tray_pause_60(self, icon=None, item=None):
        """Thread-safe callback to pause monitoring for 1 hour."""
        self.after(0, lambda: self.snooze(60))

    def _on_tray_resume(self, icon=None, item=None):
        """Thread-safe callback to resume monitoring from pause."""
        self.after(0, self.resume)

    def _on_tray_toggle_audio(self, icon=None, item=None):
        """Thread-safe callback to toggle sound alerts from tray."""
        self.after(0, self._toggle_audio_alert)

    def _on_tray_toggle_toast(self, icon=None, item=None):
        """Thread-safe callback to toggle desktop toast notifications from tray."""
        self.after(0, self._toggle_toast_notification)

    def _on_tray_toggle_eye_alert(self, icon=None, item=None):
        """Thread-safe callback to toggle eye distance alert from tray."""
        self.after(0, self._toggle_eye_alert)

    def _on_tray_toggle_break_reminder(self, icon=None, item=None):
        """Thread-safe callback to toggle smart break reminder from tray."""
        self.after(0, self._toggle_break_reminder)

    def _toggle_break_reminder(self):
        """Toggles the smart break reminder from system tray or shortcut."""
        self.config.sedentary_reminder_enabled = not self.config.sedentary_reminder_enabled
        if hasattr(self, "break_toggle_switch"):
            if self.config.sedentary_reminder_enabled:
                self.break_toggle_switch.select()
            else:
                self.break_toggle_switch.deselect()
            self._on_break_toggle()

    def _on_tray_reset_break_timer(self, icon=None, item=None):
        """Thread-safe callback to reset break timer from system tray."""
        self.after(0, self.reset_break_timer)

    def _on_tray_check_updates(self, icon=None, item=None):
        """Thread-safe callback to trigger update check from tray."""
        self.after(0, lambda: self.check_for_updates(from_tray=True))

    def _on_tray_toggle_autostart(self, icon=None, item=None):
        """Thread-safe callback to toggle system startup entry."""
        self.after(0, self.toggle_autostart)

    def _on_tray_quit_app(self, icon=None, item=None):
        """Thread-safe callback to quit application completely."""
        self.after(0, self.quit_application)

    def show_window(self):
        """Restores, lifts, and focuses the main application window."""
        self.idle_detector.mark_active()
        self.is_window_visible = True
        self.deiconify()
        self.state("normal")
        self.lift()
        self.focus_force()
        if hasattr(self, "camera_status_lbl"):
            if self.cap and self.cap.isOpened():
                self.camera_status_lbl.configure(
                    text=f"Camera: Connected ({self.config.frame_width}x{self.config.frame_height} Lite)",
                    text_color="#10B981",
                )
            elif self.is_snoozed():
                rem_mins = max(1, int(math.ceil((self.snooze_until - time.time()) / 60.0)))
                self.camera_status_lbl.configure(
                    text=f"Camera: Paused ({rem_mins}m left)",
                    text_color="#F59E0B",
                )
        if hasattr(self, "_camera_retry_event"):
            self._camera_retry_event.set()

    def hide_to_tray(self):
        """Hides the window to the tray instead of closing."""
        self.is_window_visible = False
        self.withdraw()
        if not self._has_shown_tray_hint:
            try:
                self.tray_icon.notify(
                    "PosturFix is running quietly in the background.\nRight-click or double-click this icon anytime.",
                    "Minimized to System Tray",
                )
                self._has_shown_tray_hint = True
            except Exception:
                pass

    def quick_calibrate(self):
        """Triggers baseline posture calibration immediately."""
        self.idle_detector.mark_active()
        if self.is_snoozed():
            self.snooze_until = 0.0
            self._snooze_event.set()
        self.engine.start_calibration()
        if hasattr(self, "_camera_retry_event"):
            self._camera_retry_event.set()
        try:
            self.tray_icon.notify(
                "Hold still for 1 second... Measuring your ideal upright baseline posture.",
                "Calibrating Posture",
            )
        except Exception:
            pass

    def is_snoozed(self) -> bool:
        """Returns True if posture monitoring is currently paused."""
        return time.time() < self.snooze_until

    def snooze(self, minutes: int):
        """Pauses posture checking and completely shuts down camera for specified minutes."""
        self.idle_detector.mark_active()
        self.snooze_until = time.time() + (minutes * 60.0)

        # Completely release camera hardware to turn off physical camera light
        if self.cap and self.cap.isOpened():
            try:
                self.cap.release()
            except Exception:
                pass
            self.cap = None

        if hasattr(self, "tray_icon") and self.tray_icon:
            self.tray_icon.title = f"PosturFix: Paused ({minutes}m)"
            try:
                self.tray_icon.notify(
                    f"Posture monitoring paused for {minutes} minutes.\nCamera hardware powered down.",
                    "PosturFix • Paused",
                )
            except Exception:
                pass

        if hasattr(self, "camera_status_lbl"):
            self.camera_status_lbl.configure(text=f"Camera: Paused ({minutes}m remaining)", text_color="#F59E0B")
            self._render_privacy_screen(f"Monitoring Paused ({minutes}m)\nRight-click tray icon and select Resume to continue")

        self._snooze_event.set()

    def resume(self):
        """Immediately ends snooze and resumes posture checking."""
        self.idle_detector.mark_active()
        self.snooze_until = 0.0

        if hasattr(self, "tray_icon") and self.tray_icon:
            self.tray_icon.title = "PosturFix: Resuming..."
            try:
                self.tray_icon.notify("Posture monitoring resumed.", "PosturFix • Active")
            except Exception:
                pass

        self._snooze_event.set()
        self._camera_retry_event.set()

    def _toggle_audio_alert(self):
        """Toggles sound alert setting and synchronizes UI switch."""
        self.config.audio_alert_enabled = not self.config.audio_alert_enabled
        if hasattr(self, "audio_switch"):
            if self.config.audio_alert_enabled:
                self.audio_switch.select()
            else:
                self.audio_switch.deselect()

    def _toggle_toast_notification(self):
        """Toggles toast notification setting and synchronizes UI switch."""
        self.config.toast_notification_enabled = not self.config.toast_notification_enabled
        if hasattr(self, "toast_switch"):
            if self.config.toast_notification_enabled:
                self.toast_switch.select()
            else:
                self.toast_switch.deselect()

    def _toggle_eye_alert(self):
        """Toggles eye distance alert and synchronizes UI switch."""
        self.config.eye_distance_warning_enabled = not self.config.eye_distance_warning_enabled
        if hasattr(self, "eye_alert_switch"):
            if self.config.eye_distance_warning_enabled:
                self.eye_alert_switch.select()
            else:
                self.eye_alert_switch.deselect()

    def _on_autostart_checkbox_toggle(self):
        """Handler when user toggles the startup checkbox in UI."""
        is_checked = self.autostart_checkbox.get() == 1
        self.toggle_autostart(target_state=is_checked)

    def is_autostart_active(self) -> bool:
        """Checks if PosturFix is configured to start on Windows boot or macOS login."""
        if sys.platform == "win32":
            try:
                import winreg
                reg_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, reg_path, 0, winreg.KEY_READ) as key:
                    winreg.QueryValueEx(key, "PosturFix")
                    return True
            except Exception:
                return False
        elif sys.platform == "darwin":
            plist_path = os.path.expanduser("~/Library/LaunchAgents/com.posturfix.app.plist")
            return os.path.exists(plist_path)
        return False

    def toggle_autostart(self, target_state: Optional[bool] = None):
        """Adds or removes PosturFix from system startup (Windows Registry or macOS LaunchAgents)."""
        currently_enabled = self.is_autostart_active()
        target_enabled = not currently_enabled if target_state is None else target_state

        msg = ""
        title = ""

        if sys.platform == "win32":
            try:
                import winreg
                reg_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, reg_path, 0, winreg.KEY_SET_VALUE | winreg.KEY_READ) as key:
                    if target_enabled:
                        if getattr(sys, "frozen", False):
                            cmd = f'"{sys.executable}" --minimized'
                        else:
                            python_dir = os.path.dirname(sys.executable)
                            pythonw = os.path.join(python_dir, "pythonw.exe")
                            if not os.path.exists(pythonw):
                                pythonw = sys.executable
                            app_file = os.path.abspath(__file__)
                            cmd = f'"{pythonw}" "{app_file}" --minimized'

                        winreg.SetValueEx(key, "PosturFix", 0, winreg.REG_SZ, cmd)
                        msg = "PosturFix will now run automatically on system boot."
                        title = "PosturFix • Run on Startup Enabled"
                    else:
                        try:
                            winreg.DeleteValue(key, "PosturFix")
                        except FileNotFoundError:
                            pass
                        msg = "PosturFix removed from system startup."
                        title = "PosturFix • Run on Startup Disabled"
            except Exception as e:
                msg = f"Could not update Windows startup registry: {e}"
                title = "PosturFix Error"

        elif sys.platform == "darwin":
            plist_path = os.path.expanduser("~/Library/LaunchAgents/com.posturfix.app.plist")
            try:
                if target_enabled:
                    os.makedirs(os.path.dirname(plist_path), exist_ok=True)
                    app_exec = sys.executable
                    plist_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.posturfix.app</string>
    <key>ProgramArguments</key>
    <array>
        <string>{app_exec}</string>
        <string>--minimized</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
</dict>
</plist>"""
                    with open(plist_path, "w", encoding="utf-8") as f:
                        f.write(plist_content)
                    msg = "PosturFix configured to launch at macOS login."
                    title = "PosturFix • Run on Startup Enabled"
                else:
                    if os.path.exists(plist_path):
                        os.remove(plist_path)
                    msg = "PosturFix removed from macOS startup."
                    title = "PosturFix • Run on Startup Disabled"
            except Exception as e:
                msg = f"Could not update macOS LaunchAgents: {e}"
                title = "PosturFix Error"

        # Update UI Checkbox in Settings if exists
        if hasattr(self, "autostart_checkbox"):
            if self.is_autostart_active():
                self.autostart_checkbox.select()
            else:
                self.autostart_checkbox.deselect()

        if hasattr(self, "tray_icon") and self.tray_icon:
            try:
                self.tray_icon.notify(msg, title)
            except Exception:
                pass

    def reset_break_timer(self, notify: bool = True):
        """Resets the continuous sitting timer to zero."""
        self.idle_detector.mark_active()
        self.sitting_seconds = 0.0
        self._away_start_time = None
        if hasattr(self, "break_timer_lbl"):
            target_mins = self.config.sedentary_interval_minutes
            self.break_timer_lbl.configure(text=f"Active Sitting: 0m 00s / {target_mins}m")
            self.break_progress.set(0.0)
            self.break_progress.configure(progress_color="#3B82F6")

        if notify and hasattr(self, "tray_icon") and self.tray_icon:
            try:
                self.tray_icon.notify(
                    f"Break timer reset. Next stretch reminder in {self.config.sedentary_interval_minutes} minutes.",
                    "PosturFix • Timer Reset",
                )
            except Exception:
                pass

    def trigger_sedentary_alert(self):
        """Fires native OS desktop notification and auto-resets break timer."""
        interval_mins = self.config.sedentary_interval_minutes
        if interval_mins == 60:
            duration_text = "an hour"
        elif interval_mins == 120:
            duration_text = "2 hours"
        else:
            duration_text = f"{interval_mins} minutes"

        title = "💧 Hydration Break!"
        message = (
            f"💧 Hydration Break! You've been sitting continuously for {duration_text}. "
            "Stand up, stretch your back, and drink a glass of water."
        )

        # Multi-backend native OS toast notification (Action Center, pystray, plyer)
        self._dispatch_native_toast(title, message)

        # Audio chime alert
        if self.config.audio_alert_enabled:
            self.engine._play_alert_sound()

        # Automatically reset continuous sitting timer after alert is delivered
        self.sitting_seconds = 0.0
        self._away_start_time = None

    def _format_slouch_message(self, metrics: PostureMetrics) -> str:
        """Generates dynamic, informative message for the posture slouching toast notification."""
        reason = metrics.slouch_reason.lower() if metrics.slouch_reason else ""
        if "neck" in reason:
            return "You are slouching! Please sit up straight and lift your neck."
        elif "shoulder" in reason:
            return "Uneven shoulders detected! Please sit up straight."
        elif "head tilt" in reason:
            return "Head tilt detected! Please keep your head level."
        elif "leaning" in reason:
            return "Leaning too close to the screen! Please sit back."
        else:
            return "You are slouching! Please sit up straight."

    def _dispatch_native_toast(self, title: str, message: str) -> None:
        """
        Asynchronously delivers native Windows Action Center or Tray notification in a background daemon thread.
        Guarantees zero blocking of the camera loop or GUI thread.
        """
        def _toast_worker():
            delivered = False

            # Resolve application icon path for native Windows toast banner
            icon_path = resource_path(os.path.join("assets", "icon.png"))
            if not os.path.exists(icon_path):
                icon_path = resource_path(os.path.join("assets", "icon.ico"))
            icon_arg = os.path.abspath(icon_path) if os.path.exists(icon_path) else ""

            # Attempt 1: Modern Windows 10/11 Action Center Toast via winotify (official OS notification)
            try:
                from winotify import Notification
                toast = Notification(
                    app_id=self.config.toast_app_name,
                    title=title,
                    msg=message,
                    duration="short",
                    icon=icon_arg,
                )
                toast.show()
                delivered = True
            except Exception:
                pass

            # Attempt 2: Native Windows Shell Tray Notification via pystray
            if not delivered and hasattr(self, "tray_icon") and self.tray_icon:
                try:
                    self.tray_icon.notify(message, title)
                    delivered = True
                except Exception:
                    pass

            # Attempt 3: Cross-platform fallback via plyer
            if not delivered:
                try:
                    from plyer import notification
                    notification.notify(
                        title=title,
                        message=message,
                        app_name=self.config.toast_app_name,
                        timeout=5,
                    )
                    delivered = True
                except Exception:
                    pass

        # Dedicated background daemon thread: non-blocking execution (zero impact on camera interval)
        threading.Thread(
            target=_toast_worker,
            daemon=True,
            name="PosturFix-NativeToastWorker",
        ).start()

    def trigger_slouch_toast(self, metrics: PostureMetrics) -> None:
        """
        Fires an asynchronous, non-blocking native OS toast notification for posture slouching.
        Triggered strictly when the slouch duration reaches 5 seconds, synchronized with the sound alert.
        """
        if not self.config.toast_notification_enabled:
            return

        now = time.time()
        with self._slouch_toast_lock:
            if (now - self._last_slouch_toast_time) < self.config.toast_cooldown_seconds:
                return  # Within cooldown window
            self._last_slouch_toast_time = now

        title = self.config.toast_title
        message = self._format_slouch_message(metrics)
        self._dispatch_native_toast(title, message)

    def trigger_eye_distance_toast(self, metrics: PostureMetrics) -> None:
        """
        Fires an asynchronous native OS toast alert specifically for eye protection.
        Protected by an exclusive 2-minute (120s) cooldown timer and 15s consecutive buffer.
        Completely isolated from posture slouching alerts.
        """
        if not self.config.toast_notification_enabled or not self.config.eye_distance_warning_enabled:
            return

        now = time.time()
        with self._eye_toast_lock:
            if (now - self._last_eye_toast_time) < self.config.eye_toast_cooldown_seconds:
                return  # Within 2-minute (120s) cooldown window
            self._last_eye_toast_time = now

        title = "PosturFix • Eye Distance Alert (50cm)"
        message = "Your face is closer than 50cm to the screen! Please lean back to protect your eyes."
        self._dispatch_native_toast(title, message)

    def trigger_posture_toast(self, metrics: PostureMetrics) -> None:
        """Backward-compatible proxy pointing to trigger_slouch_toast."""
        self.trigger_slouch_toast(metrics)

    def quit_application(self):
        """Stops background threads and exits cleanly."""
        self.camera_running = False
        if hasattr(self, "idle_detector") and self.idle_detector:
            try:
                self.idle_detector.stop()
            except Exception:
                pass

        if hasattr(self, "_camera_retry_event"):
            self._camera_retry_event.set()
        if hasattr(self, "_snooze_event"):
            self._snooze_event.set()

        if hasattr(self, "_camera_thread") and self._camera_thread and self._camera_thread.is_alive():
            try:
                self._camera_thread.join(timeout=0.5)
            except Exception:
                pass

        if self.cap and self.cap.isOpened():
            try:
                self.cap.release()
            except Exception:
                pass

        self.engine.close()

        if hasattr(self, "tray_icon") and self.tray_icon:
            try:
                self.tray_icon.stop()
            except Exception:
                pass

        self.destroy()
        sys.exit(0)

    # ----------------- UI BUILDERS -----------------

    def _build_header(self):
        """Top Header Bar with App Title, Privacy Badge, and Quick Toggles."""
        header_frame = ctk.CTkFrame(self, height=60, corner_radius=0, fg_color=("gray90", "#18181B"))
        header_frame.pack(fill="x", side="top", padx=0, pady=0)

        title_box = ctk.CTkFrame(header_frame, fg_color="transparent")
        title_box.pack(side="left", padx=20, pady=10)

        title_lbl = ctk.CTkLabel(
            title_box,
            text="PosturFix",
            font=ctk.CTkFont(size=20, weight="bold"),
        )
        title_lbl.pack(side="left")

        self.privacy_badge = ctk.CTkLabel(
            title_box,
            text=" [100% OFFLINE • LICENSED] " if self.is_licensed else " [100% OFFLINE • ZERO TELEMETRY] ",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color="#10B981",
            fg_color=("#D1FAE5", "#064E3B"),
            corner_radius=6,
        )
        self.privacy_badge.pack(side="left", padx=(12, 0))

        header_ctrls = ctk.CTkFrame(header_frame, fg_color="transparent")
        header_ctrls.pack(side="right", padx=20, pady=10)

        sens_lbl = ctk.CTkLabel(header_ctrls, text="Sensitivity:", font=ctk.CTkFont(size=12))
        sens_lbl.pack(side="left", padx=(0, 6))

        self.sens_menu = ctk.CTkOptionMenu(
            header_ctrls,
            values=["Low", "Medium", "High"],
            width=100,
            command=self._on_sensitivity_change,
        )
        self.sens_menu.set("Medium")
        self.sens_menu.pack(side="left", padx=(0, 15))

        self.audio_switch = ctk.CTkSwitch(
            header_ctrls,
            text="Sound Alert",
            command=self._on_audio_switch_toggle,
        )
        if self.config.audio_alert_enabled:
            self.audio_switch.select()
        self.audio_switch.pack(side="left", padx=(0, 10))

        self.toast_switch = ctk.CTkSwitch(
            header_ctrls,
            text="Windows Toast",
            command=self._on_toast_switch_toggle,
        )
        if self.config.toast_notification_enabled:
            self.toast_switch.select()
        self.toast_switch.pack(side="left", padx=(0, 12))

        tray_btn = ctk.CTkButton(
            header_ctrls,
            text="Hide to Tray",
            width=90,
            height=28,
            fg_color="#374151",
            hover_color="#4B5563",
            font=ctk.CTkFont(size=11),
            command=self.hide_to_tray,
        )
        tray_btn.pack(side="left")

    def _build_body(self):
        """Central Workspace: Left Video Preview, Right Status Dashboard & Mascot."""
        body_frame = ctk.CTkFrame(self, fg_color="transparent")
        body_frame.pack(fill="both", expand=True, padx=20, pady=15)

        # ----------------- LEFT PANEL: Video & Privacy -----------------
        left_panel = ctk.CTkFrame(body_frame, corner_radius=12)
        left_panel.pack(side="left", fill="both", expand=True, padx=(0, 10))

        left_header = ctk.CTkFrame(left_panel, fg_color="transparent")
        left_header.pack(fill="x", padx=15, pady=(12, 6))

        preview_title = ctk.CTkLabel(
            left_header,
            text="Real-time Camera Feed",
            font=ctk.CTkFont(size=15, weight="bold"),
        )
        preview_title.pack(side="left")

        self.privacy_switch = ctk.CTkSwitch(
            left_header,
            text="Privacy Mode (Hide Feed)",
            command=self._on_privacy_toggle,
        )
        self.privacy_switch.pack(side="right")

        self.video_canvas = tk.Canvas(
            left_panel,
            bg="#121214",
            highlightthickness=0,
            width=540,
            height=405,
        )
        self.video_canvas.pack(fill="both", expand=True, padx=15, pady=10)

        v_ctrls = ctk.CTkFrame(left_panel, fg_color="transparent")
        v_ctrls.pack(fill="x", padx=15, pady=(0, 12))

        self.skeleton_switch = ctk.CTkSwitch(
            v_ctrls,
            text="Show Pose Skeletal Overlay",
            command=self._on_skeleton_toggle,
        )
        self.skeleton_switch.select()
        self.skeleton_switch.pack(side="left")

        self.eye_alert_switch = ctk.CTkSwitch(
            v_ctrls,
            text="Eye Distance Alert (50cm limit)",
            command=self._on_eye_alert_toggle,
        )
        if self.config.eye_distance_warning_enabled:
            self.eye_alert_switch.select()
        self.eye_alert_switch.pack(side="left", padx=(15, 0))

        self.camera_status_lbl = ctk.CTkLabel(
            v_ctrls,
            text="Camera: Initializing...",
            font=ctk.CTkFont(size=12),
            text_color="gray",
        )
        self.camera_status_lbl.pack(side="right")

        # ----------------- RIGHT PANEL: Mascot & Dashboard -----------------
        right_panel = ctk.CTkScrollableFrame(body_frame, width=340, corner_radius=12)
        right_panel.pack(side="right", fill="both", padx=(10, 0))

        # 1. Mascot Section
        self.mascot_widget = MascotWidget(right_panel)
        self.mascot_widget.pack(fill="x", padx=10, pady=(10, 8))

        # 2. Status Badge Card
        self.status_card = ctk.CTkFrame(right_panel, corner_radius=10, fg_color=("gray85", "#27272A"))
        self.status_card.pack(fill="x", padx=10, pady=6)

        status_lbl_title = ctk.CTkLabel(self.status_card, text="CURRENT POSTURE STATUS", font=ctk.CTkFont(size=11, weight="bold"), text_color="gray")
        status_lbl_title.pack(anchor="w", padx=15, pady=(10, 2))

        self.status_badge = ctk.CTkLabel(
            self.status_card,
            text="WAITING FOR USER",
            font=ctk.CTkFont(size=18, weight="bold"),
            text_color="#9CA3AF",
        )
        self.status_badge.pack(anchor="w", padx=15, pady=(0, 4))

        self.reason_badge = ctk.CTkLabel(
            self.status_card,
            text="Initializing posture tracking...",
            font=ctk.CTkFont(size=12),
            wraplength=290,
            text_color="gray",
        )
        self.reason_badge.pack(anchor="w", padx=15, pady=(0, 10))

        self.slouch_timer_lbl = ctk.CTkLabel(self.status_card, text="Slouch Timer: 0.0s / 5.0s", font=ctk.CTkFont(size=11))
        self.slouch_timer_lbl.pack(anchor="w", padx=15, pady=(0, 2))

        self.slouch_progress = ctk.CTkProgressBar(self.status_card, height=8)
        self.slouch_progress.set(0.0)
        self.slouch_progress.configure(progress_color="#FF3B30")
        self.slouch_progress.pack(fill="x", padx=15, pady=(0, 12))

        # 3. Calibration Action Card
        calib_card = ctk.CTkFrame(right_panel, corner_radius=10, fg_color=("gray85", "#27272A"))
        calib_card.pack(fill="x", padx=10, pady=6)

        calib_title = ctk.CTkLabel(calib_card, text="POSTURE CALIBRATION", font=ctk.CTkFont(size=11, weight="bold"), text_color="gray")
        calib_title.pack(anchor="w", padx=15, pady=(10, 4))

        self.calib_hint = ctk.CTkLabel(
            calib_card,
            text="Sit straight and keep your face 50-55 cm (an arm's length) away from the screen, then click Calibrate.",
            font=ctk.CTkFont(size=11),
            wraplength=290,
            text_color="gray",
        )
        self.calib_hint.pack(anchor="w", padx=15, pady=(0, 10))

        self.calib_btn = ctk.CTkButton(
            calib_card,
            text="Calibrate Posture (Sit Straight)",
            font=ctk.CTkFont(size=13, weight="bold"),
            height=34,
            fg_color="#2563EB",
            hover_color="#1D4ED8",
            command=self._on_calibrate_clicked,
        )
        self.calib_btn.pack(fill="x", padx=15, pady=(0, 8))

        self.calib_progress_bar = ctk.CTkProgressBar(calib_card, height=6)
        self.calib_progress_bar.set(0.0)
        self.calib_progress_bar.configure(progress_color="#3B82F6")
        self.calib_progress_bar.pack(fill="x", padx=15, pady=(0, 12))

        # 4. Smart Hydration & Break Reminder Card
        break_card = ctk.CTkFrame(right_panel, corner_radius=10, fg_color=("gray85", "#27272A"))
        break_card.pack(fill="x", padx=10, pady=6)

        break_header = ctk.CTkFrame(break_card, fg_color="transparent")
        break_header.pack(fill="x", padx=15, pady=(10, 4))

        break_title = ctk.CTkLabel(
            break_header,
            text="SMART HYDRATION & BREAK REMINDER",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color="gray",
        )
        break_title.pack(side="left")

        # 1. Enable / Disable Toggle Switch
        break_toggle_row = ctk.CTkFrame(break_card, fg_color="transparent")
        break_toggle_row.pack(fill="x", padx=15, pady=(2, 6))

        self.break_toggle_switch = ctk.CTkSwitch(
            break_toggle_row,
            text="Enable Smart Break Reminder",
            font=ctk.CTkFont(size=12, weight="bold"),
            command=self._on_break_toggle,
        )
        if self.config.sedentary_reminder_enabled:
            self.break_toggle_switch.select()
        else:
            self.break_toggle_switch.deselect()
        self.break_toggle_switch.pack(side="left")

        # 2. Break Interval Selection Dropdown (45m, 60m, 90m, 120m)
        interval_row = ctk.CTkFrame(break_card, fg_color="transparent")
        interval_row.pack(fill="x", padx=15, pady=(0, 6))

        interval_lbl = ctk.CTkLabel(
            interval_row,
            text="Break Interval:",
            font=ctk.CTkFont(size=12),
            text_color=("gray20", "gray80"),
        )
        interval_lbl.pack(side="left")

        self.break_interval_menu = ctk.CTkOptionMenu(
            interval_row,
            values=["45 mins", "60 mins", "90 mins", "120 mins"],
            width=100,
            height=26,
            font=ctk.CTkFont(size=11),
            command=self._on_break_interval_change,
        )
        self.break_interval_menu.set(f"{self.config.sedentary_interval_minutes} mins")
        self.break_interval_menu.pack(side="right")

        # 3. Smart Presence Reset Hint
        smart_hint_lbl = ctk.CTkLabel(
            break_card,
            text="• Smart Reset: Timer auto-resets if you stand up and leave desk for 3+ mins.",
            font=ctk.CTkFont(size=11),
            text_color="gray",
            wraplength=290,
            justify="left",
        )
        smart_hint_lbl.pack(anchor="w", padx=15, pady=(0, 6))

        self.break_timer_lbl = ctk.CTkLabel(
            break_card,
            text=f"Active Sitting: 0m 00s / {self.config.sedentary_interval_minutes}m",
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color="#3B82F6",
        )
        self.break_timer_lbl.pack(anchor="w", padx=15, pady=(0, 4))

        self.break_progress = ctk.CTkProgressBar(break_card, height=8)
        self.break_progress.set(0.0)
        self.break_progress.configure(progress_color="#3B82F6")
        self.break_progress.pack(fill="x", padx=15, pady=(0, 10))

        self.reset_break_btn = ctk.CTkButton(
            break_card,
            text="Reset Break Timer",
            font=ctk.CTkFont(size=12),
            height=28,
            fg_color="#374151",
            hover_color="#4B5563",
            command=lambda: self.reset_break_timer(notify=False),
        )
        self.reset_break_btn.pack(fill="x", padx=15, pady=(0, 12))

        # 5. Session Posture Score Card
        score_card = ctk.CTkFrame(right_panel, corner_radius=10, fg_color=("gray85", "#27272A"))
        score_card.pack(fill="x", padx=10, pady=(6, 12))

        score_title = ctk.CTkLabel(score_card, text="SESSION ERGONOMIC SCORE", font=ctk.CTkFont(size=11, weight="bold"), text_color="gray")
        score_title.pack(anchor="w", padx=15, pady=(10, 2))

        self.score_value_lbl = ctk.CTkLabel(
            score_card,
            text="100%",
            font=ctk.CTkFont(size=24, weight="bold"),
            text_color="#10B981",
        )
        self.score_value_lbl.pack(anchor="w", padx=15, pady=(0, 2))

        self.score_desc_lbl = ctk.CTkLabel(
            score_card,
            text="Time spent in good posture during this session.",
            font=ctk.CTkFont(size=11),
            text_color="gray",
        )
        self.score_desc_lbl.pack(anchor="w", padx=15, pady=(0, 10))

        # 6. Desktop & System Settings Card
        desktop_card = ctk.CTkFrame(right_panel, corner_radius=10, fg_color=("gray85", "#27272A"))
        desktop_card.pack(fill="x", padx=10, pady=(6, 14))

        desktop_title = ctk.CTkLabel(desktop_card, text="DESKTOP & SYSTEM SETTINGS", font=ctk.CTkFont(size=11, weight="bold"), text_color="gray")
        desktop_title.pack(anchor="w", padx=15, pady=(10, 6))

        self.autostart_checkbox = ctk.CTkCheckBox(
            desktop_card,
            text="Start PosturFix on system startup",
            font=ctk.CTkFont(size=12),
            command=self._on_autostart_checkbox_toggle,
        )
        if self.is_autostart_active():
            self.autostart_checkbox.select()
        else:
            self.autostart_checkbox.deselect()
        self.autostart_checkbox.pack(anchor="w", padx=15, pady=(0, 6))

        minimize_hint = ctk.CTkLabel(
            desktop_card,
            text="Closing this window ('X') minimizes PosturFix to the System Tray so posture tracking continues uninterrupted.",
            font=ctk.CTkFont(size=11),
            text_color="gray",
            wraplength=290,
            justify="left",
        )
        minimize_hint.pack(anchor="w", padx=15, pady=(0, 10))

        # 7. About PosturFix & Update Checker Card
        about_card = ctk.CTkFrame(right_panel, corner_radius=10, fg_color=("gray85", "#27272A"))
        about_card.pack(fill="x", padx=10, pady=(6, 16))

        about_title = ctk.CTkLabel(
            about_card,
            text="ABOUT POSTURFIX",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color="gray",
        )
        about_title.pack(anchor="w", padx=15, pady=(10, 4))

        info_row = ctk.CTkFrame(about_card, fg_color="transparent")
        info_row.pack(fill="x", padx=15, pady=(0, 2))

        app_name_lbl = ctk.CTkLabel(
            info_row,
            text=f"PosturFix v{APP_VERSION}",
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color=("gray10", "gray95"),
        )
        app_name_lbl.pack(side="left")

        app_build_badge = ctk.CTkLabel(
            info_row,
            text=" Stable • 100% Offline ",
            font=ctk.CTkFont(size=10, weight="bold"),
            text_color="#10B981",
            fg_color=("#D1FAE5", "#064E3B"),
            corner_radius=4,
        )
        app_build_badge.pack(side="left", padx=8)

        copyright_lbl = ctk.CTkLabel(
            about_card,
            text=APP_COPYRIGHT,
            font=ctk.CTkFont(size=11),
            text_color="gray",
        )
        copyright_lbl.pack(anchor="w", padx=15, pady=(0, 6))

        self.update_status_lbl = ctk.CTkLabel(
            about_card,
            text="Status: Up to date",
            font=ctk.CTkFont(size=11),
            text_color="gray70",
        )
        self.update_status_lbl.pack(anchor="w", padx=15, pady=(0, 6))

        self.check_update_btn = ctk.CTkButton(
            about_card,
            text="Check for Updates",
            height=32,
            font=ctk.CTkFont(size=12, weight="bold"),
            command=self.check_for_updates,
        )
        self.check_update_btn.pack(fill="x", padx=15, pady=(0, 12))

    def _build_footer(self):
        """Bottom Information Strip."""
        footer_frame = ctk.CTkFrame(self, height=32, corner_radius=0, fg_color=("gray92", "#121214"))
        footer_frame.pack(fill="x", side="bottom")

        self.fps_lbl = ctk.CTkLabel(footer_frame, text="FPS: --", font=ctk.CTkFont(size=11), text_color="gray")
        self.fps_lbl.pack(side="left", padx=20)

        privacy_note = ctk.CTkLabel(
            footer_frame,
            text="🔒 100% Offline • In-Memory Only • Closing window minimizes to System Tray",
            font=ctk.CTkFont(size=11),
            text_color="#10B981",
        )
        privacy_note.pack(side="right", padx=20)

    # ----------------- CAMERA & PROCESSING LOOP -----------------

    def start_camera(self):
        """Initializes the background camera worker thread for non-blocking posture checks."""
        if not self.camera_running:
            self.camera_running = True
            self._camera_thread = threading.Thread(target=self._camera_worker, daemon=True)
            self._camera_thread.start()

        if not getattr(self, "_ui_poll_started", False):
            self._ui_poll_started = True
            self.after(33, self._ui_poll_loop)

    def _open_camera_safe(self) -> Optional[cv2.VideoCapture]:
        """
        Safely attempts to initialize the webcam.
        Catches device locks, Zoom/Meet conflicts, driver crashes, and empty frames.
        Returns cv2.VideoCapture instance if operational, otherwise None.
        """
        cap = None
        try:
            # On Windows, cv2.CAP_DSHOW is reliable, fast, and avoids MSMF driver lockups
            backend = cv2.CAP_DSHOW if sys.platform == "win32" else cv2.CAP_ANY
            cap = cv2.VideoCapture(self.config.camera_index, backend)
            if not cap.isOpened() and sys.platform != "win32":
                cap = cv2.VideoCapture(self.config.camera_index)

            if cap.isOpened():
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.config.frame_width)
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.config.frame_height)
                try:
                    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                except Exception:
                    pass
                # Warm-up loop: webcams need a few hundred milliseconds to negotiate capture graph
                for _ in range(12):
                    ret, test_frame = cap.read()
                    if ret and test_frame is not None and test_frame.size > 0:
                        try:
                            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                        except Exception:
                            pass
                        return cap
                    time.sleep(0.08)

                try:
                    cap.release()
                except Exception:
                    pass
                return None
        except Exception:
            if cap:
                try:
                    cap.release()
                except Exception:
                    pass
        return None

    def _camera_worker(self):
        """
        Dedicated background worker thread for camera capture and MediaPipe Pose analysis.
        - Background Mode (Tray): checks posture every 3.0 seconds (battery-saving, low CPU).
        - Foreground Mode (Visible): runs at ~20 FPS for fluid, real-time webcam feedback.
        - Deep Sleep Mode: powers down camera after 2m of global OS inactivity.
        - Snooze / Pause Mode: completely releases webcam and sleeps until timer expires or resumed.
        - Camera Conflict Handling: backs off if locked by Zoom/Meet with progressive retry.
        - Thread-Safe & Non-Blocking: Tkinter mainloop and System Tray menu never hitch or lag.
        """
        last_check_time = 0.0
        fps_frame_count = 0
        fps_start_time = time.time()
        was_calibrating = False
        is_in_deep_sleep = False
        consecutive_read_failures = 0
        camera_error_streak = 0

        while self.camera_running:
            try:
                now = time.time()
                is_visible = self.is_window_visible
                interval = self.config.posture_check_interval_seconds  # 3.0 seconds
                is_calibrating = self.engine.calibrating

                # -------------------------------------------------------------
                # 1. SNOOZE / PAUSE CHECK
                # If user selected Pause for 30m / 1hr from tray:
                # Completely bypass camera loop (0% CPU, 0 camera hardware usage).
                # -------------------------------------------------------------
                if self.is_snoozed():
                    if self.cap is not None:
                        try:
                            self.cap.release()
                        except Exception:
                            pass
                        self.cap = None

                    rem_seconds = self.snooze_until - now
                    rem_mins = max(1, int(math.ceil(rem_seconds / 60.0)))
                    if hasattr(self, "tray_icon") and self.tray_icon:
                        self.tray_icon.title = f"PosturFix: Paused ({rem_mins}m left)"

                    if is_visible:
                        self.after(0, lambda m=rem_mins: self._render_privacy_screen(
                            f"Posture Monitoring Paused\n{m} minutes remaining\nRight-click tray icon and select Resume to continue"
                        ))
                    self.after(0, lambda m=rem_mins: self.camera_status_lbl.configure(
                        text=f"Camera: Paused ({m}m left)", text_color="#F59E0B"
                    ))

                    # Sleep on event without CPU polling (up to 10s or until user clicks Resume)
                    self._snooze_event.wait(timeout=min(10.0, max(0.5, rem_seconds)))
                    self._snooze_event.clear()
                    continue

                # -------------------------------------------------------------
                # 2. DEEP SLEEP CHECK (OS Idle Detection)
                # If no mouse or keyboard activity for 2 minutes (120s),
                # completely shut off camera and pause 3s interval checks.
                # -------------------------------------------------------------
                idle_sec = self.idle_detector.get_idle_seconds()
                should_deep_sleep = (
                    self.config.deep_sleep_enabled
                    and not is_calibrating
                    and idle_sec >= self.config.idle_sleep_threshold_seconds
                )

                if should_deep_sleep:
                    if not is_in_deep_sleep:
                        is_in_deep_sleep = True
                        self.is_deep_sleeping = True
                        if self.cap is not None:
                            try:
                                self.cap.release()
                            except Exception:
                                pass
                            self.cap = None

                        if hasattr(self, "tray_icon") and self.tray_icon:
                            self.tray_icon.title = "PosturFix: Deep Sleep (Zzz) • Camera Off"

                        if is_visible:
                            self.after(0, lambda: self._render_privacy_screen(
                                "Deep Sleep Mode Active\nNo Mouse/Keyboard Activity for 2m • Camera Off"
                            ))
                        self.after(0, lambda: self.camera_status_lbl.configure(
                            text="Camera: Deep Sleep (Powered Off)", text_color="#3B82F6"
                        ))

                    # While in Deep Sleep:
                    # Do not open cv2.VideoCapture(0). Zero camera usage.
                    # Sleep in small slices to detect user wake-up promptly.
                    self._camera_retry_event.wait(timeout=0.5)
                    self._camera_retry_event.clear()

                    # Away tracking for break timer auto-reset
                    if self._away_start_time is None:
                        self._away_start_time = now
                    elif (now - self._away_start_time) >= self.config.sedentary_auto_reset_away_seconds:
                        self.sitting_seconds = 0.0

                    continue

                # -------------------------------------------------------------
                # 3. AUTO-WAKE FROM DEEP SLEEP
                # User moved mouse or pressed key -> immediately wake up!
                # -------------------------------------------------------------
                if is_in_deep_sleep:
                    is_in_deep_sleep = False
                    self.is_deep_sleeping = False
                    self._last_sitting_tick = now
                    if hasattr(self, "tray_icon") and self.tray_icon:
                        self.tray_icon.title = "PosturFix: Waking from Deep Sleep..."

                # -------------------------------------------------------------
                # 4. CAMERA CONFLICT HANDLING & INITIALIZATION
                # If camera is closed or locked by another app (Zoom/Teams/Meet),
                # safely retry without crashing or busy looping.
                # -------------------------------------------------------------
                if self.cap is None or not self.cap.isOpened():
                    self.cap = self._open_camera_safe()
                    if self.cap is None:
                        camera_error_streak += 1
                        if hasattr(self, "tray_icon") and self.tray_icon:
                            self.tray_icon.title = "PosturFix: Camera in use by another app"

                        conflict_msg = "Camera is in use by another app. PosturFix tracking is paused."
                        self.after(0, lambda: self.camera_status_lbl.configure(
                            text=conflict_msg, text_color="#EF4444"
                        ))
                        if is_visible:
                            self.after(0, lambda: self._render_privacy_screen(
                                "Camera is in use by another app (Zoom, Meet, or Teams)\nPosturFix tracking is paused.\nReconnecting automatically..."
                            ))

                        if not self._has_notified_camera_conflict:
                            self._has_notified_camera_conflict = True
                            self._dispatch_native_toast(
                                "PosturFix Alert",
                                conflict_msg,
                            )

                        retry_sec = 3.0 if camera_error_streak <= 3 else 10.0
                        self._camera_retry_event.wait(timeout=retry_sec)
                        self._camera_retry_event.clear()
                        continue
                    else:
                        camera_error_streak = 0
                        consecutive_read_failures = 0
                        if self._has_notified_camera_conflict:
                            self._has_notified_camera_conflict = False
                            if hasattr(self, "tray_icon") and self.tray_icon:
                                try:
                                    self.tray_icon.notify(
                                        "Camera reconnected! PosturFix tracking resumed.",
                                        "PosturFix • Resumed",
                                    )
                                except Exception:
                                    pass
                        self.after(0, lambda: self.camera_status_lbl.configure(
                            text=f"Camera: Connected ({self.config.frame_width}x{self.config.frame_height} Lite)",
                            text_color="#10B981"
                        ))

                # In background tray mode: only grab and check once every 3.0 seconds (unless calibrating)
                if not is_visible and not is_calibrating:
                    elapsed = now - last_check_time
                    if elapsed < interval:
                        sleep_time = min(0.3, max(0.05, interval - elapsed))
                        time.sleep(sleep_time)
                        continue

                # Read frame from camera with anti-crash protection
                ret, frame = False, None
                try:
                    ret, frame = self.cap.read()
                except Exception as read_err:
                    print(f"[PosturFix Camera Warning] Exception during cap.read(): {read_err}", file=sys.stderr)
                    ret, frame = False, None

                # Camera conflict / empty frame check & Frame Drop Protection
                if not ret or frame is None or frame.size == 0:
                    consecutive_read_failures += 1
                    print(
                        f"[PosturFix Camera Warning] Empty or dropped frame detected (consecutive: {consecutive_read_failures})",
                        file=sys.stderr,
                    )

                    # Flush buffer if possible to clear corrupted driver state
                    try:
                        if self.cap is not None and self.cap.isOpened():
                            self.cap.grab()
                    except Exception:
                        pass

                    # Tolerate brief drops without releasing camera graph (e.g., auto-exposure shifts, USB jitter)
                    # Tolerate up to 30 consecutive drops (~1.5s) before treating as camera disconnect
                    if consecutive_read_failures < 30:
                        time.sleep(0.05)
                        continue

                    # Camera truly disconnected or seized by another app after 30 consecutive failed reads
                    consecutive_read_failures = 0
                    if self.cap is not None:
                        try:
                            self.cap.release()
                        except Exception:
                            pass
                        self.cap = None

                    if hasattr(self, "tray_icon") and self.tray_icon:
                        self.tray_icon.title = "PosturFix: Camera in use by another app"

                    conflict_msg = "Camera is in use by another app. PosturFix tracking is paused."
                    self.after(0, lambda: self.camera_status_lbl.configure(
                        text=conflict_msg, text_color="#EF4444"
                    ))
                    if is_visible:
                        self.after(0, lambda: self._render_privacy_screen(
                            "Camera is in use by another app (Zoom, Meet, or Teams)\nPosturFix tracking is paused.\nReconnecting automatically..."
                        ))

                    if not self._has_notified_camera_conflict:
                        self._has_notified_camera_conflict = True
                        self._dispatch_native_toast(
                            "PosturFix Alert",
                            conflict_msg,
                        )

                    self._camera_retry_event.wait(timeout=3.0)
                    self._camera_retry_event.clear()
                    continue

                consecutive_read_failures = 0

                frame = cv2.flip(frame, 1)

                # Requirement 2: Downscale frame resolution to 640x480 before MediaPipe
                h_f, w_f = frame.shape[:2]
                if w_f > self.config.inference_width or h_f > self.config.inference_height:
                    frame = cv2.resize(
                        frame,
                        (self.config.inference_width, self.config.inference_height),
                        interpolation=cv2.INTER_NEAREST,
                    )

                # Process frame through MediaPipe Pose Engine (model_complexity=0 Lite)
                try:
                    annotated_frame, metrics = self.engine.process_frame(frame)
                except Exception as e:
                    print(f"[PosturFix Worker] Error during process_frame: {e}", file=sys.stderr)
                    annotated_frame = frame
                    metrics = PostureMetrics(state=PostureState.NO_PERSON)

                self.current_metrics = metrics
                last_check_time = now

                # Notify if quick calibration completed in background tray
                if was_calibrating and not self.engine.calibrating:
                    if hasattr(self, "tray_icon") and self.tray_icon:
                        try:
                            self.tray_icon.notify(
                                "Baseline posture calibrated successfully!",
                                "PosturFix • Calibrated",
                            )
                        except Exception:
                            pass
                was_calibrating = self.engine.calibrating

                # FPS Calculation when visible
                if is_visible:
                    fps_frame_count += 1
                    fps_dur = now - fps_start_time
                    if fps_dur >= 1.0:
                        current_fps = fps_frame_count / fps_dur
                        fps_frame_count = 0
                        fps_start_time = now
                        self.after(0, lambda f=current_fps: self.fps_lbl.configure(text=f"FPS: {f:.1f}"))

                # Requirement 2: Fast-Fail Logic (Idle Mode)
                # If no landmarks are detected, immediately skip posture math and sleep for the 3s interval
                if metrics.state == PostureState.NO_PERSON:
                    if self._away_start_time is None:
                        self._away_start_time = now
                    elif (now - self._away_start_time) >= self.config.sedentary_auto_reset_away_seconds:
                        self.sitting_seconds = 0.0

                    if hasattr(self, "tray_icon") and self.tray_icon:
                        sitting_m = int(self.sitting_seconds // 60)
                        self.tray_icon.title = f"PosturFix: Away / No Person • Sitting: {sitting_m}m"

                    if is_visible:
                        self._push_frame_to_queue(annotated_frame, metrics)
                        time.sleep(0.05)  # Fast motion ~20 FPS when window is open
                    else:
                        time.sleep(interval)  # 3.0s sleep in background
                    continue

                # User is detected: accumulate active sitting time
                dt = max(0.0, min(interval * 1.5, now - self._last_sitting_tick))
                self._last_sitting_tick = now
                self._away_start_time = None
                self.sitting_seconds += dt

                # Check Sedentary Break threshold (default 45 min)
                target_seconds = self.config.sedentary_interval_minutes * 60.0
                if self.config.sedentary_reminder_enabled and self.sitting_seconds >= target_seconds:
                    self.after(0, self.trigger_sedentary_alert)

                # Reset slouch toast cooldown if user has returned to good posture
                if metrics.state == PostureState.GOOD:
                    with self._slouch_toast_lock:
                        self._last_slouch_toast_time = 0.0

                # 1. Independent Posture Slouching Toast Notification:
                # Triggered strictly when slouch timer reaches 5 seconds (synchronized with audio alert)
                if metrics.state == PostureState.SLOUCHING and metrics.alert_fired:
                    self.trigger_slouch_toast(metrics)

                # 2. Independent Eye-to-Screen Distance Alert (15s consecutive buffer, 2-minute cooldown)
                if self.config.eye_distance_warning_enabled and metrics.is_screen_too_close_sustained:
                    self.trigger_eye_distance_toast(metrics)

                # Update tray tooltip dynamically
                if self.config.eye_distance_warning_enabled and metrics.is_screen_too_close_sustained:
                    status_text = "ALERT: Screen Too Close / Lean Back!"
                else:
                    status_text = {
                        PostureState.GOOD: "Good Posture",
                        PostureState.WARNING: "Warning: Slouching",
                        PostureState.SLOUCHING: "ALERT: Slouching Sustained",
                        PostureState.CALIBRATING: "Calibrating...",
                        PostureState.UNCALIBRATED: "Uncalibrated",
                    }.get(metrics.state, "Running")

                if hasattr(self, "tray_icon") and self.tray_icon:
                    score = int(metrics.session_good_posture_percentage)
                    sitting_m = int(self.sitting_seconds // 60)
                    target_m = self.config.sedentary_interval_minutes
                    self.tray_icon.title = f"PosturFix: {status_text} ({score}% Good) • Sitting: {sitting_m}m/{target_m}m"

                if is_visible:
                    # Push frame to single-slot queue; drops old frames to prevent UI lag
                    self._push_frame_to_queue(annotated_frame, metrics)
                    # When visible, small delay for smooth preview (fast motion)
                    delay = 0.08 if self.engine.calibrating else 0.05
                    time.sleep(delay)
                else:
                    # When hidden in tray, sleep for interval (or adjust sleep to hit 5.0s slouch check precisely)
                    if metrics.state == PostureState.WARNING and metrics.slouch_duration > 0:
                        rem = max(0.5, self.config.slouch_alert_delay_seconds - metrics.slouch_duration)
                        time.sleep(min(interval, rem))
                    else:
                        time.sleep(0.1 if is_calibrating else interval)

            except Exception as e:
                import traceback
                print(f"[PosturFix Worker Error]: {e}", file=sys.stderr)
                time.sleep(0.2)

        # Release capture when thread terminates
        if self.cap and self.cap.isOpened():
            try:
                self.cap.release()
            except Exception:
                pass

    def _push_frame_to_queue(self, annotated_frame: np.ndarray, metrics: PostureMetrics):
        """
        Thread-safe frame producer: Pushes the newest frame to the bounded queue.
        Uses put_nowait() and drops older frames if the GUI is reading slower than the camera is capturing.
        Guarantees the worker thread never blocks and the queue never overflows or leaks memory.
        """
        try:
            self._frame_queue.put_nowait((annotated_frame, metrics))
        except queue.Full:
            try:
                # Queue is full: remove the oldest unconsumed frame and insert the newest one
                self._frame_queue.get_nowait()
                self._frame_queue.put_nowait((annotated_frame, metrics))
            except Exception:
                pass
        except Exception:
            pass

    def _ui_poll_loop(self):
        """
        Main GUI thread consumer: Polls the latest processed frame from the queue.
        Drains any queued backlog to ensure the GUI only displays the freshest frame without lag.
        """
        if not self.camera_running:
            return

        try:
            if self.is_window_visible:
                latest_item = None
                while True:
                    try:
                        latest_item = self._frame_queue.get_nowait()
                    except queue.Empty:
                        break

                if latest_item is not None:
                    frame, metrics = latest_item
                    self._update_ui_frame(frame, metrics)
        except Exception:
            pass
        finally:
            if self.camera_running:
                self.after(30, self._ui_poll_loop)

    def _update_ui_frame(self, annotated_frame: np.ndarray, metrics: PostureMetrics):
        """Thread-safe UI dispatcher: updates dashboard and camera canvas on Tkinter main thread."""
        if not self.is_window_visible:
            return
        try:
            self._update_dashboard(metrics)
            self._render_video_feed(annotated_frame)
        except Exception:
            pass

    def _render_video_feed(self, frame_bgr: np.ndarray):
        """Displays video frame on the Tkinter canvas or shows privacy shield."""
        if self.privacy_mode:
            self._render_privacy_screen("Privacy Mode Active\nCamera Preview Hidden • Analysis Active in RAM")
            return

        try:
            canvas_w = self.video_canvas.winfo_width()
            canvas_h = self.video_canvas.winfo_height()
            if canvas_w < 50 or canvas_h < 50:
                canvas_w = 540
                canvas_h = 405

            h, w, _ = frame_bgr.shape
            scale = min(canvas_w / w, canvas_h / h)
            new_w, new_h = max(1, int(w * scale)), max(1, int(h * scale))

            resized_bgr = cv2.resize(frame_bgr, (new_w, new_h), interpolation=cv2.INTER_LINEAR)
            frame_rgb = cv2.cvtColor(resized_bgr, cv2.COLOR_BGR2RGB)

            pil_img = Image.fromarray(frame_rgb)
            self._current_photo = ImageTk.PhotoImage(pil_img)

            self.video_canvas.delete("all")
            pos_x = (canvas_w - new_w) // 2
            pos_y = (canvas_h - new_h) // 2
            self.video_canvas.create_image(pos_x, pos_y, anchor="nw", image=self._current_photo)
        except Exception:
            pass

    def _render_privacy_screen(self, message: str):
        """Renders an elegant privacy shield visual when camera preview is suppressed."""
        canvas_w = max(10, self.video_canvas.winfo_width())
        canvas_h = max(10, self.video_canvas.winfo_height())

        self.video_canvas.delete("all")
        cx, cy = canvas_w // 2, canvas_h // 2

        shield_color = "#10B981"
        self.video_canvas.create_oval(cx - 38, cy - 65, cx + 38, cy + 11, fill="#182721", outline=shield_color, width=2)
        self.video_canvas.create_text(cx, cy - 27, text="🔒", font=("Segoe UI Emoji", 28), fill=shield_color)

        self.video_canvas.create_text(
            cx,
            cy + 42,
            text=message,
            font=("Segoe UI", 12, "bold"),
            fill="#E5E7EB",
            justify="center",
        )

    def _update_dashboard(self, metrics: PostureMetrics):
        """Updates status badges, slouch progress countdown, break timer, and mascot."""
        state = metrics.state
        self.mascot_widget.update_state(state, metrics.slouch_reason)

        state_ui_map = {
            PostureState.GOOD: ("GOOD POSTURE", "#10B981", "#064E3B"),
            PostureState.WARNING: ("SLOUCH DETECTED", "#F59E0B", "#78350F"),
            PostureState.SLOUCHING: ("SLOUCHING ALERT!", "#EF4444", "#7F1D1D"),
            PostureState.CALIBRATING: ("CALIBRATING...", "#3B82F6", "#1E3A8A"),
            PostureState.UNCALIBRATED: ("UNCALIBRATED", "#9CA3AF", "#374151"),
            PostureState.NO_PERSON: ("NO PERSON DETECTED", "#6B7280", "#1F2937"),
        }

        title, text_color, _ = state_ui_map.get(state, ("UNKNOWN", "#9CA3AF", "#374151"))
        if self.config.eye_distance_warning_enabled and metrics.is_screen_too_close_sustained:
            title = "SCREEN TOO CLOSE / LEAN BACK!"
            text_color = "#EF4444"

        self.status_badge.configure(text=title, text_color=text_color)
        reason_text = metrics.slouch_reason or "All posture metrics normal."
        if self.config.eye_distance_warning_enabled and metrics.is_screen_too_close_sustained:
            if reason_text in ("All posture metrics normal.", "Good posture maintained"):
                reason_text = "Face closer than 50cm for >15s. Please lean back."
            else:
                reason_text += " • Face closer than 50cm (>15s)"
        self.reason_badge.configure(text=reason_text)

        # Slouch Timer
        if state in (PostureState.WARNING, PostureState.SLOUCHING):
            elapsed = metrics.slouch_duration
            max_delay = self.config.slouch_alert_delay_seconds
            fraction = min(1.0, elapsed / max_delay)
            self.slouch_timer_lbl.configure(text=f"Slouch Timer: {elapsed:.1f}s / {max_delay:.1f}s")
            self.slouch_progress.set(fraction)
        else:
            self.slouch_timer_lbl.configure(text="Slouch Timer: 0.0s / 5.0s")
            self.slouch_progress.set(0.0)

        # Calibration
        if state == PostureState.CALIBRATING:
            self.calib_progress_bar.set(metrics.calibration_progress)
            self.calib_btn.configure(text="Calibrating... Hold Still", state="disabled")
        else:
            self.calib_progress_bar.set(1.0 if metrics.is_calibrated else 0.0)
            self.calib_btn.configure(
                text="Re-Calibrate Posture" if metrics.is_calibrated else "Calibrate Posture (Sit Straight)",
                state="normal",
            )

        # Smart Hydration & Break Timer Progress
        sitting_mins = int(self.sitting_seconds // 60)
        sitting_secs = int(self.sitting_seconds % 60)
        target_mins = self.config.sedentary_interval_minutes
        target_secs = target_mins * 60.0
        break_fraction = min(1.0, max(0.0, self.sitting_seconds / target_secs))

        if self.config.sedentary_reminder_enabled:
            if self._away_start_time is not None:
                away_elapsed = int(time.time() - self._away_start_time)
                away_rem = max(0, int(self.config.sedentary_auto_reset_away_seconds - away_elapsed))
                self.break_timer_lbl.configure(
                    text=f"Active Sitting: {sitting_mins}m {sitting_secs:02d}s / {target_mins}m (Away: reset in {away_rem}s)",
                    text_color="#9CA3AF",
                )
            else:
                self.break_timer_lbl.configure(
                    text=f"Active Sitting: {sitting_mins}m {sitting_secs:02d}s / {target_mins}m",
                    text_color="#3B82F6",
                )
            self.break_progress.set(break_fraction)
            if break_fraction >= 0.9:
                self.break_progress.configure(progress_color="#EF4444")
            elif break_fraction >= 0.7:
                self.break_progress.configure(progress_color="#F59E0B")
            else:
                self.break_progress.configure(progress_color="#3B82F6")
        else:
            self.break_timer_lbl.configure(
                text=f"Break Reminder: Disabled ({sitting_mins}m logged)",
                text_color="gray",
            )
            self.break_progress.set(0.0)

        # Session Posture Score
        score = metrics.session_good_posture_percentage
        self.score_value_lbl.configure(text=f"{int(score)}%")
        if score >= 85:
            self.score_value_lbl.configure(text_color="#10B981")
        elif score >= 70:
            self.score_value_lbl.configure(text_color="#F59E0B")
        else:
            self.score_value_lbl.configure(text_color="#EF4444")

    # ----------------- EVENT HANDLERS -----------------

    def _on_calibrate_clicked(self):
        self.engine.start_calibration()

    def _on_privacy_toggle(self):
        self.privacy_mode = self.privacy_switch.get() == 1

    def _on_skeleton_toggle(self):
        self.config.draw_skeleton = self.skeleton_switch.get() == 1

    def _on_sensitivity_change(self, choice: str):
        self.engine.set_sensitivity(choice)

    def _on_audio_switch_toggle(self):
        self.config.audio_alert_enabled = self.audio_switch.get() == 1

    def _on_toast_switch_toggle(self):
        self.config.toast_notification_enabled = self.toast_switch.get() == 1

    def _on_eye_alert_toggle(self):
        self.config.eye_distance_warning_enabled = self.eye_alert_switch.get() == 1
        if not self.config.eye_distance_warning_enabled:
            self.engine._eye_close_start_time = None

    def _on_break_toggle(self):
        """Toggles the Smart Hydration & Break Reminder on or off."""
        self.config.sedentary_reminder_enabled = self.break_toggle_switch.get() == 1
        sitting_mins = int(self.sitting_seconds // 60)
        sitting_secs = int(self.sitting_seconds % 60)
        target_mins = self.config.sedentary_interval_minutes
        if not self.config.sedentary_reminder_enabled:
            self.break_timer_lbl.configure(
                text=f"Break Reminder: Disabled ({sitting_mins}m logged)",
                text_color="gray",
            )
            self.break_progress.set(0.0)
        else:
            self.break_timer_lbl.configure(
                text=f"Active Sitting: {sitting_mins}m {sitting_secs:02d}s / {target_mins}m",
                text_color="#3B82F6",
            )
            break_fraction = min(1.0, max(0.0, self.sitting_seconds / (target_mins * 60.0)))
            self.break_progress.set(break_fraction)

    def _on_break_interval_change(self, choice: str):
        try:
            mins = int("".join(filter(str.isdigit, choice)))
            if mins > 0:
                self.config.sedentary_interval_minutes = mins
                sitting_mins = int(self.sitting_seconds // 60)
                sitting_secs = int(self.sitting_seconds % 60)
                if self.config.sedentary_reminder_enabled:
                    self.break_timer_lbl.configure(text=f"Active Sitting: {sitting_mins}m {sitting_secs:02d}s / {mins}m")
                    break_fraction = min(1.0, max(0.0, self.sitting_seconds / (mins * 60.0)))
                    self.break_progress.set(break_fraction)
        except Exception:
            pass

    # ----------------- ABOUT & UPDATE CHECKER -----------------

    def check_for_updates(self, from_tray: bool = False):
        """
        Triggers an asynchronous check for updates.
        Non-blocking background thread prevents any UI or camera stutter.
        """
        if hasattr(self, "check_update_btn"):
            self.check_update_btn.configure(state="disabled", text="Checking...")
        if hasattr(self, "update_status_lbl"):
            self.update_status_lbl.configure(text="Checking for updates...", text_color="gray")

        worker = threading.Thread(
            target=self._async_check_updates_worker,
            args=(from_tray,),
            daemon=True,
            name="PosturFix-UpdateChecker",
        )
        worker.start()

    def _async_check_updates_worker(self, from_tray: bool):
        """Worker thread to fetch remote version.json and compare versions."""
        url = UPDATE_CHECK_URL
        headers = {"User-Agent": f"PosturFix/{APP_VERSION} (Desktop; Windows)"}
        req = urllib.request.Request(url, headers=headers)

        latest_version = None
        download_url = None
        release_notes = ""
        error_msg = None

        try:
            with urllib.request.urlopen(req, timeout=5) as response:
                if response.status == 200:
                    raw_data = response.read().decode("utf-8")
                    data = json.loads(raw_data)
                    latest_version = data.get("latest_version", "").strip()
                    download_url = data.get("download_url", "").strip()
                    release_notes = data.get("release_notes", "")
                else:
                    error_msg = f"HTTP {response.status}"
        except Exception as e:
            error_msg = str(e)

        self.after(
            0,
            lambda: self._handle_update_check_result(
                latest_version=latest_version,
                download_url=download_url,
                release_notes=release_notes,
                error_msg=error_msg,
                from_tray=from_tray,
            ),
        )

    def _handle_update_check_result(
        self,
        latest_version: Optional[str],
        download_url: Optional[str],
        release_notes: str,
        error_msg: Optional[str],
        from_tray: bool,
    ):
        """Processes update check results safely on main GUI thread."""
        if hasattr(self, "check_update_btn"):
            self.check_update_btn.configure(state="normal", text="Check for Updates")

        if error_msg or not latest_version:
            if hasattr(self, "update_status_lbl"):
                self.update_status_lbl.configure(
                    text="Check failed: offline / connection error",
                    text_color="#EF4444",
                )
            if from_tray and hasattr(self, "tray_icon") and self.tray_icon:
                try:
                    self.tray_icon.notify(
                        "Unable to check for updates. Please verify your internet connection.",
                        "PosturFix • Update Check",
                    )
                except Exception:
                    pass
            return

        current_tup = parse_version(APP_VERSION)
        latest_tup = parse_version(latest_version)

        if latest_tup > current_tup:
            if hasattr(self, "update_status_lbl"):
                self.update_status_lbl.configure(
                    text=f"New version v{latest_version} available!",
                    text_color="#3B82F6",
                )
            # Display interactive modal prompt
            target_download = download_url or "https://mayar.id/posturfix"
            UpdateDialog(
                parent=self,
                current_version=APP_VERSION,
                latest_version=latest_version,
                download_url=target_download,
                release_notes=release_notes,
            )
            # Dispatch native notification
            self._dispatch_native_toast(
                "Update Available",
                f"A new version (v{latest_version}) of PosturFix is available! Click to download.",
            )
        else:
            if hasattr(self, "update_status_lbl"):
                self.update_status_lbl.configure(
                    text="You are on the latest version.",
                    text_color="#10B981",
                )
            # Display toast / message
            self._dispatch_native_toast(
                "PosturFix • Up to Date",
                f"You are on the latest version (v{APP_VERSION}).",
            )


# ----------------- SINGLE INSTANCE LOCK (MUTEX) -----------------

class SingleInstance:
    """
    Enforces a strict Single-Instance Lock (Mutex) across the operating system.
    Uses a Windows Named Mutex on Windows and localhost socket binding on Unix/macOS.
    Guarantees no duplicate background processes, tray icons, or camera hardware lockups.
    """

    def __init__(self, app_id: str = "PosturFix_SingleInstance_AppLock"):
        self.app_id = app_id
        self.mutex = None
        self.sock = None

    def acquire(self) -> bool:
        """
        Attempts to acquire the single-instance lock.
        Returns True if this is the first/primary instance, False if already running.
        """
        if sys.platform == "win32":
            import ctypes
            kernel32 = ctypes.windll.kernel32
            # Use Local\ namespace so it scopes per user desktop session
            mutex_name = f"Local\\{self.app_id}"
            self.mutex = kernel32.CreateMutexW(None, False, mutex_name)
            ERROR_ALREADY_EXISTS = 183
            if kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
                if self.mutex:
                    kernel32.CloseHandle(self.mutex)
                    self.mutex = None
                return False
            return True
        else:
            import socket
            try:
                self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                # Bind to loopback on an ephemeral dedicated high port
                self.sock.bind(("127.0.0.1", 49582))
                self.sock.listen(1)
                return True
            except (socket.error, OSError):
                return False

    def release(self):
        """Releases the lock on application shutdown."""
        if sys.platform == "win32" and self.mutex:
            try:
                import ctypes
                ctypes.windll.kernel32.CloseHandle(self.mutex)
            except Exception:
                pass
            self.mutex = None
        elif self.sock:
            try:
                self.sock.close()
            except Exception:
                pass
            self.sock = None


def notify_already_running():
    """
    Alerts the user that PosturFix is already running in the background/system tray
    and attempts to bring the existing window to the foreground.
    """
    # 1. Bring existing window to front if it is open/minimized on Windows
    if sys.platform == "win32":
        try:
            import ctypes
            user32 = ctypes.windll.user32
            # Window title defined in PostureApp
            hwnd = user32.FindWindowW(None, "PosturFix • 100% Offline & Private")
            if hwnd:
                SW_RESTORE = 9
                user32.ShowWindow(hwnd, SW_RESTORE)
                user32.SetForegroundWindow(hwnd)
        except Exception:
            pass

    # 2. Fire native OS desktop toast notification
    try:
        from winotify import Notification
        icon_path = os.path.abspath(resource_path(os.path.join("assets", "icon.ico")))
        if not os.path.exists(icon_path):
            icon_path = ""
        toast = Notification(
            app_id="PosturFix",
            title="PosturFix",
            msg="PosturFix is already running in the system tray.",
            duration="short",
            icon=icon_path,
        )
        toast.show()
        return
    except Exception:
        pass

    try:
        from plyer import notification
        notification.notify(
            title="PosturFix",
            message="PosturFix is already running in the system tray.",
            app_name="PosturFix",
            timeout=4,
        )
    except Exception:
        pass


def main():
    """Application entry point: starts visible by default, runs hidden in tray if --minimized or --tray."""
    # 1. Enforce Single Instance Lock before initializing Tkinter, MediaPipe, or Camera hardware
    instance_lock = SingleInstance()
    if not instance_lock.acquire():
        notify_already_running()
        sys.exit(0)

    # 2. Proceed with normal startup
    start_hidden = ("--minimized" in sys.argv or "--tray" in sys.argv) and ("--show" not in sys.argv)
    app = PostureApp(start_hidden=start_hidden)
    try:
        app.mainloop()
    finally:
        instance_lock.release()


if __name__ == "__main__":
    main()

