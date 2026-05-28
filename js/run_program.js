/* ==========================================================================
   run_program.js — List and run Python programs from the server
   Depends on: app.js (SERVER_URL, showToast)
   ========================================================================== */


/* ---------- LOAD PROGRAM LIST ---------- */

var _programRunning = false;

function loadProgramList() {
  fetch(SERVER_URL + "/programs")
    .then(r => r.json())
    .then(function (data) {
      var sel = document.getElementById("programSelect");
      sel.innerHTML = "<option value=''>— select program —</option>";
      data.forEach(function (p) {
        var o = document.createElement("option");
        o.value = o.textContent = p;
        sel.appendChild(o);
      });
    })
    .catch(() => showToast("⚠ Could not load programs", "error"));
}
loadProgramList();

function _setProgramStatus(running, program) {
  _programRunning = !!running;
  var el = document.getElementById("program-status");
  if (running) {
    if (el) {
      el.textContent = "Running" + (program ? ": " + program : "");
      el.style.color = "var(--green)";
    }
    if (typeof lockTeleop === "function") lockTeleop();
  } else {
    if (el) {
      el.textContent = "Idle";
      el.style.color = "var(--muted)";
    }
    if (typeof unlockTeleop === "function" && !(typeof _deliveryRunning !== "undefined" && _deliveryRunning)) {
      unlockTeleop();
    }
  }
}

function refreshProgramStatus() {
  fetch(SERVER_URL + "/program/status")
    .then(r => r.json())
    .then(function(d) {
      _setProgramStatus(!!d.running, d.program || "");
      if (d.running && d.program) {
        var sel = document.getElementById("programSelect");
        if (sel) sel.value = d.program;
      }
    })
    .catch(function() {});
}

function initProgramView() {
  loadProgramList();
  setTimeout(refreshProgramStatus, 150);
}
refreshProgramStatus();


/* ---------- RUN PROGRAM ---------- */

function runProgram() {
  var prog = document.getElementById("programSelect").value;
  if (!prog) { showToast("⚠ Select a program first", "error"); return; }
  fetch(SERVER_URL + "/run_program", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ program: prog })
  })
    .then(r => r.json())
    .then(function(d) {
      showToast(d.status === "already_running" ? "▶ Already running: " + prog : "▶ Running: " + prog, "success");
      _setProgramStatus(true, prog);
    })
    .catch(() => showToast("⚠ Failed to run program", "error"));
}


/* ---------- STOP PROGRAM ---------- */

function stopProgram() {
  fetch(SERVER_URL + "/stop_program", { method: "POST" })
    .then(r => r.json())
    .then(function(d) {
      showToast("⏹ Program stopped", "info");
      _setProgramStatus(false, "");
    })
    .catch(() => showToast("⚠ Failed to stop program", "error"));
}

/* ---------- STOP PROGRAM + GO HOME ---------- */

function stopProgramAndGoHome() {
  fetch(SERVER_URL + "/stop_program", { method: "POST" })
    .then(r => r.json())
    .then(function(d) {
      _setProgramStatus(false, "");
      if (d.going_home) {
        showToast("⏹ Program stopped — navigating home", "info");
      } else {
        showToast("⏹ Program stopped", "info");
      }
    })
    .catch(() => showToast("⚠ Failed to stop program", "error"));
}
