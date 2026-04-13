


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

function scrollToBottom() {
    let box = document.getElementById("chatMessages");
    box.scrollTop = box.scrollHeight;
}

const socket = io(); 
let rooms = {}; 
let currentRoom = null;
function generateRoomCode(){
    return Math.random().toString(36).substring(2,7).toUpperCase();
}

function createRoom(){
    let text = document.getElementById("helpInput").value.trim();
    if(text === "") return;

    let roomCode = generateRoomCode();
    let initialMessage = { sender: username, text: text, time: getTime() };

    socket.emit('create_room', {
        roomCode: roomCode,
        helpText: text,
        initialMessage: initialMessage
    });

    document.getElementById("helpInput").value="";
    joinRoom(roomCode); 
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

    let messageData = { sender: username, text: message, time: getTime() };

    // Send the message to the server
    socket.emit('send_message', {
        roomCode: currentRoom,
        message: messageData
    });

    input.value = "";
}

function goHome() {

    document.getElementById("chatRoom").classList.add("hidden");
    document.getElementById("home").classList.remove("hidden");

}

showRooms();

function joinRoom(code){
    currentRoom = code;
    
    
    socket.emit('join', { room: code });

    document.getElementById("home").classList.add("hidden");
    document.getElementById("chatRoom").classList.remove("hidden");
    document.getElementById("roomTitle").innerText = "Room: " + code;

    showMessages();
    setTimeout(scrollToBottom, 50);
}

function closeRoom(){
    if(!currentRoom) return;

    socket.emit('close_room', { roomCode: currentRoom });

    goHome();
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

socket.on('update_rooms', function(serverRooms) {
    rooms = serverRooms;
    showRooms();
});


socket.on('receive_message', function(msg) {
    rooms[currentRoom].messages.push(msg);
    showMessages();
    scrollToBottom(); 
});