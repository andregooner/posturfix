"""
Configuration settings for PosturFix.
100% Offline, Privacy-First Posture Monitor.
Optimized for minimal CPU and battery consumption.
"""
from dataclasses import dataclass

@dataclass
class PostureConfig:
    # Camera & Resolution Downscaling Settings
    camera_index: int = 0
    frame_width: int = 640             # Downscale resolution to 640x480 before processing
    frame_height: int = 480
    inference_width: int = 640         # Downscale resolution for MediaPipe inference
    inference_height: int = 480
    fps_target: int = 20

    # MediaPipe Optimization (0=Lite Model for minimum CPU overhead)
    model_complexity: int = 0          # 0=Lite (Fastest, lowest CPU), 1=Full, 2=Heavy
    min_detection_confidence: float = 0.5
    min_tracking_confidence: float = 0.5

    # Check Interval (3-Second Interval for Responsive Detection)
    posture_check_interval_seconds: float = 3.0   # Background posture check interval in seconds

    # Posture Thresholds (Percentages & Degrees)
    neck_ratio_drop_threshold: float = 0.18       # If neck height drops by >18% of baseline
    shoulder_tilt_threshold_deg: float = 12.0     # Max shoulder tilt deviation in degrees
    head_tilt_threshold_deg: float = 14.0         # Max head tilt deviation in degrees
    forward_lean_threshold: float = 0.30          # Shoulder width increase >30% means leaning into screen

    # Alert Timing
    slouch_alert_delay_seconds: float = 5.0       # Sustained slouch duration before alert fires
    alert_repeat_interval_seconds: float = 10.0   # Repeat alert interval if user remains slouched
    audio_alert_enabled: bool = True
    audio_frequency_hz: int = 880                 # Pleasant warm notification tone
    audio_duration_ms: int = 250

    # Calibration Settings
    calibration_frame_count: int = 10             # Number of baseline samples to average

    # UI & Privacy Settings
    privacy_mode_default: bool = False            # If true, camera preview is hidden by default
    draw_skeleton: bool = True                    # Draw pose landmarks when camera preview is on

    # Sedentary (Prolonged Sitting) Break Settings
    sedentary_reminder_enabled: bool = True
    sedentary_interval_minutes: int = 45          # Default 45 minutes
    sedentary_notification_title: str = "PosturFix • Break Time!"
    sedentary_notification_message: str = "Time to stand up! Take a 5-minute stretch break."
    sedentary_auto_reset_away_seconds: float = 180.0  # Reset timer if user leaves desk for > 3 minutes

    # Deep Sleep (OS Idle Detection Power Saving)
    deep_sleep_enabled: bool = True
    idle_sleep_threshold_seconds: float = 120.0  # 2 minutes of OS inactivity triggers Deep Sleep

    # Camera Conflict Handling
    camera_retry_interval_busy_seconds: float = 60.0  # Backoff sleep when camera is locked by another app
