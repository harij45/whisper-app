const names = ["Shadow", "Ghost", "Silent", "Dark", "Hidden"];
const animals = ["Fox", "Wolf", "Tiger", "Raven", "Viper"];

let username = localStorage.getItem("username");
if (!username) {
    username = generateName();
    localStorage.setItem("username", username);
}

let currentRoom = null;
let rooms = {};
let isSending = false;
let currentRoomPage = 1;
let roomSearchQuery = "";
const roomsPerPage = 15;
const ownerTokens = JSON.parse(localStorage.getItem("whisperOwnerTokens") || "{}");

function generateName() {
    const name = names[Math.floor(Math.random() * names.length)];
    const animal = animals[Math.floor(Math.random() * animals.length)];
    return `${name}${animal}${Math.floor(Math.random() * 1000)}`;
}

function generateOwnerToken() {
    const values = new Uint32Array(8);
    crypto.getRandomValues(values);
    return Array.from(values, value => value.toString(16).padStart(8, "0")).join("");
}

function saveOwnerTokens() {
    localStorage.setItem("whisperOwnerTokens", JSON.stringify(ownerTokens));
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
        try {
            const body = await response.json();
            message = body.error || message;
        } catch {
            // Keep the friendly fallback when the server returns no JSON.
        }
        throw new Error(message);
    }

    return response.status === 204 ? null : response.json();
}

function setStatus(message, isError = false) {
    const status = document.getElementById("status");
    status.textContent = message;
    status.classList.toggle("error", isError);
}

async function loadRooms() {
    try {
        const data = await api("/api/rooms");
        rooms = Object.fromEntries(data.rooms.map(room => [room.code, room]));
        showRooms();
    } catch (error) {
        setStatus(`Could not load rooms: ${error.message}`, true);
    }
}

async function createRoom() {
    const input = document.getElementById("helpInput");
    const helpText = input.value.trim();
    if (!helpText || isSending) return;

    isSending = true;
    setStatus("Creating room…");
    const ownerToken = generateOwnerToken();

    try {
        const data = await api("/api/rooms", {
            method: "POST",
            body: JSON.stringify({ helpText, sender: username, ownerToken })
        });
        ownerTokens[data.room.code] = ownerToken;
        saveOwnerTokens();
        input.value = "";
        rooms[data.room.code] = data.room;
        await joinRoom(data.room.code);
    } catch (error) {
        setStatus(error.message, true);
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
    currentRoom = code;
    const messages = document.getElementById("chatMessages");
    messages.replaceChildren();
    messages.scrollTop = 0;
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
        document.getElementById("chatStatus").textContent = "";
    } catch (error) {
        if (currentRoom !== requestedRoom) return;
        if (error.message === "Room not found.") {
            goHome();
            setStatus("That room has been closed.", true);
        } else {
            document.getElementById("chatStatus").textContent = error.message;
        }
    }
}

function renderMessages(messages) {
    const box = document.getElementById("chatMessages");
    const wasNearBottom =
        box.scrollHeight - box.scrollTop - box.clientHeight < 60;
    const previousLastId = box.lastElementChild?.dataset.messageId;
    const nextLastId = messages.at(-1)?.id?.toString();

    if (previousLastId === nextLastId && box.children.length === messages.length) {
        return;
    }

    box.replaceChildren();
    messages.forEach(message => {
        const row = document.createElement("div");
        row.className = "message";
        row.dataset.messageId = message.id;

        const messageRow = document.createElement("div");
        messageRow.className = "msgRow";
        const text = document.createElement("span");
        text.className = "msgText";
        const sender = document.createElement("strong");
        sender.textContent = `${message.sender}: `;
        text.append(sender, document.createTextNode(message.text));

        const time = document.createElement("time");
        time.className = "time";
        time.textContent = formatTime(message.createdAt);
        messageRow.append(text, time);
        row.appendChild(messageRow);
        box.appendChild(row);
    });

    if (wasNearBottom || !previousLastId) {
        box.scrollTop = box.scrollHeight;
    }
}

async function sendMessage() {
    const input = document.getElementById("chatInput");
    const text = input.value.trim();
    if (!text || !currentRoom || isSending) return;

    isSending = true;
    document.getElementById("chatStatus").textContent = "Sending…";

    try {
        await api(`/api/rooms/${encodeURIComponent(currentRoom)}/messages`, {
            method: "POST",
            body: JSON.stringify({ sender: username, text })
        });
        input.value = "";
        await loadCurrentRoom();
    } catch (error) {
        document.getElementById("chatStatus").textContent = error.message;
    } finally {
        isSending = false;
    }
}

function goHome() {
    currentRoom = null;
    document.getElementById("chatMessages").replaceChildren();
    document.getElementById("chatStatus").textContent = "";
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
        goHome();
    } catch (error) {
        document.getElementById("chatStatus").textContent = error.message;
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
        document.getElementById("chatStatus").textContent = error.message;
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

setInterval(() => {
    if (document.hidden) return;
    if (currentRoom) {
        loadCurrentRoom();
    } else {
        loadRooms();
    }
}, 2000);

loadRooms();
