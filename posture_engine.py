"""
Posture Detection Engine
Privacy-First, 100% Offline Posture Analysis using Google MediaPipe Pose.

All processing occurs in local RAM. No frames or biometric data are saved to disk
or transmitted over any network interface.
"""

import math
import time
import threading
import sys
from enum import Enum
from dataclasses import dataclass, field
from typing import Optional, Tuple, List, Dict, Any

import cv2
import numpy as np
import mediapipe as mp

from config import PostureConfig

# Platform-specific audio alert helper
if sys.platform == "win32":
    import winsound
else:
    winsound = None


class PostureState(Enum):
    NO_PERSON = "NO_PERSON"
    UNCALIBRATED = "UNCALIBRATED"
    CALIBRATING = "CALIBRATING"
    GOOD = "GOOD"
    WARNING = "WARNING"
    SLOUCHING = "SLOUCHING"


@dataclass
class PostureMetrics:
    state: PostureState = PostureState.NO_PERSON
    neck_ratio: float = 0.0
    baseline_neck_ratio: float = 0.0
    shoulder_tilt_deg: float = 0.0
    baseline_shoulder_tilt_deg: float = 0.0
    head_tilt_deg: float = 0.0
    baseline_head_tilt_deg: float = 0.0
    shoulder_width: float = 0.0
    baseline_shoulder_width: float = 0.0
    slouch_duration: float = 0.0
    slouch_reason: str = ""
    is_calibrated: bool = False
    calibration_progress: float = 0.0
    session_good_posture_percentage: float = 100.0


class PostureEngine:
    """
    Core engine responsible for:
    1. Running MediaPipe Pose estimation on live video frames.
    2. Calibrating baseline ergonomic posture geometry.
    3. Detecting slouching, forward neck drop, shoulder tilts, and screen hunches.
    4. Managing warning debounce and audio alert notifications.
    """

    def __init__(self, config: Optional[PostureConfig] = None):
        self.config = config or PostureConfig()

        # Initialize MediaPipe Pose
        self.mp_pose = mp.solutions.pose
        self.mp_drawing = mp.solutions.drawing_utils
        self.mp_drawing_styles = mp.solutions.drawing_styles

        self.pose = self.mp_pose.Pose(
            static_image_mode=False,
            model_complexity=0,  # 0=Lite model for minimum CPU overhead
            smooth_landmarks=True,
            enable_segmentation=False,
            min_detection_confidence=self.config.min_detection_confidence,
            min_tracking_confidence=self.config.min_tracking_confidence,
        )

        # Baseline calibration values
        self.is_calibrated = False
        self.baseline_neck_ratio: float = 0.0
        self.baseline_shoulder_tilt_deg: float = 0.0
        self.baseline_head_tilt_deg: float = 0.0
        self.baseline_shoulder_width: float = 0.0

        # Calibration state buffer
        self.calibrating: bool = False
        self._calibration_samples: List[Dict[str, float]] = []

        # Slouch timer & alert tracking
        self._slouch_start_time: Optional[float] = None
        self._last_alert_time: float = 0.0
        self._last_state: PostureState = PostureState.NO_PERSON

        # Session tracking statistics
        self._session_start_time = time.time()
        self._total_good_time: float = 0.0
        self._total_monitored_time: float = 0.0
        self._last_frame_timestamp: float = time.time()

        # Audio lock to prevent overlapping beeps
        self._audio_lock = threading.Lock()

    def start_calibration(self) -> None:
        """Initiates calibration routine."""
        self.calibrating = True
        self._calibration_samples.clear()

    def cancel_calibration(self) -> None:
        """Cancels an in-progress calibration."""
        self.calibrating = False
        self._calibration_samples.clear()

    @staticmethod
    def _calculate_angle(point_a: Tuple[float, float], point_b: Tuple[float, float]) -> float:
        """Calculates horizontal tilt angle between two points in degrees."""
        dx = point_b[0] - point_a[0]
        dy = point_b[1] - point_a[1]
        angle_rad = math.atan2(dy, dx)
        return math.degrees(angle_rad)

    @staticmethod
    def _euclidean_distance(point_a: Tuple[float, float], point_b: Tuple[float, float]) -> float:
        """Calculates 2D Euclidean distance."""
        return math.hypot(point_b[0] - point_a[0], point_b[1] - point_a[1])

    def _play_alert_sound(self) -> None:
        """Triggers a short, gentle alert sound in a background worker thread."""
        if not self.config.audio_alert_enabled:
            return

        def _sound_worker():
            if not self._audio_lock.acquire(blocking=False):
                return  # Beep already in progress
            try:
                if sys.platform == "win32" and winsound is not None:
                    # Warm alert tone
                    winsound.Beep(self.config.audio_frequency_hz, self.config.audio_duration_ms)
                else:
                    # Terminal bell fallback for non-Windows systems
                    sys.stdout.write("\a")
                    sys.stdout.flush()
            except Exception:
                pass
            finally:
                self._audio_lock.release()

        threading.Thread(target=_sound_worker, daemon=True).start()

    def process_frame(self, frame_bgr: np.ndarray) -> Tuple[np.ndarray, PostureMetrics]:
        """
        Processes a single BGR video frame in-memory.
        Returns:
            processed_frame: Annotated frame (if drawing enabled)
            metrics: PostureMetrics dataclass with current state and measurements
        """
        now = time.time()
        dt = max(0.001, now - self._last_frame_timestamp)
        self._last_frame_timestamp = now

        # Requirement 2: Frame Downscaling to 640x480 for minimum CPU consumption
        h_orig, w_orig = frame_bgr.shape[:2]
        h, w = h_orig, w_orig
        target_w = self.config.inference_width
        target_h = self.config.inference_height

        if w_orig > target_w or h_orig > target_h:
            infer_frame = cv2.resize(frame_bgr, (target_w, target_h), interpolation=cv2.INTER_NEAREST)
        else:
            infer_frame = frame_bgr

        # Convert downscaled frame to RGB for MediaPipe inference (100% in RAM)
        frame_rgb = cv2.cvtColor(infer_frame, cv2.COLOR_BGR2RGB)
        results = self.pose.process(frame_rgb)

        metrics = PostureMetrics(
            is_calibrated=self.is_calibrated,
            baseline_neck_ratio=self.baseline_neck_ratio,
            baseline_shoulder_tilt_deg=self.baseline_shoulder_tilt_deg,
            baseline_head_tilt_deg=self.baseline_head_tilt_deg,
            baseline_shoulder_width=self.baseline_shoulder_width,
        )

        # Requirement 4: Fast-Fail (Idle Mode)
        # If no pose landmarks are detected, immediately break out of calculation logic to save CPU
        if not results.pose_landmarks:
            self._slouch_start_time = None
            metrics.state = PostureState.NO_PERSON
            self._last_state = metrics.state
            metrics.session_good_posture_percentage = self._compute_good_percentage()
            return frame_bgr, metrics

        annotated_frame = frame_bgr.copy() if self.config.draw_skeleton else frame_bgr

        landmarks = results.pose_landmarks.landmark

        # Extract Key Upper-Body Landmarks
        # Left & Right Shoulders
        sh_left = landmarks[self.mp_pose.PoseLandmark.LEFT_SHOULDER]
        sh_right = landmarks[self.mp_pose.PoseLandmark.RIGHT_SHOULDER]

        # Left & Right Ears
        ear_left = landmarks[self.mp_pose.PoseLandmark.LEFT_EAR]
        ear_right = landmarks[self.mp_pose.PoseLandmark.RIGHT_EAR]

        # Nose & Eyes for head tracking
        nose = landmarks[self.mp_pose.PoseLandmark.NOSE]
        eye_left = landmarks[self.mp_pose.PoseLandmark.LEFT_EYE]
        eye_right = landmarks[self.mp_pose.PoseLandmark.RIGHT_EYE]

        # Landmark visibility check: Shoulders and at least one ear/eye must be visible
        min_vis = 0.45
        if sh_left.visibility < min_vis or sh_right.visibility < min_vis:
            metrics.state = PostureState.NO_PERSON
            metrics.slouch_reason = "Shoulders partially obstructed"
            self._slouch_start_time = None
            return annotated_frame, metrics

        # Pixel Coordinates for geometric calculations
        p_sh_left = (sh_left.x * w, sh_left.y * h)
        p_sh_right = (sh_right.x * w, sh_right.y * h)
        p_ear_left = (ear_left.x * w, ear_left.y * h)
        p_ear_right = (ear_right.x * w, ear_right.y * h)
        p_nose = (nose.x * w, nose.y * h)

        # Midpoints
        mid_shoulder = (
            (p_sh_left[0] + p_sh_right[0]) / 2.0,
            (p_sh_left[1] + p_sh_right[1]) / 2.0,
        )
        mid_ear = (
            (p_ear_left[0] + p_ear_right[0]) / 2.0,
            (p_ear_left[1] + p_ear_right[1]) / 2.0,
        )

        # Scale Factor: Shoulder Width in pixels
        # Used to normalize vertical distances against camera zoom or moving back/forth
        shoulder_width = self._euclidean_distance(p_sh_left, p_sh_right)
        shoulder_width = max(shoulder_width, 1.0)  # Avoid zero division

        # Neck Vertical Distance: Head vertical height above shoulder line
        # In image coordinates, Y increases downward. So shoulder.y > ear.y when upright.
        neck_vertical_dist = mid_shoulder[1] - mid_ear[1]

        # Normalized Neck Ratio: Higher = upright neck, Lower = slouched/forward head
        neck_ratio = neck_vertical_dist / shoulder_width

        # Tilts in degrees
        shoulder_tilt_deg = abs(self._calculate_angle(p_sh_left, p_sh_right))
        head_tilt_deg = abs(self._calculate_angle(p_ear_left, p_ear_right))

        # Update current measurement metrics
        metrics.neck_ratio = neck_ratio
        metrics.shoulder_tilt_deg = shoulder_tilt_deg
        metrics.head_tilt_deg = head_tilt_deg
        metrics.shoulder_width = shoulder_width

        # Handle Calibration Routine
        if self.calibrating:
            metrics.state = PostureState.CALIBRATING
            self._calibration_samples.append({
                "neck_ratio": neck_ratio,
                "shoulder_tilt": shoulder_tilt_deg,
                "head_tilt": head_tilt_deg,
                "shoulder_width": shoulder_width,
            })

            progress = len(self._calibration_samples) / float(self.config.calibration_frame_count)
            metrics.calibration_progress = min(progress, 1.0)

            if len(self._calibration_samples) >= self.config.calibration_frame_count:
                # Average baseline values over collected stable frames
                self.baseline_neck_ratio = float(np.mean([s["neck_ratio"] for s in self._calibration_samples]))
                self.baseline_shoulder_tilt_deg = float(np.mean([s["shoulder_tilt"] for s in self._calibration_samples]))
                self.baseline_head_tilt_deg = float(np.mean([s["head_tilt"] for s in self._calibration_samples]))
                self.baseline_shoulder_width = float(np.mean([s["shoulder_width"] for s in self._calibration_samples]))

                self.is_calibrated = True
                self.calibrating = False
                metrics.is_calibrated = True
                metrics.state = PostureState.GOOD
                self._slouch_start_time = None

            # Render calibration visuals
            self._render_overlay(annotated_frame, mid_shoulder, mid_ear, p_sh_left, p_sh_right, metrics.state)
            return annotated_frame, metrics

        if not self.is_calibrated:
            metrics.state = PostureState.UNCALIBRATED
            metrics.slouch_reason = "Please sit upright and click Calibrate"
            self._render_overlay(annotated_frame, mid_shoulder, mid_ear, p_sh_left, p_sh_right, metrics.state)
            return annotated_frame, metrics

        # -------------------------------------------------------------
        # Ergonomic Posture Evaluation against Calibrated Baseline
        # -------------------------------------------------------------
        reasons: List[str] = []

        # 1. Forward Head Droop / Neck Slouch
        neck_drop_ratio = (self.baseline_neck_ratio - neck_ratio) / max(0.001, self.baseline_neck_ratio)
        if neck_drop_ratio > self.config.neck_ratio_drop_threshold:
            reasons.append(f"Neck slouching ({int(neck_drop_ratio * 100)}% drop)")

        # 2. Uneven / Tilted Shoulders
        shoulder_tilt_diff = abs(shoulder_tilt_deg - self.baseline_shoulder_tilt_deg)
        if shoulder_tilt_diff > self.config.shoulder_tilt_threshold_deg:
            reasons.append(f"Shoulder tilt ({int(shoulder_tilt_diff)}°)")

        # 3. Head Tilt / Asymmetric lean
        head_tilt_diff = abs(head_tilt_deg - self.baseline_head_tilt_deg)
        if head_tilt_diff > self.config.head_tilt_threshold_deg:
            reasons.append(f"Head tilt ({int(head_tilt_diff)}°)")

        # 4. Leaning excessively forward into the screen
        width_expansion = (shoulder_width - self.baseline_shoulder_width) / max(0.001, self.baseline_shoulder_width)
        if width_expansion > self.config.forward_lean_threshold:
            reasons.append("Leaning too close to screen")

        is_slouching = len(reasons) > 0
        metrics.slouch_reason = ", ".join(reasons) if is_slouching else "Good posture maintained"

        # Update Session Posture Statistics
        self._total_monitored_time += dt
        if not is_slouching:
            self._total_good_time += dt

        metrics.session_good_posture_percentage = self._compute_good_percentage()

        # Slouch Timer & State Transition
        if is_slouching:
            if self._slouch_start_time is None:
                self._slouch_start_time = now

            slouch_elapsed = now - self._slouch_start_time
            metrics.slouch_duration = slouch_elapsed

            if slouch_elapsed >= self.config.slouch_alert_delay_seconds:
                metrics.state = PostureState.SLOUCHING
                # Check alert throttle
                if (now - self._last_alert_time) >= self.config.alert_repeat_interval_seconds:
                    self._play_alert_sound()
                    self._last_alert_time = now
            else:
                metrics.state = PostureState.WARNING
        else:
            self._slouch_start_time = None
            metrics.slouch_duration = 0.0
            metrics.state = PostureState.GOOD

        self._last_state = metrics.state

        # Render Visual Overlays on Frame
        self._render_overlay(annotated_frame, mid_shoulder, mid_ear, p_sh_left, p_sh_right, metrics.state)

        return annotated_frame, metrics

    def _compute_good_percentage(self) -> float:
        """Returns the lifetime percentage of good posture for the current session."""
        if self._total_monitored_time <= 0:
            return 100.0
        pct = (self._total_good_time / self._total_monitored_time) * 100.0
        return max(0.0, min(100.0, pct))

    def _render_overlay(
        self,
        frame: np.ndarray,
        mid_shoulder: Tuple[float, float],
        mid_ear: Tuple[float, float],
        sh_left: Tuple[float, float],
        sh_right: Tuple[float, float],
        state: PostureState,
    ) -> None:
        """Draws aesthetic posture vectors and landmark nodes directly onto the frame."""
        if not self.config.draw_skeleton:
            return

        # Theme color mapping (BGR)
        color_map = {
            PostureState.GOOD: (64, 210, 110),        # Vibrant Green
            PostureState.WARNING: (0, 190, 255),      # Amber/Yellow
            PostureState.SLOUCHING: (60, 60, 235),     # Bright Coral Red
            PostureState.CALIBRATING: (255, 175, 40), # Cyan / Sky Blue
            PostureState.UNCALIBRATED: (180, 180, 180), # Gray
            PostureState.NO_PERSON: (120, 120, 120),  # Dark Gray
        }
        color = color_map.get(state, (200, 200, 200))

        # Convert coordinates to integers
        pt_mid_sh = (int(mid_shoulder[0]), int(mid_shoulder[1]))
        pt_mid_ear = (int(mid_ear[0]), int(mid_ear[1]))
        pt_sh_l = (int(sh_left[0]), int(sh_left[1]))
        pt_sh_r = (int(sh_right[0]), int(sh_right[1]))

        # Draw Shoulder Axis
        cv2.line(frame, pt_sh_l, pt_sh_r, color, 3, cv2.LINE_AA)

        # Draw Neck Spine Vector
        cv2.line(frame, pt_mid_sh, pt_mid_ear, color, 3, cv2.LINE_AA)

        # Draw Landmark Keypoints
        cv2.circle(frame, pt_mid_sh, 6, (255, 255, 255), -1, cv2.LINE_AA)
        cv2.circle(frame, pt_mid_sh, 8, color, 2, cv2.LINE_AA)

        cv2.circle(frame, pt_mid_ear, 6, (255, 255, 255), -1, cv2.LINE_AA)
        cv2.circle(frame, pt_mid_ear, 8, color, 2, cv2.LINE_AA)

        cv2.circle(frame, pt_sh_l, 5, color, -1, cv2.LINE_AA)
        cv2.circle(frame, pt_sh_r, 5, color, -1, cv2.LINE_AA)

    def set_sensitivity(self, level: str) -> None:
        """Configures posture sensitivity ('Low', 'Medium', 'High')."""
        level = level.lower()
        if level == "high":
            self.config.neck_ratio_drop_threshold = 0.12
            self.config.shoulder_tilt_threshold_deg = 8.0
            self.config.head_tilt_threshold_deg = 10.0
            self.config.forward_lean_threshold = 0.20
        elif level == "low":
            self.config.neck_ratio_drop_threshold = 0.25
            self.config.shoulder_tilt_threshold_deg = 18.0
            self.config.head_tilt_threshold_deg = 20.0
            self.config.forward_lean_threshold = 0.40
        else:  # Medium default
            self.config.neck_ratio_drop_threshold = 0.18
            self.config.shoulder_tilt_threshold_deg = 12.0
            self.config.head_tilt_threshold_deg = 14.0
            self.config.forward_lean_threshold = 0.30

    def close(self) -> None:
        """Releases MediaPipe and memory resources cleanly."""
        if self.pose:
            self.pose.close()
