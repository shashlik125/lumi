import os
import csv
import io
import json
import requests
import random
from datetime import datetime, date, timedelta
from flask import Blueprint, render_template, request, jsonify, send_file, current_app, flash, redirect, url_for
from flask_login import login_required, current_user
from app import get_db, close_db
from mysql.connector import Error
from functools import wraps

# Сначала определяем blueprint...........
main = Blueprint('main', __name__)

def with_db_connection(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        conn = None
        try:
            conn = get_db()
            if conn is None:
                return jsonify({'error': 'Database connection failed'}), 500
            
            result = f(conn, *args, **kwargs)
            return result
        except Error as e:
            print(f"❌ Database error in {f.__name__}: {e}")
            return jsonify({'error': str(e)}), 500
        except Exception as e:
            print(f"❌ Unexpected error in {f.__name__}: {e}")
            return jsonify({'error': str(e)}), 500
        finally:
            if conn:
                try:
                    close_db(conn)
                    print(f"✅ Connection closed in {f.__name__}")
                except Exception as e:
                    print(f"⚠️ Error closing connection in {f.__name__}: {e}")
    return decorated_function

# ================== ФУНКЦИИ АНАЛИЗА ==================

def generate_user_statistics(conn, user_id):
    """Генерация статистики пользователя для AI-анализа"""
    cursor = conn.cursor(dictionary=True)

    # 1. Среднее настроение за 30 дней
    cursor.execute("""
        SELECT 
            AVG(mood) as avg_mood,
            MIN(mood) as min_mood,
            MAX(mood) as max_mood,
            COUNT(*) as total_entries,
            COUNT(CASE WHEN mood >= 7 THEN 1 END) as good_days,
            COUNT(CASE WHEN mood <= 4 THEN 1 END) as bad_days
        FROM mood_entries
        WHERE user_id = %s
        AND date >= DATE_SUB(CURDATE(), INTERVAL 30 DAY)
    """, (user_id,))
    
    mood_stats = cursor.fetchone()

    # 2. Тренд настроения (последние 7 дней vs предыдущие 7 дней)
    cursor.execute("""
        SELECT 
            AVG(CASE 
                WHEN date >= DATE_SUB(CURDATE(), INTERVAL 7 DAY) 
                THEN mood 
            END) as avg_recent,
            AVG(CASE 
                WHEN date >= DATE_SUB(CURDATE(), INTERVAL 14 DAY) 
                AND date < DATE_SUB(CURDATE(), INTERVAL 7 DAY)
                THEN mood 
            END) as avg_previous,
            COUNT(CASE 
                WHEN date >= DATE_SUB(CURDATE(), INTERVAL 7 DAY) 
                THEN 1 
            END) as recent_count
        FROM mood_entries
        WHERE user_id = %s
    """, (user_id,))
    
    trend_stats = cursor.fetchone()
    
    # Вычисляем тренд
    trend = "stable"
    trend_value = 0
    
    if trend_stats['avg_recent'] and trend_stats['avg_previous'] and trend_stats['recent_count'] >= 3:
        diff = float(trend_stats['avg_recent']) - float(trend_stats['avg_previous'])
        trend_value = diff
        if diff > 0.5:
            trend = "improving"
        elif diff < -0.5:
            trend = "declining"

    # 3. Лучшее и худшее время дня
    cursor.execute("""
        SELECT hour, AVG(mood) as avg_mood, COUNT(*) as entries
        FROM hourly_moods
        WHERE user_id = %s
        GROUP BY hour
        HAVING COUNT(*) >= 2
        ORDER BY hour
    """, (user_id,))
    
    hours_data = cursor.fetchall()

    worst_hour = None
    best_hour = None
    hourly_analysis = ""

    if hours_data:
        valid_hours = [h for h in hours_data if h['entries'] >= 2]
        if valid_hours:
            worst_hour = min(valid_hours, key=lambda x: x['avg_mood'])
            best_hour = max(valid_hours, key=lambda x: x['avg_mood'])
            
            # Формируем анализ часов
            low_hours = [h for h in valid_hours if h['avg_mood'] < 5]
            high_hours = [h for h in valid_hours if h['avg_mood'] > 7]
            
            if low_hours:
                hourly_analysis += f"Низкое настроение часто в {', '.join(str(h['hour']) for h in low_hours)}:00. "
            if high_hours:
                hourly_analysis += f"Высокое настроение обычно в {', '.join(str(h['hour']) for h in high_hours)}:00."

    # 4. Анализ заметок
    cursor.execute("""
        SELECT note, mood, date
        FROM mood_entries
        WHERE user_id = %s
        AND note IS NOT NULL
        AND note != ''
        AND LENGTH(note) > 5
        ORDER BY date DESC
        LIMIT 100
    """, (user_id,))
    
    notes_data = cursor.fetchall()
    
    # Ключевые слова для анализа
    positive_keywords = ['рад', 'счастлив', 'хорошо', 'отлично', 'прекрасно', 'ура', 'успех', 'люблю', 'доволен', 'восторг']
    negative_keywords = ['стресс', 'устал', 'плохо', 'грустно', 'тревог', 'злой', 'раздраж', 'беспокоит', 'уныло', 'тоска']
    neutral_keywords = ['норм', 'обычно', 'стабильно', 'так себе', 'ничего', 'окей']
    
    keyword_counts = {
        'positive': 0,
        'negative': 0,
        'neutral': 0
    }
    
    recent_positive = 0
    recent_negative = 0
    all_notes_text = []
    
    for note in notes_data:
        note_text = note.get('note', '')  # безопасно, если note нет или None
        if isinstance(note_text, str) and note_text.strip():  # проверяем, что это непустая строка
            note_text = note_text.lower()
        all_notes_text.append(note_text)
        
        # Считаем ключевые слова
        if any(keyword in note_text for keyword in positive_keywords):
            keyword_counts['positive'] += 1
        if any(keyword in note_text for keyword in negative_keywords):
            keyword_counts['negative'] += 1
        if any(keyword in note_text for keyword in neutral_keywords):
            keyword_counts['neutral'] += 1
        
        # Разделяем по времени (последние 7 дней)
        note_date = note.get('date')
        if isinstance(note_date, str):
            try:
                note_date = datetime.strptime(note_date, '%Y-%m-%d').date()
            except ValueError:
                continue  # если дата в неверном формате, пропускаем
        elif isinstance(note_date, datetime):
            note_date = note_date.date()
        else:
            continue  # если дата вообще не строка и не datetime, пропускаем

        if note_date >= datetime.now().date() - timedelta(days=7):
            if any(keyword in note_text for keyword in positive_keywords):
                recent_positive += 1
            if any(keyword in note_text for keyword in negative_keywords):
                recent_negative += 1

    # 5. Дни недели анализ
    cursor.execute("""
        SELECT 
            DAYOFWEEK(date) as day_of_week,
            COUNT(*) as count,
            AVG(mood) as avg_mood
        FROM mood_entries
        WHERE user_id = %s
        GROUP BY DAYOFWEEK(date)
        HAVING COUNT(*) >= 3
        ORDER BY avg_mood
    """, (user_id,))
    
    days_data = cursor.fetchall()
    
    worst_day = None
    best_day = None
    day_names_russian = {
        1: 'воскресенье', 2: 'понедельник', 3: 'вторник', 
        4: 'среда', 5: 'четверг', 6: 'пятница', 7: 'суббота'
    }
    
    if days_data:
        worst_day_data = min(days_data, key=lambda x: x['avg_mood'])
        best_day_data = max(days_data, key=lambda x: x['avg_mood'])
        
        if worst_day_data['count'] >= 3:
            worst_day = {
                'name': day_names_russian.get(worst_day_data['day_of_week'], ''),
                'avg_mood': float(worst_day_data['avg_mood']),
                'count': worst_day_data['count']
            }
        
        if best_day_data['count'] >= 3:
            best_day = {
                'name': day_names_russian.get(best_day_data['day_of_week'], ''),
                'avg_mood': float(best_day_data['avg_mood']),
                'count': best_day_data['count']
            }

    # 6. Анализ циклов (если пользователь женского пола)
    cycle_analysis = ""
    cursor.execute("SELECT gender FROM users WHERE id = %s", (user_id,))
    user_gender = cursor.fetchone()
    
    if user_gender and user_gender['gender'] == 'female':
        cursor.execute("""
            SELECT 
                AVG(mood) as avg_mood_cycle,
                COUNT(*) as cycle_entries
            FROM cycle_entries
            WHERE user_id = %s
        """, (user_id,))
        
        cycle_stats = cursor.fetchone()
        
        if cycle_stats and cycle_stats['cycle_entries'] >= 5:
            cycle_analysis = f"У вас {cycle_stats['cycle_entries']} записей в дневнике цикла. "
            if cycle_stats['avg_mood_cycle']:
                cycle_analysis += f"Среднее настроение в дни цикла: {float(cycle_stats['avg_mood_cycle']):.1f}/10."
    # 7. Анализ радостей (joys)
    cursor.execute("""
        SELECT COUNT(*) as joys_count
        FROM joys
        WHERE user_id = %s
    """, (user_id,))
    joys_stats = cursor.fetchone()

    # 8. Последние 5 радостей
    cursor.execute("""
        SELECT text, created_at
        FROM joys
        WHERE user_id = %s
        ORDER BY created_at DESC
        LIMIT 5
    """, (user_id,))
    recent_joys = cursor.fetchall()
            # 9. Статистика цикла (общая)
    cursor.execute("""
        SELECT 
            COUNT(*) as cycle_entries,
            COUNT(CASE WHEN flow_intensity IN ('light', 'medium', 'heavy') THEN 1 END) as period_days,
            AVG(mood) as avg_mood_cycle
        FROM cycle_entries 
        WHERE user_id = %s
    """, (user_id,))
    cycle_stats_summary = cursor.fetchone()
    
    cursor.close()

    # Формируем финальный объект статистики
    stats = {
        # Основные показатели
        "avg_mood": round(float(mood_stats['avg_mood'] or 0), 1) if mood_stats['avg_mood'] else 0.0,
        "min_mood": float(mood_stats['min_mood'] or 0),
        "max_mood": float(mood_stats['max_mood'] or 0),
        "total_entries": mood_stats['total_entries'] or 0,
        "good_days": mood_stats['good_days'] or 0,
        "bad_days": mood_stats['bad_days'] or 0,
        
        # Тренды
        "trend": trend,
        "trend_value": trend_value,
        "avg_recent": float(trend_stats['avg_recent'] or 0),
        "avg_previous": float(trend_stats['avg_previous'] or 0),
        
        # Временной анализ
        "worst_hour": worst_hour,
        "best_hour": best_hour,
        "hourly_analysis": hourly_analysis,
        
        # Анализ заметок
        "keyword_counts": keyword_counts,
        "recent_positive": recent_positive,
        "recent_negative": recent_negative,
        "notes_sample": all_notes_text[:5],
        
        # Дни недели
        "worst_day": worst_day,
        "best_day": best_day,
        
        # Циклы
        "cycle_analysis": cycle_analysis,
        
                # ⭐⭐⭐ ДОБАВЛЯЕМ РАДОСТИ ⭐⭐⭐
        "joys_count": joys_stats['joys_count'] if joys_stats else 0,
        "recent_joys": [joy['text'] for joy in recent_joys] if recent_joys else [],
        
        # ⭐⭐⭐ ДОБАВЛЯЕМ СТАТИСТИКУ ЦИКЛА ⭐⭐⭐
        "cycle_entries": cycle_stats_summary['cycle_entries'] if cycle_stats_summary else 0,
        "period_days": cycle_stats_summary['period_days'] if cycle_stats_summary else 0,
        "avg_mood_cycle": float(cycle_stats_summary['avg_mood_cycle'] or 0) if cycle_stats_summary and cycle_stats_summary['avg_mood_cycle'] else 0,
        
        # Общая оценка
        "mood_score": 0
    }
    
    # Вычисляем общий балл настроения (0-100)
    mood_score = 0
    
    # Балл за среднее настроение (50%)
    if stats['avg_mood'] > 0:
        mood_score += min(50, stats['avg_mood'] * 5)
    
    # Балл за тренд (20%)
    if trend == "improving":
        mood_score += 20
    elif trend == "declining":
        mood_score += 5
    else:
        mood_score += 10
    
    # Балл за соотношение хороших/плохих дней (20%)
    if stats['total_entries'] > 0:
        good_ratio = stats['good_days'] / stats['total_entries']
        mood_score += min(20, good_ratio * 20)
    
    # Балл за заметки (10%)
    if stats['keyword_counts']['positive'] > stats['keyword_counts']['negative']:
        mood_score += 10
    elif stats['keyword_counts']['positive'] == stats['keyword_counts']['negative']:
        mood_score += 5
    
    stats['mood_score'] = min(100, max(0, int(mood_score)))
    
    return stats

def generate_ai_insights(stats):
    """Генерация умных выводов на основе статистики"""
    
    insights = []
    
    # 1. Основное состояние
    avg_mood = stats['avg_mood']
    if avg_mood >= 7:
        insights.append(f"В целом у вас хорошее настроение! Средний балл {avg_mood:.1f}/10 - это отличный результат. 🌟")
    elif avg_mood >= 5:
        insights.append(f"Настроение стабильное (средний балл {avg_mood:.1f}/10). Есть пространство для небольших улучшений.")
    else:
        insights.append(f"Настроение в последнее время ниже среднего ({avg_mood:.1f}/10). Возможно, стоит уделить больше внимания самочувствию. 💭")
    
    # 2. Тренд
    if stats['trend'] == "improving":
        insights.append(f"Замечательно! Настроение улучшается - последняя неделя лучше предыдущей на {stats['trend_value']:.1f} баллов. 📈")
    elif stats['trend'] == "declining":
        insights.append("Я заметил, что настроение немного снизилось за последнюю неделю. Может, стоит добавить больше приятных моментов в день?")
    
    # 3. Временной анализ
    if stats['worst_hour']:
        worst_hour_val = stats['worst_hour']['hour']
        worst_mood = stats['worst_hour']['avg_mood']
        insights.append(f"Чаще всего настроение падает около {worst_hour_val}:00 (средний балл {worst_mood:.1f}/10). Возможно, в это время стоит делать небольшой перерыв. ☕")
    
    if stats['best_hour']:
        best_hour_val = stats['best_hour']['hour']
        best_mood = stats['best_hour']['avg_mood']
        insights.append(f"Лучшее настроение обычно около {best_hour_val}:00 (средний балл {best_mood:.1f}/10). Попробуйте планировать важные дела на это время! 💪")
    
    # 4. Дни недели
    if stats['worst_day']:
        insights.append(f"{stats['worst_day']['name'].capitalize()} обычно самые сложные дни (среднее настроение {stats['worst_day']['avg_mood']:.1f}/10). Может, стоит планировать на них меньше нагрузки? 📅")
    
    if stats['best_day']:
        insights.append(f"{stats['best_day']['name'].capitalize()} - ваши любимые дни! Настроение в среднем {stats['best_day']['avg_mood']:.1f}/10. Отлично! 🎉")
    
    # 5. Анализ заметок
    pos = stats['keyword_counts']['positive']
    neg = stats['keyword_counts']['negative']
    
    if pos > neg * 2:
        insights.append("В ваших заметках много позитивных слов - вы часто отмечаете хорошие моменты! Это прекрасная привычка. ✨")
    elif neg > pos * 2:
        insights.append("В заметках преобладают сложные эмоции. Попробуйте каждый день находить хотя бы одну маленькую радость. 🌈")
    
    if stats['recent_positive'] > stats['recent_negative'] * 2:
        insights.append("В последнюю неделю стало больше позитивных записей - это отличный прогресс! 🚀")
    
    # 6. Соотношение хороших/плохих дней
    if stats['total_entries'] > 0:
        good_percentage = (stats['good_days'] / stats['total_entries']) * 100
        if good_percentage > 70:
            insights.append(f"У вас {good_percentage:.0f}% хороших дней - это впечатляюще! 🌞")
        elif good_percentage < 30:
            insights.append(f"Хороших дней пока меньше ({good_percentage:.0f}%). Давайте вместе найдем способы добавить больше света в ваши дни. 💡")
    
    # 7. Общий совет на основе оценки
    mood_score = stats['mood_score']
    if mood_score >= 80:
        insights.append(f"Ваш общий балл ментального благополучия: {mood_score}/100. Отличный результат! Продолжайте в том же духе. 🏆")
    elif mood_score >= 60:
        insights.append(f"Общий балл: {mood_score}/100. Неплохо! Есть над чем работать, но основа хорошая. 💪")
    else:
        insights.append(f"Общий балл: {mood_score}/100. Есть пространство для улучшений. Попробуйте добавить ежедневные ритуалы заботы о себе. 🌱")
    
    # 8. Цикличность (для женщин)
    if stats['cycle_analysis']:
        insights.append(stats['cycle_analysis'])
    elif 'cycle_entries' in stats:
        if stats['cycle_entries'] >= 10:
            insights.append(f"📊 У тебя {stats['cycle_entries']} записей о цикле. Отлично отслеживаешь!")
        elif stats['cycle_entries'] >= 5:
            insights.append(f"🌸 У тебя {stats['cycle_entries']} записей о цикле. Продолжай отмечать!")
        elif stats['cycle_entries'] > 0:
            insights.append(f"🌸 Ты начала отслеживать цикл ({stats['cycle_entries']} записей).")
        else:
            insights.append("🌸 Отслеживай цикл в дневнике — это поможет понять влияние физиологии на настроение!")
    
    # 9. Анализ радостей (joys)
    if 'joys_count' in stats:
        if stats['joys_count'] > 0:
            if stats['joys_count'] >= 10:
                insights.append(f"Ты записал уже {stats['joys_count']} радостей! 🎉 Отличная привычка замечать хорошее!")
            elif stats['joys_count'] >= 5:
                insights.append(f"У тебя {stats['joys_count']} записанных радостей. Продолжай копить позитивные моменты! ✨")
            elif stats['joys_count'] >= 1:
                insights.append(f"Ты начал записывать радости — это первый шаг к осознанности! 🌸")
            
            # Добавляем примеры последних радостей
            if stats.get('recent_joys') and len(stats['recent_joys']) > 0:
                joys_text = ", ".join(stats['recent_joys'][:3])
                insights.append(f"Недавно ты радовался(ась): {joys_text}. 😊")
        else:
            insights.append("Попробуй записывать маленькие радости дня — это помогает замечать хорошее даже в обычные дни. 📝")
    
    # 10. Добавляем рандомный совет из базы
    random_advice = get_random_advice(stats)
    if random_advice:
        insights.append(random_advice)
    
    # Убираем лишний пробел в конце
    result = " ".join(insights)
    return result.strip()

def get_random_advice(stats):
    """Возвращает случайный совет на основе статистики"""
    
    advice_pool = []
    
    # Совет по настроению
    if stats['avg_mood'] < 5:
        advice_pool.extend([
            "Попробуйте технику благодарности: каждый вечер записывайте 3 хорошие вещи, которые случились за день.",
            "10-минутная прогулка на свежем воздухе может значительно улучшить настроение.",
            "Позвоните близкому другу или родственнику - социальные связи важны для эмоционального здоровья."
        ])
    
    # Совет по усталости (если есть такие заметки)
    if any('устал' in note for note in stats.get('notes_sample', [])):
        advice_pool.extend([
            "Попробуйте технику 'помодоро': 25 минут работы, 5 минут отдыха.",
            "Убедитесь, что спите достаточно - 7-8 часов сна творят чудеса.",
            "Делайте короткие перерывы каждые 60-90 минут работы."
        ])
    
    # Совет по стрессу
    if stats['keyword_counts']['negative'] > 3:
        advice_pool.extend([
            "Дыхательная техника 4-7-8: вдох на 4, задержка на 7, выдох на 8 секунд.",
            "Запишите тревожные мысли на бумагу - это помогает разгрузить ум.",
            "Попробуйте 5-минутную медитацию утром или вечером."
        ])
    
    # Общие советы
    advice_pool.extend([
        "Отмечайте маленькие победы каждый день - они важны!",
        "Пейте достаточно воды - обезвоживание влияет на настроение.",
        "Планируйте хотя бы одно приятное занятие на каждый день.",
        "Практикуйте цифровой детокс: 1 час без гаджетов перед сном.",
        "Физическая активность 30 минут в день улучшает настроение.",
        "Читайте перед сном вместо просмотра соцсетей."
    ])
    
    # Совет по радостям
    advice_pool.extend([
        "Записывать радости — как собирать конфетти счастья. Попробуй каждый день хотя бы одну! 🎊",
        "Перечитай свои записи радостей, когда грустно — это работает как тёплый плед. 📖",
        "Маленькие радости важнее больших побед, потому что они случаются каждый день. 🌈",
        "Сегодня была хоть маленькая радость? Запиши её в дневник радостей! ✨",
        "Копилка радостей помогает видеть, что хорошего случилось за неделю. 💝"
    ])
    
    if advice_pool:
        return random.choice(advice_pool)
    return None

def get_fallback_response(user_message):
    """Локальные ответы если API недоступно"""
    user_message_lower = user_message.lower()
    
    # Простые ответы на русском
    responses = {
        'привет': ['Привет! Как твое настроение сегодня? 😊', 'Здравствуй! Рада тебя видеть! 🌈'],
        'как дела': ['У меня все отлично! А у тебя как дела?', 'Спасибо, хорошо! Как твое настроение?'],
        'плохо': [
            'Мне жаль это слышать 😔 Хочешь рассказать, что случилось?',
            'Понимаю, что может быть тяжело. Ты не одинок в своих чувствах 🤗',
            'Иногда просто выговориться уже помогает. Я здесь, чтобы выслушать 👂'
        ],
        'хорошо': [
            'Это прекрасно! Рада за тебя 😄 Что особенно порадовало сегодня?',
            'Здорово слышать! Позитивное настроение - это суперсила! 💪',
            'Отлично! Попробуй зафиксировать это чувство в дневнике настроения 📔'
        ],
        '10/10': [
            'Отлично! 10/10 - это прекрасно! Что особенно порадовало сегодня? 🎉',
            'Супер! Настроение 10/10 - ты на вершине мира! 🌟',
            '10 баллов из 10? Вот это да! Поделись секретом своего настроения! ✨'
        ],
        '9/10': [
            'Почти идеально! 9/10 - отличный результат! 🌈',
            'Прекрасно! С небольшим улучшением будет 10/10! 💪'
        ],
        '8/10': [
            'Хорошо! 8/10 - это здорово! 🌟',
            'Отличное настроение! Продолжай в том же духе! 😊'
        ],
        '7/10': [
            'Неплохо! 7/10 - стабильно хорошо! 👍',
            'Хороший день! Может быть, завтра будет ещё лучше! 🌈'
        ],
        '6/10': [
            'Нормально! 6/10 - неплохо, но есть куда расти! 🌱',
            'Середнячок! Может, добавить немного позитива в день? 🌞'
        ],
        '5/10': [
            'Так себе день... 5/10 - нейтрально. Может, стоит отдохнуть? ☕',
            'Серединка на половинку. Может, вечер порадует? 🌙'
        ],
        '4/10': [
            'Не очень... 4/10 - может, стоит поделиться, что случилось? 💭',
            'Сложный день? Иногда помогает просто выговориться. 👂'
        ],
        '3/10': [
            'Тяжело... 3/10 - мне жаль это слышать. Хочешь рассказать? 😔',
            'Сложный период? Помни, что это временно. 🌧️'
        ],
        '2/10': [
            'Очень тяжело... 2/10 - я здесь, чтобы выслушать. 🤗',
            'Такие дни бывают. Ты не одинок. 💪'
        ],
        '1/10': [
            'Критично... 1/10 - может, стоит обратиться к кому-то близкому или специалисту? 🆘',
            'Очень сложный день. Не бойся просить о помощи. ❤️'
        ],
        'стресс': [
            'Попробуй технику глубокого дыхания: вдох на 4, задержка на 4, выдох на 6 🧘‍♀️',
            'Стресс - временное состояние. Попробуй отвлечься на что-то приятное 🌿',
            'Иногда помогает прогулка на свежем воздухе. Хоть 10 минут! 🚶‍♀️'
        ],
        'тревож': [
            'Тревога - это нормально. Попробуй технику "5-4-3-2-1": назови 5 вещей, которые видишь, 4 - которые чувствуешь, 3 - которые слышишь, 2 - которые нюхаешь, 1 - пробуешь на вкус.',
            'Попробуй заземлиться: почувствуй стул под собой, ноги на полу. Ты здесь и сейчас. 🌍',
            'Иногда помогает записать тревожные мысли на бумагу 📝'
        ],
        'спасибо': [
            'Всегда пожалуйста! Я рада, что могу быть полезной 😊',
            'Благодарю тебя за доверие! 💖',
            'Обращайся в любое время! ✨'
        ],
        'помощь': [
            'Я могу: 1) Поболтать с тобой 2) Поддержать в сложный момент 3) Дать совет по управлению настроением 4) Помочь разобраться в эмоциях',
            'Чем я могу помочь? Расскажи о своем настроении или спроси совета!',
            'В нашем приложении ты можешь отслеживать настроение, ставить цели и отмечать радости дня!'
        ],
        'настроен': [
            'Как твое настроение сегодня по шкале от 1 до 10? Попробуй оценить в календаре! 📊',
            'Заметка о настроении сегодня может помочь лучше понять свои эмоции.',
            'Просмотр статистики настроения в разделе "Анализ" помогает увидеть закономерности.'
        ],
        'что делать': [
            'Попробуй: 1) Прогуляться 2) Послушать любимую музыку 3) Выпить чашку чая 4) Позвонить другу',
            'Иногда помогает смена деятельности. Что ты обычно делаешь, чтобы поднять настроение?',
            'Маленькие радости каждый день создают большие изменения! 🌟'
        ],
        'устал': [
            'Отдохни немного. Ты заслуживаешь перерыва! ☕',
            'Усталость - сигнал тела. Давай себе время на восстановление 🛋️',
            'Попробуй короткий отдых: 15-20 минут могут творить чудеса!'
        ],
        'одиноко': [
            'Ты не одинок в этом чувстве. Многие проходят через это 🌙',
            'Попробуй связаться с кем-то близким, даже просто написать сообщение 💌',
            'Иногда помогает заняться чем-то творческим: рисование, письмо, музыка 🎨'
        ],
        'радост': [
            'Ого! Ты записываешь радости? Это так круто! 🌟 Что сегодня порадовало?',
            'Копилка радостей — лучшее, что можно вести! Поделишься? ✨',
            'Радости делают день ярче. Записывай их в дневник радостей! 💖'
        ],
        'lumi': [
            'Lumi - это трекер настроения, который помогает понимать свои эмоции и улучшать ментальное здоровье! 🌈',
            'В Lumi ты можешь: отслеживать настроение каждый день, смотреть статистику, ставить цели, отмечать радости!',
            'Попробуй все функции Lumi: календарь настроения, анализ статистики, дневник радостей!'
        ]
    }
    
    # Ищем ключевые слова
    for keyword, reply_list in responses.items():
        if keyword in user_message_lower:
            return random.choice(reply_list)
    
    # Общие ответы если не нашли ключевых слов
    general_responses = [
        'Расскажи мне больше о том, что ты чувствуешь... 👂',
        'Я тебя слушаю. Продолжай, пожалуйста 💭',
        'Как прошел твой день? Хочешь поделиться? 🌈',
        'Заметка о настроении в приложении может помочь разобраться в эмоциях.',
        'Ты молодец, что обращаешь внимание на свои чувства! 💪',
        'Эмоции приходят и уходят, как волны. Ты сильнее, чем думаешь! 🌊',
        'Маленькие шаги каждый день приводят к большим изменениям 🚀',
        'Сегодня сложный день? Это нормально. Завтра может быть лучше ☀️'
    ]
    
    return random.choice(general_responses)

def generate_smart_response(user_message, context, history):
    """Генерация умного ответа с учётом контекста и истории (без YandexGPT)"""
    import random
    user_message_lower = user_message.lower()
    
    # Приветствия
    if any(w in user_message_lower for w in ['привет', 'здравствуй', 'доброе', 'хай']):
        return f"Привет! Рада тебя видеть! 🌸 Как твоё настроение сегодня? {context}"
    
    # Вопросы о самочувствии
    if any(w in user_message_lower for w in ['как дела', 'как настроение', 'как ты']):
        if context:
            return f"У меня всё отлично, спасибо! А у тебя? {context}"
        return "У меня всё замечательно! А как твой день проходит? Расскажи! 💫"
    
    # Сложные эмоции
    if any(w in user_message_lower for w in ['плохо', 'грустно', 'тяжело', 'устал', 'стресс']):
        return "Мне жаль это слышать 😔 Хочешь рассказать, что случилось? Я здесь, чтобы выслушать и поддержать. 🤗"
    
    # Позитивные эмоции
    if any(w in user_message_lower for w in ['хорошо', 'отлично', 'прекрасно', 'радостно', 'счастлив']):
        return "Это замечательно! 😊 Расскажи, что тебя порадовало? Давай запишем это в копилку радостей! ✨"
    
    # Вопросы о возможностях
    if any(w in user_message_lower for w in ['что ты умеешь', 'помощь', 'функции', 'можешь']):
        return """Я умею многое! 💫

📊 **Анализировать настроение** — скажи «проанализируй мои данные»
📅 **Показывать паттерны** — спроси «в какие дни настроение лучше»
📝 **Читать заметки** — скажи «покажи мои заметки»
✨ **Рассказывать о радостях** — спроси «что меня радовало»
🎯 **Отслеживать цели** — скажи «мои цели»

А ещё я просто могу с тобой поговорить и поддержать! Что хочешь? 🌸"""
    
    # Благодарности
    if any(w in user_message_lower for w in ['спасибо', 'благодарю']):
        return "Пожалуйста! Я всегда рада помочь. Обращайся в любое время! 💖"
    
    # Прощания
    if any(w in user_message_lower for w in ['пока', 'до свидания', 'увидимся']):
        return "Пока! Хорошего дня! Заглядывай ещё — я всегда здесь, чтобы поддержать тебя. 🌈"
    
    # Обычный ответ (дружеский, поддерживающий)
    responses = [
        f"Поняла тебя! {context} Расскажи ещё, что у тебя сегодня происходит? 💭",
        f"Интересно... А что ещё было сегодня? {context}",
        f"Я тебя слышу. {context} Хочешь обсудить что-то конкретное? 🌸",
        f"Спасибо, что делишься! {context} Это помогает лучше понимать себя. 💪",
        f"Продолжай, я внимательно слушаю 👂 {context}"
    ]
    return random.choice(responses)

def get_user_notes(conn, user_id):
    cursor = conn.cursor(dictionary=True)
    cursor.execute("""
        SELECT note, date 
        FROM mood_entries 
        WHERE user_id = %s AND note IS NOT NULL AND note != ''
        ORDER BY date DESC
        LIMIT 20
    """, (user_id,))
    notes = cursor.fetchall()
    cursor.close()
    return notes

def get_user_goals(conn, user_id):
    cursor = conn.cursor(dictionary=True)
    cursor.execute("""
        SELECT text, completed, created_at 
        FROM goals 
        WHERE user_id = %s 
        ORDER BY created_at DESC
        LIMIT 20
    """, (user_id,))
    goals = cursor.fetchall()
    cursor.close()
    return goals

@main.route('/api/chatbot', methods=['POST'])
@with_db_connection
def chatbot(conn):
    data = request.get_json()
    user_id = data.get('user_id')
    user_message = data.get('message', '').strip().lower()
    print(f"DEBUG user_message: '{user_message}'")

    if not user_id or not user_message:
        return jsonify({'error': 'user_id или message отсутствуют'}), 400

    try:
        # Заметки
        if "заметки" in user_message:
            notes = get_user_notes(conn, user_id)
            if notes:
                notes_text = "\n".join([f"{n['date']}: {n['note']}" for n in notes])
                return jsonify({'response': f"Вот твои последние заметки:\n{notes_text}"})
            else:
                return jsonify({'response': "У тебя пока нет заметок 😔"})

        # Цели
        if any(word in user_message for word in ["цели", "задачи", "планы"]):
            print("DEBUG: команда цели распознана")
            goals = get_user_goals(conn, user_id)
            print(f"DEBUG: goals from DB: {goals}")
            if goals:
                goals_text = "\n".join([
                    f"{g['created_at'].strftime('%d.%m.%Y') if isinstance(g['created_at'], datetime) else g['created_at']}: {g['text']} ({'выполнено' if g['completed'] else 'не выполнено'})"
                    for g in goals
                ])
                return jsonify({'response': f"Вот твои цели:\n{goals_text}"})
            else:
                return jsonify({'response': "У тебя пока нет целей 😔"})

        # Fallback / общий анализ
        stats = generate_user_statistics(conn, user_id)
        ai_response = generate_ai_insights(stats)
        fallback_response = get_fallback_response(user_message)
        final_response = f"{fallback_response}\n\n{ai_response}"
        return jsonify({'response': final_response})

    except Exception as e:
        print(f"Ошибка в chatbot: {e}")
        return jsonify({'response': get_fallback_response(user_message)})

# ================== ОСНОВНЫЕ МАРШРУТЫ СТРАНИЦ ==================

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

@main.route('/cycle-diary')
@login_required
def cycle_diary():
    # Проверка пола
    if current_user.gender != 'female':
        flash('Эта страница доступна только для пользователей женского пола', 'error')
        return redirect(url_for('main.dashboard'))
    return render_template('cycle_diary.html')

@main.route('/api/pool-status')
@login_required
def pool_status():
    """Проверка статуса пула соединений"""
    try:
        from app import mysql_pool
        if mysql_pool:
            return jsonify({
                'status': 'active',
                'pool_name': mysql_pool.pool_name,
                'pool_size': mysql_pool.pool_size
            })
        else:
            return jsonify({'status': 'not_initialized'})
    except Exception as e:
        return jsonify({'status': 'error', 'error': str(e)})

# ================== API МАРШРУТЫ ДЛЯ НАСТРОЕНИЯ ==================

# ================== MOOD ENTRIES ==================
@main.route('/api/mood_entries', methods=['GET', 'POST'])
@login_required
@with_db_connection
def mood_entries(conn):
    if request.method == 'GET':
        try:
            date_filter = request.args.get('date')
            with conn.cursor(buffered=True, dictionary=True) as cursor:
                if date_filter:
                    cursor.execute(
                        "SELECT id, user_id, date, mood, note, created_at "
                        "FROM mood_entries WHERE user_id = %s AND date = %s ORDER BY date DESC",
                        (current_user.id, date_filter)
                    )
                else:
                    cursor.execute(
                        "SELECT id, user_id, date, mood, note, created_at "
                        "FROM mood_entries WHERE user_id = %s ORDER BY date DESC",
                        (current_user.id,)
                    )
                entries = cursor.fetchall()
            for entry in entries:
                if entry.get('mood') is not None:
                    entry['mood'] = float(entry['mood'])
                if entry.get('date'):
                    entry['date'] = entry['date'].isoformat()
                if entry.get('created_at'):
                    entry['created_at'] = entry['created_at'].isoformat()
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
            with conn.cursor(buffered=True) as cursor:
                cursor.execute(
                    """INSERT INTO mood_entries (user_id, date, mood, note)
                    VALUES (%s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE mood = VALUES(mood), note = VALUES(note)""",
                    (current_user.id, date, float(mood), note)
                )
                conn.commit()
                cursor.execute("SELECT LAST_INSERT_ID() as id")
                result = cursor.fetchone()
                new_id = result[0] if result else None
            return jsonify({'message': 'Настроение сохранено успешно', 'id': new_id})
        except Error as e:
            print(f"Database error in mood_entries POST: {e}")
            return jsonify({'error': str(e)}), 500

@main.route('/api/mood_entries/<int:mood_id>', methods=['DELETE'])
@login_required
@with_db_connection
def delete_mood_entry(conn, mood_id):
    try:
        cursor = conn.cursor(buffered=True)
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

@main.route('/api/check-auth')
@login_required
def check_auth():
    return jsonify({
        'authenticated': True,
        'user_id': current_user.id,
        'username': current_user.username
    })

# ================== HOURLY MOODS ==================
@main.route('/api/hourly_moods', methods=['GET', 'POST'])
@login_required
@with_db_connection
def hourly_moods(conn):
    if request.method == 'GET':
        try:
            date_filter = request.args.get('date')
            if not date_filter:
                return jsonify({'error': 'Date parameter is required'}), 400
            cursor = conn.cursor(buffered=True, dictionary=True)
            cursor.execute(
                "SELECT id, user_id, date, hour, mood, note FROM hourly_moods WHERE user_id = %s AND date = %s ORDER BY hour",
                (current_user.id, date_filter)
            )
            entries = cursor.fetchall()
            for entry in entries:
                if 'date' in entry and entry['date']:
                    entry['date'] = entry['date'].isoformat()
            cursor.close()
            return jsonify(entries)
        except Error as e:
            print(f"Database error in hourly_moods GET: {e}")
            return jsonify({'error': str(e)}), 500
    elif request.method == 'POST':
        try:
            data = request.get_json()
            if not data:
                return jsonify({'error': 'No data provided'}), 400
            date = data.get('date')
            hour = data.get('hour')
            mood = data.get('mood')
            note = data.get('note', '')
            if date is None or hour is None or mood is None:
                return jsonify({'error': 'Date, hour and mood are required'}), 400
            cursor = conn.cursor(buffered=True)
            cursor.execute(
                """INSERT INTO hourly_moods (user_id, date, hour, mood, note)
                VALUES (%s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE mood = VALUES(mood), note = VALUES(note)""",
                (current_user.id, date, int(hour), int(mood), note)
            )
            conn.commit()
            cursor.close()
            return jsonify({'message': 'Почасовое настроение сохранено успешно'})
        except Error as e:
            print(f"Database error in hourly_moods POST: {e}")
            return jsonify({'error': str(e)}), 500

@main.route('/api/hourly_moods/<int:mood_id>', methods=['DELETE'])
@login_required
@with_db_connection
def delete_hourly_mood(conn, mood_id):
    try:
        cursor = conn.cursor(buffered=True)
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
        with conn.cursor(dictionary=True) as cursor:
            cursor.execute(
                "SELECT mood, note FROM mood_entries WHERE user_id = %s AND date = %s",
                (current_user.id, today)
            )
            mood_entry = cursor.fetchone()
        if mood_entry:
            mood_value = mood_entry.get('mood')
            return jsonify({
                'mood': float(mood_value) if mood_value is not None else None,
                'note': mood_entry.get('note', '')
            })
        else:
            return jsonify({'mood': None, 'note': ''})
    except Error as e:
        print(f"Database error in today_mood: {e}")
        return jsonify({'error': str(e)}), 500
    except Exception as e:
        print(f"Unexpected error in today_mood: {e}")
        return jsonify({'error': str(e)}), 500

# ================== API МАРШРУТЫ ДЛЯ ПРОФИЛЯ ==================

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
        gender = data.get('gender')
        if gender not in (None, '', 'male', 'female'):
            return jsonify({'error': 'Некорректное значение пола'}), 400
        cursor = conn.cursor(buffered=True)
        cursor.execute(
            """
            UPDATE users
            SET first_name = %s,
                last_name = %s,
                gender = %s
            WHERE id = %s
            """,
            (first_name, last_name, gender, current_user.id)
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
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT password FROM users WHERE id = %s", (current_user.id,))
        user_data = cursor.fetchone()
        if not user_data:
            return jsonify({'error': 'Пользователь не найден'}), 404
        from app import bcrypt
        if not bcrypt.check_password_hash(user_data['password'], current_password):
            return jsonify({'error': 'Текущий пароль неверен'}), 400
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

# ================== API МАРШРУТЫ ДЛЯ ЦЕЛЕЙ ==================

@main.route('/api/goals', methods=['GET', 'POST'])
@login_required
@with_db_connection
def goals(conn):
    if request.method == 'GET':
        cursor = None
        try:
            cursor = conn.cursor(dictionary=True, buffered=True)
            date = request.args.get('date')
            if date:
                cursor.execute(
                    "SELECT id, user_id, text, completed, created_at FROM goals WHERE user_id = %s AND date = %s ORDER BY created_at DESC",
                    (current_user.id, date)
                )
            else:
                cursor.execute(
                    "SELECT id, user_id, text, completed, created_at FROM goals WHERE user_id = %s ORDER BY created_at DESC",
                    (current_user.id,)
                )
            goals_data = cursor.fetchall()
            for goal in goals_data:
                if 'created_at' in goal and goal['created_at']:
                    goal['created_at'] = goal['created_at'].isoformat()
            return jsonify(goals_data)
        except Error as e:
            print(f"Database error in goals GET: {e}")
            return jsonify({'error': str(e)}), 500
        finally:
            if cursor:
                cursor.close()
    elif request.method == 'POST':
        cursor = None
        try:
            data = request.get_json()
            if not data:
                return jsonify({'error': 'No data provided'}), 400
            text = data.get('text', '').strip()
            if not text:
                return jsonify({'error': 'Текст цели не может быть пустым'}), 400
            date = data.get('date')
            cursor = conn.cursor(buffered=True)
            cursor.execute(
                "INSERT INTO goals (user_id, text, date) VALUES (%s, %s, %s)",
                (current_user.id, text, date)
            )
            conn.commit()
            goal_id = cursor.lastrowid
            return jsonify({
                'id': goal_id,
                'text': text,
                'completed': False,
                'user_id': current_user.id
            })
        except Error as e:
            print(f"Database error in goals POST: {e}")
            return jsonify({'error': str(e)}), 500
        finally:
            if cursor:
                cursor.close()

@main.route("/api/goals/<int:goal_id>", methods=["PUT"])
@login_required
@with_db_connection
def update_goal_status(conn, goal_id):
    cursor = None
    try:
        data = request.get_json()
        completed = data.get("completed")
        cursor = conn.cursor(buffered=True)
        cursor.execute(
            "UPDATE goals SET completed=%s WHERE id=%s AND user_id=%s",
            (completed, goal_id, current_user.id)
        )
        conn.commit()
        return jsonify({"success": True})
    except Error as e:
        print(f"Database error in update_goal_status: {e}")
        return jsonify({'error': str(e)}), 500
    finally:
        if cursor:
            cursor.close()

@main.route('/api/goals/<int:goal_id>', methods=['PATCH'])
@login_required
@with_db_connection
def patch_goal(conn, goal_id):
    """Обновление цели через PATCH (для отметки выполнения)"""
    data = request.get_json()
    if not data or 'completed' not in data:
        return jsonify({'error': 'Invalid data'}), 400
    completed = 1 if data['completed'] else 0
    cursor = conn.cursor(buffered=True)
    try:
        cursor.execute(
            "UPDATE goals SET completed = %s WHERE id = %s AND user_id = %s",
            (completed, goal_id, current_user.id)
        )
        conn.commit()
        return jsonify({'success': True})
    except Error as e:
        print(f"Database error in patch_goal: {e}")
        return jsonify({'error': str(e)}), 500
    finally:
        cursor.close()

@main.route('/api/goals/<int:goal_id>/toggle', methods=['POST'])
@login_required
@with_db_connection
def toggle_goal(conn, goal_id):
    cursor = None
    try:
        cursor = conn.cursor(buffered=True)
        cursor.execute(
            "UPDATE goals SET completed = NOT completed WHERE id = %s AND user_id = %s",
            (goal_id, current_user.id)
        )
        conn.commit()
        return jsonify({'success': True})
    except Error as e:
        print(f"Database error in toggle_goal: {e}")
        return jsonify({'error': str(e)}), 500
    finally:
        if cursor:
            cursor.close()

@main.route('/api/goals/<int:goal_id>', methods=['DELETE'])
@login_required
@with_db_connection
def delete_goal(conn, goal_id):
    cursor = None
    try:
        cursor = conn.cursor(buffered=True)
        cursor.execute(
            "DELETE FROM goals WHERE id = %s AND user_id = %s",
            (goal_id, current_user.id)
        )
        conn.commit()
        return jsonify({'success': True})
    except Error as e:
        print(f"Database error in delete_goal: {e}")
        return jsonify({'error': str(e)}), 500
    finally:
        if cursor:
            cursor.close()

# ================== API МАРШРУТЫ ДЛЯ РАДОСТЕЙ ==================
@main.route('/api/joys', methods=['GET', 'POST'])
@login_required
@with_db_connection
def joys(conn):
    if request.method == 'GET':
        cursor = None
        try:
            cursor = conn.cursor(dictionary=True, buffered=True)
            date = request.args.get('date')
            if date:
                cursor.execute(
                    "SELECT id, user_id, text, created_at FROM joys WHERE user_id = %s AND date = %s ORDER BY created_at DESC",
                    (current_user.id, date)
                )
            else:
                cursor.execute(
                    "SELECT id, user_id, text, created_at FROM joys WHERE user_id = %s ORDER BY created_at DESC",
                    (current_user.id,)
                )
            joys_data = cursor.fetchall()
            for joy in joys_data:
                if joy.get('created_at'):
                    joy['created_at'] = joy['created_at'].isoformat()
            return jsonify(joys_data)
        except Error as e:
            print(f"Database error in joys GET: {e}")
            return jsonify({'error': str(e)}), 500
        finally:
            if cursor:
                cursor.close()
    elif request.method == 'POST':
        cursor = None
        try:
            data = request.get_json()
            if not data:
                return jsonify({'error': 'No data provided'}), 400
            text = data.get('text', '').strip()
            if not text:
                return jsonify({'error': 'Текст не может быть пустым'}), 400
            date = data.get('date')
            cursor = conn.cursor(buffered=True)
            cursor.execute(
                "INSERT INTO joys (user_id, text, date) VALUES (%s, %s, %s)",
                (current_user.id, text, date)
            )
            conn.commit()
            joy_id = cursor.lastrowid
            return jsonify({
                'id': joy_id,
                'text': text,
                'user_id': current_user.id
            })
        except Error as e:
            print(f"Database error in joys POST: {e}")
            return jsonify({'error': str(e)}), 500
        finally:
            if cursor:
                cursor.close()

@main.route('/api/joys/<int:joy_id>', methods=['DELETE'])
@login_required
@with_db_connection
def delete_joy(conn, joy_id):
    cursor = None
    try:
        cursor = conn.cursor(buffered=True)
        cursor.execute(
            "DELETE FROM joys WHERE id = %s AND user_id = %s",
            (joy_id, current_user.id)
        )
        conn.commit()
        return jsonify({'success': True})
    except Error as e:
        print(f"Database error in delete_joy: {e}")
        return jsonify({'error': str(e)}), 500
    finally:
        if cursor:
            cursor.close()

# ================== API ДЛЯ ЗАГРУЗКИ АВАТАРА ==================
@main.route('/api/upload_avatar', methods=['POST'])
@login_required
@with_db_connection
def upload_avatar(conn):
    try:
        if 'avatar' not in request.files:
            return jsonify({'error': 'Файл не выбран'}), 400
        file = request.files['avatar']
        if file.filename == '':
            return jsonify({'error': 'Файл не выбран'}), 400
        if not file.content_type.startswith('image/'):
            return jsonify({'error': 'Файл должен быть изображением'}), 400
        avatars_dir = os.path.join(current_app.root_path, 'static', 'avatars')
        os.makedirs(avatars_dir, exist_ok=True)
        import time
        filename = f"avatar_{current_user.id}_{int(time.time())}.jpg"
        filepath = os.path.join(avatars_dir, filename)
        file.save(filepath)
        cursor = None
        try:
            cursor = conn.cursor(buffered=True)
            avatar_path = f"avatars/{filename}"
            cursor.execute(
                "UPDATE users SET avatar_path = %s WHERE id = %s",
                (avatar_path, current_user.id)
            )
            conn.commit()
            return jsonify({'message': 'Аватар успешно загружен', 'path': avatar_path})
        except Error as e:
            print(f"Database error in upload_avatar: {e}")
            return jsonify({'error': str(e)}), 500
        finally:
            if cursor:
                cursor.close()
    except Exception as e:
        print(f"Error in upload_avatar: {e}")
        return jsonify({'error': 'Ошибка загрузки файла'}), 500

# ================== ЭКСПОРТ В CSV ==================
@main.route('/api/export/data')
@login_required
@with_db_connection
def export_data(conn):
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute("""
            SELECT date, mood, note, created_at 
            FROM mood_entries 
            WHERE user_id = %s 
            ORDER BY date
        """, (current_user.id,))
        mood_data = cursor.fetchall()
        cursor.execute("""
            SELECT text, completed, created_at 
            FROM goals 
            WHERE user_id = %s 
            ORDER BY created_at
        """, (current_user.id,))
        goals_data = cursor.fetchall()
        cursor.execute("""
            SELECT text, created_at 
            FROM joys 
            WHERE user_id = %s 
            ORDER BY created_at
        """, (current_user.id,))
        joys_data = cursor.fetchall()
        cursor.close()
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(['Lumi - Экспорт данных'])
        writer.writerow(['Пользователь:', f"{current_user.first_name or ''} {current_user.last_name or ''}".strip()])
        writer.writerow(['Логин:', current_user.username])
        writer.writerow(['Дата экспорта:', datetime.now().strftime('%Y-%m-%d %H:%M:%S')])
        writer.writerow([])
        writer.writerow(['=== НАСТРОЕНИЕ ==='])
        writer.writerow(['Дата', 'Настроение (1-10)', 'Заметка', 'Дата создания'])
        for entry in mood_data:
            date = entry['date'].strftime('%Y-%m-%d') if entry['date'] else ''
            created_at = entry['created_at'].strftime('%Y-%m-%d %H:%M') if entry['created_at'] else ''
            writer.writerow([date, float(entry['mood']) if entry['mood'] else '', entry.get('note', ''), created_at])
        writer.writerow([])
        writer.writerow(['=== ЦЕЛИ ==='])
        writer.writerow(['Текст цели', 'Статус', 'Дата создания'])
        for goal in goals_data:
            status = 'Выполнено' if goal['completed'] else 'Не выполнено'
            created_at = goal['created_at'].strftime('%Y-%m-%d %H:%M') if goal['created_at'] else ''
            writer.writerow([goal['text'], status, created_at])
        writer.writerow([])
        writer.writerow(['=== РАДОСТИ ==='])
        writer.writerow(['Текст', 'Дата создания'])
        for joy in joys_data:
            created_at = joy['created_at'].strftime('%Y-%m-%d %H:%M') if joy['created_at'] else ''
            writer.writerow([joy['text'], created_at])
        output.seek(0)
        response = current_app.response_class(
            output,
            mimetype='text/csv',
            headers={'Content-Disposition': f'attachment; filename=lumi_export_{datetime.now().strftime("%Y%m%d_%H%M")}.csv'}
        )
        return response
    except Error as e:
        print(f"Database error in export_data: {e}")
        return jsonify({'error': str(e)}), 500

@main.route('/api/delete_avatar', methods=['DELETE'])
@login_required
@with_db_connection
def delete_avatar(conn):
    cursor = None
    try:
        cursor = conn.cursor(buffered=True)
        cursor.execute(
            "UPDATE users SET avatar_path = NULL WHERE id = %s",
            (current_user.id,)
        )
        conn.commit()
        return jsonify({'message': 'Аватар успешно удален'})
    except Error as e:
        print(f"Database error in delete_avatar: {e}")
        return jsonify({'error': str(e)}), 500
    finally:
        if cursor:
            cursor.close()

# ================== API МАРШРУТЫ ДЛЯ МЕНСТРУАЛЬНОГО ЦИКЛА ==================
@main.route('/api/cycle_entries', methods=['GET', 'POST'])
@login_required
@with_db_connection
def cycle_entries(conn):
    if request.method == 'GET':
        try:
            date_filter = request.args.get('date')
            query = """
                SELECT id, user_id, date, cycle_day, symptoms, flow_intensity, mood, notes
                FROM cycle_entries
                WHERE user_id = %s
            """
            params = [current_user.id]
            if date_filter:
                query += " AND date = %s"
                params.append(date_filter)
            else:
                query += " ORDER BY date DESC"
            with conn.cursor(dictionary=True) as cursor:
                cursor.execute(query, params)
                entries = cursor.fetchall()
            for entry in entries:
                entry['symptoms'] = json.loads(entry['symptoms']) if entry.get('symptoms') else []
                if entry.get('date'):
                    entry['date'] = entry['date'].isoformat()
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
            if not date:
                return jsonify({'error': 'Date is required'}), 400
            cycle_day = data.get('cycle_day')
            symptoms_json = json.dumps(data.get('symptoms', [])) if data.get('symptoms') else None
            flow_intensity = data.get('flow_intensity')
            mood = data.get('mood')
            notes = data.get('notes', '')
            with conn.cursor() as cursor:
                cursor.execute("""
                    INSERT INTO cycle_entries
                    (user_id, date, cycle_day, symptoms, flow_intensity, mood, notes)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                        cycle_day = VALUES(cycle_day),
                        symptoms = VALUES(symptoms),
                        flow_intensity = VALUES(flow_intensity),
                        mood = VALUES(mood),
                        notes = VALUES(notes)
                """, (current_user.id, date, cycle_day, symptoms_json, flow_intensity, mood, notes))
                conn.commit()
            return jsonify({'message': 'Данные цикла сохранены успешно'})
        except Error as e:
            print(f"Database error in cycle_entries POST: {e}")
            return jsonify({'error': str(e)}), 500

@main.route('/api/cycle_entries/<date>', methods=['DELETE'])
@login_required
@with_db_connection
def delete_cycle_entry(conn, date):
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "DELETE FROM cycle_entries WHERE date = %s AND user_id = %s",
                (date, current_user.id)
            )
            deleted = cursor.rowcount
            conn.commit()
        return jsonify({'success': True, 'deleted': deleted})
    except Error as e:
        print(f"Database error in delete_cycle_entry: {e}")
        return jsonify({'error': str(e)}), 500

@main.route('/api/cycle_settings', methods=['GET', 'PUT'])
@login_required
@with_db_connection
def cycle_settings(conn):
    if request.method == 'GET':
        try:
            with conn.cursor(dictionary=True) as cursor:
                cursor.execute("SELECT * FROM cycle_settings WHERE user_id = %s", (current_user.id,))
                settings = cursor.fetchone()
            if settings and settings.get('last_period_start'):
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
            with conn.cursor() as cursor:
                cursor.execute("SELECT id FROM cycle_settings WHERE user_id = %s", (current_user.id,))
                existing = cursor.fetchone()
                if existing:
                    cursor.execute("""
                        UPDATE cycle_settings SET
                            cycle_length = %s,
                            period_length = %s,
                            last_period_start = %s,
                            notify_before_period = %s,
                            notify_ovulation = %s
                        WHERE user_id = %s
                    """, (
                        data.get('cycle_length'),
                        data.get('period_length'),
                        data.get('last_period_start'),
                        data.get('notify_before_period'),
                        data.get('notify_ovulation'),
                        current_user.id
                    ))
                else:
                    cursor.execute("""
                        INSERT INTO cycle_settings
                        (user_id, cycle_length, period_length, last_period_start, notify_before_period, notify_ovulation)
                        VALUES (%s, %s, %s, %s, %s, %s)
                    """, (
                        current_user.id,
                        data.get('cycle_length', 28),
                        data.get('period_length', 5),
                        data.get('last_period_start'),
                        data.get('notify_before_period', True),
                        data.get('notify_ovulation', True)
                    ))
                conn.commit()
            return jsonify({'message': 'Настройки цикла обновлены'})
        except Error as e:
            print(f"Database error in cycle_settings PUT: {e}")
            return jsonify({'error': str(e)}), 500

# ================== CYCLE STATS ==================
@main.route('/api/cycle_stats')
@login_required
@with_db_connection
def cycle_stats(conn):
    try:
        with conn.cursor(dictionary=True) as cursor:
            cursor.execute("""
                SELECT 
                    COUNT(*) as total_entries,
                    AVG(mood) as avg_mood,
                    COUNT(CASE WHEN flow_intensity IN ('medium', 'heavy') THEN 1 END) as period_days
                FROM cycle_entries
                WHERE user_id = %s
            """, (current_user.id,))
            stats = cursor.fetchone() or {}
        return jsonify({
            'total_entries': stats.get('total_entries', 0),
            'avg_mood': round(float(stats.get('avg_mood') or 0), 1),
            'period_days': stats.get('period_days', 0)
        })
    except Error as e:
        print(f"Database error in cycle_stats: {e}")
        return jsonify({'error': str(e)}), 500

# ================== CYCLE PREDICTIONS ==================
@main.route('/api/cycle_predictions')
@login_required
@with_db_connection
def cycle_predictions(conn):
    try:
        cursor = None
        try:
            cursor = conn.cursor(dictionary=True)
            cursor.execute("SELECT * FROM cycle_settings WHERE user_id = %s", (current_user.id,))
            settings = cursor.fetchone()
        except Error as e:
            print(f"Database error in cycle_predictions: {e}")
            return jsonify({'error': str(e)}), 500
        finally:
            if cursor:
                cursor.close()
        if not settings:
            return jsonify({'error': 'Настройки цикла не найдены'}), 400
        if not settings.get('last_period_start'):
            return jsonify({'error': 'Дата последней менструации не указана'}), 400
        last_period = settings['last_period_start']
        if isinstance(last_period, str):
            try:
                last_period = datetime.strptime(last_period, '%Y-%m-%d').date()
            except ValueError:
                return jsonify({'error': 'Некорректная дата последней менструации'}), 400
        cycle_length = settings.get('cycle_length') or 28
        period_length = settings.get('period_length') or 5
        next_period = last_period + timedelta(days=cycle_length)
        ovulation_date = next_period - timedelta(days=14)
        fertile_start = ovulation_date - timedelta(days=5)
        fertile_end = ovulation_date + timedelta(days=1)
        current_cycle_day = (datetime.now().date() - last_period).days + 1
        return jsonify({
            'next_period': next_period.isoformat(),
            'ovulation_date': ovulation_date.isoformat(),
            'fertile_window': {'start': fertile_start.isoformat(), 'end': fertile_end.isoformat()},
            'current_cycle_day': current_cycle_day
        })
    except Error as e:
        print(f"Database error in cycle_predictions: {e}")
        return jsonify({'error': str(e)}), 500
    except Exception as e:
        print(f"Unexpected error in cycle_predictions: {e}")
        return jsonify({'error': str(e)}), 500

# ================== УМНЫЙ ЧАТ-БОТ С ИНТЕГРИРОВАННЫМ АНАЛИЗОМ ==================

@main.route('/api/chat', methods=['POST'])
@login_required
def chat_with_asya():
    """Чат-бот 'Ася' с использованием YandexGPT API и умным анализом данных"""
    try:
        data = request.get_json()
        user_message = data.get('message', '').strip()
        history = data.get('history', [])
        
        if not user_message:
            return jsonify({
                'reply': 'Привет! Я Ася, твой помощник в трекере настроения Lumi. Чем могу помочь? 😊',
                'success': True
            })
        
        # ===== ПРОВЕРКА КОМАНД ДЛЯ РАСШИРЕННОГО АНАЛИЗА =====
        user_message_lower = user_message.lower()
        
        # Анализ данных
        analysis_commands = [
            'проанализируй', 'анализ данных', 'статистика', 'отчет', 
            'анализируй мои данные', 'покажи статистику', 'дай отчет',
            'как у меня дела', 'расскажи о моем настроении', 'обзор данных',
            'проанализировать', 'отчёт', 'статистику', 'анализ', 'аналитику',
            'что с моим настроением', 'как я себя чувствую по данным'
        ]
        if any(cmd in user_message_lower for cmd in analysis_commands):
            print(f"🔍 Пользователь запросил анализ: {user_message}")
            return generate_deep_analysis(current_user.id)
        
        # Паттерны
        pattern_commands = [
            'паттерны', 'закономерности', 'тренды', 'график', 
            'какие дни', 'в какое время', 'когда у меня',
            'дни недели', 'по дням', 'по времени', 'закономерность',
            'какой день', 'во сколько'
        ]
        if any(cmd in user_message_lower for cmd in pattern_commands):
            print(f"📊 Пользователь запросил паттерны: {user_message}")
            return analyze_patterns(current_user.id, user_message)
        
        # Заметки
        notes_commands = ['заметки', 'мои записи', 'что я писал', 'дневник', 'заметок', 'записи', 'текст']
        if any(cmd in user_message_lower for cmd in notes_commands):
            print(f"📝 Пользователь запросил заметки: {user_message}")
            return analyze_notes(current_user.id, user_message)
        
        # Радости
        joys_commands = ['радости', 'радость', 'joys', 'что меня радует', 'мои радости', 'копилка радостей']
        if any(cmd in user_message_lower for cmd in joys_commands):
            print(f"✨ Пользователь запросил радости: {user_message}")
            return analyze_joys(current_user.id)
        
        # Цели
        if any(w in user_message_lower for w in ['цели', 'задачи', 'планы', 'прогресс']):
            print(f"🎯 Пользователь запросил цели: {user_message}")
            return analyze_goals(current_user.id)
        
        # Цикл
        cycle_commands = ['цикл', 'менструация', 'месячные', 'овуляция', 'пмс', 'фаза цикла', 'дневник цикла']
        if any(cmd in user_message_lower for cmd in cycle_commands):
            print(f"🔄 Пользователь запросил анализ цикла: {user_message}")
            return analyze_cycle(current_user.id)
        
        # ===== ОБЫЧНЫЙ ДИАЛОГ С ПАМЯТЬЮ =====
        conn = get_db()
        if conn is None:
            return jsonify({
                'reply': 'Извини, возникла проблема с базой данных. Попробуй позже! 🔄',
                'success': False,
                'has_analysis': False
            })
        
        # Получаем контекст для ответа
        context = ""
        try:
            cursor = conn.cursor(dictionary=True)
            cursor.execute("SELECT AVG(mood) as avg_mood, COUNT(*) as total FROM mood_entries WHERE user_id = %s AND date >= DATE_SUB(CURDATE(), INTERVAL 7 DAY)", (current_user.id,))
            mood_stats = cursor.fetchone()
            if mood_stats and mood_stats['total'] and mood_stats['total'] > 0:
                avg_mood = float(mood_stats['avg_mood'] or 0)
                if avg_mood >= 7:
                    context = "У тебя отличное настроение в последнее время! 🌟"
                elif avg_mood >= 5:
                    context = "Настроение стабильное! 💪"
                else:
                    context = "Я здесь, чтобы поддержать! 🤗"
            cursor.execute("SELECT COUNT(*) as count FROM joys WHERE user_id = %s", (current_user.id,))
            joys_count = cursor.fetchone()['count']
            if joys_count > 0:
                context += f" (и кстати, у тебя уже {joys_count} радостей в копилке! 🎉)"
        except Exception as e:
            print(f"⚠️ Ошибка получения контекста: {e}")
        finally:
            close_db(conn)
        
        # Получаем API ключи для YandexGPT
        api_key = os.environ.get('YANDEX_API_KEY')
        folder_id = os.environ.get('YANDEX_FOLDER_ID')
        
        # Если нет API ключей — используем умный ответ без YandexGPT
        if not api_key or not folder_id:
            smart_reply = generate_smart_response(user_message, context, history)
            return jsonify({'reply': smart_reply, 'success': True, 'has_analysis': False})
        
        # Создаем промпт для психологического помощника
        prompt = f"""Ты - Ася, виртуальный помощник в приложении для отслеживания настроения и ментального здоровья "Lumi".

Твоя роль:
1. Эмпатичный, поддерживающий психологический помощник
2. Говори на "ты" в дружеском, теплом тоне
3. Будь краткой (1-3 предложения), но содержательной
4. Используй эмодзи для эмоциональной поддержки (максимум 1-2 эмодзи)
5. Избегай клинических диагнозов, давай общие рекомендации
6. В сложных ситуациях рекомендуй обратиться к специалисту

ВАЖНЫЕ ПРАВИЛА:
1. НЕ добавляй статистику, анализ данных или цифры в ответ
2. НЕ говори о среднем настроении, баллах или трендах
3. Отвечай только на конкретный запрос пользователя
4. Будь дружелюбной и поддерживающей

Запрос пользователя: "{user_message}"

Твой ответ (максимум 2 предложения, дружеский тон):"""
        
        # Отправляем запрос в YandexGPT API
        headers = {'Authorization': f'Api-Key {api_key}', 'Content-Type': 'application/json'}
        payload = {
            "modelUri": f"gpt://{folder_id}/yandexgpt-lite",
            "completionOptions": {"stream": False, "temperature": 0.7, "maxTokens": 200},
            "messages": [{"role": "user", "text": prompt}]
        }
        
        response = requests.post(
            'https://llm.api.cloud.yandex.net/foundationModels/v1/completion',
            headers=headers,
            json=payload,
            timeout=15
        )
        
        if response.status_code == 200:
            data = response.json()
            reply = data['result']['alternatives'][0]['message']['text'].strip()
            return jsonify({'reply': reply, 'success': True, 'has_analysis': False})
        else:
            current_app.logger.error(f"YandexGPT API error: {response.status_code}")
            fallback_response = get_fallback_response(user_message)
            return jsonify({'reply': fallback_response, 'success': True, 'has_analysis': False})
            
    except Exception as e:
        current_app.logger.error(f"Chat error: {str(e)}")
        fallback_response = get_fallback_response(user_message)
        return jsonify({'reply': fallback_response, 'success': False, 'has_analysis': False})

# ================== ФУНКЦИИ РАСШИРЕННОГО АНАЛИЗА ==================

def generate_deep_analysis(user_id):
    """Генерация расширенного анализа данных пользователя"""
    try:
        conn = get_db()
        if conn is None:
            return jsonify({'error': 'Ошибка подключения к БД'}), 500
        
        try:
            cursor = conn.cursor(dictionary=True)
            
            # 1. Полная статистика настроения
            cursor.execute("""
                SELECT 
                    COUNT(*) as total,
                    AVG(mood) as avg_mood,
                    MIN(mood) as min_mood,
                    MAX(mood) as max_mood,
                    COUNT(CASE WHEN mood >= 7 THEN 1 END) as good_days,
                    COUNT(CASE WHEN mood <= 4 THEN 1 END) as bad_days,
                    DATEDIFF(MAX(date), MIN(date)) as tracking_days
                FROM mood_entries 
                WHERE user_id = %s
            """, (user_id,))
            mood_stats = cursor.fetchone()
            
            # 2. Последние записи с заметками
            cursor.execute("""
                SELECT date, mood, note 
                FROM mood_entries 
                WHERE user_id = %s 
                AND note IS NOT NULL 
                AND note != ''
                ORDER BY date DESC 
                LIMIT 10
            """, (user_id,))
            notes_with_text = cursor.fetchall()
            
            # 3. Самые позитивные заметки (mood >= 8)
            cursor.execute("""
                SELECT date, mood, note 
                FROM mood_entries 
                WHERE user_id = %s 
                AND mood >= 8
                AND note IS NOT NULL 
                AND note != ''
                ORDER BY mood DESC 
                LIMIT 5
            """, (user_id,))
            positive_notes = cursor.fetchall()
            
            # 4. Самые сложные дни с заметками (mood <= 4)
            cursor.execute("""
                SELECT date, mood, note 
                FROM mood_entries 
                WHERE user_id = %s 
                AND mood <= 4
                AND note IS NOT NULL 
                AND note != ''
                ORDER BY mood ASC 
                LIMIT 5
            """, (user_id,))
            challenging_notes = cursor.fetchall()
            
            # 5. По дням недели
            cursor.execute("""
                SELECT 
                    DAYNAME(date) as day_name,
                    AVG(mood) as avg_mood,
                    COUNT(*) as count
                FROM mood_entries 
                WHERE user_id = %s
                GROUP BY DAYNAME(date)
                ORDER BY FIELD(day_name, 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday')
            """, (user_id,))
            days_stats = cursor.fetchall()
            
            # Статистика радостей
            cursor.execute("""
                SELECT 
                    COUNT(*) as joys_count,
                    GROUP_CONCAT(text ORDER BY created_at DESC SEPARATOR '|||') as recent_joys_text
                FROM joys
                WHERE user_id = %s
            """, (user_id,))
            joys_stats = cursor.fetchone()
            
            recent_joys_list = []
            if joys_stats and joys_stats['recent_joys_text']:
                recent_joys_list = joys_stats['recent_joys_text'].split('|||')[:3]
            
            cursor.close()
            
        finally:
            if cursor:
                cursor.close()
            close_db(conn)
        
        # Формируем данные для промпта
        analysis_data = {
            "mood_stats": mood_stats,
            "recent_notes": notes_with_text,
            "positive_notes": positive_notes,
            "challenging_notes": challenging_notes,
            "days_stats": days_stats,
            "joys_stats": joys_stats,
            "recent_joys_list": recent_joys_list
        }
        
        # Получаем ключи API
        api_key = os.environ.get('YANDEX_API_KEY')
        folder_id = os.environ.get('YANDEX_FOLDER_ID')
        
        if not api_key or not folder_id:
            # Локальный ответ если нет API
            joys_block = ""
            if joys_stats and joys_stats['joys_count'] > 0:
                joys_block = f"""
😊 ТВОИ РАДОСТИ:
• Всего радостей: {joys_stats['joys_count']}
{chr(10).join([f"• {joy}" for joy in recent_joys_list[:3]]) if recent_joys_list else ''}
"""
            
            reply = f"""📊 Анализ ваших данных:

🎯 ОСНОВНЫЕ ПОКАЗАТЕЛИ:
• Всего записей: {mood_stats['total'] or 0}
• Среднее настроение: {float(mood_stats['avg_mood'] or 0):.1f}/10
• Лучший день: {mood_stats['max_mood'] or 0}/10
• Сложный день: {mood_stats['min_mood'] or 0}/10
• Хороших дней: {mood_stats['good_days'] or 0}
• Сложных дней: {mood_stats['bad_days'] or 0}

📝 ПОСЛЕДНИЕ ЗАМЕТКИ ({len(notes_with_text)}):
{chr(10).join([f"• {note['date']}: {note['mood']}/10 - {note['note'][:50]}..." for note in notes_with_text]) if notes_with_text else 'Нет заметок'}

😊 САМЫЕ ПОЗИТИВНЫЕ ДНИ:
{chr(10).join([f"• {note['date']}: {note['mood']}/10 - {note['note'][:40]}..." for note in positive_notes]) if positive_notes else 'Нет записей'}

💪 ПРЕОДОЛЕННЫЕ СЛОЖНОСТИ:
{chr(10).join([f"• {note['date']}: {note['mood']}/10 - {note['note'][:40]}..." for note in challenging_notes]) if challenging_notes else 'Нет записей'}

📅 ДНИ НЕДЕЛИ:
{chr(10).join([f"• {day['day_name']}: {float(day['avg_mood'] or 0):.1f}/10" for day in days_stats]) if days_stats else 'Недостаточно данных'}
{joys_block}
💡 Продолжай отслеживать настроение!"""
            
            return jsonify({
                'reply': reply,
                'success': True,
                'analysis_type': 'deep_analysis'
            })
        
        # Создаем промпт для YandexGPT с радостями
        joys_text = f"""
6. СТАТИСТИКА РАДОСТЕЙ:
- Всего радостей: {joys_stats['joys_count'] if joys_stats else 0}
- Последние радости: {', '.join(recent_joys_list) if recent_joys_list else 'нет записей'}
"""
        
        prompt = f"""
ПРОАНАЛИЗИРУЙ ДАННЫЕ ПОЛЬЗОВАТЕЛЯ И ДАЙ РАЗВЁРНУТЫЙ ОТВЕТ:

ДАННЫЕ ПОЛЬЗОВАТЕЛЯ:

1. СТАТИСТИКА НАСТРОЕНИЯ:
- Всего записей: {mood_stats['total'] or 0}
- Среднее настроение: {float(mood_stats['avg_mood'] or 0):.1f}/10
- Лучший день: {mood_stats['max_mood'] or 0}/10
- Сложный день: {mood_stats['min_mood'] or 0}/10
- Хороших дней (>7/10): {mood_stats['good_days'] or 0}
- Сложных дней (<4/10): {mood_stats['bad_days'] or 0}
- Отслеживает дней: {mood_stats['tracking_days'] or 0}

2. ПОСЛЕДНИЕ ЗАМЕТКИ ({len(notes_with_text)} записей):
{chr(10).join([f"- {note['date']}: {note['mood']}/10 - {note.get('note', 'без заметки')[:60]}..." for note in notes_with_text]) if notes_with_text else 'Нет заметок'}

3. САМЫЕ ПОЗИТИВНЫЕ ДНИ (настроение 8+/10):
{chr(10).join([f"- {note['date']}: {note['mood']}/10 - {note.get('note', '')[:50]}..." for note in positive_notes]) if positive_notes else 'Нет очень позитивных дней'}

4. СЛОЖНЫЕ ДНИ С ЗАМЕТКАМИ (настроение 4-/10):
{chr(10).join([f"- {note['date']}: {note['mood']}/10 - {note.get('note', '')[:50]}..." for note in challenging_notes]) if challenging_notes else 'Нет сложных дней с заметками'}

5. НАСТРОЕНИЕ ПО ДНЯМ НЕДЕЛИ:
{chr(10).join([f"- {day['day_name']}: {float(day['avg_mood'] or 0):.1f}/10 ({day['count']} записей)" for day in days_stats]) if days_stats else 'Недостаточно данных'}
{joys_text}
ДАЙ АНАЛИЗ (4-5 предложений):
1. Оцени общее эмоциональное состояние на основе заметок
2. Отметь, о чём пользователь чаще пишет в заметках
3. Укажи на связь между настроением и содержанием заметок
4. Похвали за количество радостей и приведи пример одной из последних
5. Дай рекомендации на основе анализа заметок и радостей
6. Будь поддерживающим и мотивирующим

Используй эмодзи. Ориентируйся на содержание заметок.
"""
        
        headers = {'Authorization': f'Api-Key {api_key}', 'Content-Type': 'application/json'}
        payload = {
            "modelUri": f"gpt://{folder_id}/yandexgpt-lite",
            "completionOptions": {"stream": False, "temperature": 0.8, "maxTokens": 400},
            "messages": [{"role": "user", "text": prompt}]
        }
        
        response = requests.post(
            'https://llm.api.cloud.yandex.net/foundationModels/v1/completion',
            headers=headers,
            json=payload,
            timeout=20
        )
        
        if response.status_code == 200:
            data = response.json()
            reply = data['result']['alternatives'][0]['message']['text'].strip()
        else:
            joys_block = ""
            if joys_stats and joys_stats['joys_count'] > 0:
                joys_block = f"\n😊 Твои радости: {joys_stats['joys_count']} записей! 🎉"
            reply = f"""📊 Анализ ваших данных:

Среднее настроение: {float(mood_stats['avg_mood'] or 0):.1f}/10
Всего записей: {mood_stats['total'] or 0}
Заметок с текстом: {len(notes_with_text)}{joys_block}
Продолжай записывать заметки для более подробного анализа! 📝"""
        
        return jsonify({'reply': reply, 'success': True, 'analysis_type': 'deep_analysis'})
        
    except Exception as e:
        current_app.logger.error(f"Deep analysis error: {str(e)}")
        return jsonify({'reply': 'Извини, не могу проанализировать данные сейчас. Попробуй позже! 🔄', 'success': False})

def analyze_patterns(user_id, user_message):
    """Анализ паттернов настроения"""
    try:
        print(f"📊 АНАЛИЗ ПАТТЕРНОВ: user_id={user_id}")
        conn = get_db()
        if conn is None:
            return jsonify({'error': 'Ошибка подключения к БД'}), 500
        
        try:
            cursor = conn.cursor(dictionary=True)
            # Анализ по дням недели
            cursor.execute("""
                SELECT 
                    DAYNAME(date) as day_name,
                    AVG(mood) as avg_mood,
                    COUNT(*) as count
                FROM mood_entries 
                WHERE user_id = %s
                GROUP BY DAYNAME(date)
                ORDER BY avg_mood
            """, (user_id,))
            days_stats = cursor.fetchall()
            print(f"📅 Статистика по дням: {len(days_stats)} дней")
            
            # Анализ по времени суток (если есть таблица hourly_moods)
            hours_stats = []
            try:
                cursor.execute("""
                    SELECT 
                        hour,
                        AVG(mood) as avg_mood,
                        COUNT(*) as count
                    FROM hourly_moods 
                    WHERE user_id = %s
                    GROUP BY hour
                    ORDER BY hour
                """, (user_id,))
                hours_stats = cursor.fetchall()
                print(f"⏰ Статистика по часам: {len(hours_stats)} часов")
            except Exception as hour_error:
                print(f"ℹ️ Таблица hourly_moods не найдена или пуста: {hour_error}")
            
            # Статистика радостей
            cursor.execute("SELECT COUNT(*) as count FROM joys WHERE user_id = %s", (user_id,))
            joys_count_result = cursor.fetchone()
            joys_count = joys_count_result['count'] if joys_count_result else 0
            
            cursor.close()
        finally:
            if cursor:
                cursor.close()
            close_db(conn)
        
        api_key = os.environ.get('YANDEX_API_KEY')
        folder_id = os.environ.get('YANDEX_FOLDER_ID')
        
        if not api_key or not folder_id or not days_stats:
            if not days_stats:
                reply = "Пока недостаточно данных для анализа паттернов. Заполни календарь настроения! 📅"
            else:
                days_text = chr(10).join([f"• {day['day_name']}: {float(day['avg_mood'] or 0):.1f}/10" for day in days_stats])
                hours_text = chr(10).join([f"• {hour['hour']}:00: {float(hour['avg_mood'] or 0):.1f}/10" for hour in hours_stats]) if hours_stats else "Недостаточно данных по времени"
                joys_text = f"\n\n✨ Твоя копилка радостей: {joys_count} записей. Отличная работа!" if joys_count > 0 else ""
                reply = f"""📈 Паттерны настроения:

📅 ПО ДНЯМ НЕДЕЛИ (от худшего к лучшему):
{days_text}

⏰ ПО ВРЕМЕНИ СУТОК:
{hours_text}{joys_text}

💡 Используй эту информацию для планирования дня!"""
            return jsonify({'reply': reply, 'success': True, 'analysis_type': 'patterns'})
        
        print(f"🔗 Отправляем запрос в YandexGPT для анализа паттернов")
        prompt = f"""
ПРОАНАЛИЗИРУЙ ПАТТЕРНЫ НАСТРОЕНИЯ ПОЛЬЗОВАТЕЛЯ:

ВОПРОС ПОЛЬЗОВАТЕЛЯ: "{user_message}"

ДАННЫЕ:

1. НАСТРОЕНИЕ ПО ДНЯМ НЕДЕЛИ (от худшего к лучшему):
{chr(10).join([f"- {day['day_name']}: {float(day['avg_mood'] or 0):.1f}/10 ({day['count']} записей)" for day in days_stats])}

2. НАСТРОЕНИЕ ПО ВРЕМЕНИ СУТОК:
{chr(10).join([f"- {hour['hour']}:00: {float(hour['avg_mood'] or 0):.1f}/10 ({hour['count']} записей)" for hour in hours_stats]) if hours_stats else 'Недостаточно данных по времени суток'}

3. СТАТИСТИКА РАДОСТЕЙ:
- Всего радостей: {joys_count}

ПРОАНАЛИЗИРУЙ ЭТИ ДАННЫЕ:
1. В какие дни настроение обычно лучше/хуже?
2. В какое время суток пики и спады настроения (если есть данные)?
3. Отметь, что пользователь записал {joys_count} радостей — похвали за это
4. Какие практические рекомендации можешь дать на основе этих паттернов?
5. Как использовать эту информацию для улучшения самочувствия?

Ответ: 3-4 предложения, дружеский тон, с эмодзи.
"""
        headers = {'Authorization': f'Api-Key {api_key}', 'Content-Type': 'application/json'}
        payload = {
            "modelUri": f"gpt://{folder_id}/yandexgpt-lite",
            "completionOptions": {"stream": False, "temperature": 0.7, "maxTokens": 300},
            "messages": [{"role": "user", "text": prompt}]
        }
        response = requests.post(
            'https://llm.api.cloud.yandex.net/foundationModels/v1/completion',
            headers=headers,
            json=payload,
            timeout=15
        )
        if response.status_code == 200:
            data = response.json()
            reply = data['result']['alternatives'][0]['message']['text'].strip()
            print("✅ Получен ответ от YandexGPT для паттернов")
        else:
            print(f"❌ Ошибка YandexGPT: {response.status_code}")
            best_day = max(days_stats, key=lambda x: x['avg_mood']) if days_stats else None
            worst_day = min(days_stats, key=lambda x: x['avg_mood']) if days_stats else None
            joys_text = f" И ещё у тебя {joys_count} радостей в копилке! 🎉" if joys_count > 0 else ""
            if best_day and worst_day:
                reply = f"""📊 Ваши паттерны настроения:

Лучший день: {best_day['day_name']} ({float(best_day['avg_mood']):.1f}/10)
Сложный день: {worst_day['day_name']} ({float(worst_day['avg_mood']):.1f}/10){joys_text}

Планируйте важные дела на {best_day['day_name']}, а на {worst_day['day_name']} оставьте время для отдыха! 💪"""
            else:
                reply = "Пока недостаточно данных для анализа паттернов."
        return jsonify({'reply': reply, 'success': True, 'analysis_type': 'patterns'})
    except Exception as e:
        current_app.logger.error(f"Patterns analysis error: {str(e)}")
        return jsonify({'reply': 'Не могу проанализировать паттерны сейчас. Попробуй позже! 📊', 'success': False})

def analyze_notes(user_id, user_message):
    """Анализ заметок пользователя из mood_entries"""
    try:
        print(f"📝 АНАЛИЗ ЗАМЕТОК: user_id={user_id}")
        conn = get_db()
        if conn is None:
            return jsonify({'error': 'Ошибка подключения к БД'}), 500
        
        try:
            cursor = conn.cursor(dictionary=True)
            cursor.execute("""
                SELECT date, mood, note 
                FROM mood_entries 
                WHERE user_id = %s 
                AND note IS NOT NULL 
                AND TRIM(note) != '' 
                AND LENGTH(TRIM(note)) > 3
                ORDER BY date DESC 
                LIMIT 50
            """, (user_id,))
            all_notes = cursor.fetchall()
            print(f"📋 Найдено заметок: {len(all_notes)}")
            cursor.execute("""
                SELECT 
                    COUNT(*) as total_notes,
                    AVG(mood) as avg_mood_with_notes,
                    COUNT(CASE WHEN mood >= 7 THEN 1 END) as positive_notes,
                    COUNT(CASE WHEN mood <= 4 THEN 1 END) as challenging_notes,
                    MAX(date) as last_note_date,
                    MIN(date) as first_note_date
                FROM mood_entries 
                WHERE user_id = %s 
                AND note IS NOT NULL 
                AND TRIM(note) != ''
            """, (user_id,))
            notes_stats = cursor.fetchone()
            all_texts = ' '.join([note['note'].lower() for note in all_notes])
            cursor.close()
        finally:
            if cursor:
                cursor.close()
            close_db(conn)
        
        if not all_notes or len(all_notes) == 0:
            return jsonify({
                'reply': 'У тебя пока нет заметок с текстом. Попробуй добавить описание к своему настроению в календаре! 📝\n\n💡 Совет: Когда отмечаешь настроение, напиши пару слов о том, что произошло за день.',
                'success': True,
                'analysis_type': 'notes'
            })
        
        api_key = os.environ.get('YANDEX_API_KEY')
        folder_id = os.environ.get('YANDEX_FOLDER_ID')
        
        if not api_key or not folder_id or len(all_notes) < 3:
            reply = f"""📝 ТВОИ ЗАМЕТКИ:

Всего заметок с текстом: {notes_stats['total_notes'] or 0}
• 📅 Первая заметка: {notes_stats['first_note_date'].strftime('%d.%m.%Y') if notes_stats['first_note_date'] else 'нет данных'}
• 📅 Последняя заметка: {notes_stats['last_note_date'].strftime('%d.%m.%Y') if notes_stats['last_note_date'] else 'нет данных'}
• 📊 Среднее настроение в заметках: {float(notes_stats['avg_mood_with_notes'] or 0):.1f}/10
• 😊 Позитивных заметок (7+/10): {notes_stats['positive_notes'] or 0}
• 💪 Сложных дней с заметками (4-/10): {notes_stats['challenging_notes'] or 0}

ПОСЛЕДНИЕ ЗАМЕТКИ:
{chr(10).join([f"• {note['date'].strftime('%d.%m')}: {note['mood']}/10 - {note['note'][:70]}..." for note in all_notes[:5]])}

💡 Записывать мысли и чувства - полезная практика для самоанализа!"""
            return jsonify({'reply': reply, 'success': True, 'analysis_type': 'notes'})
        
        print(f"🔗 Отправляем запрос в YandexGPT с {len(all_notes)} заметками")
        notes_for_prompt = []
        for i, note in enumerate(all_notes[:15], 1):
            note_date = note.get('date')
            if isinstance(note_date, date):
                date_str = note_date.strftime('%d.%m.%Y')
            else:
                date_str = str(note_date)
            notes_for_prompt.append(f"{i}. {date_str}: Настроение {note.get('mood', '?')}/10 - '{note.get('note', '')}'")
        prompt = f"""
ПРОАНАЛИЗИРУЙ ЗАМЕТКИ ПОЛЬЗОВАТЕЛЯ ИЗ ДНЕВНИКА НАСТРОЕНИЯ:

ВОПРОС ПОЛЬЗОВАТЕЛЯ: "{user_message}"

СТАТИСТИКА:
- Всего заметок: {notes_stats['total_notes'] or 0}
- Среднее настроение в заметках: {float(notes_stats['avg_mood_with_notes'] or 0):.1f}/10
- Позитивных заметок (настроение 7+/10): {notes_stats['positive_notes'] or 0}
- Сложных дней с заметками (настроение 4-/10): {notes_stats['challenging_notes'] or 0}
- Ведет заметки с: {notes_stats['first_note_date'].strftime('%d.%m.%Y') if notes_stats['first_note_date'] else 'недавно'}

ЗАМЕТКИ ПОЛЬЗОВАТЕЛЯ (последние {len(notes_for_prompt)}):
{chr(10).join(notes_for_prompt)}

ПРОАНАЛИЗИРУЙ И ДАЙ ОТВЕТ:
1. Какие основные темы, события или эмоции прослеживаются в заметках?
2. О чем пользователь чаще пишет в хорошем/плохом настроении?
3. Какие полезные инсайты можно извлечь из этих записей?
4. Похвали за привычку вести заметки и дай рекомендацию

Ответ: 3-4 предложения, дружеский тон, с эмодзи. Обращай внимание на содержание заметок.
"""
        headers = {'Authorization': f'Api-Key {api_key}', 'Content-Type': 'application/json'}
        payload = {
            "modelUri": f"gpt://{folder_id}/yandexgpt-lite",
            "completionOptions": {"stream": False, "temperature": 0.7, "maxTokens": 350},
            "messages": [{"role": "user", "text": prompt}]
        }
        response = requests.post(
            'https://llm.api.cloud.yandex.net/foundationModels/v1/completion',
            headers=headers,
            json=payload,
            timeout=15
        )
        if response.status_code == 200:
            data = response.json()
            reply = data['result']['alternatives'][0]['message']['text'].strip()
            print("✅ Получен ответ от YandexGPT для заметок")
        else:
            print(f"❌ Ошибка YandexGPT: {response.status_code}")
            if len(all_notes) >= 5:
                latest_notes = chr(10).join([f"• {note['date'].strftime('%d.%m')}: {note['mood']}/10" for note in all_notes[:5]])
                reply = f"""📝 Твои заметки:

У тебя {notes_stats['total_notes'] or 0} заметок в дневнике настроения! 📖
Среднее настроение когда ты пишешь заметки: {float(notes_stats['avg_mood_with_notes'] or 0):.1f}/10

Последние записи:
{latest_notes}

Записывать свои мысли - это отличный способ лучше понимать себя! 💭
Продолжай вести заметки для более глубокого анализа!"""
            else:
                reply = f"У тебя {len(all_notes)} заметок. Продолжай записывать свои мысли для анализа! 📝"
        return jsonify({'reply': reply, 'success': True, 'analysis_type': 'notes'})
    except Exception as e:
        current_app.logger.error(f"Notes analysis error: {str(e)}")
        return jsonify({'reply': 'Не могу проанализировать заметки сейчас. Попробуй позже! 📝', 'success': False})

def analyze_joys(user_id):
    """Анализ радостей пользователя"""
    try:
        print(f"✨ АНАЛИЗ РАДОСТЕЙ: user_id={user_id}")
        conn = get_db()
        if conn is None:
            return jsonify({'reply': 'Не могу подключиться к базе данных. Попробуй позже! 🔄', 'success': False})
        cursor = None
        try:
            cursor = conn.cursor(dictionary=True)
            cursor.execute("SELECT COUNT(*) as count FROM joys WHERE user_id = %s", (user_id,))
            joys_count = cursor.fetchone()['count']
            cursor.execute("""
                SELECT text, created_at 
                FROM joys 
                WHERE user_id = %s 
                ORDER BY created_at DESC 
                LIMIT 5
            """, (user_id,))
            recent_joys = cursor.fetchall()
        except Exception as e:
            print(f"❌ Ошибка при получении данных: {e}")
            return jsonify({'reply': 'Не могу получить данные о радостях. Попробуй позже! 🔄', 'success': False})
        finally:
            if cursor:
                cursor.close()
            close_db(conn)
        
        if joys_count == 0:
            reply = "📭 У тебя пока нет записей о радостях. Попробуй каждый день записывать хотя бы одну маленькую радость — это помогает замечать хорошее! ✨"
        elif joys_count == 1:
            reply = f"🌸 У тебя 1 радость в копилке! Это первый шаг к осознанности. Не забывай пополнять коллекцию! 💖"
        else:
            joys_examples = []
            for joy in recent_joys[:3]:
                date = joy['created_at'].strftime('%d.%m') if joy['created_at'] else ''
                joys_examples.append(f"• {joy['text']} ({date})")
            joys_text = "\n".join(joys_examples)
            if joys_count >= 10:
                reply = f"🎉 У тебя уже {joys_count} радостей! Ты настоящий коллекционер счастья! Вот твои последние:\n\n{joys_text}\n\nПродолжай в том же духе! 🌟"
            elif joys_count >= 5:
                reply = f"✨ У тебя {joys_count} радостей. Отличная привычка! Недавно ты радовался(ась):\n\n{joys_text}\n\n💖"
            else:
                reply = f"😊 У тебя {joys_count} радостей. Продолжай копить позитивные моменты!\n\n{joys_text}"
        return jsonify({'reply': reply, 'success': True, 'analysis_type': 'joys'})
    except Exception as e:
        current_app.logger.error(f"Joys analysis error: {str(e)}")
        return jsonify({'reply': 'Не могу проанализировать радости сейчас. Попробуй позже! 🔄', 'success': False})

def analyze_goals(user_id):
    """Анализ целей пользователя"""
    conn = get_db()
    if not conn:
        return jsonify({'reply': 'Не могу подключиться к базе данных. Попробуй позже! 🔄', 'success': False})
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT COUNT(*) as total, SUM(completed) as completed FROM goals WHERE user_id = %s", (user_id,))
        stats = cursor.fetchone()
        cursor.execute("SELECT text, completed, created_at FROM goals WHERE user_id = %s ORDER BY created_at DESC LIMIT 5", (user_id,))
        recent = cursor.fetchall()
        total = stats['total'] or 0
        completed = stats['completed'] or 0
        if total == 0:
            reply = "У тебя пока нет целей. Начни ставить небольшие цели на день — это помогает двигаться вперёд! 🎯"
        else:
            progress = round(completed / total * 100)
            reply = f"📊 У тебя {total} целей, из них выполнено {completed} ({progress}%)!\n\n"
            if recent:
                reply += "🎯 Последние цели:\n"
                for g in recent[:3]:
                    status = "✅" if g['completed'] else "◻️"
                    reply += f"{status} {g['text']}\n"
            reply += "\nПродолжай ставить цели и отмечать выполненные — это мотивирует! 💪"
        return jsonify({'reply': reply, 'success': True})
    except Exception as e:
        return jsonify({'reply': 'Ошибка анализа целей', 'success': False})
    finally:
        close_db(conn)

def analyze_cycle(user_id):
    """Анализ данных менструального цикла"""
    try:
        print(f"🔄 АНАЛИЗ ЦИКЛА: user_id={user_id}")
        conn = get_db()
        if conn is None:
            return jsonify({'reply': 'Не могу подключиться к базе данных. Попробуй позже! 🔄', 'success': False})
        try:
            cursor = conn.cursor(dictionary=True)
            cursor.execute("SELECT * FROM cycle_settings WHERE user_id = %s", (user_id,))
            settings = cursor.fetchone()
            cursor.execute("""
                SELECT date, cycle_day, symptoms, flow_intensity, mood, notes
                FROM cycle_entries 
                WHERE user_id = %s 
                ORDER BY date DESC 
                LIMIT 30
            """, (user_id,))
            cycle_entries = cursor.fetchall()
            cursor.execute("""
                SELECT 
                    COUNT(*) as total_entries,
                    COUNT(CASE WHEN flow_intensity IN ('light', 'medium', 'heavy') THEN 1 END) as period_days,
                    AVG(mood) as avg_mood_period
                FROM cycle_entries 
                WHERE user_id = %s
            """, (user_id,))
            stats = cursor.fetchone()
            cursor.close()
        finally:
            close_db(conn)
        if len(cycle_entries) == 0:
            return jsonify({
                'reply': "🌸 У тебя пока нет записей о цикле. Начни отмечать дни в дневнике цикла — это поможет лучше понимать своё тело!",
                'success': True,
                'analysis_type': 'cycle'
            })
        reply_parts = []
        if settings and settings.get('last_period_start'):
            try:
                last_period = datetime.strptime(str(settings['last_period_start']), '%Y-%m-%d').date()
                today = datetime.now().date()
                days_since = (today - last_period).days
                if days_since <= settings.get('period_length', 5):
                    reply_parts.append(f"🩸 У тебя сейчас менструация (день {days_since}).")
                else:
                    next_period = last_period + timedelta(days=settings.get('cycle_length', 28))
                    days_to = (next_period - today).days
                    if days_to > 0:
                        reply_parts.append(f"📅 Следующая менструация предположительно через {days_to} дней.")
            except:
                pass
        if stats and stats['total_entries'] > 0:
            if stats['period_days'] > 0:
                reply_parts.append(f"📊 Отмечено {stats['period_days']} дней менструации.")
            if stats['avg_mood_period']:
                avg_mood = float(stats['avg_mood_period'])
                if avg_mood >= 7:
                    reply_parts.append(f"😊 В дни цикла настроение в среднем {avg_mood:.1f}/10 — отлично!")
                elif avg_mood >= 5:
                    reply_parts.append(f"😐 Настроение в дни цикла: {avg_mood:.1f}/10.")
                else:
                    reply_parts.append(f"😔 В дни цикла настроение снижено ({avg_mood:.1f}/10). Обрати внимание на отдых.")
        all_symptoms = []
        for entry in cycle_entries:
            if entry.get('symptoms') and isinstance(entry['symptoms'], list):
                all_symptoms.extend(entry['symptoms'])
        if all_symptoms:
            from collections import Counter
            symptom_counts = Counter(all_symptoms)
            top_symptoms = symptom_counts.most_common(3)
            symptoms_text = ", ".join([f"{s} ({c} раз)" for s, c in top_symptoms])
            reply_parts.append(f"🔍 Частые симптомы: {symptoms_text}.")
        reply_parts.append("\n💡 Советы по фазам цикла:")
        reply_parts.append("• Менструация: отдых, тепло, меньше нагрузок")
        reply_parts.append("• Фолликулярная: энергия растёт — время для новых дел")
        reply_parts.append("• Овуляция: пик коммуникабельности")
        reply_parts.append("• Лютеиновая и ПМС: будь добрее к себе, больше отдыха")
        return jsonify({'reply': "\n".join(reply_parts), 'success': True, 'analysis_type': 'cycle'})
    except Exception as e:
        current_app.logger.error(f"Cycle analysis error: {str(e)}")
        return jsonify({'reply': 'Не могу проанализировать цикл сейчас. Попробуй позже! 🔄', 'success': False})

# ================== ДОПОЛНИТЕЛЬНЫЙ API ДЛЯ ПОЛУЧЕНИЯ АНАЛИЗА ==================

@main.route('/api/ai_insights')
@login_required
def get_ai_insights():
    """API для получения AI-анализа данных пользователя"""
    try:
        conn = get_db()
        if conn is None:
            return jsonify({'error': 'Ошибка подключения к базе данных'}), 500
        try:
            stats = generate_user_statistics(conn, current_user.id)
            insights = generate_ai_insights(stats)
            return jsonify({
                'success': True,
                'insights': insights,
                'stats_summary': {
                    'avg_mood': stats['avg_mood'],
                    'mood_score': stats['mood_score'],
                    'trend': stats['trend'],
                    'total_entries': stats['total_entries'],
                    'good_days': stats['good_days'],
                    'bad_days': stats['bad_days']
                }
            })
        finally:
            close_db(conn)
    except Exception as e:
        current_app.logger.error(f"AI Insights error: {str(e)}")
        return jsonify({
            'success': False,
            'error': 'Не удалось сгенерировать анализ',
            'insights': 'Продолжай отслеживать настроение, чтобы получить персональные рекомендации!'
        })

@main.route('/health')
def health_check():
    """Маршрут для проверки здоровья приложения Railway"""
    return jsonify({'status': 'healthy', 'service': 'Lumi'}), 200
