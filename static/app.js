
/* =========================================================
   STRIDESIGNAL — DATA DASHBOARD APPLICATION
   Handles authentication, wearable readings, monitoring,
   dashboard updates, and communication with the API.
   ========================================================= */


/* =========================================================
   1. HTML ELEMENT REFERENCES
   Selects the elements used by the JavaScript application.
   ========================================================= */

// Reading submission form and feedback message.
const form = document.querySelector("#reading-form");
const message = document.querySelector("#form-message");

// Readings table and empty-state display.
const body = document.querySelector("#readings-body");
const empty = document.querySelector("#empty-state");

// Participant filter and form submission button.
const filter = document.querySelector("#participant-filter");
const submitButton = document.querySelector("#submit-button");

// Dashboard information and authentication elements.
const feedSummary = document.querySelector("#feed-summary");
const authPanel = document.querySelector("#auth-panel");
const authForm = document.querySelector("#auth-form");
const authMessage = document.querySelector("#auth-message");
const privateWorkspace = document.querySelector("#private-workspace");

// Stores the active readings request so it can be cancelled.
let activeReadingsRequest;


/* =========================================================
   2. DASHBOARD METRIC FUNCTIONS
   Updates the monitoring cards displayed on the dashboard.
   ========================================================= */

// Updates a dashboard metric using its HTML element ID.
function updateMetric(id, value) {
  const element = document.querySelector(`#${id}`);

  if (element) {
    element.textContent = value;
  }
}

// Resets all monitoring metrics when the user signs out.
function resetMonitoring() {
  updateMetric("metric-readings", "0");
  updateMetric("metric-participants", "0");
  updateMetric("metric-heart-rate", "—");
  updateMetric("metric-acceleration", "—");
  updateMetric("metric-gyroscope", "—");
}

// Calculates and displays summary information from sensor readings.
function updateMonitoring(readings) {

  // Collects unique participant IDs.
  const participants = new Set(
    readings.map((reading) => reading.participant_id)
  );

  // Updates the total readings and participant counts.
  updateMetric("metric-readings", readings.length);
  updateMetric("metric-participants", participants.size);

  // Gets the most recent reading.
  const latest = readings[0];

  // Displays the latest heart-rate measurement.
  updateMetric(
    "metric-heart-rate",
    latest?.heart_rate_bpm == null
      ? "—"
      : `${latest.heart_rate_bpm} bpm`
  );

  // Displays the latest accelerometer measurements.
  updateMetric(
    "metric-acceleration",
    latest
      ? `${latest.acceleration_m_s2_x}, ${latest.acceleration_m_s2_y}, ${latest.acceleration_m_s2_z}`
      : "—"
  );

  // Displays the latest gyroscope measurements.
  updateMetric(
    "metric-gyroscope",
    latest
      ? `${latest.angular_velocity_rad_s_x}, ${latest.angular_velocity_rad_s_y}, ${latest.angular_velocity_rad_s_z}`
      : "—"
  );
}


/* =========================================================
   3. USER AUTHENTICATION INTERFACE
   Controls what the user sees when signed in or signed out.
   ========================================================= */

// Displays the login panel and clears private dashboard data.
function showSignedOut(notice = "") {

  // Cancels any readings request still in progress.
  if (activeReadingsRequest) {
    activeReadingsRequest.abort();
  }

  privateWorkspace.hidden = true;
  authPanel.hidden = false;

  // Displays an optional authentication message.
  authMessage.textContent = notice;
  authMessage.className = notice ? "message error" : "message";

  // Clears the readings table and form.
  body.replaceChildren();
  form.reset();
  filter.value = "";
  message.textContent = "";

  // Resets dashboard metrics.
  resetMonitoring();

  // Sets the capture time to the current time.
  setCurrentTimestamp();
}

// Displays the private dashboard after successful authentication.
function showSignedIn(user) {

  authPanel.hidden = true;
  privateWorkspace.hidden = false;

  // Displays the signed-in user's email address.
  document.querySelector("#account-name").textContent = user.email;

  // Gets the main navigation menu.
  const nav = document.querySelector(".site-nav");

  // Checks whether the administrator link already exists.
  let adminLink = nav.querySelector('[data-admin-link="true"]');

  // Adds the administrator link for administrator accounts.
  if (user.role === "admin" && !adminLink) {

    adminLink = document.createElement("a");
    adminLink.href = "./admin.html";
    adminLink.dataset.adminLink = "true";
    adminLink.textContent = "Admin";

    nav.append(adminLink);

  } else if (user.role !== "admin") {

    // Removes the administrator link for ordinary accounts.
    adminLink?.remove();
  }
}


/* =========================================================
   4. SESSION VERIFICATION
   Checks whether the user has an active login session.
   ========================================================= */

async function checkSession() {

  try {

    // Requests the current user's session information.
    const response = await fetch("/v1/auth/me");

    const result = await response.json();

    // Displays the login panel if the user is not authenticated.
    if (response.status === 401) {
      showSignedOut();
      return;
    }

    // Handles other server errors.
    if (!response.ok) {
      throw new Error(
        result.error || "Could not check your account."
      );
    }

    // Displays the authenticated user's dashboard.
    showSignedIn(result.user);

    // Loads the user's readings.
    await loadReadings();

  } catch (error) {

    // Displays an error if the server cannot be reached.
    showSignedOut(
      error.message || "Could not connect to the server."
    );
  }
}


/* =========================================================
   5. LOGIN AND REGISTRATION
   Sends authentication information to the backend API.
   ========================================================= */

authForm.addEventListener("submit", async (event) => {

  // Prevents the browser from reloading the page.
  event.preventDefault();

  // Identifies whether the user selected login or registration.
  const action = event.submitter?.dataset.action;

  // Only accepts supported authentication actions.
  if (!["login", "register"].includes(action)) {
    return;
  }

  // Collects the submitted form information.
  const values = new FormData(authForm);

  // Temporarily disables authentication buttons.
  const buttons = authForm.querySelectorAll("button");

  buttons.forEach((button) => {
    button.disabled = true;
  });

  // Displays the current authentication operation.
  authMessage.textContent =
    action === "register"
      ? "Creating account…"
      : "Signing in…";

  authMessage.className = "message";

  try {

    // Sends login or registration information to the server.
    const response = await fetch(`/v1/auth/${action}`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json"
      },
      body: JSON.stringify({
        email: values.get("email"),
        password: values.get("password")
      })
    });

    const result = await response.json();

    // Handles unsuccessful authentication.
    if (!response.ok) {
      throw new Error(
        result.error || "Could not access your account."
      );
    }

    // Clears the authentication form.
    authForm.reset();

    // Opens the authenticated workspace.
    showSignedIn(result.user);

    // Loads the user's sensor readings.
    await loadReadings();

  } catch (error) {

    // Displays authentication errors.
    authMessage.textContent =
      error.message || "Could not connect to the server.";

    authMessage.className = "message error";

  } finally {

    // Re-enables the authentication buttons.
    buttons.forEach((button) => {
      button.disabled = false;
    });
  }
});


/* =========================================================
   6. USER LOGOUT
   Ends the current server-side authentication session.
   ========================================================= */

document.querySelector("#sign-out").addEventListener("click", async () => {

  const button = document.querySelector("#sign-out");

  // Prevents repeated logout requests.
  button.disabled = true;

  try {

    // Sends the logout request to the backend.
    const response = await fetch("/v1/auth/logout", {
      method: "POST"
    });

    const result = await response.json();

    if (!response.ok) {
      throw new Error(
        result.error || "Could not sign out."
      );
    }

    // Returns the interface to the signed-out state.
    showSignedOut();

  } catch (error) {

    // Displays any logout errors.
    authMessage.textContent =
      error.message || "Could not connect to the server.";

    authMessage.className = "message error";

  } finally {

    // Re-enables the logout button.
    button.disabled = false;
  }
});


/* =========================================================
   7. DATA FORMATTING AND HELPER FUNCTIONS
   Provides reusable functions for processing sensor data.
   ========================================================= */

// Converts a form value into a JavaScript number.
function numberFrom(formData, name) {
  return Number(formData.get(name));
}

// Converts an ISO timestamp into a readable local date and time.
function formatTimestamp(value) {

  const date = new Date(value);

  return Number.isNaN(date.getTime())
    ? value
    : date.toLocaleString();
}

// Creates a table cell and safely inserts its text.
function addCell(row, value, className) {

  const cell = document.createElement("td");

  cell.textContent = value;

  if (className) {
    cell.className = className;
  }

  row.append(cell);
}

// Sets the capture-time input to the current local time.
function setCurrentTimestamp() {

  form.elements["captured-at"].value = new Date(
    Date.now() - new Date().getTimezoneOffset() * 60000
  )
    .toISOString()
    .slice(0, 19);
}


/* =========================================================
   8. LOAD SENSOR READINGS
   Retrieves stored readings from the backend API and
   displays them in the dashboard.
   ========================================================= */

async function loadReadings() {

  // Cancels the previous request if it is still running.
  if (activeReadingsRequest) {
    activeReadingsRequest.abort();
  }

  // Creates a controller for the new request.
  activeReadingsRequest = new AbortController();

  const request = activeReadingsRequest;

  // Requests a maximum of 100 readings.
  const params = new URLSearchParams({
    limit: "100"
  });

  // Adds a participant filter when one is provided.
  if (filter.value.trim()) {
    params.set("participant_id", filter.value.trim());
  }

  // Updates the dashboard loading message.
  feedSummary.textContent = "Loading readings…";

  try {

    // Retrieves readings from the protected API endpoint.
    const response = await fetch(`/v1/readings?${params}`, {
      signal: request.signal
    });

    const result = await response.json();

    // Handles unsuccessful API responses.
    if (!response.ok) {

      const error = new Error(
        result.error || "Could not load readings."
      );

      error.status = response.status;

      throw error;
    }

    // Prevents an older request from overwriting newer results.
    if (request !== activeReadingsRequest) {
      return;
    }

    // Extracts the readings returned by the API.
    const readings = result.readings;

    // Updates the dashboard monitoring cards.
    updateMonitoring(readings);

    // Clears the previous table contents.
    body.replaceChildren();

    // Creates a table row for every reading.
    for (const reading of readings) {

      const row = document.createElement("tr");

      // Displays the capture timestamp.
      addCell(
        row,
        formatTimestamp(reading.captured_at),
        "subtle"
      );

      // Displays the participant identifier.
      addCell(
        row,
        reading.participant_id,
        "participant"
      );

      // Displays the heart-rate measurement.
      addCell(
        row,
        reading.heart_rate_bpm == null
          ? "—"
          : `${reading.heart_rate_bpm} bpm`
      );

      // Displays the three accelerometer measurements.
      addCell(
        row,
        `(${reading.acceleration_m_s2_x}, ${reading.acceleration_m_s2_y}, ${reading.acceleration_m_s2_z})`,
        "subtle"
      );

      // Adds the completed row to the table.
      body.append(row);
    }

    // Updates the empty-state information.
    empty.querySelector("strong").textContent = "No readings yet";

    empty.lastChild.textContent =
      "Add a sensor reading to see it here.";

    // Shows the empty state only when there are no readings.
    empty.hidden = readings.length > 0;

    // Displays the number of retrieved readings.
    feedSummary.textContent =
      `${readings.length} ${
        readings.length === 1 ? "reading" : "readings"
      }${
        filter.value.trim()
          ? " for this participant"
          : " · latest 100"
      }`;

  } catch (error) {

    // Ignores requests that were deliberately cancelled.
    if (
      error.name === "AbortError" ||
      request !== activeReadingsRequest
    ) {
      return;
    }

    // Returns to the login screen if the session has expired.
    if (error.status === 401) {

      showSignedOut(
        "Your session has expired. Sign in again to continue."
      );

      return;
    }

    // Displays an error message in the readings area.
    body.replaceChildren();

    empty.hidden = false;

    empty.querySelector("strong").textContent =
      "Unable to load readings";

    empty.lastChild.textContent = error.message;

    feedSummary.textContent = "Connection error";

    // Resets the dashboard metrics.
    resetMonitoring();
  }
}


/* =========================================================
   9. SENSOR READING SUBMISSION
   Collects accelerometer, gyroscope, and optional heart-rate
   information and sends it to the backend.
   ========================================================= */

// Sets the initial capture timestamp when the page loads.
setCurrentTimestamp();

form.addEventListener("submit", async (event) => {

  // Prevents the default browser form submission.
  event.preventDefault();

  // Collects all values entered into the sensor form.
  const values = new FormData(form);

  // Converts the capture timestamp into a JavaScript Date.
  const timestamp = new Date(values.get("captured-at"));

  // Checks that the supplied timestamp is valid.
  if (Number.isNaN(timestamp.getTime())) {

    message.textContent = "Enter a valid capture time.";
    message.className = "message error";

    return;
  }

  // Retrieves the optional heart-rate value.
  const heartRate = values.get("heart-rate").trim();

  // Builds the sensor reading object.
  const reading = {

    // Participant and wearable device identification.
    participant_id: values.get("participant").trim(),
    device_id: values.get("device").trim(),

    // Converts the capture time to UTC ISO format.
    captured_at: timestamp.toISOString(),

    // Accelerometer measurements in metres per second squared.
    acceleration_m_s2: {
      x: numberFrom(values, "accel-x"),
      y: numberFrom(values, "accel-y"),
      z: numberFrom(values, "accel-z")
    },

    // Gyroscope measurements in radians per second.
    angular_velocity_rad_s: {
      x: numberFrom(values, "gyro-x"),
      y: numberFrom(values, "gyro-y"),
      z: numberFrom(values, "gyro-z")
    }
  };

  // Includes heart rate only when the user entered a value.
  if (heartRate) {
    reading.heart_rate_bpm = Number(heartRate);
  }

  // Disables the submit button while saving.
  submitButton.disabled = true;

  message.textContent = "Saving reading…";
  message.className = "message";

  try {

    // Sends the sensor reading to the backend API.
    const response = await fetch("/v1/readings", {
      method: "POST",

      headers: {
        "Content-Type": "application/json"
      },

      body: JSON.stringify(reading)
    });

    const result = await response.json();

    // Checks whether the user's session has expired.
    if (response.status === 401) {

      showSignedOut(
        "Your session has expired. Sign in again to continue."
      );

      return;
    }

    // Handles unsuccessful reading submissions.
    if (!response.ok) {
      throw new Error(
        result.error || "Could not save reading."
      );
    }

    // Confirms successful storage.
    message.textContent = `Reading #${result.id} saved.`;
    message.className = "message success";

    // Refreshes the table if the new reading matches the filter.
    if (
      !filter.value.trim() ||
      filter.value.trim() === reading.participant_id
    ) {
      await loadReadings();
    }

  } catch (error) {

    // Displays any errors encountered during submission.
    message.textContent = error.message;
    message.className = "message error";

  } finally {

    // Re-enables the submit button.
    submitButton.disabled = false;
  }
});


/* =========================================================
   10. DASHBOARD REFRESH AND PARTICIPANT FILTER
   Allows users to refresh readings and filter by participant.
   ========================================================= */

// Manually refreshes the readings table.
document.querySelector("#refresh-button").addEventListener(
  "click",
  loadReadings
);

// Stores the participant-filter delay.
let filterTimeout;

// Reloads readings when the participant filter changes.
filter.addEventListener("input", () => {

  // Cancels the previous scheduled filter request.
  clearTimeout(filterTimeout);

  // Waits briefly before loading the filtered readings.
  filterTimeout = setTimeout(loadReadings, 150);
});


/* =========================================================
   11. APPLICATION INITIALIZATION
   Checks the user's login session when the application starts.
   ========================================================= */

checkSession();