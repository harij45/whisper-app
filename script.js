let currentRoom = null;
let rooms = {};
let isSending = false;
let username = null;
let currentRoomPage = 1;
let roomSearchQuery = "";
let sitePaused = false;
let isBlocked = false;
let currentMessages = [];
let selectedReportMessage = null;
let isReportingMessage = false;
let openMessageActionsId = null;
const blockedMessage = "You have been blocked.";
const roomsPerPage = 15;
const ownerTokens = readStoredJson("whisperOwnerTokens", {});
let identityToken = localStorage.getItem("whisperIdentityToken");
const mutedAliases = new Set(
    readStoredArray("whisperMutedAliases")
);
const revealedMutedMessageIds = new Set();
const roomDrafts = readStoredJson("whisperRoomDrafts", {});
const homeDraftKey = "whisperHomeDraft";

function readStoredJson(key, fallback) {
    try {
        const parsed = JSON.parse(localStorage.getItem(key) || "null");
        return parsed && typeof parsed === "object" ? parsed : fallback;
    } catch {
        return fallback;
    }
}

function readStoredArray(key) {
    const value = readStoredJson(key, []);
    return Array.isArray(value) ? value : [];
}

function generateSecureToken() {
    const values = new Uint32Array(8);
    crypto.getRandomValues(values);
    return Array.from(values, value => value.toString(16).padStart(8, "0")).join("");
}

if (!identityToken) {
    identityToken = generateSecureToken();
    localStorage.setItem("whisperIdentityToken", identityToken);
}
localStorage.removeItem("username");

function generateOwnerToken() {
    return generateSecureToken();
}

function saveOwnerTokens() {
    localStorage.setItem("whisperOwnerTokens", JSON.stringify(ownerTokens));
}

function saveMutedAliases() {
    localStorage.setItem(
        "whisperMutedAliases",
        JSON.stringify(Array.from(mutedAliases))
    );
}

function saveRoomDrafts() {
    localStorage.setItem("whisperRoomDrafts", JSON.stringify(roomDrafts));
}

function updateDraftStatus(elementId, hasDraft) {
    document.getElementById(elementId).textContent = hasDraft
        ? "Draft saved on this device."
        : "";
}

function saveHomeDraft() {
    const draft = document.getElementById("helpInput").value;
    if (draft) {
        localStorage.setItem(homeDraftKey, draft);
    } else {
        localStorage.removeItem(homeDraftKey);
    }
    updateDraftStatus("homeDraftStatus", Boolean(draft));
}

function saveChatDraft() {
    if (!currentRoom) return;
    const draft = document.getElementById("chatInput").value;
    if (draft) {
        roomDrafts[currentRoom] = draft;
    } else {
        delete roomDrafts[currentRoom];
    }
    saveRoomDrafts();
    updateDraftStatus("chatDraftStatus", Boolean(draft));
}

function clearRoomDraft(roomCode) {
    delete roomDrafts[roomCode];
    saveRoomDrafts();
    if (currentRoom === roomCode) {
        document.getElementById("chatInput").value = "";
        updateDraftStatus("chatDraftStatus", false);
    }
}

function formatTime(timestamp) {
    return new Date(timestamp).toLocaleTimeString([], {
        hour: "numeric",
        minute: "2-digit"
    });
}

async function api(path, options = {}) {
    const response = await fetch(path, {
        ...options,
        headers: {
            "Content-Type": "application/json",
            ...(options.headers || {})
        }
    });

    if (!response.ok) {
        let message = "Something went wrong. Please try again.";
        let code = "";
        try {
            const body = await response.json();
            message = body.error || message;
            code = body.code || "";
        } catch {
            // Keep the friendly fallback when the server returns no JSON.
        }
        const error = new Error(message);
        error.status = response.status;
        error.code = code;
        throw error;
    }

    return response.status === 204 ? null : response.json();
}

function setStatus(message, isError = false) {
    const status = document.getElementById("status");
    if (isBlocked) {
        message = blockedMessage;
        isError = true;
    }
    status.textContent = message;
    status.classList.toggle("error", isError);
}

function handleBlockedError(error) {
    if (error.code !== "IP_BLOCKED") return false;

    isBlocked = true;
    username = null;
    currentRoom = null;
    currentMessages = [];
    openMessageActionsId = null;
    rooms = {};
    document.getElementById("roomsList").replaceChildren();
    document.getElementById("roomPagination").classList.add("hidden");
    document.getElementById("chatMessages").replaceChildren();
    document.getElementById("chatStatus").textContent = "";
    document.getElementById("chatRoom").classList.add("hidden");
    document.getElementById("home").classList.remove("hidden");
    updateActionAvailability();
    setStatus(blockedMessage, true);
    return true;
}

function updateActionAvailability() {
    document.getElementById("startRoomBtn").disabled =
        !username || sitePaused || isBlocked;
    document.getElementById("sendMessageBtn").disabled =
        !username || sitePaused || isBlocked;
    document.getElementById("helpInput").disabled = isBlocked;
    document.getElementById("chatInput").disabled = isBlocked;
    document.getElementById("toggleRoomSearchBtn").disabled = isBlocked;
    document.getElementById("roomSearchInput").disabled = isBlocked;
    document.getElementById("clearRoomSearchBtn").disabled = isBlocked;
    document.getElementById("reportRoomBtn").disabled = isBlocked;
    document.getElementById("closeRoomBtn").disabled = isBlocked;
}

async function loadSiteConfig() {
    try {
        const config = await api("/api/site-config");
        sitePaused = config.paused;
        const notice = document.getElementById("siteNotice");
        const noticeText = config.notice || (
            sitePaused
                ? "Whisper is temporarily paused. You can read rooms, but new rooms and messages are disabled."
                : ""
        );
        notice.textContent = noticeText;
        notice.classList.toggle("hidden", !noticeText);
        notice.classList.toggle("paused", sitePaused);
        updateActionAvailability();
    } catch {
        // Keep the site usable if the optional status check briefly fails.
    }
}

async function loadRooms() {
    try {
        const data = await api("/api/rooms");
        rooms = Object.fromEntries(data.rooms.map(room => [room.code, room]));
        let removedDraft = false;
        for (const roomCode of Object.keys(roomDrafts)) {
            if (!rooms[roomCode] && roomCode !== currentRoom) {
                delete roomDrafts[roomCode];
                removedDraft = true;
            }
        }
        if (removedDraft) saveRoomDrafts();
        showRooms();
    } catch (error) {
        if (!handleBlockedError(error)) {
            setStatus(`Could not load rooms: ${error.message}`, true);
        }
    }
}

async function loadIdentity() {
    updateActionAvailability();

    try {
        const data = await api("/api/identity", {
            method: "POST",
            body: JSON.stringify({ identityToken })
        });
        username = data.alias;
        updateActionAvailability();
        if (currentMessages.length) renderMessages(currentMessages, true);
    } catch (error) {
        if (!handleBlockedError(error)) {
            setStatus(
                `Could not reserve your anonymous name: ${error.message}`,
                true
            );
        }
    }
}

async function createRoom() {
    const input = document.getElementById("helpInput");
    const helpText = input.value.trim();
    if (!helpText || !username || isSending || sitePaused) return;

    isSending = true;
    setStatus("Creating room…");
    const ownerToken = generateOwnerToken();

    try {
        const data = await api("/api/rooms", {
            method: "POST",
            body: JSON.stringify({ helpText, identityToken, ownerToken })
        });
        ownerTokens[data.room.code] = ownerToken;
        saveOwnerTokens();
        input.value = "";
        saveHomeDraft();
        rooms[data.room.code] = data.room;
        await joinRoom(data.room.code);
    } catch (error) {
        if (!handleBlockedError(error)) {
            setStatus(error.message, true);
        }
    } finally {
        isSending = false;
    }
}

function showRooms() {
    const list = document.getElementById("roomsList");
    const pagination = document.getElementById("roomPagination");
    const previousButton = document.getElementById("previousRoomsBtn");
    const nextButton = document.getElementById("nextRoomsBtn");
    const query = roomSearchQuery.toUpperCase();
    const matchingRooms = Object.values(rooms).filter(room =>
        room.code.includes(query)
    );
    const totalPages = Math.max(1, Math.ceil(matchingRooms.length / roomsPerPage));
    currentRoomPage = Math.min(Math.max(currentRoomPage, 1), totalPages);
    const pageStart = (currentRoomPage - 1) * roomsPerPage;
    const pageRooms = matchingRooms.slice(pageStart, pageStart + roomsPerPage);

    list.replaceChildren();

    pageRooms.forEach(room => {
        const card = document.createElement("button");
        card.type = "button";
        card.className = "room";

        const title = document.createElement("strong");
        title.textContent = `Room: ${room.code}`;
        const text = document.createElement("span");
        text.textContent = room.help;

        card.append(title, text);
        card.addEventListener("click", () => joinRoom(room.code));
        list.appendChild(card);
    });

    pagination.classList.toggle("hidden", totalPages <= 1);
    previousButton.disabled = currentRoomPage === 1;
    nextButton.disabled = currentRoomPage === totalPages;
    document.getElementById("roomPageIndicator").textContent =
        `${currentRoomPage} / ${totalPages}`;

    if (!Object.keys(rooms).length) {
        setStatus("No live rooms yet. Start the first whisper.");
    } else if (!matchingRooms.length) {
        setStatus(`No room found for “${roomSearchQuery}”.`);
    } else {
        setStatus("");
    }
}

function toggleRoomSearch() {
    const search = document.getElementById("roomSearch");
    const toggle = document.getElementById("toggleRoomSearchBtn");
    const willOpen = search.classList.contains("hidden");

    search.classList.toggle("hidden", !willOpen);
    toggle.setAttribute("aria-expanded", String(willOpen));

    if (willOpen) {
        document.getElementById("roomSearchInput").focus();
    } else {
        clearRoomSearch();
    }
}

function updateRoomSearch(event) {
    const input = event.currentTarget;
    const cleanedCode = input.value.toUpperCase().replace(/[^A-Z0-9]/g, "");
    input.value = cleanedCode;
    roomSearchQuery = cleanedCode;
    currentRoomPage = 1;
    showRooms();
}

function clearRoomSearch() {
    const input = document.getElementById("roomSearchInput");
    input.value = "";
    roomSearchQuery = "";
    currentRoomPage = 1;
    showRooms();
}

function changeRoomPage(direction) {
    currentRoomPage += direction;
    showRooms();
    document.querySelector(".sectionHeading").scrollIntoView({ block: "start" });
}

async function joinRoom(code) {
    if (isBlocked) return;

    currentRoom = code;
    currentMessages = [];
    openMessageActionsId = null;
    updateLiveStatus("Disconnected", "disconnected");
    const messages = document.getElementById("chatMessages");
    messages.replaceChildren();
    messages.scrollTop = 0;
    const savedDraft = roomDrafts[code] || "";
    document.getElementById("chatInput").value = savedDraft;
    updateDraftStatus("chatDraftStatus", Boolean(savedDraft));
    document.getElementById("chatStatus").textContent = "Loading room…";
    document.getElementById("home").classList.add("hidden");
    document.getElementById("chatRoom").classList.remove("hidden");
    document.getElementById("roomTitle").textContent = `Room: ${code}`;
    document.getElementById("closeRoomBtn").classList.toggle(
        "hidden",
        !ownerTokens[code]
    );
    await loadCurrentRoom();
}

async function loadCurrentRoom() {
    if (!currentRoom) return;
    const requestedRoom = currentRoom;

    try {
        const data = await api(`/api/rooms/${encodeURIComponent(requestedRoom)}`);
        if (currentRoom !== requestedRoom) return;
        renderMessages(data.messages);
        document.getElementById("chatStatus").textContent =
            isBlocked ? blockedMessage : "";
        updateLiveStatus("Connected", "connected");
    } catch (error) {
        if (handleBlockedError(error)) return;
        if (currentRoom !== requestedRoom) return;
        updateLiveStatus("Disconnected", "disconnected");
        if (error.message === "Room not found.") {
            goHome();
            setStatus("That room has been closed.", true);
        } else {
            document.getElementById("chatStatus").textContent = error.message;
        }
    }
}

function updateLiveStatus(message, state = "") {
    const status = document.getElementById("liveStatus");
    status.dataset.state = state;
    status.setAttribute("aria-label", message);
    status.title = message;
}

function muteAlias(alias) {
    if (!alias || alias === username) return;
    mutedAliases.add(alias);
    saveMutedAliases();
    renderMessages(currentMessages, true);
    document.getElementById("chatStatus").textContent =
        `${alias} is muted on this device.`;
}

function unmuteAlias(alias) {
    mutedAliases.delete(alias);
    saveMutedAliases();
    for (const message of currentMessages) {
        if (message.sender === alias) revealedMutedMessageIds.delete(message.id);
    }
    renderMessages(currentMessages, true);
    document.getElementById("chatStatus").textContent = `${alias} was unmuted.`;
}

function createMessageAction(label, action) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "messageActionBtn";
    button.textContent = label;
    button.addEventListener("click", action);
    return button;
}

function toggleMessageActions(messageId) {
    openMessageActionsId =
        openMessageActionsId === messageId ? null : messageId;
    renderMessages(currentMessages, true);
}

function renderMessages(messages, force = false) {
    const box = document.getElementById("chatMessages");
    const wasNearBottom =
        box.scrollHeight - box.scrollTop - box.clientHeight < 60;
    const previousScrollTop = box.scrollTop;
    const previousLastId = box.lastElementChild?.dataset.messageId;
    const nextLastId = messages.at(-1)?.id?.toString();

    if (
        !force
        && previousLastId === nextLastId
        && box.children.length === messages.length
    ) {
        return;
    }

    currentMessages = messages.slice();
    box.replaceChildren();
    messages.forEach(message => {
        const row = document.createElement("div");
        row.className = "message";
        row.dataset.messageId = message.id;
        const isMuted = mutedAliases.has(message.sender);
        const isRevealed = revealedMutedMessageIds.has(message.id);
        const hideContent = isMuted && !isRevealed;
        row.classList.toggle("mutedMessage", hideContent);

        const messageRow = document.createElement("div");
        messageRow.className = "msgRow";
        const text = document.createElement("span");
        text.className = "msgText";
        if (message.sender !== username) {
            const sender = document.createElement("button");
            sender.type = "button";
            sender.className = "messageSenderBtn";
            sender.textContent = `${message.sender}:`;
            sender.setAttribute(
                "aria-label",
                `Message actions for ${message.sender}`
            );
            sender.setAttribute(
                "aria-expanded",
                String(openMessageActionsId === message.id)
            );
            sender.setAttribute(
                "aria-controls",
                `message-actions-${message.id}`
            );
            sender.addEventListener(
                "click",
                () => toggleMessageActions(message.id)
            );
            text.append(
                sender,
                document.createTextNode(
                    hideContent ? " Message is muted." : ` ${message.text}`
                )
            );
        } else {
            const sender = document.createElement("strong");
            sender.textContent = `${message.sender}:`;
            text.append(sender, document.createTextNode(` ${message.text}`));
        }

        const time = document.createElement("time");
        time.className = "time";
        time.textContent = formatTime(message.createdAt);
        messageRow.append(text, time);
        row.appendChild(messageRow);

        if (message.sender !== username) {
            const actions = document.createElement("div");
            actions.className = "messageActions";
            actions.id = `message-actions-${message.id}`;
            actions.classList.toggle(
                "hidden",
                openMessageActionsId !== message.id
            );
            if (hideContent) {
                actions.appendChild(
                    createMessageAction("Show once", () => {
                        revealedMutedMessageIds.add(message.id);
                        renderMessages(currentMessages, true);
                    })
                );
                actions.appendChild(
                    createMessageAction("Unmute", () => unmuteAlias(message.sender))
                );
            } else {
                actions.appendChild(
                    createMessageAction(
                        "Report",
                        () => openMessageReport(message)
                    )
                );
                if (isMuted) {
                    actions.appendChild(
                        createMessageAction(
                            "Hide again",
                            () => {
                                revealedMutedMessageIds.delete(message.id);
                                renderMessages(currentMessages, true);
                            }
                        )
                    );
                    actions.appendChild(
                        createMessageAction(
                            "Unmute",
                            () => unmuteAlias(message.sender)
                        )
                    );
                } else {
                    actions.appendChild(
                        createMessageAction(
                            "Mute sender",
                            () => muteAlias(message.sender)
                        )
                    );
                }
            }
            row.appendChild(actions);
        }
        box.appendChild(row);
    });

    if (wasNearBottom || !previousLastId) {
        box.scrollTop = box.scrollHeight;
    } else {
        box.scrollTop = previousScrollTop;
    }
}

function openMessageReport(message) {
    selectedReportMessage = message;
    const dialog = document.getElementById("messageReportDialog");
    document.getElementById("messageReportHeading").textContent =
        `Report message from ${message.sender}`;
    document.getElementById("messageReportReason").value = "";
    document.getElementById("messageReportDetails").value = "";
    document.getElementById("messageReportStatus").textContent = "";
    dialog.showModal();
    document.getElementById("messageReportReason").focus();
}

function closeMessageReport() {
    if (isReportingMessage) return;
    selectedReportMessage = null;
    document.getElementById("messageReportDialog").close();
}

async function submitMessageReport(event) {
    event.preventDefault();
    if (!selectedReportMessage || !currentRoom || isReportingMessage) return;

    const reason = document.getElementById("messageReportReason").value;
    const details = document.getElementById("messageReportDetails").value.trim();
    const status = document.getElementById("messageReportStatus");
    if (!reason) {
        status.textContent = "Choose a report reason.";
        status.classList.add("error");
        return;
    }
    if (reason === "other" && !details) {
        status.textContent = "Please briefly describe the issue.";
        status.classList.add("error");
        return;
    }

    isReportingMessage = true;
    status.textContent = "Sending report…";
    status.classList.remove("error");
    document.getElementById("submitMessageReportBtn").disabled = true;

    try {
        const message = selectedReportMessage;
        const result = await api(
            `/api/rooms/${encodeURIComponent(currentRoom)}/messages/${message.id}/report`,
            {
                method: "POST",
                body: JSON.stringify({
                    identityToken,
                    reason,
                    details
                })
            }
        );
        document.getElementById("chatStatus").textContent = result.created
            ? "Message reported privately to the moderators."
            : "You already reported this message.";
        selectedReportMessage = null;
        document.getElementById("messageReportDialog").close();
    } catch (error) {
        if (!handleBlockedError(error)) {
            status.textContent = error.message;
            status.classList.add("error");
        }
    } finally {
        isReportingMessage = false;
        document.getElementById("submitMessageReportBtn").disabled = false;
    }
}

async function sendMessage() {
    const input = document.getElementById("chatInput");
    const text = input.value.trim();
    if (!text || !currentRoom || !username || isSending || sitePaused) return;

    isSending = true;
    document.getElementById("chatStatus").textContent = "Sending…";

    try {
        await api(`/api/rooms/${encodeURIComponent(currentRoom)}/messages`, {
            method: "POST",
            body: JSON.stringify({ identityToken, text })
        });
        input.value = "";
        saveChatDraft();
        await loadCurrentRoom();
    } catch (error) {
        if (!handleBlockedError(error)) {
            document.getElementById("chatStatus").textContent = error.message;
        }
    } finally {
        isSending = false;
    }
}

function goHome() {
    const reportDialog = document.getElementById("messageReportDialog");
    if (reportDialog.open && !isReportingMessage) reportDialog.close();
    selectedReportMessage = null;
    currentRoom = null;
    currentMessages = [];
    openMessageActionsId = null;
    document.getElementById("chatMessages").replaceChildren();
    document.getElementById("chatStatus").textContent = "";
    document.getElementById("chatDraftStatus").textContent = "";
    document.getElementById("chatRoom").classList.add("hidden");
    document.getElementById("home").classList.remove("hidden");
    loadRooms();
}

async function closeRoom() {
    if (!currentRoom || !ownerTokens[currentRoom]) return;
    if (!confirm("Close this room for everyone?")) return;

    const roomCode = currentRoom;
    try {
        await api(`/api/rooms/${encodeURIComponent(roomCode)}`, {
            method: "DELETE",
            body: JSON.stringify({ ownerToken: ownerTokens[roomCode] })
        });
        delete ownerTokens[roomCode];
        saveOwnerTokens();
        clearRoomDraft(roomCode);
        goHome();
    } catch (error) {
        if (!handleBlockedError(error)) {
            document.getElementById("chatStatus").textContent = error.message;
        }
    }
}

async function reportRoom() {
    if (!currentRoom || !confirm("Report this room?")) return;

    try {
        await api(`/api/rooms/${encodeURIComponent(currentRoom)}/report`, {
            method: "POST",
            body: "{}"
        });
        alert("Room reported successfully.");
    } catch (error) {
        if (!handleBlockedError(error)) {
            document.getElementById("chatStatus").textContent = error.message;
        }
    }
}

document.addEventListener("keydown", event => {
    if (event.key === "Enter" && !event.shiftKey) {
        if (document.activeElement === document.getElementById("chatInput")) {
            event.preventDefault();
            sendMessage();
        } else if (document.activeElement === document.getElementById("helpInput")) {
            event.preventDefault();
            createRoom();
        } else if (document.activeElement === document.getElementById("roomSearchInput")) {
            const exactRoom = rooms[roomSearchQuery];
            if (exactRoom) {
                event.preventDefault();
                joinRoom(exactRoom.code);
            }
        }
    }

    if (event.key === "Escape" && currentRoom) {
        goHome();
    }
});

document.getElementById("startRoomBtn").addEventListener("click", createRoom);
document.getElementById("backHomeBtn").addEventListener("click", goHome);
document.getElementById("reportRoomBtn").addEventListener("click", reportRoom);
document.getElementById("closeRoomBtn").addEventListener("click", closeRoom);
document.getElementById("sendMessageBtn").addEventListener("click", sendMessage);
document.getElementById("helpInput").addEventListener("input", saveHomeDraft);
document.getElementById("chatInput").addEventListener("input", saveChatDraft);
document.getElementById("toggleRoomSearchBtn").addEventListener("click", toggleRoomSearch);
document.getElementById("roomSearchInput").addEventListener("input", updateRoomSearch);
document.getElementById("clearRoomSearchBtn").addEventListener("click", clearRoomSearch);
document.getElementById("previousRoomsBtn").addEventListener(
    "click",
    () => changeRoomPage(-1)
);
document.getElementById("nextRoomsBtn").addEventListener(
    "click",
    () => changeRoomPage(1)
);
document.getElementById("messageReportForm").addEventListener(
    "submit",
    submitMessageReport
);
document.getElementById("cancelMessageReportBtn").addEventListener(
    "click",
    closeMessageReport
);
document.getElementById("dismissMessageReportBtn").addEventListener(
    "click",
    closeMessageReport
);
document.getElementById("messageReportDialog").addEventListener("cancel", event => {
    if (isReportingMessage) {
        event.preventDefault();
    } else {
        selectedReportMessage = null;
    }
});

setInterval(() => {
    if (document.hidden) return;
    if (currentRoom) {
        loadCurrentRoom();
    }
}, 2000);

setInterval(() => {
    if (document.hidden) return;
    if (!currentRoom) {
        loadRooms();
    }
}, 15000);

setInterval(loadSiteConfig, 15000);

document.addEventListener("visibilitychange", () => {
    if (document.hidden) return;
    if (currentRoom) {
        loadCurrentRoom();
    } else {
        loadRooms();
    }
});

const savedHomeDraft = localStorage.getItem(homeDraftKey) || "";
document.getElementById("helpInput").value = savedHomeDraft;
updateDraftStatus("homeDraftStatus", Boolean(savedHomeDraft));
loadRooms();
loadSiteConfig();
loadIdentity();
