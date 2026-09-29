# 🛡️ PosturFix (100% Offline & Privacy-First)

> A lightweight, modern desktop application that monitors your sitting posture in real-time using your webcam. Built with privacy and ergonomics at its core.

---

## 🔒 Privacy & Offline Architecture

PosturFix was engineered from day one under a strict zero-trust privacy policy:
- **100% Offline Local Inference:** Runs Google MediaPipe Pose models locally on your CPU. No network calls or cloud APIs are ever made.
- **Volatile In-Memory Processing:** Video frames are captured directly into RAM (`numpy.ndarray`), analyzed by the pose estimator, and immediately recycled by garbage collection.
- **Zero Disk Writes:** Not a single video frame, snapshot, or biometric landmark is ever written to your hard drive.
- **Privacy Mode (Feed Concealment):** Need to hide your camera feed during meetings or screen shares? Toggle "Privacy Mode" in the app. The video preview is replaced with an elegant privacy shield visual while local background analysis continues uninterrupted.

---

## 📐 How the Ergonomic Posture Engine Works

Rather than relying on brittle raw pixel coordinates that break when you shift in your seat, Posture Guardian uses **scale-invariant geometry**:

1. **Natural Scale Normalization:** The Euclidean distance between your left and right shoulders ($W_{shoulder}$) serves as the dynamic scale factor.
2. **Vertical Neck Ratio ($R_{neck}$):** 
   $$R_{neck} = \frac{Y_{shoulder\_midpoint} - Y_{ear\_midpoint}}{W_{shoulder}}$$
   When you sit upright, this ratio is at its maximum. When your head drops forward or you slouch, this ratio decreases significantly.
3. **Lateral Shoulder & Head Tilt:** Angles relative to horizontal are monitored to detect asymmetric leaning to one side.
4. **Lean Proximity:** Expansions in shoulder width relative to baseline detect excessive forward hunching toward the monitor.
5. **Debounce & Alert Timing:** To prevent false alarms from natural micro-movements, poor posture must be sustained for **more than 5.0 seconds** before triggering an alert.
6. **One-Click Calibration:** Simply sit comfortably upright looking at your monitor and click **"Calibrate Posture"**. The engine averages 30 stable frames to construct your personalized ergonomic baseline.

---

## 📂 Project Structure

```text
posture-guardian/
├── .venv/                         # Python 3.11 Virtual Environment
├── assets/
│   └── mascot/                    # 2D mascot asset directory
│       └── README.txt             # Guide for dropping custom PNG sprites
├── config.py                      # Configurable ergonomic thresholds & audio settings
├── posture_engine.py              # MediaPipe Pose tracking & calibration engine
├── app.py                         # Modern CustomTkinter graphical desktop interface
├── build_exe.py                   # Automated PyInstaller packaging script
├── requirements.txt               # Dependencies list
└── README.md                      # Documentation
```

---

## 🚀 Quickstart Guide

### 1. Prerequisites
- Python 3.11 (or 3.10)
- Webcam (built-in or USB)

### 2. Setup Virtual Environment & Install Dependencies
```bash
# Activate virtual environment (Windows PowerShell)
.\.venv\Scripts\Activate.ps1

# Install requirements
pip install -r requirements.txt
```

### 3. Run the Application
```bash
python app.py
```

---

## 🧸 2D Mascot & Gamification

Posture Guardian includes an interactive vector avatar ("Posture Pal") that dynamically reacts to your posture:
- **Good Posture:** Cheerful green mascot with a bright smile, upright posture, and encouraging tips.
- **Warning (< 5s Slouch):** Curious amber mascot noticing your head drooping.
- **Slouching (> 5s Slouch):** Alert red mascot with slumped posture and visual alert badge.
- **No Person / Inactive:** Sleeping avatar resting until you return.

### Custom Sprites
You can drop your own 2D character sprites into `assets/mascot/`:
- `good.png`
- `warning.png`
- `slouch.png`
- `calibrating.png`
- `neutral.png`
- `sleep.png`

---

## 📦 Building Standalone Executable (.exe)

To build a standalone Windows executable using PyInstaller:

```bash
# Run the automated build script
python build_exe.py
```

The compiled standalone application will be generated in `dist/PostureGuardian/PostureGuardian.exe`.
