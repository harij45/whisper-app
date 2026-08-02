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

    if (response.status === 401) {
        window.location.replace("/admin/login");
        throw new Error("Your admin session has ended.");
    }

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
        ["reports", "Room reports"],
        ["messageReports", "Message reports"],
        ["bans", "Bans"]
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
        meta.textContent =
            `${room.messageCount} messages · `
            + `${room.reportCount} room reports · `
            + `${room.messageReportCount} message reports · `
            + formatAdminDate(room.createdAt);
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

function renderReportedRooms() {
    const list = document.getElementById("adminReportedRooms");
    const count = document.getElementById("reportedRoomsCount");
    const reportedRooms = adminOverview.rooms
        .filter(room => room.reportCount > 0)
        .sort((first, second) =>
            second.reportCount - first.reportCount
            || second.createdAt - first.createdAt
        );
    const totalReports = adminOverview.stats.reports;

    count.textContent = `${totalReports} ${totalReports === 1 ? "report" : "reports"}`;
    count.classList.toggle("paused", totalReports > 0);
    list.replaceChildren();

    if (!reportedRooms.length) {
        const empty = document.createElement("p");
        empty.className = "adminEmpty";
        empty.textContent = "No reported rooms.";
        list.appendChild(empty);
        return;
    }

    reportedRooms.forEach(room => {
        const row = document.createElement("article");
        row.className = "adminRoom";

        const info = document.createElement("div");
        const title = document.createElement("strong");
        title.textContent = room.code;
        const help = document.createElement("p");
        help.textContent = room.help;
        const meta = document.createElement("span");
        const reportLabel = room.reportCount === 1 ? "report" : "reports";
        meta.textContent =
            `${room.reportCount} ${reportLabel} · `
            + `${room.messageCount} messages · `
            + formatAdminDate(room.createdAt);
        info.append(title, help, meta);

        const actions = document.createElement("div");
        actions.className = "adminRoomActions";
        const reviewButton = document.createElement("button");
        reviewButton.type = "button";
        reviewButton.className = "secondaryBtn";
        reviewButton.textContent = "Review";
        reviewButton.addEventListener("click", () => loadAdminRoom(room.code));
        const clearButton = document.createElement("button");
        clearButton.type = "button";
        clearButton.className = "secondaryBtn";
        clearButton.textContent = "Clear";
        clearButton.addEventListener("click", () => clearReports(room.code));
        const deleteButton = document.createElement("button");
        deleteButton.type = "button";
        deleteButton.className = "dangerBtn";
        deleteButton.textContent = "Delete";
        deleteButton.addEventListener("click", () => deleteAdminRoom(room.code));
        actions.append(reviewButton, clearButton, deleteButton);

        row.append(info, actions);
        list.appendChild(row);
    });
}

function renderBans() {
    const list = document.getElementById("adminBans");
    const status = document.getElementById("ipModerationStatus");
    list.replaceChildren();

    status.textContent = adminOverview.ipModerationConfigured
        ? "Recent addresses are encrypted and are visible only in this admin session. They are removed from user records after 30 days."
        : "IP moderation is disabled. Add IP_PRIVACY_KEY in Render to enable encrypted address viewing and bans.";
    status.classList.toggle(
        "error",
        !adminOverview.ipModerationConfigured
    );

    if (!adminOverview.bans.length) {
        const empty = document.createElement("p");
        empty.className = "adminEmpty";
        empty.textContent = "No active bans.";
        list.appendChild(empty);
        return;
    }

    adminOverview.bans.forEach(ban => {
        const row = document.createElement("article");
        row.className = "adminBan";
        const info = document.createElement("div");
        const address = document.createElement("strong");
        address.textContent = ban.ipAddress
            ? `IP address: ${ban.ipAddress}`
            : "IP address: unavailable";
        const alias = document.createElement("p");
        alias.textContent = ban.alias
            ? `Username: ${ban.alias}`
            : "Username: unavailable";
        const reason = document.createElement("p");
        reason.textContent = `Reason: ${ban.reason}`;
        const date = document.createElement("span");
        date.textContent = `Banned ${formatAdminDate(ban.createdAt)}`;
        const expiry = document.createElement("span");
        expiry.className = "adminBanExpiry";
        expiry.textContent = ban.expiresAt
            ? `Expires ${formatAdminDate(ban.expiresAt)}`
            : "Permanent ban";
        info.append(address, alias, reason, date, expiry);

        const unbanButton = document.createElement("button");
        unbanButton.type = "button";
        unbanButton.className = "secondaryBtn";
        unbanButton.textContent = "Remove ban";
        unbanButton.addEventListener("click", () => removeBan(ban.id));
        row.append(info, unbanButton);
        list.appendChild(row);
    });
}

function formatReportReason(reason) {
    const labels = {
        harassment: "Harassment or bullying",
        hate: "Hateful content",
        threat: "Threat or immediate danger",
        "self-harm": "Self-harm concern",
        spam: "Spam or scam",
        other: "Something else"
    };
    return labels[reason] || reason;
}

function renderMessageReports() {
    const list = document.getElementById("adminMessageReports");
    const count = document.getElementById("reportedMessagesCount");
    const reports = adminOverview.messageReports;
    const total = adminOverview.stats.messageReports;
    count.textContent = `${total} ${total === 1 ? "report" : "reports"}`;
    count.classList.toggle("paused", total > 0);
    list.replaceChildren();

    if (!reports.length) {
        const empty = document.createElement("p");
        empty.className = "adminEmpty";
        empty.textContent = "No open message reports.";
        list.appendChild(empty);
        return;
    }

    reports.forEach(report => {
        const row = document.createElement("article");
        row.className = "adminMessageReport";

        const heading = document.createElement("div");
        heading.className = "adminReportHeading";
        const title = document.createElement("strong");
        title.textContent = `${report.sender} · Room ${report.roomCode}`;
        const date = document.createElement("span");
        date.textContent = formatAdminDate(report.createdAt);
        heading.append(title, date);

        const message = document.createElement("p");
        message.className = "adminReportedText";
        message.textContent = report.text;
        const reason = document.createElement("p");
        reason.className = "adminReportReason";
        reason.textContent = `Reason: ${formatReportReason(report.reason)}`;
        const details = document.createElement("p");
        details.className = "adminReportDetails";
        details.textContent = report.details
            ? `Reporter details: ${report.details}`
            : "Reporter details: none";

        const noteLabel = document.createElement("label");
        noteLabel.htmlFor = `moderatorNote-${report.id}`;
        noteLabel.textContent = "Private moderator note";
        const note = document.createElement("textarea");
        note.id = `moderatorNote-${report.id}`;
        note.maxLength = 1000;
        note.value = report.moderatorNote;
        note.placeholder = "Record context or the action taken";

        const actions = document.createElement("div");
        actions.className = "adminButtonRow";
        const roomButton = document.createElement("button");
        roomButton.type = "button";
        roomButton.className = "secondaryBtn";
        roomButton.textContent = "Manage room";
        roomButton.addEventListener("click", () =>
            loadAdminRoom(report.roomCode)
        );
        const saveButton = document.createElement("button");
        saveButton.type = "button";
        saveButton.className = "secondaryBtn";
        saveButton.textContent = "Save note";
        saveButton.addEventListener("click", () =>
            updateMessageReport(report.id, "save", note.value)
        );
        const deleteButton = document.createElement("button");
        deleteButton.type = "button";
        deleteButton.className = "dangerBtn";
        deleteButton.textContent = "Delete message";
        deleteButton.addEventListener("click", () =>
            deleteAdminMessage(report.messageId)
        );
        const resolveButton = document.createElement("button");
        resolveButton.type = "button";
        resolveButton.textContent = "Resolve report";
        resolveButton.addEventListener("click", () =>
            updateMessageReport(report.id, "resolve", note.value)
        );
        actions.append(roomButton, saveButton, deleteButton, resolveButton);

        row.append(
            heading,
            message,
            reason,
            details,
            noteLabel,
            note,
            actions
        );
        list.appendChild(row);
    });
}

function renderAuditLog() {
    const list = document.getElementById("adminAuditLog");
    list.replaceChildren();

    if (!adminOverview.auditLog.length) {
        const empty = document.createElement("p");
        empty.className = "adminEmpty";
        empty.textContent = "No admin actions recorded yet.";
        list.appendChild(empty);
        return;
    }

    adminOverview.auditLog.forEach(entry => {
        const row = document.createElement("article");
        row.className = "adminAuditEntry";
        const heading = document.createElement("div");
        const action = document.createElement("strong");
        action.textContent = entry.action.replaceAll("_", " ");
        const target = document.createElement("span");
        target.textContent = entry.target;
        heading.append(action, target);
        const details = document.createElement("p");
        details.textContent = entry.details || "No additional details.";
        const time = document.createElement("time");
        time.textContent = formatAdminDate(entry.createdAt);
        row.append(heading, details, time);
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
        renderReportedRooms();
        renderMessageReports();
        renderRooms();
        renderBans();
        renderAuditLog();
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
        time.textContent =
            `${formatAdminDate(message.createdAt)} · `
            + `${message.messageReportCount} open `
            + `${message.messageReportCount === 1 ? "report" : "reports"}`;
        const address = document.createElement("span");
        address.className = "adminIp";
        address.textContent = message.ipAddress
            ? `Recent IP: ${message.ipAddress}`
            : "Recent IP: unavailable";
        info.append(sender, text, time, address);

        const actions = document.createElement("div");
        actions.className = "adminMessageActions";
        const moderationButton = document.createElement("button");
        moderationButton.type = "button";
        moderationButton.className = message.banId ? "secondaryBtn" : "dangerBtn";
        moderationButton.textContent = message.banId ? "Remove IP ban" : "Ban IP";
        moderationButton.disabled = message.banId
            ? false
            : !message.identityId || !message.ipAddress;
        moderationButton.addEventListener("click", () => {
            if (message.banId) {
                removeBan(message.banId, true);
            } else {
                banIdentity(message);
            }
        });
        const deleteButton = document.createElement("button");
        deleteButton.type = "button";
        deleteButton.className = "dangerBtn";
        deleteButton.textContent = "Delete message";
        deleteButton.addEventListener("click", () =>
            deleteAdminMessage(message.id)
        );
        actions.append(moderationButton, deleteButton);
        row.append(info, actions);
        list.appendChild(row);
    });
}

async function banIdentity(message) {
    const reason = prompt(
        `Ban the recent IP used by ${message.sender}? Enter a reason:`,
        "Abuse or spam"
    );
    if (reason === null) return;
    const durationInput = prompt(
        "How many hours should the ban last? Leave blank for a permanent ban.",
        "24"
    );
    if (durationInput === null) return;
    const cleanedDuration = durationInput.trim();
    const durationHours = cleanedDuration === ""
        ? null
        : Number(cleanedDuration);
    if (
        durationHours !== null
        && (
            !Number.isInteger(durationHours)
            || durationHours < 1
            || durationHours > 8760
        )
    ) {
        setAdminStatus(
            "Ban duration must be a whole number from 1 to 8,760 hours.",
            true
        );
        return;
    }

    try {
        await adminApi("/api/admin/bans", {
            method: "POST",
            body: JSON.stringify({
                identityId: message.identityId,
                reason: reason.trim(),
                durationHours
            })
        });
        await Promise.all([
            loadAdminRoom(selectedRoomCode),
            loadAdminOverview()
        ]);
        setAdminStatus(`${message.sender}’s recent IP was banned.`);
    } catch (error) {
        setAdminStatus(error.message, true);
    }
}

async function updateMessageReport(reportId, action, moderatorNote) {
    try {
        await adminApi(`/api/admin/message-reports/${reportId}`, {
            method: "PUT",
            body: JSON.stringify({
                action,
                moderatorNote: moderatorNote.trim()
            })
        });
        await loadAdminOverview();
        if (selectedRoomCode) await loadAdminRoom(selectedRoomCode);
        setAdminStatus(
            action === "resolve"
                ? "Message report resolved."
                : "Moderator note saved."
        );
    } catch (error) {
        setAdminStatus(error.message, true);
    }
}

async function removeBan(banId, refreshRoom = false) {
    if (!confirm("Remove this ban?")) return;
    try {
        await adminApi(`/api/admin/bans/${banId}`, {
            method: "DELETE"
        });
        await loadAdminOverview();
        if (refreshRoom && selectedRoomCode) {
            await loadAdminRoom(selectedRoomCode);
        }
        setAdminStatus("Ban removed.");
    } catch (error) {
        setAdminStatus(error.message, true);
    }
}

async function deleteAdminMessage(messageId) {
    if (!confirm("Delete this message permanently?")) return;
    try {
        await adminApi(`/api/admin/messages/${messageId}`, {
            method: "DELETE"
        });
        const refreshes = [loadAdminOverview()];
        if (selectedRoomCode) {
            refreshes.push(loadAdminRoom(selectedRoomCode));
        }
        await Promise.all(refreshes);
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

async function signOut() {
    try {
        await adminApi("/api/admin/logout", {
            method: "POST",
            body: "{}"
        });
        window.location.replace("/admin/login");
    } catch (error) {
        setAdminStatus(error.message, true);
    }
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
document.getElementById("adminLogoutBtn").addEventListener("click", signOut);

loadAdminOverview();
