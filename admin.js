let adminOverview = null;
let selectedRoomCode = null;
let pendingPaused = false;

async function adminApi(path, options = {}) {
    const method = options.method || "GET";
    const headers = {
        "Content-Type": "application/json",
        ...(options.headers || {})
    };
    if (method !== "GET") {
        headers["X-Admin-Action"] = "Whisper-Admin";
    }

    const response = await fetch(path, {
        ...options,
        method,
        headers,
        credentials: "same-origin"
    });

    if (!response.ok) {
        let message = "The admin action could not be completed.";
        try {
            const body = await response.json();
            message = body.error || message;
        } catch {
            // Keep the fallback for unexpected server responses.
        }
        throw new Error(message);
    }

    return response.status === 204 ? null : response.json();
}

function setAdminStatus(message, isError = false) {
    const status = document.getElementById("adminStatus");
    status.textContent = message;
    status.classList.toggle("error", isError);
}

function formatAdminDate(timestamp) {
    return new Date(timestamp).toLocaleString([], {
        dateStyle: "medium",
        timeStyle: "short"
    });
}

function renderSiteState() {
    const badge = document.getElementById("siteStateBadge");
    const toggle = document.getElementById("togglePauseBtn");
    badge.textContent = pendingPaused ? "Paused" : "Live";
    badge.classList.toggle("paused", pendingPaused);
    toggle.setAttribute("aria-pressed", String(pendingPaused));
    toggle.textContent = pendingPaused
        ? "Resume rooms and messages"
        : "Pause rooms and messages";
}

function renderStats() {
    const stats = document.getElementById("adminStats");
    const labels = [
        ["rooms", "Live rooms"],
        ["messages", "Messages"],
        ["identities", "Reserved names"],
        ["reports", "Reports"]
    ];
    stats.replaceChildren();

    labels.forEach(([key, label]) => {
        const card = document.createElement("div");
        card.className = "adminStat";
        const value = document.createElement("strong");
        value.textContent = adminOverview.stats[key].toLocaleString();
        const name = document.createElement("span");
        name.textContent = label;
        card.append(value, name);
        stats.appendChild(card);
    });
}

function renderRooms() {
    const list = document.getElementById("adminRooms");
    const query = document.getElementById("adminRoomSearch").value
        .trim()
        .toLowerCase();
    const matching = adminOverview.rooms.filter(room =>
        room.code.toLowerCase().includes(query)
        || room.help.toLowerCase().includes(query)
    );
    list.replaceChildren();

    if (!matching.length) {
        const empty = document.createElement("p");
        empty.className = "adminEmpty";
        empty.textContent = query ? "No matching rooms." : "No live rooms.";
        list.appendChild(empty);
        return;
    }

    matching.forEach(room => {
        const row = document.createElement("article");
        row.className = "adminRoom";

        const info = document.createElement("div");
        const title = document.createElement("strong");
        title.textContent = room.code;
        const help = document.createElement("p");
        help.textContent = room.help;
        const meta = document.createElement("span");
        meta.textContent = `${room.messageCount} messages · ${room.reportCount} reports · ${formatAdminDate(room.createdAt)}`;
        info.append(title, help, meta);

        const actions = document.createElement("div");
        actions.className = "adminRoomActions";
        const openButton = document.createElement("button");
        openButton.type = "button";
        openButton.className = "secondaryBtn";
        openButton.textContent = "Manage";
        openButton.addEventListener("click", () => loadAdminRoom(room.code));
        const deleteButton = document.createElement("button");
        deleteButton.type = "button";
        deleteButton.className = "dangerBtn";
        deleteButton.textContent = "Delete";
        deleteButton.addEventListener("click", () => deleteAdminRoom(room.code));
        actions.append(openButton, deleteButton);

        row.append(info, actions);
        list.appendChild(row);
    });
}

async function loadAdminOverview(showMessage = false) {
    try {
        const data = await adminApi("/api/admin/overview");
        adminOverview = data;
        pendingPaused = data.config.paused;
        document.getElementById("siteNotice").value = data.config.notice;
        renderSiteState();
        renderStats();
        renderRooms();
        if (showMessage) setAdminStatus("Dashboard refreshed.");
    } catch (error) {
        setAdminStatus(error.message, true);
    }
}

async function saveSiteSettings() {
    const notice = document.getElementById("siteNotice").value.trim();
    try {
        const config = await adminApi("/api/admin/settings", {
            method: "PUT",
            body: JSON.stringify({ notice, paused: pendingPaused })
        });
        adminOverview.config = config;
        pendingPaused = config.paused;
        renderSiteState();
        setAdminStatus("Site settings saved.");
    } catch (error) {
        setAdminStatus(error.message, true);
    }
}

async function loadAdminRoom(code) {
    try {
        const data = await adminApi(`/api/admin/rooms/${encodeURIComponent(code)}`);
        selectedRoomCode = code;
        document.getElementById("roomDetailHeading").textContent = `Room ${code}`;
        document.getElementById("roomDetailText").textContent =
            `${data.room.help} · ${data.reportCount} reports`;
        renderAdminMessages(data.messages);
        const detail = document.getElementById("roomDetailSection");
        detail.classList.remove("hidden");
        detail.scrollIntoView({ behavior: "smooth", block: "start" });
    } catch (error) {
        setAdminStatus(error.message, true);
    }
}

function renderAdminMessages(messages) {
    const list = document.getElementById("adminMessages");
    list.replaceChildren();

    messages.forEach(message => {
        const row = document.createElement("article");
        row.className = "adminMessage";
        const info = document.createElement("div");
        const sender = document.createElement("strong");
        sender.textContent = message.sender;
        const text = document.createElement("p");
        text.textContent = message.text;
        const time = document.createElement("span");
        time.textContent = formatAdminDate(message.createdAt);
        info.append(sender, text, time);

        const deleteButton = document.createElement("button");
        deleteButton.type = "button";
        deleteButton.className = "dangerBtn";
        deleteButton.textContent = "Delete message";
        deleteButton.addEventListener("click", () =>
            deleteAdminMessage(message.id)
        );
        row.append(info, deleteButton);
        list.appendChild(row);
    });
}

async function deleteAdminMessage(messageId) {
    if (!confirm("Delete this message permanently?")) return;
    try {
        await adminApi(`/api/admin/messages/${messageId}`, {
            method: "DELETE"
        });
        await Promise.all([
            loadAdminRoom(selectedRoomCode),
            loadAdminOverview()
        ]);
        setAdminStatus("Message deleted.");
    } catch (error) {
        setAdminStatus(error.message, true);
    }
}

async function deleteAdminRoom(code) {
    if (!confirm(`Delete room ${code} and all of its messages?`)) return;
    try {
        await adminApi(`/api/admin/rooms/${encodeURIComponent(code)}`, {
            method: "DELETE"
        });
        if (selectedRoomCode === code) closeRoomDetail();
        await loadAdminOverview();
        setAdminStatus(`Room ${code} deleted.`);
    } catch (error) {
        setAdminStatus(error.message, true);
    }
}

async function clearReports(roomCode = "") {
    const description = roomCode ? `room ${roomCode}` : "every room";
    if (!confirm(`Clear reports for ${description}?`)) return;
    try {
        await adminApi("/api/admin/reports", {
            method: "DELETE",
            body: JSON.stringify({ roomCode })
        });
        await loadAdminOverview();
        if (roomCode) await loadAdminRoom(roomCode);
        setAdminStatus("Reports cleared.");
    } catch (error) {
        setAdminStatus(error.message, true);
    }
}

async function deleteAllRooms() {
    const confirmation = prompt(
        "This permanently deletes every live room and message. Type DELETE ALL ROOMS to continue."
    );
    if (confirmation !== "DELETE ALL ROOMS") return;

    try {
        await adminApi("/api/admin/rooms", {
            method: "DELETE",
            body: JSON.stringify({ confirmation })
        });
        closeRoomDetail();
        await loadAdminOverview();
        setAdminStatus("All rooms and messages deleted.");
    } catch (error) {
        setAdminStatus(error.message, true);
    }
}

function closeRoomDetail() {
    selectedRoomCode = null;
    document.getElementById("roomDetailSection").classList.add("hidden");
    document.getElementById("adminMessages").replaceChildren();
}

document.getElementById("togglePauseBtn").addEventListener("click", () => {
    pendingPaused = !pendingPaused;
    renderSiteState();
});
document.getElementById("saveSettingsBtn").addEventListener("click", saveSiteSettings);
document.getElementById("refreshAdminBtn").addEventListener(
    "click",
    () => loadAdminOverview(true)
);
document.getElementById("adminRoomSearch").addEventListener("input", renderRooms);
document.getElementById("clearAllReportsBtn").addEventListener(
    "click",
    () => clearReports()
);
document.getElementById("clearRoomReportsBtn").addEventListener(
    "click",
    () => clearReports(selectedRoomCode)
);
document.getElementById("deleteSelectedRoomBtn").addEventListener(
    "click",
    () => deleteAdminRoom(selectedRoomCode)
);
document.getElementById("closeRoomDetailBtn").addEventListener(
    "click",
    closeRoomDetail
);
document.getElementById("deleteAllRoomsBtn").addEventListener(
    "click",
    deleteAllRooms
);

loadAdminOverview();
