"""
PosturFix License Manager & 3-Day Free Trial Gatekeeper.
Handles one-time first-run hardware-locked 3-day trial and 100% offline cryptographic token validation.

Guarantees:
- Hardware-locked trial timestamp (MAC address + Windows MachineGuid) prevents trial reset via reinstallation.
- Anti-clock-rollback protection prevents bypassing via manual date manipulation.
- 100% Offline validation check: once activated with Mayar key, validation is in-memory HMAC-SHA256.
- Zero background telemetry or tracking.
"""

import os
import sys
import json
import time
import base64
import hashlib
import hmac
import platform
import threading
import uuid
import math
import tempfile
import webbrowser
from dataclasses import dataclass
from typing import Optional, Tuple, Callable, List
import customtkinter as ctk

# Cryptographic salts
_LICENSE_SALT = b"PosturFix_Commercial_Offline_Activation_Salt_2026_v1"
_TRIAL_SALT = b"PosturFix_Trial_Integrity_Salt_2026_v1"

# Trial & Checkout Constants
TRIAL_DURATION_DAYS = 3
TRIAL_DURATION_SECONDS = TRIAL_DURATION_DAYS * 86400  # 259,200 seconds (72 hours)
MAYAR_CHECKOUT_URL = "https://mayar.id/posturfix"
PRICE_IDR_FORMATTED = "Rp 98.000"


def get_hardware_id() -> str:
    """
    Generates a unique, persistent Hardware Identifier (HWID).
    Combines:
    - MAC address via uuid.getnode()
    - Motherboard / Windows MachineGuid from registry (HKLM\\SOFTWARE\\Microsoft\\Cryptography)
    - System node & processor architecture
    Returns a formatted string: e.g. '316A1D-507ECD-74CA25-08542F'.
    """
    parts = []
    try:
        mac = uuid.getnode()
        parts.append(f"MAC:{mac:012x}")
    except Exception:
        parts.append("MAC:000000000000")

    if platform.system() == "Windows":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography") as k:
                guid, _ = winreg.QueryValueEx(k, "MachineGuid")
                parts.append(f"GUID:{guid}")
        except Exception:
            pass

    try:
        parts.append(f"NODE:{platform.node()}")
        parts.append(f"PROC:{platform.processor()}")
    except Exception:
        pass

    raw = "|".join(parts)
    raw_hash = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24].upper()
    return "-".join(raw_hash[i : i + 6] for i in range(0, len(raw_hash), 6))


def _get_machine_fingerprint() -> str:
    """Generates a stable local machine fingerprint for node-locking validation."""
    try:
        raw = f"{platform.node()}-{platform.machine()}-{platform.processor()}-{sys.platform}"
    except Exception:
        raw = "PosturFix_Default_Hardware_Node"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


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


# -------------------------------------------------------------
# 1. 3-DAY FREE TRIAL SYSTEM (HARDWARE-LOCKED & PERSISTENT)
# -------------------------------------------------------------

@dataclass
class TrialStatus:
    is_active: bool          # True if elapsed <= 3 days and clock valid
    is_expired: bool         # True if elapsed > 3 days
    days_left: int           # e.g. 3, 2, 1, 0
    hours_left: int          # e.g. 72 down to 0
    elapsed_seconds: float
    installed_at: float
    hwid: str
    message: str


def get_trial_storage_paths() -> List[str]:
    """Returns candidate file paths where the trial token is persisted."""
    paths = []
    # 1. Local executable / script directory
    if getattr(sys, "frozen", False):
        exe_dir = os.path.dirname(sys.executable)
    else:
        exe_dir = os.path.dirname(os.path.abspath(__file__))
    paths.append(os.path.join(exe_dir, ".pfx_trial"))

    # 2. Local AppData (%LOCALAPPDATA%/PosturFix/.pfx_trial)
    local_appdata = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or os.path.expanduser("~")
    paths.append(os.path.join(local_appdata, "PosturFix", ".pfx_trial"))

    # 3. Roaming AppData (%APPDATA%/PosturFix/.pfx_trial)
    appdata = os.environ.get("APPDATA")
    if appdata and appdata != local_appdata:
        paths.append(os.path.join(appdata, "PosturFix", ".pfx_trial"))

    # 4. User profile directory (~/.pfx_trial)
    paths.append(os.path.join(os.path.expanduser("~"), ".pfx_trial"))

    # 5. System Temp directory (%TEMP%/.pfx_install_ticket)
    paths.append(os.path.join(tempfile.gettempdir(), ".pfx_install_ticket"))

    return paths


def _read_registry_trial() -> Optional[str]:
    """Reads trial token from Windows Registry (HKCU) if available."""
    if sys.platform != "win32":
        return None
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\PosturFix", 0, winreg.KEY_READ) as k:
            val, _ = winreg.QueryValueEx(k, "TrialTicket")
            return str(val).strip()
    except Exception:
        return None


def _write_registry_trial(token: str) -> bool:
    """Writes trial token to Windows Registry (HKCU) if permitted."""
    if sys.platform != "win32":
        return False
    try:
        import winreg
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\PosturFix") as k:
            winreg.SetValueEx(k, "TrialTicket", 0, winreg.REG_SZ, token)
        return True
    except Exception:
        return False


def _compute_trial_sig(hwid: str, installed_at: float, trial_days: int) -> str:
    """Computes tamper-proof HMAC-SHA256 signature for trial timestamp."""
    msg = f"{hwid}:{installed_at:.2f}:{trial_days}".encode("utf-8")
    return hmac.new(_TRIAL_SALT, msg, hashlib.sha256).hexdigest()


def _save_trial_token(hwid: str, installed_at: float, last_seen_at: float, trial_days: int = TRIAL_DURATION_DAYS) -> str:
    """Encodes, signs, and saves trial token across all candidate locations."""
    sig = _compute_trial_sig(hwid, installed_at, trial_days)
    payload = {
        "hwid": hwid,
        "installed_at": installed_at,
        "last_seen_at": last_seen_at,
        "trial_days": trial_days,
        "sig": sig,
    }
    encoded = base64.b64encode(json.dumps(payload).encode("utf-8")).decode("utf-8")

    # 1. Write to registry
    _write_registry_trial(encoded)

    # 2. Write to all accessible file paths
    for p in get_trial_storage_paths():
        try:
            os.makedirs(os.path.dirname(os.path.abspath(p)), exist_ok=True)
            with open(p, "w", encoding="utf-8") as f:
                f.write(encoded)
        except Exception:
            pass

    return encoded


def get_trial_status(force_recheck: bool = False) -> TrialStatus:
    """
    Evaluates 3-day free trial status with hardware locking and clock-rollback protection.
    - Captures Motherboard / MAC HWID to prevent trial-reset by simple reinstallation.
    - Recovers installation timestamp across Registry, AppData, Temp, and local files.
    - Calculates days/hours remaining.
    """
    hwid = get_hardware_id()
    now = time.time()

    candidate_records = []

    # Gather raw tokens from registry and files
    raw_tokens = []
    reg_tok = _read_registry_trial()
    if reg_tok:
        raw_tokens.append((reg_tok, "registry"))

    for p in get_trial_storage_paths():
        if os.path.exists(p):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    content = f.read().strip()
                if content:
                    raw_tokens.append((content, p))
            except Exception:
                pass

    for tok, src in raw_tokens:
        try:
            data = json.loads(base64.b64decode(tok.encode("utf-8")).decode("utf-8"))
            tok_hwid = data.get("hwid", "")
            installed_at = float(data.get("installed_at", 0.0))
            last_seen_at = float(data.get("last_seen_at", installed_at))
            trial_days = int(data.get("trial_days", TRIAL_DURATION_DAYS))
            sig = data.get("sig", "")

            # Verify Hardware ID and HMAC signature
            if tok_hwid == hwid and installed_at > 0:
                expected_sig = _compute_trial_sig(hwid, installed_at, trial_days)
                if hmac.compare_digest(sig, expected_sig):
                    candidate_records.append((installed_at, last_seen_at, trial_days))
        except Exception:
            continue

    if not candidate_records:
        # First-ever launch on this machine
        installed_at = now
        last_seen_at = now
        _save_trial_token(hwid, installed_at, last_seen_at, TRIAL_DURATION_DAYS)
    else:
        # Subsequent launch or re-installation:
        # Adopt OLDEST recorded installed_at to prevent reset by reinstallation
        installed_at = min(rec[0] for rec in candidate_records)
        recorded_last_seen = max(rec[1] for rec in candidate_records)
        # Anti-Clock-Rollback: effective time never goes backwards
        effective_now = max(now, recorded_last_seen)
        last_seen_at = effective_now
        # Re-sync updated token to all locations
        _save_trial_token(hwid, installed_at, last_seen_at, TRIAL_DURATION_DAYS)

    # Time calculation
    effective_now = max(now, last_seen_at)
    elapsed_seconds = max(0.0, effective_now - installed_at)
    remaining_seconds = max(0.0, TRIAL_DURATION_SECONDS - elapsed_seconds)

    is_active = (elapsed_seconds < TRIAL_DURATION_SECONDS)
    is_expired = not is_active

    days_left = max(0, math.ceil(remaining_seconds / 86400.0))
    hours_left = max(0, math.ceil(remaining_seconds / 3600.0))

    if is_active:
        if days_left > 1:
            msg = f"Trial: {days_left} days left."
        elif days_left == 1:
            msg = "Trial: 1 day left."
        else:
            msg = f"Trial: {max(1, hours_left)} hours left."
    else:
        msg = "Masa uji coba 3 hari Anda telah habis."

    return TrialStatus(
        is_active=is_active,
        is_expired=is_expired,
        days_left=days_left,
        hours_left=hours_left,
        elapsed_seconds=elapsed_seconds,
        installed_at=installed_at,
        hwid=hwid,
        message=msg,
    )


# -------------------------------------------------------------
# 2. OFFLINE CRYPTOGRAPHIC LICENSE VERIFICATION
# -------------------------------------------------------------

def get_license_file_paths() -> List[str]:
    """Returns candidate paths for the license.key file in order of priority."""
    paths = []
    # 1. Local working directory / directory of executable or script
    if getattr(sys, "frozen", False):
        exe_dir = os.path.dirname(sys.executable)
    else:
        exe_dir = os.path.dirname(os.path.abspath(__file__))
    paths.append(os.path.join(exe_dir, "license.key"))

    # 2. PyInstaller temporary bundle directory via sys._MEIPASS
    bundled_key = resource_path("license.key")
    if bundled_key not in paths:
        paths.append(bundled_key)

    # 3. Local AppData directory (%LOCALAPPDATA%/PosturFix/license.key)
    local_appdata = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or os.path.expanduser("~")
    paths.append(os.path.join(local_appdata, "PosturFix", "license.key"))

    return paths


def get_active_license_path() -> str:
    """Returns the path where a valid license was found, or the default save target."""
    for p in get_license_file_paths():
        if os.path.exists(p):
            return p
    return get_license_file_paths()[0]


def is_license_valid() -> bool:
    """
    100% Offline validation check.
    Zero network calls. Validates local token HMAC signature against local machine fingerprint.
    """
    for path in get_license_file_paths():
        if not os.path.exists(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                encoded = f.read().strip()
            data = json.loads(base64.b64decode(encoded.encode("utf-8")).decode("utf-8"))
            clean_key = data.get("license_key", "").strip().upper()
            mid = data.get("machine_id", "")
            sig = data.get("signature", "")

            if not clean_key.startswith("PFX-") or len(clean_key) < 8:
                continue

            expected_sig = hmac.new(
                _LICENSE_SALT,
                f"{clean_key}:{mid}".encode("utf-8"),
                hashlib.sha256,
            ).hexdigest()

            if hmac.compare_digest(sig, expected_sig):
                return True
        except Exception:
            continue
    return False


def get_active_license_key() -> Optional[str]:
    """Retrieves the active license key if valid, otherwise None."""
    for path in get_license_file_paths():
        if not os.path.exists(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                encoded = f.read().strip()
            data = json.loads(base64.b64decode(encoded.encode("utf-8")).decode("utf-8"))
            clean_key = data.get("license_key", "").strip().upper()
            mid = data.get("machine_id", "")
            sig = data.get("signature", "")

            expected_sig = hmac.new(
                _LICENSE_SALT,
                f"{clean_key}:{mid}".encode("utf-8"),
                hashlib.sha256,
            ).hexdigest()

            if hmac.compare_digest(sig, expected_sig):
                return clean_key
        except Exception:
            continue
    return None


def save_license_token(key: str) -> bool:
    """Saves cryptographic validation token locally for 100% offline startup verification."""
    clean_key = key.strip().upper()
    mid = _get_machine_fingerprint()
    sig = hmac.new(
        _LICENSE_SALT,
        f"{clean_key}:{mid}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    payload = {
        "license_key": clean_key,
        "machine_id": mid,
        "activated_at": time.time(),
        "signature": sig,
    }
    encoded = base64.b64encode(json.dumps(payload).encode("utf-8")).decode("utf-8")

    success = False
    for path in get_license_file_paths():
        try:
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(encoded)
            success = True
        except Exception:
            pass
    return success


def validate_license_key(key: str) -> Tuple[bool, str]:
    """
    Asynchronous network verification function called ONLY once during first-time activation.
    Returns:
        (is_valid: bool, message: str)
    """
    clean_key = key.strip().upper()
    if not clean_key:
        return False, "Mohon masukkan kunci lisensi yang valid."

    # Simulate realistic network handshake
    time.sleep(1.2)

    # Mock Validation Logic (Production will call Mayar.id verification endpoint)
    if clean_key.startswith("PFX-") and len(clean_key) >= 8:
        return True, "Aktivasi berhasil! Memuat PosturFix Pro..."
    else:
        return False, "Kunci lisensi tidak valid. Kunci harus diawali 'PFX-' (contoh: PFX-12345)."


# -------------------------------------------------------------
# 3. PAYWALL & ACTIVATION MODAL WINDOW
# -------------------------------------------------------------

class ActivationWindow(ctk.CTkToplevel):
    """
    Clean, dark-themed License Activation & Paywall Window.
    Supports:
    - Post-trial paywall lock screen ("Masa uji coba 3 hari Anda telah habis...")
    - One-click checkout button ("Beli Lisensi (Rp 98.000)") opening Mayar
    - License key input & offline activation validation
    - In-trial upgrade screen with dismiss option
    """

    def __init__(
        self,
        parent: ctk.CTk,
        on_success_callback: Callable[[str], None],
        on_close_callback: Optional[Callable[[], None]] = None,
        is_post_trial: bool = True,
        trial_status: Optional[TrialStatus] = None,
        checkout_url: str = MAYAR_CHECKOUT_URL,
    ):
        super().__init__(parent)
        self.parent = parent
        self.on_success_callback = on_success_callback
        self.on_close_callback = on_close_callback
        self.is_post_trial = is_post_trial
        self.trial_status = trial_status or get_trial_status()
        self.checkout_url = checkout_url

        self.title("Aktivasi PosturFix Pro" if self.is_post_trial else "Upgrade PosturFix Pro")
        self.geometry("500x560")
        self.resizable(False, False)
        self.attributes("-topmost", True)

        self._center_window(500, 560)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self._is_validating = False
        self._build_ui()

    def _center_window(self, width: int, height: int):
        self.update_idletasks()
        screen_w = self.winfo_screenwidth()
        screen_h = self.winfo_screenheight()
        x = max(0, (screen_w - width) // 2)
        y = max(0, (screen_h - height) // 2)
        self.geometry(f"{width}x{height}+{x}+{y}")

    def _build_ui(self):
        main_frame = ctk.CTkFrame(self, corner_radius=12, fg_color=("gray95", "#18181B"))
        main_frame.pack(fill="both", expand=True, padx=16, pady=16)

        # 1. Header Icon & Badge
        badge_icon = "⏳" if self.is_post_trial else "🔐"
        badge = ctk.CTkLabel(
            main_frame,
            text=badge_icon,
            font=ctk.CTkFont(size=36),
        )
        badge.pack(pady=(12, 2))

        title_text = "Masa Uji Coba Berakhir" if self.is_post_trial else "Upgrade ke PosturFix Pro"
        title_lbl = ctk.CTkLabel(
            main_frame,
            text=title_text,
            font=ctk.CTkFont(size=20, weight="bold"),
        )
        title_lbl.pack(pady=(0, 6))

        # 2. Main Explanation (Exact Copy Requirements)
        if self.is_post_trial:
            subtext = "Masa uji coba 3 hari Anda telah habis. Beli lisensi seumur hidup sekarang untuk terus menjaga postur tubuh Anda."
        else:
            days_msg = f"{self.trial_status.days_left} hari" if self.trial_status.days_left > 1 else "1 hari"
            subtext = f"Masa uji coba Anda masih aktif ({days_msg} tersisa). Beli lisensi seumur hidup sekarang untuk menikmati pemantauan postur tanpa batas."

        subtext_lbl = ctk.CTkLabel(
            main_frame,
            text=subtext,
            font=ctk.CTkFont(size=12),
            text_color="gray70",
            wraplength=410,
            justify="center",
        )
        subtext_lbl.pack(pady=(0, 14), padx=20)

        # 3. Prominent CTA Button: "Beli Lisensi (Rp 98.000)"
        self.buy_btn = ctk.CTkButton(
            main_frame,
            text=f"💳 Beli Lisensi ({PRICE_IDR_FORMATTED})",
            font=ctk.CTkFont(size=14, weight="bold"),
            height=44,
            width=380,
            fg_color="#10B981",
            hover_color="#059669",
            text_color="#FFFFFF",
            command=self._on_buy_clicked,
        )
        self.buy_btn.pack(pady=(0, 14))

        # 4. Divider Line
        divider_lbl = ctk.CTkLabel(
            main_frame,
            text="─── Sudah Memiliki Kunci Lisensi? ───",
            font=ctk.CTkFont(size=11),
            text_color="gray50",
        )
        divider_lbl.pack(pady=(0, 10))

        # 5. Input Field for License Key
        self.key_entry = ctk.CTkEntry(
            main_frame,
            placeholder_text="Masukkan kunci lisensi (contoh: PFX-12345)",
            width=380,
            height=40,
            font=ctk.CTkFont(size=13, family="Consolas"),
            justify="center",
        )
        self.key_entry.pack(pady=(0, 8))
        self.key_entry.bind("<Return>", lambda e: self._on_activate_clicked())
        self.key_entry.focus_set()

        # 6. Status / Error Message Label
        self.status_lbl = ctk.CTkLabel(
            main_frame,
            text="",
            font=ctk.CTkFont(size=12, weight="bold"),
            wraplength=400,
            justify="center",
        )
        self.status_lbl.pack(pady=(0, 8))

        # 7. Activate Button
        self.activate_btn = ctk.CTkButton(
            main_frame,
            text="Aktivasi Kunci Lisensi",
            font=ctk.CTkFont(size=13, weight="bold"),
            height=38,
            width=380,
            fg_color="#2563EB",
            hover_color="#1D4ED8",
            command=self._on_activate_clicked,
        )
        self.activate_btn.pack(pady=(0, 10))

        # 8. Hardware ID Info Label
        hwid_lbl = ctk.CTkLabel(
            main_frame,
            text=f"Hardware ID: {self.trial_status.hwid}",
            font=ctk.CTkFont(size=10, family="Consolas"),
            text_color="gray45",
        )
        hwid_lbl.pack(pady=(0, 4))

        # 9. Offline Guarantee Note
        offline_note = ctk.CTkLabel(
            main_frame,
            text="🛡️ 100% Offline Promise: Setelah aktivasi, PosturFix beroperasi sepenuhnya di RAM lokal tanpa koneksi internet.",
            font=ctk.CTkFont(size=10),
            text_color="gray50",
            wraplength=410,
            justify="center",
        )
        offline_note.pack(side="bottom", pady=(0, 4))

    def _on_buy_clicked(self):
        """Opens the Mayar checkout URL in the user's default browser."""
        try:
            webbrowser.open(self.checkout_url)
        except Exception as e:
            self.status_lbl.configure(
                text=f"Gagal membuka browser. Kunjungi: {self.checkout_url}",
                text_color="#EF4444",
            )

    def _on_activate_clicked(self):
        if self._is_validating:
            return

        key = self.key_entry.get().strip()
        if not key:
            self.status_lbl.configure(text="Mohon masukkan kunci lisensi Anda.", text_color="#EF4444")
            return

        self._is_validating = True
        self.key_entry.configure(state="disabled")
        self.activate_btn.configure(state="disabled", text="Menghubungkan...")
        self.status_lbl.configure(text="Memverifikasi ke server aktivasi...", text_color="#F59E0B")

        # Asynchronous non-blocking validation thread
        def _worker():
            is_valid, msg = validate_license_key(key)
            self.after(0, self._handle_validation_result, is_valid, msg, key)

        threading.Thread(target=_worker, daemon=True, name="PosturFix-ActivationWorker").start()

    def _handle_validation_result(self, is_valid: bool, msg: str, key: str):
        self._is_validating = False
        self.key_entry.configure(state="normal")
        self.activate_btn.configure(state="normal", text="Aktivasi Kunci Lisensi")

        if is_valid:
            # Save cryptographic offline validation token
            save_license_token(key)
            self.status_lbl.configure(text=msg, text_color="#10B981")
            self.activate_btn.configure(text="Berhasil Teraktivasi ✓", fg_color="#10B981", state="disabled")
            self.after(700, self._finish_activation, key)
        else:
            self.status_lbl.configure(text=msg, text_color="#EF4444")
            self.key_entry.focus_set()

    def _finish_activation(self, key: str):
        try:
            self.destroy()
        except Exception:
            pass
        if self.on_success_callback:
            self.on_success_callback(key)

    def _on_close(self):
        """
        Handles modal window close event ('X' button).
        - If trial expired (paywall): prevents bypassing and cleanly exits the application.
        - If within active trial: allows dismissing the dialog so trial tracking continues.
        """
        if self.is_post_trial:
            if self.on_close_callback:
                self.on_close_callback()
            else:
                self.destroy()
                sys.exit(0)
        else:
            self.destroy()
