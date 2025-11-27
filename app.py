from flask import Flask, redirect, url_for, session, render_template, request, flash, make_response
from flask_sqlalchemy import SQLAlchemy
from flask_dance.contrib.google import make_google_blueprint, google
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
import pandas as pd
import os
import re
import io
from xhtml2pdf import pisa # Library PDF

# === Import modul ANP ===
# Pastikan folder 'anp' ada dan berisi anp_processor.py
from anp.anp_processor import run_anp_analysis

app = Flask(__name__)
app.secret_key = "supersecretkey"

# === KONFIGURASI ===
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

# === MODEL DATABASE ===
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100))
    email = db.Column(db.String(120), unique=True)
    password = db.Column(db.String(255), nullable=True)
    picture = db.Column(db.String(250))
    sales = db.relationship('Sale', backref='owner', lazy=True)

class Sale(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    item_name = db.Column(db.String(100), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    price = db.Column(db.Float, nullable=False)
    profit = db.Column(db.Float, nullable=False)

# === FILTER FORMAT RUPIAH ===
@app.template_filter('rupiah')
def rupiah_format(value):
    try:
        return "Rp {:,.0f}".format(float(value)).replace(',', '.')
    except:
        return "Rp 0"

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

def convert_youtube_embed(url):
    if not isinstance(url, str): return None
    youtube_regex = (r'(https?://)?(www\.)?(youtube|youtu|youtube-nocookie)\.(com|be)/(watch\?v=|embed/|v/|.+\?v=)?([^&=%\?]{11})')
    match = re.match(youtube_regex, url)
    return f"https://www.youtube.com/embed/{match.group(6)}" if match else None

def create_pdf(html_content):
    result = io.BytesIO()
    pdf = pisa.pisaDocument(io.BytesIO(html_content.encode("UTF-8")), result)
    if not pdf.err:
        return result.getvalue()
    return None

# === ROUTES UTAMA ===
@app.route("/")
def index(): return render_template("landing.html")

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name, email, pwd = request.form.get("name"), request.form.get("email"), request.form.get("password")
        if User.query.filter_by(email=email).first():
            flash("Email sudah terdaftar.", "warning"); return redirect(url_for("login"))
        db.session.add(User(name=name, email=email, password=generate_password_hash(pwd, method='pbkdf2:sha256'), picture=None))
        db.session.commit()
        flash("Berhasil daftar.", "success"); return redirect(url_for("login"))
    return render_template("register.html")

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email, pwd = request.form.get("email"), request.form.get("password")
        user = User.query.filter_by(email=email).first()
        if user and user.password and check_password_hash(user.password, pwd):
            session["user_id"] = user.id; session["user_name"] = user.name
            session["user_picture"] = user.picture if user.picture else f"https://ui-avatars.com/api/?name={user.name}"
            return redirect(url_for("dashboard"))
        flash("Login gagal.", "danger")
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
        db.session.add(user); db.session.commit()
    session["user_id"] = user.id; session["user_name"] = user.name; session["user_picture"] = user.picture
    return redirect(url_for("dashboard"))

@app.route("/logout")
def logout(): session.clear(); return redirect(url_for("index"))

@app.route("/dashboard")
def dashboard():
    if "user_id" not in session: return redirect(url_for("login"))
    return render_template("dashboard.html", name=session["user_name"], picture=session["user_picture"])

# --- SPK ROUTE ---
@app.route("/upload", methods=["GET", "POST"])
def upload_file():
    if "user_id" not in session: return redirect(url_for("login"))
    if request.method == "POST":
        file = request.files.get("file")
        if file and allowed_file(file.filename):
            filename = secure_filename(file.filename)
            filepath = os.path.join(app.config["UPLOAD_FOLDER"], filename)
            file.save(filepath)
            session['uploaded_filepath'] = filepath 
            try:
                df = pd.read_csv(filepath, sep=";", engine="python") if filename.endswith(".csv") else pd.read_excel(filepath)
                if filename.endswith(".csv") and len(df.columns) == 1: df = pd.read_csv(filepath, sep=",", engine="python")
                
                video_map = {}
                col_name = df.columns[0]
                col_video = next((c for c in df.columns if "video" in c.lower() or "link" in c.lower()), None)
                if col_video:
                    for idx, row in df.iterrows():
                        video_map[str(row[col_name])] = convert_youtube_embed(str(row[col_video]))

                result_data = run_anp_analysis(df)
                for item in result_data["ranking"]:
                    item["video_url"] = video_map.get(item["Alternatif"])

                return render_template("upload.html", uploaded=True, tables=[df.to_html(classes="table", index=False)], chart=result_data["chart"], results=result_data["ranking"], info=result_data, name=session["user_name"])
            except Exception as e: flash(f"Error: {e}", "danger")
    return render_template("upload.html", uploaded=False, name=session["user_name"])

# --- FINANCE ROUTE ---
@app.route("/finance", methods=["GET", "POST"])
def finance():
    if "user_id" not in session: return redirect(url_for("login"))
    if request.method == "POST":
        item_name = request.form.get("item_name")
        quantity = int(request.form.get("quantity"))
        price = float(request.form.get("price"))
        profit = float(request.form.get("profit"))
        
        new_sale = Sale(user_id=session["user_id"], item_name=item_name, quantity=quantity, price=price, profit=profit)
        db.session.add(new_sale)
        db.session.commit()
        flash("Data tersimpan!", "success")
        return redirect(url_for("finance"))

    sales_data = Sale.query.filter_by(user_id=session["user_id"]).all()
    total_profit = sum(s.profit for s in sales_data)
    return render_template("finance.html", name=session["user_name"], sales=sales_data, total_profit=total_profit)

@app.route("/finance/delete/<int:id>")
def delete_sale(id):
    if "user_id" not in session: return redirect(url_for("login"))
    sale = Sale.query.get_or_404(id)
    if sale.user_id == session["user_id"]:
        db.session.delete(sale)
        db.session.commit()
    return redirect(url_for("finance"))

# --- PDF DOWNLOAD ROUTES ---
@app.route("/download/spk")
def download_spk_pdf():
    if "user_id" not in session:
        return redirect(url_for("login"))

    filepath = session.get('uploaded_filepath')
    if not filepath or not os.path.exists(filepath):
        flash("Upload data dulu.", "warning")
        return redirect(url_for("upload_file"))

    try:
        df = pd.read_csv(filepath, sep=";", engine="python") if filepath.endswith(".csv") else pd.read_excel(filepath)
        if filepath.endswith(".csv") and len(df.columns) == 1:
            df = pd.read_csv(filepath, sep=",", engine="python")

        result_data = run_anp_analysis(df)

        html = render_template(
            "pdf_spk.html",
            name=session["user_name"],
            results=result_data["ranking"],
            info=result_data,
            table_data=df.to_html(classes="table table-bordered", index=False)
        )

        # === Tambahkan CSS dari /static/css/pages/pdf_spk.css ===
        css_path = os.path.join(app.static_folder, "css", "pages", "pdf_spk.css")
        with open(css_path, "r", encoding="utf-8") as css_file:
            css_content = css_file.read()

        # Satukan HTML dan CSS
        full_html = f"<style>{css_content}</style>{html}"

        pdf = create_pdf(full_html)
        response = make_response(pdf)
        response.headers['Content-Type'] = 'application/pdf'
        response.headers['Content-Disposition'] = 'attachment; filename=Laporan_SPK_Analisis.pdf'
        return response

    except Exception as e:
        flash(f"Gagal membuat PDF: {e}", "danger")
        return redirect(url_for("upload_file"))


@app.route("/download/finance")
def download_finance_pdf():
    if "user_id" not in session: return redirect(url_for("login"))
    sales_data = Sale.query.filter_by(user_id=session["user_id"]).all()
    total_profit = sum(s.profit for s in sales_data)
    html = render_template("pdf_finance.html", name=session["user_name"], sales=sales_data, total_profit=total_profit)
    pdf = create_pdf(html)
    response = make_response(pdf)
    response.headers['Content-Type'] = 'application/pdf'
    response.headers['Content-Disposition'] = 'attachment; filename=Laporan_Keuangan.pdf'
    return response

if __name__ == "__main__":
    with app.app_context(): db.create_all()
    app.run(debug=True)