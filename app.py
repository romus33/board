from engineio.payload import Payload

from flask import Flask, render_template, request, redirect, url_for, session, jsonify
from flask_socketio import SocketIO, emit, join_room, leave_room
from functools import wraps
import json
import time
import random
import string


Payload.max_decode_packets = 2000  
app = Flask(__name__)
app.config['SECRET_KEY'] = 'secret_key_для_доски_рисования'  # поменяй на свой секрет в проде
socketio = SocketIO(app, cors_allowed_origins="*")

# Простейшее in-memory хранилище пользователей (для примера)
USERS = {
    "admin": "1234",
    "test": "test"
}

# Глобальные словари для хранения досок и их данных
boards = {}       # {board_id: {drawing_history: [], formula_history: [], shape_history: [], text_history: []}}
board_users = {}  # {board_id: {user_sid: {username, color, board_id}}}

def generate_board_id():
    """Генерация уникального ID для доски"""
    return ''.join(random.choices(string.ascii_letters + string.digits, k=8))

def login_required(f):
    """Декоратор: доступ только для авторизованных пользователей"""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("login", next=request.path))
        return f(*args, **kwargs)
    return decorated

@app.route('/login', methods=['GET', 'POST'])
def login():
    """Страница входа"""
    error = None
    if request.method == 'POST':
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        if username in USERS and USERS[username] == password:
            session["logged_in"] = True
            session["username"] = username
            # перенаправление на next если передано
            next_url = request.args.get("next") or url_for("index")
            return redirect(next_url)
        else:
            error = "Неверный логин или пароль"
    return render_template("login.html", error=error)

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/')
def index():
    """
    Главная страница:
    - Если передан id (/?id=XXX), то:
        * если доска существует — открываем её (анонимам разрешено).
        * если доски нет — если пользователь авторизован, создаём и открываем; иначе — показываем ошибку/подсказку.
    - Если id не передан — перенаправляем на /boards (список) — он защищён логином.
    """
    board_id = request.args.get('id')
    if board_id:
        if board_id not in boards:
            # доска не найдена — только авторизованные могут её создать автоматически
            if session.get("logged_in"):
                # создаём новую доску
                boards[board_id] = {
                    'drawing_history': [],
                    'formula_history': [],
                    'shape_history': [],
                    'text_history': [],
                    'image_history': []  # ← ДОБАВЬТЕ ЭТУ СТРОКУ
}
                board_users[board_id] = {}
                return render_template('index.html', board_id=board_id)
            else:
                # анонимный пользователь: не можем создать новую доску — покажем сообщение
                return (
                    "Доска не найдена. Чтобы создать новую доску — войдите в систему. "
                    f"<a href='{url_for('login', next=request.path)}'>Войти</a>"
                ), 404
        # доска существует — разрешаем вход даже без логина
        return render_template('index.html', board_id=board_id)
    else:
        # Без id — перенаправляем на список досок (требует авторизации)
        return redirect(url_for('list_boards'))

@app.route('/boards')
@login_required
def list_boards():
    """Страница со списком активных досок — доступна только авторизованным"""
    return render_template('boards.html')

@app.route('/create')
@login_required
def create_board():
    """Создание новой доски — только авторизованные"""
    new_board_id = generate_board_id()
    boards[new_board_id] = {
        'drawing_history': [],
        'formula_history': [],
        'shape_history': [],
        'text_history': []
    }
    board_users[new_board_id] = {}
    return redirect(url_for('index', id=new_board_id))

@app.route('/board/<board_id>')
def join_board(board_id):
    """Присоединение к существующей доске — доступно всем, если доска существует"""
    if board_id in boards:
        return render_template('index.html', board_id=board_id)
    else:
        # если не существует — только авторизованные могут создавать (через /create)
        return (
            "Доска не найдена. Если вы хотите создать доску — войдите в систему. "
            f"<a href='{url_for('login', next=url_for('create_board'))}'>Войти</a>"
        ), 404

# --- Socket.IO события ---
# Добавьте обработчик для изображений
@socketio.on('add_image')
def handle_add_image(data):
    """Обработка добавления изображения"""
    try:
        user_sid = request.sid

        # Находим доску пользователя
        board_id = None
        for bid, users in board_users.items():
            if user_sid in users:
                board_id = bid
                break

        if not board_id:
            return

        # Добавляем информацию о пользователе
        data['user_color'] = board_users[board_id][user_sid]['color']
        data['username'] = board_users[board_id][user_sid]['username']

        # Сохраняем изображение в историю доски
        board_data = boards[board_id]
        image_history = board_data['image_history']

        if len(image_history) > 50:  # Ограничение количества изображений
            image_history.pop(0)

        data['timestamp'] = time.time()
        data['id'] = data.get('id', f"image_{len(image_history)}")
        image_history.append(data)

        # Рассылаем всем пользователям в этой доске, кроме отправителя
        emit('add_image', data, room=board_id, include_self=False)

    except Exception as e:
        app.logger.exception(f'Ошибка обработки изображения: {e}')

@socketio.on('update_image')
def handle_update_image(data):
    """Обновление позиции/размера изображения"""
    image_id = data.get('id')
    if image_id:
        # Находим доску с этим изображением
        for board_id, board_data in boards.items():
            for i, image in enumerate(board_data['image_history']):
                if image.get('id') == image_id:
                    # Обновляем только позицию и размер
                    board_data['image_history'][i].update({
                        'x': data.get('x', image.get('x', 0)),
                        'y': data.get('y', image.get('y', 0)),
                        'width': data.get('width', image.get('width', 100)),
                        'height': data.get('height', image.get('height', 100)),
                        'timestamp': time.time()
                    })
                    # Рассылаем обновление всем, кроме отправителя
                    socketio.emit('update_image', board_data['image_history'][i], 
                                room=board_id, include_self=False)
                    return

@socketio.on('remove_image')
def handle_remove_image(data):
    """Удаление изображения"""
    image_id = data.get('id')
    if image_id:
        # Находим доску с этим изображением
        for board_id, board_data in boards.items():
            board_data['image_history'] = [img for img in board_data['image_history'] 
                                         if img.get('id') != image_id]
            emit('remove_image', data, room=board_id)
            return

@socketio.on('connect')
def handle_connect():
    """Обработка подключения нового пользователя через Socket.IO"""
    """Обработка подключения нового пользователя через Socket.IO"""
    user_sid = request.sid
    board_id = request.args.get('board_id') or request.args.get('id')    
    # Для socket соединения board_id обычно передается как query param: ?board_id=XXXX
    board_id = request.args.get('board_id') or request.args.get('id')
    
    if not board_id:
        emit('error', {'message': 'Не указан ID доски'})
        return

    # Если доски нет — не создаём её автоматически для анонимных подключений.
    if board_id not in boards:
        # Если сессия авторизована — создаём; иначе — возвращаем ошибку
        if session.get("logged_in"):
            boards[board_id] = {
                'drawing_history': [],
                'formula_history': [],
                'shape_history': [],
                'text_history': [],
                'image_history': []
            }
            board_users[board_id] = {}
        else:
            emit('error', {'message': 'Доска не найдена'})
            return

    # Добавляем пользователя в комнату доски
    join_room(board_id)

    # Определяем имя пользователя: из сессии или как гость
    if session.get("logged_in"):
        username = session.get("username", f"User_{random.randrange(1000,9999)}")
    else:
        # Сформируем уникальное гостевое имя на основе текущих пользователей доски
        existing = board_users.get(board_id, {})
        guest_index = sum(1 for u in existing.values() if u.get('username', '').startswith('Guest')) + 1
        username = f"Guest_{guest_index}"

    color = f"hsl({(len(board_users.get(board_id, {})) * 60) % 360}, 70%, 50%)"

    if board_id not in board_users:
        board_users[board_id] = {}

    board_users[board_id][user_sid] = {
        'username': username,
        'color': color,
        'board_id': board_id
    }

    app.logger.info(f'Пользователь подключился: {user_sid} ({username}) к доске {board_id}')

    # Отправляем историю доски новому пользователю
    board_data = boards[board_id]
    
    # Отправляем только последние N рисунков для быстрой загрузки
    recent_drawings = board_data['drawing_history'][-100:]  # Последние 100
    emit('drawing_history', recent_drawings)
    
    # Отправляем полный список формул, фигур и текста
    emit('formula_history', board_data['formula_history'])
    emit('shape_history', board_data['shape_history'])
    emit('text_history', board_data['text_history'])
    emit('image_history', board_data['image_history'])
    
    # Отправляем метку времени для синхронизации
    emit('sync_info', {
        'server_time': time.time(),
        'total_drawings': len(board_data['drawing_history']),
        'sent_drawings': len(recent_drawings)
    })


    # Рассылаем обновлённый список пользователей (используем socketio.emit, чтобы гарантированно отработало вне контекста)
    update_users_list(board_id)

@socketio.on('request_shape_history')
def handle_request_shape_history():
    user_sid = request.sid
    board_id = None
    # Находим доску пользователя
    for bid, users in board_users.items():
        if user_sid in users:
            board_id = bid
            break
    if board_id and board_id in boards:
        emit('shape_history', boards[board_id]['shape_history'])

@socketio.on('disconnect')
def handle_disconnect():
    """Обработка отключения пользователя"""
    user_sid = request.sid
    # Находим, к какой доске принадлежал пользователь
    for board_id, users in list(board_users.items()):
        if user_sid in users:
            username = users[user_sid]['username']
            del users[user_sid]
            app.logger.info(f'Пользователь отключился: {user_sid} ({username}) от доски {board_id}')

            # Не удаляем доску автоматически — сохраняем историю
            # Если нужно удалять пустые доски, можно раскомментировать:
            # if not users and board_id in boards:
            #     del boards[board_id]
            #     del board_users[board_id]

            update_users_list(board_id)
            break

# @socketio.on('drawing')
# def handle_drawing(data):
#     """Обработка события рисования"""
#     try:
#         user_sid = request.sid

#         # Находим доску пользователя
#         board_id = None
#         for bid, users in board_users.items():
#             if user_sid in users:
#                 board_id = bid
#                 break

#         if not board_id:
#             return

#         # Добавляем информацию о пользователе
#         data['username'] = board_users[board_id][user_sid]['username']

#         # Сохраняем рисунок в историю доски
#         board_data = boards[board_id]
#         drawing_history = board_data['drawing_history']

#         if len(drawing_history) > 2000:
#             drawing_history.pop(0)

#         data['timestamp'] = time.time()
#         drawing_history.append(data)

#         # Рассылаем всем пользователям в этой доске, кроме отправителя
#         emit('drawing', data, room=board_id, include_self=False)

#     except Exception as e:
#         app.logger.exception(f'Ошибка обработки рисунка: {e}')

@socketio.on('drawing')
def handle_drawing(data):
    """Обработка события рисования с подтверждением получения"""
    try:
        user_sid = request.sid
        client_timestamp = data.get('client_timestamp', time.time())

        # Находим доску пользователя
        board_id = None
        for bid, users in board_users.items():
            if user_sid in users:
                board_id = bid
                break

        if not board_id:
            return {'status': 'error', 'message': 'Board not found'}, 400

        # Добавляем серверную метку времени
        data['server_timestamp'] = time.time()
        data['client_timestamp'] = client_timestamp
        data['username'] = board_users[board_id][user_sid]['username']
        data['user_sid'] = user_sid  # Для отслеживания источника

        # ID для отслеживания пакета
        drawing_id = data.get('id', f"draw_{int(time.time() * 1000)}_{random.randint(1000, 9999)}")
        data['id'] = drawing_id

        # Сохраняем рисунок в историю доски
        board_data = boards[board_id]
        drawing_history = board_data['drawing_history']

        # Ограничиваем историю
        if len(drawing_history) > 2000:
            drawing_history.pop(0)

        drawing_history.append(data)

        # Отправляем с подтверждением получения
        emit('drawing', data, room=board_id, include_self=False, 
             callback=lambda: app.logger.debug(f"Drawing {drawing_id} confirmed"))
        
        # Возвращаем подтверждение отправителю
        return {'status': 'ok', 'id': drawing_id, 'server_timestamp': data['server_timestamp']}

    except Exception as e:
        app.logger.exception(f'Ошибка обработки рисунка: {e}')
        return {'status': 'error', 'message': str(e)}, 500

@socketio.on('batch_drawing')
def handle_batch_drawing(data):
    """Обработка пакетного рисования (для медленных соединений)"""
    try:
        user_sid = request.sid
        drawings = data.get('drawings', [])
        board_id = None

        # Находим доску пользователя
        for bid, users in board_users.items():
            if user_sid in users:
                board_id = bid
                break

        if not board_id:
            return {'status': 'error', 'message': 'Board not found'}, 400

        username = board_users[board_id][user_sid]['username']
        confirmed_ids = []
        server_timestamp = time.time()

        for drawing_data in drawings:
            drawing_data['server_timestamp'] = server_timestamp
            drawing_data['username'] = username
            drawing_data['user_sid'] = user_sid
            
            # Генерируем ID если нет
            if 'id' not in drawing_data:
                drawing_data['id'] = f"draw_{int(server_timestamp * 1000)}_{random.randint(1000, 9999)}"
            
            confirmed_ids.append(drawing_data['id'])
            
            # Сохраняем в историю
            board_data = boards[board_id]
            if len(board_data['drawing_history']) > 2000:
                board_data['drawing_history'].pop(0)
            board_data['drawing_history'].append(drawing_data)

        # Отправляем все рисунки одним пакетом
        emit('batch_drawing', {
            'drawings': drawings,
            'batch_id': data.get('batch_id', f"batch_{int(server_timestamp * 1000)}"),
            'user_sid': user_sid
        }, room=board_id, include_self=False)

        return {'status': 'ok', 'ids': confirmed_ids, 'count': len(drawings)}

    except Exception as e:
        app.logger.exception(f'Ошибка обработки пакетного рисования: {e}')
        return {'status': 'error', 'message': str(e)}, 500
@socketio.on('request_missing_drawings')
def handle_request_missing(data):
    """Запрос пропущенных рисунков (для восстановления после обрыва)"""
    try:
        user_sid = request.sid
        board_id = None
        
        # Находим доску пользователя
        for bid, users in board_users.items():
            if user_sid in users:
                board_id = bid
                break

        if not board_id or board_id not in boards:
            return {'status': 'error', 'message': 'Board not found'}, 404

        last_timestamp = data.get('last_timestamp', 0)
        max_count = data.get('max_count', 100)
        
        # Находим рисунки, созданные после указанного времени
        recent_drawings = []
        for drawing in boards[board_id]['drawing_history']:
            if drawing.get('server_timestamp', 0) > last_timestamp:
                # Исключаем рисунки самого пользователя
                if drawing.get('user_sid') != user_sid:
                    recent_drawings.append(drawing)
                if len(recent_drawings) >= max_count:
                    break

        return {
            'status': 'ok',
            'drawings': recent_drawings,
            'count': len(recent_drawings),
            'latest_timestamp': time.time()
        }

    except Exception as e:
        app.logger.exception(f'Ошибка запроса пропущенных рисунков: {e}')
        return {'status': 'error', 'message': str(e)}, 500    

@socketio.on('ping_drawing')
def handle_ping(data):
    """Пинг для поддержания соединения и проверки лага"""
    client_time = data.get('client_time', time.time())
    return {
        'status': 'pong',
        'client_time': client_time,
        'server_time': time.time(),
        'latency': time.time() - client_time
    }



@socketio.on('shape_drawn')
def handle_shape_drawn(data):
    """Обработка рисования фигуры"""
    try:
        user_sid = request.sid

        # Находим доску пользователя
        board_id = None
        for bid, users in board_users.items():
            if user_sid in users:
                board_id = bid
                break

        if not board_id:
            return

        # Добавляем информацию о пользователе
        data['user_sid'] = user_sid
        data['timestamp'] = time.time()
        
        # Убедимся, что есть ID
        if 'id' not in data:
            data['id'] = f"shape_{int(time.time() * 1000)}_{random.randint(1000, 9999)}"
        
        # Убедимся, что есть необходимые поля
        if 'brushSize' not in data:
            data['brushSize'] = 5
        if 'color' not in data:
            data['color'] = '#000000'

        # Сохраняем фигуру в историю доски
        board_data = boards[board_id]
        shape_history = board_data['shape_history']

        if len(shape_history) > 500:
            shape_history.pop(0)

        shape_history.append(data)

        # Рассылаем всем пользователям в этой доске, кроме отправителя
        emit('shape_drawn', data, room=board_id, include_self=False)

    except Exception as e:
        app.logger.exception(f'Ошибка обработки фигуры: {e}')

@socketio.on('add_formula')
def handle_add_formula(data):
    """Обработка добавления формулы"""
    try:
        user_sid = request.sid

        # Находим доску пользователя
        board_id = None
        for bid, users in board_users.items():
            if user_sid in users:
                board_id = bid
                break

        if not board_id:
            return

        # Добавляем информацию о пользователе
        data['user_color'] = board_users[board_id][user_sid]['color']
        data['username'] = board_users[board_id][user_sid]['username']

        # Сохраняем формулу в историю доски
        board_data = boards[board_id]
        formula_history = board_data['formula_history']

        if len(formula_history) > 100:
            formula_history.pop(0)

        data['timestamp'] = time.time()
        data['id'] = f"formula_{len(formula_history)}"
        formula_history.append(data)

        # Рассылаем всем пользователям в этой доске
        emit('add_formula', data, room=board_id)

    except Exception as e:
        app.logger.exception(f'Ошибка обработки формулы: {e}')

@socketio.on('add_text')
def handle_add_text(data):
    """Обработка добавления текста"""
    try:
        user_sid = request.sid

        # Находим доску пользователя
        board_id = None
        for bid, users in board_users.items():
            if user_sid in users:
                board_id = bid
                break

        if not board_id:
            return

        # Добавляем информацию о пользователе
        data['user_color'] = board_users[board_id][user_sid]['color']
        data['username'] = board_users[board_id][user_sid]['username']

        # Сохраняем текст в историю доски
        board_data = boards[board_id]
        text_history = board_data['text_history']

        if len(text_history) > 100:
            text_history.pop(0)

        data['timestamp'] = time.time()
        data['id'] = f"text_{len(text_history)}"
        text_history.append(data)

        # Рассылаем всем пользователям в этой доске
        emit('add_text', data, room=board_id)

    except Exception as e:
        app.logger.exception(f'Ошибка обработки текста: {e}')

@socketio.on('clear_canvas')
def handle_clear_canvas():
    """Обработка очистки холста"""
    user_sid = request.sid

    # Находим доску пользователя
    board_id = None
    for bid, users in board_users.items():
        if user_sid in users:
            board_id = bid
            break

    if not board_id:
        return

    # Очищаем историю текущей доски
    boards[board_id] = {
        'drawing_history': [],
        'formula_history': [],
        'shape_history': [],
        'text_history': [],
        'image_history': []
    }

    emit('clear_canvas', room=board_id)

@socketio.on('undo_last')
def handle_undo(data):
    """Отмена последнего действия"""
    user_sid = request.sid

    # Находим доску пользователя
    board_id = None
    for bid, users in board_users.items():
        if user_sid in users:
            board_id = bid
            break

    if not board_id:
        return

    board_data = boards[board_id]
    action_type = data.get('type', 'drawing')

    if action_type == 'drawing' and board_data['drawing_history']:
        board_data['drawing_history'].pop()
        emit('undo_last', {'type': 'drawing'}, room=board_id)
    elif action_type == 'formula' and board_data['formula_history']:
        board_data['formula_history'].pop()
        emit('undo_last', {'type': 'formula'}, room=board_id)
    elif action_type == 'shape' and board_data['shape_history']:
        board_data['shape_history'].pop()
        emit('undo_last', {'type': 'shape'}, room=board_id)
    elif action_type == 'text' and board_data['text_history']:
        board_data['text_history'].pop()
        emit('undo_last', {'type': 'text'}, room=board_id)

@socketio.on('update_formula')
def handle_update_formula(data):
    """Обновление положения/содержания формулы"""
    try:
        formula_id = data.get('id')
        if not formula_id:
            return

        # Находим доску текущего пользователя
        user_sid = request.sid
        board_id = None
        
        for bid, users in board_users.items():
            if user_sid in users:
                board_id = bid
                break

        if not board_id or board_id not in boards:
            return

        board_data = boards[board_id]
        
        # Ищем формулу в истории текущей доски
        for i, formula in enumerate(board_data['formula_history']):
            if formula.get('id') == formula_id:
                # СОХРАНЯЕМ старые поля формулы и добавляем только новые
                updated_formula = {**formula, **data, 'timestamp': time.time()}
                board_data['formula_history'][i] = updated_formula
                
                # Логируем обновление
                #log_info(f'Формула {formula_id} обновлена пользователем {user_sid}')
                
                # Рассылаем ВСЕМ в комнате, включая отправителя
                socketio.emit('update_formula', updated_formula, room=board_id)
                return
        
        # Если формула не найдена, создаем новую
        #log_warning(f'Формула {formula_id} не найдена, создаем новую')
        data['timestamp'] = time.time()
        board_data['formula_history'].append(data)
        socketio.emit('update_formula', data, room=board_id)
        
    except Exception as e:
        pass
        #log_error(f'Ошибка обновления формулы: {e}')

@socketio.on('update_text')
def handle_update_text(data):
    """Обновление положения/содержания текста"""
    try:
        text_id = data.get('id')
        if not text_id:
            return

        # Находим доску текущего пользователя
        user_sid = request.sid
        board_id = None
        
        for bid, users in board_users.items():
            if user_sid in users:
                board_id = bid
                break

        if not board_id or board_id not in boards:
            return

        board_data = boards[board_id]
        
        # Ищем текст в истории текущей доски
        for i, text in enumerate(board_data['text_history']):
            if text.get('id') == text_id:
                # Сохраняем старые поля и добавляем новые
                updated_text = {**text, **data, 'timestamp': time.time()}
                board_data['text_history'][i] = updated_text
                
                #log_info(f'Текст {text_id} обновлен пользователем {user_sid}')
                
                # Рассылаем ВСЕМ в комнате
                socketio.emit('update_text', updated_text, room=board_id)
                return
        
        # Если текст не найден, создаем новый
        #log_warning(f'Текст {text_id} не найден, создаем новый')
        data['timestamp'] = time.time()
        board_data['text_history'].append(data)
        socketio.emit('update_text', data, room=board_id)
        
    except Exception as e:
        pass
        #log_error(f'Ошибка обновления текста: {e}')

@socketio.on('remove_formula')
def handle_remove_formula(data):
    """Удаление формулы"""
    formula_id = data.get('id')
    if formula_id:
        # Находим доску с этой формулой
        for board_id, board_data in boards.items():
            board_data['formula_history'] = [f for f in board_data['formula_history'] if f.get('id') != formula_id]
            emit('remove_formula', data, room=board_id)
            return

@socketio.on('remove_text')
def handle_remove_text(data):
    """Удаление текста"""
    text_id = data.get('id')
    if text_id:
        # Находим доску с этим текстом
        for board_id, board_data in boards.items():
            board_data['text_history'] = [t for t in board_data['text_history'] if t.get('id') != text_id]
            emit('remove_text', data, room=board_id)
            return
 
@socketio.on('ping_drawing')
def handle_ping_drawing(data):
    """Пинг для поддержания соединения и проверки лага"""
    client_time = data.get('client_time', time.time())
    return {
        'status': 'pong',
        'client_time': client_time,
        'server_time': time.time(),
        'latency': time.time() - client_time
    } 
@socketio.on('batch_drawing')
def handle_batch_drawing(data):
    """Обработка пакетного рисования (для медленных соединений)"""
    try:
        user_sid = request.sid
        drawings = data.get('drawings', [])
        board_id = None

        # Находим доску пользователя
        for bid, users in board_users.items():
            if user_sid in users:
                board_id = bid
                break

        if not board_id:
            return {'status': 'error', 'message': 'Board not found'}, 400

        username = board_users[board_id][user_sid]['username']
        confirmed_ids = []
        server_timestamp = time.time()

        for drawing_data in drawings:
            drawing_data['server_timestamp'] = server_timestamp
            drawing_data['username'] = username
            drawing_data['user_sid'] = user_sid
            
            # Генерируем ID если нет
            if 'id' not in drawing_data:
                drawing_data['id'] = f"draw_{int(server_timestamp * 1000)}_{random.randint(1000, 9999)}"
            
            confirmed_ids.append(drawing_data['id'])
            
            # Сохраняем в историю
            board_data = boards[board_id]
            if len(board_data['drawing_history']) > 2000:
                board_data['drawing_history'].pop(0)
            board_data['drawing_history'].append(drawing_data)

        # Отправляем все рисунки одним пакетом
        emit('batch_drawing', {
            'drawings': drawings,
            'batch_id': data.get('batch_id', f"batch_{int(server_timestamp * 1000)}"),
            'user_sid': user_sid
        }, room=board_id, include_self=False)

        return {'status': 'ok', 'ids': confirmed_ids, 'count': len(drawings)}

    except Exception as e:
        app.logger.exception(f'Ошибка обработки пакетного рисования: {e}')
        return {'status': 'error', 'message': str(e)}, 500

@socketio.on('request_missing_drawings')
def handle_request_missing_drawings(data):
    """Запрос пропущенных рисунков (для восстановления после обрыва)"""
    try:
        user_sid = request.sid
        board_id = None
        
        # Находим доску пользователя
        for bid, users in board_users.items():
            if user_sid in users:
                board_id = bid
                break

        if not board_id or board_id not in boards:
            return {'status': 'error', 'message': 'Board not found'}, 404

        last_timestamp = data.get('last_timestamp', 0)
        max_count = data.get('max_count', 100)
        
        # Находим рисунки, созданные после указанного времени
        recent_drawings = []
        for drawing in boards[board_id]['drawing_history']:
            if drawing.get('server_timestamp', 0) > last_timestamp:
                # Исключаем рисунки самого пользователя
                if drawing.get('user_sid') != user_sid:
                    recent_drawings.append(drawing)
                if len(recent_drawings) >= max_count:
                    break

        return {
            'status': 'ok',
            'drawings': recent_drawings,
            'count': len(recent_drawings),
            'latest_timestamp': time.time()
        }

    except Exception as e:
        app.logger.exception(f'Ошибка запроса пропущенных рисунков: {e}')
        return {'status': 'error', 'message': str(e)}, 500
     
@socketio.on('get_shape_info')
def handle_get_shape_info(data):
    """Получение информации о фигуре"""
    shape_id = data.get('id')
    user_sid = request.sid
    
    # Находим доску пользователя
    board_id = None
    for bid, users in board_users.items():
        if user_sid in users:
            board_id = bid
            break
    
    if not board_id or board_id not in boards:
        return
    
    # Ищем фигуру
    for shape in boards[board_id]['shape_history']:
        if shape.get('id') == shape_id:
            emit('shape_info', shape)
            return
                
@socketio.on('remove_shape')
def handle_remove_shape(data):
    """Удаление фигуры"""
    shape_id = data.get('id')
    if shape_id:
        # Находим доску с этой фигурой
        for board_id, board_data in boards.items():
            # Фильтруем фигуры, удаляя указанную
            board_data['shape_history'] = [
                shape for shape in board_data['shape_history'] 
                if shape.get('id') != shape_id
            ]
            emit('remove_shape', data, room=board_id)
            return
                
@socketio.on('update_shape')
def handle_update_shape(data):
    """Обработка обновления/перемещения фигуры"""
    try:
        shape_id = data.get('id')
        if not shape_id:
            return

        user_sid = request.sid
        
        # Сначала ищем доску пользователя
        board_id = None
        for bid, users in board_users.items():
            if user_sid in users:
                board_id = bid
                break
        
        if not board_id or board_id not in boards:
            app.logger.warning(f'Доска не найдена для пользователя {user_sid}')
            return

        board_data = boards[board_id]
        
        # Ищем фигуру в истории текущей доски
        for i, shape in enumerate(board_data['shape_history']):
            if shape.get('id') == shape_id:
                # Обновляем данные фигуры
                updated_shape = shape.copy()
                
                # Обновляем только переданные координаты
                if 'x1' in data: updated_shape['x1'] = data['x1']
                if 'y1' in data: updated_shape['y1'] = data['y1']
                if 'x2' in data: updated_shape['x2'] = data['x2']
                if 'y2' in data: updated_shape['y2'] = data['y2']
                if 'shape' in data: updated_shape['shape'] = data['shape']
                if 'color' in data: updated_shape['color'] = data['color']
                if 'brushSize' in data: updated_shape['brushSize'] = data['brushSize']
                
                updated_shape['timestamp'] = time.time()
                updated_shape['user_sid'] = user_sid  # Добавляем информацию о пользователе
                
                # Заменяем в истории
                board_data['shape_history'][i] = updated_shape
                
                app.logger.info(f'Фигура {shape_id} обновлена пользователем {user_sid}')
                
                # Рассылаем обновление всем, кроме отправителя
                emit('update_shape', updated_shape, room=board_id, include_self=False)
                return
        
        # Если фигура не найдена, можно создать её
        app.logger.warning(f'Фигура {shape_id} не найдена при обновлении')
        
        # Добавим как новую фигуру
        data['timestamp'] = time.time()
        data['user_sid'] = user_sid
        
        # Добавляем недостающие поля
        if 'brushSize' not in data:
            data['brushSize'] = 5
        if 'color' not in data:
            data['color'] = '#000000'
            
        board_data['shape_history'].append(data)
        
        # Рассылаем всем
        emit('update_shape', data, room=board_id)
                
    except Exception as e:
        app.logger.exception(f'Ошибка обновления фигуры: {e}')
        
def update_users_list(board_id):
    """
    Обновление списка пользователей конкретной доски.
    Исправлено: используем socketio.emit чтобы точно отправлять сообщение даже из не-событийной функции.
    """
    if not board_id or board_id not in board_users:
        return

    users_list = [{
        'username': user['username'],
        'color': user['color']
    } for user in board_users[board_id].values()]

    payload = {
        'users': users_list,
        'count': len(users_list),
        'board_id': board_id
    }

    # Используем socketio.emit с указанием room
    socketio.emit('users_update', payload, room=board_id)

# --- HTTP API ---

@app.route('/health')
def health_check():
    """Проверка работоспособности сервера"""
    total_users = sum(len(users) for users in board_users.values())
    return jsonify({
        'status': 'ok',
        'total_users': total_users,
        'total_boards': len(boards)
    })

@app.route('/api/boards')
@login_required
def get_boards():
    """Получение списка активных досок — только авторизованным"""
    active_boards = []
    for board_id, users in board_users.items():
        if users:  # Только доски с активными пользователями
            active_boards.append({
                'id': board_id,
                'users': len(users),
                'drawings': len(boards.get(board_id, {}).get('drawing_history', [])),
                'formulas': len(boards.get(board_id, {}).get('formula_history', []))
            })
    return jsonify({'boards': active_boards, 'count': len(active_boards)})

@app.route('/api/board/<board_id>')
def get_board_info(board_id):
    """Получение информации о конкретной доске — доступно всем"""
    if board_id in boards:
        users = board_users.get(board_id, {})
        return jsonify({
            'id': board_id,
            'users': len(users),
            'user_list': [{'username': u['username'], 'color': u['color']} for u in users.values()],
            'drawings': len(boards[board_id]['drawing_history']),
            'formulas': len(boards[board_id]['formula_history']),
            'shapes': len(boards[board_id]['shape_history']),
            'texts': len(boards[board_id]['text_history'])
        })
    return jsonify({'error': 'Доска не найдена'}), 404

def optimize_drawing_history(board_id, max_points_per_drawing=50):
    """Оптимизация истории рисунков для уменьшения трафика"""
    if board_id not in boards:
        return
    
    history = boards[board_id]['drawing_history']
    if len(history) < 1000:
        return
    
    optimized = []
    current_chunk = []
    current_user = None
    
    for drawing in history:
        if drawing.get('user_sid') != current_user or len(current_chunk) >= max_points_per_drawing:
            if current_chunk:
                optimized.append({
                    'type': 'batch',
                    'points': current_chunk,
                    'user_sid': current_user,
                    'timestamp': current_chunk[0].get('timestamp', time.time())
                })
            current_chunk = []
            current_user = drawing.get('user_sid')
        
        current_chunk.append({
            'x': drawing.get('x', 0),
            'y': drawing.get('y', 0),
            'pressure': drawing.get('pressure', 0.5)
        })
    
    if current_chunk:
        optimized.append({
            'type': 'batch',
            'points': current_chunk,
            'user_sid': current_user,
            'timestamp': time.time()
        })
    
    boards[board_id]['drawing_history'] = optimized[-1000:]  # Сохраняем оптимизированную версию

if __name__ == '__main__':
    print("=" * 60)
    print("Сервер онлайн-доски с уникальными комнатами запущен!")
    print("=" * 60)
    print("Доступные адреса:")
    print(f"  • Вход: http://localhost:5000/login")
    print(f"  • Создать новую доску (только для авторизованных): http://localhost:5000/create")
    print(f"  • Присоединиться к доске: http://localhost:5000/?id=ID_ДОСКИ")
    print(f"  • Список активных досок (только для авторизованных): http://localhost:5000/api/boards")
    print("=" * 60)
    socketio.run(app, host='0.0.0.0', port=5000, debug=False, allow_unsafe_werkzeug=True)
