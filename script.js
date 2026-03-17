let rooms = JSON.parse(localStorage.getItem("shadowRooms")) || {};
let currentRoom = null;
let currentUser = "User_" + Math.floor(Math.random() * 10000);

function saveRooms() {
    localStorage.setItem("shadowRooms", JSON.stringify(rooms));
}

function generateRoomCode(){
    return Math.random().toString(36).substring(2,7).toUpperCase();
}

function createRoom(){

    let text = document.getElementById("helpInput").value.trim();
    if(text === "") return;

    let roomCode = generateRoomCode();

    rooms[roomCode] = {
        help: text,
        messages: []
    };

    showRooms();

    document.getElementById("helpInput").value="";
}

function showRooms(){

    let list = document.getElementById("roomsList");
    list.innerHTML = "";

    for(let code in rooms){

        let div = document.createElement("div");

        div.className = "room";

        div.innerHTML = `
            <b>Room: ${code}</b><br>
            ${rooms[code].help}
        `;

        div.onclick = ()=>joinRoom(code);

        list.appendChild(div);
    }
}

function openRoom(roomId) {

    currentRoom = roomId;

    document.getElementById("home").classList.add("hidden");
    document.getElementById("chatRoom").classList.remove("hidden");

    document.getElementById("roomTitle").innerText = "Private Room";

    showMessages();
}

function showMessages() {

    let box = document.getElementById("chatMessages");

    box.innerHTML = "";

    rooms[currentRoom].messages.forEach(msg => {

        let div = document.createElement("div");

        div.className = "message";

        div.innerHTML = "<b>" + msg.sender + ":</b> " + msg.text;

        box.appendChild(div);

    });

}

function sendMessage() {

    let input = document.getElementById("chatInput");

    if (!input.value) return;

    rooms[currentRoom].messages.push({

        sender: currentUser,
        text: input.value

    });

    input.value = "";

    saveRooms();

    showMessages();
}

function goHome() {

    document.getElementById("chatRoom").classList.add("hidden");
    document.getElementById("home").classList.remove("hidden");

}

showRooms();

function joinRoom(code){

    currentRoom = code;

    document.getElementById("home").classList.add("hidden");
    document.getElementById("chatRoom").classList.remove("hidden");

    document.getElementById("roomTitle").innerText = "Room: " + code;

    showMessages();
}

function closeRoom(){

    if(!currentRoom) return;

    delete rooms[currentRoom];

    goHome();

    showRooms();
}