#!/usr/bin/env python3
"""
zone_handler.py - Zone storage, PGM mask generation, and Nav2 filter integration.
"""

import json
import os
import subprocess


class ZoneHandler:
    """Manage navigation zones and Nav2 costmap filter masks."""

    KEEP_VALUE = {
        "no-go": 100,
        "lane": 5,
    }
    SLOW_VALUE = 40

    def __init__(self, maps_dir, nav2_params_path=None):
        self.maps_dir = os.path.abspath(maps_dir)
        self.nav2_params_path = nav2_params_path or self._default_nav2_params_path()
        os.makedirs(self.maps_dir, exist_ok=True)

    def _default_nav2_params_path(self):
        """Find nav2_params.yaml — search multiple locations, then copy
        to a local writable path so update_nav2_params() always works."""
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        local_copy = os.path.join(base_dir, "config", "nav2_params.yaml")

        # If we already have a local writable copy, use it
        if os.path.isfile(local_copy):
            return local_copy

        # Search for the source file in likely locations
        candidates = []
        # 1. Sibling source tree:  ../navigation2/nav2_bringup/params/
        src_dir = os.path.dirname(base_dir)
        candidates.append(os.path.join(src_dir, "navigation2", "nav2_bringup", "params", "nav2_params.yaml"))
        # 2. Installed package share directory (colcon workspace)
        try:
            from ament_index_python.packages import get_package_share_directory
            share = get_package_share_directory("nav2_bringup")
            candidates.append(os.path.join(share, "params", "nav2_params.yaml"))
        except Exception:
            pass
        # 3. Standard ROS install
        candidates.append("/opt/ros/humble/share/nav2_bringup/params/nav2_params.yaml")

        for candidate in candidates:
            if os.path.isfile(candidate):
                # Copy to local writable location so update_nav2_params() can modify it
                try:
                    import shutil
                    os.makedirs(os.path.dirname(local_copy), exist_ok=True)
                    shutil.copy2(candidate, local_copy)
                    print(f"[zone_handler] Copied nav2_params.yaml from {candidate} → {local_copy}")
                    return local_copy
                except Exception as e:
                    print(f"[zone_handler] Cannot copy {candidate}: {e}, using in-place")
                    return candidate

        print("[zone_handler] WARNING: nav2_params.yaml not found in any location!")
        return None

    def _map_base(self, map_name):
        base = os.path.basename(map_name or "")
        for ext in (".yaml", ".yml", ".pgm", ".png", ".jpg", ".jpeg"):
            if base.lower().endswith(ext):
                return base[:-len(ext)]
        return base

    def get_zones_file(self, map_name):
        return os.path.join(self.maps_dir, self._map_base(map_name) + "_zones.json")

    def get_mask_file(self, map_name, mask_type):
        return os.path.join(self.maps_dir, self._map_base(map_name) + "_" + mask_type + "_mask.pgm")

    def get_mask_yaml_file(self, map_name, mask_type):
        return os.path.join(self.maps_dir, self._map_base(map_name) + "_" + mask_type + "_mask.yaml")

    def load_zones(self, map_name):
        path = self.get_zones_file(map_name)
        if os.path.exists(path):
            with open(path) as f:
                return json.load(f)
        return {"zones": []}

    def save_zones(self, map_name, zones):
        map_base = self._map_base(map_name)
        if not map_base:
            raise ValueError("Missing map name")

        clean_zones = self._clean_zones(zones)
        with open(self.get_zones_file(map_base), "w") as f:
            json.dump({"zones": clean_zones}, f, indent=2)

        masks = self.generate_masks(map_base, clean_zones)
        self.update_nav2_params(map_base)
        return {"status": "saved", "masks": masks}

    def prepare_map(self, map_name):
        map_base = self._map_base(map_name)
        zones = self.load_zones(map_base).get("zones", [])
        self.generate_masks(map_base, zones)
        self.update_nav2_params(map_base)

    def _clean_zones(self, zones):
        clean = []
        for zone in zones or []:
            zone_type = zone.get("type")
            vertices = zone.get("vertices", [])
            min_vertices = 2 if zone_type == "lane" else 3
            if zone_type not in ("no-go", "lane", "slow") or len(vertices) < min_vertices:
                continue
            clean_vertices = []
            for v in vertices:
                clean_vertices.append({"x": float(v["x"]), "y": float(v["y"])})
            clean_zone = {"type": zone_type, "vertices": clean_vertices}
            if zone_type == "lane":
                clean_zone["width"] = max(0.01, float(zone.get("width", 0.5)))
            clean.append(clean_zone)
        return clean

    def _read_map_yaml(self, map_name):
        yaml_path = os.path.join(self.maps_dir, self._map_base(map_name) + ".yaml")
        meta = {}
        with open(yaml_path) as f:
            for raw in f:
                line = raw.split("#", 1)[0].strip()
                if not line or ":" not in line:
                    continue
                key, value = line.split(":", 1)
                meta[key.strip()] = value.strip().strip("'\"")
        origin = meta.get("origin", "[0, 0, 0]").strip("[]")
        origin = [float(v.strip()) for v in origin.split(",")[:3]]
        return {
            "yaml": yaml_path,
            "image": meta.get("image", self._map_base(map_name) + ".pgm"),
            "resolution": float(meta.get("resolution", "0.05")),
            "origin": origin,
        }

    def _read_pgm_size(self, pgm_path):
        with open(pgm_path, "rb") as f:
            def token():
                buf = b""
                while True:
                    ch = f.read(1)
                    if not ch:
                        return buf.decode()
                    if ch == b"#":
                        f.readline()
                        continue
                    if ch.isspace():
                        if buf:
                            return buf.decode()
                        continue
                    buf += ch

            magic = token()
            if magic not in ("P5", "P2"):
                raise ValueError("Unsupported PGM format: " + magic)
            width = int(token())
            height = int(token())
            max_value = int(token())
            if max_value <= 0:
                raise ValueError("Invalid PGM max value")
            return width, height

    def _occupancy_to_pgm(self, value):
        value = max(0, min(100, int(value)))
        return int(round(255 - (value * 255 / 100.0)))

    def _write_pgm(self, path, width, height, data):
        with open(path, "wb") as f:
            f.write(("P5\n" + str(width) + " " + str(height) + "\n255\n").encode())
            f.write(bytes(data))

    def _write_mask_yaml(self, path, image_path, resolution, origin, speed=False):
        occupied_thresh = "0.99" if speed else "0.65"
        free_thresh = "0.01" if speed else "0.196"
        with open(path, "w") as f:
            f.write("image: " + os.path.basename(image_path) + "\n")
            f.write("mode: scale\n")
            f.write("resolution: " + str(resolution) + "\n")
            f.write("origin: [" + str(origin[0]) + ", " + str(origin[1]) + ", " + str(origin[2]) + "]\n")
            f.write("negate: 0\n")
            f.write("occupied_thresh: " + occupied_thresh + "\n")
            f.write("free_thresh: " + free_thresh + "\n")

    def _world_to_image(self, vertex, width, height, resolution, origin):
        px = int(round((vertex["x"] - origin[0]) / resolution))
        py = int(round((vertex["y"] - origin[1]) / resolution))
        return px, height - 1 - py

    def _fill_polygon(self, data, width, height, points, pgm_value):
        if len(points) < 3:
            return
        min_y = max(0, min(y for _, y in points))
        max_y = min(height - 1, max(y for _, y in points))
        for y in range(min_y, max_y + 1):
            xs = []
            j = len(points) - 1
            for i in range(len(points)):
                xi, yi = points[i]
                xj, yj = points[j]
                if (yi > y) != (yj > y):
                    x = xi + (y - yi) * (xj - xi) / float(yj - yi)
                    xs.append(x)
                j = i
            xs.sort()
            for i in range(0, len(xs), 2):
                if i + 1 >= len(xs):
                    break
                x0 = max(0, int(round(xs[i])))
                x1 = min(width - 1, int(round(xs[i + 1])))
                for x in range(x0, x1 + 1):
                    data[y * width + x] = pgm_value

    def _fill_disc(self, data, width, height, cx, cy, radius, pgm_value):
        r = max(1, int(round(radius)))
        r2 = r * r
        for y in range(max(0, cy - r), min(height - 1, cy + r) + 1):
            for x in range(max(0, cx - r), min(width - 1, cx + r) + 1):
                dx = x - cx
                dy = y - cy
                if dx * dx + dy * dy <= r2:
                    data[y * width + x] = pgm_value

    def _fill_polyline(self, data, width, height, points, line_width, pgm_value):
        if len(points) < 2:
            return
        radius = max(1, line_width / 2.0)
        for i in range(1, len(points)):
            x0, y0 = points[i - 1]
            x1, y1 = points[i]
            dx = x1 - x0
            dy = y1 - y0
            steps = max(1, int(max(abs(dx), abs(dy))))
            for step in range(steps + 1):
                t = step / float(steps)
                x = int(round(x0 + dx * t))
                y = int(round(y0 + dy * t))
                self._fill_disc(data, width, height, x, y, radius, pgm_value)

    def generate_masks(self, map_name, zones):
        meta = self._read_map_yaml(map_name)
        pgm_path = os.path.join(self.maps_dir, meta["image"])
        width, height = self._read_pgm_size(pgm_path)

        keepout = [255] * (width * height)
        speed = [255] * (width * height)

        for zone in self._clean_zones(zones):
            points = [
                self._world_to_image(v, width, height, meta["resolution"], meta["origin"])
                for v in zone["vertices"]
            ]
            if zone["type"] == "slow":
                self._fill_polygon(speed, width, height, points, self._occupancy_to_pgm(self.SLOW_VALUE))
            elif zone["type"] == "lane":
                line_width = zone.get("width", 0.5) / meta["resolution"]
                self._fill_polyline(
                    keepout, width, height, points, line_width,
                    self._occupancy_to_pgm(self.KEEP_VALUE["lane"])
                )
            else:
                self._fill_polygon(
                    keepout, width, height, points,
                    self._occupancy_to_pgm(self.KEEP_VALUE[zone["type"]])
                )

        keepout_pgm = self.get_mask_file(map_name, "keepout")
        speed_pgm = self.get_mask_file(map_name, "speed")
        keepout_yaml = self.get_mask_yaml_file(map_name, "keepout")
        speed_yaml = self.get_mask_yaml_file(map_name, "speed")

        self._write_pgm(keepout_pgm, width, height, keepout)
        self._write_pgm(speed_pgm, width, height, speed)
        self._write_mask_yaml(keepout_yaml, keepout_pgm, meta["resolution"], meta["origin"], speed=False)
        self._write_mask_yaml(speed_yaml, speed_pgm, meta["resolution"], meta["origin"], speed=True)

        return {
            "keepout": keepout_yaml,
            "speed": speed_yaml,
        }

    def update_nav2_params(self, map_name):
        if not self.nav2_params_path or not os.path.exists(self.nav2_params_path):
            return {"status": "skipped", "message": "nav2_params.yaml not found"}

        replacements = {
            "keepout_filter_mask_server": self.get_mask_yaml_file(map_name, "keepout"),
            "speed_filter_mask_server": self.get_mask_yaml_file(map_name, "speed"),
        }
        with open(self.nav2_params_path) as f:
            lines = f.readlines()

        current_node = None
        updated = []
        for line in lines:
            stripped = line.strip()
            if line and not line.startswith(" ") and stripped.endswith(":"):
                current_node = stripped[:-1]
            if current_node in replacements and stripped.startswith("yaml_filename:"):
                indent = line[:len(line) - len(line.lstrip())]
                line = indent + "yaml_filename: \"" + replacements[current_node] + "\"\n"
            updated.append(line)

        with open(self.nav2_params_path, "w") as f:
            f.writelines(updated)
        return {"status": "updated", "file": self.nav2_params_path}

    def _service_call(self, service, srv_type, request, timeout=6):
        cmd = ["ros2", "service", "call", service, srv_type, request]
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)

    def reload_costmap_filters(self, map_name=None):
        mask_services = []
        if map_name:
            mask_services.append((
                "/keepout_filter_mask_server/load_map",
                "nav2_msgs/srv/LoadMap",
                "{map_url: '" + self.get_mask_yaml_file(map_name, "keepout") + "'}"
            ))
            mask_services.append((
                "/speed_filter_mask_server/load_map",
                "nav2_msgs/srv/LoadMap",
                "{map_url: '" + self.get_mask_yaml_file(map_name, "speed") + "'}"
            ))
        clear_services = [
            ("/global_costmap/clear_entirely_global_costmap", "nav2_msgs/srv/ClearEntireCostmap", "{}"),
            ("/local_costmap/clear_entirely_local_costmap", "nav2_msgs/srv/ClearEntireCostmap", "{}"),
        ]

        results = []
        # Call mask load_map services first — these are the critical ones
        masks_ok = True
        for service, srv_type, request in mask_services:
            try:
                result = self._service_call(service, srv_type, request)
                results.append({"service": service, "code": result.returncode})
                if result.returncode != 0:
                    masks_ok = False
            except Exception as e:
                results.append({"service": service, "error": str(e)})
                masks_ok = False

        # Clear costmaps regardless
        for service, srv_type, request in clear_services:
            try:
                result = self._service_call(service, srv_type, request)
                results.append({"service": service, "code": result.returncode})
            except Exception as e:
                results.append({"service": service, "error": str(e)})

        # Report based on mask reload success, NOT costmap clear success
        if not mask_services:
            status = "reloaded"  # no masks to load — just cleared costmaps
            message = "Costmaps cleared (no mask reload requested)"
        elif masks_ok:
            status = "reloaded"
            message = "Costmap filter masks reloaded successfully"
        else:
            status = "error"
            message = "Failed to reload mask servers — are they running?"

        return {
            "status": status,
            "message": message,
            "results": results,
        }


_zone_handler = None


def init_zone_handler(maps_dir, nav2_params_path=None):
    global _zone_handler
    _zone_handler = ZoneHandler(maps_dir, nav2_params_path)
    return _zone_handler


def get_zone_handler():
    return _zone_handler
