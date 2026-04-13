


const names = ["Shadow","Ghost","Silent","Dark","Hidden"];
const animals = ["Fox","Wolf","Tiger","Raven","Viper"];

let username = localStorage.getItem("username");

if(!username){
    username = generateName();
    localStorage.setItem("username", username);
}

function generateName(){
    const name = names[Math.floor(Math.random() * names.length)];
    const animal = animals[Math.floor(Math.random() * animals.length)];
    return name + animal;
}

function getTime(){
    let now = new Date();

    let hours = now.getHours();
    let minutes = now.getMinutes();

    let ampm = hours >= 12 ? "PM" : "AM";
    hours = hours % 12;
    hours = hours ? hours : 12;

    minutes = minutes < 10 ? "0" + minutes : minutes;

    return hours + ":" + minutes + " " + ampm;
}

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

    let initialMessage = {
        sender: username,
        text: text,
        time: getTime()
    };

    rooms[roomCode] = {
        help: text,
        messages: [initialMessage]
    };
    
    saveRooms(); 
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

        // <-- UPDATE THIS to match your sendMessage layout
        div.innerHTML = `
        <div class="msgRow">
            <span class="msgText"><b>${msg.sender}</b>: ${msg.text}</span>
            <span class="time">${msg.time || ""}</span>
        </div>
        `;

        box.appendChild(div);
    });
}

function sendMessage(){
    let input = document.getElementById("chatInput");
    let message = input.value;

    if(message.trim() === "") return;

    let messageData = {
        sender: username,
        text: message,
        time: getTime() 
    };
    rooms[currentRoom].messages.push(messageData);
    saveRooms();

    let msgDiv = document.createElement("div");
    msgDiv.classList.add("message");

    msgDiv.innerHTML = `
    <div class="msgRow">
        <span class="msgText"><b>${username}</b>: ${message}</span>
        <span class="time">${getTime()}</span>
    </div>
`;

    document.getElementById("chatMessages").appendChild(msgDiv);

    input.value = "";
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

    saveRooms();

    goHome();

    showRooms();
}

function reportRoom(){
    if(confirm("Report this room?")){
        alert("Room reported successfully.");
        
    }
}

document.addEventListener("keydown", function(event){

    if(event.key === "Enter"){
        let chatInput = document.getElementById("chatInput");
        let helpInput = document.getElementById("helpInput");

        
        if(document.activeElement === chatInput){
            event.preventDefault();
            sendMessage();
        }
        
        else if(document.activeElement === helpInput){
            event.preventDefault(); 
            createRoom();
        }
    }

    
    if(event.key === "Escape"){
        goHome(); 
    }
});