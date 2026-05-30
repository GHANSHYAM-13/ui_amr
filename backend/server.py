from flask import Flask, request, jsonify, send_from_directory, send_file
from flask_cors import CORS
import subprocess
import os
import signal
import time
import math
import json
import atexit
from zone_handler import init_zone_handler, get_zone_handler

# server.py lives in amr_ui/backend/ — root of the package is one level up
BACKEND_DIR     = os.path.dirname(os.path.abspath(__file__))
BASE_DIR        = os.path.dirname(BACKEND_DIR)   # amr_ui/
UI_FOLDER       = BASE_DIR
MAP_FOLDER      = os.path.join(BASE_DIR, "maps")
MISSION_FOLDER  = os.path.join(BASE_DIR, "missions")
MISSION_FILE    = os.path.join(MISSION_FOLDER, "mission.json")
RUNNER_SCRIPT   = os.path.join(BACKEND_DIR, "mission_runner.py")
PROGRAMS_FOLDER = os.path.join(BASE_DIR, "programs")

os.makedirs(MAP_FOLDER,      exist_ok=True)
os.makedirs(MISSION_FOLDER,  exist_ok=True)
os.makedirs(PROGRAMS_FOLDER, exist_ok=True)

# Initialize zone handler
_zone_handler = init_zone_handler(MAP_FOLDER)

import json as _json

# ── Load robot_config.js as Python config ────────────────────────────────────
import json as _json, re as _re

def _load_robot_config():
    cfg_path = os.path.join(BASE_DIR, "config", "robot_config.js")
    try:
        with open(cfg_path) as f:
            src = f.read()
        # Strip JS block and line comments
        src = _re.sub(r'/\*.*?\*/', '', src, flags=_re.DOTALL)
        src = _re.sub(r'//[^\n]*', '', src)
        # Extract object between first { and the matching }
        brace = src.find('var RobotConfig')
        src = src[brace:]
        depth = 0; start = src.index('{'); i = start
        for i, ch in enumerate(src[start:], start):
            if ch == '{': depth += 1
            elif ch == '}':
                depth -= 1
                if depth == 0: break
        obj_str = src[start:i+1]
        # Quote unquoted JS keys
        obj_str = _re.sub(r'([\{,])\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*:', r'\1"\2":', obj_str)
        # Remove trailing commas
        obj_str = _re.sub(r',(\s*[\}\]])', r'\1', obj_str)
        return _json.loads(obj_str)
    except Exception as e:
        print(f"[robot_config] Could not parse robot_config.js ({e}) — using defaults")
        return {
            "mode": "real", "use_sim_time": False,
            "topics": {"map": "/map"},
            "launch": {
                "slam":         {"package": "slam_toolbox", "file": "online_async_launch.py", "extra": []},
                "localization": {"package": "nav2_bringup", "file": "localization_launch.py", "extra": []},
                "navigation":   {"package": "nav2_bringup", "file": "navigation_launch.py",   "extra": []}
            }
        }

_cfg          = _load_robot_config()
USE_SIM_TIME  = str(_cfg.get("use_sim_time", False)).lower()
SLAM_PKG      = _cfg["launch"]["slam"]["package"]
SLAM_LAUNCH   = _cfg["launch"]["slam"]["file"]
SLAM_EXTRA    = _cfg["launch"]["slam"].get("extra", [])
LOC_PKG       = _cfg["launch"]["localization"]["package"]
LOC_LAUNCH    = _cfg["launch"]["localization"]["file"]
LOC_EXTRA     = _cfg["launch"]["localization"].get("extra", [])
NAV_PKG       = _cfg["launch"]["navigation"]["package"]
NAV_LAUNCH    = _cfg["launch"]["navigation"]["file"]
NAV_EXTRA     = _cfg["launch"]["navigation"].get("extra", [])
MAP_TOPIC     = _cfg.get("topics", {}).get("map", "/map")
print(f"[robot_config] mode={_cfg.get('mode')} use_sim_time={USE_SIM_TIME} slam={SLAM_PKG}/{SLAM_LAUNCH}")



# ── ROS2 shared context ──────────────────────────────────────────────────────
# rclpy.init() must be called ONCE per process.
# We init here at import time, then all nodes (map listener, delivery, etc.)
# share this context via a single MultiThreadedExecutor.
import threading
import rclpy
from rclpy.node import Node
from rclpy.executors import MultiThreadedExecutor
from rclpy.action import ActionClient
from nav_msgs.msg import OccupancyGrid
from geometry_msgs.msg import PoseWithCovarianceStamped
from nav2_msgs.action import NavigateToPose
from action_msgs.msg import GoalStatus
from rclpy.qos import QoSProfile, ReliabilityPolicy, DurabilityPolicy, HistoryPolicy

rclpy.init()

# QoS that matches nav2 map server (transient-local, reliable, depth 1)
_MAP_QOS = QoSProfile(
    reliability  = ReliabilityPolicy.RELIABLE,
    durability   = DurabilityPolicy.TRANSIENT_LOCAL,
    history      = HistoryPolicy.KEEP_LAST,
    depth        = 1
)

_latest_map  = None
_latest_pose = None
_map_lock    = threading.Lock()
_pose_lock   = threading.Lock()
_ros_executor = MultiThreadedExecutor(num_threads=4)

class _MapListenerNode(Node):
    def __init__(self):
        super().__init__("flask_map_listener")
        self.create_subscription(OccupancyGrid, MAP_TOPIC, self._cb, _MAP_QOS)
        self.get_logger().info(f"Subscribed to {MAP_TOPIC}")

    _seq = 0

    def _cb(self, msg):
        global _latest_map
        info = msg.info
        _MapListenerNode._seq += 1
        with _map_lock:
            _latest_map = {
                "width":      info.width,
                "height":     info.height,
                "resolution": info.resolution,
                "origin": {
                    "x": info.origin.position.x,
                    "y": info.origin.position.y,
                    "yaw": 0.0
                },
                "seq":  _MapListenerNode._seq,
                "data": list(msg.data)
            }

_map_node = _MapListenerNode()
_ros_executor.add_node(_map_node)


class _PoseListenerNode(Node):
    def __init__(self):
        super().__init__("flask_pose_listener")
        transient_qos = QoSProfile(
            reliability=ReliabilityPolicy.RELIABLE,
            durability=DurabilityPolicy.TRANSIENT_LOCAL,
            history=HistoryPolicy.KEEP_LAST,
            depth=1
        )
        volatile_qos = QoSProfile(
            reliability=ReliabilityPolicy.RELIABLE,
            durability=DurabilityPolicy.VOLATILE,
            history=HistoryPolicy.KEEP_LAST,
            depth=10
        )
        topic = _cfg.get("topics", {}).get("amcl_pose", "/amcl_pose")
        self.create_subscription(PoseWithCovarianceStamped, topic, self._cb, transient_qos)
        self.create_subscription(PoseWithCovarianceStamped, topic, self._cb, volatile_qos)
        self.get_logger().info(f"Subscribed to {topic}")

    def _cb(self, msg):
        global _latest_pose
        p = msg.pose.pose.position
        q = msg.pose.pose.orientation
        yaw = math.atan2(2*(q.w*q.z + q.x*q.y), 1 - 2*(q.y*q.y + q.z*q.z))
        stamp = msg.header.stamp.sec + msg.header.stamp.nanosec * 1e-9
        with _pose_lock:
            _latest_pose = {
                "x": p.x,
                "y": p.y,
                "yaw": yaw,
                "stamp": stamp,
                "received_at": time.time(),
            }


_pose_node = _PoseListenerNode()
_ros_executor.add_node(_pose_node)


class _NavGoalNode(Node):
    """Own browser-sent Nav2 goals inside the Flask process.

    Starting goals with `ros2 action send_goal` creates a short-lived action
    client process. Keeping the action client here makes a browser refresh or
    remote frontend disconnect irrelevant to the active robot goal.
    """

    _ACTIVE_STATUSES = {
        GoalStatus.STATUS_ACCEPTED,
        GoalStatus.STATUS_EXECUTING,
        GoalStatus.STATUS_CANCELING,
    }

    def __init__(self):
        super().__init__("flask_nav_goal_client")
        self._client = ActionClient(self, NavigateToPose, "/navigate_to_pose")
        self._lock = threading.Lock()
        self._goal_handle = None
        self._status = GoalStatus.STATUS_UNKNOWN
        self._last_goal = None
        self._last_error = ""

    def send_goal(self, x, y, yaw):
        qz = math.sin(yaw / 2.0)
        qw = math.cos(yaw / 2.0)

        goal = NavigateToPose.Goal()
        goal.pose.header.frame_id = "map"
        goal.pose.header.stamp = self.get_clock().now().to_msg()
        goal.pose.pose.position.x = float(x)
        goal.pose.pose.position.y = float(y)
        goal.pose.pose.position.z = 0.0
        goal.pose.pose.orientation.x = 0.0
        goal.pose.pose.orientation.y = 0.0
        goal.pose.pose.orientation.z = qz
        goal.pose.pose.orientation.w = qw

        with self._lock:
            self._status = GoalStatus.STATUS_ACCEPTED
            self._last_goal = {"x": float(x), "y": float(y), "yaw": float(yaw)}
            self._last_error = ""

        if not self._client.wait_for_server(timeout_sec=2.0):
            with self._lock:
                self._status = GoalStatus.STATUS_UNKNOWN
                self._last_error = "Nav2 action server unavailable"
            raise RuntimeError(self._last_error)

        future = self._client.send_goal_async(goal)
        future.add_done_callback(self._goal_response_cb)

    def _goal_response_cb(self, future):
        try:
            goal_handle = future.result()
            if not goal_handle.accepted:
                with self._lock:
                    self._goal_handle = None
                    self._status = GoalStatus.STATUS_ABORTED
                    self._last_error = "Goal rejected"
                return
            with self._lock:
                self._goal_handle = goal_handle
                self._status = GoalStatus.STATUS_EXECUTING
                self._last_error = ""
            goal_handle.get_result_async().add_done_callback(self._result_cb)
        except Exception as e:
            with self._lock:
                self._goal_handle = None
                self._status = GoalStatus.STATUS_UNKNOWN
                self._last_error = str(e)

    def _result_cb(self, future):
        try:
            status = future.result().status
        except Exception as e:
            status = GoalStatus.STATUS_UNKNOWN
            with self._lock:
                self._last_error = str(e)
        with self._lock:
            self._goal_handle = None
            self._status = status

    def cancel_goal(self):
        with self._lock:
            goal_handle = self._goal_handle
            if goal_handle is not None:
                self._status = GoalStatus.STATUS_CANCELING
        if goal_handle is not None:
            goal_handle.cancel_goal_async()

    def status(self):
        with self._lock:
            status = self._status
            return {
                "active": status in self._ACTIVE_STATUSES,
                "status": int(status),
                "goal": self._last_goal,
                "error": self._last_error,
            }

    def has_active_goal(self):
        return self.status()["active"]


_nav_goal_node = _NavGoalNode()
_ros_executor.add_node(_nav_goal_node)

def _ros_spin_thread():
    try:
        _ros_executor.spin()
    except Exception as e:
        print(f"[ros_executor] stopped: {e}")

threading.Thread(target=_ros_spin_thread, daemon=True).start()
print(f"[ros] Executor started — listening on {MAP_TOPIC}")

app = Flask(__name__, static_folder=UI_FOLDER, static_url_path="")
CORS(app)


# ── Serve UI ──────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return send_file(os.path.join(UI_FOLDER, "index.html"))

slam_process         = None
localization_process = None
navigation_process   = None
mission_process      = None   # mission_runner.py subprocess
active_map_yaml      = None
_localization_start_time = 0

# Mission runtime metadata (populated on start, cleared on stop)
mission_meta = {
    "cycles": -1,
    "total_wps": 0,
    "map": "",
    "start_time": 0,
}


def kill_process(proc):
    if not proc:
        return
    if proc.poll() is not None:
        return
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except Exception:
        pass
    # Wait up to 3 seconds for clean exit
    start = time.time()
    while time.time() - start < 3.0:
        if proc.poll() is not None:
            return
        time.sleep(0.1)
    # Force kill if still running
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        proc.wait(timeout=1.0)
    except Exception:
        pass


def wait_for_no_processes_matching(pattern, timeout=3.0):
    start = time.time()
    while time.time() - start < timeout:
        try:
            result = subprocess.run(["pgrep", "-f", pattern], capture_output=True, text=True)
            if not result.stdout.strip():
                return True
        except Exception:
            return True
        time.sleep(0.15)
    return False


def kill_nav_nodes():
    os.system("pkill -9 -f map_server 2>/dev/null")
    os.system("pkill -9 -f amcl 2>/dev/null")
    os.system("pkill -9 -f lifecycle_manager 2>/dev/null")
    os.system("pkill -9 -f slam_toolbox 2>/dev/null")
    os.system("pkill -9 -f nav2 2>/dev/null")
    os.system("pkill -9 -f bt_navigator 2>/dev/null")
    os.system("pkill -9 -f planner_server 2>/dev/null")
    os.system("pkill -9 -f controller_server 2>/dev/null")
    os.system("pkill -9 -f waypoint_follower 2>/dev/null")
    wait_for_no_processes_matching("map_server", 1.5)
    wait_for_no_processes_matching("amcl", 1.5)
    wait_for_no_processes_matching("lifecycle_manager", 1.5)


def cleanup_all():
    global slam_process, localization_process, navigation_process, mission_process, active_map_yaml
    kill_process(slam_process)
    kill_process(localization_process)
    kill_process(navigation_process)
    kill_process(mission_process)
    kill_nav_nodes()
    active_map_yaml = None


def cleanup_loc_only():
    """Kill only the localization/nav stack — never the mission_runner."""
    global slam_process, localization_process, navigation_process, active_map_yaml
    kill_process(slam_process)
    kill_process(localization_process)
    kill_process(navigation_process)
    slam_process         = None
    localization_process = None
    navigation_process   = None
    active_map_yaml      = None
    os.system("pkill -9 -f map_server 2>/dev/null")
    os.system("pkill -9 -f 'ros2.*amcl' 2>/dev/null")
    wait_for_no_processes_matching("map_server", 1.5)
    wait_for_no_processes_matching("amcl", 1.5)

atexit.register(cleanup_all)


# ── Map routes ──────────────────────────────────────────────────────────────

@app.route("/map/save_edit", methods=["POST"])
def saveMapEdit():
    """Save an edited map.

    Accepts JSON: { "name": "warehouse_a.pgm", "png": "<base64 PNG data>" }

    The PNG pixels are converted back to ROS OccupancyGrid conventions:
      black  (R < 50)              → 0   (occupied / wall)
      white  (R > 200)             → 254 (free / floor)
      mid-grey (everything else)   → 205 (unknown)

    The result is written back to the original .pgm file.
    The .yaml sidecar is left untouched (resolution/origin are unchanged).
    """
    import base64 as _b64
    import io as _io
    data = request.json
    pgm_name = data.get("name", "")
    png_b64  = data.get("png",  "")

    if not pgm_name or not png_b64:
        return jsonify({"status": "error", "message": "Missing name or png"}), 400

    pgm_path = os.path.join(MAP_FOLDER, pgm_name)
    if not os.path.isfile(pgm_path):
        return jsonify({"status": "error", "message": "Map file not found: " + pgm_name}), 404

    try:
        from PIL import Image as _PILImage
        raw = _b64.b64decode(png_b64)
        img = _PILImage.open(_io.BytesIO(raw)).convert("RGB")
        w, h = img.size
        pixels_rgb = list(img.getdata())

        # Convert RGB pixels → OccupancyGrid PGM bytes
        # The PNG is stored top→bottom (row 0 = visual top = map max-y).
        # PGM is also stored top→bottom so no flip needed here.
        pgm_pixels = []
        for r, g, b in pixels_rgb:
            brightness = (r + g + b) // 3
            if brightness < 50:
                pgm_pixels.append(0)    # occupied (wall) → black in PGM
            elif brightness > 200:
                pgm_pixels.append(254)  # free (floor) → white in PGM
            else:
                pgm_pixels.append(205)  # unknown → grey in PGM

        # Write P5 (binary) PGM
        header = ("P5\n" + str(w) + " " + str(h) + "\n255\n").encode()
        with open(pgm_path, "wb") as f:
            f.write(header)
            f.write(bytes(pgm_pixels))

        return jsonify({"status": "map_edit_saved", "file": pgm_name})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/maps")
def get_maps():
    maps = []
    # Sidecar patterns to exclude from the map selector:
    # mask files (_keepout_mask.pgm, _speed_mask.pgm, etc.)
    _EXCLUDE_SUFFIXES = ("_keepout_mask.pgm", "_speed_mask.pgm",
                         "_mask_no-go.pgm", "_mask_lane.pgm", "_mask_slow.pgm",
                         "_mask.pgm")
    for file in sorted(os.listdir(MAP_FOLDER)):
        if not file.endswith(".pgm"):
            continue
        lower = file.lower()
        if any(lower.endswith(s) for s in _EXCLUDE_SUFFIXES):
            continue
        maps.append(file)
    return jsonify(maps)


@app.route("/map/delete", methods=["POST"])
def deleteMap():
    """Delete a map and all its sidecar files (yaml, zones, masks)."""
    data = request.json or {}
    name = data.get("name", "")
    if not name:
        return jsonify({"status": "error", "message": "Missing map name"}), 400

    base = os.path.basename(name)
    for ext in (".yaml", ".yml", ".pgm", ".png", ".jpg", ".jpeg"):
        if base.lower().endswith(ext):
            base = base[:-len(ext)]
            break

    if not base:
        return jsonify({"status": "error", "message": "Invalid map name"}), 400

    deleted_files = []
    # Delete image & YAML files in MAP_FOLDER
    for ext in (".pgm", ".yaml", ".png", ".jpg", ".jpeg"):
        filepath = os.path.join(MAP_FOLDER, base + ext)
        if os.path.isfile(filepath):
            try:
                os.remove(filepath)
                deleted_files.append(os.path.basename(filepath))
            except Exception as e:
                print(f"[deleteMap] Error deleting {filepath}: {e}")

    # Delete zones file and mask files via zone_handler naming conventions
    try:
        from zone_handler import get_zone_handler
        zh = get_zone_handler()
        if zh:
            files_to_check = [
                zh.get_zones_file(base),
                zh.get_mask_file(base, "keepout"),
                zh.get_mask_file(base, "speed"),
                zh.get_mask_yaml_file(base, "keepout"),
                zh.get_mask_yaml_file(base, "speed")
            ]
            for filepath in files_to_check:
                if os.path.isfile(filepath):
                    try:
                        os.remove(filepath)
                        deleted_files.append(os.path.basename(filepath))
                    except Exception as e:
                        print(f"[deleteMap] Error deleting filter/zone file {filepath}: {e}")
    except Exception as e:
        print(f"[deleteMap] Error deleting zone artifacts: {e}")

    return jsonify({"status": "map_deleted", "deleted_files": deleted_files})


@app.route("/map_image/<path:name>")
def get_map_image(name):
    from flask import Response
    import io
    base = name
    for ext in (".yaml", ".yml"):
        if base.lower().endswith(ext):
            base = base[:-len(ext)]
            break
    has_ext = "." in os.path.basename(base)
    candidates = [base] if has_ext else [
        base + ".pgm", base + ".png", base + ".jpg", base + ".jpeg"
    ]
    filepath, found_name = None, None
    for c in candidates:
        fp = os.path.join(MAP_FOLDER, c)
        if os.path.isfile(fp):
            filepath, found_name = fp, c
            break
    if not filepath:
        return jsonify({"error": "map image not found: " + name}), 404
    if found_name.lower().endswith((".png", ".jpg", ".jpeg")):
        resp = send_from_directory(MAP_FOLDER, found_name)
        resp.headers["Access-Control-Allow-Origin"] = "*"
        return resp
    try:
        from PIL import Image
        img = Image.open(filepath)
        if img.mode not in ("RGB", "RGBA"):
            img = img.convert("RGB")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)
        resp = Response(buf.read(), mimetype="image/png")
        resp.headers["Access-Control-Allow-Origin"] = "*"
        return resp
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/map_metadata/<path:name>")
def getMapMetadata(name):
    """Return map geometry needed by browser-side zone drawing."""
    try:
        base = os.path.basename(name)
        for ext in (".yaml", ".yml", ".pgm", ".png", ".jpg", ".jpeg"):
            if base.lower().endswith(ext):
                base = base[:-len(ext)]
                break
        meta = _zone_handler._read_map_yaml(base)
        pgm_path = os.path.join(MAP_FOLDER, meta["image"])
        width, height = _zone_handler._read_pgm_size(pgm_path)
        return jsonify({
            "name": base,
            "image": meta["image"],
            "resolution": meta["resolution"],
            "origin": meta["origin"],
            "width": width,
            "height": height,
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 404



# ── Mapping routes ───────────────────────────────────────────────────────────

@app.route("/start_mapping", methods=["POST"])
def startMapping():
    global slam_process, _latest_map
    cleanup_all()
    # Clear cached map so new mapping session starts fresh (not stale localization map)
    with _map_lock:
        _latest_map = None
    slam_process = subprocess.Popen(
        [f"ros2", "launch", SLAM_PKG, SLAM_LAUNCH,
         f"use_sim_time:={USE_SIM_TIME}"] + SLAM_EXTRA,
        preexec_fn=os.setsid
    )
    return jsonify({"status": "mapping_started"})


@app.route("/stop_mapping", methods=["POST"])
def stopMapping():
    global slam_process
    kill_process(slam_process)
    kill_nav_nodes()
    return jsonify({"status": "mapping_stopped"})


# ── Robot bringup ─────────────────────────────────────────────────────────────
robot_process = None

@app.route("/start_robot", methods=["POST"])
def startRobot():
    global robot_process
    if robot_process and robot_process.poll() is None:
        return jsonify({"status": "already_running"})
    robot_process = subprocess.Popen(
        ["ros2", "launch", "robot_bringup", "robomuse_launch.py"],
        preexec_fn=os.setsid
    )
    return jsonify({"status": "robot_started"})

@app.route("/stop_robot", methods=["POST"])
def stopRobot():
    global robot_process, slam_process, localization_process, navigation_process
    # 1. Kill the bringup launch process group (kills all children including lidar drivers)
    kill_process(robot_process)
    robot_process = None
    # 2. Kill by process name to catch any orphaned nodes from the bringup
    for pattern in [
        "robomuse_launch", "robot_bringup",
        "ira_laser_tools", "laser_merger", "merged_laser",   # lidar merger
        "ldlidar", "rplidar", "urg_node", "laser_scan",       # common lidar drivers
        "robot_state_publisher", "joint_state_publisher",     # robot description
    ]:
        try:
            subprocess.Popen(["pkill", "-9", "-f", pattern], preexec_fn=os.setsid)
        except Exception:
            pass
    # 3. Also kill nav/slam if running
    try:
        kill_nav_nodes()
        kill_process(slam_process);         slam_process         = None
        kill_process(localization_process); localization_process = None
        kill_process(navigation_process);   navigation_process   = None
    except Exception:
        pass
    # 4. Wait for bringup launch nodes and common lidar driver components to be fully terminated
    wait_for_no_processes_matching("robot_bringup", 1.5)
    wait_for_no_processes_matching("ldlidar|rplidar|urg_node|merged_laser", 1.5)
    return jsonify({"status": "robot_stopped"})

@app.route("/robot_status")
def robotStatus():
    global robot_process
    running = robot_process is not None and robot_process.poll() is None
    return jsonify({"running": running})


@app.route("/save_map", methods=["POST"])
def saveMap():
    data     = request.json
    name     = data["name"]
    out_path = os.path.join(MAP_FOLDER, name)

    # ── Strategy 1: save from cached map data in memory (always works) ──────
    with _map_lock:
        cached = _latest_map

    if cached:
        try:
            w   = cached["width"]
            h   = cached["height"]
            res = cached["resolution"]
            ox  = cached["origin"]["x"]
            oy  = cached["origin"]["y"]
            dat = cached["data"]

            # Build PGM image (map_saver_cli convention):
            #   PGM is stored top-to-bottom visually.
            #   map_saver_cli writes: PGM row 0 = TOP of map (highest y).
            #   OccupancyGrid row 0 = BOTTOM of map (y=origin_y).
            #   So we must VERTICALLY FLIP: write rows in reverse order.
            # Pixel values: 205=unknown, 254=free, 0=occupied
            converted = []
            for v in dat:
                if v == -1:   converted.append(205)
                elif v == 0:  converted.append(254)
                else:         converted.append(0)

            # Flip rows: write row h-1 first (map top), row 0 last (map bottom)
            pixels = []
            for row in range(h - 1, -1, -1):
                pixels.extend(converted[row * w : row * w + w])

            pgm_path  = out_path + ".pgm"
            yaml_path = out_path + ".yaml"

            # Write binary PGM (P5)
            with open(pgm_path, "wb") as f:
                header = "P5\n" + str(w) + " " + str(h) + "\n255\n"
                f.write(header.encode())
                f.write(bytes(pixels))

            # Write YAML
            yaml_content = (
                "image: " + os.path.basename(pgm_path) + "\n"
                "resolution: " + str(res) + "\n"
                "origin: [" + str(ox) + ", " + str(oy) + ", 0.0]\n"
                "negate: 0\n"
                "occupied_thresh: 0.65\n"
                "free_thresh: 0.25\n"
            )
            with open(yaml_path, "w") as f:
                f.write(yaml_content)

            app.logger.info(f"Map saved from cache: {yaml_path}")
            return jsonify({"status": "map_saved", "method": "cache", "path": yaml_path})

        except Exception as e:
            app.logger.error(f"Cache save failed: {e}, trying map_saver_cli")

    # ── Strategy 2: fallback — use map_saver_cli (requires /map topic live) ──
    try:
        result = subprocess.run([
            "ros2", "run", "nav2_map_server", "map_saver_cli",
            "-f", out_path, "--timeout-sec", "8",
            "--ros-args", "-p", f"use_sim_time:={USE_SIM_TIME}"
        ], timeout=12, capture_output=True, text=True)
        if result.returncode == 0:
            return jsonify({"status": "map_saved", "method": "map_saver_cli"})
        else:
            return jsonify({"status": "error", "message": result.stderr}), 500
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/map_data")
def mapData():
    """Returns full map JSON (used by save_map). For display use /map_tile."""
    with _map_lock:
        if _latest_map is None:
            return jsonify({"status": "no_map"}), 204
        return jsonify(_latest_map)


@app.route("/map_meta")
def mapMeta():
    """Lightweight poll endpoint — returns metadata + checksum only.
    JS polls this at 1Hz; fetches /map_tile only when seq changes."""
    with _map_lock:
        if _latest_map is None:
            return jsonify({"status": "no_map"}), 204
        m = _latest_map
        return jsonify({
            "status":     "ok",
            "width":      m["width"],
            "height":     m["height"],
            "resolution": m["resolution"],
            "origin":     m["origin"],
            "seq":        m.get("seq", 0)
        })


@app.route("/map_tile")
def mapTile():
    """Serves the current live map as a PNG image (RViz colour scheme).
    Called by JS only when seq has changed."""
    import io
    with _map_lock:
        if _latest_map is None:
            return jsonify({"status": "no_map"}), 204
        m      = _latest_map
        w      = m["width"]
        h      = m["height"]
        dat    = m["data"]

    # Build RGBA pixels exactly matching RViz OccupancyGrid display:
    #   -1   → unknown   → 127,127,127 (mid grey, same as RViz)
    #   0    → free      → 255,255,255 (pure white, same as RViz)
    #   1-99 → inflation → scaled grey (darker = more occupied)
    #   100  → occupied  → 0,0,0 (black, same as RViz)
    rgba = bytearray(w * h * 4)
    for i, v in enumerate(dat):
        base = i * 4
        if v < 0:           # unknown → mid grey (RViz)
            rgba[base]=127; rgba[base+1]=127; rgba[base+2]=127; rgba[base+3]=255
        elif v == 0:        # free → white (RViz)
            rgba[base]=255; rgba[base+1]=255; rgba[base+2]=255; rgba[base+3]=255
        elif v >= 100:      # fully occupied → black (RViz)
            rgba[base]=0;   rgba[base+1]=0;   rgba[base+2]=0;   rgba[base+3]=255
        else:               # partially occupied → interpolate white→black
            g = max(0, int(255 * (1.0 - v / 100.0)))
            rgba[base]=g; rgba[base+1]=g; rgba[base+2]=g; rgba[base+3]=255

    # Encode as PNG using zlib-compressed raw RGBA
    try:
        from PIL import Image
        img = Image.frombytes("RGBA", (w, h), bytes(rgba))
        # ROS OccupancyGrid: data[0] = bottom-left cell (y = origin_y)
        # PIL stores top-to-bottom, so PIL row 0 = ROS bottom row
        # EaselJS scaleY<0 handles the flip correctly when bmp.scaleY = +res
        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=False, compress_level=1)
        buf.seek(0)
        from flask import Response
        resp = Response(buf.read(), mimetype="image/png")
        resp.headers["Cache-Control"] = "no-store"
        resp.headers["Access-Control-Allow-Origin"] = "*"
        return resp
    except ImportError:
        # PIL not available — send raw PGM
        pgm = ("P5\n" + str(w) + " " + str(h) + "\n255\n").encode()
        pix = bytearray(w * h)
        for i, v in enumerate(dat):
            if v == -1:   pix[i] = 127
            elif v == 0:  pix[i] = 255
            else:         pix[i] = max(0, 255 - int(v * 2.55))
        from flask import Response
        resp = Response(pgm + bytes(pix), mimetype="image/x-portable-graymap")
        resp.headers["Access-Control-Allow-Origin"] = "*"
        return resp


@app.route("/ros_status")
def rosStatus():
    """Debug endpoint — shows ROS node health and map subscription status."""
    with _map_lock:
        has_map = _latest_map is not None
        map_info = {
            "width":  _latest_map["width"]  if has_map else 0,
            "height": _latest_map["height"] if has_map else 0,
        } if has_map else {}
    return jsonify({
        "rclpy_ok":    rclpy.ok(),
        "map_topic":   MAP_TOPIC,
        "map_received": has_map,
        "map_info":    map_info,
        "executor_nodes": len(_ros_executor._nodes) if hasattr(_ros_executor, '_nodes') else "unknown"
    })


# ── Localization + Navigation ─────────────────────────────────────────────────

@app.route("/stop_localization", methods=["POST"])
def stopLocalization():
    global localization_process, navigation_process, active_map_yaml
    kill_process(localization_process)
    kill_process(navigation_process)
    localization_process = None
    navigation_process   = None
    active_map_yaml      = None
    return jsonify({"status": "localization_stopped"})


@app.route("/start_localization", methods=["POST"])
def startLocalization():
    global localization_process, navigation_process, slam_process, active_map_yaml, _localization_start_time
    data     = request.json
    map_yaml = data["map"]
    # Normalize: frontend may send .pgm; bringup_launch.py expects .yaml
    for _ext in (".pgm", ".png", ".jpg", ".jpeg"):
        if map_yaml.lower().endswith(_ext):
            map_yaml = map_yaml[:-len(_ext)] + ".yaml"
            break
    map_path = os.path.join(MAP_FOLDER, map_yaml)
    if not os.path.isfile(map_path):
        return jsonify({"status": "error", "message": f"Map YAML not found: {map_yaml}"}), 404
    running = localization_process is not None and localization_process.poll() is None
    _zone_handler.prepare_map(map_yaml)

    # A browser refresh or weak network reconnect must not restart Nav2.
    # If localization is already up, keep the robot-side stack untouched and
    # let the frontend reattach to the existing pose/map streams.
    if running:
        _localization_start_time = time.time() - 3600
        return jsonify({
            "status": "localization_already_running",
            "navigation_active": True,
            "map": active_map_yaml or map_yaml
        })

    # Browser refresh should not tear down Nav2 while a server-owned
    # NavigateToPose goal is still executing.
    if _nav_goal_node.has_active_goal():
        _localization_start_time = time.time() - 3600
        return jsonify({
            "status": "localization_already_active_goal_running",
            "navigation_active": True,
            "map": active_map_yaml
        })

    # Use cleanup_loc_only — never kill mission_process.
    # If a mission is running and the user refreshes, the page calls
    # startLocalization again but the mission_runner must keep running.
    cleanup_loc_only()
    active_map_yaml = map_yaml
    _localization_start_time = time.time()

    ros_env = os.environ.copy()
    ros_env["ROS_DOMAIN_ID"] = ros_env.get("ROS_DOMAIN_ID", "0")

    def _launch_stack():
        """Use bringup_launch.py which starts AMCL + map_server + full Nav2
        in a single launch file with a single map argument. This is the
        standard, reliable way to start everything together."""
        global localization_process, navigation_process, active_map_yaml

        time.sleep(1)   # brief pause for old nodes to die

        # bringup_launch.py handles: map_server, AMCL, bt_navigator,
        # controller_server, planner_server, costmap nodes — everything.
        # Standard argument is map:=<yaml_path>
        # If user has a custom bringup, they can set it in robot_config.js
        bringup_pkg    = _cfg["launch"].get("bringup", {}).get("package", "nav2_bringup")
        bringup_launch = _cfg["launch"].get("bringup", {}).get("file",    "bringup_launch.py")
        bringup_extra  = list(_cfg["launch"].get("bringup", {}).get("extra", []))
        if not any(str(arg).startswith("params_file:=") for arg in bringup_extra):
            nav2_params_path = getattr(_zone_handler, "nav2_params_path", None)
            if nav2_params_path:
                bringup_extra.append(f"params_file:={nav2_params_path}")

        localization_process = subprocess.Popen(
            ["ros2", "launch", bringup_pkg, bringup_launch,
             f"map:={map_path}",
             f"use_sim_time:={USE_SIM_TIME}"] + bringup_extra,
            preexec_fn=os.setsid,
            env=ros_env
        )
        # navigation_process points to same process for cleanup compatibility
        navigation_process = localization_process
        active_map_yaml = map_yaml

    threading.Thread(target=_launch_stack, daemon=True).start()

    return jsonify({"status": "localization_and_navigation_starting"})


@app.route("/localization/status", methods=["GET"])
def localizationStatus():
    running = localization_process is not None and localization_process.poll() is None
    return jsonify({
        "running": running or _nav_goal_node.has_active_goal(),
        "map": active_map_yaml
    })


@app.route("/robot_pose", methods=["GET"])
def robotPose():
    with _pose_lock:
        pose = dict(_latest_pose) if _latest_pose is not None else None
    if pose is None:
        return jsonify({"status": "no_pose"}), 204
    pose["status"] = "ok"
    return jsonify(pose)


@app.route("/amcl/ready", methods=["GET"])
def amclReady():
    """Returns {ready: true} once AMCL has published at least one /amcl_pose.
    The frontend polls this after startLocalization to know when it is safe
    to republish the saved initialpose (publishing too early is ignored by AMCL
    because the lifecycle nodes are not yet active)."""
    global _latest_pose, _localization_start_time
    ready = False
    with _pose_lock:
        pose = _latest_pose
    if pose is not None and _localization_start_time is not None:
        # Verify if the pose received timestamp is newer than our localization start time
        ready = (pose.get("received_at", 0) > _localization_start_time)
    return jsonify({"ready": ready})


@app.route("/navigate_to_pose", methods=["POST"])
def navigateToPose():
    data = request.json
    x    = float(data["x"])
    y    = float(data["y"])
    yaw  = float(data["yaw"])

    try:
        _nav_goal_node.send_goal(x, y, yaw)
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 503
    return jsonify({"status": "goal_sent", "x": x, "y": y, "yaw": yaw})


@app.route("/navigation/status", methods=["GET"])
def navigationStatus():
    return jsonify(_nav_goal_node.status())


# ── Mission routes ────────────────────────────────────────────────────────────

@app.route("/mission", methods=["GET"])
def getMission():
    """Return current waypoints from mission.json (empty list if none)."""
    if not os.path.exists(MISSION_FILE):
        return jsonify({"waypoints": []})
    with open(MISSION_FILE, "r") as f:
        return jsonify(json.load(f))


@app.route("/mission/save", methods=["POST"])
def saveMission():
    """Save the full waypoints list sent from the frontend."""
    data = request.get_json(silent=True) or {}
    waypoints = data.get("waypoints", [])
    if not isinstance(waypoints, list):
        return jsonify({"status": "error", "message": "waypoints must be a list"}), 400
    os.makedirs(MISSION_FOLDER, exist_ok=True)
    tmp_file = MISSION_FILE + ".tmp"
    payload = {"waypoints": waypoints}
    with open(tmp_file, "w") as f:
        json.dump(payload, f, indent=2)
    os.replace(tmp_file, MISSION_FILE)
    return jsonify({"status": "mission_saved", "count": len(waypoints), "path": MISSION_FILE})


@app.route("/mission/save_named", methods=["POST"])
def saveNamedMission():
    """Save a mission with a custom name."""
    data = request.get_json(silent=True) or {}
    name = data.get("name", "").strip()
    if not name:
        return jsonify({"status": "error", "message": "Name is required"}), 400
    safe_name = "".join([c for c in name if c.isalpha() or c.isdigit() or c in '._-']).strip()
    if not safe_name:
        return jsonify({"status": "error", "message": "Invalid name"}), 400
    if not safe_name.endswith(".json"):
        safe_name += ".json"
    
    waypoints = data.get("waypoints", [])
    if not isinstance(waypoints, list) or not waypoints:
        return jsonify({"status": "error", "message": "No waypoints to save"}), 400
    os.makedirs(MISSION_FOLDER, exist_ok=True)
    file_path = os.path.join(MISSION_FOLDER, safe_name)
    tmp_file = file_path + ".tmp"
    with open(tmp_file, "w") as f:
        json.dump({"waypoints": waypoints}, f, indent=2)
    os.replace(tmp_file, file_path)
    return jsonify({"status": "success", "name": safe_name[:-5]})


@app.route("/missions/list", methods=["GET"])
def listMissions():
    """List all saved named missions on disk."""
    missions = []
    if os.path.exists(MISSION_FOLDER):
        for file in os.listdir(MISSION_FOLDER):
            if file.endswith(".json") and file not in ["mission_history.json", "mission.json"]:
                file_path = os.path.join(MISSION_FOLDER, file)
                try:
                    with open(file_path, "r") as f:
                        content = json.load(f)
                        missions.append({
                            "name": file[:-5],
                            "waypoints": content.get("waypoints", [])
                        })
                except Exception:
                    pass
    return jsonify(missions)


@app.route("/mission/load_named", methods=["POST"])
def loadNamedMission():
    """Load a named mission into the main mission.json file."""
    data = request.json
    name = data.get("name", "").strip()
    if not name.endswith(".json"):
        name += ".json"
    file_path = os.path.join(MISSION_FOLDER, name)
    if not os.path.exists(file_path):
        return jsonify({"status": "error", "message": "Mission not found"}), 404
    
    with open(file_path, "r") as src, open(MISSION_FILE, "w") as dest:
        content = json.load(src)
        json.dump(content, dest, indent=2)
    return jsonify({"status": "success", "waypoints": content.get("waypoints", [])})


# Pause flag and History paths
PAUSE_FLAG_FILE = os.path.join(MISSION_FOLDER, "pause.flag")
HISTORY_FILE    = os.path.join(MISSION_FOLDER, "mission_history.json")

def _add_mission_history(map_name, waypoints, cycles):
    try:
        history = []
        if os.path.exists(HISTORY_FILE):
            with open(HISTORY_FILE, "r") as f:
                history = json.load(f)
        
        # Add new entry at top
        new_entry = {
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "map": map_name or "Active Map",
            "waypoints": waypoints,
            "cycles": cycles if cycles != -1 else "Infinite",
            "status": "Active"
        }
        history.insert(0, new_entry)
        # Limit to last 20 missions
        history = history[:20]
        with open(HISTORY_FILE, "w") as f:
            json.dump(history, f, indent=2)
    except Exception as e:
        print(f"[history] Error adding mission history: {e}")

def _update_mission_history_status(status):
    try:
        if not os.path.exists(HISTORY_FILE):
            return
        with open(HISTORY_FILE, "r") as f:
            history = json.load(f)
        if history:
            history[0]["status"] = status
            with open(HISTORY_FILE, "w") as f:
                json.dump(history, f, indent=2)
    except Exception as e:
        print(f"[history] Error updating mission history status: {e}")


@app.route("/mission/history", methods=["GET"])
def getMissionHistory():
    if not os.path.exists(HISTORY_FILE):
        return jsonify([])
    try:
        with open(HISTORY_FILE, "r") as f:
            return jsonify(json.load(f))
    except Exception:
        return jsonify([])


@app.route("/mission/start", methods=["POST"])
def startMission():
    """Launch mission_runner.py as a background process."""
    global mission_process, mission_meta

    # Stop any running mission first
    kill_process(mission_process)

    if not os.path.exists(MISSION_FILE):
        return jsonify({"status": "error", "message": "No mission file found"}), 400

    # Ensure pause flag is cleared on a fresh start
    if os.path.exists(PAUSE_FLAG_FILE):
        try:
            os.remove(PAUSE_FLAG_FILE)
        except Exception:
            pass

    # Clear progress file
    progress_file = os.path.join(MISSION_FOLDER, "mission_progress.json")
    try:
        with open(progress_file, "w") as f:
            json.dump({"cycle": 0, "waypoint": 0, "waypoint_name": ""}, f)
    except Exception:
        pass

    data = request.json or {}
    cycles = data.get("cycles", -1)

    # Parse waypoints for history log and meta
    waypoints = []
    total_wps = 0
    total_distance = 0.0
    try:
        with open(MISSION_FILE, "r") as f:
            m_data = json.load(f)
            waypoints_data = m_data.get("waypoints", [])
            waypoints = [w.get("name", "Station") for w in waypoints_data]
            total_wps = len(waypoints_data)
            # Calculate total cycle distance
            for i in range(1, len(waypoints_data)):
                x1 = float(waypoints_data[i-1].get("x", 0))
                y1 = float(waypoints_data[i-1].get("y", 0))
                x2 = float(waypoints_data[i].get("x", 0))
                y2 = float(waypoints_data[i].get("y", 0))
                total_distance += math.sqrt((x2 - x1)**2 + (y2 - y1)**2)
    except Exception:
        pass

    _add_mission_history(active_map_yaml, waypoints, cycles)

    import time as _time
    mission_meta = {
        "cycles": cycles,
        "total_wps": total_wps,
        "waypoint_names": waypoints,
        "map": active_map_yaml or "",
        "start_time": _time.time(),
        "total_distance": total_distance,
    }

    # ── Persist meta to disk so it survives server restarts ───────────────────
    meta_file = os.path.join(MISSION_FOLDER, "mission_meta.json")
    try:
        with open(meta_file, "w") as f:
            json.dump(mission_meta, f)
    except Exception:
        pass

    cmd = ["python3", RUNNER_SCRIPT]
    if cycles != -1:
        cmd += ["--cycles", str(cycles)]

    ros_env = os.environ.copy()
    ros_env["ROS_DOMAIN_ID"] = ros_env.get("ROS_DOMAIN_ID", "0")

    mission_process = subprocess.Popen(
        cmd,
        preexec_fn=os.setsid,
        env=ros_env
    )
    return jsonify({"status": "mission_started"})


@app.route("/dock", methods=["POST"])
def dockRobot():
    """Call manual_dock service on the auto_charging_node."""
    try:
        subprocess.Popen(
            ["ros2", "service", "call",
             "/auto_charging_node/manual_dock",
             "std_srvs/srv/Trigger", "{}"],
            preexec_fn=os.setsid
        )
    except Exception:
        pass
    return jsonify({"status": "docking"})


@app.route("/undock", methods=["POST"])
def undockRobot():
    """Call manual_undock service on the auto_charging_node."""
    try:
        subprocess.Popen(
            ["ros2", "service", "call",
             "/auto_charging_node/manual_undock",
             "std_srvs/srv/Trigger", "{}"],
            preexec_fn=os.setsid
        )
    except Exception:
        pass
    return jsonify({"status": "undocking"})


@app.route("/cancel_goal", methods=["POST"])
def cancelGoal():
    """Cancel all active Nav2 navigation goals.
    Uses the correct ROS2 CLI: ros2 action cancel /navigate_to_pose
    """
    _nav_goal_node.cancel_goal()
    try:
        subprocess.Popen(
            ["ros2", "action", "cancel", "/navigate_to_pose"],
            preexec_fn=os.setsid
        )
    except Exception:
        pass
    return jsonify({"status": "goal_cancelled"})


@app.route("/mission/pause", methods=["POST"])
def pauseMission():
    """Pause the running mission by writing a pause flag and throttling speed to zero (hold active goal)."""
    try:
        with open(PAUSE_FLAG_FILE, "w") as f:
            f.write("paused")
        _update_mission_history_status("Paused")
    except Exception as e:
        return jsonify({"status": "error", "message": f"Could not pause: {e}"}), 500

    # Publish zero speed limit (hold pose) without cancelling active Nav2 goal
    try:
        subprocess.Popen(
            ["ros2", "topic", "pub", "--once", "/speed_limit",
             "nav2_msgs/msg/SpeedLimit", "{speed_limit: 0.001, percentage: true}"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            preexec_fn=os.setsid
        )
    except Exception:
        pass

    return jsonify({"status": "mission_paused"})


@app.route("/mission/resume", methods=["POST"])
def resumeMission():
    """Resume the running mission by deleting the pause flag and releasing the speed limit."""
    if os.path.exists(PAUSE_FLAG_FILE):
        try:
            os.remove(PAUSE_FLAG_FILE)
            _update_mission_history_status("Active")
        except Exception as e:
            return jsonify({"status": "error", "message": f"Could not resume: {e}"}), 500

    # Restore full speed (cancel speed limit)
    try:
        subprocess.Popen(
            ["ros2", "topic", "pub", "--once", "/speed_limit",
             "nav2_msgs/msg/SpeedLimit", "{speed_limit: 0.0, percentage: false}"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            preexec_fn=os.setsid
        )
    except Exception:
        pass

    return jsonify({"status": "mission_resumed"})


@app.route("/mission/stop", methods=["POST"])
def stopMission():
    """Stop the running mission.
    Kills mission_runner immediately, releases any speed limit, and cancels Nav2 task.
    """
    global mission_process
    kill_process(mission_process)
    mission_process = None

    if os.path.exists(PAUSE_FLAG_FILE):
        try:
            os.remove(PAUSE_FLAG_FILE)
        except Exception:
            pass

    _update_mission_history_status("Stopped")

    # Release any speed limit and cancel navigation goal
    try:
        subprocess.Popen(
            ["ros2", "topic", "pub", "--once", "/speed_limit",
             "nav2_msgs/msg/SpeedLimit", "{speed_limit: 0.0, percentage: false}"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            preexec_fn=os.setsid
        )
        subprocess.Popen(
            ["ros2", "action", "cancel", "/navigate_to_pose"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            preexec_fn=os.setsid
        )
    except Exception:
        pass

    return jsonify({"status": "mission_stopped"})


@app.route("/mission/status", methods=["GET"])
def missionStatus():
    """Check mission runner status with full rich progress info."""
    global mission_process, mission_meta
    import time as _time
    running = mission_process is not None and mission_process.poll() is None
    paused = os.path.exists(PAUSE_FLAG_FILE)

    # If the process completed by itself, update history to Completed
    if not running and mission_process is not None:
        _update_mission_history_status("Completed")
        mission_process = None

    # ── Restore meta from disk if in-memory meta is stale (server restart) ────
    meta_file = os.path.join(MISSION_FOLDER, "mission_meta.json")
    if running and mission_meta.get("total_wps", 0) == 0:
        try:
            if os.path.exists(meta_file):
                with open(meta_file) as f:
                    mission_meta = json.load(f)
        except Exception:
            pass

    # Read live progress written by mission_runner.py
    progress = {"cycle": 0, "waypoint": 0, "waypoint_name": ""}
    progress_file = os.path.join(MISSION_FOLDER, "mission_progress.json")
    try:
        if os.path.exists(progress_file):
            with open(progress_file) as f:
                progress = json.load(f)
    except Exception:
        pass

    elapsed = 0
    if mission_meta.get("start_time"):
        elapsed = int(_time.time() - mission_meta["start_time"])

    return jsonify({
        "running": running,
        "paused": paused,
        "cycle": progress.get("cycle", 0),
        "waypoint": progress.get("waypoint", 0),
        "waypoint_name": progress.get("waypoint_name", ""),
        "total_cycles": mission_meta.get("cycles", -1),
        "total_wps": mission_meta.get("total_wps", 0),
        "waypoint_names": mission_meta.get("waypoint_names", []),
        "map": mission_meta.get("map", ""),
        "elapsed_seconds": elapsed,
        "total_distance": mission_meta.get("total_distance", 0.0),
    })


# ── Program Runner ────────────────────────────────────────────────────────────

program_process  = None
program_name     = None


@app.route("/programs")
def getPrograms():
    progs = []
    if os.path.isdir(PROGRAMS_FOLDER):
        for f in sorted(os.listdir(PROGRAMS_FOLDER)):
            if f.endswith(".py"):
                progs.append(f)
    return jsonify(progs)


@app.route("/run_program", methods=["POST"])
def runProgram():
    global program_process, program_name
    data = request.json
    prog = data.get("program", "")
    path = os.path.join(PROGRAMS_FOLDER, prog)
    if not os.path.isfile(path):
        return jsonify({"status": "error", "message": "File not found"}), 404
    if program_process is not None and program_process.poll() is None and program_name == prog:
        return jsonify({"status": "already_running", "program": program_name})
    kill_process(program_process)
    ros_env = os.environ.copy()
    ros_env["ROS_DOMAIN_ID"] = ros_env.get("ROS_DOMAIN_ID", "0")
    program_process = subprocess.Popen(
        ["python3", path],
        preexec_fn=os.setsid,
        env=ros_env
    )
    program_name = prog
    return jsonify({"status": "running", "program": prog})


@app.route("/stop_program", methods=["POST"])
def stopProgram():
    global program_process, program_name
    kill_process(program_process)
    program_process = None
    program_name = None
    # Navigate robot to home position after program stops
    home = _cfg.get("home", {})
    hx   = float(home.get("x", 0.0))
    hy   = float(home.get("y", 0.0))
    hyaw = float(home.get("yaw", 0.0))
    qz   = math.sin(hyaw / 2.0)
    qw   = math.cos(hyaw / 2.0)
    goal_yaml = (f"pose: {{header: {{frame_id: map}}, pose: {{"
                 f"position: {{x: {hx}, y: {hy}, z: 0.0}}, "
                 f"orientation: {{x: 0.0, y: 0.0, z: {qz}, w: {qw}}}}}}}")
    try:
        subprocess.Popen(["ros2", "action", "send_goal", "/navigate_to_pose",
                          "nav2_msgs/action/NavigateToPose", goal_yaml])
    except Exception:
        pass
    return jsonify({"status": "stopped", "going_home": True})


@app.route("/program/status", methods=["GET"])
def programStatus():
    global program_process, program_name
    running = program_process is not None and program_process.poll() is None
    if not running and program_process is not None:
        program_process = None
        program_name = None
    return jsonify({"running": running, "program": program_name if running else None})


# ── Shutdown ──────────────────────────────────────────────────────────────────

@app.route("/shutdown", methods=["POST"])
def shutdown():
    cleanup_all()
    os.kill(os.getpid(), signal.SIGTERM)
    return jsonify({"status": "server_shutdown"})


# ── Load Cell Weight (always-on, starts with robot bringup) ──────────────────

_weight_value      = 0.0
_weight_poll_timer = None

def _start_weight_polling():
    """Called once when server starts. Continuously reads /load_cell_data."""
    import re as _re, time as _time, threading as _th
    def _poll():
        global _weight_value
        ros_env = os.environ.copy()
        ros_env["ROS_DOMAIN_ID"] = ros_env.get("ROS_DOMAIN_ID", "0")
        while True:
            try:
                result = subprocess.run(
                    ["ros2", "topic", "echo", "--once", "/load_cell_data"],
                    capture_output=True, text=True, timeout=3, env=ros_env
                )
                m = _re.search(r"data:\s*([\-\d.]+)", result.stdout)
                if m:
                    _weight_value = float(m.group(1))
            except Exception:
                pass
            _time.sleep(0.8)
    _th.Thread(target=_poll, daemon=True).start()

# Start polling immediately when server loads
_start_weight_polling()


@app.route("/weight/status")
def weightStatus():
    return jsonify({"weight": _weight_value})


# ── C4i4 Delivery ─────────────────────────────────────────────────────────────

_delivery_launch_process = None
_delivery_weight         = 0.0
_delivery_state          = "IDLE"

def _delivery_weight_listener():
    import re as _re, time as _time
    global _delivery_weight, _delivery_launch_process
    ros_env = os.environ.copy()
    ros_env["ROS_DOMAIN_ID"] = ros_env.get("ROS_DOMAIN_ID", "0")
    while _delivery_launch_process is not None and _delivery_launch_process.poll() is None:
        try:
            echo = subprocess.run(
                ["ros2", "topic", "echo", "--once", "/load_cell_data"],
                capture_output=True, text=True, timeout=3, env=ros_env
            )
            m = _re.search(r"data:\s*([\-\d.]+)", echo.stdout)
            if m:
                _delivery_weight = float(m.group(1))
        except Exception:
            pass
        _time.sleep(0.8)


def _reverse_then_go_home(speed=0.09, duration=8.0):
    home = _cfg.get("home", {})
    hx   = float(home.get("x",   0.0))
    hy   = float(home.get("y",   0.0))
    hyaw = float(home.get("yaw", 0.0))
    import math as _math
    hz = _math.sin(hyaw / 2.0)
    hw = _math.cos(hyaw / 2.0)
    goal = (
        '{"pose": {"header": {"frame_id": "map"}, "pose": {"position": '
        '{"x": %f, "y": %f, "z": 0.0}, "orientation": '
        '{"x": 0.0, "y": 0.0, "z": %f, "w": %f}}}}' % (hx, hy, hz, hw)
    )
    ros_env = os.environ.copy()
    ros_env["ROS_DOMAIN_ID"] = ros_env.get("ROS_DOMAIN_ID", "0")

    # Publish backward velocity for the requested duration, then stop.
    reverse_cmd = (
        "timeout %f ros2 topic pub -r 10 /cmd_vel geometry_msgs/msg/Twist "
        "'{linear: {x: %f, y: 0.0, z: 0.0}, angular: {x: 0.0, y: 0.0, z: 0.0}}'" %
        (duration, -abs(speed))
    )
    try:
        subprocess.run(reverse_cmd, shell=True, env=ros_env,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       check=False)
    except Exception:
        pass

    # Send a zero twist to ensure stop, then go home.
    stop_cmd = (
        "ros2 topic pub -1 /cmd_vel geometry_msgs/msg/Twist '{}'"
    )
    try:
        subprocess.run(stop_cmd, shell=True, env=ros_env,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       check=False)
    except Exception:
        pass

    subprocess.Popen(
        ["ros2", "action", "send_goal", "/navigate_to_pose",
         "nav2_msgs/action/NavigateToPose", goal],
        preexec_fn=os.setsid, env=ros_env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )


@app.route("/delivery/start", methods=["POST"])
def deliveryStart():
    global _delivery_launch_process, _delivery_state, _delivery_weight
    if _delivery_launch_process is not None and _delivery_launch_process.poll() is None:
        return jsonify({"status": "already_running"})
    ros_env = os.environ.copy()
    ros_env["ROS_DOMAIN_ID"] = ros_env.get("ROS_DOMAIN_ID", "0")
    _delivery_launch_process = subprocess.Popen(
        ["ros2", "launch", "load_cell_pkg", "delivery_v2.launch.py"],
        preexec_fn=os.setsid,
        env=ros_env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )
    _delivery_state  = "IDLE"
    _delivery_weight = 0.0
    import threading as _th
    _th.Thread(target=_delivery_weight_listener, daemon=True).start()
    return jsonify({"status": "started"})


@app.route("/delivery/stop", methods=["POST"])
def deliveryStop():
    global _delivery_launch_process, _delivery_state, _delivery_weight
    if _delivery_launch_process is not None:
        try:
            import signal as _sig
            os.killpg(os.getpgid(_delivery_launch_process.pid), _sig.SIGTERM)
        except Exception:
            try: _delivery_launch_process.terminate()
            except Exception: pass
        _delivery_launch_process = None
    _delivery_state  = "IDLE"
    _delivery_weight = 0.0
    import threading as _th
    _th.Thread(target=_reverse_then_go_home, daemon=True).start()
    return jsonify({"status": "stopped", "going_home": True})


@app.route("/delivery/status")
def deliveryStatus():
    global _delivery_launch_process, _delivery_weight, _delivery_state
    if _delivery_launch_process is None or _delivery_launch_process.poll() is not None:
        return jsonify({"status": "not_running"}), 204
    return jsonify({
        "state":     _delivery_state,
        "weight":    _delivery_weight,
        "threshold": _cfg.get("delivery", {}).get("weight_threshold", 2.0),
    })


# ── Zone Editor API ──────────────────────────────────────────────────────────

@app.route("/zones/load/<map_name>", methods=["GET"])
def loadZones(map_name):
    """Load zones for a specific map."""
    try:
        zones = _zone_handler.load_zones(map_name)
        return jsonify(zones)
    except Exception as e:
        return jsonify({"zones": [], "error": str(e)}), 200


@app.route("/zones/save", methods=["POST"])
def saveZones():
    """Save zones and generate PGM masks."""
    try:
        data = request.json
        map_name = data.get("map")
        zones = data.get("zones", [])
        
        result = _zone_handler.save_zones(map_name, zones)
        return jsonify(result)
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 400


@app.route("/zones/reload_costmap", methods=["POST"])
def reloadCostmap():
    """Trigger live costmap filter reload."""
    try:
        data = request.get_json(silent=True) or {}
        result = _zone_handler.reload_costmap_filters(data.get("map"))
        return jsonify(result)
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


app.run(host="0.0.0.0", port=5000)
