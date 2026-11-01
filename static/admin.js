const message = document.querySelector("#admin-message");
const usersBody = document.querySelector("#users-body");
const readingsBody = document.querySelector("#admin-readings-body");
const auditBody = document.querySelector("#audit-body");
const readingDialog = document.querySelector("#reading-dialog");
const editForm = document.querySelector("#edit-reading-form");
const readingsById = new Map();
let activeUser;

// API authorization is mandatory; this client-side redirect is only for navigation.
async function request(path, options = {}) {
  const response = await fetch(path, options);
  if (response.status === 401) {
    window.location.assign("./dashboard.html");
    throw new Error("Your session expired. Sign in again.");
  }
  const contentType = response.headers.get("Content-Type") || "";
  const result = contentType.includes("application/json") ? await response.json() : null;
  if (!response.ok) throw new Error(result?.error || "The request could not be completed.");
  return {response, result};
}

function showMessage(text, kind = "") {
  message.textContent = text;
  message.className = `message ${kind}`.trim();
}

function cell(row, value) {
  const td = document.createElement("td");
  td.textContent = value == null || value === "" ? "—" : String(value);
  row.append(td);
  return td;
}

function button(label, action, id, danger = false) {
  const element = document.createElement("button");
  element.type = "button";
  element.textContent = label;
  element.className = danger ? "small-button danger-button" : "small-button";
  element.dataset.action = action;
  element.dataset.id = id;
  return element;
}

function renderUsers(users) {
  usersBody.replaceChildren();
  for (const user of users) {
    const row = document.createElement("tr");
    row.dataset.id = user.id;
    cell(row, user.email);
    cell(row, user.role);
    cell(row, user.is_premium ? "Premium" : "Standard");
    cell(row, user.is_active ? "Active" : "Suspended");
    cell(row, user.reading_count);
    cell(row, new Date(user.created_at).toLocaleDateString());
    const actions = document.createElement("td");
    actions.className = "admin-actions";
    actions.append(
      button(user.is_premium ? "Revoke premium" : "Grant premium", "premium", user.id),
      button(user.role === "admin" ? "Make user" : "Make admin", "role", user.id),
      button(user.is_active ? "Suspend" : "Reactivate", "active", user.id),
      button("Reset password", "reset", user.id),
      button("Delete", "delete-user", user.id, true),
    );
    row.append(actions);
    usersBody.append(row);
  }
}

function renderReadings(readings) {
  // Keep the full record for the edit dialog while rendering only safe text nodes.
  readingsById.clear();
  readingsBody.replaceChildren();
  for (const reading of readings) {
    readingsById.set(reading.id, reading);
    const row = document.createElement("tr");
    cell(row, reading.id);
    cell(row, reading.owner_email || "Unassigned");
    cell(row, reading.participant_id);
    cell(row, new Date(reading.captured_at).toLocaleString());
    cell(row, reading.heart_rate_bpm == null ? "—" : `${reading.heart_rate_bpm} bpm`);
    const actions = document.createElement("td");
    actions.className = "admin-actions";
    actions.append(
      button("Edit", "edit-reading", reading.id),
      button("Delete", "delete-reading", reading.id, true),
    );
    row.append(actions);
    readingsBody.append(row);
  }
}

function renderAudit(events) {
  auditBody.replaceChildren();
  for (const event of events) {
    const row = document.createElement("tr");
    cell(row, new Date(event.created_at).toLocaleString());
    cell(row, event.actor_email || "Removed account");
    cell(row, event.action.replaceAll("_", " "));
    cell(row, event.target);
    auditBody.append(row);
  }
}

async function loadAdminData() {
  showMessage("Loading admin data…");
  try {
    const [overviewResponse, usersResponse, readingsResponse, auditResponse] = await Promise.all([
      request("/v1/admin/overview"),
      request("/v1/admin/users"),
      request("/v1/admin/readings"),
      request("/v1/admin/audit"),
    ]);
    const overview = overviewResponse.result.overview;
    for (const [key, value] of Object.entries(overview)) {
      const target = document.querySelector(`[data-stat="${key}"]`);
      if (target) {
        target.textContent = key === "database_bytes"
          ? `${(value / 1024).toFixed(1)} KB`
          : Number(value).toLocaleString();
      }
    }
    renderUsers(usersResponse.result.users);
    renderReadings(readingsResponse.result.readings);
    renderAudit(auditResponse.result.events);
    showMessage(`Signed in as administrator ${activeUser.email}.`);
  } catch (error) {
    showMessage(error.message, "error");
  }
}

async function updateUser(id, changes) {
  const {result} = await request(`/v1/admin/users/${id}`, {
    method: "PATCH",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify(changes),
  });
  return result;
}

usersBody.addEventListener("click", async (event) => {
  const control = event.target.closest("button[data-action]");
  if (!control) return;
  const id = Number(control.dataset.id);
  const user = [...usersBody.children].find((row) => Number(row.dataset.id) === id);
  const currentRole = user?.children[1].textContent;
  const currentPremium = user?.children[2].textContent === "Premium";
  const currentActive = user?.children[3].textContent === "Active";
  try {
    switch (control.dataset.action) {
      case "premium":
        await updateUser(id, {is_premium: !currentPremium});
        break;
      case "role":
        await updateUser(id, {role: currentRole === "admin" ? "user" : "admin"});
        break;
      case "active":
        await updateUser(id, {is_active: !currentActive});
        break;
      case "reset": {
        // The temporary password is returned once and is intentionally not stored in the audit log.
        if (!window.confirm("Reset this account password and sign out its active sessions?")) return;
        const {result} = await request(`/v1/admin/users/${id}/reset-password`, {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: "{}",
        });
        window.alert(`Temporary password (shown once):\n\n${result.temporary_password}\n\nCopy it now and share it securely.`);
        break;
      }
      case "delete-user":
        // Account deletion also permanently deletes the account's sensor records.
        if (!window.confirm("Permanently delete this account and all readings owned by it? This cannot be undone.")) return;
        await request(`/v1/admin/users/${id}`, {method: "DELETE"});
        break;
    }
    await loadAdminData();
  } catch (error) {
    showMessage(error.message, "error");
  }
});

readingsBody.addEventListener("click", async (event) => {
  const control = event.target.closest("button[data-action]");
  if (!control) return;
  const id = Number(control.dataset.id);
  const reading = readingsById.get(id);
  if (control.dataset.action === "delete-reading") {
    if (!window.confirm(`Permanently delete reading #${id}?`)) return;
    try {
      await request(`/v1/admin/readings/${id}`, {method: "DELETE"});
      await loadAdminData();
    } catch (error) {
      showMessage(error.message, "error");
    }
    return;
  }
  if (control.dataset.action !== "edit-reading" || !reading) return;
  document.querySelector("#editing-id").textContent = `#${id}`;
  editForm.dataset.id = id;
  for (const [key, value] of Object.entries(reading)) {
    const input = editForm.elements.namedItem(key);
    if (input) input.value = value ?? "";
  }
  readingDialog.showModal();
});

document.querySelector("#cancel-edit").addEventListener("click", () => readingDialog.close());
editForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const values = new FormData(editForm);
  const getNumber = (name) => {
    const value = values.get(name).trim();
    return value ? Number(value) : null;
  };
  const payload = {
    participant_id: values.get("participant_id").trim(),
    device_id: values.get("device_id").trim(),
    captured_at: values.get("captured_at").trim(),
    acceleration_m_s2: {
      x: getNumber("acceleration_m_s2_x"),
      y: getNumber("acceleration_m_s2_y"),
      z: getNumber("acceleration_m_s2_z"),
    },
    angular_velocity_rad_s: {
      x: getNumber("angular_velocity_rad_s_x"),
      y: getNumber("angular_velocity_rad_s_y"),
      z: getNumber("angular_velocity_rad_s_z"),
    },
    heart_rate_bpm: getNumber("heart_rate_bpm"),
  };
  try {
    await request(`/v1/admin/readings/${editForm.dataset.id}`, {
      method: "PUT",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(payload),
    });
    readingDialog.close();
    await loadAdminData();
  } catch (error) {
    showMessage(error.message, "error");
  }
});

document.querySelector("#create-account-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const values = new FormData(form);
  try {
    const {result} = await request("/v1/admin/users", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({
        email: values.get("email"),
        password: values.get("password"),
        role: values.get("role"),
        is_premium: values.has("is_premium"),
      }),
    });
    form.reset();
    showMessage(`Account #${result.id} created.`, "success");
    await loadAdminData();
  } catch (error) {
    showMessage(error.message, "error");
  }
});

document.querySelector("#backup-button").addEventListener("click", async () => {
  try {
    const {response} = await request("/v1/admin/backup");
    const url = URL.createObjectURL(await response.blob());
    const link = document.createElement("a");
    link.href = url;
    link.download = "stridesignal-backup.sqlite3";
    link.click();
    URL.revokeObjectURL(url);
    await loadAdminData();
  } catch (error) {
    showMessage(error.message, "error");
  }
});

document.querySelector("#refresh-admin").addEventListener("click", loadAdminData);
document.querySelector("#admin-sign-out").addEventListener("click", async () => {
  try {
    await request("/v1/auth/logout", {method: "POST"});
    window.location.assign("./dashboard.html");
  } catch (error) {
    showMessage(error.message, "error");
  }
});

async function start() {
  try {
    const {result} = await request("/v1/auth/me");
    activeUser = result.user;
    if (activeUser.role !== "admin") {
      showMessage("Administrator access is required.", "error");
      window.location.assign("./dashboard.html");
      return;
    }
    await loadAdminData();
  } catch (error) {
    showMessage(error.message, "error");
  }
}

start();
