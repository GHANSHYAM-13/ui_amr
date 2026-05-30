/* ==========================================================================
   mission.js — Waypoint mission system & Settings Orchestration
   ========================================================================== */

var waypoints = [];
var waypointMode = false;
var missionRunning = false;
var missionPaused = false;
var _MISSION_KEY = "rbm_mission";

var wpArrowContainer = new createjs.Container();
wpArrowContainer.visible = false;
if (window.rootObject) {
  rootObject.addChild(wpArrowContainer);
}

(function buildWpArrow() {
  wpArrowContainer.alpha = 0.5; /* Beautifully faded preview symbol */

  var body = new createjs.Shape();
  body.graphics.beginFill("#a78bfa").drawRoundRect(-0.25, -0.20, 0.50, 0.40, 0.08);
  body.graphics.beginStroke("#ffffff").setStrokeStyle(0.02).drawRoundRect(-0.25, -0.20, 0.50, 0.40, 0.08);

  var leftTrack = new createjs.Shape();
  leftTrack.graphics.beginFill("#3b82f6").drawRect(-0.10, -0.22, 0.20, 0.02);
  var rightTrack = new createjs.Shape();
  rightTrack.graphics.beginFill("#3b82f6").drawRect(-0.10, 0.20, 0.20, 0.02);

  var dir = new createjs.Shape();
  dir.graphics.beginFill("rgba(255,255,255,0.95)")
    .moveTo(0.16, 0).lineTo(-0.04, -0.08).lineTo(-0.04, 0.08).closePath();

  var front = new createjs.Shape();
  front.graphics.beginStroke("#ffffff").setStrokeStyle(0.03)
    .moveTo(0.23, -0.12).lineTo(0.23, 0.12);

  wpArrowContainer.addChild(body);
  wpArrowContainer.addChild(leftTrack);
  wpArrowContainer.addChild(rightTrack);
  wpArrowContainer.addChild(dir);
  wpArrowContainer.addChild(front);
})();

/* Enable waypoint placement mode on the canvas viewer */
function enableWaypointMode() {
  if (!poseHasBeenSet) { showToast("⚠ Set 2D Pose Estimate first", "error"); return; }
  waypointMode = true;
  poseEstimateMode = false;
  goalPoseMode = false;

  var mapEl = document.getElementById("map");
  var banner = document.getElementById("pose-banner");
  var coords = document.getElementById("pose-coords");
  var addWp = document.getElementById("btn-add-wp");
  if (mapEl) mapEl.style.cursor = "crosshair";
  if (banner) banner.style.display = "flex";
  if (coords) coords.textContent = "Click on map to place waypoint…";
  if (addWp) addWp.classList.add("btn-wp-active");
  showToast("📍 Click and drag to place waypoint", "info");
}

/* Render the waypoint markers and labels on the canvas stage */
function redrawWaypointMarkers() {
  if (!window.waypointLayer || !window.stage) return;
  waypointLayer.removeAllChildren();
  
  if (window._currentView && window._currentView !== 'missions') {
    stage.update();
    return;
  }

  var scaleX_fix = (typeof FLIP_X !== "undefined" && FLIP_X < 0) ? -1 : 1;
  waypoints.forEach(function (wp, idx) {
    var wpGrp = new createjs.Container();
    wpGrp.x = wp.x; wpGrp.y = wp.y;
    wpGrp.cursor = "pointer";

    // Red Map Pin (Google Maps style)
    var pin = new createjs.Shape();
    pin.graphics.beginFill("#ea4335")
       .moveTo(0, 0)
       .bezierCurveTo(0.12, 0.15, 0.2, 0.25, 0.2, 0.35)
       .arc(0, 0.35, 0.2, 0, Math.PI, false)
       .bezierCurveTo(-0.2, 0.25, -0.12, 0.15, 0, 0)
       .closePath();
       
    var inner = new createjs.Shape();
    inner.graphics.beginFill("#7f1d1d").drawCircle(0, 0.35, 0.08);

    var lbl = new createjs.Text(String(idx + 1), "bold 0.12px Arial", "#ffffff");
    lbl.textAlign = "center"; lbl.textBaseline = "middle";
    lbl.y = 0.35;
    lbl.scaleY = -1; lbl.scaleX = scaleX_fix;
    
    var shadow = new createjs.Text(String(idx + 1), "bold 0.12px Arial", "#000000");
    shadow.textAlign = "center"; shadow.textBaseline = "middle";
    shadow.y = 0.36; shadow.x = 0.01;
    shadow.scaleY = -1; shadow.scaleX = scaleX_fix;
    shadow.alpha = 0.5;

    wpGrp.addChild(pin);
    wpGrp.addChild(inner);
    wpGrp.addChild(shadow);
    wpGrp.addChild(lbl);

    wpGrp.on("mousedown", function(evt) {
      if (typeof window.viewer !== "undefined") {
        var pos = window.viewer.scene.globalToLocal(evt.stageX, evt.stageY);
        this.offset = { x: this.x - pos.x, y: this.y - pos.y };
      }
    });
    wpGrp.on("pressmove", function(evt) {
      if (typeof window.viewer !== "undefined") {
        var pos = window.viewer.scene.globalToLocal(evt.stageX, evt.stageY);
        this.x = pos.x + this.offset.x;
        this.y = pos.y + this.offset.y;
        stage.update();
      }
    });
    wpGrp.on("pressup", function(evt) {
      wp.x = parseFloat(this.x.toFixed(3));
      wp.y = parseFloat(this.y.toFixed(3));
      if (typeof saveMissionFile === "function") {
        saveMissionFile({ notify: true }).catch(function(){});
      }
    });

    waypointLayer.addChild(wpGrp);
  });
  stage.update();
}

/* Populate waypoint lists in both the missions view and localization sidebar */
function renderWpList() {
  var el = document.getElementById("wp-list");
  if (!el) return;

  if (waypoints.length === 0) {
    el.innerHTML = "<div style='color:var(--muted);font-size:13px;padding:8px 0;text-align:center;'>No waypoints placed on map yet</div>";
    var b = document.getElementById("btn-start-mission");
    if (b) b.disabled = true;
    return;
  }

  el.innerHTML = waypoints.map(function (wp, i) {
    var delay = wp.delay !== undefined ? wp.delay : 0;
    return "<div class='wp-item' draggable='true' data-idx='" + i + "'" +
      " ondragstart='wpDragStart(event," + i + ")' ondragover='wpDragOver(event)'" +
      " ondrop='wpDrop(event," + i + ")' ondragend='wpDragEnd(event)'>" +
      "<span class='wp-drag'>⠿</span>" +
      "<span class='wp-name'>" + (i + 1) + ". " + wp.name + "</span>" +
      "<div class='wp-delay-wrap'><input class='wp-delay-input' type='number' min='0' step='1' value='" + delay + "'" +
      " onchange='setWpDelay(" + i + ",this.value)' onclick='event.stopPropagation()'> <span style='font-size:10px;color:var(--muted);'>s</span></div>" +
      "<button class='wp-del' onclick='removeWaypoint(" + i + ")'>✕</button></div>";
  }).join("");

  var bs = document.getElementById("btn-start-mission");
  if (bs) bs.disabled = false;
}

var _dragIdx = null;
function wpDragStart(e, idx) { _dragIdx = idx; e.currentTarget.classList.add("wp-dragging"); e.dataTransfer.effectAllowed = "move"; }
function wpDragOver(e) { e.preventDefault(); e.dataTransfer.dropEffect = "move"; document.querySelectorAll(".wp-item").forEach(function (el) { el.classList.remove("wp-drag-over"); }); e.currentTarget.classList.add("wp-drag-over"); }
function wpDrop(e, toIdx) { e.preventDefault(); if (_dragIdx === null || _dragIdx === toIdx) return; var moved = waypoints.splice(_dragIdx, 1)[0]; waypoints.splice(toIdx, 0, moved); renderWpList(); redrawWaypointMarkers(); saveMissionFile().catch(function () { }); }
function wpDragEnd() { _dragIdx = null; document.querySelectorAll(".wp-item").forEach(function (el) { el.classList.remove("wp-dragging"); el.classList.remove("wp-drag-over"); }); }
function setWpDelay(idx, val) { waypoints[idx].delay = Math.max(0, parseFloat(val) || 0); saveMissionFile().catch(function () { }); }
function removeWaypoint(idx) { waypoints.splice(idx, 1); renderWpList(); redrawWaypointMarkers(); saveMissionFile({ notify: true }).catch(function () { }); }
function clearWaypoints() {
  waypoints = [];
  renderWpList();
  redrawWaypointMarkers();
  saveMissionFile({ notify: true }).catch(function () { });
  showToast("🗑 Waypoints cleared", "info");
}

function saveMissionFile(opts) {
  opts = opts || {};
  return fetch(SERVER_URL + "/mission/save", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ waypoints: waypoints })
  })
    .then(function (r) {
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.json();
    })
    .then(function (data) {
      if (data.status !== "mission_saved") throw new Error(data.message || "save failed");
      if (opts.notify) showToast("💾 Mission saved (" + data.count + " spots)", "success");
      return data;
    })
    .catch(function (err) {
      console.error("Mission save failed:", err);
      showToast("⚠ Mission save failed: " + err.message, "error");
      throw err;
    });
}

/* Update slider text dynamically */
function updateCycleDisplay(val) {
  var disp = document.getElementById("mission-cycle-val");
  if (!disp) return;
  val = parseInt(val);
  if (val === 11) {
    disp.textContent = "🔁 Infinite Cycles";
  } else if (val === 1) {
    disp.textContent = "1 Loop Cycle";
  } else {
    disp.textContent = val + " Loop Cycles";
  }
}

/* Safety Warning and Confirmation overlays */
window.showWarningModal = function (title, message, confirmCallback) {
  var titleEl = document.getElementById("warning-modal-title");
  var msgEl = document.getElementById("warning-modal-message");
  var overlay = document.getElementById("warning-modal");

  if (titleEl) titleEl.textContent = title;
  if (msgEl) msgEl.textContent = message;

  window._warningCallback = confirmCallback;
  if (overlay) overlay.style.display = "flex";
};

window.dismissWarningModal = function () {
  var overlay = document.getElementById("warning-modal");
  if (overlay) overlay.style.display = "none";
  window._warningCallback = null;
};

window.showSafetyAlert = function (message) {
  var msgEl = document.getElementById("safety-alert-message");
  var overlay = document.getElementById("safety-alert-modal");
  if (msgEl && message) {
    msgEl.textContent = message;
  }
  if (overlay) overlay.style.display = "flex";
};

window.dismissSafetyAlert = function () {
  var overlay = document.getElementById("safety-alert-modal");
  if (overlay) overlay.style.display = "none";
};

window.executeWarningModalCallback = function () {
  var overlay = document.getElementById("warning-modal");
  if (overlay) overlay.style.display = "none";
  if (typeof window._warningCallback === "function") {
    window._warningCallback();
  }
  window._warningCallback = null;
};

/* Map deletion safety warning */
window.showDeleteMapModal = function (mapName, confirmCallback) {
  var nameLabel = document.getElementById("delete-map-name-label");
  var overlay = document.getElementById("delete-map-modal");

  if (nameLabel) nameLabel.textContent = mapName;
  window._deleteMapCallback = confirmCallback;
  if (overlay) overlay.style.display = "flex";
};

window.dismissDeleteMapModal = function () {
  var overlay = document.getElementById("delete-map-modal");
  if (overlay) overlay.style.display = "none";
  window._deleteMapCallback = null;
};

window.executeDeleteMap = function () {
  var overlay = document.getElementById("delete-map-modal");
  if (overlay) overlay.style.display = "none";
  if (typeof window._deleteMapCallback === "function") {
    window._deleteMapCallback();
  }
  window._deleteMapCallback = null;
};

/* Admin/Settings Modal actions */
window.openAdminSettings = function () {
  var overlay = document.getElementById("settings-overlay");
  if (overlay) overlay.style.display = "flex";
  loadSettingsMapList();
};

window.closeAdminSettings = function () {
  var overlay = document.getElementById("settings-overlay");
  if (overlay) overlay.style.display = "none";
};

/* Populates administrative panel map manager */
window.loadSettingsMapList = function () {
  var container = document.getElementById("settings-map-list");
  if (!container) return;

  container.innerHTML = "<div style='color:var(--muted);font-size:12px;padding:12px;text-align:center;'>Scanning system maps...</div>";

  fetch(SERVER_URL + "/maps?t=" + Date.now())
    .then(function (r) { return r.json(); })
    .then(function (maps) {
      if (!maps || maps.length === 0) {
        container.innerHTML = "<div style='color:var(--muted);font-size:12px;padding:12px;text-align:center;'>No saved maps found on disk</div>";
        return;
      }

      container.innerHTML = maps.map(function (mapFile) {
        return "<div style='background:rgba(255,255,255,0.03);border:1px solid var(--border);border-radius:8px;padding:10px;display:flex;justify-content:space-between;align-items:center;transition:border-color 0.2s;' onmouseover='this.style.borderColor=\"var(--border-focus)\"' onmouseout='this.style.borderColor=\"var(--border)\"'>" +
          "<div style='display:flex;flex-direction:column;gap:2px;'>" +
          "<span style='font-size:12px;font-weight:600;color:var(--text);'>" + mapFile + "</span>" +
          "<span style='font-size:10px;color:var(--muted);'>ROS 2 Occupancy Grid Map</span>" +
          "</div>" +
          "<div style='display:flex;gap:6px;'>" +
          "<button onclick='loadMapForLocalization(\"" + mapFile + "\")' style='padding:5px 8px;font-size:10px;background:var(--accent);color:#fff;border:none;border-radius:4px;cursor:pointer;font-weight:600;'>⚡ Load</button>" +
          "<button onclick='deleteMapClick(\"" + mapFile + "\")' style='padding:5px 8px;font-size:10px;background:var(--red);color:#fff;border:none;border-radius:4px;cursor:pointer;'>🗑</button>" +
          "</div>" +
          "</div>";
      }).join("");
    })
    .catch(function () {
      container.innerHTML = "<div style='color:var(--red);font-size:12px;padding:12px;text-align:center;'>Failed to communicate with map database</div>";
    });
};

/* Start mapping SLAM environment scanner */
window.startMappingFromSettings = function () {
  if (typeof _robotRunning !== 'undefined' && !_robotRunning) {
    showToast('⚠ Start the robot first (▶ START ROBOT)', 'error');
    return;
  }
  fetch(SERVER_URL + "/start_mapping", { method: "POST" })
    .then(function (r) { return r.json(); })
    .then(function (data) {
      if (data.status === "mapping_started") {
        showToast("🗺️ SLAM Mapping scanner initialized!", "success");
        document.getElementById("settings-mapping-idle").style.display = "none";
        document.getElementById("settings-mapping-active").style.display = "block";

        // Seamless transition to active mapping workspace
        if (typeof closeAdminSettings === 'function') closeAdminSettings();
        if (typeof switchView === 'function') switchView('mapping');
        if (typeof startMappingFlow === 'function') {
          startMappingFlow();
        }
      }
    })
    .catch(function () {
      showToast("⚠ Failed to launch SLAM mapping stack", "error");
    });
};

window.stopMappingFromSettings = function () {
  fetch(SERVER_URL + "/stop_mapping", { method: "POST" })
    .then(function (r) { return r.json(); })
    .then(function () {
      showToast("⏹ SLAM mapping stack halted", "info");
      document.getElementById("settings-mapping-idle").style.display = "block";
      document.getElementById("settings-mapping-active").style.display = "none";
    })
    .catch(function () {
      showToast("⚠ Failed to halt SLAM mapping process", "error");
    });
};

window.saveMapFromSettings = function () {
  var input = document.getElementById("settings-mapname");
  if (!input || !input.value.trim()) {
    showToast("⚠ Please enter a valid map name first", "error");
    return;
  }
  var name = input.value.trim();

  fetch(SERVER_URL + "/save_map", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name: name })
  })
    .then(function (r) { return r.json(); })
    .then(function (data) {
      if (data.status === "map_saved") {
        showToast("💾 Map successfully saved as: " + name, "success");
        input.value = "";
        window.stopMappingFromSettings();
        window.loadSettingsMapList();
      } else {
        showToast("⚠ Save failed: " + (data.message || "unknown"), "error");
      }
    })
    .catch(function () {
      showToast("⚠ Network error during save operation", "error");
    });
};

window.loadMapForLocalization = function (mapFile) {
  showToast("⏳ Launching Navigation and loading map...", "info");
  // Backend expects a .yaml filename, not .pgm
  var yamlFile = mapFile.replace(/\.(pgm|png|jpg|jpeg)$/i, ".yaml");

  fetch(SERVER_URL + "/start_localization", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ map: yamlFile })
  })
    .then(function (r) { return r.json(); })
    .then(function (data) {
      showToast("✅ Navigation loaded successfully!", "success");
      closeAdminSettings();

      // Update map selector globally
      var selector = document.getElementById("mapSelect");
      if (selector) {
        var opt = document.createElement("option");
        opt.value = mapFile; opt.textContent = mapFile; opt.selected = true;
        selector.appendChild(opt);
      }
    })
    .catch(function () {
      showToast("⚠ Failed to load environment map stack", "error");
    });
};

window.deleteMapClick = function (mapFile) {
  showDeleteMapModal(mapFile, function () {
    fetch(SERVER_URL + "/map/delete", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: mapFile })
    })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (data.status === "map_deleted") {
          showToast("🗑 Map permanently deleted", "success");
          window.loadSettingsMapList();
        }
      })
      .catch(function () {
        showToast("⚠ Failed to delete map files", "error");
      });
  });
};

/* Loads mission history table */
window.loadMissionHistory = function () {
  var table = document.getElementById("mission-history-body");
  if (!table) return;

  fetch(SERVER_URL + "/mission/history")
    .then(function (r) { return r.json(); })
    .then(function (history) {
      if (!history || history.length === 0) {
        table.innerHTML = "<tr><td colspan='5' style='text-align:center;color:var(--muted);font-size:12px;padding:12px;'>No past mission entries found</td></tr>";
        return;
      }

      table.innerHTML = history.map(function (item) {
        var badgeClass = "badge-stopped";
        if (item.status === "Completed") badgeClass = "badge-completed";
        if (item.status === "Active") badgeClass = "badge-active";
        if (item.status === "Paused") badgeClass = "badge-paused";

        var wpPath = Array.isArray(item.waypoints) ? item.waypoints.join(" → ") : "None";
        return "<tr>" +
          "<td>" + item.timestamp + "</td>" +
          "<td style='color:var(--accent);'>" + item.map + "</td>" +
          "<td>" + (item.cycles === -1 || item.cycles === "Infinite" ? "🔁 Infinite" : item.cycles) + "</td>" +
          "<td style='font-size:11px;color:var(--muted);' title='" + wpPath + "'>" + (wpPath.length > 30 ? wpPath.substring(0, 27) + "..." : wpPath) + "</td>" +
          "<td><span class='badge " + badgeClass + "'>" + item.status + "</span></td>" +
          "</tr>";
      }).join("");
    })
    .catch(function () {
      table.innerHTML = "<tr><td colspan='5' style='text-align:center;color:var(--red);font-size:12px;'>Failed to fetch mission history</td></tr>";
    });
};

/* Start running waypoint mission */
function startMission() {
  if (waypoints.length === 0) { showToast("⚠ Please add waypoints first", "error"); return; }

  var cycleInfinite = document.getElementById("mission-cycle-infinite");
  var cycleInput = document.getElementById("mission-cycle-input");
  var cycles = 1;
  if (cycleInfinite && cycleInfinite.checked) {
    cycles = -1;
  } else if (cycleInput) {
    cycles = parseInt(cycleInput.value) || 1;
    if (cycles < 1) cycles = 1;
  }

  showToast("🚀 Initiating Waypoint Mission...", "info");

  saveMissionFile()
    .then(function () {
      // Calculate total cycle distance for progress display
      var totalDist = 0.0;
      for (var i = 1; i < waypoints.length; i++) {
        var dx = waypoints[i].x - waypoints[i - 1].x;
        var dy = waypoints[i].y - waypoints[i - 1].y;
        totalDist += Math.sqrt(dx * dx + dy * dy);
      }
      window._missionCycleDistance = totalDist;
      console.log("[mission] Calculated cycle distance: " + totalDist.toFixed(2) + "m");
      
      return fetch(SERVER_URL + "/mission/start", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ cycles: cycles })
      });
    })
    .then(function (r) { return r.json(); })
    .then(function (data) {
      missionRunning = true;
      missionPaused = false;
      _missionStartEpoch = Date.now();
      if (typeof lockTeleop === "function") lockTeleop();
      _saveMissionSession();
      _uiRunning();
      showToast("🚀 Waypoint Mission successfully launched!", "success");
      window.loadMissionHistory();
      _poll();
    })
    .catch(function () {
      showToast("⚠ Failed to start waypoint mission flow", "error");
    });
}

function stopMission() {
  showWarningModal("STOP AUTONOMOUS MISSION", "Are you absolutely sure you want to stop the active waypoint mission cycle? This halts the robot immediately.", function () {
    missionRunning = false;
    missionPaused = false;
    if (typeof unlockTeleop === "function") unlockTeleop();
    if (typeof clearNavPath === "function") clearNavPath();
    _clearMissionSession();
    _uiStopped();
    showToast("⛔ Autonomous waypoint mission halted", "success");

    fetch(SERVER_URL + "/mission/stop", { method: "POST" })
      .then(function () {
        window.loadMissionHistory();
      })
      .catch(function () { });
  });
}

function togglePauseMission() {
  if (!missionRunning) return;

  if (missionPaused) {
    // Resume
    fetch(SERVER_URL + "/mission/resume", { method: "POST" })
      .then(function (r) { return r.json(); })
      .then(function () {
        missionPaused = false;
        showToast("▶ Autonomous Waypoint Mission resumed", "success");
        window.loadMissionHistory();
        _poll();
      });
  } else {
    // Pause
    fetch(SERVER_URL + "/mission/pause", { method: "POST" })
      .then(function (r) { return r.json(); })
      .then(function () {
        missionPaused = true;
        showToast("⏸ Autonomous Waypoint Mission PAUSED (safe halt active)", "warn");
        window.loadMissionHistory();
      });
  }
}

/* Poll Timer and loop status check */
var _pollTimer = null;
var _elapsedTicker = null;
var _missionStartEpoch    = 0;   // kept for session restore; not used for display
var _missionServerElapsed = 0;   // last elapsed_seconds received from server
var _missionLastPollMs    = 0;   // Date.now() when last poll arrived

function _fmtTime(seconds) {
  seconds = Math.max(0, Math.round(seconds));
  var m = Math.floor(seconds / 60);
  var s = seconds % 60;
  return (m < 10 ? '0' : '') + m + ':' + (s < 10 ? '0' : '') + s;
}

function _updateElapsedUI() {
  // Use server epoch as baseline; interpolate by 1s between polls
  var elapsed = _missionServerElapsed + Math.round((Date.now() - _missionLastPollMs) / 1000);
  elapsed = Math.max(0, elapsed);
  var t = _fmtTime(elapsed);
  var el1 = document.getElementById('mission-elapsed-label');
  var el2 = document.getElementById('mission-elapsed-time');
  if (el1) el1.textContent = t;
  if (el2) el2.textContent = t;
}

function _updateProgressUI(d) {
  var totalCycles  = d.total_cycles;          // -1 = infinite
  var currentCycle = d.cycle   || 0;
  var totalWps     = d.total_wps || 0;
  var currentWp    = d.waypoint || 0;
  var elapsed      = d.elapsed_seconds || 0;
  var totalDistance = d.total_distance || window._missionCycleDistance || 0;

  // ── Sync elapsed ticker to server truth ─────────────────────────────────
  _missionServerElapsed = elapsed;
  _missionLastPollMs    = Date.now();

  // ── Cycle bar ─────────────────────────────────────────────────────────────
  var cycleBar      = document.getElementById('mission-cycle-bar');
  var cycleBarLabel = document.getElementById('mission-cycle-bar-label');
  var cyclePct      = document.getElementById('mission-cycle-pct');
  var cycleThumb    = document.getElementById('mission-cycle-thumb');
  var cyclesLeft    = document.getElementById('mission-cycles-left');

  var cyclePctVal = 0;
  if (totalCycles > 0) {
    // Completed cycles = currentCycle (runner writes cycle number AFTER reaching wp1)
    // e.g. cycle=1 of 2 total = 50%
    cyclePctVal = Math.min(100, Math.round((currentCycle / totalCycles) * 100));
  }

  if (cycleBar)      cycleBar.style.width = cyclePctVal + '%';
  if (cyclePct)      cyclePct.textContent  = cyclePctVal + '%';
  if (cycleBarLabel) {
    if (totalCycles === -1) {
      cycleBarLabel.textContent = 'Cycle ' + currentCycle + '  (∞)';
    } else {
      cycleBarLabel.textContent = currentCycle + ' of ' + totalCycles + ' complete';
    }
  }
  // Show white thumb when bar has any fill
  if (cycleThumb) cycleThumb.style.display = (cyclePctVal > 0) ? 'inline-block' : 'none';
  if (cyclesLeft) {
    cyclesLeft.textContent = totalCycles === -1 ? '∞' : Math.max(0, totalCycles - currentCycle);
  }

  // ── Waypoint bar ──────────────────────────────────────────────────────────
  var wpBar      = document.getElementById('mission-wp-bar');
  var wpBarLabel = document.getElementById('mission-wp-bar-label');
  var wpPct      = document.getElementById('mission-wp-pct');
  var wpName     = document.getElementById('mission-wp-name');

  var wpPctVal = totalWps > 0 ? Math.min(100, Math.round((currentWp / totalWps) * 100)) : 0;
  if (wpBar)      wpBar.style.width   = wpPctVal + '%';
  if (wpPct)      wpPct.textContent   = wpPctVal + '%';
  if (wpBarLabel) wpBarLabel.textContent = currentWp + ' / ' + totalWps + (totalWps > 0 ? ' wp' : '');
  if (wpName)     wpName.textContent  = d.waypoint_name || '';

  // ── Distance-to-Travel Progress ──────────────────────────────────────────
  var distEl = document.getElementById('mission-distance-remaining');
  var distBar      = document.getElementById('mission-time-bar');
  var distBarLabel = document.getElementById('mission-time-bar-label');

  var distPctVal = 0;
  var remDist    = 0;
  var cycleTotal = totalDistance > 0 ? totalDistance : 0;
  
  if (cycleTotal > 0) {
    var distTraveled = (currentCycle - 1) * cycleTotal;
    if (currentWp > 0 && totalWps > 0) {
      distTraveled += (currentWp / totalWps) * cycleTotal;
    }
    remDist = Math.max(0, (totalCycles > 0 ? totalCycles : 1) * cycleTotal - distTraveled);
    distPctVal = cycleTotal > 0 ? Math.min(100, Math.round((distTraveled / ((totalCycles > 0 ? totalCycles : 1) * cycleTotal)) * 100)) : 0;
  }

  if (distBar)      distBar.style.width = distPctVal + '%';
  if (distBarLabel) {
    if (cycleTotal > 0) {
      var distTrav = totalCycles > 0 ? ((currentCycle - 1 + (currentWp / totalWps)) * cycleTotal) : 0;
      distBarLabel.textContent = distTrav.toFixed(1) + 'm  /  ' + (remDist > 0 ? remDist.toFixed(1) : '0') + 'm rem.';
    } else if (totalCycles === -1) {
      distBarLabel.textContent = 'Cycle ' + currentCycle + '  (∞)';
    } else {
      distBarLabel.textContent = '—';
    }
  }
  if (distEl) distEl.textContent = remDist > 0 ? remDist.toFixed(1) + 'm' : '—';
  
  // Update main display box
  var distDisplay = document.getElementById('mission-distance-display');
  if (distDisplay) distDisplay.textContent = remDist > 0 ? remDist.toFixed(1) + 'm' : '—';
  
  var wpDisplay = document.getElementById('mission-current-wp');
  if (wpDisplay) wpDisplay.textContent = currentWp + ' / ' + totalWps;

  // ── Bottom badge (bottom bar) ─────────────────────────────────────────────
  var badge = document.getElementById('mission-progress-badge');
  if (badge) badge.textContent = currentCycle + ' / ' + (totalCycles === -1 ? '∞' : totalCycles);

  // ── Active map name ───────────────────────────────────────────────────────
  var mapName = document.getElementById('missions-map-name');
  if (mapName && d.map) mapName.textContent = d.map;

  // ── Running / Paused state indicator ─────────────────────────────────────
  var stateLabel = document.getElementById('mission-run-state-label');
  var pulseDot   = document.getElementById('mission-pulse-dot');
  if (d.paused) {
    if (stateLabel) { stateLabel.textContent = 'PAUSED';  stateLabel.style.color = 'var(--yellow)'; }
    if (pulseDot)  { pulseDot.style.background = 'var(--yellow)'; pulseDot.style.boxShadow = '0 0 6px var(--yellow)'; pulseDot.style.animation = 'none'; }
  } else {
    if (stateLabel) { stateLabel.textContent = 'RUNNING'; stateLabel.style.color = 'var(--green)'; }
    if (pulseDot)  { pulseDot.style.background = 'var(--green)';  pulseDot.style.boxShadow = '0 0 8px var(--green)';  pulseDot.style.animation = 'pulse 1.2s infinite'; }
  }
}

function _poll() {
  if (_pollTimer) { clearTimeout(_pollTimer); _pollTimer = null; }
  if (!missionRunning) return;

  fetch(SERVER_URL + "/mission/status")
    .then(function (r) { return r.json(); })
    .then(function (d) {
      if (!missionRunning) return;

      missionPaused = d.paused;

      // Update rich progress UI
      _updateProgressUI(d);

      // Update legacy status indicators
      var statusBadge = document.getElementById("mission-status-badge");
      var pauseBtn = document.getElementById("btn-pause-mission");

      if (statusBadge) {
        if (missionPaused) {
          statusBadge.innerHTML = "<span class='pulse-dot' style='background:var(--yellow); animation: none;'></span> PAUSED";
          statusBadge.className = "status-indicator";
          statusBadge.style.color = "var(--yellow)";
        } else {
          statusBadge.innerHTML = "<span class='pulse-dot' style='background:var(--green);'></span> RUNNING";
          statusBadge.className = "status-indicator active";
          statusBadge.style.color = "var(--green)";
        }
      }

      if (pauseBtn) {
        if (missionPaused) {
          pauseBtn.textContent = "▶ Resume Mission";
          pauseBtn.style.background = "linear-gradient(135deg, var(--green), #047857)";
          pauseBtn.style.borderColor = "var(--green)";
        } else {
          pauseBtn.textContent = "⏸ Pause Mission";
          pauseBtn.style.background = "linear-gradient(135deg, var(--yellow), #b45309)";
          pauseBtn.style.borderColor = "var(--yellow)";
        }
      }

      if (!d.running) {
        missionRunning = false;
        if (_elapsedTicker) { clearInterval(_elapsedTicker); _elapsedTicker = null; }
        if (typeof unlockTeleop === "function") unlockTeleop();
        if (typeof clearNavPath === "function") clearNavPath();
        _clearMissionSession();
        _uiStopped();
        showToast("✅ Waypoint Mission cycle successfully completed!", "success");
        window.loadMissionHistory();
      } else {
        _pollTimer = setTimeout(_poll, 2000);
      }
    })
    .catch(function () {
      if (missionRunning) _pollTimer = setTimeout(_poll, 4000);
    });
}

function enableMissionButtons() {
  var el = document.getElementById("btn-add-wp");
  if (el) el.disabled = false;
}

/* UI transition modifiers */
function _uiRunning() {
  var bs = document.getElementById("btn-start-mission");
  var mrc = document.getElementById("mission-running-controls");
  var indicators = document.getElementById("mission-status-badge");
  var progressPanel = document.getElementById("mission-progress-panel");

  var cycleInput = document.getElementById("mission-cycle-input");
  var cycleInfinite = document.getElementById("mission-cycle-infinite");

  if (bs) bs.style.display = "none";
  if (mrc) mrc.style.display = "flex";
  if (indicators) indicators.style.display = "flex";
  if (progressPanel) progressPanel.style.display = "flex";

  if (cycleInput) cycleInput.disabled = true;
  if (cycleInfinite) cycleInfinite.disabled = true;

  // Start elapsed ticker
  if (_missionStartEpoch === 0) _missionStartEpoch = Date.now();
  if (_elapsedTicker) clearInterval(_elapsedTicker);
  _elapsedTicker = setInterval(_updateElapsedUI, 1000);
}

function _uiStopped() {
  var bs = document.getElementById("btn-start-mission");
  var mrc = document.getElementById("mission-running-controls");
  var indicators = document.getElementById("mission-status-badge");
  var progressPanel = document.getElementById("mission-progress-panel");

  var cycleInput = document.getElementById("mission-cycle-input");
  var cycleInfinite = document.getElementById("mission-cycle-infinite");

  if (bs) { bs.style.display = "flex"; bs.disabled = (waypoints.length === 0); }
  if (mrc) mrc.style.display = "none";
  if (indicators) indicators.style.display = "none";
  if (progressPanel) progressPanel.style.display = "none";

  if (cycleInput && !(cycleInfinite && cycleInfinite.checked)) {
    cycleInput.disabled = false;
  }
  if (cycleInfinite) cycleInfinite.disabled = false;

  // Stop ticker
  if (_elapsedTicker) { clearInterval(_elapsedTicker); _elapsedTicker = null; }
  _missionStartEpoch = 0;
}

/* Page load entry point initialization */
window.initMissionsView = function () {
  renderWpList();
  redrawWaypointMarkers();
  window.loadMissionHistory();
  if (typeof loadSavedMissionsList === "function") loadSavedMissionsList();

  // Initialize new cycle input displays
  var cycleInput = document.getElementById("mission-cycle-input");
  if (cycleInput) {
    window.onCycleInputChanged(cycleInput.value);
  }
};

/* Session management */
function _saveMissionSession() {
  try {
    var m = document.getElementById("mapSelect");
    sessionStorage.setItem(_MISSION_KEY, JSON.stringify({
      running: true,
      map: m ? m.value : "",
      startEpoch: Date.now()
    }));
  } catch (e) { }
}
function _clearMissionSession() { try { sessionStorage.removeItem(_MISSION_KEY); } catch (e) { } }
function _loadMissionSession() { try { var r = sessionStorage.getItem(_MISSION_KEY); return r ? JSON.parse(r) : null; } catch (e) { return null; } }
function _missionMapFallback() {
  try { return localStorage.getItem("robomuse_loc_map") || ""; } catch (e) { return ""; }
}

/* Load initial config waypoints */
fetch(SERVER_URL + "/mission")
  .then(function (r) { return r.json(); })
  .then(function (data) {
    if (data.waypoints && data.waypoints.length > 0) {
      waypoints = data.waypoints;
      renderWpList();
    }
  })
  .catch(function () { });

/* Auto-reconnect flow logic */
function _reconnect() {
  var sess = _loadMissionSession();
  fetch(SERVER_URL + "/mission/status")
    .then(function (r) { return r.json(); })
    .then(function (d) {
      if (!d.running) { _clearMissionSession(); return; }
      _restoreRunningMission(sess);
    })
    .catch(function () { if (sess && sess.running) _clearMissionSession(); });
}

function _restoreRunningMission(sess) {
  sess = sess || { running: true, map: _missionMapFallback() };
  missionRunning = true;
  if (typeof lockTeleop === "function") lockTeleop();

  // Restore elapsed time from saved epoch
  if (sess.startEpoch) {
    _missionStartEpoch = sess.startEpoch;
  } else {
    _missionStartEpoch = Date.now();
  }
  _uiRunning();

  if (sess.map) {
    var ms = document.getElementById("mapSelect");
    if (ms) { for (var i = 0; i < ms.options.length; i++) { if (ms.options[i].value === sess.map) { ms.selectedIndex = i; break; } } }
  }
  _saveMissionSession();

  poseHasBeenSet = true;
  var g = document.getElementById("btn-goal");
  var h = document.getElementById("btn-home");
  if (g) { g.disabled = false; g.classList.add("btn-goal-ready"); }
  if (h) h.disabled = false;
  if (typeof enableMissionButtons === "function") enableMissionButtons();
  if (typeof startLivePosePolling === "function") startLivePosePolling();

  showToast("🔄 Reconnected — active mission running", "success");
  _poll();
}

window.saveNamedMissionPrompt = function() {
  if (waypoints.length === 0) {
    showToast("⚠ Cannot save an empty mission configuration", "error");
    return;
  }
  var defaultName = window._currentMissionName || "";
  var name = prompt("Enter a name for this mission configuration (overwrite to update):", defaultName);
  if (name === null) return;
  name = name.trim();
  if (!name) {
    showToast("⚠ Mission name cannot be empty", "error");
    return;
  }
  
  saveMissionFile()
  .then(function() {
    return fetch(SERVER_URL + "/mission/save_named", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: name, waypoints: waypoints })
    });
  })
  .then(function(r) {
    if (!r.ok) throw new Error("HTTP " + r.status);
    return r.json();
  })
  .then(function(data) {
    if (data.status === "success") {
      window._currentMissionName = name;
      showToast("💾 Mission successfully saved as: " + data.name, "success");
      if (typeof loadSavedMissionsList === "function") loadSavedMissionsList();
    } else {
      showToast("⚠ Failed to save named mission: " + (data.message || "unknown"), "error");
    }
  })
  .catch(function(err) {
    console.error("Save named mission error:", err);
    showToast("⚠ Network error saving named mission: " + err.message, "error");
  });
};

window.loadSavedMissionsList = function() {
  var dropdown = document.getElementById("mission-select-dropdown");
  if (!dropdown) return;
  
  fetch(SERVER_URL + "/missions/list?t=" + Date.now())
  .then(function(r) { return r.json(); })
  .then(function(list) {
    dropdown.innerHTML = '<option value="">-- Choose a Saved Mission --</option>';
    if (list && list.length > 0) {
      list.forEach(function(item) {
        var opt = document.createElement("option");
        opt.value = item.name;
        opt.textContent = item.name + " (" + item.waypoints.length + " spots)";
        dropdown.appendChild(opt);
      });
    }
  })
  .catch(function() {
    console.error("Failed to load saved named missions list");
  });
};

window.onSavedMissionSelected = function(val) {
  if (!val) {
    window._currentMissionName = "";
    return;
  }
  
  showToast("⏳ Loading mission config: " + val + "...", "info");
  fetch(SERVER_URL + "/mission/load_named", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name: val })
  })
  .then(function(r) { return r.json(); })
  .then(function(data) {
    if (data.status === "success") {
      waypoints = data.waypoints || [];
      window._currentMissionName = val;
      renderWpList();
      redrawWaypointMarkers();
      showToast("✅ Loaded " + val + " configuration", "success");
    } else {
      showToast("⚠ Failed to load mission: " + (data.message || "unknown"), "error");
    }
  })
  .catch(function() {
    showToast("⚠ Network error loading named mission", "error");
  });
};

setTimeout(function () {
  _reconnect();
  window.initMissionsView();
}, 1500);
