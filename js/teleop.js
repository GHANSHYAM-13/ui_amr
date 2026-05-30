/* ==========================================================================
   teleop.js — Robot teleoperation (buttons + keyboard)
   Depends on: app.js (cmdVel, showToast, ros)

   KEY DESIGN: motor.py has a 0.5s watchdog — if no /cmd_vel arrives in
   0.5s it stops the robot. We must publish CONTINUOUSLY at 100ms (10 Hz)
   while a button is held. On release we simply stop publishing — motor.py
   watchdog handles the physical stop automatically.
   ========================================================================== */


/* ---------- SPEED HELPERS ---------- */
function getLinearSpeed() {
  var el = document.getElementById("speed-linear");
  return el ? (parseFloat(el.value) || 0.4) : 0.4;
}
function getAngularSpeed() {
  var el = document.getElementById("speed-angular");
  return el ? (parseFloat(el.value) || 0.8) : 0.8;
}


/* ---------- VELOCITY PUBLISHER ---------- */
function publishVel(linX, angZ) {
  if (!cmdVel) return;   /* ROS not connected yet — silently ignore */
  cmdVel.publish(new ROSLIB.Message({
    linear: { x: linX, y: 0.0, z: 0.0 },
    angular: { x: 0.0, y: 0.0, z: angZ }
  }));
}
function stop() { publishVel(0, 0); }


/* ---------- CONTINUOUS PUBLISHING WHILE BUTTON HELD -------------------
 * _velInterval fires every 100 ms while a direction button is held.
 * This keeps motor.py watchdog (0.5 s) fed so robot never stops mid-press.
 * On release: clearInterval → stop publishing → motor.py stops robot.
 * ---------------------------------------------------------------------- */
var _velInterval = null;   /* active interval handle     */
var _velLinX = 0;      /* current linear  velocity   */
var _velAngZ = 0;      /* current angular velocity   */

/* ---------- MISSION TELEOP LOCK ----------------------------------------
 * While a mission is running, Nav2's controller_server owns cmd_vel.
 * Any simultaneous teleop publish (even a residual 100ms interval tick)
 * creates conflicting velocity commands → robot spins, jerks, or stops mid-path.
 * We expose two functions that mission.js calls to lock/unlock teleop.
 * ----------------------------------------------------------------------- */
var _teleopLocked = false;

function lockTeleop() {
  _teleopLocked = true;
  _stopContinuous();   /* kill any live interval immediately */
}
function unlockTeleop() {
  _teleopLocked = false;
}

function _startContinuous(linX, angZ) {
  if (_teleopLocked) return;   /* mission running — ignore all teleop */
  _velLinX = linX;
  _velAngZ = angZ;
  publishVel(_velLinX, _velAngZ);
  if (!_velInterval) {
    _velInterval = setInterval(function () {
      if (_teleopLocked) { _stopContinuous(); return; }
      publishVel(_velLinX, _velAngZ);
    }, 100);
  }
}

function _stopContinuous() {
  /* Stop publishing — motor.py watchdog handles physical stop */
  if (_velInterval) {
    clearInterval(_velInterval);
    _velInterval = null;
  }
  _velLinX = 0;
  _velAngZ = 0;
  /* Do NOT send stop() — motor.py watchdog is the stop mechanism */
}


/* ---------- DIRECTION FUNCTIONS (called by buttons + keyboard) --------- */
function forward() { _startContinuous(getLinearSpeed(), 0); }
function back() { _startContinuous(-getLinearSpeed(), 0); }
function left() { _startContinuous(0, getAngularSpeed()); }
function right() { _startContinuous(0, -getAngularSpeed()); }

/* Hard stop — used by stop button (■) and spacebar */
function stopHard() {
  if (_teleopLocked) return;
  _stopContinuous();
  stop();   /* send explicit zero once for immediate stop */
}

/* Button release handler — just stop publishing, let motor.py watchdog act */
function releaseButton() { _stopContinuous(); }


/* ---------- KEYBOARD TELEOP -------------------------------------------- */
var keysDown = {};

document.addEventListener("keydown", function (e) {
  var tag = document.activeElement && document.activeElement.tagName;
  if (tag === "INPUT" || tag === "SELECT" || tag === "TEXTAREA") return;
  if (keysDown[e.key]) return;   /* suppress browser key-repeat */
  keysDown[e.key] = true;

  switch (e.key) {
    case "ArrowUp": case "w": case "W": forward(); break;
    case "ArrowDown": case "s": case "S": back(); break;
    case "ArrowLeft": case "a": case "A": left(); break;
    case "ArrowRight": case "d": case "D": right(); break;
    case " ": stopHard(); e.preventDefault(); break;
  }
});

document.addEventListener("keyup", function (e) {
  keysDown[e.key] = false;
  var moveKeys = ["ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight",
    "w", "a", "s", "d", "W", "A", "S", "D"];
  if (moveKeys.indexOf(e.key) !== -1) releaseButton();
});


/* ---------- SAFETY STOP on page hide ----------------------------------- */
document.addEventListener("visibilitychange", function () {
  if (document.hidden && !_teleopLocked) stopHard();
});

/* ---------- EMERGENCY STOP --------------------------------------------- */
var _eStopActive = false;
var _eStopInterval = null;

window.eStop = function() {
  var btn = document.getElementById("e-stop-btn");
  
  if (!_eStopActive) {
    _eStopActive = true;
    if (typeof showToast === "function") showToast("🛑 EMERGENCY STOP ACTIVATED", "error");
    
    if (btn) {
      btn.innerHTML = "RELEASE E-STOP";
      btn.style.background = "#b91c1c"; // Darker red to indicate active hold
      btn.style.color = "#ffffff";
    }
    
    // 1. Force continuous zero velocity to override anything else instantly
    _eStopInterval = setInterval(function() {
      if (typeof cmdVel !== 'undefined' && cmdVel) {
        cmdVel.publish(new ROSLIB.Message({
          linear: { x: 0.0, y: 0.0, z: 0.0 },
          angular: { x: 0.0, y: 0.0, z: 0.0 }
        }));
      }
    }, 50); // Publish at 20Hz
    
    // 2. Stop running mission via backend and explicitly cancel any direct Nav2 goal
    fetch(SERVER_URL + "/mission/stop", { method: "POST" })
      .then(function() { 
        if (typeof showToast === "function") showToast("Mission Cancelled", "info"); 
      })
      .catch(function(e) { console.error("eStop mission stop error:", e); });
      
    fetch(SERVER_URL + "/cancel_goal", { method: "POST" })
      .catch(function(e) { console.error("eStop nav cancel error:", e); });
      
    // 3. Update UI
    var btnStart = document.getElementById("btn-start-mission");
    if (btnStart) btnStart.innerHTML = "▶ START MISSION";
    var btnPause = document.getElementById("btn-pause-mission");
    if (btnPause) btnPause.disabled = true;
    var btnStop = document.getElementById("btn-stop-mission");
    if (btnStop) btnStop.disabled = true;
    
  } else {
    // Release E-STOP
    _eStopActive = false;
    if (_eStopInterval) {
      clearInterval(_eStopInterval);
      _eStopInterval = null;
    }
    
    if (btn) {
      btn.innerHTML = "E-STOP";
      btn.style.background = "rgba(239, 68, 68, 0.15)";
      btn.style.color = "var(--red, #ef4444)";
    }
    
    if (typeof showToast === "function") showToast("✅ E-STOP RELEASED", "success");
  }
};
