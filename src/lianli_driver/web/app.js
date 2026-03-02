const statusPill = document.getElementById("status-pill");
const refreshBtn = document.getElementById("refresh-btn");
const sensorsEl = document.getElementById("sensors");
const fansEl = document.getElementById("fans");
const devicesEl = document.getElementById("devices");
const lcdForm = document.getElementById("lcd-form");
const lcdHidraw = document.getElementById("lcd-hidraw");
const lcdImage = document.getElementById("lcd-image");
const lcdVideo = document.getElementById("lcd-video");
const lcdWidth = document.getElementById("lcd-width");
const lcdHeight = document.getElementById("lcd-height");
const lcdVideoFps = document.getElementById("lcd-video-fps");
const lcdVideoSeconds = document.getElementById("lcd-video-seconds");
const lcdUnsafe = document.getElementById("lcd-unsafe");
const lcdProbeBtn = document.getElementById("lcd-probe-btn");
const lcdVideoBtn = document.getElementById("lcd-video-btn");
const lcdResult = document.getElementById("lcd-result");

let appState = null;
let selectedLcdTarget = "";
let lastResolutionTarget = "";

function inferLcdResolution(device) {
  const merged = `${device.model || ""} ${device.product || ""} ${device.name || ""}`.toUpperCase();
  if (
    merged.includes("SL-LCD") ||
    merged.includes("TL LCD") ||
    merged.includes("TL-LCD") ||
    merged.includes("SL LCD")
  ) {
    return { width: 400, height: 400 };
  }
  return { width: 480, height: 480 };
}

function applyRecommendedResolutionForTarget() {
  const selected = lcdHidraw.selectedOptions && lcdHidraw.selectedOptions[0];
  if (!selected) return;
  const target = selected.value || "";
  if (!target || target === lastResolutionTarget) return;
  const recWidth = Number(selected.dataset.recWidth || 0);
  const recHeight = Number(selected.dataset.recHeight || 0);
  if (recWidth > 0 && recHeight > 0) {
    lcdWidth.value = String(recWidth);
    lcdHeight.value = String(recHeight);
  }
  lastResolutionTarget = target;
}

function setStatus(text) {
  statusPill.textContent = text;
}

async function httpGet(path) {
  const res = await fetch(path);
  const payload = await res.json();
  if (!res.ok || payload.ok === false) {
    throw new Error(payload.error || payload.result?.message || `HTTP ${res.status}`);
  }
  return payload;
}

async function httpPost(path, body) {
  const res = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
  const payload = await res.json();
  if (!res.ok || payload.ok === false) {
    throw new Error(payload.error || payload.result?.message || `HTTP ${res.status}`);
  }
  return payload;
}

function fmtTemp(temp) {
  if (typeof temp !== "number") return "n/a";
  return `${temp.toFixed(1)} C`;
}

function renderSensors(snapshot) {
  sensorsEl.innerHTML = "";
  if (!snapshot.sensors.length) {
    sensorsEl.innerHTML = `<div class="card muted">No sensors detected.</div>`;
    return;
  }

  for (const sensor of snapshot.sensors) {
    const card = document.createElement("article");
    card.className = "card";
    card.innerHTML = `
      <h3 class="title">${sensor.label}</h3>
      <p class="muted">${sensor.id}</p>
      <p>${fmtTemp(sensor.temp_c)}</p>
    `;
    sensorsEl.appendChild(card);
  }
}

function sensorOptions(snapshot, selected) {
  return snapshot.sensors
    .map((s) => {
      const sel = s.id === selected ? "selected" : "";
      return `<option value="${s.id}" ${sel}>${s.label}</option>`;
    })
    .join("");
}

function renderFans(snapshot, autoAssignments, lastAutoResults) {
  fansEl.innerHTML = "";
  if (!snapshot.pwm_channels.length) {
    fansEl.innerHTML = `<div class="card muted">No PWM channels detected.</div>`;
    return;
  }

  for (const channel of snapshot.pwm_channels) {
    const auto = autoAssignments[channel.id];
    const last = lastAutoResults[channel.id];
    const card = document.createElement("article");
    card.className = "card";
    card.innerHTML = `
      <h3 class="title">${channel.id}</h3>
      <p class="muted">${channel.controller}</p>
      <p>Current: ${channel.percent ?? "n/a"}% | RPM: ${channel.rpm ?? "n/a"}</p>
      <div class="fan-controls">
        <div class="fan-row">
          <input type="range" min="0" max="100" value="${Math.round(channel.percent || 30)}" data-role="slider" />
          <span class="fan-pct" data-role="slider-value">${Math.round(channel.percent || 30)}%</span>
        </div>
        <button data-action="manual">Apply Manual</button>
        <div>
          <select data-role="sensor">${sensorOptions(snapshot, auto?.sensor_id)}</select>
          <select data-role="preset">
            <option value="quiet" ${auto?.curve_name === "quiet" ? "selected" : ""}>quiet</option>
            <option value="balanced" ${auto?.curve_name === "balanced" ? "selected" : ""}>balanced</option>
            <option value="performance" ${auto?.curve_name === "performance" ? "selected" : ""}>performance</option>
          </select>
        </div>
        <button data-action="auto">Enable Auto Curve</button>
        <button data-action="disable-auto">Disable Auto</button>
        <p class="muted">Auto target: ${
          last ? `${fmtTemp(last.temp_c)} -> ${last.duty_pct.toFixed(1)}%` : "none"
        }</p>
      </div>
    `;

    const slider = card.querySelector('[data-role="slider"]');
    const sliderValue = card.querySelector('[data-role="slider-value"]');
    const sensorSelect = card.querySelector('[data-role="sensor"]');
    const presetSelect = card.querySelector('[data-role="preset"]');
    slider.addEventListener("input", () => {
      sliderValue.textContent = `${slider.value}%`;
    });

    card.querySelector('[data-action="manual"]').addEventListener("click", async () => {
      try {
        setStatus("writing manual fan");
        await httpPost("/api/fans/manual", {
          channel_id: channel.id,
          percent: Number(slider.value),
        });
        await refreshState();
      } catch (err) {
        setStatus(`error: ${err.message}`);
      }
    });

    card.querySelector('[data-action="auto"]').addEventListener("click", async () => {
      try {
        setStatus("writing auto curve");
        await httpPost("/api/fans/auto", {
          channel_id: channel.id,
          sensor_id: sensorSelect.value,
          preset: presetSelect.value,
        });
        await refreshState();
      } catch (err) {
        setStatus(`error: ${err.message}`);
      }
    });

    card.querySelector('[data-action="disable-auto"]').addEventListener("click", async () => {
      try {
        setStatus("disabling auto");
        await httpPost("/api/fans/auto/disable", { channel_id: channel.id });
        await refreshState();
      } catch (err) {
        setStatus(`error: ${err.message}`);
      }
    });

    fansEl.appendChild(card);
  }
}

function renderDevices(snapshot) {
  const previousSelection = selectedLcdTarget || lcdHidraw.value || "";
  devicesEl.innerHTML = "";
  lcdHidraw.innerHTML = "";

  const hidDevices = snapshot.hid_devices || [];
  const bulkDevices = snapshot.bulk_devices || [];
  const allDevices = [...hidDevices, ...bulkDevices];

  if (!allDevices.length) {
    devicesEl.innerHTML = `<div class="card muted">No Lian Li USB devices detected.</div>`;
    return;
  }

  for (const device of hidDevices) {
    const card = document.createElement("article");
    card.className = "card";
    card.innerHTML = `
      <h3 class="title">${device.model}</h3>
      <p class="muted">${device.path} | ${device.vendor_id}:${device.product_id}</p>
      <p>transport: HID</p>
      <p>name: ${device.name || "n/a"}</p>
      <p>caps: ${(device.capabilities || []).join(", ") || "none"}</p>
      <p>protocol: ${device.protocol_loaded ? "loaded" : "missing"}</p>
      <p>access: ${device.accessible ? "ok" : "blocked"}</p>
    `;
    devicesEl.appendChild(card);

    const opt = document.createElement("option");
    opt.value = device.path;
    const caps = (device.capabilities || []).length ? (device.capabilities || []).join(",") : "none";
    opt.textContent = `${device.path} (${device.model}) [hid caps:${caps}]`;
    const rec = inferLcdResolution(device);
    opt.dataset.recWidth = String(rec.width);
    opt.dataset.recHeight = String(rec.height);
    lcdHidraw.appendChild(opt);
  }

  for (const device of bulkDevices) {
    const card = document.createElement("article");
    card.className = "card";
    const endpointText = (device.endpoints || [])
      .map((ep) => `${ep.direction}:${ep.address}/${ep.transfer_type}/${ep.max_packet_size}`)
      .join(" | ");
    card.innerHTML = `
      <h3 class="title">${device.model}</h3>
      <p class="muted">${device.id} | ${device.vendor_id}:${device.product_id}</p>
      <p>transport: USB bulk</p>
      <p>product: ${device.product || "n/a"} | manufacturer: ${device.manufacturer || "n/a"}</p>
      <p>caps: ${(device.capabilities || []).join(", ") || "none"}</p>
      <p>protocol: ${device.protocol_loaded ? "loaded" : "missing"}</p>
      <p>access: ${device.accessible ? "ok" : "blocked"}</p>
      <p class="muted">${endpointText || "no endpoint data"}</p>
    `;
    devicesEl.appendChild(card);

    const opt = document.createElement("option");
    opt.value = device.id;
    const caps = (device.capabilities || []).length ? (device.capabilities || []).join(",") : "none";
    opt.textContent = `${device.id} (${device.model}) [usb caps:${caps}]`;
    const rec = inferLcdResolution(device);
    opt.dataset.recWidth = String(rec.width);
    opt.dataset.recHeight = String(rec.height);
    lcdHidraw.appendChild(opt);
  }

  if (!lcdHidraw.options.length) {
    const opt = document.createElement("option");
    opt.value = "";
    opt.textContent = "No LCD targets available";
    lcdHidraw.appendChild(opt);
  }

  if (previousSelection) {
    const hasPrevious = Array.from(lcdHidraw.options).some((opt) => opt.value === previousSelection);
    if (hasPrevious) {
      lcdHidraw.value = previousSelection;
    }
  }
  selectedLcdTarget = lcdHidraw.value || "";
  applyRecommendedResolutionForTarget();
}

async function refreshState() {
  try {
    setStatus("syncing");
    const payload = await httpGet("/api/state");
    appState = payload.state;
    renderSensors(appState.snapshot);
    renderFans(appState.snapshot, appState.auto_assignments, appState.last_auto_results);
    renderDevices(appState.snapshot);
    setStatus(`ok ${new Date().toLocaleTimeString()}`);
  } catch (err) {
    setStatus(`error: ${err.message}`);
  }
}

refreshBtn.addEventListener("click", async () => {
  try {
    setStatus("refreshing hardware");
    await httpPost("/api/refresh", {});
    await refreshState();
  } catch (err) {
    setStatus(`error: ${err.message}`);
  }
});

lcdForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    if (!lcdHidraw.value) {
      throw new Error("No LCD target selected.");
    }
    setStatus("uploading lcd frame");
    const payload = await httpPost("/api/lcd/upload", {
      target_id: lcdHidraw.value,
      image_path: lcdImage.value,
      width: Number(lcdWidth.value),
      height: Number(lcdHeight.value),
      unsafe_hid_writes: lcdUnsafe.checked,
    });
    lcdResult.textContent = JSON.stringify(payload.result, null, 2);
    await refreshState();
  } catch (err) {
    lcdResult.textContent = err.message;
    setStatus(`error: ${err.message}`);
  }
});

lcdProbeBtn.addEventListener("click", async () => {
  try {
    if (!lcdHidraw.value) {
      throw new Error("No LCD target selected.");
    }
    setStatus("probing lcd target");
    const payload = await httpPost("/api/lcd/probe", { target_id: lcdHidraw.value });
    lcdResult.textContent = JSON.stringify(payload.result, null, 2);
    setStatus("probe done");
  } catch (err) {
    lcdResult.textContent = err.message;
    setStatus(`error: ${err.message}`);
  }
});

lcdVideoBtn.addEventListener("click", async () => {
  try {
    if (!lcdHidraw.value) {
      throw new Error("No LCD target selected.");
    }
    if (!lcdHidraw.value.startsWith("usb:")) {
      const usbOption = Array.from(lcdHidraw.options).find((opt) => String(opt.value).startsWith("usb:"));
      if (usbOption) {
        lcdHidraw.value = usbOption.value;
      }
    }
    if (!lcdVideo.value) {
      throw new Error("Video path is required.");
    }
    setStatus("streaming lcd video");
    const payload = await httpPost("/api/lcd/video", {
      target_id: lcdHidraw.value,
      video_path: lcdVideo.value,
      width: Number(lcdWidth.value),
      height: Number(lcdHeight.value),
      fps: Number(lcdVideoFps.value),
      max_seconds: Number(lcdVideoSeconds.value),
      unsafe_hid_writes: lcdUnsafe.checked,
    });
    lcdResult.textContent = JSON.stringify(payload.result, null, 2);
    setStatus("video stream done");
  } catch (err) {
    lcdResult.textContent = err.message;
    setStatus(`error: ${err.message}`);
  }
});

lcdHidraw.addEventListener("change", () => {
  selectedLcdTarget = lcdHidraw.value || "";
  applyRecommendedResolutionForTarget();
});

refreshState();
setInterval(refreshState, 3000);
