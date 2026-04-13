from flask import Flask, send_from_directory
from flask_socketio import SocketIO, emit, join_room, leave_room

app = Flask(__name__, static_folder='.', static_url_path='')
app.config['SECRET_KEY'] = 'whisper_secret_key'
socketio = SocketIO(app, cors_allowed_origins="*")

rooms = {}

@app.route('/')
def index():
    return send_from_directory('.', 'index.html')

@socketio.on('connect')
def handle_connect():
    emit('update_rooms', rooms)

@socketio.on('create_room')
def handle_create_room(data):
    room_code = data['roomCode']
    
    rooms[room_code] = {
        'help': data['helpText'],
        'messages': [data['initialMessage']]
    }
    
    emit('update_rooms', rooms, broadcast=True)

@socketio.on('join')
def on_join(data):
    join_room(data['room'])

@socketio.on('send_message')
def handle_send_message(data):
    room_code = data['roomCode']
    message = data['message']
    
    if room_code in rooms:
        rooms[room_code]['messages'].append(message)
        emit('receive_message', message, to=room_code)

if __name__ == '__main__':
    print("Starting Whisper Server on http://127.0.0.1:5000")
    socketio.run(app, debug=True, port=5000)