"""
Automated PyInstaller Build Script for Posture Guardian.
Packages the offline application into a standalone desktop executable.

Handles:
- CustomTkinter data files and JSON themes
- MediaPipe pose models, binary graphs, and DLLs
- Assets folder (mascot images)
- Console hiding for a native GUI app experience
"""

import os
import sys
import subprocess
import shutil

def build_executable():
    print("=" * 60)
    print(" Building Standalone Executable: PosturFix")
    print("=" * 60)

    project_dir = os.path.dirname(os.path.abspath(__file__))
    assets_dir = os.path.join(project_dir, "assets")

    # Ensure assets directory exists
    if not os.path.exists(assets_dir):
        os.makedirs(assets_dir, exist_ok=True)

    import tempfile
    temp_workpath = os.path.join(tempfile.gettempdir(), "pyi_build_posturfix")
    temp_distpath = os.path.join(tempfile.gettempdir(), "pyi_dist_posturfix")

    final_dist_dir = os.path.join(project_dir, "dist", "PosturFix")

    # PyInstaller arguments
    cmd = [
        sys.executable,
        "-m", "PyInstaller",
        "--name=PosturFix",
        "--onedir",                       # Fast startup directory distribution (or --onefile)
        "--windowed",                     # Hide terminal console
        "--noconfirm",                    # Overwrite existing build
        f"--workpath={temp_workpath}",    # Bypass OneDrive file-locking on intermediate builds
        f"--distpath={temp_distpath}",    # Bypass OneDrive file-locking on dist directory
        "--collect-all=customtkinter",    # Ensure all themes/fonts are included
        "--collect-all=mediapipe",        # Ensure tflite models & binary pb are included
        "--collect-all=pystray",          # Ensure Windows tray hooks are included
        "--collect-all=pynput",           # Ensure global input hooks are included
        "--collect-all=plyer",            # Ensure notification backend is included
        "--collect-all=winotify",         # Ensure Windows 10/11 toast backend is included
        f"--icon={os.path.join(assets_dir, 'icon.ico')}",
        f"--add-data={assets_dir};assets" if sys.platform == "win32" else f"--add-data={assets_dir}:assets",
        os.path.join(project_dir, "app.py"),
    ]

    print(f"Executing: {' '.join(cmd)}\n")
    try:
        subprocess.check_call(cmd, cwd=project_dir)

        # Copy completed build from temp to project dist
        os.makedirs(os.path.dirname(final_dist_dir), exist_ok=True)
        if os.path.exists(final_dist_dir):
            shutil.rmtree(final_dist_dir, ignore_errors=True)

        built_folder = os.path.join(temp_distpath, "PosturFix")
        if os.path.exists(built_folder):
            shutil.copytree(built_folder, final_dist_dir, dirs_exist_ok=True)

        # Package distribution zip file for sharing
        zip_path = os.path.join(project_dir, "PosturFix_v1.0_Windows")
        shutil.make_archive(zip_path, "zip", root_dir=os.path.join(project_dir, "dist"), base_dir="PosturFix")
        full_zip = f"{zip_path}.zip"
        zip_size_mb = round(os.path.getsize(full_zip) / (1024 * 1024), 2) if os.path.exists(full_zip) else 0

        print("\n" + "=" * 60)
        print(" [SUCCESS] Build completed successfully!")
        print(f" Executable: {os.path.join(final_dist_dir, 'PosturFix.exe')}")
        print(f" Distribution ZIP: {full_zip} ({zip_size_mb} MB)")
        print("=" * 60)
    except subprocess.CalledProcessError as e:
        print(f"\n [ERROR] Build failed with exit code: {e.returncode}")
        sys.exit(e.returncode)

if __name__ == "__main__":
    build_executable()
