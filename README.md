# 🛡️ PosturFix (100% Offline & Privacy-First)

> A lightweight, modern desktop application that monitors your sitting posture and eye distance in real-time using your webcam. Built with privacy, battery-efficiency, and ergonomics at its core.

---

## 🔒 Privacy & Offline Architecture

PosturFix was engineered from day one under a strict zero-trust privacy policy:
- **100% Offline Local Inference:** Runs Google MediaPipe Pose models locally on your CPU. No network calls or cloud APIs are ever made.
- **Volatile In-Memory Processing:** Video frames are captured directly into RAM (`numpy.ndarray`), analyzed by the pose estimator, and immediately recycled by garbage collection.
- **Zero Disk Writes:** Not a single video frame, snapshot, or biometric landmark is ever written to your hard drive.
- **Privacy Mode (Feed Concealment):** Need to hide your camera feed during meetings or screen shares? Toggle "Privacy Mode" in the app. The video preview is replaced with an elegant privacy shield visual while local background analysis continues uninterrupted.

---

## 📐 How the Ergonomic Posture Engine Works

Rather than relying on brittle raw pixel coordinates that break when you shift in your seat, PosturFix uses **scale-invariant geometry**:

1. **Natural Scale Normalization:** The Euclidean distance between your left and right shoulders ($W_{shoulder}$) serves as the dynamic scale factor.
2. **Vertical Neck Ratio ($R_{neck}$):** 
   $$R_{neck} = \frac{Y_{shoulder\_midpoint} - Y_{ear\_midpoint}}{W_{shoulder}}$$
   When you sit upright, this ratio is at its maximum. When your head drops forward or you slouch, this ratio decreases significantly.
3. **Lateral Shoulder & Head Tilt:** Angles relative to horizontal are monitored to detect asymmetric leaning to one side.
4. **Lean Proximity:** Expansions in shoulder width relative to baseline detect excessive forward hunching toward the monitor.
5. **👁️ Eye-to-Screen Distance Warning:** To prevent eye strain without loading heavy 3D face models, PosturFix calculates the Euclidean distance between `LEFT_EYE` and `RIGHT_EYE` landmarks from `mp_pose`. If the distance expands >30% over baseline, a *"Screen Too Close / Lean Back"* warning triggers immediately.
6. **Debounce & Alert Timing:** To prevent false alarms from natural micro-movements, poor posture must be sustained for **more than 5.0 seconds** before triggering an alert.
7. **One-Click Calibration:** Simply sit comfortably upright looking at your monitor and click **"Calibrate Posture"**. The engine averages stable frames to construct your personalized ergonomic baseline.

---

## 🔔 Native System Notifications & Audio Alerts

- **Pure Native OS Toast:** Displays Windows 10/11 system notifications in the bottom-right Action Center (`winotify`).
- **Asynchronous & Non-Blocking:** Dispatched on separate daemon threads to guarantee 0% hitching or delay to camera and posture loops.
- **Notification Cooldown:** Built-in 25-second cooldown timer prevents desktop pop-up spam.
- **Audio Chime:** Gentle audio alert tone that can be toggled on/off in the header.

---

## 🔋 Battery & Resource Optimization

- **MediaPipe Pose Lite (`model_complexity=0`):** Ultra-fast, lightweight model tuned for low CPU usage.
- **3-Second Interval Background Monitoring:** When minimized to the system tray, checks run once every 3 seconds to preserve laptop battery.
- **Deep Sleep Mode:** Automatically powers down camera hardware after 2 minutes of OS keyboard/mouse inactivity.
- **Snooze / Pause:** Pause monitoring for 30 minutes or 1 hour directly from the tray context menu.
- **Sedentary Break Reminders:** Automatic stretch timer (configurable, default 45 minutes) that resets automatically when you step away from your desk.

---

## 📂 Project Structure

```text
posturfix/
├── .venv/                         # Python 3.11 Virtual Environment
├── assets/
│   ├── icon.ico                   # Desktop and tray application icon
│   └── mascot/                    # 2D mascot asset directory
├── app.py                         # Modern CustomTkinter graphical desktop interface
├── config.py                      # Configurable ergonomic thresholds & notification settings
├── posture_engine.py              # MediaPipe Pose tracking & calibration engine
├── build_exe.py                   # Automated PyInstaller packaging script
├── create_shortcut.ps1            # Creates desktop shortcut pointing to executable
├── requirements.txt               # Dependencies list
├── push_to_github.bat             # Quick Git push automation
└── README.md                      # Documentation
```

---

## 🚀 Quickstart Guide

### 1. Prerequisites
- Windows 10 / 11
- Python 3.11 (or 3.10)
- Webcam (built-in or USB)

### 2. Setup Virtual Environment & Install Dependencies
```powershell
# Activate virtual environment (Windows PowerShell)
.\.venv\Scripts\Activate.ps1

# Install requirements
pip install -r requirements.txt
```

### 3. Run the Application
```powershell
python app.py
```

---

## 📦 Building Standalone Executable (.exe)

To build a standalone Windows executable using PyInstaller:

```powershell
python build_exe.py
```

The compiled standalone application will be generated in `dist/PosturFix/PosturFix.exe`.
A desktop shortcut can be created using:

```powershell
powershell -ExecutionPolicy Bypass -File .\create_shortcut.ps1
```
