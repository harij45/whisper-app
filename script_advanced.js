let rooms = JSON.parse(localStorage.getItem("shadowRooms")) || {};
let currentRoom = null;

// Generate anonymous user ID
let currentUser = "User_" + Math.floor(Math.random() * 10000);

let typingTimeout = null;

// Auto delete time (5 minutes for demo)
let AUTO_DELETE_TIME = 5 * 60 * 1000;

function saveRooms() {
    localStorage.setItem("shadowRooms", JSON.stringify(rooms));
}

function createRoom() {

    let text = document.getElementById("helpInput").value;

    if (!text) return;

    let roomId = "room_" + Date.now();

    rooms[roomId] = {
        title: text,
        createdAt: Date.now(),
        messages: [
            {
                sender: currentUser,
                text: text,
                time: Date.now()
            }
        ]
    };

    saveRooms();
    document.getElementById("helpInput").value = "";
    showRooms();
}

function showRooms() {

    deleteExpiredRooms();

    let list = document.getElementById("roomsList");
    list.innerHTML = "";

    for (let id in rooms) {

        let div = document.createElement("div");

        div.className = "room";

        div.innerHTML = `
            ${rooms[id].title}
            <br>
            <small>Private Anonymous Room</small>
        `;

        div.onclick = () => openRoom(id);

        list.appendChild(div);
    }
}

function openRoom(roomId) {

    currentRoom = roomId;

    document.getElementById("home").classList.add("hidden");
    document.getElementById("chatRoom").classList.remove("hidden");

    document.getElementById("roomTitle").innerText =
        "Private Room • Anonymous";

    showMessages();
}

function showMessages() {

    let box = document.getElementById("chatMessages");
    box.innerHTML = "";

    rooms[currentRoom].messages.forEach(msg => {

        let div = document.createElement("div");

        div.className = "message";

        div.innerHTML =
            `<b>${msg.sender}:</b> ${msg.text}`;

        box.appendChild(div);
    });

    box.scrollTop = box.scrollHeight;
}

function sendMessage() {

    let input = document.getElementById("chatInput");

    if (!input.value) return;

    let messageText = input.value;

    input.value = "";

    rooms[currentRoom].messages.push({
        sender: currentUser,
        text: messageText,
        time: Date.now()
    });

    saveRooms();

    showMessages();

    simulateTypingResponse();
}


// Typing indicator simulation
function simulateTypingResponse() {

    let box = document.getElementById("chatMessages");

    let typingDiv = document.createElement("div");

    let helperId = "Helper_" + Math.floor(Math.random() * 10000);

    typingDiv.innerHTML =
        `<i>${helperId} is typing...</i>`;

    box.appendChild(typingDiv);

    box.scrollTop = box.scrollHeight;

    clearTimeout(typingTimeout);

    typingTimeout = setTimeout(() => {

        typingDiv.remove();

        let replies = [
            "You are not alone.",
            "Stay strong. Things improve.",
            "Take small steps.",
            "I understand how you feel.",
            "Focus on one step at a time."
        ];

        let reply =
            replies[Math.floor(Math.random() * replies.length)];

        rooms[currentRoom].messages.push({
            sender: helperId,
            text: reply,
            time: Date.now()
        });

        saveRooms();

        showMessages();

    }, 2000);
}


// Auto delete expired rooms
function deleteExpiredRooms() {

    let now = Date.now();

    for (let id in rooms) {

        if (now - rooms[id].createdAt > AUTO_DELETE_TIME) {

            delete rooms[id];
        }
    }

    saveRooms();
}


function goHome() {

    document.getElementById("chatRoom").classList.add("hidden");
    document.getElementById("home").classList.remove("hidden");

    showRooms();
}


// typing indicator when user types
document.addEventListener("DOMContentLoaded", () => {

    let input = document.getElementById("chatInput");

    input.addEventListener("input", () => {

        console.log("User typing...");
    });

});


showRooms();