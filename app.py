import jsonify as jsonify
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

        conn.commit()




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

        # Получаем активные задачи (в процессе или ожидающие)
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

        completed_tasks_count = user_data[7] if user_data[7] else 0
        user_rating = user_data[6] if user_data[6] else 0
        is_verified = user_data[9] if user_data[9] else 0

        return render_template('dashboard.html',
                               user_id=user_data[0],
                               user_name=user_data[1],
                               user_email=user_data[2],
                               user_phone=user_data[3],
                               user_role=user_data[4],
                               user_photo=user_data[5],
                               user_rating=user_rating,
                               completed_tasks=completed_tasks_count,
                               in_progress_tasks=in_progress_count,
                               created_at=user_data[8],
                               is_verified=is_verified,
                               organization_description=user_data[10] if len(user_data) > 10 else None,
                               reports=reports,
                               active_tasks=active_tasks)

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
        c.execute('SELECT * FROM needies WHERE id = ?', (id,))
        needy = c.fetchone()
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
            help_info = request.form['help_info']
            urgency_level = int(request.form['urgency_level'])

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

            c.execute('''INSERT INTO needies (name, tag, photo, description, address, lat, lng, 
                                             help_info, organization_id, created_by, created_at, 
                                             urgency_level, status)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                      (name, tag, photo, description, address, lat, lng, help_info,
                       session['user_id'], session['user_id'], datetime.now().isoformat(),
                       urgency_level, status))
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
        # Записываем пожертвование
        c.execute('''INSERT INTO donations (user_id, needy_id, amount, is_subscription, created_at)
                     VALUES (?, ?, ?, ?, ?)''',
                  (session['user_id'], needy_id, amount, is_subscription, datetime.now().isoformat()))

        # Обновляем сумму сбора
        c.execute('''UPDATE needies SET funds_collected = funds_collected + ? WHERE id = ?''', (amount, needy_id))

        # Получаем обновленные данные
        c.execute('SELECT funds_collected, goal FROM needies WHERE id = ?', (needy_id,))
        needy_data = c.fetchone()

        # Получаем количество уникальных помощников (уникальные user_id в donations)
        c.execute('SELECT COUNT(DISTINCT user_id) FROM donations WHERE needy_id = ?', (needy_id,))
        helpers_count = c.fetchone()[0] or 0

        conn.commit()

        # Для AJAX запроса возвращаем JSON
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.form.get('ajax'):
            return jsonify({
                'success': True,
                'funds_collected': needy_data[0],
                'goal': needy_data[1],
                'helpers_count': helpers_count
            })

    flash('Спасибо за помощь!')
    return redirect(url_for('needy_profile', id=needy_id))

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

# --- Запуск ---
if __name__ == '__main__':
    init_db()
    app.run(debug=True)
