/* ==========================================================================
   zone_editor.js - No-Go, Preferred Lane, and Slow Zone drawing.
   Drag to draw closed polygons. Click/drag existing zones to move them.
   ========================================================================== */

var zones = [];
var zoneEditorMode = false;
var mapEditorZoneMode = false;
var currentZoneType = "no-go";
var drawingPolygon = [];
var selectedZoneIdx = -1;
var isMovingZone = false;
var isDrawingZone = false;
var moveStartPos = null;
var zoneDragStart = null;
var zoneDrawLayer = null;
var mapName = null;
var zoneMapMeta = null;
var mapEditorZoneEventsAttached = false;
var previewTarget = null;
var previewCurrent = null;
var previewAnimId = null;
var PREVIEW_LERP = 0.22;

/* LERP rubber-band animation state */
var _lerpCurrent = null;   /* {x,y} animated corner being lerped */
var _lerpTarget  = null;   /* {x,y} actual mouse position */
var _lerpAnimId  = null;   /* rAF handle */
var LERP_SPEED   = 0.18;   /* 0=frozen, 1=instant — tune for feel */

/* Dim overlay canvas (injected once) */
var _dimCanvas   = null;

var ZONE_COLORS = {
  "no-go": { stroke: "#ef4444", fill: "rgba(239,68,68,0.32)", label: "NO-GO" },
  "lane":  { stroke: "#22c55e", fill: "rgba(34,197,94,0.28)", label: "LANE" },
  "slow":  { stroke: "#f59e0b", fill: "rgba(245,158,11,0.28)", label: "SLOW" }
};

function _zoneBaseName(name) {
  if (!name) return null;
  return String(name).replace(/\.(pgm|yaml|yml|png|jpg|jpeg)$/i, "");
}

function _getCurrentMapName() {
  var mapSelect = document.getElementById("mapSelect");
  if (!mapSelect || !mapSelect.value) return null;
  return _zoneBaseName(mapSelect.value);
}

function _zoneToast(message, type) {
  if (typeof showToast === "function") showToast(message, type || "info");
}

function _zonePointer(e) {
  var src = e.touches && e.touches.length ? e.touches[0] : e;
  return { clientX: src.clientX, clientY: src.clientY };
}

function _cloneVertices(vertices) {
  return JSON.parse(JSON.stringify(vertices || []));
}

function _zoneDistance(a, b) {
  var dx = a.x - b.x;
  var dy = a.y - b.y;
  return Math.sqrt(dx * dx + dy * dy);
}

function _maybeAddDrawPoint(point) {
  if (!drawingPolygon.length || _zoneDistance(point, drawingPolygon[drawingPolygon.length - 1]) > 0.05) {
    drawingPolygon.push({ x: point.x, y: point.y });
  }
}

function _zoneWidthPixels() {
  var input = document.getElementById("edit-line-width");
  return Math.max(1, parseInt(input ? input.value : 12, 10) || 12);
}

function _zoneWidthMeters() {
  if (!zoneMapMeta) return 0.5;
  return _zoneWidthPixels() * zoneMapMeta.resolution;
}

function _rectVertices(a, b) {
  return [
    { x: a.x, y: a.y },
    { x: b.x, y: a.y },
    { x: b.x, y: b.y },
    { x: a.x, y: b.y }
  ];
}

function _loadZoneMapMeta(name, done) {
  var base = _zoneBaseName(name);
  if (!base) return;
  fetch(SERVER_URL + "/map_metadata/" + base)
    .then(function(r) { return r.json(); })
    .then(function(meta) {
      if (!meta || meta.status === "error") throw new Error(meta.message || "metadata failed");
      zoneMapMeta = meta;
      if (done) done(meta);
    })
    .catch(function(e) {
      zoneMapMeta = null;
      _zoneToast("Map metadata unavailable: " + e.message, "error");
    });
}

function initZoneEditor() {
  _zoneToast("Add zones from Edit Map only.", "info");
}

function _updateZoneButtonUI() {
  var btnDraw = document.getElementById("btn-zone-draw");
  var btnN = document.getElementById("btn-zone-nogo");
  var btnL = document.getElementById("btn-zone-lane");
  var btnS = document.getElementById("btn-zone-slow");
  
  if (btnDraw) {
    btnDraw.style.background = mapEditorZoneMode ? "rgba(14,165,233,0.35)" : "rgba(14,165,233,0.12)";
    btnDraw.style.borderColor = mapEditorZoneMode ? "#0ea5e9" : "rgba(14,165,233,0.5)";
    btnDraw.style.color = mapEditorZoneMode ? "#38bdf8" : "#7dd3fc";
    btnDraw.style.boxShadow = mapEditorZoneMode ? "0 0 10px rgba(14,165,233,0.45)" : "none";
  }
  
  if (btnN) {
    var active = mapEditorZoneMode && currentZoneType === "no-go";
    btnN.style.background = active ? "#ef4444" : "rgba(239,68,68,0.12)";
    btnN.style.color = active ? "#ffffff" : "#fecaca";
    btnN.style.borderColor = active ? "#ef4444" : "#ef4444";
    btnN.style.boxShadow = active ? "0 0 10px rgba(239,68,68,0.5)" : "none";
    btnN.style.fontWeight = active ? "800" : "700";
  }
  
  if (btnL) {
    var active = mapEditorZoneMode && currentZoneType === "lane";
    btnL.style.background = active ? "#22c55e" : "rgba(34,197,94,0.12)";
    btnL.style.color = active ? "#ffffff" : "#bbf7d0";
    btnL.style.borderColor = active ? "#22c55e" : "#22c55e";
    btnL.style.boxShadow = active ? "0 0 10px rgba(34,197,94,0.5)" : "none";
    btnL.style.fontWeight = active ? "800" : "700";
  }
  
  if (btnS) {
    var active = mapEditorZoneMode && currentZoneType === "slow";
    btnS.style.background = active ? "#f59e0b" : "rgba(245,158,11,0.12)";
    btnS.style.color = active ? "#ffffff" : "#fde68a";
    btnS.style.borderColor = active ? "#f59e0b" : "#f59e0b";
    btnS.style.boxShadow = active ? "0 0 10px rgba(245,158,11,0.5)" : "none";
    btnS.style.fontWeight = active ? "800" : "700";
  }
}

function initMapEditorZoneEditor() {
  var canvas = document.getElementById("map-edit-canvas");
  var overlay = document.getElementById("map-zone-overlay");
  var select = document.getElementById("edit-map-select");
  if (!canvas || !overlay || !select || !select.value) {
    _zoneToast("Open a map in Edit Map first", "error");
    return;
  }
  if (mapEditorZoneMode) {
    disableZoneEditor();
    return;
  }
  mapName = _zoneBaseName(select.value);
  zoneEditorMode = true;
  mapEditorZoneMode = true;
  window.mapEditorZoneMode = true;
  selectedZoneIdx = -1;
  drawingPolygon = [];
  zoneDragStart = null;
  overlay.style.pointerEvents = "auto";
  overlay.style.cursor = "crosshair";
  overlay.style.zIndex = "20";
  _loadZoneMapMeta(mapName, function() {
    loadZones();
    _attachMapEditorZoneEvents();
    _updateZoneButtonUI();
    _zoneToast("Zone drawing active. Click & drag to draw!", "info");
  });
}

function onMapEditorCanvasLoaded(name) {
  mapName = _zoneBaseName(name);
  window.mapEditorZoneMode = false;
  mapEditorZoneMode = false;
  _loadZoneMapMeta(mapName, function() {
    loadZones();
    _attachMapEditorZoneEvents();
    _updateZoneButtonUI();
  });
}

function disableZoneEditor() {
  zoneEditorMode = false;
  mapEditorZoneMode = false;
  window.mapEditorZoneMode = false;
  drawingPolygon = [];
  zoneDragStart = null;
  selectedZoneIdx = -1;
  isDrawingZone = false;
  isMovingZone = false;
  moveStartPos = null;
  if (typeof mapCanvas !== "undefined" && mapCanvas) mapCanvas.style.cursor = "default";
  var zoneOverlay = document.getElementById("map-zone-overlay");
  if (zoneOverlay) {
    zoneOverlay.style.pointerEvents = "none";
    zoneOverlay.style.cursor = "default";
  }
  redrawZones();
  renderMapEditorZones();
  _updateZoneButtonUI();
  _zoneToast("Zone editor disabled", "info");
}

function loadZones() {
  if (!mapName) return;
  fetch(SERVER_URL + "/zones/load/" + mapName)
    .then(function(r) { return r.json(); })
    .then(function(d) {
      zones = d.zones || [];
      redrawZones();
      renderMapEditorZones();
      updateZoneList();
    })
    .catch(function() {
      zones = [];
      redrawZones();
      renderMapEditorZones();
      updateZoneList();
    });
}

function saveZones() {
  if (!mapName) {
    _zoneToast("No map selected", "error");
    return;
  }
  fetch(SERVER_URL + "/zones/save", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ map: mapName, zones: zones })
  })
    .then(function(r) { return r.json(); })
    .then(function(d) {
      if (d.status === "saved") {
        _zoneToast("Zones saved and masks generated", "success");
        reloadCostmapFilters();
      } else {
        _zoneToast(d.error || "Save failed", "error");
      }
    })
    .catch(function(e) { _zoneToast("Save error: " + e, "error"); });
}

function reloadCostmapFilters() {
  fetch(SERVER_URL + "/zones/reload_costmap", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ map: mapName })
  })
    .then(function(r) { return r.json(); })
    .then(function(d) {
      if (d.status === "reloaded") _zoneToast("Costmap filters reloaded", "success");
      else _zoneToast("Reload status: " + (d.message || "unknown"), "info");
    })
    .catch(function() { _zoneToast("Could not reload costmap", "error"); });
}

function setZoneType(type) {
  if (!ZONE_COLORS[type]) return;
  currentZoneType = type;
  
  /* Automatically activate zone editor mode if not active */
  if (!mapEditorZoneMode) {
    initMapEditorZoneEditor();
  } else {
    _updateZoneButtonUI();
  }
  
  var btn = document.getElementById("zone-type-btn");
  if (btn) {
    btn.textContent = ZONE_COLORS[type].label;
    btn.style.borderColor = ZONE_COLORS[type].stroke;
    btn.style.color = ZONE_COLORS[type].stroke;
  }
  _zoneToast("Zone type: " + ZONE_COLORS[type].label, "info");
}

function _canvasRosPoint(e) {
  var pt = _zonePointer(e);
  return canvasToRos(pt.clientX, pt.clientY);
}

if (typeof mapCanvas !== "undefined" && mapCanvas) {
  mapCanvas.addEventListener("mousedown", _onLiveZoneDown);
  mapCanvas.addEventListener("touchstart", _onLiveZoneDown, { passive: false });
  mapCanvas.addEventListener("mousemove", _onLiveZoneMove);
  mapCanvas.addEventListener("touchmove", _onLiveZoneMove, { passive: false });
  mapCanvas.addEventListener("mouseup", _onLiveZoneUp);
  mapCanvas.addEventListener("touchend", _onLiveZoneUp);
  mapCanvas.addEventListener("touchcancel", _onLiveZoneUp);
}

function _onLiveZoneDown(e) {
  if (!zoneEditorMode || mapEditorZoneMode || mapMode !== "localization") return;
  if (poseEstimateMode || goalPoseMode) return;
  e.preventDefault();
  var pos = _canvasRosPoint(e);
  var clicked = getZoneAtPoint(pos.x, pos.y);
  if (clicked >= 0) {
    selectedZoneIdx = clicked;
    isMovingZone = true;
    moveStartPos = pos;
  } else {
    selectedZoneIdx = -1;
    isDrawingZone = true;
    drawingPolygon = [pos];
  }
  redrawZones();
  updateZoneList();
}

function _onLiveZoneMove(e) {
  if (!zoneEditorMode || mapEditorZoneMode) return;
  if (!isDrawingZone && !isMovingZone) return;
  e.preventDefault();
  var pos = _canvasRosPoint(e);
  if (isMovingZone && selectedZoneIdx >= 0) {
    _moveSelectedZone(pos);
  } else if (isDrawingZone) {
    _maybeAddDrawPoint(pos);
  }
  redrawZones();
}

function _onLiveZoneUp(e) {
  if (!zoneEditorMode || mapEditorZoneMode) return;
  if (isDrawingZone) _finishDrawnZone();
  isDrawingZone = false;
  isMovingZone = false;
  moveStartPos = null;
}

function _moveSelectedZone(pos) {
  if (!moveStartPos || selectedZoneIdx < 0 || !zones[selectedZoneIdx]) return;
  var dx = pos.x - moveStartPos.x;
  var dy = pos.y - moveStartPos.y;
  zones[selectedZoneIdx].vertices.forEach(function(v) {
    v.x += dx;
    v.y += dy;
  });
  moveStartPos = pos;
}

function _finishDrawnZone() {
  if ((currentZoneType === "lane" && drawingPolygon.length >= 2) ||
      (currentZoneType !== "lane" && drawingPolygon.length >= 3)) {
    var zone = { type: currentZoneType, vertices: _cloneVertices(drawingPolygon) };
    if (currentZoneType === "lane") zone.width = _zoneWidthMeters();
    zones.push(zone);
    selectedZoneIdx = zones.length - 1;
    _zoneToast(ZONE_COLORS[currentZoneType].label + " zone created", "success");
  }
  drawingPolygon = [];
  zoneDragStart = null;
  updateZoneList();
  redrawZones();
  renderMapEditorZones();
}

function redrawZones() {
  if (typeof stage === "undefined" || typeof rootObject === "undefined" || !stage || !rootObject) return;
  if (zoneDrawLayer && rootObject.contains(zoneDrawLayer)) {
    rootObject.removeChild(zoneDrawLayer);
  }
  if (typeof mapMode !== "undefined" && mapMode !== "localization") {
    zoneDrawLayer = null;
    stage.update();
    return;
  }
  zoneDrawLayer = new createjs.Container();
  rootObject.addChild(zoneDrawLayer);

  zones.forEach(function(zone, idx) {
    _drawLiveZone(zone, idx === selectedZoneIdx);
  });
  if (drawingPolygon.length) {
    _drawLiveZone({ type: currentZoneType, vertices: drawingPolygon }, true, true);
  }
  stage.update();
}

function _drawLiveZone(zone, selected, preview) {
  if (!zone.vertices || zone.vertices.length < 2) return;
  var color = ZONE_COLORS[zone.type] || ZONE_COLORS["no-go"];
  var shape = new createjs.Shape();
  shape.alpha = preview ? 0.82 : 1;
  shape.graphics
    .setStrokeStyle(selected ? 0.08 : 0.04)
    .beginStroke(selected ? "#f8fafc" : color.stroke)
    .beginFill(color.fill);
  zone.vertices.forEach(function(v, i) {
    if (i === 0) shape.graphics.moveTo(v.x, -v.y);
    else shape.graphics.lineTo(v.x, -v.y);
  });
  if (zone.vertices.length >= 3) shape.graphics.closePath();
  zoneDrawLayer.addChild(shape);

}

function _ensureDimCanvas() {
  if (_dimCanvas) return;
  var container = document.getElementById("edit-canvas-container");
  if (!container) return;
  _dimCanvas = document.createElement("canvas");
  _dimCanvas.style.cssText = "position:absolute;top:0;left:0;width:100%;height:100%;pointer-events:none;z-index:19;opacity:0;transition:opacity 0.18s ease;";
  container.appendChild(_dimCanvas);
}

function _showDim(visible) {
  _ensureDimCanvas();
  if (!_dimCanvas) return;
  var container = document.getElementById("edit-canvas-container");
  if (container) {
    _dimCanvas.width  = container.clientWidth  || 800;
    _dimCanvas.height = container.clientHeight || 600;
  }
  if (visible) {
    var ctx = _dimCanvas.getContext("2d");
    ctx.clearRect(0, 0, _dimCanvas.width, _dimCanvas.height);
    ctx.fillStyle = "rgba(0,0,0,0.45)";
    ctx.fillRect(0, 0, _dimCanvas.width, _dimCanvas.height);
    _dimCanvas.style.opacity = "1";
  } else {
    _dimCanvas.style.opacity = "0";
  }
}

function _stopLerp() {
  if (_lerpAnimId) { cancelAnimationFrame(_lerpAnimId); _lerpAnimId = null; }
  _lerpCurrent = null;
  _lerpTarget  = null;
}

function _startLerp(startPx) {
  _lerpCurrent = { x: startPx.x, y: startPx.y };
  _lerpTarget  = { x: startPx.x, y: startPx.y };
  function _tick() {
    if (!_lerpCurrent || !_lerpTarget || !isDrawingZone) return;
    _lerpCurrent.x += (_lerpTarget.x - _lerpCurrent.x) * LERP_SPEED;
    _lerpCurrent.y += (_lerpTarget.y - _lerpCurrent.y) * LERP_SPEED;
    /* Convert lerped pixel back to world coords and update polygon */
    if (zoneDragStart && zoneMapMeta) {
      var canvas = document.getElementById("map-edit-canvas");
      if (canvas) {
        var container = document.getElementById("edit-canvas-container");
        var cRect = container ? container.getBoundingClientRect() : null;
        /* _lerpCurrent is in container pixels — convert to world */
        var worldPt = _containerPxToWorld(_lerpCurrent);
        if (worldPt) {
          if (currentZoneType === "lane") {
            drawingPolygon = [zoneDragStart, worldPt];
          } else {
            drawingPolygon = _rectVertices(zoneDragStart, worldPt);
          }
          renderMapEditorZones();
        }
      }
    }
    _lerpAnimId = requestAnimationFrame(_tick);
  }
  _lerpAnimId = requestAnimationFrame(_tick);
}

/* Convert container-local pixel {x,y} → world ROS coords */
function _containerPxToWorld(px) {
  if (!zoneMapMeta) return null;
  var canvas = document.getElementById("map-edit-canvas");
  if (!canvas) return null;
  var rect = canvas.getBoundingClientRect();
  /* rect is already in viewport coords; px is in container coords */
  var container = document.getElementById("edit-canvas-container");
  var cRect = container ? container.getBoundingClientRect() : { left: 0, top: 0 };
  var clientX = px.x + cRect.left;
  var clientY = px.y + cRect.top;
  var mapPx = (clientX - rect.left) / (rect.width  / zoneMapMeta.width);
  var mapPy = (clientY - rect.top)  / (rect.height / zoneMapMeta.height);
  return {
    x: zoneMapMeta.origin[0] + mapPx * zoneMapMeta.resolution,
    y: zoneMapMeta.origin[1] + (zoneMapMeta.height - 1 - mapPy) * zoneMapMeta.resolution
  };
}

function _attachMapEditorZoneEvents() {
  var overlay = document.getElementById("map-zone-overlay");
  if (!overlay || mapEditorZoneEventsAttached) return;
  mapEditorZoneEventsAttached = true;

  overlay.addEventListener("mousedown", _onMapEditorZoneDown);
  overlay.addEventListener("mousemove", _onMapEditorZoneMove);
  window.addEventListener("mouseup", _onMapEditorZoneUp);
}

function _mapEditorPoint(e) {
  var canvas = document.getElementById("map-edit-canvas");
  if (!canvas || !zoneMapMeta) return null;
  var rect = canvas.getBoundingClientRect();
  var px = (e.clientX - rect.left) / (rect.width / zoneMapMeta.width);
  var py = (e.clientY - rect.top) / (rect.height / zoneMapMeta.height);
  return {
    x: zoneMapMeta.origin[0] + px * zoneMapMeta.resolution,
    y: zoneMapMeta.origin[1] + (zoneMapMeta.height - 1 - py) * zoneMapMeta.resolution
  };
}

function _worldToMapEditorPixel(v) {
  return {
    x: (v.x - zoneMapMeta.origin[0]) / zoneMapMeta.resolution,
    y: zoneMapMeta.height - 1 - ((v.y - zoneMapMeta.origin[1]) / zoneMapMeta.resolution)
  };
}

function _onMapEditorZoneDown(e) {
  if (!zoneEditorMode || !mapEditorZoneMode || !zoneMapMeta) return;
  e.preventDefault();
  var pos = _mapEditorPoint(e);
  var clicked = getZoneAtPoint(pos.x, pos.y);
  if (clicked >= 0) {
    selectedZoneIdx = clicked;
    isMovingZone = true;
    moveStartPos = pos;
    _stopLerp();
  } else {
    selectedZoneIdx = -1;
    isDrawingZone = true;
    zoneDragStart = pos;
    drawingPolygon = [pos];
    /* Start LERP from click pixel position */
    var container = document.getElementById("edit-canvas-container");
    var cRect = container ? container.getBoundingClientRect() : { left: 0, top: 0 };
    var startPx = { x: e.clientX - cRect.left, y: e.clientY - cRect.top };
    _startLerp(startPx);
    _showDim(true);
  }
  renderMapEditorZones();
  updateZoneList();
}

function _onMapEditorZoneMove(e) {
  if (!zoneEditorMode || !mapEditorZoneMode || !zoneMapMeta) return;
  if (!isDrawingZone && !isMovingZone) return;
  e.preventDefault();
  var pos = _mapEditorPoint(e);
  if (isMovingZone && selectedZoneIdx >= 0) {
    _moveSelectedZone(pos);
    renderMapEditorZones();
  } else if (isDrawingZone && _lerpTarget) {
    /* Update lerp target — rAF loop does the actual polygon update */
    var container = document.getElementById("edit-canvas-container");
    var cRect = container ? container.getBoundingClientRect() : { left: 0, top: 0 };
    _lerpTarget.x = e.clientX - cRect.left;
    _lerpTarget.y = e.clientY - cRect.top;
  }
}

function _onMapEditorZoneUp() {
  if (!zoneEditorMode || !mapEditorZoneMode) return;
  _stopLerp();
  _showDim(false);
  if (isDrawingZone) _finishDrawnZone();
  isDrawingZone = false;
  isMovingZone = false;
  moveStartPos = null;
  renderMapEditorZones();
}

function renderMapEditorZones() {
  var overlay = document.getElementById("map-zone-overlay");
  if (!overlay || !zoneMapMeta) return;
  var ctx = overlay.getContext("2d");
  ctx.clearRect(0, 0, overlay.width, overlay.height);
  zones.forEach(function(zone, idx) {
    _drawMapEditorZone(ctx, zone, idx === selectedZoneIdx, false);
  });
  if (drawingPolygon.length) {
    _drawMapEditorZone(ctx, { type: currentZoneType, vertices: drawingPolygon }, true, true);
  }
  _updateFloatingToolbar();
}

function _drawMapEditorZone(ctx, zone, selected, preview) {
  if (!zone.vertices || zone.vertices.length < 2) return;
  var color = ZONE_COLORS[zone.type] || ZONE_COLORS["no-go"];
  ctx.save();
  ctx.lineCap  = "round";
  ctx.lineJoin = "round";

  /* ── Preferred Lane: thick green path ── */
  if (zone.type === "lane") {
    var laneWidth = Math.max(6, Math.round((zone.width || _zoneWidthMeters()) / zoneMapMeta.resolution));

    /* Glow / shadow layer */
    if (preview || selected) {
      ctx.globalAlpha = 0.25;
      ctx.strokeStyle = "#22c55e";
      ctx.lineWidth   = laneWidth + 8;
      ctx.beginPath();
      zone.vertices.forEach(function(v, i) {
        var p = _worldToMapEditorPixel(v);
        if (i === 0) ctx.moveTo(p.x, p.y); else ctx.lineTo(p.x, p.y);
      });
      ctx.stroke();
    }

    /* Main green band */
    ctx.globalAlpha = preview ? 0.55 : (selected ? 0.9 : 0.72);
    ctx.strokeStyle = "#22c55e";
    ctx.lineWidth   = laneWidth;
    ctx.beginPath();
    zone.vertices.forEach(function(v, i) {
      var p = _worldToMapEditorPixel(v);
      if (i === 0) ctx.moveTo(p.x, p.y); else ctx.lineTo(p.x, p.y);
    });
    ctx.stroke();

    /* Centre dashed line for directionality */
    ctx.globalAlpha = 0.95;
    ctx.strokeStyle = selected ? "#ffffff" : "#bbf7d0";
    ctx.lineWidth   = Math.max(1, laneWidth * 0.18);
    ctx.setLineDash([8, 6]);
    ctx.beginPath();
    zone.vertices.forEach(function(v, i) {
      var p = _worldToMapEditorPixel(v);
      if (i === 0) ctx.moveTo(p.x, p.y); else ctx.lineTo(p.x, p.y);
    });
    ctx.stroke();
    ctx.restore();
    return;
  }

  /* ── No-Go / Slow Zone: transparent colored rect ── */
  /* Choose fill colour based on type */
  var fillAlpha = preview ? 0.28 : (selected ? 0.42 : 0.22);
  if (zone.type === "no-go") {
    ctx.fillStyle = "rgba(239,68,68," + fillAlpha + ")";
    ctx.strokeStyle = preview ? "#fca5a5" : (selected ? "#ffffff" : "#ef4444");
  } else {
    /* slow */
    ctx.fillStyle = "rgba(245,158,11," + fillAlpha + ")";
    ctx.strokeStyle = preview ? "#fde68a" : (selected ? "#ffffff" : "#f59e0b");
  }

  ctx.globalAlpha = 1;
  ctx.beginPath();
  zone.vertices.forEach(function(v, i) {
    var p = _worldToMapEditorPixel(v);
    if (i === 0) ctx.moveTo(p.x, p.y); else ctx.lineTo(p.x, p.y);
  });
  if (zone.vertices.length >= 3) {
    ctx.closePath();
    ctx.fill();
  }

  ctx.lineWidth = selected ? 2.5 : 1.8;
  ctx.setLineDash(preview ? [10, 6] : []);

  /* Subtle glow for the active preview box */
  if (preview) {
    ctx.shadowColor = ctx.strokeStyle;
    ctx.shadowBlur  = 8;
  }
  ctx.stroke();
  ctx.restore();
}

function getZoneAtPoint(x, y) {
  for (var i = zones.length - 1; i >= 0; i--) {
    if (zones[i].type === "lane" && pointNearPolyline(x, y, zones[i].vertices, zones[i].width || _zoneWidthMeters())) {
      return i;
    }
    if (pointInPolygon(x, y, zones[i].vertices)) return i;
  }
  return -1;
}

function pointNearPolyline(x, y, vertices, width) {
  if (!vertices || vertices.length < 2) return false;
  var maxDist = Math.max(width / 2, zoneMapMeta ? zoneMapMeta.resolution * 4 : 0.1);
  for (var i = 1; i < vertices.length; i++) {
    if (_pointSegmentDistance(x, y, vertices[i - 1], vertices[i]) <= maxDist) return true;
  }
  return false;
}

function _pointSegmentDistance(x, y, a, b) {
  var dx = b.x - a.x;
  var dy = b.y - a.y;
  if (dx === 0 && dy === 0) return Math.hypot(x - a.x, y - a.y);
  var t = ((x - a.x) * dx + (y - a.y) * dy) / (dx * dx + dy * dy);
  t = Math.max(0, Math.min(1, t));
  return Math.hypot(x - (a.x + t * dx), y - (a.y + t * dy));
}

function pointInPolygon(x, y, vertices) {
  var inside = false;
  for (var i = 0, j = vertices.length - 1; i < vertices.length; j = i++) {
    var xi = vertices[i].x;
    var yi = vertices[i].y;
    var xj = vertices[j].x;
    var yj = vertices[j].y;
    if (((yi > y) !== (yj > y)) &&
        (x < (xj - xi) * (y - yi) / (yj - yi) + xi)) {
      inside = !inside;
    }
  }
  return inside;
}

function getPolygonCentroid(vertices) {
  var cx = 0;
  var cy = 0;
  vertices.forEach(function(v) {
    cx += v.x;
    cy += v.y;
  });
  return { x: cx / vertices.length, y: cy / vertices.length };
}

function updateZoneList() {
  var list = document.getElementById("zone-list");
  if (!list) return;
  list.innerHTML = "";
  zones.forEach(function(zone, i) {
    var color = ZONE_COLORS[zone.type] || ZONE_COLORS["no-go"];
    var item = document.createElement("div");
    item.style.cssText = "padding:8px;margin:4px 0;background:rgba(255,255,255,0.05);border-radius:4px;border-left:3px solid " + color.stroke + ";";
    item.innerHTML =
      "<div style='display:flex;justify-content:space-between;align-items:center;gap:8px;'>" +
      "<span onclick='selectZone(" + i + ")' style='cursor:pointer;flex:1;'>" +
      "<strong>" + color.label + "</strong> (" + zone.vertices.length + " pts)</span>" +
      "<button onclick='deleteZone(" + i + ")' style='padding:4px 8px;background:rgba(239,68,68,0.3);border:1px solid #ef4444;color:#ef4444;border-radius:4px;cursor:pointer;font-size:11px;'>Delete</button>" +
      "</div>";
    list.appendChild(item);
  });
}

function selectZone(idx) {
  selectedZoneIdx = idx;
  redrawZones();
  renderMapEditorZones();
}

function deleteZone(idx) {
  if (idx < 0 || idx >= zones.length) return;
  zones.splice(idx, 1);
  selectedZoneIdx = -1;
  updateZoneList();
  redrawZones();
  renderMapEditorZones();
  _zoneToast("Zone deleted", "success");
}

function clearAllZones() {
  if (!confirm("Delete all zones on this map?")) return;
  zones = [];
  selectedZoneIdx = -1;
  updateZoneList();
  redrawZones();
  renderMapEditorZones();
  _zoneToast("All zones cleared", "success");
}

/* ==========================================================================
   FLOATING ACTION TOOLBAR - DUPLICATE, RESIZE/SCALE, ROTATE/TILT, DELETE
   ========================================================================== */

function _updateFloatingToolbar() {
  var container = document.getElementById("edit-canvas-container");
  if (!container) return;
  
  var toolbar = document.getElementById("zone-actions-floating");
  if (!toolbar) {
    toolbar = document.createElement("div");
    toolbar.id = "zone-actions-floating";
    toolbar.style.cssText = "position:absolute; display:none; gap:6px; background:#1e293b; border:1.5px solid #38bdf8; padding:5px 9px; border-radius:8px; z-index:100; box-shadow:0 10px 30px rgba(0,0,0,0.6); align-items:center; pointer-events:auto; font-family:sans-serif; user-select:none; transition: top 0.1s ease, left 0.1s ease;";
    
    toolbar.innerHTML = 
      "<span style='font-size:10px; color:#38bdf8; font-weight:800; padding:0 6px 0 2px; border-right:1px solid #334155; margin-right:4px; letter-spacing:1px;'>ZONE</span>" +
      // Duplicate
      "<button onclick='duplicateSelectedZone()' title='Duplicate Zone' style='width:30px; height:30px; padding:0; display:inline-flex; align-items:center; justify-content:center; background:#0284c7; border:none; border-radius:5px; color:#fff; cursor:pointer; font-size:13px; margin:0 2px; transition:background 0.1s;'>👥</button>" +
      // Scale Up (Resize +)
      "<button onclick='scaleSelectedZone(1.1)' title='Scale Up' style='width:30px; height:30px; padding:0; display:inline-flex; align-items:center; justify-content:center; background:#334155; border:1px solid #475569; border-radius:5px; color:#fff; cursor:pointer; font-size:13px; margin:0 2px; transition:background 0.1s;'>➕</button>" +
      // Scale Down (Resize -)
      "<button onclick='scaleSelectedZone(0.9)' title='Scale Down' style='width:30px; height:30px; padding:0; display:inline-flex; align-items:center; justify-content:center; background:#334155; border:1px solid #475569; border-radius:5px; color:#fff; cursor:pointer; font-size:13px; margin:0 2px; transition:background 0.1s;'>➖</button>" +
      // Rotate Left (Tilt Left)
      "<button onclick='rotateSelectedZone(-15)' title='Tilt Left 15°' style='width:30px; height:30px; padding:0; display:inline-flex; align-items:center; justify-content:center; background:#334155; border:1px solid #475569; border-radius:5px; color:#fff; cursor:pointer; font-size:13px; margin:0 2px; transition:background 0.1s;'>⟲</button>" +
      // Rotate Right (Tilt Right)
      "<button onclick='rotateSelectedZone(15)' title='Tilt Right 15°' style='width:30px; height:30px; padding:0; display:inline-flex; align-items:center; justify-content:center; background:#334155; border:1px solid #475569; border-radius:5px; color:#fff; cursor:pointer; font-size:13px; margin:0 2px; transition:background 0.1s;'>⟳</button>" +
      // Delete
      "<button onclick='deleteSelectedZoneBtn()' title='Delete Zone' style='width:30px; height:30px; padding:0; display:inline-flex; align-items:center; justify-content:center; background:#dc2626; border:none; border-radius:5px; color:#fff; cursor:pointer; font-size:13px; margin:0 2px; transition:background 0.1s;'>🗑</button>";
      
    // Add hover effects dynamically
    var btns = toolbar.getElementsByTagName("button");
    for (var i = 0; i < btns.length; i++) {
      (function(b) {
        var origBg = b.style.background;
        b.onmouseover = function() { b.style.background = b.title === "Delete Zone" ? "#b91c1c" : (b.title === "Duplicate Zone" ? "#0369a1" : "#475569"); };
        b.onmouseout = function() { b.style.background = origBg; };
      })(btns[i]);
    }
    
    container.appendChild(toolbar);
  }
  
  if (selectedZoneIdx < 0 || selectedZoneIdx >= zones.length || !zoneMapMeta) {
    toolbar.style.display = "none";
    return;
  }
  
  var zone = zones[selectedZoneIdx];
  if (!zone.vertices || !zone.vertices.length) {
    toolbar.style.display = "none";
    return;
  }
  
  // Calculate bounding box in world space vertices
  var minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
  zone.vertices.forEach(function(v) {
    var p = _worldToMapEditorPixel(v);
    if (p.x < minX) minX = p.x;
    if (p.x > maxX) maxX = p.x;
    if (p.y < minY) minY = p.y;
    if (p.y > maxY) maxY = p.y;
  });
  
  // Transform bounding box to current zoom and pan coords
  var zoom = typeof window._mapEditorZoom !== "undefined" ? window._mapEditorZoom : 1.0;
  var panX = typeof window._mapEditorPanX !== "undefined" ? window._mapEditorPanX : 0.0;
  var panY = typeof window._mapEditorPanY !== "undefined" ? window._mapEditorPanY : 0.0;
  
  var leftPx = panX + minX * zoom;
  var topPx = panY + minY * zoom;
  var widthPx = (maxX - minX) * zoom;
  
  toolbar.style.display = "flex";
  
  // Bounding calculations for the floating toolbar
  var toolbarWidth = 248;
  var centerX = leftPx + (widthPx / 2) - (toolbarWidth / 2);
  var centerY = topPx - 48; // Position toolbar slightly above bounding box
  
  centerX = Math.max(10, Math.min(container.clientWidth - toolbarWidth - 10, centerX));
  centerY = Math.max(10, Math.min(container.clientHeight - 50, centerY));
  
  toolbar.style.left = centerX + "px";
  toolbar.style.top = centerY + "px";
}

// Hook map editor transform zoom/pan events
window._onMapEditorTransform = function() {
  _updateFloatingToolbar();
};

window.duplicateSelectedZone = function() {
  if (selectedZoneIdx < 0 || selectedZoneIdx >= zones.length) return;
  var srcZone = zones[selectedZoneIdx];
  
  // Shift new zone by ~15 pixels in resolution space
  var offset = zoneMapMeta ? zoneMapMeta.resolution * 15 : 0.5;
  var newVertices = srcZone.vertices.map(function(v) {
    return { x: v.x + offset, y: v.y + offset };
  });
  
  var newZone = {
    type: srcZone.type,
    vertices: newVertices
  };
  if (srcZone.width) newZone.width = srcZone.width;
  
  zones.push(newZone);
  selectedZoneIdx = zones.length - 1;
  
  _zoneToast("Zone duplicated", "success");
  redrawZones();
  renderMapEditorZones();
  updateZoneList();
  _updateFloatingToolbar();
};

window.scaleSelectedZone = function(factor) {
  if (selectedZoneIdx < 0 || selectedZoneIdx >= zones.length) return;
  var zone = zones[selectedZoneIdx];
  if (!zone.vertices || !zone.vertices.length) return;
  
  var centroid = getPolygonCentroid(zone.vertices);
  
  zone.vertices.forEach(function(v) {
    v.x = centroid.x + (v.x - centroid.x) * factor;
    v.y = centroid.y + (v.y - centroid.y) * factor;
  });
  
  _zoneToast("Zone scaled", "success");
  redrawZones();
  renderMapEditorZones();
  _updateFloatingToolbar();
};

window.rotateSelectedZone = function(angleDegrees) {
  if (selectedZoneIdx < 0 || selectedZoneIdx >= zones.length) return;
  var zone = zones[selectedZoneIdx];
  if (!zone.vertices || !zone.vertices.length) return;
  
  var centroid = getPolygonCentroid(zone.vertices);
  var rad = (angleDegrees * Math.PI) / 180;
  var cos = Math.cos(rad);
  var sin = Math.sin(rad);
  
  zone.vertices.forEach(function(v) {
    var dx = v.x - centroid.x;
    var dy = v.y - centroid.y;
    v.x = centroid.x + (dx * cos - dy * sin);
    v.y = centroid.y + (dx * sin + dy * cos);
  });
  
  _zoneToast("Zone rotated", "success");
  redrawZones();
  renderMapEditorZones();
  _updateFloatingToolbar();
};

window.deleteSelectedZoneBtn = function() {
  if (selectedZoneIdx < 0 || selectedZoneIdx >= zones.length) return;
  deleteZone(selectedZoneIdx);
  _updateFloatingToolbar();
};

document.addEventListener("keydown", function(e) {
  if (!zoneEditorMode) return;
  if (e.key === "Escape") {
    drawingPolygon = [];
    isDrawingZone = false;
    isMovingZone = false;
    redrawZones();
    renderMapEditorZones();
  } else if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "z") {
    e.preventDefault();
    if (drawingPolygon.length) drawingPolygon.pop();
    else if (selectedZoneIdx >= 0) deleteZone(selectedZoneIdx);
    else zones.pop();
    redrawZones();
    renderMapEditorZones();
    updateZoneList();
  } else if ((e.key === "Delete" || e.key === "Backspace") && selectedZoneIdx >= 0) {
    e.preventDefault();
    deleteZone(selectedZoneIdx);
  }
});
