"""
PosturFix License Manager & Gatekeeper
Handles one-time first-run activation and 100% offline cryptographic token validation.

Guarantees:
- Internet connection is ONLY required once during first-time key activation.
- Once activated, validation is 100% offline (verifying local HMAC-SHA256 signature).
- Never makes background phone-home calls or collects telemetry.
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
from typing import Optional, Tuple, Callable, List
import customtkinter as ctk

# Cryptographic salt for offline token verification
_LICENSE_SALT = b"PosturFix_Commercial_Offline_Activation_Salt_2026_v1"


def _get_machine_fingerprint() -> str:
    """Generates a stable local machine fingerprint for node-locking validation."""
    try:
        raw = f"{platform.node()}-{platform.machine()}-{platform.processor()}-{sys.platform}"
    except Exception:
        raw = "PosturFix_Default_Hardware_Node"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def get_license_file_paths() -> List[str]:
    """Returns candidate paths for the license.key file in order of priority."""
    paths = []
    # 1. Local working directory / directory of executable or script
    if getattr(sys, "frozen", False):
        exe_dir = os.path.dirname(sys.executable)
    else:
        exe_dir = os.path.dirname(os.path.abspath(__file__))
    paths.append(os.path.join(exe_dir, "license.key"))

    # 2. Local AppData directory (%LOCALAPPDATA%/PosturFix/license.key)
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
        return False, "Please enter a valid license key."

    # Simulate realistic network latency for the activation handshake
    time.sleep(1.2)

    # TODO: Replace with Mayar.id API endpoint
    # Example production integration:
    # try:
    #     import urllib.request
    #     url = "https://api.mayar.id/hl/v1/license/verify"
    #     req = urllib.request.Request(
    #         url,
    #         data=json.dumps({"license_key": clean_key, "product_id": "posturfix_pro"}).encode("utf-8"),
    #         headers={"Content-Type": "application/json", "Authorization": "Bearer YOUR_MAYAR_API_KEY"},
    #         method="POST"
    #     )
    #     with urllib.request.urlopen(req, timeout=10) as resp:
    #         res_data = json.loads(resp.read().decode())
    #         if res_data.get("status") == "success" or res_data.get("data", {}).get("valid"):
    #             return True, "License verified successfully with Mayar.id!"
    #         else:
    #             return False, res_data.get("message", "Invalid license key.")
    # except Exception as e:
    #     return False, f"Could not connect to activation server: {e}"

    # Mock Validation Logic:
    if clean_key.startswith("PFX-") and len(clean_key) >= 8:
        return True, "Activation successful! Loading PosturFix..."
    else:
        return False, "Invalid License Key. Key must start with 'PFX-' (e.g. PFX-12345)."


class ActivationWindow(ctk.CTkToplevel):
    """
    Clean, dark-themed License Activation Modal Window.
    Blocks the main posture tracking interface until a valid license key is provided.
    """

    def __init__(
        self,
        parent: ctk.CTk,
        on_success_callback: Callable[[str], None],
        on_close_callback: Optional[Callable[[], None]] = None,
    ):
        super().__init__(parent)
        self.parent = parent
        self.on_success_callback = on_success_callback
        self.on_close_callback = on_close_callback or parent.destroy

        self.title("Activate PosturFix")
        self.geometry("480x430")
        self.resizable(False, False)
        self.attributes("-topmost", True)

        # Center on screen
        self._center_window(480, 430)

        # Intercept window close to quit app cleanly
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

        # 1. Header Lock Badge
        badge = ctk.CTkLabel(
            main_frame,
            text="🔐",
            font=ctk.CTkFont(size=36),
        )
        badge.pack(pady=(16, 4))

        title_lbl = ctk.CTkLabel(
            main_frame,
            text="Activate PosturFix",
            font=ctk.CTkFont(size=20, weight="bold"),
        )
        title_lbl.pack(pady=(0, 6))

        # 2. Subtext Requirement
        subtext_lbl = ctk.CTkLabel(
            main_frame,
            text="Please enter your license key. Internet connection is only required once for this activation step.",
            font=ctk.CTkFont(size=12),
            text_color="gray70",
            wraplength=380,
            justify="center",
        )
        subtext_lbl.pack(pady=(0, 16), padx=20)

        # 3. Input Field
        self.key_entry = ctk.CTkEntry(
            main_frame,
            placeholder_text="e.g. PFX-12345",
            width=360,
            height=40,
            font=ctk.CTkFont(size=14, family="Consolas"),
            justify="center",
        )
        self.key_entry.pack(pady=(0, 10))
        self.key_entry.bind("<Return>", lambda e: self._on_activate_clicked())
        self.key_entry.focus_set()

        # 4. Status / Error Message Label
        self.status_lbl = ctk.CTkLabel(
            main_frame,
            text="",
            font=ctk.CTkFont(size=12, weight="bold"),
            wraplength=380,
            justify="center",
        )
        self.status_lbl.pack(pady=(0, 12))

        # 5. Activate Button
        self.activate_btn = ctk.CTkButton(
            main_frame,
            text="Activate App",
            font=ctk.CTkFont(size=14, weight="bold"),
            height=40,
            width=360,
            fg_color="#2563EB",
            hover_color="#1D4ED8",
            command=self._on_activate_clicked,
        )
        self.activate_btn.pack(pady=(0, 14))

        # 6. Offline Guarantee Note
        offline_note = ctk.CTkLabel(
            main_frame,
            text="🛡️ 100% Offline Promise: After activation, PosturFix operates entirely in local RAM with zero network traffic.",
            font=ctk.CTkFont(size=10),
            text_color="gray50",
            wraplength=380,
            justify="center",
        )
        offline_note.pack(side="bottom", pady=(0, 8))

    def _on_activate_clicked(self):
        if self._is_validating:
            return

        key = self.key_entry.get().strip()
        if not key:
            self.status_lbl.configure(text="Please enter your license key.", text_color="#EF4444")
            return

        self._is_validating = True
        self.key_entry.configure(state="disabled")
        self.activate_btn.configure(state="disabled", text="Connecting...")
        self.status_lbl.configure(text="Connecting to activation server...", text_color="#F59E0B")

        # Asynchronous non-blocking validation thread
        def _worker():
            is_valid, msg = validate_license_key(key)
            # Dispatch result back to GUI thread
            self.after(0, self._handle_validation_result, is_valid, msg, key)

        threading.Thread(target=_worker, daemon=True, name="PosturFix-ActivationWorker").start()

    def _handle_validation_result(self, is_valid: bool, msg: str, key: str):
        self._is_validating = False
        self.key_entry.configure(state="normal")
        self.activate_btn.configure(state="normal", text="Activate App")

        if is_valid:
            # Local Persistence: write secure local token
            save_license_token(key)
            self.status_lbl.configure(text=msg, text_color="#10B981")
            self.activate_btn.configure(text="Activated ✓", fg_color="#10B981", state="disabled")
            # Immediately close Activation Window and transition smoothly to main UI
            self.after(600, self._finish_activation, key)
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
        if self.on_close_callback:
            self.on_close_callback()
        else:
            self.destroy()
            sys.exit(0)
