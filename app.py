from flask import Flask, redirect, url_for, session, render_template, request, flash
from flask_sqlalchemy import SQLAlchemy
from flask_dance.contrib.google import make_google_blueprint, google
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash # Import tambahan untuk password
import pandas as pd
import os

# === Import modul ANP ===
# Pastikan folder 'anp' dan file 'anp_processor.py' tetap ada di tempatnya
from anp.anp_processor import run_anp_analysis

# === Inisialisasi Flask ===
app = Flask(__name__)
app.secret_key = "supersecretkey"  # Ganti dengan key yang aman untuk production

# === PATH DATABASE & UPLOAD ===
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DB_PATH = os.path.join(BASE_DIR, "database", "spk_anp.db")
UPLOAD_FOLDER = os.path.join(BASE_DIR, "uploads")

os.makedirs(os.path.join(BASE_DIR, "database"), exist_ok=True)
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# Konfigurasi Flask
app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{DB_PATH}"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
ALLOWED_EXTENSIONS = {"csv", "xlsx", "xls"}

db = SQLAlchemy(app)

# === MODEL DATABASE ===
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100))
    email = db.Column(db.String(120), unique=True)
    # Password boleh kosong (nullable) khusus untuk user yang login via Google
    password = db.Column(db.String(255), nullable=True) 
    picture = db.Column(db.String(250))

# === GOOGLE OAUTH CONFIG ===
# Hapus baris ini jika sudah deploy ke production (HTTPS)
os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"

google_bp = make_google_blueprint(
    client_id="202395510870-3o4jasv2hbtjksqbm0m2c2ihs4l66g7c.apps.googleusercontent.com",
    client_secret="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",
    scope=[
        "https://www.googleapis.com/auth/userinfo.profile",
        "https://www.googleapis.com/auth/userinfo.email",
        "openid",
    ],
    redirect_to="after_login",
)
app.register_blueprint(google_bp, url_prefix="/login")

# === HELPER ===
def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS

# === ROUTES UTAMA ===
@app.route("/")
def index():
    return render_template("landing.html")

# --- FITUR REGISTER (BARU) ---
@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name = request.form.get("name")
        email = request.form.get("email")
        password = request.form.get("password")

        # Validasi sederhana
        if not name or not email or not password:
            flash("⚠️ Semua kolom wajib diisi!", "warning")
            return redirect(url_for("register"))

        # Cek apakah email sudah ada
        existing_user = User.query.filter_by(email=email).first()
        if existing_user:
            flash("⚠️ Email sudah terdaftar! Silakan login.", "warning")
            return redirect(url_for("login"))

        # Hash password dan simpan
        hashed_pw = generate_password_hash(password, method='pbkdf2:sha256')
        new_user = User(
            name=name, 
            email=email, 
            password=hashed_pw, 
            picture=None # User manual tidak punya foto profil google
        )
        
        db.session.add(new_user)
        db.session.commit()

        flash("✅ Pendaftaran berhasil! Silakan login.", "success")
        return redirect(url_for("login"))

    return render_template("register.html")

# --- FITUR LOGIN MANUAL & GOOGLE (DIPERBARUI) ---
@app.route("/login", methods=["GET", "POST"])
def login():
    # Jika User Submit Form Login Manual
    if request.method == "POST":
        email = request.form.get("email")
        password = request.form.get("password")
        
        user = User.query.filter_by(email=email).first()

        # Cek user ada DAN password cocok
        if user and user.password and check_password_hash(user.password, password):
            session["user_id"] = user.id
            session["user_name"] = user.name
            # Gunakan placeholder jika tidak ada foto (user manual)
            session["user_picture"] = user.picture if user.picture else "https://ui-avatars.com/api/?name=" + user.name
            return redirect(url_for("dashboard"))
        else:
            flash("❌ Email atau Password salah!", "danger")
            return redirect(url_for("login"))

    # Jika method GET (Buka halaman)
    return render_template("login.html")

# --- GOOGLE AUTH ROUTES ---
@app.route("/login/google")
def google_login():
    if not google.authorized:
        return redirect(url_for("google.login"))
    return redirect(url_for("dashboard"))

@app.route("/login/authorized")
def after_login():
    if not google.authorized:
        return redirect(url_for("google.login"))
    resp = google.get("/oauth2/v2/userinfo")
    if not resp.ok:
        return redirect(url_for("google.login"))
    
    user_info = resp.json()
    user = User.query.filter_by(email=user_info["email"]).first()
    
    if not user:
        user = User(
            name=user_info["name"],
            email=user_info["email"],
            picture=user_info["picture"],
            password=None # User Google tidak punya password
        )
        db.session.add(user)
        db.session.commit()
    
    session["user_id"] = user.id
    session["user_name"] = user.name
    session["user_picture"] = user.picture
    return redirect(url_for("dashboard"))

# --- DASHBOARD & LOGIC ---
@app.route("/dashboard")
def dashboard():
    if "user_id" not in session:
        return redirect(url_for("login"))
    return render_template(
        "dashboard.html",
        name=session["user_name"],
        picture=session["user_picture"],
    )

@app.route("/upload", methods=["GET", "POST"])
def upload_file():
    if "user_id" not in session:
        return redirect(url_for("login"))

    if request.method == "POST":
        file = request.files.get("file")

        if not file:
            flash("⚠️ Tidak ada file yang diunggah.")
            return redirect(request.url)

        filename = secure_filename(file.filename)
        if not allowed_file(filename):
            flash("⚠️ Format file tidak didukung! Gunakan CSV atau Excel (.xlsx/.xls)")
            return redirect(request.url)

        filepath = os.path.join(app.config["UPLOAD_FOLDER"], filename)
        file.save(filepath)

        try:
            if filename.endswith(".csv"):
                df = pd.read_csv(filepath, sep=";", engine="python")
                if len(df.columns) == 1:
                    df = pd.read_csv(filepath, sep=",", engine="python")
            else:
                df = pd.read_excel(filepath)
            
        except Exception as e:
            flash(f"❌ Gagal membaca file: {e}")
            return redirect(request.url)

        try:
            result_data = run_anp_analysis(df)
            results = result_data["ranking"]
            chart_path = result_data["chart"]
            info = {
                "weights": result_data["weights"],
                "CI": result_data["CI"],
                "CR": result_data["CR"],
                "summary": result_data["summary"],
            }
            supermatrix_html = result_data.get("supermatrix_html", "")
            limitmatrix_html = result_data.get("limitmatrix_html", "")

        except Exception as e:
            print(f"ERROR ANP: {e}") 
            flash(f"❌ Terjadi kesalahan saat menghitung ANP: {e}")
            return redirect(request.url)

        return render_template(
            "upload.html",
            uploaded=True,
            tables=[df.to_html(classes="table table-bordered", index=False)],
            chart=chart_path,
            results=results,
            info=info,
            supermatrix_html=supermatrix_html,
            limitmatrix_html=limitmatrix_html,
            name=session["user_name"],
        )

    return render_template("upload.html", uploaded=False, name=session["user_name"])

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))

if __name__ == "__main__":
    with app.app_context():
        db.create_all()
    app.run(debug=True)