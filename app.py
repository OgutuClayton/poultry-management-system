from flask import Flask, render_template, request, redirect, url_for, session, make_response
from flask_mysqldb import MySQL
import MySQLdb.cursors
from flask_mail import Mail, Message
from threading import Thread
from io import BytesIO
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet
from datetime import date

app = Flask(__name__)

# ─── SECRET KEY ───────────────────────────────────────────
app.secret_key = 'poultry_secret_key_2024'

# ─── MYSQL CONFIGURATION ──────────────────────────────────
app.config['MYSQL_HOST'] = 'localhost'
app.config['MYSQL_USER'] = 'root'
app.config['MYSQL_PASSWORD'] = ''
app.config['MYSQL_DB'] = 'poultry_db'
mysql = MySQL(app)

# ─── MAIL CONFIGURATION ───────────────────────────────────
app.config['MAIL_SERVER'] = 'smtp.gmail.com'
app.config['MAIL_PORT'] = 587
app.config['MAIL_USE_TLS'] = True
app.config['MAIL_USERNAME'] = 'example@gmail.com'
app.config['MAIL_PASSWORD'] = '**** **** **** ****'
app.config['MAIL_DEFAULT_SENDER'] = 'example@gmail.com'
mail = Mail(app)

# ─── EMAIL HELPER ─────────────────────────────────────────
def send_alert_email(subject, body, recipients):
    try:
        if isinstance(recipients, str):
            recipients = [recipients]
        msg = Message(subject, recipients=recipients)
        msg.body = body
        mail.send(msg)
        print(f"Email sent successfully to {recipients}")
    except Exception as e:
        print(f"Email error: {e}")

# ─── AUDIT TRAIL ──────────────────────────────────────────
def log_audit(action, table_name, record_id, old_value=None, new_value=None):
    try:
        username = session.get('username', 'system')
        ip = request.remote_addr
        cursor = mysql.connection.cursor()
        cursor.execute('''INSERT INTO audit_log
            (username, action, table_name, record_id, old_value, new_value, ip_address)
            VALUES (%s,%s,%s,%s,%s,%s,%s)''',
            (username, action, table_name, record_id,
             str(old_value) if old_value else None,
             str(new_value) if new_value else None,
             ip))
        mysql.connection.commit()
    except Exception as e:
        print(f"Audit log error: {e}")

# ─── LOGIN ────────────────────────────────────────────────
@app.route('/', methods=['GET', 'POST'])
def login():
    error = None
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        cursor = mysql.connection.cursor(MySQLdb.cursors.DictCursor)
        cursor.execute('SELECT * FROM users WHERE username = %s AND password = %s', (username, password))
        user = cursor.fetchone()
        if user:
            session['loggedin'] = True
            session['username'] = user['username']
            session['role'] = user['role']
            return redirect(url_for('dashboard'))
        else:
            error = 'Invalid username or password!'
    return render_template('login.html', error=error)

# ─── LOGOUT ───────────────────────────────────────────────
@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

# ─── REGISTER ─────────────────────────────────────────────
@app.route('/register', methods=['GET', 'POST'])
def register():
    if 'loggedin' not in session or session.get('role') != 'admin':
        return redirect(url_for('dashboard'))
    error = None
    success = None
    if request.method == 'POST':
        import re
        username = request.form['username']
        password = request.form['password']
        role = request.form['role']
        if len(password) < 8:
            error = 'Password must be at least 8 characters!'
        elif not re.search(r'[A-Z]', password):
            error = 'Password must contain at least one uppercase letter!'
        elif not re.search(r'[a-z]', password):
            error = 'Password must contain at least one lowercase letter!'
        elif not re.search(r'[0-9]', password):
            error = 'Password must contain at least one number!'
        elif not re.search(r'[!@#$%^&*(),.?":{}|<>]', password):
            error = 'Password must contain at least one special character!'
        else:
            cursor = mysql.connection.cursor(MySQLdb.cursors.DictCursor)
            cursor.execute('SELECT * FROM users WHERE username = %s', (username,))
            existing = cursor.fetchone()
            if existing:
                error = 'Username already exists!'
            else:
                cursor.execute('INSERT INTO users (username, password, role) VALUES (%s, %s, %s)',
                               (username, password, role))
                mysql.connection.commit()
                success = f'User {username} created successfully!'
    cursor = mysql.connection.cursor(MySQLdb.cursors.DictCursor)
    cursor.execute('SELECT * FROM users')
    users = cursor.fetchall()
    return render_template('register.html', error=error, success=success, users=users)

# ─── DELETE USER ──────────────────────────────────────────
@app.route('/users/delete/<int:user_id>')
def delete_user(user_id):
    if 'loggedin' not in session or session.get('role') != 'admin':
        return redirect(url_for('dashboard'))
    cursor = mysql.connection.cursor()
    cursor.execute('DELETE FROM users WHERE id = %s AND username != "admin"', (user_id,))
    mysql.connection.commit()
    return redirect(url_for('register'))

# ─── DASHBOARD ────────────────────────────────────────────
@app.route('/dashboard')
def dashboard():
    if 'loggedin' not in session:
        return redirect(url_for('login'))
    cursor = mysql.connection.cursor(MySQLdb.cursors.DictCursor)
    cursor.execute('SELECT * FROM flocks WHERE status = "Active"')
    flocks = cursor.fetchall()
    cursor.execute('SELECT SUM(amount) as total FROM transactions WHERE type = "Revenue"')
    revenue = cursor.fetchone()['total'] or 0
    cursor.execute('SELECT SUM(amount) as total FROM transactions WHERE type = "Expense"')
    expenses = cursor.fetchone()['total'] or 0
    cursor.execute('SELECT SUM(eggs_collected) as total FROM daily_logs')
    eggs = cursor.fetchone()['total'] or 0
    cursor.execute('''SELECT dl.*, f.name as flock_name
                      FROM daily_logs dl JOIN flocks f ON dl.flock_id = f.id
                      ORDER BY dl.log_date DESC LIMIT 5''')
    recent_logs = cursor.fetchall()
    cursor.execute('SELECT * FROM inventory WHERE quantity <= reorder_level')
    low_stock = cursor.fetchall()
    alerts = []
    for flock in flocks:
        cursor.execute('SELECT SUM(mortality) as dead FROM daily_logs WHERE flock_id = %s', (flock['id'],))
        dead = cursor.fetchone()['dead'] or 0
        if flock['initial_count'] > 0:
            rate = (dead / flock['initial_count']) * 100
            if rate > 5:
                alerts.append(f"{flock['name']}: Bird Death Rate at {rate:.1f}% — above safe threshold!")
    for item in low_stock:
        alerts.append(f"Running Low: {item['item_name']} ({item['quantity']} {item['unit']} left)")

    # Chart data — last 7 days feed and mortality
    cursor.execute('''SELECT log_date,
                      SUM(feed_consumed) as total_feed,
                      SUM(mortality) as total_mortality
                      FROM daily_logs
                      GROUP BY log_date
                      ORDER BY log_date DESC
                      LIMIT 7''')
    chart_data = cursor.fetchall()
    chart_data = list(reversed(chart_data))

    chart_labels = [str(row['log_date']) for row in chart_data]
    chart_feed = [float(row['total_feed']) for row in chart_data]
    chart_mortality = [int(row['total_mortality']) for row in chart_data]

    return render_template('dashboard.html',
        flocks=flocks, revenue=revenue, expenses=expenses,
        profit=revenue - expenses, eggs=eggs,
        recent_logs=recent_logs, alerts=alerts,
        chart_labels=chart_labels,
        chart_feed=chart_feed,
        chart_mortality=chart_mortality)

# ─── FLOCKS ───────────────────────────────────────────────
@app.route('/flocks')
def flocks():
    if 'loggedin' not in session:
        return redirect(url_for('login'))
    cursor = mysql.connection.cursor(MySQLdb.cursors.DictCursor)
    cursor.execute('SELECT * FROM flocks')
    all_flocks = cursor.fetchall()
    flock_stats = {}
    for flock in all_flocks:
        cursor.execute('''SELECT SUM(mortality) as dead, SUM(feed_consumed) as feed,
                          MAX(avg_weight) as weight FROM daily_logs WHERE flock_id = %s''', (flock['id'],))
        stats = cursor.fetchone()
        dead = stats['dead'] or 0
        feed = stats['feed'] or 0
        weight = stats['weight'] or 0
        mortality_rate = round((dead / flock['initial_count']) * 100, 1) if flock['initial_count'] > 0 else 0
        feed_efficiency = round(feed / (weight * flock['current_count'] / 1000), 2) if weight > 0 and flock['current_count'] > 0 else 0
        flock_stats[flock['id']] = {
            'mortality_rate': mortality_rate,
            'feed_efficiency': feed_efficiency,
            'total_dead': int(dead)
        }
    today = date.today().strftime('%Y-%m-%d')
    return render_template('flocks.html', flocks=all_flocks, flock_stats=flock_stats, today=today)

# ─── ADD FLOCK ────────────────────────────────────────────
@app.route('/flocks/add', methods=['POST'])
def add_flock():
    if 'loggedin' not in session:
        return redirect(url_for('login'))
    name = request.form['name']
    ftype = request.form['type']
    breed = request.form['breed']
    house = request.form['house']
    start_date = request.form['start_date']
    count = int(request.form['initial_count'])
    cursor = mysql.connection.cursor()
    cursor.execute('''INSERT INTO flocks (name, type, breed, house, start_date, initial_count, current_count)
                      VALUES (%s,%s,%s,%s,%s,%s,%s)''',
                   (name, ftype, breed, house, start_date, count, count))
    mysql.connection.commit()
    log_audit('ADD', 'flocks', cursor.lastrowid,
              new_value=f"Name:{name}, Type:{ftype}, Count:{count}")
    return redirect(url_for('flocks'))

# ─── DELETE FLOCK ─────────────────────────────────────────
@app.route('/flocks/delete/<int:flock_id>')
def delete_flock(flock_id):
    if 'loggedin' not in session:
        return redirect(url_for('login'))
    cursor = mysql.connection.cursor()
    cursor.execute('SELECT * FROM flocks WHERE id = %s', (flock_id,))
    old = cursor.fetchone()
    cursor.execute('DELETE FROM daily_logs WHERE flock_id = %s', (flock_id,))
    cursor.execute('DELETE FROM transactions WHERE flock_id = %s', (flock_id,))
    cursor.execute('DELETE FROM flocks WHERE id = %s', (flock_id,))
    mysql.connection.commit()
    log_audit('DELETE', 'flocks', flock_id, old_value=str(old))
    return redirect(url_for('flocks'))

# ─── DAILY LOGS ───────────────────────────────────────────
@app.route('/logs')
def logs():
    if 'loggedin' not in session:
        return redirect(url_for('login'))
    cursor = mysql.connection.cursor(MySQLdb.cursors.DictCursor)
    cursor.execute('''SELECT dl.*, f.name as flock_name
                      FROM daily_logs dl JOIN flocks f ON dl.flock_id = f.id
                      ORDER BY dl.log_date DESC''')
    all_logs = cursor.fetchall()
    cursor.execute('SELECT * FROM flocks WHERE status="Active"')
    all_flocks = cursor.fetchall()
    today = date.today().strftime('%Y-%m-%d')
    return render_template('logs.html', logs=all_logs, flocks=all_flocks, today=today)

# ─── ADD LOG ──────────────────────────────────────────────
@app.route('/logs/add', methods=['POST'])
def add_log():
    if 'loggedin' not in session:
        return redirect(url_for('login'))
    flock_id = request.form['flock_id']
    log_date = request.form['log_date']
    mortality = int(request.form['mortality'])
    feed = float(request.form['feed_consumed'])
    water = float(request.form['water_intake'])
    weight = float(request.form.get('avg_weight', 0))
    eggs = int(request.form.get('eggs_collected', 0))
    notes = request.form.get('notes', '')
    cursor = mysql.connection.cursor()
    cursor.execute('''INSERT INTO daily_logs
        (flock_id, log_date, mortality, feed_consumed, water_intake, avg_weight, eggs_collected, notes)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s)''',
        (flock_id, log_date, mortality, feed, water, weight, eggs, notes))
    cursor.execute('UPDATE flocks SET current_count = current_count - %s WHERE id = %s',
                   (mortality, flock_id))
    mysql.connection.commit()
    log_audit('ADD', 'daily_logs', cursor.lastrowid,
              new_value=f"Flock:{flock_id}, Date:{log_date}, Mortality:{mortality}, Feed:{feed}")
    cursor2 = mysql.connection.cursor(MySQLdb.cursors.DictCursor)
    cursor2.execute('SELECT * FROM flocks WHERE id = %s', (flock_id,))
    flock = cursor2.fetchone()
    cursor2.execute('SELECT SUM(mortality) as dead FROM daily_logs WHERE flock_id = %s', (flock_id,))
    result = cursor2.fetchone()
    dead = result['dead'] or 0
    if flock and flock['initial_count'] > 0:
        rate = (dead / flock['initial_count']) * 100
        if rate > 5:
            subject = f"ALERT: High Bird Deaths - {flock['name']}"
            email_body = (
                "Farm Alert - Poultry Management System\n\n"
                f"Chicken Group: {flock['name']}\n"
                f"Bird Death Rate: {rate:.1f}%\n"
                f"Total Birds Dead: {int(dead)}\n"
                f"Birds Still Alive: {flock['current_count']}\n\n"
                "This exceeds the safe limit of 5%.\n"
                "Please check on your chickens immediately.\n\n"
                "- PoultryMS Automated Alert"
            )
            send_alert_email(subject, email_body, [
                'claytonogutu@gmail.com',
                'musyokikelvin18@gmail.com',
                'tikiturkey19@gmail.com',
                'otienoheldon03@gmail.com',
                'lavenderchumba5@gmail.com'
            ])
    return redirect(url_for('logs'))

# ─── DELETE LOG ───────────────────────────────────────────
@app.route('/logs/delete/<int:log_id>')
def delete_log(log_id):
    if 'loggedin' not in session:
        return redirect(url_for('login'))
    cursor = mysql.connection.cursor()
    cursor.execute('SELECT * FROM daily_logs WHERE id = %s', (log_id,))
    old = cursor.fetchone()
    cursor.execute('DELETE FROM daily_logs WHERE id = %s', (log_id,))
    mysql.connection.commit()
    log_audit('DELETE', 'daily_logs', log_id, old_value=str(old))
    return redirect(url_for('logs'))

# ─── INVENTORY ────────────────────────────────────────────
@app.route('/inventory')
def inventory():
    if 'loggedin' not in session:
        return redirect(url_for('login'))
    cursor = mysql.connection.cursor(MySQLdb.cursors.DictCursor)
    cursor.execute('SELECT * FROM inventory')
    items = cursor.fetchall()
    return render_template('inventory.html', items=items)

# ─── ADD INVENTORY ────────────────────────────────────────
@app.route('/inventory/add', methods=['POST'])
def add_inventory():
    if 'loggedin' not in session:
        return redirect(url_for('login'))
    name = request.form['item_name']
    category = request.form['category']
    qty = float(request.form['quantity'])
    unit = request.form['unit']
    reorder = float(request.form['reorder_level'])
    cost = float(request.form['cost_per_unit'])
    cursor = mysql.connection.cursor()
    cursor.execute('''INSERT INTO inventory
                      (item_name, category, quantity, unit, reorder_level, cost_per_unit)
                      VALUES (%s,%s,%s,%s,%s,%s)''',
                   (name, category, qty, unit, reorder, cost))
    mysql.connection.commit()
    log_audit('ADD', 'inventory', cursor.lastrowid,
              new_value=f"Item:{name}, Qty:{qty}, Unit:{unit}")
    return redirect(url_for('inventory'))

# ─── DELETE INVENTORY ─────────────────────────────────────
@app.route('/inventory/delete/<int:item_id>')
def delete_inventory(item_id):
    if 'loggedin' not in session:
        return redirect(url_for('login'))
    cursor = mysql.connection.cursor()
    cursor.execute('SELECT * FROM inventory WHERE id = %s', (item_id,))
    old = cursor.fetchone()
    cursor.execute('DELETE FROM inventory WHERE id = %s', (item_id,))
    mysql.connection.commit()
    log_audit('DELETE', 'inventory', item_id, old_value=str(old))
    return redirect(url_for('inventory'))

# ─── FINANCIALS ───────────────────────────────────────────
@app.route('/financials')
def financials():
    if 'loggedin' not in session:
        return redirect(url_for('login'))
    cursor = mysql.connection.cursor(MySQLdb.cursors.DictCursor)
    cursor.execute('''SELECT t.*, f.name as flock_name
                      FROM transactions t LEFT JOIN flocks f ON t.flock_id = f.id
                      ORDER BY t.trans_date DESC''')
    transactions = cursor.fetchall()
    cursor.execute('SELECT * FROM flocks')
    all_flocks = cursor.fetchall()
    cursor.execute('SELECT SUM(amount) as total FROM transactions WHERE type="Revenue"')
    revenue = cursor.fetchone()['total'] or 0
    cursor.execute('SELECT SUM(amount) as total FROM transactions WHERE type="Expense"')
    expenses = cursor.fetchone()['total'] or 0
    today = date.today().strftime('%Y-%m-%d')
    return render_template('financials.html',
        transactions=transactions, flocks=all_flocks,
        revenue=revenue, expenses=expenses,
        profit=revenue - expenses, today=today)

# ─── ADD TRANSACTION ──────────────────────────────────────
@app.route('/financials/add', methods=['POST'])
def add_transaction():
    if 'loggedin' not in session:
        return redirect(url_for('login'))
    flock_id = request.form.get('flock_id') or None
    trans_date = request.form['trans_date']
    ttype = request.form['type']
    category = request.form['category']
    amount = float(request.form['amount'])
    description = request.form.get('description', '')
    cursor = mysql.connection.cursor()
    cursor.execute('''INSERT INTO transactions
                      (flock_id, trans_date, type, category, amount, description)
                      VALUES (%s,%s,%s,%s,%s,%s)''',
                   (flock_id, trans_date, ttype, category, amount, description))
    mysql.connection.commit()
    log_audit('ADD', 'transactions', cursor.lastrowid,
              new_value=f"Type:{ttype}, Amount:{amount}, Category:{category}")
    return redirect(url_for('financials'))

# ─── DELETE TRANSACTION ───────────────────────────────────
@app.route('/transactions/delete/<int:tx_id>')
def delete_transaction(tx_id):
    if 'loggedin' not in session:
        return redirect(url_for('login'))
    cursor = mysql.connection.cursor()
    cursor.execute('SELECT * FROM transactions WHERE id = %s', (tx_id,))
    old = cursor.fetchone()
    cursor.execute('DELETE FROM transactions WHERE id = %s', (tx_id,))
    mysql.connection.commit()
    log_audit('DELETE', 'transactions', tx_id, old_value=str(old))
    return redirect(url_for('financials'))

# ─── PDF EXPORT ───────────────────────────────────────────
@app.route('/report/pdf')
def export_pdf():
    if 'loggedin' not in session:
        return redirect(url_for('login'))
    cursor = mysql.connection.cursor(MySQLdb.cursors.DictCursor)
    cursor.execute('SELECT * FROM flocks')
    all_flocks = cursor.fetchall()
    cursor.execute('SELECT SUM(amount) as total FROM transactions WHERE type="Revenue"')
    revenue = cursor.fetchone()['total'] or 0
    cursor.execute('SELECT SUM(amount) as total FROM transactions WHERE type="Expense"')
    expenses = cursor.fetchone()['total'] or 0
    cursor.execute('''SELECT dl.*, f.name as flock_name FROM daily_logs dl
                      JOIN flocks f ON dl.flock_id = f.id
                      ORDER BY dl.log_date DESC LIMIT 20''')
    all_logs = cursor.fetchall()
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4)
    styles = getSampleStyleSheet()
    elements = []
    elements.append(Paragraph("Poultry Management System - Farm Report", styles['Title']))
    elements.append(Spacer(1, 12))
    elements.append(Paragraph("Money Summary", styles['Heading2']))
    fin_data = [
        ['Item', 'Amount (KSh)'],
        ['Money Received', f'{revenue:,.0f}'],
        ['Money Spent', f'{expenses:,.0f}'],
        ['Total Earnings', f'{revenue - expenses:,.0f}'],
    ]
    fin_table = Table(fin_data, colWidths=[300, 150])
    fin_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#1a3c2b')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.HexColor('#f5f5f5'), colors.white]),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
        ('PADDING', (0, 0), (-1, -1), 8),
    ]))
    elements.append(fin_table)
    elements.append(Spacer(1, 20))
    elements.append(Paragraph("Chicken Groups", styles['Heading2']))
    flock_data = [['Name', 'Type', 'House', 'Started With', 'Alive Now', 'Status']]
    for f in all_flocks:
        flock_data.append([f['name'], f['type'], f['house'] or '-',
                           str(f['initial_count']), str(f['current_count']), f['status']])
    flock_table = Table(flock_data, colWidths=[120, 70, 70, 60, 60, 70])
    flock_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#1a3c2b')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.HexColor('#f5f5f5'), colors.white]),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
        ('PADDING', (0, 0), (-1, -1), 6),
    ]))
    elements.append(flock_table)
    elements.append(Spacer(1, 20))
    elements.append(Paragraph("Recent Daily Farm Reports", styles['Heading2']))
    log_data = [['Date', 'Chicken Group', 'Birds Died', 'Feed Used(kg)', 'Water Used(L)', 'Eggs Gathered']]
    for l in all_logs:
        log_data.append([str(l['log_date']), l['flock_name'], str(l['mortality']),
                        str(l['feed_consumed']), str(l['water_intake']),
                        str(l['eggs_collected'])])
    log_table = Table(log_data, colWidths=[80, 120, 70, 70, 70, 60])
    log_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#1a3c2b')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.HexColor('#f5f5f5'), colors.white]),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
        ('PADDING', (0, 0), (-1, -1), 6),
    ]))
    elements.append(log_table)
    doc.build(elements)
    buffer.seek(0)
    response = make_response(buffer.read())
    response.headers['Content-Type'] = 'application/pdf'
    response.headers['Content-Disposition'] = 'attachment; filename=farm_report.pdf'
    return response

# ─── AUDIT LOG ────────────────────────────────────────────
@app.route('/audit')
def audit():
    if 'loggedin' not in session or session.get('role') != 'admin':
        return redirect(url_for('dashboard'))
    cursor = mysql.connection.cursor(MySQLdb.cursors.DictCursor)
    cursor.execute('SELECT * FROM audit_log ORDER BY created_at DESC LIMIT 100')
    logs = cursor.fetchall()
    return render_template('audit.html', logs=logs)

if __name__ == '__main__':
    app.run(debug=True)
