import os
import csv
import io
import json
from datetime import datetime
from flask import Blueprint, render_template, request, jsonify, send_file, current_app
from flask_login import login_required, current_user
from app import get_db, close_db
from mysql.connector import Error
from functools import wraps

# Сначала определяем blueprint
main = Blueprint('main', __name__)

def with_db_connection(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        conn = get_db()
        if conn is None:
            return jsonify({'error': 'Database connection failed'}), 500
            
        try:
            result = f(conn, *args, **kwargs)
            return result
        except Error as e:
            print(f"Database error in {f.__name__}: {e}")
            return jsonify({'error': str(e)}), 500
        finally:
            close_db(conn)
    return decorated_function

# Основные маршруты страниц
@main.route('/')
def index():
    return render_template('index.html')

@main.route('/dashboard')
@login_required
def dashboard():
    return render_template('dashboard.html')

@main.route('/calendar')
@login_required
def calendar():
    return render_template('calendar.html')

@main.route('/calendar/day/<date>')
@login_required
def day_detail(date):
    return render_template('day_detail.html')  

@main.route('/profile')
@login_required
def profile():
    return render_template('profile.html')

@main.route('/chart')
@login_required
def chart():
    return render_template('chart.html')

# API маршруты для настроения
@main.route('/api/mood_entries', methods=['GET', 'POST'])
@login_required
@with_db_connection
def mood_entries(conn):
    if request.method == 'GET':
        try:
            # ДОБАВЛЕНА ВОЗМОЖНОСТЬ ФИЛЬТРАЦИИ ПО ДАТЕ
            date_filter = request.args.get('date')
            
            if date_filter:
                cursor = conn.cursor(dictionary=True)
                cursor.execute(
                    "SELECT id, user_id, date, mood, note, created_at FROM mood_entries WHERE user_id = %s AND date = %s ORDER BY date DESC",
                    (current_user.id, date_filter)
                )
            else:
                cursor = conn.cursor(dictionary=True)
                cursor.execute(
                    "SELECT id, user_id, date, mood, note, created_at FROM mood_entries WHERE user_id = %s ORDER BY date DESC",
                    (current_user.id,)
                )
            
            entries = cursor.fetchall()
            
            # Преобразуем Decimal в float для JSON
            for entry in entries:
                if 'mood' in entry and entry['mood'] is not None:
                    entry['mood'] = float(entry['mood'])
                if 'date' in entry and entry['date']:
                    entry['date'] = entry['date'].isoformat()
                if 'created_at' in entry and entry['created_at']:
                    entry['created_at'] = entry['created_at'].isoformat()
            
            cursor.close()
            return jsonify(entries)
        except Error as e:
            print(f"Database error in mood_entries GET: {e}")
            return jsonify({'error': str(e)}), 500
            
    elif request.method == 'POST':
        try:
            data = request.get_json()
            if not data:
                return jsonify({'error': 'No data provided'}), 400
                
            date = data.get('date')
            mood = data.get('mood')
            note = data.get('note', '')
            
            if not date or mood is None:
                return jsonify({'error': 'Date and mood are required'}), 400
            
            cursor = conn.cursor()
            cursor.execute(
                """INSERT INTO mood_entries (user_id, date, mood, note) 
                   VALUES (%s, %s, %s, %s) 
                   ON DUPLICATE KEY UPDATE mood = VALUES(mood), note = VALUES(note)""",
                (current_user.id, date, float(mood), note)
            )
            conn.commit()
            cursor.close()
            
            return jsonify({'message': 'Настроение сохранено успешно'})
        except Error as e:
            print(f"Database error in mood_entries POST: {e}")
            return jsonify({'error': str(e)}), 500

# ДОБАВЛЕН МАРШРУТ ДЛЯ УДАЛЕНИЯ ЗАПИСИ НАСТРОЕНИЯ
@main.route('/api/mood_entries/<int:mood_id>', methods=['DELETE'])
@login_required
@with_db_connection
def delete_mood_entry(conn, mood_id):
    try:
        cursor = conn.cursor()
        cursor.execute(
            "DELETE FROM mood_entries WHERE id = %s AND user_id = %s",
            (mood_id, current_user.id)
        )
        conn.commit()
        cursor.close()
        return jsonify({'success': True})
    except Error as e:
        print(f"Database error in delete_mood_entry: {e}")
        return jsonify({'error': str(e)}), 500

# API маршруты для почасового настроения
@main.route('/api/hourly_moods', methods=['GET', 'POST'])
@login_required
@with_db_connection
def hourly_moods(conn):
    if request.method == 'GET':
        try:
            date_filter = request.args.get('date')
            print(f"🔍 GET hourly_moods - date: {date_filter}, user_id: {current_user.id}")
            
            if not date_filter:
                return jsonify({'error': 'Date parameter is required'}), 400
                
            cursor = conn.cursor(dictionary=True)
            cursor.execute(
                "SELECT id, user_id, date, hour, mood, note FROM hourly_moods WHERE user_id = %s AND date = %s ORDER BY hour",
                (current_user.id, date_filter)
            )
            entries = cursor.fetchall()
            
            print(f"📊 Found {len(entries)} hourly mood entries")
            
            # Преобразуем даты
            for entry in entries:
                if 'date' in entry and entry['date']:
                    entry['date'] = entry['date'].isoformat()
            
            cursor.close()
            return jsonify(entries)
        except Error as e:
            print(f"❌ Database error in hourly_moods GET: {e}")
            return jsonify({'error': str(e)}), 500
            
    elif request.method == 'POST':
        try:
            data = request.get_json()
            print(f"💾 POST hourly_moods - data: {data}, user_id: {current_user.id}")
            
            if not data:
                return jsonify({'error': 'No data provided'}), 400
                
            date = data.get('date')
            hour = data.get('hour')
            mood = data.get('mood')
            note = data.get('note', '')
            
            if not all([date, hour is not None, mood is not None]):
                return jsonify({'error': 'Date, hour and mood are required'}), 400
            
            cursor = conn.cursor()
            cursor.execute(
                """INSERT INTO hourly_moods (user_id, date, hour, mood, note) 
                   VALUES (%s, %s, %s, %s, %s) 
                   ON DUPLICATE KEY UPDATE mood = VALUES(mood), note = VALUES(note)""",
                (current_user.id, date, int(hour), int(mood), note)
            )
            conn.commit()
            cursor.close()
            
            print(f"✅ Hourly mood saved successfully - date: {date}, hour: {hour}, mood: {mood}")
            
            return jsonify({'message': 'Почасовое настроение сохранено успешно'})
        except Error as e:
            print(f"❌ Database error in hourly_moods POST: {e}")
            return jsonify({'error': str(e)}), 500

@main.route('/api/hourly_moods/<int:mood_id>', methods=['DELETE'])
@login_required
@with_db_connection
def delete_hourly_mood(conn, mood_id):
    try:
        cursor = conn.cursor()
        cursor.execute(
            "DELETE FROM hourly_moods WHERE id = %s AND user_id = %s",
            (mood_id, current_user.id)
        )
        conn.commit()
        cursor.close()
        return jsonify({'success': True})
    except Error as e:
        print(f"Database error in delete_hourly_mood: {e}")
        return jsonify({'error': str(e)}), 500

@main.route('/api/stats')
@login_required
@with_db_connection
def stats(conn):
    try:
        cursor = conn.cursor(dictionary=True)
        
        cursor.execute("""
            SELECT 
                COUNT(*) as total_entries,
                AVG(mood) as avg_mood,
                COUNT(CASE WHEN mood >= 7 THEN 1 END) as good_days
            FROM mood_entries 
            WHERE user_id = %s
        """, (current_user.id,))
        
        stats = cursor.fetchone()
        cursor.close()
        
        # Обрабатываем случай, когда нет записей
        total_entries = stats['total_entries'] or 0
        avg_mood = round(float(stats['avg_mood'] or 0), 1) if stats['avg_mood'] is not None else 0.0
        good_days = stats['good_days'] or 0
        
        return jsonify({
            'total_entries': total_entries,
            'avg_mood': avg_mood,
            'good_days': good_days,
            'current_streak': 0
        })
        
    except Error as e:
        print(f"Database error in stats: {e}")
        return jsonify({'error': str(e)}), 500

@main.route('/api/today_mood')
@login_required
@with_db_connection
def today_mood(conn):
    try:
        today = datetime.now().date().isoformat()
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            "SELECT mood, note FROM mood_entries WHERE user_id = %s AND date = %s",
            (current_user.id, today)
        )
        mood_entry = cursor.fetchone()
        cursor.close()
        
        if mood_entry:
            return jsonify({
                'mood': float(mood_entry['mood']),
                'note': mood_entry.get('note', '')
            })
        else:
            return jsonify({'mood': 5, 'note': ''})
        
    except Error as e:
        print(f"Database error in today_mood: {e}")
        return jsonify({'error': str(e)}), 500

# API маршруты для профиля
@main.route('/api/profile', methods=['PUT'])
@login_required
@with_db_connection
def update_profile(conn):
    try:
        data = request.get_json()
        if not data:
            return jsonify({'error': 'No data provided'}), 400
            
        first_name = data.get('first_name', '').strip()
        last_name = data.get('last_name', '').strip()
        
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE users SET first_name = %s, last_name = %s WHERE id = %s",
            (first_name, last_name, current_user.id)
        )
        conn.commit()
        cursor.close()
        
        return jsonify({'message': 'Профиль успешно обновлен'})
        
    except Error as e:
        print(f"Database error in update_profile: {e}")
        return jsonify({'error': str(e)}), 500

@main.route('/api/change_password', methods=['POST'])
@login_required
@with_db_connection
def change_password(conn):
    try:
        data = request.get_json()
        if not data:
            return jsonify({'error': 'No data provided'}), 400
            
        current_password = data.get('current_password')
        new_password = data.get('new_password')
        confirm_password = data.get('confirm_password')
        
        if not all([current_password, new_password, confirm_password]):
            return jsonify({'error': 'Все поля обязательны для заполнения'}), 400
            
        if new_password != confirm_password:
            return jsonify({'error': 'Новые пароли не совпадают'}), 400
            
        if len(new_password) < 8:
            return jsonify({'error': 'Новый пароль должен содержать не менее 8 символов'}), 400
        
        # Проверяем текущий пароль
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT password FROM users WHERE id = %s", (current_user.id,))
        user_data = cursor.fetchone()
        
        if not user_data:
            return jsonify({'error': 'Пользователь не найден'}), 404
            
        from app import bcrypt
        if not bcrypt.check_password_hash(user_data['password'], current_password):
            return jsonify({'error': 'Текущий пароль неверен'}), 400
        
        # Обновляем пароль
        hashed_password = bcrypt.generate_password_hash(new_password).decode('utf-8')
        cursor.execute(
            "UPDATE users SET password = %s WHERE id = %s",
            (hashed_password, current_user.id)
        )
        conn.commit()
        cursor.close()
        
        return jsonify({'message': 'Пароль успешно изменен'})
        
    except Error as e:
        print(f"Database error in change_password: {e}")
        return jsonify({'error': str(e)}), 500

# API маршруты для целей
@main.route('/api/goals', methods=['GET', 'POST'])
@login_required
@with_db_connection
def goals(conn):
    if request.method == 'GET':
        try:
            cursor = conn.cursor(dictionary=True)
            cursor.execute(
                "SELECT id, user_id, text, completed, created_at FROM goals WHERE user_id = %s ORDER BY created_at DESC",
                (current_user.id,)
            )
            goals_data = cursor.fetchall()
            
            # Преобразуем даты
            for goal in goals_data:
                if 'created_at' in goal and goal['created_at']:
                    goal['created_at'] = goal['created_at'].isoformat()
            
            cursor.close()
            return jsonify(goals_data)
        except Error as e:
            print(f"Database error in goals GET: {e}")
            return jsonify({'error': str(e)}), 500
            
    elif request.method == 'POST':
        try:
            data = request.get_json()
            if not data:
                return jsonify({'error': 'No data provided'}), 400
                
            text = data.get('text', '').strip()
            
            if not text:
                return jsonify({'error': 'Текст цели не может быть пустым'}), 400
            
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO goals (user_id, text) VALUES (%s, %s)",
                (current_user.id, text)
            )
            conn.commit()
            goal_id = cursor.lastrowid
            cursor.close()
            
            return jsonify({
                'id': goal_id, 
                'text': text, 
                'completed': False,
                'user_id': current_user.id
            })
        except Error as e:
            print(f"Database error in goals POST: {e}")
            return jsonify({'error': str(e)}), 500

@main.route('/api/goals/<int:goal_id>/toggle', methods=['POST'])
@login_required
@with_db_connection
def toggle_goal(conn, goal_id):
    try:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE goals SET completed = NOT completed WHERE id = %s AND user_id = %s",
            (goal_id, current_user.id)
        )
        conn.commit()
        cursor.close()
        return jsonify({'success': True})
    except Error as e:
        print(f"Database error in toggle_goal: {e}")
        return jsonify({'error': str(e)}), 500

@main.route('/api/goals/<int:goal_id>', methods=['DELETE'])
@login_required
@with_db_connection
def delete_goal(conn, goal_id):
    try:
        cursor = conn.cursor()
        cursor.execute(
            "DELETE FROM goals WHERE id = %s AND user_id = %s",
            (goal_id, current_user.id)
        )
        conn.commit()
        cursor.close()
        return jsonify({'success': True})
    except Error as e:
        print(f"Database error in delete_goal: {e}")
        return jsonify({'error': str(e)}), 500

# API маршруты для радостей
@main.route('/api/joys', methods=['GET', 'POST'])
@login_required
@with_db_connection
def joys(conn):
    if request.method == 'GET':
        try:
            cursor = conn.cursor(dictionary=True)
            cursor.execute(
                "SELECT id, user_id, text, created_at FROM joys WHERE user_id = %s ORDER BY created_at DESC",
                (current_user.id,)
            )
            joys_data = cursor.fetchall()
            
            # Преобразуем даты
            for joy in joys_data:
                if 'created_at' in joy and joy['created_at']:
                    joy['created_at'] = joy['created_at'].isoformat()
            
            cursor.close()
            return jsonify(joys_data)
        except Error as e:
            print(f"Database error in joys GET: {e}")
            return jsonify({'error': str(e)}), 500
            
    elif request.method == 'POST':
        try:
            data = request.get_json()
            if not data:
                return jsonify({'error': 'No data provided'}), 400
                
            text = data.get('text', '').strip()
            
            if not text:
                return jsonify({'error': 'Текст не может быть пустым'}), 400
            
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO joys (user_id, text) VALUES (%s, %s)",
                (current_user.id, text)
            )
            conn.commit()
            joy_id = cursor.lastrowid
            cursor.close()
            
            return jsonify({
                'id': joy_id, 
                'text': text,
                'user_id': current_user.id
            })
        except Error as e:
            print(f"Database error in joys POST: {e}")
            return jsonify({'error': str(e)}), 500

@main.route('/api/joys/<int:joy_id>', methods=['DELETE'])
@login_required
@with_db_connection
def delete_joy(conn, joy_id):
    try:
        cursor = conn.cursor()
        cursor.execute(
            "DELETE FROM joys WHERE id = %s AND user_id = %s",
            (joy_id, current_user.id)
        )
        conn.commit()
        cursor.close()
        return jsonify({'success': True})
    except Error as e:
        print(f"Database error in delete_joy: {e}")
        return jsonify({'error': str(e)}), 500

# API для загрузки аватара
@main.route('/api/upload_avatar', methods=['POST'])
@login_required
def upload_avatar():
    try:
        if 'avatar' not in request.files:
            return jsonify({'error': 'Файл не выбран'}), 400
            
        file = request.files['avatar']
        if file.filename == '':
            return jsonify({'error': 'Файл не выбран'}), 400
            
        if not file.content_type.startswith('image/'):
            return jsonify({'error': 'Файл должен быть изображением'}), 400
        
        # Создаем папку для аватаров, если её нет
        avatars_dir = os.path.join(current_app.root_path, 'static', 'avatars')
        os.makedirs(avatars_dir, exist_ok=True)
        
        # Генерируем уникальное имя файла
        import time
        filename = f"avatar_{current_user.id}_{int(time.time())}.jpg"
        filepath = os.path.join(avatars_dir, filename)
        
        # Сохраняем файл
        file.save(filepath)
        
        # Обновляем путь в БД
        conn = get_db()
        if conn is None:
            return jsonify({'error': 'Ошибка подключения к БД'}), 500
            
        try:
            cursor = conn.cursor()
            avatar_path = f"avatars/{filename}"
            cursor.execute(
                "UPDATE users SET avatar_path = %s WHERE id = %s",
                (avatar_path, current_user.id)
            )
            conn.commit()
            cursor.close()
            
            return jsonify({
                'message': 'Аватар успешно загружен', 
                'path': avatar_path
            })
            
        except Error as e:
            print(f"Database error in upload_avatar: {e}")
            return jsonify({'error': str(e)}), 500
        finally:
            close_db(conn)
        
    except Exception as e:
        print(f"Error in upload_avatar: {e}")
        return jsonify({'error': 'Ошибка загрузки файла'}), 500

# Экспорт в CSV
@main.route('/api/export/data')
@login_required
@with_db_connection
def export_data(conn):
    try:
        cursor = conn.cursor(dictionary=True)
        
        # Получаем данные настроения
        cursor.execute("""
            SELECT date, mood, note, created_at 
            FROM mood_entries 
            WHERE user_id = %s 
            ORDER BY date
        """, (current_user.id,))
        mood_data = cursor.fetchall()
        
        # Получаем цели
        cursor.execute("""
            SELECT text, completed, created_at 
            FROM goals 
            WHERE user_id = %s 
            ORDER BY created_at
        """, (current_user.id,))
        goals_data = cursor.fetchall()
        
        # Получаем радости
        cursor.execute("""
            SELECT text, created_at 
            FROM joys 
            WHERE user_id = %s 
            ORDER BY created_at
        """, (current_user.id,))
        joys_data = cursor.fetchall()
        
        cursor.close()
        
        # Создаем CSV в памяти
        output = io.StringIO()
        writer = csv.writer(output)
        
        # Заголовок файла
        writer.writerow(['Lumi - Экспорт данных'])
        writer.writerow(['Пользователь:', f"{current_user.first_name or ''} {current_user.last_name or ''}".strip()])
        writer.writerow(['Логин:', current_user.username])
        writer.writerow(['Дата экспорта:', datetime.now().strftime('%Y-%m-%d %H:%M:%S')])
        writer.writerow([])
        
        # Раздел настроения
        writer.writerow(['=== НАСТРОЕНИЕ ==='])
        writer.writerow(['Дата', 'Настроение (1-10)', 'Заметка', 'Дата создания'])
        for entry in mood_data:
            date = entry['date'].strftime('%Y-%m-%d') if entry['date'] else ''
            created_at = entry['created_at'].strftime('%Y-%m-%d %H:%M') if entry['created_at'] else ''
            writer.writerow([
                date,
                float(entry['mood']) if entry['mood'] else '',
                entry.get('note', ''),
                created_at
            ])
        writer.writerow([])
        
        # Раздел целей
        writer.writerow(['=== ЦЕЛИ ==='])
        writer.writerow(['Текст цели', 'Статус', 'Дата создания'])
        for goal in goals_data:
            status = 'Выполнено' if goal['completed'] else 'Не выполнено'
            created_at = goal['created_at'].strftime('%Y-%m-%d %H:%M') if goal['created_at'] else ''
            writer.writerow([
                goal['text'],
                status,
                created_at
            ])
        writer.writerow([])
        
        # Раздел радостей
        writer.writerow(['=== РАДОСТИ ==='])
        writer.writerow(['Текст', 'Дата создания'])
        for joy in joys_data:
            created_at = joy['created_at'].strftime('%Y-%m-%d %H:%M') if joy['created_at'] else ''
            writer.writerow([
                joy['text'],
                created_at
            ])
        
        # Подготовка ответа
        output.seek(0)
        response = current_app.response_class(
            output,
            mimetype='text/csv',
            headers={
                'Content-Disposition': f'attachment; filename=lumi_export_{datetime.now().strftime("%Y%m%d_%H%M")}.csv'
            }
        )
        
        return response
        
    except Error as e:
        print(f"Database error in export_data: {e}")
        return jsonify({'error': str(e)}), 500

@main.route('/api/delete_avatar', methods=['DELETE'])
@login_required
def delete_avatar():
    try:
        conn = get_db()
        if conn is None:
            return jsonify({'error': 'Ошибка подключения к БД'}), 500
            
        cursor = conn.cursor()
        # Устанавливаем avatar_path в NULL
        cursor.execute(
            "UPDATE users SET avatar_path = NULL WHERE id = %s",
            (current_user.id,)
        )
        conn.commit()
        cursor.close()
        
        return jsonify({'message': 'Аватар успешно удален'})
        
    except Error as e:
        print(f"Database error in delete_avatar: {e}")
        return jsonify({'error': str(e)}), 500
    finally:
        close_db(conn)
# Маршрут для страницы дневника цикла
# Маршрут для страницы дневника цикла
@main.route('/cycle-diary')
@login_required
def cycle_diary():
    return render_template('cycle_diary.html')

# API маршруты для менструального цикла
@main.route('/api/cycle_entries', methods=['GET', 'POST'])
@login_required
@with_db_connection
def cycle_entries(conn):
    if request.method == 'GET':
        try:
            date_filter = request.args.get('date')
            
            if date_filter:
                cursor = conn.cursor(dictionary=True)
                cursor.execute(
                    "SELECT id, user_id, date, cycle_day, symptoms, flow_intensity, mood, notes FROM cycle_entries WHERE user_id = %s AND date = %s",
                    (current_user.id, date_filter)
                )
            else:
                cursor = conn.cursor(dictionary=True)
                cursor.execute(
                    "SELECT id, user_id, date, cycle_day, symptoms, flow_intensity, mood, notes FROM cycle_entries WHERE user_id = %s ORDER BY date DESC",
                    (current_user.id,)
                )
            
            entries = cursor.fetchall()
            
            # Обрабатываем JSON симптомы
            for entry in entries:
                if entry['symptoms']:
                    try:
                        entry['symptoms'] = json.loads(entry['symptoms'])
                    except:
                        entry['symptoms'] = []
                else:
                    entry['symptoms'] = []
                
                if entry['date']:
                    entry['date'] = entry['date'].isoformat()
            
            cursor.close()
            return jsonify(entries)
        except Error as e:
            print(f"Database error in cycle_entries GET: {e}")
            return jsonify({'error': str(e)}), 500
            
    elif request.method == 'POST':
        try:
            data = request.get_json()
            if not data:
                return jsonify({'error': 'No data provided'}), 400
                
            date = data.get('date')
            cycle_day = data.get('cycle_day')
            symptoms = data.get('symptoms', [])
            flow_intensity = data.get('flow_intensity')
            mood = data.get('mood')
            notes = data.get('notes', '')
            
            if not date:
                return jsonify({'error': 'Date is required'}), 400
            
            symptoms_json = json.dumps(symptoms) if symptoms else None
            
            cursor = conn.cursor()
            cursor.execute(
                """INSERT INTO cycle_entries 
                   (user_id, date, cycle_day, symptoms, flow_intensity, mood, notes) 
                   VALUES (%s, %s, %s, %s, %s, %s, %s)
                   ON DUPLICATE KEY UPDATE 
                   cycle_day = VALUES(cycle_day), 
                   symptoms = VALUES(symptoms),
                   flow_intensity = VALUES(flow_intensity),
                   mood = VALUES(mood),
                   notes = VALUES(notes)""",
                (current_user.id, date, cycle_day, symptoms_json, flow_intensity, mood, notes)
            )
            conn.commit()
            cursor.close()
            
            return jsonify({'message': 'Данные цикла сохранены успешно'})
        except Error as e:
            print(f"Database error in cycle_entries POST: {e}")
            return jsonify({'error': str(e)}), 500

@main.route('/api/cycle_entries/<int:entry_id>', methods=['DELETE'])
@login_required
@with_db_connection
def delete_cycle_entry(conn, entry_id):
    try:
        cursor = conn.cursor()
        cursor.execute(
            "DELETE FROM cycle_entries WHERE id = %s AND user_id = %s",
            (entry_id, current_user.id)
        )
        conn.commit()
        cursor.close()
        return jsonify({'success': True})
    except Error as e:
        print(f"Database error in delete_cycle_entry: {e}")
        return jsonify({'error': str(e)}), 500

@main.route('/api/cycle_settings', methods=['GET', 'PUT'])
@login_required
@with_db_connection
def cycle_settings(conn):
    if request.method == 'GET':
        try:
            cursor = conn.cursor(dictionary=True)
            cursor.execute(
                "SELECT * FROM cycle_settings WHERE user_id = %s",
                (current_user.id,)
            )
            settings = cursor.fetchone()
            cursor.close()
            
            if settings and settings['last_period_start']:
                settings['last_period_start'] = settings['last_period_start'].isoformat()
            
            return jsonify(settings or {})
        except Error as e:
            print(f"Database error in cycle_settings GET: {e}")
            return jsonify({'error': str(e)}), 500
            
    elif request.method == 'PUT':
        try:
            data = request.get_json()
            if not data:
                return jsonify({'error': 'No data provided'}), 400
            
            cursor = conn.cursor()
            
            # Проверяем существующие настройки
            cursor.execute("SELECT id FROM cycle_settings WHERE user_id = %s", (current_user.id,))
            existing = cursor.fetchone()
            
            if existing:
                # Обновляем существующие
                cursor.execute(
                    """UPDATE cycle_settings SET 
                       cycle_length = %s, period_length = %s, last_period_start = %s,
                       notify_before_period = %s, notify_ovulation = %s
                       WHERE user_id = %s""",
                    (data.get('cycle_length'), data.get('period_length'), data.get('last_period_start'),
                     data.get('notify_before_period'), data.get('notify_ovulation'), current_user.id)
                )
            else:
                # Создаем новые
                cursor.execute(
                    """INSERT INTO cycle_settings 
                       (user_id, cycle_length, period_length, last_period_start, notify_before_period, notify_ovulation)
                       VALUES (%s, %s, %s, %s, %s, %s)""",
                    (current_user.id, data.get('cycle_length', 28), data.get('period_length', 5), 
                     data.get('last_period_start'), data.get('notify_before_period', True), 
                     data.get('notify_ovulation', True))
                )
            
            conn.commit()
            cursor.close()
            return jsonify({'message': 'Настройки цикла обновлены'})
        except Error as e:
            print(f"Database error in cycle_settings PUT: {e}")
            return jsonify({'error': str(e)}), 500

@main.route('/api/cycle_stats')
@login_required
@with_db_connection
def cycle_stats(conn):
    try:
        cursor = conn.cursor(dictionary=True)
        
        # Статистика по циклу
        cursor.execute("""
            SELECT 
                COUNT(*) as total_entries,
                AVG(mood) as avg_mood,
                COUNT(CASE WHEN flow_intensity IN ('medium', 'heavy') THEN 1 END) as period_days
            FROM cycle_entries 
            WHERE user_id = %s
        """, (current_user.id,))
        
        stats = cursor.fetchone()
        cursor.close()
        
        return jsonify({
            'total_entries': stats['total_entries'] or 0,
            'avg_mood': round(float(stats['avg_mood'] or 0), 1),
            'period_days': stats['period_days'] or 0
        })
        
    except Error as e:
        print(f"Database error in cycle_stats: {e}")
        return jsonify({'error': str(e)}), 500     

@main.route('/api/cycle_predictions')
@login_required
@with_db_connection
def cycle_predictions(conn):
    try:
        cursor = conn.cursor(dictionary=True)
        
        # Получаем настройки пользователя
        cursor.execute("SELECT * FROM cycle_settings WHERE user_id = %s", (current_user.id,))
        settings = cursor.fetchone()
        
        if not settings or not settings['last_period_start']:
            return jsonify({'error': 'Недостаточно данных для прогноза'}), 400
        
        # Простой расчет прогнозов
        last_period = settings['last_period_start']
        cycle_length = settings['cycle_length'] or 28
        period_length = settings['period_length'] or 5
        
        # Следующая менструация
        from datetime import timedelta
        next_period = last_period + timedelta(days=cycle_length)
        
        # Овуляция (примерно за 14 дней до следующей менструации)
        ovulation_date = next_period - timedelta(days=14)
        
        # Фертильное окно (за 5 дней до овуляции и 1 день после)
        fertile_start = ovulation_date - timedelta(days=5)
        fertile_end = ovulation_date + timedelta(days=1)
        
        cursor.close()
        
        return jsonify({
            'next_period': next_period.isoformat(),
            'ovulation_date': ovulation_date.isoformat(),
            'fertile_window': {
                'start': fertile_start.isoformat(),
                'end': fertile_end.isoformat()
            },
            'current_cycle_day': (datetime.now().date() - last_period).days + 1
        })
        
    except Error as e:
        print(f"Database error in cycle_predictions: {e}")
        return jsonify({'error': str(e)}), 500