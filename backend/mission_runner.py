#!/usr/bin/env python3
"""
mission_runner.py  —  AMR Waypoint Mission
Reads waypoints from /home/jd/ros2_ws/src/amr_ui/missions/mission.json
Navigates to each waypoint in order, loops forever until stopped.

mission.json format:
{
  "waypoints": [
    {"name": "Station A", "x": 1.0, "y": -0.5, "yaw": 0.0},
    {"name": "Station B", "x": 3.0, "y":  1.2, "yaw": 1.57}
  ]
}
"""

import sys
import json
import math
import time
import signal
import rclpy
from nav2_simple_commander.robot_navigator import BasicNavigator, TaskResult
from geometry_msgs.msg import PoseStamped


# Dynamic path: mission_runner.py lives in amr_ui/backend/
import os as _os
_BACKEND_DIR = _os.path.dirname(_os.path.abspath(__file__))
_BASE_DIR    = _os.path.dirname(_BACKEND_DIR)
MISSION_FILE = _os.path.join(_BASE_DIR, "missions", "mission.json")


def yaw_to_quaternion(yaw):
    return 0.0, 0.0, math.sin(yaw / 2.0), math.cos(yaw / 2.0)


def make_pose(nav, x, y, yaw):
    qx, qy, qz, qw = yaw_to_quaternion(yaw)
    pose = PoseStamped()
    pose.header.frame_id = "map"
    pose.header.stamp = nav.get_clock().now().to_msg()
    pose.pose.position.x = x
    pose.pose.position.y = y
    pose.pose.position.z = 0.0
    pose.pose.orientation.x = qx
    pose.pose.orientation.y = qy
    pose.pose.orientation.z = qz
    pose.pose.orientation.w = qw
    return pose


def main():
    # Load mission file
    try:
        with open(MISSION_FILE, "r") as f:
            mission = json.load(f)
    except Exception as e:
        print(f"[mission_runner] ERROR: Could not load mission file: {e}", flush=True)
        sys.exit(1)

    waypoints = mission.get("waypoints", [])
    if not waypoints:
        print("[mission_runner] ERROR: No waypoints in mission file.", flush=True)
        sys.exit(1)

    print(f"[mission_runner] Loaded {len(waypoints)} waypoints:", flush=True)
    for i, wp in enumerate(waypoints):
        print(f"  {i+1}. {wp['name']}  x={wp['x']}  y={wp['y']}  yaw={wp['yaw']}", flush=True)

    rclpy.init()
    # Use a distinct node name so this client never collides with bt_navigator
    # or other Nav2 nodes. A name collision confuses the lifecycle manager and
    # can trigger spurious deactivation — which is what causes the robot to
    # jump position on the map when the mission starts or stops.
    nav = BasicNavigator(node_name="mission_runner_client")
    # Read use_sim_time from robot_config.js
    _cfg_path = _os.path.join(_BASE_DIR, "config", "robot_config.js")
    _use_sim  = False
    try:
        with open(_cfg_path) as _f:
            for _line in _f:
                if "use_sim_time" in _line and "true" in _line.lower():
                    _use_sim = True; break
    except Exception:
        pass
    nav.set_parameters([rclpy.parameter.Parameter("use_sim_time",
                        rclpy.parameter.Parameter.Type.BOOL, _use_sim)])

    # ── Graceful stop on SIGTERM (sent by server.py kill_process) ────────────
    # When "Stop Mission" is clicked the server kills this process with SIGTERM.
    # Without this handler Python raises no exception — the process just dies
    # mid-goal, leaving Nav2 with an active goal that drives the robot to a
    # stale or zero pose (causing the spin-then-reverse behaviour).
    _stop_requested = [False]

    def _sigterm_handler(signum, frame):
        _stop_requested[0] = True
        print("\n[mission_runner] SIGTERM received — cancelling active goal…", flush=True)
        try:
            nav.cancelTask()
        except Exception:
            pass
        # Do NOT call nav.lifecycleShutdown() here — that deactivates all Nav2
        # lifecycle nodes (bt_navigator, controller_server, planner_server…)
        # leaving them in an unconfigured state that rejects every subsequent
        # goal until the entire Nav2 stack is restarted.

    signal.signal(signal.SIGTERM, _sigterm_handler)
    # ─────────────────────────────────────────────────────────────────────────

    # Parse cycles count from arguments
    cycles_to_run = -1
    if "--cycles" in sys.argv:
        try:
            idx = sys.argv.index("--cycles")
            cycles_to_run = int(sys.argv[idx + 1])
        except Exception:
            pass

    PAUSE_FLAG_FILE = _os.path.join(_BASE_DIR, "missions", "pause.flag")

    # Read home from config
    def get_home_position():
        hx, hy, hyaw = 2.707015, 2.565094, 1.995912
        try:
            cfg_path = _os.path.join(_BASE_DIR, "config", "robot_config.js")
            if _os.path.exists(cfg_path):
                with open(cfg_path) as f:
                    content = f.read()
                import re
                # Match home: { x: ..., y: ..., yaw: ... }
                m = re.search(r'home\s*:\s*\{\s*x\s*:\s*([\d\.-]+)\s*,\s*y\s*:\s*([\d\.-]+)\s*,\s*yaw\s*:\s*([\d\.-]+)', content)
                if m:
                    hx = float(m.group(1))
                    hy = float(m.group(2))
                    hyaw = float(m.group(3))
        except Exception as e:
            print(f"[mission_runner] Error parsing home config: {e}", flush=True)
        return hx, hy, hyaw

    print("[mission_runner] Waiting for Nav2 to become active...", flush=True)
    nav.waitUntilNav2Active()
    print("[mission_runner] Nav2 active. Starting mission loop.", flush=True)

    loop = 0
    try:
        while True:
            if _stop_requested[0]:
                break
            
            # Check cycles completion
            if cycles_to_run > 0 and loop >= cycles_to_run:
                hx, hy, hyaw = get_home_position()
                print(f"\n[mission_runner] ✓ Completed all {cycles_to_run} cycles!", flush=True)
                print(f"[mission_runner] 🏠 Returning to Home Station (x={hx:.3f}, y={hy:.3f}, yaw={hyaw:.3f})...", flush=True)
                home_pose = make_pose(nav, hx, hy, hyaw)
                nav.goToPose(home_pose)
                
                while not nav.isTaskComplete():
                    if _stop_requested[0]:
                        nav.cancelTask()
                        break
                    if _os.path.exists(PAUSE_FLAG_FILE):
                        print("\n[mission_runner] Pause flag detected on go-home. Halting...", flush=True)
                        nav.cancelTask()
                        while _os.path.exists(PAUSE_FLAG_FILE):
                            if _stop_requested[0]:
                                break
                            time.sleep(0.3)
                        if _stop_requested[0]:
                            break
                        print("\n[mission_runner] Resuming go-home navigation...", flush=True)
                        nav.goToPose(home_pose)
                    time.sleep(0.5)
                
                if not _stop_requested[0]:
                    print("[mission_runner] 🏁 Arrived at Home Station. Mission successfully finished!", flush=True)
                break

            loop += 1
            print(f"\n[mission_runner] ── Loop {loop} ──", flush=True)

            for i, wp in enumerate(waypoints):
                if _stop_requested[0]:
                    break
                name = wp["name"]
                pose = make_pose(nav, wp["x"], wp["y"], wp["yaw"])

                print(f"[mission_runner] → Navigating to '{name}' ({i+1}/{len(waypoints)})", flush=True)
                nav.goToPose(pose)

                while not nav.isTaskComplete():
                    if _stop_requested[0]:
                        print(f"\n[mission_runner] Stop requested — cancelling goal for '{name}'", flush=True)
                        nav.cancelTask()
                        break
                    
                    # File-based Pause / Resume check
                    if _os.path.exists(PAUSE_FLAG_FILE):
                        print(f"\n[mission_runner] Pause flag detected. Halting task for '{name}'...", flush=True)
                        nav.cancelTask()
                        while _os.path.exists(PAUSE_FLAG_FILE):
                            if _stop_requested[0]:
                                break
                            time.sleep(0.3)
                        if _stop_requested[0]:
                            break
                        print(f"\n[mission_runner] Resumed! Resending navigation goal to '{name}'...", flush=True)
                        nav.goToPose(pose)

                    feedback = nav.getFeedback()
                    if feedback:
                        cp = feedback.current_pose.pose
                        print(
                            f"  [{name}] x={cp.position.x:.2f}  y={cp.position.y:.2f}",
                            end="\r", flush=True
                        )
                    time.sleep(0.2)

                if _stop_requested[0]:
                    break

                result = nav.getResult()
                if result == TaskResult.SUCCEEDED:
                    print(f"\n[mission_runner] ✓ Reached '{name}'", flush=True)
                    delay = wp.get("delay", 0)
                    if delay and delay > 0:
                        print(f"[mission_runner] ⏳ Waiting {delay}s at '{name}'...", flush=True)
                        t_start = time.time()
                        while time.time() - t_start < delay:
                            if _stop_requested[0]:
                                break
                            if _os.path.exists(PAUSE_FLAG_FILE):
                                print("\n[mission_runner] Pause flag detected during station wait. Sleeping...", flush=True)
                                while _os.path.exists(PAUSE_FLAG_FILE):
                                    if _stop_requested[0]:
                                        break
                                    time.sleep(0.3)
                                t_start = time.time()  # Reset wait time after resume
                                print("[mission_runner] Resumed station wait.", flush=True)
                            time.sleep(0.1)
                elif result == TaskResult.CANCELED:
                    print(f"\n[mission_runner] Mission canceled — Nav2 stack stays active.", flush=True)
                    try:
                        rclpy.shutdown()
                    except Exception:
                        pass
                    return
                elif result == TaskResult.FAILED:
                    print(f"\n[mission_runner] ✗ Failed to reach '{name}', skipping.", flush=True)

    except KeyboardInterrupt:
        print("\n[mission_runner] Stopped by user.", flush=True)
        try:
            nav.cancelTask()
        except Exception:
            pass

    try:
        rclpy.shutdown()
    except Exception:
        pass


if __name__ == "__main__":
    main()
