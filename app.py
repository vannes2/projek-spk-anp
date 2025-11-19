from flask import Flask, redirect, url_for, session, render_template, request, flash
from flask_sqlalchemy import SQLAlchemy
from flask_dance.contrib.google import make_google_blueprint, google
from werkzeug.utils import secure_filename
import pandas as pd
import os

# === Import modul ANP dari file terpisah ===
from anp.anp_processor import run_anp_analysis


# === Inisialisasi Flask ===
app = Flask(__name__)
app.secret_key = "supersecretkey"  # Ganti dengan key yang lebih aman

# === PATH DATABASE ===
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DB_PATH = os.path.join(BASE_DIR, "database", "spk_anp.db")

# Pastikan folder database dan uploads ada
os.makedirs(os.path.join(BASE_DIR, "database"), exist_ok=True)
UPLOAD_FOLDER = os.path.join(BASE_DIR, "uploads")
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# Konfigurasi Flask
app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{DB_PATH}"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
ALLOWED_EXTENSIONS = {"csv", "xlsx", "xls"}


# === Inisialisasi SQLAlchemy ===
db = SQLAlchemy(app)


# === MODEL DATABASE ===
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100))
    email = db.Column(db.String(120), unique=True)
    picture = db.Column(db.String(250))


# === KONFIGURASI GOOGLE OAUTH ===
os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"  # biar bisa jalan di localhost tanpa HTTPS

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


# === HELPER: cek ekstensi file ===
def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


# === ROUTES ===

@app.route("/")
def index():
    return render_template("landing.html")


@app.route("/login")
def login():
    return render_template("login.html")


@app.route("/login/google")
def google_login():
    return redirect(url_for("google.login"))


@app.route("/login/authorized")
def after_login():
    if not google.authorized:
        return redirect(url_for("google.login"))

    resp = google.get("/oauth2/v2/userinfo")
    if not resp.ok:
        return redirect(url_for("google.login"))

    user_info = resp.json()

    # Cek user di database
    user = User.query.filter_by(email=user_info["email"]).first()
    if not user:
        user = User(
            name=user_info["name"],
            email=user_info["email"],
            picture=user_info["picture"],
        )
        db.session.add(user)
        db.session.commit()

    # Simpan ke session
    session["user_id"] = user.id
    session["user_name"] = user.name
    session["user_picture"] = user.picture

    return redirect(url_for("dashboard"))


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
    """Halaman upload Excel/CSV dan hasil perhitungan ANP."""
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

        # Baca file pakai Pandas
        try:
            # Deteksi otomatis delimiter dan format file
            if filename.endswith(".csv"):
                # Coba baca dengan titik koma (;) karena banyak CSV Indonesia pakai ini
                df = pd.read_csv(filepath, sep=";")

                # Jika masih terbaca 1 kolom, coba ulang dengan koma (,)
                if len(df.columns) == 1:
                    df = pd.read_csv(filepath, sep=",")

            else:
                # Format Excel (.xlsx, .xls)
                df = pd.read_excel(filepath)

            # Debug tampilkan hasil kolom
            print("\n=== DEBUG UPLOAD FILE ===")
            print("File terbaca dengan kolom:", df.columns.tolist())
            print("Jumlah kolom:", len(df.columns))
            print("=========================\n")

        except Exception as e:
            flash(f"❌ Gagal membaca file: {e}")
            return redirect(request.url)

        # Jalankan perhitungan ANP
        try:
            result_data = run_anp_analysis(df)
            results = result_data["ranking"]
            chart_path = result_data["chart"]
            info = {
                "weights": result_data["weights"],
                "CI": result_data["CI"],
                "CR": result_data["CR"],
                "summary": result_data["summary"]
            }
        except Exception as e:
            flash(f"❌ Terjadi kesalahan saat menghitung ANP: {e}")
            return redirect(request.url)

        # Kirim hasil ke halaman HTML
        return render_template(
            "upload.html",
            uploaded=True,
            tables=[df.to_html(classes="table table-bordered", index=False)],
            chart=chart_path,
            results=results,
            info=info,
            name=session["user_name"],
        )

    # Jika metode GET (belum upload)
    return render_template("upload.html", uploaded=False, name=session["user_name"])


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))


# === JALANKAN APLIKASI ===
if __name__ == "__main__":
    with app.app_context():
        db.create_all()
    app.run(debug=True)
