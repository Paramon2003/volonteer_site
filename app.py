import jsonify as jsonify
from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta
import calendar
from flask import Flask, render_template, redirect, url_for, request, session, flash, send_from_directory, jsonify
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
import sqlite3
import os
import requests
from datetime import datetime

app = Flask(__name__)
app.secret_key = 'your_secret_key_here'
UPLOAD_FOLDER = 'static/uploads'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
DB_NAME = 'volunteer.db'

COLLECTION_TYPES = {
    'product_basket': 'Продуктовая корзина',
    'medicine': 'Лекарства',
    'treatment': 'Лечение',
    'equipment': 'Оборудование',
    'one_time': 'Разовый сбор',
}

def geocode_address(address, city="Воронеж"):
    base_url = "https://nominatim.openstreetmap.org/search"

    # Формируем запрос (Nominatim требует User-Agent)
    headers = {
        "User-Agent": "YourApp/1.0 (your@email.com)"  # Укажите свои данные
    }
    params = {
        "q": f"{city}, {address}",
        "format": "json",
        "limit": 1,
    }

    try:
        response = requests.get(base_url, params=params, headers=headers).json()
        if response:
            return float(response[0]["lat"]), float(response[0]["lon"])
    except Exception as e:
        print(f"Geocoding error: {e}")

    # Возвращаем координаты центра Воронежа, если геокодирование не удалось
    return (51.660598, 39.200585)


# --- Инициализация базы данных ---

def init_db():
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()

        # Таблица пользователей с ролями
        c.execute('''CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            email TEXT UNIQUE,
            password TEXT,
            role TEXT DEFAULT 'volunteer',  -- admin, volunteer, organization
            is_verified INTEGER DEFAULT 0,   -- 0 = не подтвержден, 1 = подтвержден
            rating REAL DEFAULT 0,
            completed_tasks INTEGER DEFAULT 0,
            organization_name TEXT,          -- для организаций
            organization_description TEXT,   -- описание организации
            created_at TEXT,
            last_login TEXT
        )''')

        # Таблица нуждающихся (добавляются только организациями/админами)
        c.execute('''CREATE TABLE IF NOT EXISTS needies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            tag TEXT,
            photo TEXT,
            description TEXT,
            address TEXT,
            lat REAL,
            lng REAL,
            funds_collected REAL DEFAULT 0,
            help_info TEXT,
            organization_id INTEGER,          -- кто добавил (ID организации)
            created_by INTEGER,               -- ID пользователя, добавившего
            created_at TEXT,
            is_active INTEGER DEFAULT 1,      -- активен ли профиль
            urgency_level INTEGER DEFAULT 2,   -- 1=высокая, 2=средняя, 3=низкая
            status TEXT DEFAULT 'pending',    -- pending, approved, rejected
            goal INTEGER,
            FOREIGN KEY(organization_id) REFERENCES users(id),
            FOREIGN KEY(created_by) REFERENCES users(id)
        )''')

        # Остальные таблицы с небольшими изменениями
        c.execute('''CREATE TABLE IF NOT EXISTS help_tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT,
            description TEXT,
            needy_id INTEGER,
            status TEXT DEFAULT 'pending',    -- pending, in_progress, completed, cancelled
            assigned_to INTEGER,              -- волонтер, взявший задачу
            created_by INTEGER,               -- кто создал задачу
            deadline TEXT,
            created_at TEXT,
            completed_at TEXT,
            FOREIGN KEY(needy_id) REFERENCES needies(id),
            FOREIGN KEY(assigned_to) REFERENCES users(id),
            FOREIGN KEY(created_by) REFERENCES users(id)
        )''')

        c.execute('''CREATE TABLE IF NOT EXISTS reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            needy_id INTEGER,
            task_id INTEGER,                  -- связь с задачей
            photo TEXT,
            text TEXT,
            status TEXT DEFAULT 'pending',     -- pending, approved, rejected
            created_at TEXT,
            approved_at TEXT,
            approved_by INTEGER,
            FOREIGN KEY(user_id) REFERENCES users(id),
            FOREIGN KEY(needy_id) REFERENCES needies(id),
            FOREIGN KEY(task_id) REFERENCES help_tasks(id),
            FOREIGN KEY(approved_by) REFERENCES users(id)
        )''')

        c.execute('''CREATE TABLE IF NOT EXISTS donations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            needy_id INTEGER,
            amount REAL,
            is_subscription INTEGER,
            created_at TEXT,
            FOREIGN KEY(user_id) REFERENCES users(id),
            FOREIGN KEY(needy_id) REFERENCES needies(id)
        )''')

        # Новая таблица для заявок на добавление нуждающихся
        c.execute('''CREATE TABLE IF NOT EXISTS needy_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            tag TEXT,
            description TEXT,
            address TEXT,
            help_info TEXT,
            requester_name TEXT,
            requester_phone TEXT,
            requester_email TEXT,
            status TEXT DEFAULT 'pending',    -- pending, approved, rejected
            created_at TEXT,
            reviewed_by INTEGER,
            reviewed_at TEXT,
            review_comment TEXT,
            FOREIGN KEY(reviewed_by) REFERENCES users(id)
        )''')

        # Создаем администратора по умолчанию, если его нет
        admin_exists = c.execute('SELECT * FROM users WHERE role = "admin"').fetchone()
        if not admin_exists:
            from werkzeug.security import generate_password_hash
            c.execute('''INSERT INTO users (name, email, password, role, is_verified, created_at)
                        VALUES (?, ?, ?, ?, ?, ?)''',
                      ('Администратор', 'admin@example.com',
                       generate_password_hash('admin123'), 'admin', 1,
                       datetime.now().isoformat()))
        c.execute("PRAGMA table_info(needies)")
        columns = [col[1] for col in c.fetchall()]

        if 'collection_status' not in columns:
            c.execute('ALTER TABLE needies ADD COLUMN collection_status TEXT DEFAULT "active"')
        if 'completed_date' not in columns:
            c.execute('ALTER TABLE needies ADD COLUMN completed_date TEXT')
        if 'last_reset_date' not in columns:
            c.execute('ALTER TABLE needies ADD COLUMN last_reset_date TEXT')
        if 'reset_day' not in columns:
            c.execute('ALTER TABLE needies ADD COLUMN reset_day INTEGER DEFAULT 1')

        # Таблица для достижений
        c.execute('''CREATE TABLE IF NOT EXISTS badges (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                badge_name TEXT,
                badge_icon TEXT,
                earned_date TEXT,
                FOREIGN KEY(user_id) REFERENCES users(id)
            )''')

        # Таблица для уведомлений
        c.execute('''CREATE TABLE IF NOT EXISTS notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                type TEXT,
                title TEXT,
                message TEXT,
                link TEXT,
                is_read INTEGER DEFAULT 0,
                created_at TEXT,
                FOREIGN KEY(user_id) REFERENCES users(id)
            )''')

        # Таблица для детализации расходов в отчетах
        c.execute('''CREATE TABLE IF NOT EXISTS report_expenses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                report_id INTEGER,
                breakdown TEXT,
                total_amount REAL,
                created_at TEXT,
                FOREIGN KEY(report_id) REFERENCES reports(id)
            )''')

        # Добавляем новые колонки в needies
        c.execute("PRAGMA table_info(needies)")
        columns = [col[1] for col in c.fetchall()]

        if 'report_created' not in columns:
            c.execute('ALTER TABLE needies ADD COLUMN report_created INTEGER DEFAULT 0')
        if 'report_id' not in columns:
            c.execute('ALTER TABLE needies ADD COLUMN report_id INTEGER')

        # Таблица для комментариев к отчетам
        c.execute('''CREATE TABLE IF NOT EXISTS report_comments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                report_id INTEGER,
                user_id INTEGER,
                comment TEXT,
                created_at TEXT,
                FOREIGN KEY(report_id) REFERENCES reports(id),
                FOREIGN KEY(user_id) REFERENCES users(id)
            )''')

        # Таблица для подписок на нуждающихся
        c.execute('''CREATE TABLE IF NOT EXISTS needy_subscriptions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                needy_id INTEGER,
                created_at TEXT,
                UNIQUE(user_id, needy_id),
                FOREIGN KEY(user_id) REFERENCES users(id),
                FOREIGN KEY(needy_id) REFERENCES needies(id)
            )''')
        conn.commit()


def check_and_update_collection_status(needy_id):
    """Проверяет статус сбора и обновляет его при необходимости"""
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        c.execute('''SELECT id, help_info, goal, funds_collected, collection_status, 
                            last_reset_date, reset_day
                     FROM needies WHERE id = ?''', (needy_id,))
        needy = c.fetchone()

        if not needy:
            return

        help_info = needy[1]
        goal = needy[2]
        collected = needy[3]
        status = needy[4]
        last_reset = needy[5]
        reset_day = needy[6]

        # Проверяем, нужно ли сбросить сбор для продуктовой корзины
        if 'продуктовая' in help_info.lower() or 'корзина' in help_info.lower():
            now = datetime.now()

            # Если сбор завершен и наступил новый месяц
            if status == 'completed':
                if last_reset:
                    last_reset_date = datetime.fromisoformat(last_reset)
                    if now.month != last_reset_date.month or now.year != last_reset_date.year:
                        # Сбрасываем сбор на новый месяц
                        c.execute('''UPDATE needies 
                                     SET funds_collected = 0, 
                                         collection_status = 'active',
                                         last_reset_date = ?
                                     WHERE id = ?''',
                                  (now.isoformat(), needy_id))
                        conn.commit()
                        return
            elif status == 'active' and collected >= goal:
                # Достигнута цель - завершаем сбор до следующего месяца
                c.execute('''UPDATE needies 
                             SET collection_status = 'completed',
                                 completed_date = ?
                             WHERE id = ?''',
                          (now.isoformat(), needy_id))
                conn.commit()

        # Для разовых сборов - просто завершаем
        elif 'разовый' in help_info.lower():
            if collected >= goal and status == 'active':
                c.execute('''UPDATE needies 
                             SET collection_status = 'completed',
                                 completed_date = ?
                             WHERE id = ?''',
                          (datetime.now().isoformat(), needy_id))
                conn.commit()


def check_and_award_badges(user_id):
    """Проверяет и выдает достижения пользователю"""
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()

        # Получаем статистику пользователя
        c.execute('''SELECT COUNT(DISTINCT needy_id) as helped_needies,
                            SUM(amount) as total_donated,
                            COUNT(*) as donations_count
                     FROM donations 
                     WHERE user_id = ?''', (user_id,))
        stats = c.fetchone()

        badges_earned = []

        if stats[0] >= 1:
            badges_earned.append('🥇 Первая помощь')
        if stats[0] >= 5:
            badges_earned.append('🌟 Щедрое сердце')
        if stats[1] and stats[1] >= 10000:
            badges_earned.append('💰 Крупный благотворитель')
        if stats[2] >= 10:
            badges_earned.append('🔥 Постоянный помощник')

        return badges_earned


def create_notification(user_id, n_type, title, message, link=None):
    """Создание уведомления для пользователя"""
    try:
        with sqlite3.connect(DB_NAME) as conn:
            c = conn.cursor()
            c.execute('''INSERT INTO notifications 
                        (user_id, type, title, message, link, created_at, is_read)
                        VALUES (?, ?, ?, ?, ?, ?, 0)''',
                      (user_id, n_type, title, message, link, datetime.now().isoformat()))
            conn.commit()
    except Exception as e:
        print(f"Error creating notification: {e}")


def check_organization_badges(org_id):
    """Проверяет и выдает достижения организации"""
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()

        # Получаем статистику
        c.execute('''SELECT 
                        (SELECT COUNT(*) FROM needies WHERE organization_id = ?) as total_needies,
                        (SELECT COUNT(*) FROM reports WHERE user_id = ? AND status = 'approved') as total_reports,
                        (SELECT COALESCE(SUM(funds_collected), 0) FROM needies WHERE organization_id = ?) as total_funds
                     ''', (org_id, org_id, org_id))
        stats = c.fetchone()

        badges_to_add = []

        if stats[0] >= 1 and not has_badge(org_id, 'first_needy'):
            badges_to_add.append(('Первый подопечный', '🌟', 'first_needy'))
        if stats[0] >= 5 and not has_badge(org_id, 'five_needies'):
            badges_to_add.append(('5 подопечных', '🏆', 'five_needies'))
        if stats[1] >= 1 and not has_badge(org_id, 'first_report'):
            badges_to_add.append(('Первый отчет', '📋', 'first_report'))
        if stats[2] >= 100000 and not has_badge(org_id, 'major_funds'):
            badges_to_add.append(('Крупный сбор', '💰', 'major_funds'))

        for badge in badges_to_add:
            c.execute('''INSERT INTO badges (user_id, badge_name, badge_icon, earned_date, badge_type)
                        VALUES (?, ?, ?, ?, ?)''',
                      (org_id, badge[0], badge[1], datetime.now().isoformat(), badge[2]))

        conn.commit()
        return badges_to_add


def has_badge(user_id, badge_type):
    """Проверяет, есть ли у пользователя достижение"""
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        c.execute('SELECT id FROM badges WHERE user_id = ? AND badge_type = ?',
                  (user_id, badge_type))
        return c.fetchone() is not None


@app.route('/choose-role')
def choose_role():
    if 'user_id' in session:
        return redirect(url_for('dashboard'))
    return render_template('choose_role.html')


@app.route('/register/volunteer', methods=['GET', 'POST'])
def register_volunteer():
    if 'user_id' in session:
        return redirect(url_for('dashboard'))

    if request.method == 'POST':
        name = request.form['name']
        email = request.form['email']
        phone = request.form['phone']
        password = request.form['password']
        confirm_password = request.form['confirm_password']
        role = 'volunteer'

        # Проверка пароля
        if password != confirm_password:
            flash('Пароли не совпадают')
            return redirect(url_for('register_volunteer'))

        password_hash = generate_password_hash(password)

        # Обработка фото
        photo = None
        if 'photo' in request.files:
            f = request.files['photo']
            if f.filename:
                filename = secure_filename(f.filename)
                photo = os.path.join(UPLOAD_FOLDER, filename)
                f.save(photo)

        with sqlite3.connect(DB_NAME) as conn:
            try:
                conn.execute('''INSERT INTO users (name, email, password, phone, role, photo, is_verified, created_at)
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
                             (name, email, password_hash, phone, role, photo, 1, datetime.now().isoformat()))
                conn.commit()
                flash('Регистрация успешна! Теперь вы можете войти.')
                return redirect(url_for('login'))
            except sqlite3.IntegrityError:
                flash('Email уже используется')

    return render_template('register_volunteer.html')


@app.route('/register/organization', methods=['GET', 'POST'])
def register_organization():
    """Регистрация организации"""
    if 'user_id' in session:
        return redirect(url_for('dashboard'))

    if request.method == 'POST':
        name = request.form['name']
        email = request.form['email']
        phone = request.form['phone']
        password = request.form['password']
        confirm_password = request.form['confirm_password']
        organization_description = request.form.get('organization_description', '')
        role = 'organization'
        is_verified = 0  # Требует подтверждения

        # Проверка пароля
        if password != confirm_password:
            flash('Пароли не совпадают')
            return redirect(url_for('register_organization'))

        password_hash = generate_password_hash(password)

        # Обработка фото
        photo = None
        if 'photo' in request.files:
            f = request.files['photo']
            if f.filename:
                filename = secure_filename(f.filename)
                photo = os.path.join(UPLOAD_FOLDER, filename)
                f.save(photo)

        with sqlite3.connect(DB_NAME) as conn:
            try:
                conn.execute('''INSERT INTO users (name, email, password, phone, role, photo, is_verified, 
                                                  organization_name, organization_description, created_at)
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                             (name, email, password_hash, phone, role, photo, is_verified,
                              name, organization_description, datetime.now().isoformat()))
                conn.commit()
                flash('Ваша заявка отправлена на рассмотрение. После подтверждения вы сможете добавлять нуждающихся.',
                      'info')
                return redirect(url_for('login'))
            except sqlite3.IntegrityError:
                flash('Email уже используется')

    return render_template('register_organization.html')


# --- Авторизация ---
# app.py - обновленный логин с сохранением роли в сессии

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']
        with sqlite3.connect(DB_NAME) as conn:
            c = conn.cursor()
            c.execute('SELECT * FROM users WHERE email = ?', (email,))
            user = c.fetchone()
            if user and check_password_hash(user[3], password):
                session['user_id'] = user[0]
                session['user_role'] = user[5]  # сохраняем роль
                session['user_name'] = user[1]

                # Обновляем время последнего входа
                c.execute('UPDATE users SET last_login = ? WHERE id = ?',
                          (datetime.now().isoformat(), user[0]))
                conn.commit()

                return redirect(url_for('dashboard'))
            else:
                flash('Неверный email или пароль')
    return render_template('login.html')


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))


# --- Главная страница ---
@app.route('/')
def index():
    return render_template('index.html')


# --- Личный кабинет ---

@app.route('/dashboard')
def dashboard():
    if 'user_id' not in session:
        return redirect(url_for('login'))

    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()

        # Получаем данные пользователя
        c.execute('''SELECT id, name, email, phone, role, photo, rating, completed_tasks, 
                            created_at, is_verified, organization_description 
                     FROM users WHERE id = ?''', (session['user_id'],))
        user_data = c.fetchone()

        if not user_data:
            session.clear()
            return redirect(url_for('login'))

        # Преобразуем в словарь для удобства
        user = {
            'id': user_data[0],
            'name': user_data[1],
            'email': user_data[2],
            'phone': user_data[3],
            'role': user_data[4],
            'photo': user_data[5],
            'rating': user_data[6],
            'completed_tasks': user_data[7],
            'created_at': user_data[8],
            'is_verified': user_data[9],
            'organization_description': user_data[10]
        }

        # Проверяем, есть ли таблица notifications и нужные колонки в needies
        c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='notifications'")
        has_notifications = c.fetchone() is not None

        # Проверяем наличие новых колонок в needies
        c.execute("PRAGMA table_info(needies)")
        needies_columns = [col[1] for col in c.fetchall()]
        has_collection_status = 'collection_status' in needies_columns
        has_report_created = 'report_created' in needies_columns

        # Для организаций - получаем особые данные
        if user['role'] == 'organization':
            # Активные сборы (в процессе)
            if has_collection_status:
                c.execute('''SELECT id, name, photo, funds_collected, goal, 
                                    collection_status, help_info
                             FROM needies 
                             WHERE organization_id = ? 
                             AND status = 'approved' 
                             AND is_active = 1
                             AND collection_status = 'active'
                             ORDER BY created_at DESC''', (session['user_id'],))
            else:
                # Если колонки collection_status нет, используем старую логику
                c.execute('''SELECT id, name, photo, funds_collected, goal, 
                                    'active' as collection_status, help_info
                             FROM needies 
                             WHERE organization_id = ? 
                             AND status = 'approved' 
                             AND is_active = 1
                             ORDER BY created_at DESC''', (session['user_id'],))
            active_collections = c.fetchall()

            # Завершенные сборы без отчета (задачи в процессе)
            if has_collection_status and has_report_created:
                c.execute('''SELECT id, name, photo, funds_collected, goal,
                                    completed_date
                             FROM needies 
                             WHERE organization_id = ? 
                             AND collection_status = 'completed'
                             AND (report_created = 0 OR report_created IS NULL)
                             ORDER BY completed_date DESC''', (session['user_id'],))
            else:
                # Если колонок нет, возвращаем пустой список
                c.execute('''SELECT id, name, photo, funds_collected, goal,
                                    created_at as completed_date
                             FROM needies 
                             WHERE organization_id = ? 
                             AND funds_collected >= goal
                             AND goal > 0
                             ORDER BY created_at DESC''', (session['user_id'],))
            pending_reports = c.fetchall()

            # Созданные отчеты
            c.execute('''SELECT r.id, r.text, r.photo, r.created_at, 
                                r.status, n.name as needy_name, n.id as needy_id
                         FROM reports r
                         JOIN needies n ON r.needy_id = n.id
                         WHERE r.user_id = ?
                         ORDER BY r.created_at DESC
                         LIMIT 10''', (session['user_id'],))
            reports_data = c.fetchall()

            # Статистика
            c.execute('''SELECT 
                            COUNT(*) as total_needies,
                            COALESCE(SUM(funds_collected), 0) as total_raised,
                            COUNT(CASE WHEN funds_collected >= goal AND goal > 0 THEN 1 END) as completed_collections
                         FROM needies 
                         WHERE organization_id = ?''', (session['user_id'],))
            stats = c.fetchone()

            # Форматируем отчеты
            reports = []
            for r in reports_data:
                reports.append({
                    'id': r[0],
                    'text': r[1],
                    'photo': r[2],
                    'created_at': r[3],
                    'status': r[4],
                    'needy_name': r[5],
                    'needy_id': r[6]
                })

            return render_template('dashboard_organization.html',
                                   user=user,
                                   active_collections=active_collections,
                                   pending_reports=pending_reports,
                                   reports=reports,
                                   stats=stats,
                                   has_notifications=has_notifications)

        # Для волонтеров и админов
        else:
            # Получаем отчеты пользователя с именами нуждающихся
            c.execute('''SELECT r.text, r.photo, r.created_at, n.name as needy_name 
                         FROM reports r
                         JOIN needies n ON n.id = r.needy_id
                         WHERE r.user_id = ?
                         ORDER BY r.created_at DESC
                         LIMIT 10''', (session['user_id'],))
            reports_data = c.fetchall()

            reports = []
            for r in reports_data:
                reports.append({
                    'text': r[0],
                    'photo': r[1],
                    'created_at': r[2],
                    'needy_name': r[3]
                })

            # Получаем активные задачи
            c.execute('''SELECT id, title, description, status 
                         FROM help_tasks 
                         WHERE assigned_to = ? AND status IN ('pending', 'in_progress')
                         ORDER BY created_at DESC
                         LIMIT 5''', (session['user_id'],))
            tasks_data = c.fetchall()

            active_tasks = []
            in_progress_count = 0
            for t in tasks_data:
                active_tasks.append({
                    'id': t[0],
                    'title': t[1],
                    'description': t[2] or '',
                    'status': t[3]
                })
                if t[3] == 'in_progress':
                    in_progress_count += 1

            completed_tasks_count = user['completed_tasks'] or 0
            user_rating = user['rating'] or 0
            is_verified = user['is_verified'] or 0

            # Получаем пожертвования пользователя
            c.execute('''SELECT d.amount, d.created_at, n.name as needy_name, n.id as needy_id
                         FROM donations d
                         JOIN needies n ON d.needy_id = n.id
                         WHERE d.user_id = ?
                         ORDER BY d.created_at DESC
                         LIMIT 5''', (session['user_id'],))
            donations_data = c.fetchall()

            donations = []
            for d in donations_data:
                donations.append({
                    'amount': d[0],
                    'created_at': d[1],
                    'needy_name': d[2],
                    'needy_id': d[3]
                })

            # Получаем количество непрочитанных уведомлений
            unread_notifications = 0
            if has_notifications:
                c.execute('''SELECT COUNT(*) FROM notifications 
                             WHERE user_id = ? AND is_read = 0''', (session['user_id'],))
                unread_notifications = c.fetchone()[0]

            return render_template('dashboard.html',
                                   user_id=user['id'],
                                   user_name=user['name'],
                                   user_email=user['email'],
                                   user_phone=user['phone'],
                                   user_role=user['role'],
                                   user_photo=user['photo'],
                                   user_rating=user_rating,
                                   completed_tasks=completed_tasks_count,
                                   in_progress_tasks=in_progress_count,
                                   created_at=user['created_at'],
                                   is_verified=is_verified,
                                   organization_description=user['organization_description'],
                                   reports=reports,
                                   active_tasks=active_tasks,
                                   donations=donations,
                                   unread_notifications=unread_notifications)
# app.py - обновленный список нуждающихся

@app.route('/needies')
def needies_list():
    view = request.args.get('view', 'list')

    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        # Показываем только подтвержденных нуждающихся
        c.execute('''SELECT id, name, tag, photo, description, address, lat, lng, urgency_level, funds_collected, goal, help_info 
                     FROM needies 
                     WHERE status = "approved" AND is_active = 1 
                     ORDER BY urgency_level ASC, created_at DESC''')
        needies = c.fetchall()

        # Для карты добавляем координаты
        if view == 'map':
            map_data = []
            for needy in needies:
                if needy[6] and needy[7]:
                    lat, lng = needy[6], needy[7]
                else:
                    lat, lng = geocode_address(needy[5])
                map_data.append({
                    'id': needy[0],
                    'name': needy[1],
                    'tag': needy[2],
                    'photo': needy[3],
                    'description': needy[4],
                    'address': needy[5],
                    'lat': lat,
                    'lng': lng,
                    'urgency_level': needy[8]
                })
            return render_template('needies_map.html', needies=map_data)

        return render_template('needies_list.html', needies=needies)


# --- Список нуждающихся ---
@app.route('/needy/<int:id>')
def needy_profile(id):
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()

        # Явно указываем нужные колонки вместо SELECT *
        c.execute('''SELECT n.id, n.name, n.tag, n.photo, n.description, n.address, 
                            n.funds_collected, n.lat, n.lng, n.help_info, n.goal, 
                            n.urgency_level, n.collection_status, n.created_at, n.organization_id,
                            (SELECT COUNT(DISTINCT user_id) FROM donations WHERE needy_id = n.id) as helpers_count
                     FROM needies n 
                     WHERE n.id = ?''', (id,))
        needy = c.fetchone()

        if not needy:
            flash('Нуждающийся не найден')
            return redirect(url_for('needies_list'))

    return render_template('needy_profile.html', needy=needy)

# добавление нуждающегося
@app.route('/add_needy', methods=['GET', 'POST'])
def add_needy():
    if 'user_id' not in session:
        return redirect(url_for('login'))

    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        c.execute('SELECT role, is_verified FROM users WHERE id = ?', (session['user_id'],))
        user = c.fetchone()

        # Проверка прав: только организации (подтвержденные) и админы
        if user[0] == 'volunteer':
            flash('Только организации и администраторы могут добавлять нуждающихся')
            return redirect(url_for('needies_list'))

        if user[0] == 'organization' and not user[1]:
            flash('Ваша организация еще не подтверждена администратором')
            return redirect(url_for('needies_list'))

        if request.method == 'POST':
            name = request.form['name']
            tag = request.form['tag']
            description = request.form['description']
            address = request.form['address']
            help_info = request.form.get('help_info', '')
            urgency_level = int(request.form.get('urgency_level', 2))
            goal = float(request.form.get('goal', 0))  # ДОБАВИТЬ ЭТУ СТРОКУ

            photo = None
            if 'photo' in request.files:
                f = request.files['photo']
                if f.filename:
                    filename = secure_filename(f.filename)
                    photo = os.path.join(UPLOAD_FOLDER, filename)
                    f.save(photo)

            # Геокодирование адреса
            lat, lng = geocode_address(address)

            # Для организаций - сразу активный, для админов - тоже
            status = 'approved'  # Сразу одобрено, так как добавляет верифицированная организация или админ

            # ИСПРАВЛЕННЫЙ INSERT с полем goal
            c.execute('''INSERT INTO needies (name, tag, photo, description, address, lat, lng, 
                                             help_info, organization_id, created_by, created_at, 
                                             urgency_level, status, goal)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                      (name, tag, photo, description, address, lat, lng, help_info,
                       session['user_id'], session['user_id'], datetime.now().isoformat(),
                       urgency_level, status, goal))
            conn.commit()

            flash('Нуждающийся успешно добавлен!')
            return redirect(url_for('needies_list'))

    return render_template('add_needy.html', user_role=user[0], is_verified=user[1])

# --- Задания ---
@app.route('/tasks')
def tasks():
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        c.execute('''SELECT t.id, t.title, t.description, n.name FROM help_tasks t
                     JOIN needies n ON t.needy_id = n.id''')
        tasks = c.fetchall()
    return render_template('help_tasks.html', tasks=tasks)


# --- Отчет ---
@app.route('/report/<int:needy_id>', methods=['GET', 'POST'])
def report(needy_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))
    if request.method == 'POST':
        text = request.form['text']
        photo = None
        if 'photo' in request.files:
            f = request.files['photo']
            if f.filename:
                photo = os.path.join(UPLOAD_FOLDER, f.filename)
                f.save(photo)
        with sqlite3.connect(DB_NAME) as conn:
            conn.execute('''INSERT INTO reports (user_id, needy_id, photo, text, created_at)
                            VALUES (?, ?, ?, ?, ?)''',
                         (session['user_id'], needy_id, photo, text, datetime.now().isoformat()))
            conn.commit()
        return redirect(url_for('dashboard'))
    return render_template('report_form.html', needy_id=needy_id)


@app.route('/needy/<int:needy_id>/reports')
def needy_reports(needy_id):
    """Страница со всеми отчетами по нуждающемуся"""
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()

        # Получаем информацию о нуждающемся
        c.execute('''SELECT id, name, photo, funds_collected, goal, 
                            collection_status, help_info
                     FROM needies WHERE id = ?''', (needy_id,))
        needy = c.fetchone()

        if not needy:
            flash('Нуждающийся не найден')
            return redirect(url_for('needies_list'))

        # Получаем все одобренные отчеты
        c.execute('''SELECT r.id, r.text, r.photo, r.created_at, 
                            u.name as org_name, u.id as org_id,
                            (SELECT SUM(amount) FROM donations WHERE needy_id = r.needy_id) as total_donated
                     FROM reports r
                     JOIN users u ON r.user_id = u.id
                     WHERE r.needy_id = ? AND r.status = 'approved'
                     ORDER BY r.created_at DESC''', (needy_id,))
        reports = c.fetchall()

        # Статистика расходования средств
        c.execute('''SELECT SUM(amount) FROM reports_expenses WHERE report_id IN 
                     (SELECT id FROM reports WHERE needy_id = ?)''', (needy_id,))
        total_spent = c.fetchone()[0] or 0

        return render_template('needy_reports.html',
                               needy=needy,
                               reports=reports,
                               total_spent=total_spent)

# --- Платежи (заглушка) ---
@app.route('/donate/<int:needy_id>', methods=['POST'])
def donate(needy_id):
    if 'user_id' not in session:
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.form.get('ajax'):
            return jsonify({'success': False, 'error': 'Unauthorized'}), 401
        return redirect(url_for('login'))

    amount = float(request.form['amount'])
    is_subscription = int(request.form.get('subscription', 0))

    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()

        # Проверяем статус сбора
        c.execute('''SELECT collection_status, goal, funds_collected, help_info 
                     FROM needies WHERE id = ?''', (needy_id,))
        needy = c.fetchone()

        if not needy:
            return jsonify({'success': False, 'error': 'Нуждающийся не найден'})

        # Если сбор завершен - проверяем возможность возобновления
        if needy[0] == 'completed':
            help_info = needy[3]
            if 'продуктовая' in help_info.lower() or 'корзина' in help_info.lower():
                # Проверяем, не начался ли новый месяц
                check_and_update_collection_status(needy_id)
                # Получаем обновленный статус
                c.execute('SELECT collection_status FROM needies WHERE id = ?', (needy_id,))
                new_status = c.fetchone()[0]
                if new_status == 'completed':
                    return jsonify({
                        'success': False,
                        'error': 'Сбор средств завершен. Новый сбор начнется с 1 числа следующего месяца.'
                    })
            else:
                return jsonify({
                    'success': False,
                    'error': 'Сбор средств завершен. Спасибо за помощь!'
                })

        # Записываем пожертвование
        c.execute('''INSERT INTO donations (user_id, needy_id, amount, is_subscription, created_at)
                     VALUES (?, ?, ?, ?, ?)''',
                  (session['user_id'], needy_id, amount, is_subscription, datetime.now().isoformat()))

        # Обновляем сумму сбора
        c.execute('''UPDATE needies SET funds_collected = funds_collected + ? WHERE id = ?''',
                  (amount, needy_id))

        # Проверяем, не достигнута ли цель
        c.execute('SELECT funds_collected, goal FROM needies WHERE id = ?', (needy_id,))
        updated_needy = c.fetchone()

        if updated_needy[0] >= updated_needy[1]:
            # Отмечаем сбор как завершенный
            c.execute('''UPDATE needies 
                         SET collection_status = 'completed',
                             completed_date = ?
                         WHERE id = ?''',
                      (datetime.now().isoformat(), needy_id))

        # Получаем количество уникальных помощников
        c.execute('SELECT COUNT(DISTINCT user_id) FROM donations WHERE needy_id = ?', (needy_id,))
        helpers_count = c.fetchone()[0] or 0

        # Начисляем рейтинг волонтеру
        c.execute('''UPDATE users SET rating = rating + 0.1 
                     WHERE id = ? AND role = 'volunteer' ''', (session['user_id'],))

        conn.commit()

        # Получаем финальные данные
        c.execute('''SELECT funds_collected, goal, collection_status, 
                            (SELECT COUNT(DISTINCT user_id) FROM donations WHERE needy_id = ?) as helpers
                     FROM needies WHERE id = ?''', (needy_id, needy_id))
        final_data = c.fetchone()

        return jsonify({
            'success': True,
            'funds_collected': final_data[0],
            'goal': final_data[1],
            'helpers_count': final_data[3],
            'status': final_data[2],
            'message': 'Спасибо за помощь! ❤️',
            'badge_earned': check_and_award_badges(session['user_id'])
        })


@app.route('/organization/<int:org_id>')
def organization_profile(org_id):
    """Публичный профиль организации"""
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()

        # Информация об организации
        c.execute('''SELECT id, name, email, phone, photo, organization_description,
                            rating, completed_tasks, created_at, is_verified
                     FROM users WHERE id = ? AND role = 'organization' ''', (org_id,))
        org_data = c.fetchone()

        if not org_data:
            flash('Организация не найдена')
            return redirect(url_for('index'))

        org = {
            'id': org_data[0],
            'name': org_data[1],
            'email': org_data[2],
            'phone': org_data[3],
            'photo': org_data[4],
            'description': org_data[5],
            'rating': org_data[6],
            'completed_tasks': org_data[7],
            'created_at': org_data[8],
            'is_verified': org_data[9]
        }

        # Активные нуждающиеся организации
        c.execute('''SELECT n.id, n.name, n.photo, n.description, n.funds_collected, n.goal,
                            n.collection_status, n.help_info, n.urgency_level, n.created_at,
                            (SELECT COUNT(DISTINCT user_id) FROM donations WHERE needy_id = n.id) as helpers_count
                     FROM needies n
                     WHERE n.organization_id = ? AND n.status = 'approved' AND n.is_active = 1
                     ORDER BY 
                        CASE n.collection_status 
                            WHEN 'active' THEN 1 
                            WHEN 'completed' THEN 2 
                        END,
                        n.urgency_level ASC,
                        n.created_at DESC''', (org_id,))
        active_needies = c.fetchall()

        # Завершенные нуждающиеся
        c.execute('''SELECT id, name, photo, funds_collected, goal, completed_date, report_created
                     FROM needies 
                     WHERE organization_id = ? AND collection_status = 'completed'
                     ORDER BY completed_date DESC
                     LIMIT 10''', (org_id,))
        completed_needies = c.fetchall()

        # Статистика организации
        c.execute('''SELECT 
                        COUNT(DISTINCT n.id) as total_needies,
                        COALESCE(SUM(n.funds_collected), 0) as total_funds,
                        COUNT(DISTINCT d.user_id) as total_donors,
                        (SELECT COUNT(*) FROM reports WHERE user_id = ? AND status = 'approved') as total_reports
                     FROM needies n
                     LEFT JOIN donations d ON n.id = d.needy_id
                     WHERE n.organization_id = ?''', (org_id, org_id))
        stats_data = c.fetchone()

        stats = {
            'total_needies': stats_data[0] or 0,
            'total_funds': stats_data[1] or 0,
            'total_donors': stats_data[2] or 0,
            'total_reports': stats_data[3] or 0
        }

        # Получаем последние отчеты
        c.execute('''SELECT r.id, r.text, r.photo, r.created_at, n.name as needy_name, n.id as needy_id
                     FROM reports r
                     JOIN needies n ON r.needy_id = n.id
                     WHERE r.user_id = ? AND r.status = 'approved'
                     ORDER BY r.created_at DESC
                     LIMIT 5''', (org_id,))
        reports = c.fetchall()

        # Получаем достижения организации
        c.execute('''SELECT badge_name, badge_icon, earned_date 
                     FROM badges 
                     WHERE user_id = ?
                     ORDER BY earned_date DESC''', (org_id,))
        badges = c.fetchall()

        return render_template('organization_profile.html',
                               org=org,
                               active_needies=active_needies,
                               completed_needies=completed_needies,
                               stats=stats,
                               reports=reports,
                               badges=badges)


@app.route('/create_report/<int:needy_id>', methods=['GET', 'POST'])
def create_report(needy_id):
    """Создание отчета о расходовании средств (только для организации)"""
    if 'user_id' not in session:
        return redirect(url_for('login'))

    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()

        # Проверяем права
        c.execute('SELECT role FROM users WHERE id = ?', (session['user_id'],))
        user = c.fetchone()
        if user[0] not in ['organization', 'admin']:
            flash('Только организации могут создавать отчеты')
            return redirect(url_for('needy_profile', id=needy_id))

        # Проверяем, что нуждающийся принадлежит этой организации
        c.execute('''SELECT n.id, n.name, n.photo, n.funds_collected, n.goal, n.help_info,
                                    n.collection_status, n.organization_id, n.report_created
                             FROM needies n
                             WHERE n.id = ?''', (needy_id,))
        needy = c.fetchone()

        if not needy:
            flash('Нуждающийся не найден')
            return redirect(url_for('dashboard'))

        # Проверяем, что организация имеет право создавать отчет
        if needy[7] != session['user_id'] and user[0] != 'admin':
            flash('Вы можете создавать отчеты только для своих подопечных')
            return redirect(url_for('needy_profile', id=needy_id))

        # Проверяем, что сбор завершен
        if needy[6] != 'completed':
            flash('Отчет можно создать только после полного сбора средств')
            return redirect(url_for('needy_profile', id=needy_id))

        if needy[8]:
            flash('Отчет для этого нуждающегося уже создан')
            return redirect(url_for('needy_profile', id=needy_id))

        needy_data = {
            'id': needy[0],
            'name': needy[1],
            'photo': needy[2],
            'collected': needy[3],
            'goal': needy[4],
            'help_info': needy[5]
        }

        if request.method == 'POST':
            text = request.form.get('text', '').strip()
            expenses_breakdown = request.form.get('expenses_breakdown', '')

            # Валидация
            if len(text) < 50:
                flash('Пожалуйста, напишите более подробный отчет (минимум 50 символов)')
                return render_template('create_report.html', needy=needy)

            # Обработка фото
            photos = []
            if 'photos' in request.files:
                files = request.files.getlist('photos')
                for f in files[:5]:  # Максимум 5 фото
                    if f and f.filename:
                        filename = secure_filename(f"{datetime.now().timestamp()}_{f.filename}")
                        photo_path = os.path.join(UPLOAD_FOLDER, filename)
                        f.save(photo_path)
                        photos.append(photo_path)

            if not photos:
                flash('Добавьте хотя бы одну фотографию')
                return render_template('create_report.html', needy=needy)

            photos_str = ','.join(photos)

            # Создаем отчет
            c.execute('''INSERT INTO reports 
                                (user_id, needy_id, photo, text, status, created_at)
                                VALUES (?, ?, ?, ?, 'pending', ?)''',
                      (session['user_id'], needy_id, photos_str, text,
                       datetime.now().isoformat()))
            report_id = c.lastrowid

            # Сохраняем детализацию расходов
            if expenses_breakdown:
                c.execute('''INSERT INTO report_expenses 
                                    (report_id, breakdown, total_amount, created_at)
                                    VALUES (?, ?, ?, ?)''',
                          (report_id, expenses_breakdown, needy['collected'],
                           datetime.now().isoformat()))

            # Обновляем статус нуждающегося
            c.execute('''UPDATE needies 
                                SET report_created = 1,
                                    report_id = ?
                                WHERE id = ?''', (report_id, needy_id))

            # Обновляем счетчик выполненных задач у организации
            c.execute('''UPDATE users 
                                SET completed_tasks = COALESCE(completed_tasks, 0) + 1
                                WHERE id = ?''', (session['user_id'],))

            # Начисляем рейтинг
            c.execute('''UPDATE users 
                                SET rating = COALESCE(rating, 0) + 0.5
                                WHERE id = ?''', (session['user_id'],))

            conn.commit()

            # Создаем уведомления для всех жертвователей
            c.execute('''SELECT DISTINCT user_id FROM donations WHERE needy_id = ?''', (needy_id,))
            donors = c.fetchall()

            for (donor_id,) in donors:
                create_notification(
                    donor_id,
                    'report_created',
                    '📊 Новый отчет',
                    f'Опубликован отчет о расходовании средств для {needy["name"]}',
                    url_for('needy_reports', needy_id=needy_id)
                )

            # Проверяем достижения
            check_organization_badges(session['user_id'])

            flash('Отчет успешно создан и отправлен на проверку!', 'success')
            return redirect(url_for('dashboard'))

        return render_template('create_report.html', needy=needy)


#административные маршруты

@app.route('/admin/users')
def admin_users():
    if 'user_id' not in session:
        return redirect(url_for('login'))

    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        c.execute('SELECT role FROM users WHERE id = ?', (session['user_id'],))
        user_role = c.fetchone()

        if not user_role or user_role[0] != 'admin':
            flash('Доступ запрещен')
            return redirect(url_for('index'))

        # Получаем всех пользователей, кроме админов? включаем всех
        c.execute('''SELECT id, name, email, phone, role, is_verified, rating, 
                            completed_tasks, created_at
                     FROM users 
                     ORDER BY created_at DESC''')
        users_data = c.fetchall()

        users = []
        for u in users_data:
            users.append({
                'id': u[0],
                'name': u[1],
                'email': u[2],
                'phone': u[3],
                'role': u[4],
                'is_verified': u[5],
                'rating': u[6],
                'completed_tasks': u[7],
                'created_at': u[8]
            })

        return render_template('admin/users.html', users=users, active_tab='users')


@app.route('/admin/organizations')
def admin_organizations():
    if 'user_id' not in session:
        return redirect(url_for('login'))

    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        c.execute('SELECT role FROM users WHERE id = ?', (session['user_id'],))
        user_role = c.fetchone()

        if not user_role or user_role[0] != 'admin':
            flash('Доступ запрещен')
            return redirect(url_for('index'))

        # Получаем неподтвержденные организации
        c.execute('''SELECT id, name, email, phone, organization_description, 
                            photo, created_at
                     FROM users 
                     WHERE role = 'organization' AND is_verified = 0
                     ORDER BY created_at DESC''')
        orgs_data = c.fetchall()

        organizations = []
        for o in orgs_data:
            organizations.append({
                'id': o[0],
                'name': o[1],
                'email': o[2],
                'phone': o[3],
                'organization_description': o[4],
                'photo': o[5],
                'created_at': o[6]
            })

        return render_template('admin/organizations.html', organizations=organizations, active_tab='organizations')


@app.route('/admin/needies')
def admin_needies():
    if 'user_id' not in session:
        return redirect(url_for('login'))

    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        c.execute('SELECT role FROM users WHERE id = ?', (session['user_id'],))
        user_role = c.fetchone()

        if not user_role or user_role[0] != 'admin':
            flash('Доступ запрещен')
            return redirect(url_for('index'))

        # Получаем неподтвержденных нуждающихся
        c.execute('''SELECT n.id, n.name, n.tag, n.photo, n.description, n.address, 
                            n.urgency_level, n.created_at, n.organization_id, u.name as org_name
                     FROM needies n
                     LEFT JOIN users u ON n.organization_id = u.id
                     WHERE n.status = 'pending'
                     ORDER BY n.created_at DESC''')
        needies_data = c.fetchall()

        needies = []
        for n in needies_data:
            needies.append({
                'id': n[0],
                'name': n[1],
                'tag': n[2],
                'photo': n[3],
                'description': n[4],
                'address': n[5],
                'urgency_level': n[6],
                'created_at': n[7],
                'organization_id': n[8],
                'org_name': n[9] if n[9] else 'Не указана'
            })

        return render_template('admin/needies.html', needies=needies, active_tab='needies')


@app.route('/admin/verify_organization/<int:org_id>')
def verify_organization(org_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))

    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        c.execute('SELECT role FROM users WHERE id = ?', (session['user_id'],))
        user_role = c.fetchone()

        if not user_role or user_role[0] != 'admin':
            flash('Доступ запрещен')
            return redirect(url_for('index'))

        c.execute('UPDATE users SET is_verified = 1 WHERE id = ? AND role = "organization"', (org_id,))
        conn.commit()

        flash('Организация подтверждена')
        return redirect(url_for('admin_organizations'))


@app.route('/admin/reject_organization/<int:org_id>')
def reject_organization(org_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))

    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        c.execute('SELECT role FROM users WHERE id = ?', (session['user_id'],))
        user_role = c.fetchone()

        if not user_role or user_role[0] != 'admin':
            flash('Доступ запрещен')
            return redirect(url_for('index'))

        # Удаляем организацию (или помечаем как отклоненную)
        c.execute('DELETE FROM users WHERE id = ? AND role = "organization" AND is_verified = 0', (org_id,))
        conn.commit()

        flash('Заявка организации отклонена и удалена')
        return redirect(url_for('admin_organizations'))


@app.route('/admin/approve_needy/<int:needy_id>')
def approve_needy(needy_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))

    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        c.execute('SELECT role FROM users WHERE id = ?', (session['user_id'],))
        user_role = c.fetchone()

        if not user_role or user_role[0] != 'admin':
            flash('Доступ запрещен')
            return redirect(url_for('index'))

        c.execute('UPDATE needies SET status = "approved" WHERE id = ?', (needy_id,))
        conn.commit()

        flash('Нуждающийся одобрен и опубликован')
        return redirect(url_for('admin_needies'))


@app.route('/admin/reject_needy/<int:needy_id>')
def reject_needy(needy_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))

    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        c.execute('SELECT role FROM users WHERE id = ?', (session['user_id'],))
        user_role = c.fetchone()

        if not user_role or user_role[0] != 'admin':
            flash('Доступ запрещен')
            return redirect(url_for('index'))

        c.execute('DELETE FROM needies WHERE id = ? AND status = "pending"', (needy_id,))
        conn.commit()

        flash('Заявка нуждающегося отклонена и удалена')
        return redirect(url_for('admin_needies'))


@app.route('/admin/user/<int:user_id>')
def admin_user_detail(user_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))

    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        c.execute('SELECT role FROM users WHERE id = ?', (session['user_id'],))
        admin_role = c.fetchone()

        if not admin_role or admin_role[0] != 'admin':
            flash('Доступ запрещен')
            return redirect(url_for('index'))

        # Получаем информацию о пользователе
        c.execute('''SELECT id, name, email, phone, role, is_verified, rating, 
                            completed_tasks, photo, created_at, last_login, 
                            organization_description
                     FROM users WHERE id = ?''', (user_id,))
        user_data = c.fetchone()

        if not user_data:
            flash('Пользователь не найден')
            return redirect(url_for('admin_users'))

        user = {
            'id': user_data[0],
            'name': user_data[1],
            'email': user_data[2],
            'phone': user_data[3],
            'role': user_data[4],
            'is_verified': user_data[5],
            'rating': user_data[6],
            'completed_tasks': user_data[7],
            'photo': user_data[8],
            'created_at': user_data[9],
            'last_login': user_data[10],
            'organization_description': user_data[11]
        }

        # Если это организация, получаем добавленных нуждающихся
        needies = []
        if user['role'] == 'organization':
            c.execute('''SELECT id, name, address, status 
                         FROM needies 
                         WHERE organization_id = ?
                         ORDER BY created_at DESC''', (user_id,))
            needies_data = c.fetchall()
            for n in needies_data:
                needies.append({
                    'id': n[0],
                    'name': n[1],
                    'address': n[2],
                    'status': n[3]
                })

        return render_template('admin/user_detail.html', user=user, needies=needies)


@app.route('/admin/delete_user/<int:user_id>')
def delete_user(user_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))

    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        c.execute('SELECT role FROM users WHERE id = ?', (session['user_id'],))
        admin_role = c.fetchone()

        if not admin_role or admin_role[0] != 'admin':
            flash('Доступ запрещен')
            return redirect(url_for('index'))

        # Нельзя удалить админа
        c.execute('SELECT role FROM users WHERE id = ?', (user_id,))
        user_role = c.fetchone()

        if user_role and user_role[0] == 'admin':
            flash('Нельзя удалить администратора')
            return redirect(url_for('admin_users'))

        # Удаляем пользователя
        c.execute('DELETE FROM users WHERE id = ?', (user_id,))
        conn.commit()

        flash('Пользователь удален')
        return redirect(url_for('admin_users'))


@app.route('/admin/reports')
def admin_reports():
    """Страница администрирования отчетов"""
    if 'user_id' not in session:
        return redirect(url_for('login'))

    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        c.execute('SELECT role FROM users WHERE id = ?', (session['user_id'],))
        if c.fetchone()[0] != 'admin':
            flash('Доступ запрещен')
            return redirect(url_for('index'))

        # Получаем отчеты на проверке
        c.execute('''SELECT r.id, r.text, r.photo, r.created_at, r.status,
                            u.name as org_name, u.id as org_id,
                            n.name as needy_name, n.id as needy_id
                     FROM reports r
                     JOIN users u ON r.user_id = u.id
                     JOIN needies n ON r.needy_id = n.id
                     WHERE r.status = 'pending'
                     ORDER BY r.created_at DESC''')
        pending_reports = c.fetchall()

        # Получаем одобренные отчеты
        c.execute('''SELECT r.id, r.text, r.photo, r.created_at, r.status,
                            u.name as org_name, u.id as org_id,
                            n.name as needy_name, n.id as needy_id
                     FROM reports r
                     JOIN users u ON r.user_id = u.id
                     JOIN needies n ON r.needy_id = n.id
                     WHERE r.status = 'approved'
                     ORDER BY r.created_at DESC
                     LIMIT 20''')
        approved_reports = c.fetchall()

        return render_template('admin/reports.html',
                               pending_reports=pending_reports,
                               approved_reports=approved_reports)


@app.route('/admin/approve_report/<int:report_id>')
def approve_report(report_id):
    """Одобрение отчета администратором"""
    if 'user_id' not in session:
        return redirect(url_for('login'))

    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()

        # Проверяем права админа
        c.execute('SELECT role FROM users WHERE id = ?', (session['user_id'],))
        if c.fetchone()[0] != 'admin':
            flash('Доступ запрещен')
            return redirect(url_for('index'))

        # Получаем информацию об отчете
        c.execute('''SELECT r.needy_id, r.user_id, u.name 
                     FROM reports r
                     JOIN users u ON r.user_id = u.id
                     WHERE r.id = ?''', (report_id,))
        report_info = c.fetchone()

        # Одобряем отчет
        c.execute('''UPDATE reports 
                     SET status = 'approved',
                         approved_by = ?,
                         approved_at = ?
                     WHERE id = ?''',
                  (session['user_id'], datetime.now().isoformat(), report_id))

        # Начисляем рейтинг организации
        c.execute('''UPDATE users 
                     SET rating = rating + 0.5
                     WHERE id = ?''', (report_info[1],))

        conn.commit()

        # Уведомляем организацию
        create_notification(
            report_info[1],
            'report_approved',
            '✅ Отчет одобрен',
            f'Ваш отчет для нуждающегося одобрен администратором',
            url_for('needy_reports', needy_id=report_info[0])
        )

        flash('Отчет одобрен')
        return redirect(url_for('admin_reports'))



@app.route('/update_profile', methods=['POST'])
def update_profile():
    if 'user_id' not in session:
        return redirect(url_for('login'))

    user_id = session['user_id']
    name = request.form.get('name')
    phone = request.form.get('phone')
    new_password = request.form.get('new_password')
    organization_description = request.form.get('organization_description')

    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()

        # Обновляем имя и телефон
        updates = []
        params = []

        updates.append("name = ?")
        params.append(name)

        updates.append("phone = ?")
        params.append(phone)

        # Обновляем описание для организации
        if organization_description is not None:
            updates.append("organization_description = ?")
            params.append(organization_description)

        # Обновляем пароль, если указан
        if new_password:
            from werkzeug.security import generate_password_hash
            updates.append("password = ?")
            params.append(generate_password_hash(new_password))

        # Обновляем фото, если загружено
        if 'photo' in request.files:
            f = request.files['photo']
            if f and f.filename:
                from werkzeug.utils import secure_filename
                filename = secure_filename(f.filename)
                photo_path = os.path.join(UPLOAD_FOLDER, filename)
                f.save(photo_path)
                updates.append("photo = ?")
                params.append(photo_path)

        params.append(user_id)

        if updates:
            query = f"UPDATE users SET {', '.join(updates)} WHERE id = ?"
            c.execute(query, params)
            conn.commit()

        flash('Профиль успешно обновлен!', 'success')

    return redirect(url_for('dashboard'))


@app.route('/cron/reset-collections')
def reset_collections():
    """Эндпоинт для cron-задачи (вызывать 1 числа каждого месяца)"""
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()

        # Находим все завершенные сборы с продуктовыми корзинами
        c.execute('''SELECT id FROM needies 
                     WHERE collection_status = 'completed' 
                     AND (help_info LIKE '%продуктовая%' OR help_info LIKE '%корзина%')''')

        reset_count = 0
        for (needy_id,) in c.fetchall():
            c.execute('''UPDATE needies 
                         SET funds_collected = 0,
                             collection_status = 'active',
                             last_reset_date = ?
                         WHERE id = ?''',
                      (datetime.now().isoformat(), needy_id))
            reset_count += 1

        conn.commit()

        return jsonify({
            'success': True,
            'reset_count': reset_count,
            'message': f'Сброшено {reset_count} сборов'
        })


@app.route('/notifications')
def notifications():
    """Страница уведомлений пользователя"""
    if 'user_id' not in session:
        return redirect(url_for('login'))

    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        c.execute('''SELECT id, type, title, message, link, is_read, created_at
                     FROM notifications 
                     WHERE user_id = ?
                     ORDER BY created_at DESC
                     LIMIT 50''', (session['user_id'],))
        notifications_list = c.fetchall()

        # Отмечаем как прочитанные
        c.execute('''UPDATE notifications SET is_read = 1 
                     WHERE user_id = ? AND is_read = 0''', (session['user_id'],))
        conn.commit()

    return render_template('notifications.html', notifications=notifications_list)


@app.route('/api/notifications/count')
def notifications_count():
    """API для получения количества непрочитанных уведомлений"""
    if 'user_id' not in session:
        return jsonify({'count': 0})

    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        c.execute('''SELECT COUNT(*) FROM notifications 
                     WHERE user_id = ? AND is_read = 0''', (session['user_id'],))
        count = c.fetchone()[0]

    return jsonify({'count': count})


@app.route('/api/needy/<int:needy_id>/subscribe', methods=['POST'])
def subscribe_to_needy(needy_id):
    """Подписка на обновления нуждающегося"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': 'Unauthorized'}), 401

    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()

        try:
            c.execute('''INSERT INTO needy_subscriptions (user_id, needy_id, created_at)
                        VALUES (?, ?, ?)''',
                      (session['user_id'], needy_id, datetime.now().isoformat()))
            conn.commit()

            create_notification(
                session['user_id'],
                'subscription',
                '🔔 Подписка оформлена',
                'Вы будете получать уведомления о новых отчетах',
                url_for('needy_profile', id=needy_id)
            )

            return jsonify({'success': True, 'message': 'Подписка оформлена'})
        except sqlite3.IntegrityError:
            return jsonify({'success': False, 'error': 'Вы уже подписаны'})

# --- Запуск ---
if __name__ == '__main__':
    init_db()
    app.run(debug=True)
