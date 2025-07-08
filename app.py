# Standard library imports
import os
import uuid
import random
import string
import csv
import base64
from datetime import datetime, time, timedelta
from functools import wraps
from flask_socketio import SocketIO
import eventlet
import ssl

# Third-party imports
import pytz
from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    flash,
    session,
    jsonify
)
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.exc import IntegrityError
from sqlalchemy import func, text

# Configuration
app = Flask(__name__)
app.config.update(
    SECRET_KEY='your-secret-key-here',
    SQLALCHEMY_DATABASE_URI='sqlite:///queue.db',
    SQLALCHEMY_TRACK_MODIFICATIONS=False,
    PERMANENT_SESSION_LIFETIME=timedelta(hours=8)
)

# Constants
MAX_SLOTS_PER_HOUR = 15
ADMIN_USERNAME = 'admin'
ADMIN_PASSWORD = 'admin123'
LOCAL_TIMEZONE = pytz.timezone('Asia/Singapore')
UTC = pytz.UTC

# Database initialization
db = SQLAlchemy(app)

# Models
class Queue(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    time_slot = db.Column(db.DateTime, nullable=False)
    date = db.Column(db.Date, nullable=False)
    queue_code = db.Column(db.String(50), unique=True, nullable=False)
    browser_id = db.Column(db.String(50), nullable=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(UTC))
    completed_at = db.Column(db.DateTime, nullable=True)
    status = db.Column(db.String(20), default='waiting')

    def __init__(self, **kwargs):
        # Ensure all datetime fields are timezone aware
        if 'time_slot' in kwargs and not kwargs['time_slot'].tzinfo:
            kwargs['time_slot'] = UTC.localize(kwargs['time_slot'])
        if 'completed_at' in kwargs and kwargs['completed_at'] and not kwargs['completed_at'].tzinfo:
            kwargs['completed_at'] = UTC.localize(kwargs['completed_at'])
        super().__init__(**kwargs)

    @property
    def wait_time(self):
        if self.completed_at and self.time_slot:
            wait_minutes = int((self.completed_at - self.time_slot).total_seconds() / 60)
            return max(0, wait_minutes)
        return None

# Initialize Flask-SocketIO
socketio = SocketIO(app)

# Initialize database
with app.app_context():
    db.create_all()

# Utility functions
def load_words_from_csv():
    words = []
    csv_path = os.path.join(os.path.dirname(__file__), 'word_bank.csv')
    try:
        with open(csv_path, 'r') as file:
            words = [line.strip() for line in file if line.strip()]
        return words if words else ["apple", "beach", "cloud", "dance", "eagle"]
    except Exception as e:
        print(f"Error loading CSV: {e}")
        return ["apple", "beach", "cloud", "dance", "eagle"]

def local_to_utc(local_dt):
    local_tz = LOCAL_TIMEZONE.localize(local_dt)
    return local_tz.astimezone(UTC)

def utc_to_local(utc_dt):
    # Ensure the datetime is timezone-aware
    if utc_dt and not utc_dt.tzinfo:
        utc_dt = utc_dt.replace(tzinfo=UTC)
    return utc_dt.astimezone(LOCAL_TIMEZONE)

def ensure_timezone(dt, tz=UTC):
    """Ensure a datetime is timezone-aware, using UTC by default"""
    if dt and not dt.tzinfo:
        return dt.replace(tzinfo=tz)
    return dt

def combine_date_time(date, time_obj):
    local_dt = datetime.combine(date, time_obj)
    return local_to_utc(local_dt)

def generate_random_suffix():
    return ''.join(random.choices(string.ascii_uppercase, k=3))

def format_hour_ampm(hour):
    # Ensure we're using local hour for queue code generation
    if hour == 0: return "12A"
    elif hour < 12: return f"{hour}A"
    elif hour == 12: return "12P"
    else: return f"{hour-12}P"

def get_average_wait_time(date):
    # Convert local date to UTC range for query
    day_start = LOCAL_TIMEZONE.localize(datetime.combine(date, time.min))
    day_end = LOCAL_TIMEZONE.localize(datetime.combine(date, time.max))

    # Ensure day_start and day_end are timezone-aware
    day_start = ensure_timezone(day_start, LOCAL_TIMEZONE)
    day_end = ensure_timezone(day_end, LOCAL_TIMEZONE)

    completed_tickets = Queue.query.filter(
        Queue.time_slot >= day_start,
        Queue.time_slot <= day_end,
        Queue.status == 'completed',
        Queue.completed_at.isnot(None)
    ).all()

    if not completed_tickets:
        return None

    wait_times = [
        ticket.wait_time for ticket in completed_tickets
        if ticket.wait_time and ticket.wait_time <= 60
    ]

    return round(sum(wait_times) / len(wait_times)) if wait_times else 4  # Default avg_wait_time = 4

def get_average_completion_time(slot_start, slot_end):
    """Calculate average completion time for a specific time slot based on historical data"""
    # Ensure slot_start is timezone-aware
    slot_start = ensure_timezone(slot_start)
    slot_end = ensure_timezone(slot_end)

    week_ago = datetime.now(UTC) - timedelta(days=7)

    # Get completed tickets for this hour from past week
    hour = utc_to_local(slot_start).hour
    completed_tickets = Queue.query.filter(
        func.extract('hour', Queue.time_slot) == hour,
        Queue.status == 'completed',
        Queue.created_at >= week_ago,
        Queue.completed_at.isnot(None),
        # Ignore extremely long waits
        Queue.completed_at <= Queue.time_slot + timedelta(minutes=60)
    ).all()

    if not completed_tickets:
        return None

    completion_times = [
        (ticket.completed_at - ticket.time_slot)
        for ticket in completed_tickets
        if ticket.completed_at and
           timedelta(minutes=1) <= (ticket.completed_at - ticket.time_slot) <= timedelta(minutes=60)
    ]

    if len(completion_times) < 3:  # Require minimum sample size
        return None

    return sum(completion_times, timedelta()) / len(completion_times)

def get_available_slots(slot_start, slot_end):
    """Calculate available slots for a given time period"""
    # Ensure slot_start and slot_end are timezone-aware
    slot_start = ensure_timezone(slot_start)
    slot_end = ensure_timezone(slot_end)

    # Count ALL active tickets for this time slot (not just recent ones)
    active_count = Queue.query.filter(
        Queue.time_slot >= slot_start,
        Queue.time_slot < slot_end,
        Queue.status.in_(['waiting', 'in_progress'])
    ).count()

    # Start with default buffer
    buffer_slots = 2

    # Adjust buffer based on historical performance
    avg_completion_time = get_average_completion_time(slot_start, slot_end)
    if avg_completion_time:
        completion_minutes = avg_completion_time.total_seconds() / 60
        if completion_minutes < 10:
            buffer_slots = 0  # Fast service, no buffer needed
        elif completion_minutes > 20:
            buffer_slots = 3  # Slow service, more buffer

    available = MAX_SLOTS_PER_HOUR - active_count - buffer_slots
    return max(0, available)  # Ensure we don't return negative

# Password management
current_password = {"value": "", "expires_at": datetime.now(UTC)}

def generate_new_password():
    global current_password
    words = load_words_from_csv()
    password = random.choice(words)
    current_password = {
        "value": password,
        "expires_at": datetime.now(UTC) + timedelta(minutes=1)
    }
    return current_password

# Initialize the first password
generate_new_password()

# Decorators
def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'admin_logged_in' not in session:
            return redirect(url_for('admin_login'))
        return f(*args, **kwargs)
    return decorated_function

def require_admin_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth = request.headers.get('Authorization')
        if not auth:
            return jsonify({'error': 'No authorization header'}), 401

        try:
            auth_type, auth_string = auth.split(' ', 1)
            if auth_type.lower() != 'basic':
                return jsonify({'error': 'Invalid authorization type'}), 401

            username, password = base64.b64decode(auth_string).decode().split(':')
            if username != ADMIN_USERNAME or password != ADMIN_PASSWORD:
                return jsonify({'error': 'Invalid credentials'}), 401

        except Exception:
            return jsonify({'error': 'Invalid authorization format'}), 401

        return f(*args, **kwargs)
    return decorated

# Template configuration
app.jinja_env.globals.update(
    utc_to_local=utc_to_local,
    get_average_completion_time=get_average_completion_time,
    combine_date_time=combine_date_time,
    timedelta=timedelta
)

# Routes: Public
@app.route('/')
def index():
    # Ensure browser_id is set
    if 'browser_id' not in session:
        session.permanent = True
        session['browser_id'] = str(uuid.uuid4())
        print(f"New browser session created: {session['browser_id']}")

    current_local_time = datetime.now(LOCAL_TIMEZONE)
    current_local_date = current_local_time.date()
    time_slots = [
        time(hour=h) for h in range(9, 18)
        if h >= current_local_time.hour or current_local_time.date() < current_local_time.date()
    ]

    slot_counts = {}
    for slot_time in time_slots:
        slot_start = combine_date_time(current_local_date, slot_time)
        slot_end = slot_start + timedelta(hours=1)

        # Count active tickets directly for accurate count
        active_count = Queue.query.filter(
            Queue.time_slot >= slot_start,
            Queue.time_slot < slot_end,
            Queue.status.in_(['waiting', 'in_progress'])
        ).count()

        # Calculate available slots
        available = max(0, MAX_SLOTS_PER_HOUR - active_count)
        taken_slots = MAX_SLOTS_PER_HOUR - available

        # Debug: Log slot counts
        time_str = utc_to_local(slot_start).strftime('%H:%M')
        print(f"Time slot {time_str}: {active_count} active tickets, {available} slots available")

        slot_counts[time_str] = taken_slots

    return render_template('index.html',
                         time_slots=time_slots,
                         slot_counts=slot_counts,
                         max_slots=MAX_SLOTS_PER_HOUR,
                         current_local_date=current_local_date,
                         combine_date_time=combine_date_time,
                         timedelta=timedelta)

@app.route('/create_queue', methods=['POST'])
def create_queue():
    try:
        # Validate the location password first
        submitted_password = request.form.get('location_password')
        if submitted_password != current_password['value'] or \
           datetime.now(UTC) >= current_password['expires_at']:
            flash('Invalid or expired location password. Please try again.')
            return redirect(url_for('index'))

        name = request.form.get('name')
        time_slot_str = request.form.get('time_slot')

        # Ensure browser_id is set
        if 'browser_id' not in session:
            session.permanent = True
            session['browser_id'] = str(uuid.uuid4())

        browser_id = session['browser_id']

        # Convert time_slot_str to a datetime object
        local_time = datetime.strptime(time_slot_str, '%H:%M').time()
        local_date = datetime.now(LOCAL_TIMEZONE).date()
        local_datetime = datetime.combine(local_date, local_time)

        # Convert to UTC before storing
        utc_datetime = local_to_utc(local_datetime)

        # Check if the time slot is available
        slot_start = utc_datetime
        slot_end = slot_start + timedelta(hours=1)

        # Count active tickets directly for accurate count
        active_count = Queue.query.filter(
            Queue.time_slot >= slot_start,
            Queue.time_slot < slot_end,
            Queue.status.in_(['waiting', 'in_progress'])
        ).count()

        # Calculate available slots
        available_slots = max(0, MAX_SLOTS_PER_HOUR - active_count)

        # Debug: Log availability
        print(f"Time slot {time_slot_str}: {active_count} active tickets, {available_slots} slots available")

        if available_slots <= 0:
            flash(f'This time slot is full. Please select a different time.')
            return redirect(url_for('index'))

        # Generate queue code using local hour
        local_hour = utc_to_local(utc_datetime).hour
        hour_ampm = format_hour_ampm(local_hour)

        # Get max sequence number for this hour
        max_number = db.session.query(db.func.max(
            db.cast(db.func.substr(Queue.queue_code, 1, 2), db.Integer)
        )).filter(
            Queue.date == local_date,
            Queue.queue_code.like(f'__-{hour_ampm}-%')
        ).scalar() or 0

        next_number = max_number + 1

        # Generate queue code with retry logic
        max_attempts = 5
        for attempt in range(max_attempts):
            suffix = generate_random_suffix()
            queue_code = f"{next_number:02d}-{hour_ampm}-{suffix}"

            existing_code = Queue.query.filter_by(queue_code=queue_code).first()
            if not existing_code:
                break
        else:
            flash('Error generating unique queue code. Please try again.')
            return redirect(url_for('index'))

        queue = Queue(
            name=name,
            time_slot=utc_datetime,  # Store in UTC
            date=local_date,
            queue_code=queue_code,
            browser_id=browser_id
        )

        try:
            db.session.add(queue)
            db.session.commit()

            # Debug: Log the created queue
            print(f"Created queue ticket: {queue_code}, Browser ID: {browser_id}")

            flash(f'Queue ticket created successfully. Your code is: {queue_code}')
        except IntegrityError:
            db.session.rollback()
            flash('Error creating queue ticket. Please try again.')
            return redirect(url_for('index'))

        return redirect(url_for('view_my_queue'))
    except Exception as e:
        flash(f"An error occurred: {str(e)}", 'error')
        return redirect(url_for('index'))

@app.route('/view_my_queue')
def view_my_queue():
    # Ensure browser_id is set
    if 'browser_id' not in session:
        session.permanent = True
        session['browser_id'] = str(uuid.uuid4())
        return render_template('view_queue.html', queue=None)

    # Get the browser_id from session
    browser_id = session['browser_id']

    # Debug: Log the browser_id
    print(f"Looking for queue with browser_id: {browser_id}")

    # Find active tickets for this browser
    queue = Queue.query.filter_by(
        browser_id=browser_id
    ).filter(
        Queue.status.in_(['waiting', 'in_progress'])
    ).first()

    # Debug: Log the result
    if queue:
        print(f"Found queue: {queue.queue_code}, Status: {queue.status}")
    else:
        print("No active queue found for this browser")

    if queue and queue.status == 'waiting':
        current_seq = int(queue.queue_code.split('-')[0])

        people_ahead = Queue.query.filter(
            Queue.date == queue.date,
            Queue.status == 'waiting',
            db.or_(
                Queue.time_slot < queue.time_slot,
                db.and_(
                    Queue.time_slot == queue.time_slot,
                    db.cast(db.func.substr(Queue.queue_code, 1, 2), db.Integer) < current_seq
                )
            )
        ).count()

        queue.people_ahead = people_ahead

        # Get average wait time for today's completed tickets only
        avg_wait_time = get_average_wait_time(queue.date)
    if avg_wait_time:
        estimated_wait = min(avg_wait_time * (people_ahead + 1), 60)  # Cap at 1 hour
    else:
        avg_wait_time = 4  # Default average updated to 4
        estimated_wait = min(4 * (people_ahead + 1), 60)  # Cap at 1 hour

        return render_template('view_queue.html',
                             queue=queue,
                             avg_wait_time=avg_wait_time,
                             estimated_wait=estimated_wait)

    return render_template('view_queue.html', queue=queue)

@app.route('/cancel_queue/<queue_code>')
def cancel_queue(queue_code):
    queue = Queue.query.filter_by(
        queue_code=queue_code,
        browser_id=session['browser_id']
    ).first()

    if queue:
        queue.status = 'cancelled'
        db.session.commit()
        flash('Queue ticket cancelled successfully.')

    return redirect(url_for('index'))

# Routes: Admin
@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')

        if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
            session['admin_logged_in'] = True
            return redirect(url_for('admin_panel'))
        else:
            flash('Invalid credentials')

    return render_template('admin/login.html')

@app.route('/admin/')
@app.route('/admin/<string:selected_date>')
@admin_required
def admin_panel(selected_date=None):
    try:
        if selected_date:
            # Parse the date in Singapore timezone
            selected_date = datetime.strptime(selected_date, '%Y-%m-%d')
            selected_date = LOCAL_TIMEZONE.localize(selected_date).date()
        else:
            # Get current date in Singapore timezone
            selected_date = datetime.now(LOCAL_TIMEZONE).date()

        # Convert date range to UTC for database query
        day_start = LOCAL_TIMEZONE.localize(datetime.combine(selected_date, time.min))
        day_end = LOCAL_TIMEZONE.localize(datetime.combine(selected_date, time.max))

        # Ensure day_start and day_end are timezone-aware
        day_start = ensure_timezone(day_start, LOCAL_TIMEZONE)
        day_end = ensure_timezone(day_end, LOCAL_TIMEZONE)

        # Query using UTC range
        queues = Queue.query.filter(
            Queue.time_slot >= day_start,
            Queue.time_slot <= day_end
        ).order_by(Queue.time_slot).all()

        # Get all time_slots and convert to local dates in Python
        all_times = db.session.query(Queue.time_slot).distinct().all()
        available_dates = sorted(
            set(utc_to_local(time[0]).date() for time in all_times),
            reverse=True
        )

        return render_template('admin/panel.html',
                             queues=queues,
                             selected_date=selected_date,
                             available_dates=available_dates,
                             current_date=datetime.now(LOCAL_TIMEZONE).date())

    except Exception as e:
        flash(f"An error occurred: {str(e)}", 'error')
        return render_template('admin/panel.html',
                             queues=[],
                             selected_date=datetime.now(LOCAL_TIMEZONE).date(),
                             available_dates=[],
                             current_date=datetime.now(LOCAL_TIMEZONE).date())

@app.route('/admin/logout')
def admin_logout():
    session.pop('admin_logged_in', None)
    return redirect(url_for('admin_login'))

@app.route('/admin/update_status/<queue_code>', methods=['POST'])
@admin_required
def update_status(queue_code):
    queue = Queue.query.filter_by(queue_code=queue_code).first()
    if queue:
        new_status = request.form.get('status')
        if new_status in ['waiting', 'in_progress', 'completed', 'cancelled']:
            queue.status = new_status
            if new_status == 'completed':
                queue.completed_at = datetime.now(UTC)  # Use timezone-aware datetime
            db.session.commit()
            flash(f'Queue {queue_code} status updated to {new_status}')

    return redirect(url_for('admin_panel', selected_date=queue.date.strftime('%Y-%m-%d')))

# Routes: API
@app.route('/api/password', methods=['GET'])
@require_admin_auth
def get_current_password():
    """Secure API endpoint to get current password"""
    global current_password
    if datetime.now(UTC) >= current_password.get('expires_at', datetime.now(UTC)):
        generate_new_password()

    return jsonify({
        "password": current_password["value"],
        "expires_at": current_password["expires_at"].isoformat(),
        "next_update": (current_password["expires_at"] - datetime.now(UTC)).total_seconds()
    })

@app.route('/api/current_in_progress', methods=['GET'])
@require_admin_auth
def get_current_in_progress():
    """Get the earliest queue item that is currently in progress"""
    queue = Queue.query.filter_by(
        status='in_progress',
        date=datetime.now(LOCAL_TIMEZONE).date()
    ).order_by(
        Queue.time_slot
    ).first()

    return jsonify({
        "queue_code": queue.queue_code if queue else None,
        "name": queue.name if queue else None,
        "time_slot": queue.time_slot.isoformat() if queue else None,
        "wait_time": queue.wait_time if queue else None,
        "status": queue.status if queue else None,
        "message": "Queue found" if queue else "No queue items currently in progress"
    })

# Main
if __name__ == '__main__':
    # Define SSL context manually
    ssl_context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    ssl_context.load_cert_chain(
        certfile='D:/certificates/fullchain.pem',  # Local path to fullchain.pem
        keyfile='D:/certificates/privkey.pem'      # Local path to privkey.pem
    )

    # Wrap the socket with SSL
    eventlet_socket = eventlet.listen(('0.0.0.0', 5000))
    wrapped_socket = eventlet.wrap_ssl(
        eventlet_socket,
        certfile='D:/certificates/fullchain.pem',
        keyfile='D:/certificates/privkey.pem',
        server_side=True
    )

    # Run the server
    socketio.run(app, debug=True, socket=wrapped_socket)