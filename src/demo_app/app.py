from flask import Flask, render_template_string, request, jsonify, session, redirect, url_for
from flask_cors import CORS
import random
import time
from datetime import datetime
from typing import Dict, Any, Optional

app = Flask(__name__)
app.secret_key = "demo-banking-secret-key-for-testing-only"
CORS(app)

# Simulated database
MEMBERS = {
    "12345": {
        "member_id": "12345",
        "name": "John Smith",
        "email": "john.smith@example.com",
        "phone": "555-0123",
        "accounts": {
            "SAV-001": {"type": "Savings", "balance": 15420.50, "currency": "USD"},
            "CHK-001": {"type": "Checking", "balance": 3210.75, "currency": "USD"},
        },
        "status": "Active",
        "joined": "2020-03-15",
    },
    "67890": {
        "member_id": "67890",
        "name": "Jane Doe",
        "email": "jane.doe@example.com",
        "phone": "555-0456",
        "accounts": {
            "SAV-002": {"type": "Savings", "balance": 28750.00, "currency": "USD"},
            "CHK-002": {"type": "Checking", "balance": 5600.25, "currency": "USD"},
            "CD-001": {"type": "Certificate of Deposit", "balance": 50000.00, "currency": "USD"},
        },
        "status": "Active",
        "joined": "2018-11-22",
    },
    "11111": {
        "member_id": "11111",
        "name": "Robert Johnson",
        "email": "robert.j@example.com",
        "phone": "555-0789",
        "accounts": {
            "SAV-003": {"type": "Savings", "balance": 890.00, "currency": "USD"},
        },
        "status": "Active",
        "joined": "2022-01-10",
    },
}

SESSIONS = {}

# HTML Templates - Legacy-style with frames, tables, no test IDs
BASE_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>Core Banking System - {{ title }}</title>
    <style>
        body { font-family: Arial, sans-serif; margin: 0; padding: 20px; background: #f0f0f0; }
        .container { max-width: 1200px; margin: 0 auto; background: white; padding: 20px; border: 1px solid #ccc; }
        .header { background: #003366; color: white; padding: 15px; margin: -20px -20px 20px -20px; }
        .header h1 { margin: 0; font-size: 24px; }
        .nav { background: #e8e8e8; padding: 10px; margin: -20px -20px 20px -20px; border-bottom: 1px solid #ccc; }
        .nav a { margin-right: 20px; color: #003366; text-decoration: none; font-weight: bold; }
        .nav a:hover { text-decoration: underline; }
        table { width: 100%; border-collapse: collapse; margin: 10px 0; }
        th, td { border: 1px solid #ccc; padding: 8px; text-align: left; }
        th { background: #003366; color: white; }
        tr:nth-child(even) { background: #f9f9f9; }
        .btn { background: #003366; color: white; padding: 8px 16px; border: none; cursor: pointer; margin: 5px; }
        .btn:hover { background: #005599; }
        .btn-danger { background: #cc0000; }
        .btn-danger:hover { background: #aa0000; }
        .btn-secondary { background: #666; }
        .form-group { margin: 15px 0; }
        label { display: block; margin-bottom: 5px; font-weight: bold; }
        input, select { width: 300px; padding: 8px; border: 1px solid #ccc; }
        .alert { padding: 15px; margin: 15px 0; border-radius: 4px; }
        .alert-success { background: #d4edda; color: #155724; border: 1px solid #c3e6cb; }
        .alert-error { background: #f8d7da; color: #721c24; border: 1px solid #f5c6cb; }
        .alert-warning { background: #fff3cd; color: #856404; border: 1px solid #ffeeba; }
        .frame-container { border: 2px inset #ccc; margin: 10px 0; }
        .frame { width: 100%; height: 400px; border: none; }
        .status-bar { background: #e8e8e8; padding: 5px; margin: 20px -20px -20px -20px; border-top: 1px solid #ccc; font-size: 12px; color: #666; }
        .modal { display: none; position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: rgba(0,0,0,0.5); z-index: 1000; }
        .modal-content { background: white; width: 500px; margin: 100px auto; padding: 20px; border: 1px solid #ccc; }
        .loading { display: inline-block; width: 16px; height: 16px; border: 2px solid #fff; border-radius: 50%; border-top-color: transparent; animation: spin 1s linear infinite; }
        @keyframes spin { to { transform: rotate(360deg); } }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>Core Banking System</h1>
            <div style="font-size: 12px; margin-top: 5px;">{{ institution }}</div>
        </div>
        <div class="nav">
            <a href="/">Dashboard</a>
            <a href="/member-lookup">Member Lookup</a>
            <a href="/account-services">Account Services</a>
            <a href="/reports">Reports</a>
            <a href="/admin">Admin</a>
        </div>
        {% block content %}{% endblock %}
        <div class="status-bar">
            User: {{ session.get('user', 'Teller') }} | Institution: {{ institution }} | Session: {{ session.get('session_id', 'N/A')[:8] }}
        </div>
    </div>
    {% block scripts %}{% endblock %}
</body>
</html>
"""

DASHBOARD_TEMPLATE = BASE_TEMPLATE.replace("{% block content %}{% endblock %}", """
<div style="display: grid; grid-template-columns: 1fr 1fr; gap: 20px;">
    <div>
        <h2>Quick Actions</h2>
        <table>
            <tr><th>Action</th><th>Description</th></tr>
            <tr><td><a href="/member-lookup" class="btn">Member Lookup</a></td><td>Search for member by ID</td></tr>
            <tr><td><a href="/account-services" class="btn">New Account</a></td><td>Open new sub-account</td></tr>
            <tr><td><a href="/transaction-entry" class="btn">Transaction Entry</a></td><td>Process deposit/withdrawal</td></tr>
        </table>
    </div>
    <div>
        <h2>Recent Activity</h2>
        <table>
            <tr><th>Time</th><th>Member</th><th>Action</th><th>Amount</th></tr>
            {% for activity in recent_activity %}
            <tr><td>{{ activity.time }}</td><td>{{ activity.member }}</td><td>{{ activity.action }}</td><td>{{ activity.amount }}</td></tr>
            {% endfor %}
        </table>
    </div>
</div>
""")

MEMBER_LOOKUP_TEMPLATE = BASE_TEMPLATE.replace("{% block content %}{% endblock %}", """
<h2>Member Lookup</h2>
<form method="POST" action="/member-lookup">
    <div class="form-group">
        <label for="member_id">Member ID:</label>
        <input type="text" id="member_id" name="member_id" required placeholder="Enter member ID (e.g., 12345)">
    </div>
    <button type="submit" class="btn">Search</button>
    <button type="button" class="btn btn-secondary" onclick="clearForm()">Clear</button>
</form>

{% if member %}
<div class="frame-container">
    <h3>Member Details</h3>
    <table>
        <tr><th>Field</th><th>Value</th></tr>
        <tr><td>Member ID</td><td>{{ member.member_id }}</td></tr>
        <tr><td>Name</td><td>{{ member.name }}</td></tr>
        <tr><td>Email</td><td>{{ member.email }}</td></tr>
        <tr><td>Phone</td><td>{{ member.phone }}</td></tr>
        <tr><td>Status</td><td>{{ member.status }}</td></tr>
        <tr><td>Member Since</td><td>{{ member.joined }}</td></tr>
    </table>
    
    <h3>Accounts</h3>
    <table>
        <tr><th>Account ID</th><th>Type</th><th>Balance</th><th>Actions</th></tr>
        {% for acc_id, acc in member.accounts.items() %}
        <tr>
            <td>{{ acc_id }}</td>
            <td>{{ acc.type }}</td>
            <td>${{ "%.2f"|format(acc.balance) }} {{ acc.currency }}</td>
            <td>
                <a href="/account-detail/{{ member.member_id }}/{{ acc_id }}" class="btn">View Details</a>
                <form method="POST" action="/account-detail/{{ member.member_id }}/{{ acc_id }}/close" style="display:inline;">
                    <button type="submit" class="btn btn-danger" onclick="return confirm('Close this account?')">Close</button>
                </form>
            </td>
        </tr>
        {% endfor %}
    </table>
</div>
{% endif %}

{% if error %}
<div class="alert alert-error">{{ error }}</div>
{% endif %}

<script>
function clearForm() {
    document.getElementById('member_id').value = '';
}
</script>
""")

ACCOUNT_DETAIL_TEMPLATE = BASE_TEMPLATE.replace("{% block content %}{% endblock %}", """
<h2>Account Detail: {{ account_id }}</h2>
<a href="/member-lookup" class="btn btn-secondary">Back to Member</a>

<table>
    <tr><th>Field</th><th>Value</th></tr>
    <tr><td>Account ID</td><td>{{ account_id }}</td></tr>
    <tr><td>Type</td><td>{{ account.type }}</td></tr>
    <tr><td>Balance</td><td>${{ "%.2f"|format(account.balance) }} {{ account.currency }}</td></tr>
    <tr><td>Status</td><td>Active</td></tr>
    <tr><td>Opened</td><td>{{ account.opened }}</td></tr>
</table>

<h3>Recent Transactions</h3>
<table>
    <tr><th>Date</th><th>Description</th><th>Amount</th><th>Balance</th></tr>
    {% for txn in transactions %}
    <tr>
        <td>{{ txn.date }}</td>
        <td>{{ txn.description }}</td>
        <td class="{% if txn.amount > 0 %}credit{% else %}debit{% endif %}">${{ "%.2f"|format(txn.amount) }}</td>
        <td>${{ "%.2f"|format(txn.balance) }}</td>
    </tr>
    {% endfor %}
</table>
""")

ACCOUNT_SERVICES_TEMPLATE = BASE_TEMPLATE.replace("{% block content %}{% endblock %}", """
<h2>Account Services - Open New Sub-Account</h2>

<form method="POST" action="/account-services/create">
    <div class="form-group">
        <label for="member_id">Member ID:</label>
        <input type="text" id="member_id" name="member_id" required placeholder="Enter member ID">
    </div>
    
    <div class="form-group">
        <label for="account_type">Account Type:</label>
        <select id="account_type" name="account_type" required>
            <option value="">Select account type</option>
            <option value="SAVINGS">Savings Account</option>
            <option value="CHECKING">Checking Account</option>
            <option value="CD">Certificate of Deposit (CD)</option>
            <option value="MONEY_MARKET">Money Market</option>
        </select>
    </div>
    
    <div class="form-group">
        <label for="initial_deposit">Initial Deposit ($):</label>
        <input type="number" id="initial_deposit" name="initial_deposit" step="0.01" min="0" required placeholder="0.00">
    </div>
    
    <div class="form-group">
        <label for="product_code">Product Code (Optional):</label>
        <input type="text" id="product_code" name="product_code" placeholder="e.g., SAV-STD-01">
    </div>
    
    <button type="submit" class="btn">Create Account</button>
    <button type="button" class="btn btn-secondary" onclick="this.form.reset()">Reset</button>
</form>

{% if result %}
<div class="alert {% if result.success %}alert-success{% else %}alert-error{% endif %}">
    {% if result.success %}
    <strong>Success!</strong> Account {{ result.account_id }} created for member {{ result.member_id }}.
    <br>Confirmation Number: {{ result.confirmation }}
    {% else %}
    <strong>Error:</strong> {{ result.error }}
    {% endif %}
</div>
{% endif %}

{% if error %}
<div class="alert alert-error">{{ error }}</div>
{% endif %}
""")

TRANSACTION_TEMPLATE = BASE_TEMPLATE.replace("{% block content %}{% endblock %}", """
<h2>Transaction Entry</h2>

<form method="POST" action="/transaction-entry">
    <div class="form-group">
        <label for="member_id">Member ID:</label>
        <input type="text" id="member_id" name="member_id" required>
    </div>
    
    <div class="form-group">
        <label for="account_id">Account ID:</label>
        <input type="text" id="account_id" name="account_id" required>
    </div>
    
    <div class="form-group">
        <label for="txn_type">Transaction Type:</label>
        <select id="txn_type" name="txn_type" required>
            <option value="">Select type</option>
            <option value="DEPOSIT">Deposit</option>
            <option value="WITHDRAWAL">Withdrawal</option>
            <option value="TRANSFER_IN">Transfer In</option>
            <option value="TRANSFER_OUT">Transfer Out</option>
        </select>
    </div>
    
    <div class="form-group">
        <label for="amount">Amount ($):</label>
        <input type="number" id="amount" name="amount" step="0.01" min="0.01" required>
    </div>
    
    <div class="form-group">
        <label for="description">Description:</label>
        <input type="text" id="description" name="description" placeholder="Transaction description">
    </div>
    
    <button type="submit" class="btn">Process Transaction</button>
</form>

{% if result %}
<div class="alert {% if result.success %}alert-success{% else %}alert-error{% endif %}">
    {% if result.success %}
    <strong>Transaction Processed!</strong> Reference: {{ result.reference }}
    <br>New Balance: ${{ "%.2f"|format(result.new_balance) }}
    {% else %}
    <strong>Error:</strong> {{ result.error }}
    {% endif %}
</div>
{% endif %}
""")

LOGIN_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>Core Banking System - Login</title>
    <style>
        body { font-family: Arial, sans-serif; background: #f0f0f0; display: flex; justify-content: center; align-items: center; height: 100vh; margin: 0; }
        .login-box { background: white; padding: 40px; border: 1px solid #ccc; width: 400px; }
        .login-box h2 { margin-top: 0; color: #003366; }
        .form-group { margin: 20px 0; }
        label { display: block; margin-bottom: 5px; font-weight: bold; }
        input { width: 100%; padding: 10px; border: 1px solid #ccc; box-sizing: border-box; }
        .btn { width: 100%; padding: 12px; background: #003366; color: white; border: none; cursor: pointer; font-size: 16px; }
        .btn:hover { background: #005599; }
        .error { color: #cc0000; margin: 10px 0; }
    </style>
</head>
<body>
    <div class="login-box">
        <h2>Core Banking System</h2>
        <p>Institution: {{ institution }}</p>
        {% if error %}<div class="error">{{ error }}</div>{% endif %}
        <form method="POST" action="/login">
            <div class="form-group">
                <label>User ID</label>
                <input type="text" name="user_id" required autofocus>
            </div>
            <div class="form-group">
                <label>Password</label>
                <input type="password" name="password" required>
            </div>
            <button type="submit" class="btn">Sign In</button>
        </form>
    </div>
</body>
</html>
"""

CONFIRMATION_TEMPLATE = BASE_TEMPLATE.replace("{% block content %}{% endblock %}", """
<h2>Confirm Action</h2>
<div class="alert alert-warning">
    <strong>Please confirm:</strong> {{ message }}
</div>
<form method="POST">
    <input type="hidden" name="confirmed" value="true">
    <button type="submit" class="btn">Confirm</button>
    <a href="{{ cancel_url }}" class="btn btn-secondary">Cancel</a>
</form>
""")

# Routes
# @app.before_request
# def require_login():
#     allowed_paths = ['/login', '/health', '/static']
#     if request.path not in allowed_paths and not request.path.startswith('/static'):
#         if 'user' not in session:
#             return redirect('/login')

@app.route('/health')
def health():
    return jsonify({"status": "ok", "service": "core-banking-demo"})

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        user_id = request.form.get('user_id')
        password = request.form.get('password')
        # Demo: any non-empty credentials work
        if user_id and password:
            session['user'] = user_id
            session['session_id'] = f"sess_{random.randint(100000, 999999)}"
            session['institution'] = "First Federal Credit Union"
            return redirect('/')
        return render_template_string(LOGIN_TEMPLATE, institution="First Federal Credit Union", error="Invalid credentials")
    return render_template_string(LOGIN_TEMPLATE, institution="First Federal Credit Union")

@app.route('/logout')
def logout():
    session.clear()
    return redirect('/login')

@app.route('/')
def dashboard():
    recent_activity = [
        {"time": "10:32 AM", "member": "12345", "action": "Deposit", "amount": "$500.00"},
        {"time": "10:15 AM", "member": "67890", "action": "Withdrawal", "amount": "$200.00"},
        {"time": "09:45 AM", "member": "11111", "action": "New Account", "amount": "$1,000.00"},
        {"time": "09:30 AM", "member": "12345", "action": "Transfer", "amount": "$1,200.00"},
    ]
    return render_template_string(DASHBOARD_TEMPLATE, 
        title="Dashboard", 
        institution=session.get('institution', 'Demo Bank'),
        session=session,
        recent_activity=recent_activity)

@app.route('/member-lookup', methods=['GET', 'POST'])
def member_lookup():
    member = None
    error = None
    
    if request.method == 'POST':
        member_id = request.form.get('member_id', '').strip()
        # Simulate occasional slow response
        if random.random() < 0.1:
            time.sleep(2)
        # Simulate occasional error
        if random.random() < 0.05:
            error = "System temporarily unavailable. Please try again."
        elif member_id in MEMBERS:
            member = MEMBERS[member_id]
        else:
            error = f"Member {member_id} not found"
    
    return render_template_string(MEMBER_LOOKUP_TEMPLATE,
        title="Member Lookup",
        institution=session.get('institution', 'Demo Bank'),
        session=session,
        member=member,
        error=error)

@app.route('/account-detail/<member_id>/<account_id>')
def account_detail(member_id, account_id):
    if member_id not in MEMBERS or account_id not in MEMBERS[member_id]['accounts']:
        return "Account not found", 404
    
    account = MEMBERS[member_id]['accounts'][account_id]
    transactions = [
        {"date": "2024-01-15", "description": "Deposit - Mobile Check", "amount": 500.00, "balance": account['balance']},
        {"date": "2024-01-10", "description": "Withdrawal - ATM", "amount": -200.00, "balance": account['balance'] - 500.00},
        {"date": "2024-01-05", "description": "Interest Payment", "amount": 12.50, "balance": account['balance'] - 300.00},
        {"date": "2024-01-01", "description": "Opening Deposit", "amount": 1000.00, "balance": account['balance'] - 312.50},
    ]
    
    return render_template_string(ACCOUNT_DETAIL_TEMPLATE,
        title=f"Account {account_id}",
        institution=session.get('institution', 'Demo Bank'),
        session=session,
        account_id=account_id,
        account=account,
        transactions=transactions)

@app.route('/account-detail/<member_id>/<account_id>/close', methods=['POST'])
def close_account(member_id, account_id):
    # Requires confirmation page first
    return redirect(url_for('confirm_close', member_id=member_id, account_id=account_id))

@app.route('/confirm-close/<member_id>/<account_id>', methods=['GET', 'POST'])
def confirm_close(member_id, account_id):
    if request.method == 'POST':
        if request.form.get('confirmed'):
            # Actually close account
            if member_id in MEMBERS and account_id in MEMBERS[member_id]['accounts']:
                del MEMBERS[member_id]['accounts'][account_id]
            return redirect(f'/member-lookup?success=Account+{account_id}+closed')
    return render_template_string(CONFIRMATION_TEMPLATE,
        title="Confirm Account Closure",
        institution=session.get('institution', 'Demo Bank'),
        session=session,
        message=f"Are you sure you want to close account {account_id} for member {member_id}? This action cannot be undone.",
        cancel_url=f'/account-detail/{member_id}/{account_id}')

@app.route('/account-services', methods=['GET'])
def account_services():
    return render_template_string(ACCOUNT_SERVICES_TEMPLATE,
        title="Account Services",
        institution=session.get('institution', 'Demo Bank'),
        session=session)

@app.route('/account-services/create', methods=['POST'])
def create_account():
    member_id = request.form.get('member_id', '').strip()
    account_type = request.form.get('account_type', '')
    initial_deposit = float(request.form.get('initial_deposit', 0))
    product_code = request.form.get('product_code', '').strip()
    
    # Simulate processing delay
    time.sleep(1)
    
    # Simulate occasional error
    if random.random() < 0.1:
        result = {"success": False, "error": "Core system timeout. Please try again."}
        return render_template_string(ACCOUNT_SERVICES_TEMPLATE,
            title="Account Services",
            institution=session.get('institution', 'Demo Bank'),
            session=session,
            result=result)
    
    if member_id not in MEMBERS:
        result = {"success": False, "error": f"Member {member_id} not found"}
        return render_template_string(ACCOUNT_SERVICES_TEMPLATE,
            title="Account Services",
            institution=session.get('institution', 'Demo Bank'),
            session=session,
            result=result)
    
    if initial_deposit < 25:
        result = {"success": False, "error": "Minimum initial deposit is $25.00"}
        return render_template_string(ACCOUNT_SERVICES_TEMPLATE,
            title="Account Services",
            institution=session.get('institution', 'Demo Bank'),
            session=session,
            result=result)
    
    # Generate new account ID
    type_prefix = {"SAVINGS": "SAV", "CHECKING": "CHK", "CD": "CD", "MONEY_MARKET": "MM"}[account_type]
    new_account_id = f"{type_prefix}-{random.randint(100, 999)}"
    while any(new_account_id in m['accounts'] for m in MEMBERS.values()):
        new_account_id = f"{type_prefix}-{random.randint(100, 999)}"
    
    MEMBERS[member_id]['accounts'][new_account_id] = {
        "type": account_type.replace("_", " ").title(),
        "balance": initial_deposit,
        "currency": "USD",
        "opened": datetime.now().strftime("%Y-%m-%d"),
    }
    
    confirmation = f"CONF-{random.randint(100000, 999999)}"
    result = {
        "success": True,
        "account_id": new_account_id,
        "member_id": member_id,
        "confirmation": confirmation,
    }
    
    return render_template_string(ACCOUNT_SERVICES_TEMPLATE,
        title="Account Services",
        institution=session.get('institution', 'Demo Bank'),
        session=session,
        result=result)

@app.route('/transaction-entry', methods=['GET', 'POST'])
def transaction_entry():
    result = None
    if request.method == 'POST':
        member_id = request.form.get('member_id', '').strip()
        account_id = request.form.get('account_id', '').strip()
        txn_type = request.form.get('txn_type', '')
        amount = float(request.form.get('amount', 0))
        description = request.form.get('description', '')
        
        time.sleep(0.5)
        
        if member_id not in MEMBERS or account_id not in MEMBERS[member_id]['accounts']:
            result = {"success": False, "error": "Invalid member or account"}
        elif txn_type in ['WITHDRAWAL', 'TRANSFER_OUT'] and MEMBERS[member_id]['accounts'][account_id]['balance'] < amount:
            result = {"success": False, "error": "Insufficient funds"}
        else:
            # Process transaction
            if txn_type in ['DEPOSIT', 'TRANSFER_IN']:
                MEMBERS[member_id]['accounts'][account_id]['balance'] += amount
            else:
                MEMBERS[member_id]['accounts'][account_id]['balance'] -= amount
            
            reference = f"TXN-{random.randint(100000, 999999)}"
            result = {
                "success": True,
                "reference": reference,
                "new_balance": MEMBERS[member_id]['accounts'][account_id]['balance'],
            }
    
    return render_template_string(TRANSACTION_TEMPLATE,
        title="Transaction Entry",
        institution=session.get('institution', 'Demo Bank'),
        session=session,
        result=result)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)