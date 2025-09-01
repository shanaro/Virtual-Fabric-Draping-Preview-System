import os
import tempfile
import cv2
import pyvista as pv
import nest_asyncio
import numpy as np
from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    jsonify,
    session,
    flash
)
from flask_mail import Mail, Message
from logging.handlers import SMTPHandler
import logging
import sqlite3
from datetime import datetime

# Firestore imports
import firebase_admin
from firebase_admin import credentials, firestore
import pyrebase

# Configure logging first
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize SQLite database
def init_db():
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    
    # Create users table (1-to-many relationship with logs)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username VARCHAR(255) UNIQUE NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Create event_def table for event type definitions
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS event_def (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            template VARCHAR(255) NOT NULL,
            description TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Create logs table with proper foreign key relationship
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lg_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            user_id INTEGER NOT NULL,
            event_type INTEGER NOT NULL,
            additional_data TEXT,
            ip_address VARCHAR(45),
            user_agent TEXT,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY (event_type) REFERENCES event_def(id) ON DELETE CASCADE
        )
    ''')
    
    # Insert default event types if they don't exist
    default_events = [
        (1, "User Login", "User successfully logged into the system"),
        (2, "3D Model View", "User viewed a 3D model"),
        (3, "Photo Capture", "User captured a photo from camera"),
        (4, "Fabric Upload", "User uploaded a fabric image"),
        (5, "Admin Login", "Administrator logged into admin panel"),
        (6, "Model Upload", "Admin uploaded a new 3D model"),
        (7, "Model Delete", "Admin deleted a 3D model"),
        (8, "Model Edit", "Admin edited model properties")
    ]
    
    for event_id, template, description in default_events:
        cursor.execute('''
            INSERT OR IGNORE INTO event_def (id, template, description) 
            VALUES (?, ?, ?)
        ''', (event_id, template, description))
    
    # Create indexes for better performance
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_logs_user_id ON logs(user_id)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_logs_event_type ON logs(event_type)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_logs_time ON logs(lg_time)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_users_username ON users(username)')
    
    conn.commit()
    conn.close()

# Run the initialization when app starts
init_db()

def get_sqlite_connection():
    conn = sqlite3.connect('database.db')
    conn.row_factory = sqlite3.Row
    return conn

def log_user_event(user_id=None, event_type=None, username=None, additional_data=None, ip_address=None, user_agent=None):
    conn = get_sqlite_connection()
    cursor = conn.cursor()
    
    try:
        # If username is provided but user_id isn't, create/get user
        if username and not user_id:
            cursor.execute("INSERT OR IGNORE INTO users (username) VALUES (?)", (username,))
            cursor.execute("SELECT id FROM users WHERE username = ?", (username,))
            user_row = cursor.fetchone()
            user_id = user_row[0] if user_row else None
        
        if user_id and event_type:
            cursor.execute(
                "INSERT INTO logs (user_id, event_type, additional_data, ip_address, user_agent) VALUES (?, ?, ?, ?, ?)", 
                (user_id, event_type, additional_data, ip_address, user_agent)
            )
            conn.commit()
            logger.info(f"Logged event: User {user_id} performed event {event_type}")
        else:
            logger.warning(f"Failed to log event: user_id={user_id}, event_type={event_type}")
    except Exception as e:
        logger.error(f"Failed to log user event: {str(e)}")
    finally:
        conn.close()

# Firebase configuration
firebase_config = {
    "apiKey": "AIzaSyCsJN4cQXMN4PTuZ-Ki160I-fPVkMlq650",
    "authDomain": "ar-tech-f874a.firebaseapp.com",
    "projectId": "ar-tech-f874a",
    "storageBucket": "ar-tech-f874a.appspot.com",
    "messagingSenderId": "599046200568",
    "appId": "1:599046200568:web:0f8a4d30f829f6f4eeaf0d",
    "databaseURL": ""
}

# Initialize Firebase and Firestore
if not os.path.exists('serviceAccountKey.json'):
    logger.error("Service account key file not found. Firebase services will be disabled.")
    db = None
    firebase = None
    auth = None
else:
    try:
        # Check if Firebase app is already initialized
        try:
            firebase_admin.get_app()
        except ValueError:
            # No app exists, initialize a new one
            cred = credentials.Certificate('serviceAccountKey.json')
            firebase_admin.initialize_app(cred)
        
        db = firestore.client()
        firebase = pyrebase.initialize_app(firebase_config)
        auth = firebase.auth()
    except Exception as e:
        logger.error(f"Failed to initialize Firebase: {str(e)}")
        # Set fallback values
        db = None
        firebase = None
        auth = None

# Force PyVista to use the panel backend for interactive export
os.environ["PYVISTA_WEB_EXPORT_BACKEND"] = "panel"
nest_asyncio.apply()

# Flask config
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-key")
UPLOAD_FOLDER = os.path.join(os.getcwd(), "uploads")
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER

# Check if uploads folder exists and is writable
if not os.access(UPLOAD_FOLDER, os.W_OK):
    logger.error(f"Upload folder {UPLOAD_FOLDER} is not writable")

# Flask-Mail configuration
app.config['MAIL_SERVER'] = 'live.smtp.mailtrap.io'
app.config['MAIL_PORT'] = 587
app.config['MAIL_USERNAME'] = 'api'
app.config['MAIL_PASSWORD'] = '40f43da658219ca0b2eede26f66b8380'
app.config['MAIL_USE_TLS'] = True
app.config['MAIL_USE_SSL'] = False
app.config['MAIL_DEFAULT_SENDER'] = 'no-reply@demomailtrap.co'
app.config['ADMINS'] = ['roshanabeagam@gmail.com']
mail = Mail(app)

if not app.debug:
    mail_handler = SMTPHandler(
        mailhost=(app.config['MAIL_SERVER'], app.config['MAIL_PORT']),
        fromaddr=app.config['MAIL_DEFAULT_SENDER'],
        toaddrs=app.config['ADMINS'],
        subject='Application Error',
        credentials=(app.config['MAIL_USERNAME'], app.config['MAIL_PASSWORD']),
        secure=()
    )
    mail_handler.setLevel(logging.ERROR)
    app.logger.addHandler(mail_handler)

# Helper functions
def verify_login(username, password):
    if auth is None:
        logger.error("Firebase authentication not available")
        return False
    try:
        user = auth.sign_in_with_email_and_password(username, password)
        return True
    except Exception as e:
        logger.error(f"Login failed: {str(e)}")
        return False

def log_admin_activity(action, details=""):
    if session.get("admin_logged_in") and db is not None:
        try:
            db.collection("admin_logs").add({
                "action": action,
                "details": details,
                "timestamp": firestore.SERVER_TIMESTAMP
            })
        except Exception as e:
            logger.error(f"Failed to log admin activity: {str(e)}")

def get_model_by_filename(filename):
    if db is None:
        logger.error("Firestore database not available")
        return None
    try:
        docs = db.collection('models').where('filename', '==', filename).limit(1).stream()
        for doc in docs:
            data = doc.to_dict()
            return (doc.id, data.get('filename'), data.get('height_percentage', '100'))
        return None
    except Exception as e:
        logger.error(f"Failed to get model by filename: {str(e)}")
        return None

def get_all_models():
    if db is None:
        logger.error("Firestore database not available")
        return []
    try:
        models = []
        for doc in db.collection('models').stream():
            data = doc.to_dict()
            models.append((doc.id, data.get('filename'), data.get('height_percentage', '100')))
        return models
    except Exception as e:
        logger.error(f"Failed to get all models: {str(e)}")
        return []

def generate_model_html(model_filename, man_height="", man_width="", texture_frame=None):
    model_path = os.path.join(app.config["UPLOAD_FOLDER"], model_filename)
    if not os.path.exists(model_path):
        raise FileNotFoundError(f"Model file not found: {model_path}")
    
    mesh = pv.read(model_path)
    smoothed_mesh = mesh.smooth(n_iter=30, boundary_smoothing=True)

    texture = None
    if texture_frame is not None:
        frame_rgb = cv2.cvtColor(texture_frame, cv2.COLOR_BGR2RGB)
        smoothed_mesh = smoothed_mesh.texture_map_to_plane(inplace=False)
        texture = pv.numpy_to_texture(frame_rgb)

    plotter = pv.Plotter(off_screen=True)
    if texture is not None:
        plotter.add_mesh(
            smoothed_mesh,
            texture=texture,
            show_edges=False,
            smooth_shading=True
        )
    else:
        plotter.add_mesh(
            smoothed_mesh,
            color="lightgray",
            show_edges=False,
            smooth_shading=True
        )
    plotter.set_background("lightblue")
    plotter.camera_position = "xy"

    with tempfile.NamedTemporaryFile(suffix=".html", delete=False) as tmp_file:
        temp_filename = tmp_file.name
    
    plotter.export_html(temp_filename)
    plotter.close()

    with open(temp_filename, "r", encoding="utf-8") as f:
        plot_html = f.read()

    os.remove(temp_filename)
    return plot_html

# Routes
@app.route('/login', methods=['POST'])
def login():
    username = request.form.get('username')
    password = request.form.get('password')

    if not username or not password:
        return jsonify({"error": "Username and password are required"}), 400

    login_successful = verify_login(username, password)
    
    if login_successful:
        log_user_event(event_type=1, username=username)
        session['username'] = username
        session['user_email'] = username  # Store email for logging
        return jsonify({"status": "success"}), 200
    else:
        return jsonify({"error": "Invalid credentials"}), 401

@app.route('/', methods=['GET', 'POST'])
def index():
    models = get_all_models()
    available_filenames = [row[1] for row in models] if models else []
    
    if request.method == 'POST':
        model_name = request.form.get('model_name')
        man_height = request.form.get('man_height', '')
        man_width = request.form.get('man_width', '')

        if not model_name or model_name not in available_filenames:
            return jsonify({"error": "Invalid model selected"}), 400

        session['model_name'] = model_name
        session['man_height'] = man_height
        session['man_width'] = man_width
        
        return redirect(url_for('view_model'))
    
    return render_template('index.html', available_models=available_filenames)

@app.route('/view_model')
def view_model():
    model_name = session.get('model_name')
    man_height = session.get('man_height', '')
    man_width = session.get('man_width', '')
    
    available_filenames = [row[1] for row in get_all_models()] if get_all_models() else []
    if not model_name or model_name not in available_filenames:
        return redirect(url_for('index'))
    
    log_user_event(
        event_type=2,  # 'Viewed 3D Model'
        username=session.get('user_email', 'anonymous')  
    )
    
    model_record = get_model_by_filename(model_name)
    if model_record is None:
        height_percentage = "100"
    else:
        height_percentage = model_record[2] if len(model_record) > 2 else "100"

    try:
        computed_height = float(man_height) * (float(height_percentage) / 100)
    except (ValueError, TypeError):
        computed_height = man_height

    try:
        plot_html = generate_model_html(model_name, man_height, man_width)
        return render_template(
            'view_model.html',
            plot_html=plot_html,
            model_name=model_name,
            computed_height=computed_height,
            man_width=man_width
        )
    except Exception as e:
        logger.error(f"Error generating model: {str(e)}")
        flash("Error displaying model. Please try again.")
        return redirect(url_for('index'))

@app.route('/camera_feed', methods=['GET'])
def camera_feed():
    IP_CAMERA_STREAM = "http://192.168.8.171:8080/video"
    return render_template('camera_feed.html', ip_camera_url=IP_CAMERA_STREAM)

@app.route('/capture_photo', methods=['POST'])
def capture_photo():
    model_name = session.get('model_name')
    man_height = session.get('man_height', '')
    man_width = session.get('man_width', '')

    available_filenames = [row[1] for row in get_all_models()] if get_all_models() else []
    if not model_name or model_name not in available_filenames:
        return jsonify({"error": "No valid model in session."}), 400

    IP_CAMERA_STREAM = "http://192.168.8.171:8080/video"
    cap = cv2.VideoCapture(IP_CAMERA_STREAM)
    ret, frame = cap.read()
    cap.release()

    if not ret:
        return jsonify({"error": "Failed to capture from IP camera."}), 500

    try:
        plot_html = generate_model_html(model_name, man_height, man_width, texture_frame=frame)
        log_user_event(
            event_type=3,  # 'Captured Photo'
            username=session.get('user_email', 'anonymous')
        )
        return jsonify({"plot_html": plot_html})
    except Exception as e:
        logger.error(f"Error generating model with texture: {str(e)}")
        return jsonify({"error": "Failed to generate model view."}), 500

@app.route('/upload_photo', methods=['POST'])
def upload_photo():
    model_name = session.get('model_name')
    available_filenames = [row[1] for row in get_all_models()] if get_all_models() else []
    
    if not model_name or model_name not in available_filenames:
        return jsonify({"error": "No valid model in session."}), 400

    if 'photo' not in request.files:
        return jsonify({"error": "No photo provided."}), 400

    file = request.files['photo']
    if file.filename == '':
        return jsonify({"error": "No selected file."}), 400

    try:
        file_bytes = file.read()
        np_arr = np.frombuffer(file_bytes, np.uint8)
        img = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)
        
        if img is None:
            return jsonify({"error": "Invalid image file."}), 400

        log_user_event(
            event_type=4,  # 'Uploaded Fabric Image'
            username=session.get('user_email', 'anonymous')
        )

        man_height = session.get('man_height', '')
        man_width = session.get('man_width', '')
        plot_html = generate_model_html(model_name, man_height, man_width, texture_frame=img)
        
        return jsonify({"plot_html": plot_html})
    except Exception as e:
        logger.error(f"Error processing uploaded photo: {str(e)}")
        return jsonify({"error": "Error processing image."}), 500

@app.route('/adminpage', methods=['GET', 'POST'])
def admin_page():
    if 'admin_logged_in' not in session:
        if 'admin_failed_attempts' not in session:
            session['admin_failed_attempts'] = 0

        if request.method == "POST":
            admin_mail = request.form.get("admin_mail")
            admin_password = request.form.get("admin_password")
            
            if not admin_mail or not admin_password:
                flash("Both email and password are required.")
                return render_template("admin_login.html")

            if auth is None:
                flash("Authentication service not available. Please try again later.")
                return render_template("admin_login.html")
            try:
                user = auth.sign_in_with_email_and_password(admin_mail, admin_password)
                if admin_mail == "admin@gmail.com":
                    session["admin_logged_in"] = True
                    session['admin_failed_attempts'] = 0
                    log_admin_activity("Admin Login", f"Email: {admin_mail}")
                    return redirect(url_for("admin_page"))
                else:
                    flash("Not authorized as admin.")
            except Exception as e:
                session['admin_failed_attempts'] += 1
                flash("Invalid Login Please try Again")

                if session['admin_failed_attempts'] >= 2:
                    flash("Invalid Login Admin has been Notified!")
                    try:
                        msg = Message(
                            subject="🔐 Alert: Multiple Failed Admin Logins",
                            recipients=app.config['ADMINS'],
                            body=f"There have been {session['admin_failed_attempts']} failed login attempts to the admin panel.\n\nLast attempted email: {admin_mail}"
                        )
                        mail.send(msg)
                    except Exception as mail_error:
                        logger.error(f"Mail send failed: {str(mail_error)}")

            return render_template("admin_login.html")
        return render_template("admin_login.html")
    else:
        if request.method == "POST" and "stl_file" in request.files:
            stl_file = request.files["stl_file"]
            height_percentage = request.form.get("man_height", "").strip()

            if not height_percentage or stl_file.filename == '':
                flash("Both STL file and height percentage are required.")
                return redirect(url_for("admin_page"))

            try:
                float(height_percentage)  # Validate it's a number
            except ValueError:
                flash("Height percentage must be a number.")
                return redirect(url_for("admin_page"))

            filename = stl_file.filename
            if not filename.lower().endswith('.stl'):
                flash("Only STL files are allowed.")
                return redirect(url_for("admin_page"))

            save_path = os.path.join(app.config["UPLOAD_FOLDER"], filename)
            stl_file.save(save_path)

            if db is not None:
                db.collection("models").add({
                    "filename": filename,
                    "height_percentage": height_percentage,
                    "file_path": save_path,
                    "uploaded_at": firestore.SERVER_TIMESTAMP
                })
            else:
                flash("Database not available. Model file saved but not registered in database.")

            flash("STL model uploaded successfully.")
            log_admin_activity("Upload Model", f"Filename: {filename}, Height %: {height_percentage}")
            return redirect(url_for("admin_page"))

        models = get_all_models()
        return render_template("admin_dashboard.html", models=models if models else [])

@app.route('/delete_model/<model_id>', methods=['POST'])
def delete_model(model_id):
    if 'admin_logged_in' not in session:
        return redirect(url_for('admin_page'))

    if db is None:
        flash("Database not available. Cannot delete model.")
        return redirect(url_for("admin_page"))
    
    doc_ref = db.collection('models').document(model_id)
    doc = doc_ref.get()
    if doc.exists:
        try:
            data = doc.to_dict()
            file_path = data.get('file_path')
            if file_path and os.path.exists(file_path):
                os.remove(file_path)
            doc_ref.delete()
            flash("Model deleted successfully!")
            log_admin_activity("Delete Model", f"Model ID: {model_id}")
        except Exception as e:
            logger.error(f"Error deleting model: {str(e)}")
            flash("Error deleting model.")
    else:
        flash("Model not found.")
    return redirect(url_for("admin_page"))

@app.route('/edit_model/<model_id>', methods=['GET', 'POST'])
def edit_model(model_id):
    if 'admin_logged_in' not in session:
        return redirect(url_for('admin_page'))

    if db is None:
        flash("Database not available. Cannot edit model.")
        return redirect(url_for("edit_model", model_id=model_id))
    
    doc_ref = db.collection('models').document(model_id)
    if request.method == "POST":
        height_percentage = request.form.get("man_height", "").strip()
        if not height_percentage:
            flash("Height percentage is required.")
            return redirect(url_for("edit_model", model_id=model_id))
        
        try:
            float(height_percentage)  # Validate it's a number
        except ValueError:
            flash("Height percentage must be a number.")
            return redirect(url_for("edit_model", model_id=model_id))

        try:
            doc_ref.update({'height_percentage': height_percentage})
            flash("Model updated successfully!")
            log_admin_activity("Edit Model", f"Model ID: {model_id}, New Height %: {height_percentage}")
        except Exception as e:
            flash(f"Error updating model: {str(e)}")
        return redirect(url_for("admin_page"))
    else:
        doc = doc_ref.get()
        if not doc.exists:
            flash("Model not found.")
            return redirect(url_for("admin_page"))
        data = doc.to_dict()
        model = (doc.id, data.get('filename'), data.get('height_percentage'))
        return render_template("edit_model.html", model=model)

@app.route('/admin_logs')
def admin_logs():
    if 'admin_logged_in' not in session:
        return redirect(url_for('admin_page'))

    if db is None:
        flash("Database not available. Cannot retrieve logs.")
        return redirect(url_for("admin_page"))

    try:
        logs = db.collection("admin_logs").order_by("timestamp", direction=firestore.Query.DESCENDING).limit(100).stream()
        log_entries = []
        for log in logs:
            entry = log.to_dict()
            entry["id"] = log.id
            log_entries.append(entry)
        return render_template("admin_logs.html", logs=log_entries)
    except Exception as e:
        logger.error(f"Failed to retrieve admin logs: {str(e)}")
        flash("Error retrieving logs.")
        return redirect(url_for("admin_page"))

@app.route('/logout')
def logout():
    if 'admin_logged_in' in session:
        log_admin_activity("Admin Logout")
        session.pop("admin_logged_in", None)
    return redirect(url_for("admin_page"))

@app.errorhandler(404)
def page_not_found(e):
    return render_template('404.html'), 404

@app.errorhandler(500)
def internal_server_error(e):
    logger.error(f"Server error: {str(e)}")
    return render_template('500.html'), 500

if __name__ == '__main__':
    # Print startup information
    print("=" * 50)
    print("AR Tech Application Starting...")
    print(f"Firebase Status: {'Connected' if db is not None else 'Disabled'}")
    print(f"Upload Folder: {UPLOAD_FOLDER}")
    print(f"Templates: {len(os.listdir('templates'))} files found")
    print(f"Static Files: {len(os.listdir('static')) if os.path.exists('static') else 0} files found")
    print("=" * 50)
    
    app.run(host='0.0.0.0', port=5000, debug=False)