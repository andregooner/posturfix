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
import tkinter as tk
from typing import Optional

import cv2
import numpy as np
from PIL import Image, ImageTk, ImageDraw
import customtkinter as ctk
import pystray

from config import PostureConfig
from posture_engine import PostureEngine, PostureState, PostureMetrics


def get_or_create_placeholder_icon(icon_size: int = 64) -> Image.Image:
    """
    Safely retrieves the application icon.
    If assets/icon.png does not exist, programmatically generates an aesthetic
    placeholder icon so the application never crashes.
    """
    assets_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")
    png_path = os.path.join(assets_dir, "icon.png")
    ico_path = os.path.join(assets_dir, "icon.ico")

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
        self.assets_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "mascot")
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
        dialogues = {
            PostureState.GOOD: "Awesome posture! Looking confident and energized!",
            PostureState.WARNING: "Heads up! Slouching detected, straighten up!",
            PostureState.SLOUCHING: f"Ouch! {slouch_reason or 'Slouching sustained'}! Please sit straight!",
            PostureState.CALIBRATING: "Hold still... Measuring your perfect upright baseline!",
            PostureState.UNCALIBRATED: "Please sit upright and click 'Calibrate Posture'.",
            PostureState.NO_PERSON: "Looking for you... Ensure your upper body is in view.",
        }
        self.speech_label.configure(text=dialogues.get(state, "Sit tall and stay healthy!"))


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
        self.is_window_visible: bool = not start_hidden
        self.privacy_mode = self.config.privacy_mode_default
        self.current_metrics: PostureMetrics = PostureMetrics()
        self._has_shown_tray_hint = False

        # Sedentary (Break) Timer Variables
        self.sitting_seconds: float = 0.0
        self._last_sitting_tick: float = time.time()
        self._away_start_time: Optional[float] = None
        self._last_posture_check: float = 0.0

        # Load Icon
        self.app_icon = get_or_create_placeholder_icon()
        try:
            ico_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "icon.ico")
            if os.path.exists(ico_path):
                self.iconbitmap(ico_path)
        except Exception:
            pass

        # Build UI Layout
        self._build_header()
        self._build_body()
        self._build_footer()

        # Intercept Window Close ('X' button) to minimize to tray
        self.protocol("WM_DELETE_WINDOW", self.hide_to_tray)

        # Initialize System Tray
        self._setup_system_tray()

        # Start Camera Processing
        self.start_camera()

        # Start hidden in background tray by default
        if start_hidden:
            self.withdraw()

    def _setup_system_tray(self):
        """Initializes the background system tray icon and context menu."""
        menu = pystray.Menu(
            pystray.MenuItem("PosturFix", None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Show Window", self._on_tray_show_window, default=True),
            pystray.MenuItem("Quick Calibrate", self._on_tray_quick_calibrate),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Pause for 30 Mins", self._on_tray_pause_30),
            pystray.MenuItem("Pause for 1 Hour", self._on_tray_pause_60),
            pystray.MenuItem("Resume", self._on_tray_resume, enabled=lambda item: self.is_snoozed()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Reset Break Timer", self._on_tray_reset_break_timer),
            pystray.MenuItem("Run on Startup", self._on_tray_toggle_autostart, checked=lambda item: self.is_autostart_active()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", self._on_tray_quit_app),
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

    def _on_tray_reset_break_timer(self, icon=None, item=None):
        """Thread-safe callback to reset break timer from system tray."""
        self.after(0, self.reset_break_timer)

    def _on_tray_toggle_autostart(self, icon=None, item=None):
        """Thread-safe callback to toggle Windows startup entry."""
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

    def is_autostart_active(self) -> bool:
        """Checks if PosturFix is configured to start on Windows boot."""
        if sys.platform != "win32":
            return False
        try:
            import winreg
            reg_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, reg_path, 0, winreg.KEY_READ) as key:
                winreg.QueryValueEx(key, "PosturFix")
                return True
        except Exception:
            return False

    def toggle_autostart(self):
        """Adds or removes PosturFix from the Windows startup registry."""
        if sys.platform != "win32":
            return

        currently_enabled = self.is_autostart_active()
        target_enabled = not currently_enabled

        try:
            import winreg
            reg_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, reg_path, 0, winreg.KEY_SET_VALUE | winreg.KEY_READ) as key:
                if target_enabled:
                    if getattr(sys, "frozen", False):
                        cmd = f'"{sys.executable}"'
                    else:
                        python_dir = os.path.dirname(sys.executable)
                        pythonw = os.path.join(python_dir, "pythonw.exe")
                        if not os.path.exists(pythonw):
                            pythonw = sys.executable
                        app_file = os.path.abspath(__file__)
                        cmd = f'"{pythonw}" "{app_file}"'

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

            if hasattr(self, "tray_icon") and self.tray_icon:
                try:
                    self.tray_icon.notify(msg, title)
                except Exception:
                    pass

        except Exception as e:
            if hasattr(self, "tray_icon") and self.tray_icon:
                try:
                    self.tray_icon.notify(f"Could not update startup setting: {e}", "PosturFix Error")
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
        title = self.config.sedentary_notification_title
        message = self.config.sedentary_notification_message

        # Native OS Desktop Notification via pystray
        if hasattr(self, "tray_icon") and self.tray_icon:
            try:
                self.tray_icon.notify(message, title)
            except Exception:
                pass

        # Audio chime alert
        if self.config.audio_alert_enabled:
            self.engine._play_alert_sound()

        # Automatically reset sitting timer after alert is delivered
        self.sitting_seconds = 0.0
        self._away_start_time = None

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

        privacy_badge = ctk.CTkLabel(
            title_box,
            text=" [100% OFFLINE • ZERO TELEMETRY] ",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color="#10B981",
            fg_color=("#D1FAE5", "#064E3B"),
            corner_radius=6,
        )
        privacy_badge.pack(side="left", padx=(12, 0))

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
        self.audio_switch.pack(side="left", padx=(0, 12))

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
            text="Sit comfortably straight looking at your screen, then click Calibrate.",
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

        # 4. Sedentary Reminder / Break Timer Card
        break_card = ctk.CTkFrame(right_panel, corner_radius=10, fg_color=("gray85", "#27272A"))
        break_card.pack(fill="x", padx=10, pady=6)

        break_header = ctk.CTkFrame(break_card, fg_color="transparent")
        break_header.pack(fill="x", padx=15, pady=(10, 2))

        break_title = ctk.CTkLabel(break_header, text="SEDENTARY BREAK TIMER", font=ctk.CTkFont(size=11, weight="bold"), text_color="gray")
        break_title.pack(side="left")

        self.break_interval_menu = ctk.CTkOptionMenu(
            break_header,
            values=["30m", "45m", "60m", "90m"],
            width=70,
            height=22,
            font=ctk.CTkFont(size=11),
            command=self._on_break_interval_change,
        )
        self.break_interval_menu.set(f"{self.config.sedentary_interval_minutes}m")
        self.break_interval_menu.pack(side="right")

        self.break_timer_lbl = ctk.CTkLabel(
            break_card,
            text=f"Active Sitting: 0m 00s / {self.config.sedentary_interval_minutes}m",
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color="#3B82F6",
        )
        self.break_timer_lbl.pack(anchor="w", padx=15, pady=(4, 4))

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
        self.camera_running = True
        self._camera_thread = threading.Thread(target=self._camera_worker, daemon=True)
        self._camera_thread.start()

    def _open_camera_safe(self) -> Optional[cv2.VideoCapture]:
        """
        Safely attempts to initialize the webcam.
        Catches device locks, Zoom/Meet conflicts, driver crashes, and empty frames.
        Returns cv2.VideoCapture instance if operational, otherwise None.
        """
        cap = None
        try:
            backend = cv2.CAP_DSHOW if sys.platform == "win32" else cv2.CAP_ANY
            cap = cv2.VideoCapture(self.config.camera_index, backend)
            if not cap.isOpened():
                cap = cv2.VideoCapture(self.config.camera_index)

            if cap.isOpened():
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.config.frame_width)
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.config.frame_height)
                # Verify camera actually delivers valid non-empty frames
                ret, test_frame = cap.read()
                if ret and test_frame is not None and test_frame.size > 0:
                    return cap
                else:
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
        - Camera Conflict Handling: backs off for 60s if locked by Zoom/Meet with 0% CPU.
        - Thread-Safe & Non-Blocking: Tkinter mainloop and System Tray menu never hitch or lag.
        """
        last_check_time = 0.0
        fps_frame_count = 0
        fps_start_time = time.time()
        was_calibrating = False
        is_in_deep_sleep = False

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
                # safely retry and back off for 60s without crashing or busy looping.
                # -------------------------------------------------------------
                if self.cap is None or not self.cap.isOpened():
                    self.cap = self._open_camera_safe()
                    if self.cap is None:
                        # Camera in use by another app or unavailable
                        if hasattr(self, "tray_icon") and self.tray_icon:
                            self.tray_icon.title = "PosturFix: Camera in use by another app"

                        if is_visible:
                            self.after(0, lambda: self.camera_status_lbl.configure(
                                text="Camera in use by another app (Zoom/Meet)", text_color="#EF4444"
                            ))
                            self.after(0, lambda: self._render_privacy_screen(
                                "Camera in use by another app\n(Zoom, Meet, or Teams)\nRetrying in 60s to save battery"
                            ))

                        # Battery-saving 60s backoff wait using OS kernel event (0.0% CPU)
                        retry_sec = self.config.camera_retry_interval_busy_seconds
                        self._camera_retry_event.wait(timeout=retry_sec)
                        self._camera_retry_event.clear()
                        continue
                    else:
                        if is_visible:
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
                except Exception:
                    ret, frame = False, None

                # Camera conflict / empty frame check
                if not ret or frame is None or frame.size == 0:
                    # Camera locked mid-session or disconnected
                    if self.cap is not None:
                        try:
                            self.cap.release()
                        except Exception:
                            pass
                        self.cap = None

                    if hasattr(self, "tray_icon") and self.tray_icon:
                        self.tray_icon.title = "PosturFix: Camera in use by another app"

                    if is_visible:
                        self.after(0, lambda: self.camera_status_lbl.configure(
                            text="Camera: In use by another app", text_color="#EF4444"
                        ))
                        self.after(0, lambda: self._render_privacy_screen(
                            "Camera in use by another app\n(Zoom, Meet, or Teams)\nRetrying in 60s to save battery"
                        ))

                    # Sleep 60 seconds before retrying (0% CPU)
                    retry_sec = self.config.camera_retry_interval_busy_seconds
                    self._camera_retry_event.wait(timeout=retry_sec)
                    self._camera_retry_event.clear()
                    continue

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
                annotated_frame, metrics = self.engine.process_frame(frame)
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
                        self.after(0, self._update_ui_frame, annotated_frame, metrics)
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

                # Update tray tooltip dynamically
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
                    # Dispatch UI and video feed update to main thread
                    self.after(0, self._update_ui_frame, annotated_frame, metrics)
                    # When visible, small delay for smooth preview (fast motion)
                    delay = 0.08 if self.engine.calibrating else 0.05
                    time.sleep(delay)
                else:
                    # When hidden in tray, sleep for the 3-second interval
                    time.sleep(0.1 if is_calibrating else interval)

            except Exception:
                time.sleep(0.2)

        # Release capture when thread terminates
        if self.cap and self.cap.isOpened():
            try:
                self.cap.release()
            except Exception:
                pass

    def _update_ui_frame(self, annotated_frame: np.ndarray, metrics: PostureMetrics):
        """Thread-safe UI dispatcher: updates dashboard and camera canvas on Tkinter main thread."""
        if not self.is_window_visible:
            return
        self._update_dashboard(metrics)
        self._render_video_feed(annotated_frame)

    def _render_video_feed(self, frame_bgr: np.ndarray):
        """Displays video frame on the Tkinter canvas or shows privacy shield."""
        if self.privacy_mode:
            self._render_privacy_screen("Privacy Mode Active\nCamera Preview Hidden • Analysis Active in RAM")
            return

        canvas_w = max(10, self.video_canvas.winfo_width())
        canvas_h = max(10, self.video_canvas.winfo_height())

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
        self.status_badge.configure(text=title, text_color=text_color)
        self.reason_badge.configure(text=metrics.slouch_reason or "All posture metrics normal.")

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

        # Sedentary Break Timer Progress
        sitting_mins = int(self.sitting_seconds // 60)
        sitting_secs = int(self.sitting_seconds % 60)
        target_mins = self.config.sedentary_interval_minutes
        target_secs = target_mins * 60.0
        break_fraction = min(1.0, max(0.0, self.sitting_seconds / target_secs))

        self.break_timer_lbl.configure(text=f"Active Sitting: {sitting_mins}m {sitting_secs:02d}s / {target_mins}m")
        self.break_progress.set(break_fraction)
        if break_fraction >= 0.9:
            self.break_progress.configure(progress_color="#EF4444")
        elif break_fraction >= 0.7:
            self.break_progress.configure(progress_color="#F59E0B")
        else:
            self.break_progress.configure(progress_color="#3B82F6")

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

    def _on_break_interval_change(self, choice: str):
        try:
            mins = int(choice.replace("m", "").strip())
            self.config.sedentary_interval_minutes = mins
            sitting_mins = int(self.sitting_seconds // 60)
            sitting_secs = int(self.sitting_seconds % 60)
            self.break_timer_lbl.configure(text=f"Active Sitting: {sitting_mins}m {sitting_secs:02d}s / {mins}m")
        except Exception:
            pass


def main():
    """Application entry point: starts quietly in system tray by default."""
    start_hidden = "--show" not in sys.argv
    app = PostureApp(start_hidden=start_hidden)
    app.mainloop()


if __name__ == "__main__":
    main()

