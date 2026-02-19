from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet
from io import BytesIO
from flask import make_response
from flask import Flask, render_template, request, redirect, url_for, session, jsonify
from flask_mysqldb import MySQL
import MySQLdb.cursors

app = Flask(__name__)

# Secret key for sessions
app.secret_key = 'poultry_secret_key_2024'

# MySQL Configuration — match your XAMPP settings
app.config['MYSQL_HOST'] = 'localhost'
app.config['MYSQL_USER'] = 'root'
app.config['MYSQL_PASSWORD'] = ''       # Leave blank if XAMPP default
app.config['MYSQL_DB'] = 'poultry_db'

mysql = MySQL(app)

# ─── LOGIN ───────────────────────────────────────────────
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

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/register', methods=['GET', 'POST'])
def register():
    if 'loggedin' not in session or session.get('role') != 'admin':
        return redirect(url_for('dashboard'))
    error = None
    success = None
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        role = request.form['role']
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

@app.route('/users/delete/<int:user_id>')
def delete_user(user_id):
    if 'loggedin' not in session or session.get('role') != 'admin':
        return redirect(url_for('dashboard'))
    cursor = mysql.connection.cursor()
    cursor.execute('DELETE FROM users WHERE id = %s AND username != "admin"', (user_id,))
    mysql.connection.commit()
    return redirect(url_for('register'))
# ─── DASHBOARD ───────────────────────────────────────────
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

    cursor.execute('SELECT * FROM daily_logs ORDER BY log_date DESC LIMIT 5')
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
                alerts.append(f"{flock['name']}: Mortality at {rate:.1f}% — above safe threshold!")

    for item in low_stock:
        alerts.append(f"Low stock: {item['item_name']} ({item['quantity']} {item['unit']} left)")

    return render_template('dashboard.html',
        flocks=flocks, revenue=revenue, expenses=expenses,
        profit=revenue-expenses, eggs=eggs,
        recent_logs=recent_logs, alerts=alerts)

# ─── FLOCKS ──────────────────────────────────────────────
@app.route('/flocks')
def flocks():
    if 'loggedin' not in session:
        return redirect(url_for('login'))
    from datetime import date

    cursor = mysql.connection.cursor(MySQLdb.cursors.DictCursor)
    cursor.execute('SELECT * FROM flocks')
    flocks = cursor.fetchall()
    flock_stats = {}
    for flock in flocks:
        cursor.execute('SELECT SUM(mortality) as dead, SUM(feed_consumed) as feed, MAX(avg_weight) as weight FROM daily_logs WHERE flock_id = %s', (flock['id'],))
        stats = cursor.fetchone()
        dead = stats['dead'] or 0
        feed = stats['feed'] or 0
        weight = stats['weight'] or 0
        mortality_rate = round((dead / flock['initial_count']) * 100, 1) if flock['initial_count'] > 0 else 0
        fcr = round(feed / (weight * flock['current_count'] / 1000), 2) if weight > 0 and flock['current_count'] > 0 else 0
        flock_stats[flock['id']] = {
            'mortality_rate': mortality_rate,
            'fcr': fcr,
            'total_dead': int(dead)
        }
    return render_template('flocks.html', flocks=flocks, flock_stats=flock_stats, today=date.today())

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
    cursor.execute('INSERT INTO flocks (name, type, breed, house, start_date, initial_count, current_count) VALUES (%s,%s,%s,%s,%s,%s,%s)',
                   (name, ftype, breed, house, start_date, count, count))
    mysql.connection.commit()
    return redirect(url_for('flocks'))

# ─── DAILY LOGS ───────────────────────────────────────────
@app.route('/logs')
def logs():
    if 'loggedin' not in session:
        return redirect(url_for('login'))
    from datetime import date
    cursor = mysql.connection.cursor(MySQLdb.cursors.DictCursor)
    cursor.execute('''SELECT dl.*, f.name as flock_name 
                      FROM daily_logs dl JOIN flocks f ON dl.flock_id = f.id 
                      ORDER BY dl.log_date DESC''')
    logs = cursor.fetchall()
    cursor.execute('SELECT * FROM flocks WHERE status="Active"')
    flocks = cursor.fetchall()
    return render_template('logs.html', logs=logs, flocks=flocks, today=date.today())

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
    cursor.execute('INSERT INTO inventory (item_name, category, quantity, unit, reorder_level, cost_per_unit) VALUES (%s,%s,%s,%s,%s,%s)',
                   (name, category, qty, unit, reorder, cost))
    mysql.connection.commit()
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
    flocks = cursor.fetchall()
    cursor.execute('SELECT SUM(amount) as total FROM transactions WHERE type="Revenue"')
    revenue = cursor.fetchone()['total'] or 0
    cursor.execute('SELECT SUM(amount) as total FROM transactions WHERE type="Expense"')
    expenses = cursor.fetchone()['total'] or 0
    return render_template('financials.html',
        transactions=transactions, flocks=flocks,
        revenue=revenue, expenses=expenses, profit=revenue-expenses)

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
    cursor.execute('INSERT INTO transactions (flock_id, trans_date, type, category, amount, description) VALUES (%s,%s,%s,%s,%s,%s)',
                   (flock_id, trans_date, ttype, category, amount, description))
    mysql.connection.commit()
    return redirect(url_for('financials'))

# ─── DELETE ROUTES ────────────────────────────────────────
@app.route('/flocks/delete/<int:flock_id>')
def delete_flock(flock_id):
    if 'loggedin' not in session:
        return redirect(url_for('login'))
    cursor = mysql.connection.cursor()
    cursor.execute('DELETE FROM daily_logs WHERE flock_id = %s', (flock_id,))
    cursor.execute('DELETE FROM transactions WHERE flock_id = %s', (flock_id,))
    cursor.execute('DELETE FROM flocks WHERE id = %s', (flock_id,))
    mysql.connection.commit()
    return redirect(url_for('flocks'))

@app.route('/logs/delete/<int:log_id>')
def delete_log(log_id):
    if 'loggedin' not in session:
        return redirect(url_for('login'))
    cursor = mysql.connection.cursor()
    cursor.execute('DELETE FROM daily_logs WHERE id = %s', (log_id,))
    mysql.connection.commit()
    return redirect(url_for('logs'))

@app.route('/inventory/delete/<int:item_id>')
def delete_inventory(item_id):
    if 'loggedin' not in session:
        return redirect(url_for('login'))
    cursor = mysql.connection.cursor()
    cursor.execute('DELETE FROM inventory WHERE id = %s', (item_id,))
    mysql.connection.commit()
    return redirect(url_for('inventory'))

@app.route('/transactions/delete/<int:tx_id>')
def delete_transaction(tx_id):
    if 'loggedin' not in session:
        return redirect(url_for('login'))
    cursor = mysql.connection.cursor()
    cursor.execute('DELETE FROM transactions WHERE id = %s', (tx_id,))
    mysql.connection.commit()
    return redirect(url_for('financials'))

@app.route('/report/pdf')
def export_pdf():
    if 'loggedin' not in session:
        return redirect(url_for('login'))

    cursor = mysql.connection.cursor(MySQLdb.cursors.DictCursor)
    cursor.execute('SELECT * FROM flocks')
    flocks = cursor.fetchall()
    cursor.execute('SELECT SUM(amount) as total FROM transactions WHERE type="Revenue"')
    revenue = cursor.fetchone()['total'] or 0
    cursor.execute('SELECT SUM(amount) as total FROM transactions WHERE type="Expense"')
    expenses = cursor.fetchone()['total'] or 0
    cursor.execute('''SELECT dl.*, f.name as flock_name FROM daily_logs dl
                      JOIN flocks f ON dl.flock_id = f.id
                      ORDER BY dl.log_date DESC LIMIT 20''')
    logs = cursor.fetchall()

    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4)
    styles = getSampleStyleSheet()
    elements = []

    # Title
    elements.append(Paragraph("🐔 Poultry Management System — Farm Report", styles['Title']))
    elements.append(Spacer(1, 12))

    # Financial Summary
    elements.append(Paragraph("Financial Summary", styles['Heading2']))
    fin_data = [
        ['Item', 'Amount (KSh)'],
        ['Total Revenue', f'{revenue:,.0f}'],
        ['Total Expenses', f'{expenses:,.0f}'],
        ['Net Profit', f'{revenue - expenses:,.0f}'],
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

    # Flocks
    elements.append(Paragraph("Active Flocks", styles['Heading2']))
    flock_data = [['Name', 'Type', 'House', 'Initial', 'Current', 'Status']]
    for f in flocks:
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

    # Recent Logs
    elements.append(Paragraph("Recent Daily Logs", styles['Heading2']))
    log_data = [['Date', 'Flock', 'Mortality', 'Feed(kg)', 'Water(L)', 'Eggs']]
    for l in logs:
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

if __name__ == '__main__':
    app.run(debug=True)