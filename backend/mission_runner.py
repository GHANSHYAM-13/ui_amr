#!/usr/bin/env python3
"""
mission_runner.py  —  AMR Waypoint Mission
Reads waypoints from <amr_ui>/missions/mission.json
Navigates to each waypoint in order, loops per --cycles arg (default: infinite).

mission.json format:
{
  "waypoints": [
    {"name": "Station A", "x": 1.0, "y": -0.5, "yaw": 0.0, "delay": 2},
    {"name": "Station B", "x": 3.0, "y":  1.2, "yaw": 1.57}
  ]
}

Usage:
  python3 mission_runner.py              # infinite loop
  python3 mission_runner.py --cycles 3  # run 3 cycles then go home
"""

import sys
import json
import math
import time
import signal
import os

import rclpy
from nav2_simple_commander.robot_navigator import BasicNavigator, TaskResult
from geometry_msgs.msg import PoseStamped

# ── Dynamic path resolution ──────────────────────────────────────────────────
# mission_runner.py lives in amr_ui/backend/
_BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
_BASE_DIR    = os.path.dirname(_BACKEND_DIR)   # amr_ui/
MISSION_FILE = os.path.join(_BASE_DIR, "missions", "mission.json")

# File-based pause flag — written by server.py /mission/pause endpoint
PAUSE_FLAG_FILE    = os.path.join(_BASE_DIR, "missions", "pause.flag")
# File-based progress — read by server.py /mission/status endpoint
PROGRESS_FILE      = os.path.join(_BASE_DIR, "missions", "mission_progress.json")


# ── Helpers ───────────────────────────────────────────────────────────────────

def yaw_to_quaternion(yaw: float):
    return 0.0, 0.0, math.sin(yaw / 2.0), math.cos(yaw / 2.0)


def make_pose(nav: BasicNavigator, x: float, y: float, yaw: float) -> PoseStamped:
    qx, qy, qz, qw = yaw_to_quaternion(yaw)
    pose = PoseStamped()
    pose.header.frame_id = "map"
    pose.header.stamp = nav.get_clock().now().to_msg()
    pose.pose.position.x  = float(x)
    pose.pose.position.y  = float(y)
    pose.pose.position.z  = 0.0
    pose.pose.orientation.x = qx
    pose.pose.orientation.y = qy
    pose.pose.orientation.z = qz
    pose.pose.orientation.w = qw
    return pose


def get_home_position() -> tuple[float, float, float]:
    """Read home pose from robot_config.js; fall back to hardcoded defaults."""
    hx, hy, hyaw = 0.0, 0.0, 0.0
    cfg_path = os.path.join(_BASE_DIR, "config", "robot_config.js")
    try:
        import re
        with open(cfg_path) as f:
            content = f.read()
        m = re.search(
            r'home\s*:\s*\{\s*x\s*:\s*([\d\.-]+)\s*,\s*y\s*:\s*([\d\.-]+)\s*,\s*yaw\s*:\s*([\d\.-]+)',
            content
        )
        if m:
            hx   = float(m.group(1))
            hy   = float(m.group(2))
            hyaw = float(m.group(3))
    except Exception as e:
        print(f"[mission_runner] Warning: could not parse home from robot_config.js: {e}", flush=True)
    return hx, hy, hyaw


def write_progress(cycle: int, waypoint: int, waypoint_name: str) -> None:
    """Write current mission progress to disk so server.py can serve it."""
    try:
        with open(PROGRESS_FILE, "w") as f:
            json.dump({
                "cycle": cycle,
                "waypoint": waypoint,
                "waypoint_name": waypoint_name,
            }, f)
    except Exception:
        pass  # best-effort; never crash the mission loop



def get_use_sim_time() -> bool:
    """Read use_sim_time from robot_config.js; default False (real robot)."""
    cfg_path = os.path.join(_BASE_DIR, "config", "robot_config.js")
    try:
        with open(cfg_path) as f:
            for line in f:
                if "use_sim_time" in line and "true" in line.lower():
                    return True
    except Exception:
        pass
    return False


def navigate_and_wait(
    nav: BasicNavigator,
    pose: PoseStamped,
    name: str,
    stop_flag: list[bool]
) -> TaskResult:
    """
    Send goToPose and block until complete.
    Handles pause flag polling and stop requests mid-navigation.
    Returns the TaskResult for the caller to act on.
    """
    nav.goToPose(pose)

    while not nav.isTaskComplete():

        # ── Stop request (SIGTERM from server.py kill_process) ─────────────
        if stop_flag[0]:
            print(f"\n[mission_runner] Stop requested — cancelling goal for '{name}'", flush=True)
            nav.cancelTask()
            break

        # ── File-based pause / resume ──────────────────────────────────────
        if os.path.exists(PAUSE_FLAG_FILE):
            print(f"\n[mission_runner] ⏸  Pause flag detected. Holding robot at '{name}'...", flush=True)
            while os.path.exists(PAUSE_FLAG_FILE):
                if stop_flag[0]:
                    break
                time.sleep(0.3)
            if stop_flag[0]:
                break
            print(f"\n[mission_runner] ▶  Resumed. Continuing navigation to '{name}'...", flush=True)

        # ── Live pose feedback ─────────────────────────────────────────────
        feedback = nav.getFeedback()
        if feedback:
            cp = feedback.current_pose.pose
            print(
                f"  [{name}] x={cp.position.x:.2f}  y={cp.position.y:.2f}",
                end="\r", flush=True
            )
        time.sleep(0.2)

    return nav.getResult()


def do_delay(delay_s: float, name: str, stop_flag: list[bool]) -> None:
    """
    Sleep at a waypoint for delay_s seconds.
    Respects both stop flag and pause flag during the wait.
    Resets the timer after a resume so the full delay is served post-pause.
    """
    if not delay_s or delay_s <= 0:
        return

    print(f"[mission_runner] ⏳ Waiting {delay_s}s at '{name}'...", flush=True)
    t_start = time.time()

    while time.time() - t_start < delay_s:
        if stop_flag[0]:
            return

        if os.path.exists(PAUSE_FLAG_FILE):
            print(f"\n[mission_runner] ⏸  Paused during station wait at '{name}'.", flush=True)
            while os.path.exists(PAUSE_FLAG_FILE):
                if stop_flag[0]:
                    return
                time.sleep(0.3)
            t_start = time.time()   # reset timer — serve full delay after resume
            print(f"[mission_runner] ▶  Resumed station wait at '{name}'.", flush=True)

        time.sleep(0.1)


def navigate_home(nav: BasicNavigator, stop_flag: list[bool]) -> None:
    """Navigate robot back to home pose defined in robot_config.js."""
    hx, hy, hyaw = get_home_position()
    print(
        f"\n[mission_runner] 🏠 Returning home (x={hx:.3f}, y={hy:.3f}, yaw={hyaw:.3f})...",
        flush=True
    )
    home_pose = make_pose(nav, hx, hy, hyaw)
    result = navigate_and_wait(nav, home_pose, "Home", stop_flag)

    if not stop_flag[0] and result == TaskResult.SUCCEEDED:
        print("[mission_runner] 🏁 Arrived at Home Station.", flush=True)
    elif stop_flag[0]:
        print("[mission_runner] Home navigation aborted by stop request.", flush=True)
    else:
        print("[mission_runner] ⚠  Failed to reach Home Station.", flush=True)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    # ── Load mission file ─────────────────────────────────────────────────────
    try:
        with open(MISSION_FILE, "r") as f:
            mission = json.load(f)
    except Exception as e:
        print(f"[mission_runner] ERROR: Could not load mission file: {e}", flush=True)
        sys.exit(1)

    waypoints: list[dict] = mission.get("waypoints", [])
    if not waypoints:
        print("[mission_runner] ERROR: No waypoints in mission file.", flush=True)
        sys.exit(1)

    print(f"[mission_runner] Loaded {len(waypoints)} waypoints:", flush=True)
    for i, wp in enumerate(waypoints):
        print(f"  {i+1}. {wp['name']}  x={wp['x']}  y={wp['y']}  yaw={wp['yaw']}", flush=True)

    # ── Parse --cycles argument ───────────────────────────────────────────────
    cycles_to_run = -1   # -1 = infinite
    if "--cycles" in sys.argv:
        try:
            idx = sys.argv.index("--cycles")
            cycles_to_run = int(sys.argv[idx + 1])
            print(f"[mission_runner] Cycle count: {cycles_to_run}", flush=True)
        except (ValueError, IndexError) as e:
            print(f"[mission_runner] Warning: bad --cycles arg ({e}), defaulting to infinite.", flush=True)

    # ── ROS2 init ─────────────────────────────────────────────────────────────
    rclpy.init()

    # Use a distinct node name so this client never collides with bt_navigator
    # or other Nav2 nodes. A name collision confuses the lifecycle manager and
    # can trigger spurious deactivation — which causes the robot to jump
    # position on the map when the mission starts or stops.
    nav = BasicNavigator(node_name="mission_runner_client")

    # Read use_sim_time from robot_config.js (never hardcode True for real robot)
    _use_sim = get_use_sim_time()
    nav.set_parameters([
        rclpy.parameter.Parameter(
            "use_sim_time",
            rclpy.parameter.Parameter.Type.BOOL,
            _use_sim
        )
    ])
    print(f"[mission_runner] use_sim_time={_use_sim}", flush=True)

    # ── SIGTERM handler ───────────────────────────────────────────────────────
    # When "Stop Mission" is clicked, server.py kills this process with SIGTERM.
    # Without this handler Python raises no exception — the process just dies
    # mid-goal, leaving Nav2 with an active goal that drives the robot to a
    # stale or zero pose (causing spin-then-reverse behaviour).
    #
    # CRITICAL: Do NOT call nav.lifecycleShutdown() here — that deactivates all
    # Nav2 lifecycle nodes (bt_navigator, controller_server, planner_server…)
    # leaving them in an unconfigured state that rejects every subsequent goal
    # until the entire Nav2 stack is restarted.
    _stop_requested: list[bool] = [False]

    def _sigterm_handler(signum, frame):
        _stop_requested[0] = True
        print("\n[mission_runner] SIGTERM received — cancelling active goal…", flush=True)
        try:
            nav.cancelTask()
        except Exception:
            pass

    signal.signal(signal.SIGTERM, _sigterm_handler)

    # ── Wait for Nav2 ─────────────────────────────────────────────────────────
    print("[mission_runner] Waiting for Nav2 to become active...", flush=True)
    nav.waitUntilNav2Active()
    print("[mission_runner] Nav2 active. Starting mission loop.", flush=True)

    # ── Mission loop ──────────────────────────────────────────────────────────
    loop = 0
    try:
        while True:
            if _stop_requested[0]:
                break

            # ── Cycle completion check ─────────────────────────────────────
            if cycles_to_run > 0 and loop >= cycles_to_run:
                print(f"\n[mission_runner] ✓ Completed all {cycles_to_run} cycle(s).", flush=True)
                navigate_home(nav, _stop_requested)
                break

            loop += 1
            print(f"\n[mission_runner] ── Loop {loop}"
                  + (f"/{cycles_to_run}" if cycles_to_run > 0 else " (∞)")
                  + " ──", flush=True)

            # ── Waypoint iteration ─────────────────────────────────────────
            for i, wp in enumerate(waypoints):
                if _stop_requested[0]:
                    break

                name  = wp["name"]
                pose  = make_pose(nav, wp["x"], wp["y"], wp["yaw"])
                delay = float(wp.get("delay", 0))

                # Write live progress so the UI can display it
                write_progress(loop, i + 1, name)

                print(f"[mission_runner] → Navigating to '{name}' ({i+1}/{len(waypoints)})",
                      flush=True)

                result = navigate_and_wait(nav, pose, name, _stop_requested)

                if _stop_requested[0]:
                    break

                if result == TaskResult.SUCCEEDED:
                    print(f"\n[mission_runner] ✓ Reached '{name}'", flush=True)
                    do_delay(delay, name, _stop_requested)

                elif result == TaskResult.CANCELED:
                    # cancelTask() was called — either by stop request (handled above)
                    # or externally. Either way, do NOT call lifecycleShutdown():
                    # that deactivates the entire Nav2 stack.
                    print(f"\n[mission_runner] Mission canceled — Nav2 stack stays active.",
                          flush=True)
                    try:
                        rclpy.shutdown()
                    except Exception:
                        pass
                    return

                elif result == TaskResult.FAILED:
                    print(f"\n[mission_runner] ✗ Failed to reach '{name}', skipping.",
                          flush=True)

            # ── Brief inter-cycle pause ────────────────────────────────────
            if not _stop_requested[0] and (cycles_to_run == -1 or loop < cycles_to_run):
                time.sleep(0.5)

    except KeyboardInterrupt:
        print("\n[mission_runner] Stopped by user (KeyboardInterrupt).", flush=True)
        try:
            nav.cancelTask()
        except Exception:
            pass

    # ── Shutdown — DO NOT call lifecycleShutdown() ────────────────────────────
    # nav.lifecycleShutdown() would bring down bt_navigator, planner_server,
    # controller_server, and costmap nodes — the entire Nav2 stack. The robot
    # would then reject every new goal until Nav2 is restarted externally.
    # Simply shutting down rclpy is sufficient to clean up this process.
    try:
        rclpy.shutdown()
    except Exception:
        pass

    print("[mission_runner] Exited cleanly.", flush=True)


if __name__ == "__main__":
    main()