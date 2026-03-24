from flask import Flask, render_template, redirect, url_for, request, session, flash, send_from_directory
from werkzeug.security import generate_password_hash, check_password_hash
import sqlite3
import os
import requests
from datetime import datetime

app = Flask(__name__)
app.secret_key = 'your_secret_key_here'
UPLOAD_FOLDER = 'static/uploads'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
DB_NAME = 'volunteer.db'


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
            phone TEXT,
            city TEXT,
            role TEXT DEFAULT 'volunteer',  -- admin, volunteer, organization
            photo TEXT,
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

        conn.commit()


# --- Регистрация ---
@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        name = request.form['name']
        email = request.form['email']
        password = generate_password_hash(request.form['password'])
        photo = None
        if 'photo' in request.files:
            f = request.files['photo']
            if f.filename:
                photo = os.path.join(UPLOAD_FOLDER, f.filename)
                f.save(photo)
        with sqlite3.connect(DB_NAME) as conn:
            try:
                conn.execute('INSERT INTO users (name, email, password, photo) VALUES (?, ?, ?, ?)',
                             (name, email, password, photo))
                conn.commit()
                flash('Registered successfully!')
                return redirect(url_for('login'))
            except sqlite3.IntegrityError:
                flash('Email already used.')
    return render_template('register.html')


# --- Авторизация ---
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
                return redirect(url_for('dashboard'))
            else:
                flash('Invalid login.')
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
        c.execute('SELECT name, photo FROM users WHERE id = ?', (session['user_id'],))
        user = c.fetchone()
        c.execute('''SELECT n.name, r.text, r.photo FROM reports r
                     JOIN needies n ON n.id = r.needy_id
                     WHERE r.user_id = ?''', (session['user_id'],))
        reports = c.fetchall()
    return render_template('dashboard.html', user=user, reports=reports)


@app.route('/needies')
def needies_list():
    view = request.args.get('view', 'list')

    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        c.execute('SELECT id, name, tag, photo, description, address, lat, lng FROM needies')
        needies = c.fetchall()

    if view == 'map':
        map_data = []
        for needy in needies:
            if not needy[6] or not needy[7]:
                lat, lng = geocode_address(needy[5])
                with sqlite3.connect(DB_NAME) as conn:
                    conn.execute('UPDATE needies SET lat=?, lng=? WHERE id=?',
                                 (lat, lng, needy[0]))
                    conn.commit()
                map_data.append({
                    **dict(zip(['id', 'name', 'tag', 'photo', 'description', 'address', 'lat', 'lng'], needy)),
                    'lat': lat,
                    'lng': lng
                })
            else:
                map_data.append(
                    dict(zip(['id', 'name', 'tag', 'photo', 'description', 'address', 'lat', 'lng'], needy)))

        return render_template('needies_map.html', needies=map_data)

    return render_template('needies_list.html', needies=needies)


# --- Список нуждающихся ---
@app.route('/needy/<int:id>')
def needy_profile(id):
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        c.execute('SELECT * FROM needies WHERE id = ?', (id,))
        needy = c.fetchone()
    return render_template('needy_profile.html', needy=needy)


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


# --- Платежи (заглушка) ---
@app.route('/donate/<int:needy_id>', methods=['POST'])
def donate(needy_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))
    amount = float(request.form['amount'])
    is_subscription = int(request.form.get('subscription', 0))
    with sqlite3.connect(DB_NAME) as conn:
        conn.execute('''INSERT INTO donations (user_id, needy_id, amount, is_subscription, created_at)
                        VALUES (?, ?, ?, ?, ?)''',
                     (session['user_id'], needy_id, amount, is_subscription, datetime.now().isoformat()))
        conn.execute('''UPDATE needies SET funds_collected = funds_collected + ? WHERE id = ?''', (amount, needy_id))
        conn.commit()
    flash('Спасибо за помощь!')
    return redirect(url_for('needy_profile', id=needy_id))


# --- Запуск ---
if __name__ == '__main__':
    init_db()
    app.run(debug=True)
