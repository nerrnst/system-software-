
const form = document.querySelector("#reading-form");
const message = document.querySelector("#form-message");
const body = document.querySelector("#readings-body");
const empty = document.querySelector("#empty-state");
const filter = document.querySelector("#participant-filter");
const submitButton = document.querySelector("#submit-button");
const feedSummary = document.querySelector("#feed-summary");
const authPanel = document.querySelector("#auth-panel");
const authForm = document.querySelector("#auth-form");
const authMessage = document.querySelector("#auth-message");
const privateWorkspace = document.querySelector("#private-workspace");

let activeReadingsRequest;

function updateMetric(id, value) {
  const element = document.querySelector(`#${id}`);
  if (element) element.textContent = value;
}

function resetMonitoring() {
  updateMetric("metric-readings", "0");
  updateMetric("metric-participants", "0");
  updateMetric("metric-heart-rate", "—");
  updateMetric("metric-acceleration", "—");
  updateMetric("metric-gyroscope", "—");
}

function updateMonitoring(readings) {
  const participants = new Set(
    readings.map((reading) => reading.participant_id)
  );

  updateMetric("metric-readings", readings.length);
  updateMetric("metric-participants", participants.size);

  const latest = readings[0];

  updateMetric(
    "metric-heart-rate",
    latest?.heart_rate_bpm == null
      ? "—"
      : `${latest.heart_rate_bpm} bpm`
  );

  updateMetric(
    "metric-acceleration",
    latest
      ? `${latest.acceleration_m_s2_x}, ${latest.acceleration_m_s2_y}, ${latest.acceleration_m_s2_z}`
      : "—"
  );

  updateMetric(
    "metric-gyroscope",
    latest
      ? `${latest.angular_velocity_rad_s_x}, ${latest.angular_velocity_rad_s_y}, ${latest.angular_velocity_rad_s_z}`
      : "—"
  );
}

function showSignedOut(notice = "") {
  if (activeReadingsRequest) activeReadingsRequest.abort();

  privateWorkspace.hidden = true;
  authPanel.hidden = false;
  authMessage.textContent = notice;
  authMessage.className = notice ? "message error" : "message";

  body.replaceChildren();
  form.reset();
  filter.value = "";
  message.textContent = "";

  resetMonitoring();
  setCurrentTimestamp();
}

function showSignedIn(user) {
  authPanel.hidden = true;
  privateWorkspace.hidden = false;

  document.querySelector("#account-name").textContent = user.email;

  const nav = document.querySelector(".site-nav");
  let adminLink = nav.querySelector('[data-admin-link="true"]');

  if (user.role === "admin" && !adminLink) {
    adminLink = document.createElement("a");
    adminLink.href = "./admin.html";
    adminLink.dataset.adminLink = "true";
    adminLink.textContent = "Admin";
    nav.append(adminLink);
  } else if (user.role !== "admin") {
    adminLink?.remove();
  }
}

async function checkSession() {
  try {
    const response = await fetch("/v1/auth/me");
    const result = await response.json();

    if (response.status === 401) {
      showSignedOut();
      return;
    }

    if (!response.ok) {
      throw new Error(result.error || "Could not check your account.");
    }

    showSignedIn(result.user);
    await loadReadings();
  } catch (error) {
    showSignedOut(error.message || "Could not connect to the server.");
  }
}

authForm.addEventListener("submit", async (event) => {
  event.preventDefault();

  const action = event.submitter?.dataset.action;
  if (!["login", "register"].includes(action)) return;

  const values = new FormData(authForm);
  const buttons = authForm.querySelectorAll("button");

  buttons.forEach((button) => {
    button.disabled = true;
  });

  authMessage.textContent =
    action === "register" ? "Creating account…" : "Signing in…";
  authMessage.className = "message";

  try {
    const response = await fetch(`/v1/auth/${action}`, {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({
        email: values.get("email"),
        password: values.get("password"),
      }),
    });

    const result = await response.json();

    if (!response.ok) {
      throw new Error(result.error || "Could not access your account.");
    }

    authForm.reset();
    showSignedIn(result.user);
    await loadReadings();
  } catch (error) {
    authMessage.textContent =
      error.message || "Could not connect to the server.";
    authMessage.className = "message error";
  } finally {
    buttons.forEach((button) => {
      button.disabled = false;
    });
  }
});

document.querySelector("#sign-out").addEventListener("click", async () => {
  const button = document.querySelector("#sign-out");
  button.disabled = true;

  try {
    const response = await fetch("/v1/auth/logout", {
      method: "POST",
    });

    const result = await response.json();

    if (!response.ok) {
      throw new Error(result.error || "Could not sign out.");
    }

    showSignedOut();
  } catch (error) {
    authMessage.textContent =
      error.message || "Could not connect to the server.";
    authMessage.className = "message error";
  } finally {
    button.disabled = false;
  }
});

function numberFrom(formData, name) {
  return Number(formData.get(name));
}

function formatTimestamp(value) {
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? value
    : date.toLocaleString();
}

function addCell(row, value, className) {
  const cell = document.createElement("td");
  cell.textContent = value;

  if (className) cell.className = className;

  row.append(cell);
}

function setCurrentTimestamp() {
  form.elements["captured-at"].value = new Date(
    Date.now() - new Date().getTimezoneOffset() * 60000,
  )
    .toISOString()
    .slice(0, 19);
}

async function loadReadings() {
  if (activeReadingsRequest) {
    activeReadingsRequest.abort();
  }

  activeReadingsRequest = new AbortController();
  const request = activeReadingsRequest;

  const params = new URLSearchParams({limit: "100"});

  if (filter.value.trim()) {
    params.set("participant_id", filter.value.trim());
  }

  feedSummary.textContent = "Loading readings…";

  try {
    const response = await fetch(`/v1/readings?${params}`, {
      signal: request.signal,
    });

    const result = await response.json();

    if (!response.ok) {
      const error = new Error(
        result.error || "Could not load readings."
      );
      error.status = response.status;
      throw error;
    }

    if (request !== activeReadingsRequest) return;

    const readings = result.readings;

    // Update dashboard monitoring cards.
    updateMonitoring(readings);

    // Update the readings table.
    body.replaceChildren();

    for (const reading of readings) {
      const row = document.createElement("tr");

      addCell(
        row,
        formatTimestamp(reading.captured_at),
        "subtle"
      );

      addCell(row, reading.participant_id, "participant");

      addCell(
        row,
        reading.heart_rate_bpm == null
          ? "—"
          : `${reading.heart_rate_bpm} bpm`
      );

      addCell(
        row,
        `(${reading.acceleration_m_s2_x}, ${reading.acceleration_m_s2_y}, ${reading.acceleration_m_s2_z})`,
        "subtle"
      );

      body.append(row);
    }

    empty.querySelector("strong").textContent = "No readings yet";
    empty.lastChild.textContent =
      "Add a sensor reading to see it here.";

    empty.hidden = readings.length > 0;

    feedSummary.textContent =
      `${readings.length} ${
        readings.length === 1 ? "reading" : "readings"
      }${
        filter.value.trim()
          ? " for this participant"
          : " · latest 100"
      }`;

  } catch (error) {
    if (
      error.name === "AbortError" ||
      request !== activeReadingsRequest
    ) {
      return;
    }

    if (error.status === 401) {
      showSignedOut(
        "Your session has expired. Sign in again to continue."
      );
      return;
    }

    body.replaceChildren();
    empty.hidden = false;
    empty.querySelector("strong").textContent =
      "Unable to load readings";
    empty.lastChild.textContent = error.message;
    feedSummary.textContent = "Connection error";

    resetMonitoring();
  }
}

// Set the current local timestamp.
setCurrentTimestamp();

form.addEventListener("submit", async (event) => {
  event.preventDefault();

  const values = new FormData(form);
  const timestamp = new Date(values.get("captured-at"));

  if (Number.isNaN(timestamp.getTime())) {
    message.textContent = "Enter a valid capture time.";
    message.className = "message error";
    return;
  }

  const heartRate = values.get("heart-rate").trim();

  const reading = {
    participant_id: values.get("participant").trim(),
    device_id: values.get("device").trim(),
    captured_at: timestamp.toISOString(),

    acceleration_m_s2: {
      x: numberFrom(values, "accel-x"),
      y: numberFrom(values, "accel-y"),
      z: numberFrom(values, "accel-z"),
    },

    angular_velocity_rad_s: {
      x: numberFrom(values, "gyro-x"),
      y: numberFrom(values, "gyro-y"),
      z: numberFrom(values, "gyro-z"),
    },
  };

  if (heartRate) {
    reading.heart_rate_bpm = Number(heartRate);
  }

  submitButton.disabled = true;
  message.textContent = "Saving reading…";
  message.className = "message";

  try {
    const response = await fetch("/v1/readings", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(reading),
    });

    const result = await response.json();

    if (response.status === 401) {
      showSignedOut(
        "Your session has expired. Sign in again to continue."
      );
      return;
    }

    if (!response.ok) {
      throw new Error(
        result.error || "Could not save reading."
      );
    }

    message.textContent = `Reading #${result.id} saved.`;
    message.className = "message success";

    if (
      !filter.value.trim() ||
      filter.value.trim() === reading.participant_id
    ) {
      await loadReadings();
    }

  } catch (error) {
    message.textContent = error.message;
    message.className = "message error";
  } finally {
    submitButton.disabled = false;
  }
});

document.querySelector("#refresh-button").addEventListener(
  "click",
  loadReadings
);

let filterTimeout;

filter.addEventListener("input", () => {
  clearTimeout(filterTimeout);
  filterTimeout = setTimeout(loadReadings, 150);
});

checkSession();