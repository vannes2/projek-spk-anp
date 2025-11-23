from flask import Flask, redirect, url_for, session, render_template, request, flash
from flask_sqlalchemy import SQLAlchemy
from flask_dance.contrib.google import make_google_blueprint, google
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
import pandas as pd
import numpy as np
import matplotlib
matplotlib.use("Agg") # Mode server (tanpa GUI window)
import matplotlib.pyplot as plt
import os

# === Import modul ANP ===
# Pastikan folder 'anp' ada dan berisi anp_processor.py
from anp.anp_processor import run_anp_analysis

app = Flask(__name__)
app.secret_key = "supersecretkey" # Ganti dengan key yang aman

# === KONFIGURASI PATH ===
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DB_PATH = os.path.join(BASE_DIR, "database", "spk_anp.db")
UPLOAD_FOLDER = os.path.join(BASE_DIR, "uploads")
CHART_DIR = os.path.join(BASE_DIR, "static", "charts")

os.makedirs(os.path.join(BASE_DIR, "database"), exist_ok=True)
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(CHART_DIR, exist_ok=True)

app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{DB_PATH}"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
ALLOWED_EXTENSIONS = {"csv", "xlsx", "xls"}

db = SQLAlchemy(app)

# === MODEL DATABASE (Hanya User) ===
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100))
    email = db.Column(db.String(120), unique=True)
    password = db.Column(db.String(255), nullable=True)
    picture = db.Column(db.String(250))

# === GOOGLE OAUTH ===
os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"
google_bp = make_google_blueprint(
    client_id="202395510870-3o4jasv2hbtjksqbm0m2c2ihs4l66g7c.apps.googleusercontent.com",
    client_secret="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",
    scope=["https://www.googleapis.com/auth/userinfo.profile", "https://www.googleapis.com/auth/userinfo.email", "openid"],
    redirect_to="after_login",
)
app.register_blueprint(google_bp, url_prefix="/login")

# === HELPER FUNCTIONS ===
def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS

def identify_revenue_column(df):
    """Mencari kolom yang kemungkinan berisi data pendapatan"""
    for col in df.columns:
        c = col.lower()
        # Kata kunci: C2, Jual, Omzet, Pendapatan, Revenue
        if "c2" in c or "jual" in c or "pendapatan" in c or "omzet" in c:
            return col
    return None

def clean_currency(value):
    """Mengubah format '50 Juta', 'Rp 5.000.000' menjadi float"""
    v_str = str(value).lower().replace("rp", "").replace(".", "").replace(",", ".").strip()
    multiplier = 1
    
    if "juta" in v_str:
        multiplier = 1000000
        v_str = v_str.replace("juta", "").strip()
    elif "jt" in v_str:
        multiplier = 1000000
        v_str = v_str.replace("jt", "").strip()
    elif "milyar" in v_str or "m" in v_str: # Asumsi M = Milyar jika user kaya
        multiplier = 1000000000
        v_str = v_str.replace("milyar", "").replace("m", "").strip()

    try:
        # Ambil angka saja
        import re
        number = re.findall(r"[\d\.]+", v_str)
        if number:
            return float(number[0]) * multiplier
        return 0.0
    except:
        return 0.0

# === ROUTES UTAMA ===

@app.route("/")
def index(): return render_template("landing.html")

# --- AUTH ROUTES ---
@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name = request.form.get("name")
        email = request.form.get("email")
        password = request.form.get("password")
        if User.query.filter_by(email=email).first():
            flash("⚠️ Email sudah terdaftar.", "warning")
            return redirect(url_for("login"))
        new_user = User(name=name, email=email, password=generate_password_hash(password, method='pbkdf2:sha256'), picture=None)
        db.session.add(new_user)
        db.session.commit()
        flash("✅ Berhasil daftar! Silakan login.", "success")
        return redirect(url_for("login"))
    return render_template("register.html")

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email")
        password = request.form.get("password")
        user = User.query.filter_by(email=email).first()
        if user and user.password and check_password_hash(user.password, password):
            session["user_id"] = user.id
            session["user_name"] = user.name
            session["user_picture"] = user.picture if user.picture else f"https://ui-avatars.com/api/?name={user.name}"
            return redirect(url_for("dashboard"))
        else:
            flash("❌ Login gagal.", "danger")
    return render_template("login.html")

@app.route("/login/google")
def google_login():
    if not google.authorized: return redirect(url_for("google.login"))
    return redirect(url_for("dashboard"))

@app.route("/login/authorized")
def after_login():
    if not google.authorized: return redirect(url_for("google.login"))
    resp = google.get("/oauth2/v2/userinfo")
    if not resp.ok: return redirect(url_for("google.login"))
    info = resp.json()
    user = User.query.filter_by(email=info["email"]).first()
    if not user:
        user = User(name=info["name"], email=info["email"], picture=info["picture"], password=None)
        db.session.add(user)
        db.session.commit()
    session["user_id"] = user.id; session["user_name"] = user.name; session["user_picture"] = user.picture
    return redirect(url_for("dashboard"))

@app.route("/logout")
def logout(): session.clear(); return redirect(url_for("index"))

@app.route("/dashboard")
def dashboard():
    if "user_id" not in session: return redirect(url_for("login"))
    return render_template("dashboard.html", name=session["user_name"], picture=session["user_picture"])

# --- FITUR 1: UPLOAD & SPK ---
@app.route("/upload", methods=["GET", "POST"])
def upload_file():
    if "user_id" not in session: return redirect(url_for("login"))
    
    if request.method == "POST":
        file = request.files.get("file")
        if file and allowed_file(file.filename):
            filename = secure_filename(file.filename)
            filepath = os.path.join(app.config["UPLOAD_FOLDER"], filename)
            file.save(filepath)
            
            # PENTING: Simpan lokasi file di session agar bisa dibaca fitur Keuangan
            session['uploaded_filepath'] = filepath
            
            try:
                # Baca File
                if filename.endswith(".csv"):
                    df = pd.read_csv(filepath, sep=";", engine="python")
                    if len(df.columns) == 1: df = pd.read_csv(filepath, sep=",", engine="python")
                else:
                    df = pd.read_excel(filepath)
                
                # Jalankan ANP
                result_data = run_anp_analysis(df)
                
                return render_template(
                    "upload.html", 
                    uploaded=True, 
                    tables=[df.to_html(classes="table", index=False)], 
                    chart=result_data["chart"], 
                    results=result_data["ranking"], 
                    info=result_data, 
                    name=session["user_name"]
                )
            except Exception as e:
                flash(f"Error Analisis: {e}", "danger")
                return redirect(request.url)
    
    return render_template("upload.html", uploaded=False, name=session["user_name"])

# --- FITUR 2: KEUANGAN OTOMATIS (INTEGRASI) ---
@app.route("/finance")
def finance():
    if "user_id" not in session: return redirect(url_for("login"))
    
    # 1. Cek apakah ada file yang sudah diupload
    filepath = session.get('uploaded_filepath')
    if not filepath or not os.path.exists(filepath):
        flash("⚠️ Data kosong! Harap upload file Excel lokasi di menu 'Analisis SPK' terlebih dahulu.", "warning")
        return redirect(url_for("upload_file"))
    
    try:
        # 2. Baca kembali file Excel user
        if filepath.endswith(".csv"):
            df = pd.read_csv(filepath, sep=";", engine="python")
            if len(df.columns) == 1: df = pd.read_csv(filepath, sep=",", engine="python")
        else:
            df = pd.read_excel(filepath)
            
        # 3. Cari Data Pendapatan (Kolom C2 atau yang mirip)
        col_name = df.columns[0] # Kolom pertama biasanya Nama Lokasi
        col_revenue = identify_revenue_column(df)
        
        projections = []
        chart_url = None
        
        if col_revenue:
            names = []
            yearly_values = []
            
            for idx, row in df.iterrows():
                loc_name = str(row[col_name])
                raw_value = row[col_revenue]
                
                # Bersihkan data (misal: "50 Juta" -> 50000000)
                monthly_income = clean_currency(raw_value)
                yearly_income = monthly_income * 12 # Proyeksi 1 Tahun
                
                names.append(loc_name)
                yearly_values.append(yearly_income)
                
                projections.append({
                    "name": loc_name,
                    "raw": raw_value,
                    "monthly": monthly_income,
                    "yearly": yearly_income
                })
            
            # 4. Buat Visualisasi Grafik Batang (Bar Chart)
            plt.figure(figsize=(10, 6))
            # Warna batang berbeda tiap lokasi agar cantik
            colors = plt.cm.viridis(np.linspace(0, 1, len(names)))
            
            bars = plt.barh(names, yearly_values, color=colors)
            plt.xlabel('Proyeksi Omzet 1 Tahun (Rupiah)')
            plt.title('Perbandingan Potensi Pendapatan Lokasi')
            plt.grid(axis='x', linestyle='--', alpha=0.5)
            
            # Tambahkan label angka di ujung batang
            for bar in bars:
                width = bar.get_width()
                label_text = f'Rp {width:,.0f}'.replace(',', '.')
                plt.text(width, bar.get_y() + bar.get_height()/2, ' ' + label_text, va='center', fontsize=9)
            
            plt.tight_layout()
            
            # Simpan Grafik
            chart_filename = f"finance_proj_{session['user_id']}.png"
            chart_path = os.path.join(CHART_DIR, chart_filename)
            plt.savefig(chart_path)
            plt.close()
            
            chart_url = f"static/charts/{chart_filename}"
        else:
            flash("⚠️ Tidak ditemukan kolom pendapatan (C2/Omzet/Jual) di file Excel Anda.", "danger")
            
        return render_template("finance.html", name=session["user_name"], projections=projections, chart_url=chart_url)
        
    except Exception as e:
        flash(f"Gagal memproses data keuangan: {e}", "danger")
        return redirect(url_for("dashboard"))

if __name__ == "__main__":
    with app.app_context(): db.create_all()
    app.run(debug=True)