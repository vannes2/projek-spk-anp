from flask import Flask, redirect, url_for, session, render_template, request, flash, make_response, send_file, abort
from flask_sqlalchemy import SQLAlchemy
from flask_dance.contrib.google import make_google_blueprint, google
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
import pandas as pd
import json
import os
import re
import io
import math
from xhtml2pdf import pisa
from functools import wraps
from datetime import datetime
from urllib.parse import unquote
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
    role = db.Column(db.String(20), default="user") 
    sales = db.relationship('Sale', backref='owner', lazy=True)

class Sale(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    item_name = db.Column(db.String(100), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    modal = db.Column(db.Float, nullable=False, default=0) 
    price = db.Column(db.Float, nullable=False) 
    profit = db.Column(db.Float, nullable=False) 
    date_added = db.Column(db.DateTime, default=datetime.utcnow)

class AnalysisHistory(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    filename = db.Column(db.String(200))
    best_location = db.Column(db.String(100))
    best_score = db.Column(db.Float)
    detail_json = db.Column(db.Text)
    date_created = db.Column(db.DateTime, default=db.func.current_timestamp())
    
class SystemLog(db.Model):
    __tablename__ = "system_logs"
    id = db.Column(db.Integer, primary_key=True)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    level = db.Column(db.String(20))   
    actor = db.Column(db.String(100))  
    action = db.Column(db.String(200))
    detail = db.Column(db.String(500))

    def __repr__(self):
        return f"<Log {self.level} {self.action}>"

class Criteria(db.Model):
    __tablename__ = "criteria"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    description = db.Column(db.String(500))
    weight_default = db.Column(db.Float, default=0.0)
    aliases = db.Column(db.String(500)) 

    def __repr__(self):
        return f"<Criteria {self.id} {self.name}>"

def normalize_aliases(aliases_str):
    if not aliases_str:
        return []
    parts = [p.strip().lower() for p in aliases_str.split(",") if p.strip()]
    return parts

def map_columns_using_criteria_db(df):
    cols = list(df.columns)
    mapped = {}
    criteria_list = Criteria.query.order_by(Criteria.id).all()
    for idx, c in enumerate(criteria_list):
        aliases = normalize_aliases(c.aliases)
        if c.name:
            aliases.append(c.name.lower())
        found = None
        for col in cols:
            col_l = str(col).lower()
            if any(a in col_l for a in aliases if a):
                found = col
                break
        mapped[c.name] = found
    return mapped

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

def write_log(level, actor, action, detail=""):
    try:
        entry = SystemLog(
            level=(level or "INFO").upper(),
            actor=str(actor),
            action=str(action),
            detail=str(detail)
        )
        db.session.add(entry)
        db.session.commit()
        print("[LOGGED]", level, actor, action)
    except Exception as e:
        print("[LOG ERROR]", e)

# === ROUTES UTAMA ===
@app.route("/")
def index(): 
    return render_template("user/landing.html")

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name, email, pwd = request.form.get("name"), request.form.get("email"), request.form.get("password")
        if User.query.filter_by(email=email).first():
            flash("Email sudah terdaftar.", "warning")
            return redirect(url_for("login"))
        db.session.add(User(name=name, email=email, password=generate_password_hash(pwd, method='pbkdf2:sha256'), picture=None))
        db.session.commit()
        flash("Berhasil daftar.", "success")
        return redirect(url_for("login"))
    return render_template("user/register.html")

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email")
        pwd = request.form.get("password")
        
        user = User.query.filter_by(email=email).first()

        if not user:
            flash("❌ Akun tidak ditemukan. Silakan daftar dulu.", "danger")
            return redirect(url_for("login"))

        if user.password and check_password_hash(user.password, pwd):
            session["user_id"] = user.id
            session["user_name"] = user.name
            session["user_picture"] = user.picture or f"https://ui-avatars.com/api/?name={user.name}"
            session["user_role"] = user.role

            if user.role == "admin":
                return redirect(url_for("admin_home"))
            else:
                return redirect(url_for("dashboard"))
        else:
            flash("❌ Password salah.", "danger")
            return redirect(url_for("login"))

    return render_template("user/login.html")

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
        user = User(name=info["name"], email=info["email"], picture=info["picture"], password=None, role="user")
        db.session.add(user)
        db.session.commit()
    session["user_id"] = user.id
    session["user_name"] = user.name
    session["user_picture"] = user.picture
    session["user_role"] = user.role or "user"
    return redirect(url_for("dashboard"))

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if session.get("user_role") != "admin":
            abort(403)
        return f(*args, **kwargs)
    return decorated_function

@app.route("/admin/home")
@admin_required
def admin_home():
    users = User.query.all()
    total_user_role = len([u for u in users if u.role.lower() == "user"])
    total_admin_role = len([u for u in users if u.role.lower() == "admin"])

    return render_template(
        "admin/home.html",
        users=users,
        name=session.get("user_name"),
        total_user_role=total_user_role,
        total_admin_role=total_admin_role
    )

@app.route("/admin/logs")
@admin_required
def admin_logs():
    from sqlalchemy import or_

    query = SystemLog.query.order_by(SystemLog.timestamp.desc())
    actor_filter = request.args.get('actor')
    
    if actor_filter:
        matching_users = User.query.filter(User.name.ilike(f"%{actor_filter}%")).all()
        conditions = []
        conditions.append(SystemLog.actor.ilike(f"%{actor_filter}%"))
        
        for u in matching_users:
            conditions.append(SystemLog.actor == f"user:{u.id}")
            
        query = query.filter(or_(*conditions))
    
    logs = query.limit(200).all()
    all_users = User.query.all()
    user_map = {f"user:{u.id}": u.name for u in all_users}

    return render_template("admin/logs.html", logs=logs, user_map=user_map)

@app.route('/admin/kriteria', methods=['GET', 'POST'])
def admin_kriteria():
    filename = 'anp_config.json'
    criteria_names = ["C1 (Sewa)", "C2 (Jual)", "C3 (Bahan)", "C4 (Fasilitas)", "C5 (Saing)"]

    if request.method == 'POST':
        try:
            new_matrix = [[0.0]*5 for _ in range(5)]
            
            for i in range(5):
                for j in range(5):
                    if i == j:
                        new_matrix[i][j] = 1.0
                    else:
                        val = request.form.get(f'cell_{i}_{j}')
                        new_matrix[i][j] = float(val) if val else 1.0
            
            with open(filename, 'w') as f:
                json.dump({"matrix": new_matrix}, f)
                
            flash("✅ Matriks Kriteria berhasil diperbarui! Perhitungan ANP otomatis menyesuaikan.", "success")
        except Exception as e:
            flash(f"❌ Gagal menyimpan: {str(e)}", "danger")
            
        return redirect(url_for('admin_kriteria'))

    try:
        with open(filename, 'r') as f:
            data = json.load(f)
            current_matrix = data["matrix"]
    except:
        current_matrix = [[1]*5 for _ in range(5)] 

    return render_template('admin/kriteria.html', matrix=current_matrix, names=criteria_names)

@app.route("/admin/kriteria/edit/<int:id>", methods=["POST"])
@admin_required
def admin_kriteria_edit(id):
    k = Criteria.query.get_or_404(id)
    name = request.form.get('name')
    desc = request.form.get('description')
    weight = request.form.get('weight_default')

    if not name:
        flash("Nama kriteria wajib diisi!", "danger")
        return redirect(url_for('admin_kriteria'))

    k.name = name
    k.description = desc or ""
    try:
        k.weight_default = float(weight) if weight is not None and weight != "" else k.weight_default
    except ValueError:
        pass

    db.session.commit()
    flash("Kriteria berhasil diperbarui.", "success")
    return redirect(url_for('admin_kriteria'))

@app.route("/logout")
def logout(): 
    session.clear()
    return redirect(url_for("index"))

@app.route("/dashboard")
def dashboard():
    if "user_id" not in session: return redirect(url_for("login"))
    return render_template("user/dashboard.html", name=session["user_name"], picture=session["user_picture"])

# --- SPK ROUTE ---
@app.route("/upload", methods=["GET", "POST"])
def upload_file():
    """Upload file Excel/CSV untuk analisis ANP-TOPSIS & simpan hasil komparasi lengkap"""
    if "user_id" not in session:
        return redirect(url_for("login"))

    if request.method == "POST":
        file = request.files.get("file")

        if not file or not allowed_file(file.filename):
            flash("⚠️ Harap unggah file dengan format CSV atau Excel.", "warning")
            write_log(
                "WARN",
                f"user:{session.get('user_id')}",
                "Upload Failed",
                f"invalid_file={bool(file)};filename={getattr(file,'filename',None)}"
            )
            return redirect(request.url)

        filename = secure_filename(file.filename)
        filepath = os.path.join(app.config["UPLOAD_FOLDER"], filename)
        file.save(filepath)
        session["uploaded_filepath"] = filepath

        try:
            if filename.endswith(".csv"):
                df = pd.read_csv(filepath, sep=";", engine="python")
                if len(df.columns) == 1:
                    df = pd.read_csv(filepath, sep=",", engine="python")
            else:
                df = pd.read_excel(filepath)

            video_map = {}
            col_name = df.columns[0]
            col_video = next((c for c in df.columns if "video" in c.lower() or "link" in c.lower()), None)

            if col_video:
                for _, row in df.iterrows():
                    video_map[str(row[col_name])] = convert_youtube_embed(str(row[col_video]))

            # ⚙️ Jalankan analisis multi-engine terintegrasi
            result_data = run_anp_analysis(df)
            result_data["table_html"] = df.to_html(classes="table table-bordered table-striped text-center mb-0", index=False)

            # Tambahkan tautan video ke alternatif di seluruh list perangkingan
            for list_key in ["pure_anp_ranking", "pure_topsis_ranking", "hybrid_ranking"]:
                if list_key in result_data:
                    for item in result_data[list_key]:
                        item["video_url"] = video_map.get(item.get("Alternatif"))

            # 💾 Simpan riwayat berdasarkan keputusan seimbang utama (Hybrid ANP-TOPSIS)
            try:
                best_result = result_data["hybrid_ranking"][0]

                new_history = AnalysisHistory(
                    user_id=session["user_id"],
                    filename=filename,
                    best_location=best_result["Alternatif"],
                    best_score=float(best_result["Skor"]),
                    detail_json=json.dumps(result_data)
                )

                db.session.add(new_history)
                db.session.commit()
                
                write_log(
                    "INFO",
                    f"user:{session['user_id']}",
                    "Analisis ANP-TOPSIS",
                    f"file={filename};path={filepath};history_id={new_history.id}"
                )

            except Exception as e:
                print(f"⚠️ Gagal menyimpan riwayat ke database: {e}")
                write_log(
                    "ERROR",
                    f"user:{session.get('user_id')}",
                    "ANP Save Error",
                    f"file={filename};error={e}"
                )

            # ✅ Tampilkan hasil terperinci ke layout geser di HTML (Menggunakan template subfolder user)
            return render_template(
                "user/upload.html",
                uploaded=True,
                tables=[result_data["table_html"]],
                chart=result_data.get("chart_path"),
                results_anp=result_data.get("pure_anp_ranking", []),
                results_topsis=result_data.get("pure_topsis_ranking", []),
                results_hybrid=result_data.get("hybrid_ranking", []),
                info=result_data,
                name=session["user_name"],
                picture=session.get("user_picture")
            )

        except Exception as e:
            write_log(
                "ERROR",
                f"user:{session.get('user_id')}",
                "ANP Processing Error",
                f"file={filename};error={e}"
            )
            flash(f"❌ Terjadi kesalahan saat membaca atau memproses file: {e}", "danger")
            return redirect(request.url)

    return render_template("user/upload.html", uploaded=False, name=session["user_name"], picture=session.get("user_picture"))

# === HISTORY ROUTE ===
@app.route("/history")
def history():
    if "user_id" not in session:
        return redirect(url_for("login"))

    histories = AnalysisHistory.query.filter_by(
        user_id=session["user_id"]
    ).order_by(AnalysisHistory.date_created.desc()).all()

    return render_template(
        "user/history.html",
        name=session["user_name"],
        picture=session["user_picture"],
        histories=histories
    )

@app.route("/history/clear")
def clear_history():
    if "user_id" not in session:
        return redirect(url_for("login"))

    user_id = session["user_id"]
    AnalysisHistory.query.filter_by(user_id=user_id).delete()
    db.session.commit()

    flash("Semua riwayat analisis telah dihapus.", "success")
    return redirect(url_for("history"))

@app.route("/history/<int:id>")
def view_history_detail(id):
    if "user_id" not in session:
        return redirect(url_for("login"))

    history_item = AnalysisHistory.query.filter_by(id=id, user_id=session["user_id"]).first_or_404()
    result_data = {}
    tables = []
    chart = None
    results_anp, results_topsis, results_hybrid = [], [], []

    try:
        if history_item.detail_json:
            result_data = json.loads(history_item.detail_json)
            results_anp = result_data.get("pure_anp_ranking", [])
            results_topsis = result_data.get("pure_topsis_ranking", [])
            results_hybrid = result_data.get("hybrid_ranking", [])
            chart = result_data.get("chart_path")
            
            if "table_html" in result_data:
                tables = [result_data["table_html"]]

        if "weights_anp_global" not in result_data:
            result_data["weights_anp_global"] = {}
            
    except Exception as e:
        print(f"⚠️ Gagal memuat detail JSON: {e}")

    return render_template(
        "user/upload.html",
        uploaded=True,
        tables=tables,
        chart=chart,
        results_anp=results_anp,
        results_topsis=results_topsis,
        results_hybrid=results_hybrid,
        info=result_data,
        name=session["user_name"],
        picture=session.get("user_picture")
    )

# --- FINANCE ROUTE ===
@app.route("/finance", methods=["GET", "POST"])
def finance():
    if "user_id" not in session: return redirect(url_for("login"))

    if request.method == "POST":
        try:
            item_name = request.form.get("item_name")
            quantity = int(request.form.get("quantity"))
            modal_satuan = float(request.form.get("modal")) 
            harga_jual_satuan = float(request.form.get("price"))
            
            untung_per_unit = harga_jual_satuan - modal_satuan
            total_profit_transaksi = untung_per_unit * quantity

            new_sale = Sale(
                user_id=session["user_id"], 
                item_name=item_name, 
                quantity=quantity, 
                modal=modal_satuan,          
                price=harga_jual_satuan,       
                profit=total_profit_transaksi  
            )
            db.session.add(new_sale)
            db.session.commit()
            
            flash(f"✅ Penjualan tersimpan! Keuntungan terhitung: Rp {total_profit_transaksi:,.0f}", "success")
        except Exception as e:
            flash(f"❌ Gagal menyimpan: {e}", "danger")
        return redirect(url_for("finance"))

    sales_data = Sale.query.filter_by(user_id=session["user_id"]).order_by(Sale.id.desc()).all()
    total_profit = sum(s.profit for s in sales_data)
    
    return render_template(
        "user/finance.html", 
        name=session["user_name"], 
        sales=sales_data, 
        total_profit=total_profit,
        active_tab="keuangan" 
    )

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

        # Ambil path absolut file gambar untuk disematkan di PDF
        criteria_chart_path = os.path.join(BASE_DIR, "static", "charts", "anp_network_criteria.png")
        alternatives_chart_path = os.path.join(BASE_DIR, "static", "charts", "anp_network_alternatives.png")
        hierarchy_chart_path = os.path.join(BASE_DIR, "static", "charts", "anp_network_hierarchy.png")

        html = render_template(
            "pdf_spk.html",
            name=session["user_name"],
            results=result_data["hybrid_ranking"],
            info=result_data,
            table_data=df.to_html(classes="table table-bordered", index=False),
            criteria_chart=criteria_chart_path if os.path.exists(criteria_chart_path) else None,
            alternatives_chart=alternatives_chart_path if os.path.exists(alternatives_chart_path) else None,
            hierarchy_chart=hierarchy_chart_path if os.path.exists(hierarchy_chart_path) else None
        )

        css_path = os.path.join(app.static_folder, "css", "pages", "pdf_spk.css")
        with open(css_path, "r", encoding="utf-8") as css_file:
            css_content = css_file.read()

        full_html = f"<style>{css_content}</style>{html}"

        pdf = create_pdf(full_html)
        response = make_response(pdf)
        response.headers['Content-Type'] = 'application/pdf'
        response.headers['Content-Disposition'] = 'attachment; filename=Laporan_SPK_Analisis.pdf'
        return response

    except Exception as e:
        flash(f"Gagal membuat PDF: {e}", "danger")
        return redirect(url_for("upload_file"))

@app.route("/download/diagrams-zip")
def download_diagrams_zip():
    if "user_id" not in session:
        return redirect(url_for("login"))

    import zipfile
    
    base_charts_dir = os.path.join(BASE_DIR, "static", "charts")
    files_to_zip = {
        "anp_network_criteria.png": "1_Hubungan_Kriteria_Ke_Kriteria.png",
        "anp_network_alternatives.png": "2_Hubungan_Alternatif_Ke_Alternatif.png",
        "anp_network_hierarchy.png": "3_Hubungan_Alternatif_Ke_Kriteria.png"
    }

    zip_buffer = io.BytesIO()
    has_files = False
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
        for filename, zip_name in files_to_zip.items():
            filepath = os.path.join(base_charts_dir, filename)
            if os.path.exists(filepath):
                zip_file.write(filepath, zip_name)
                has_files = True

    if not has_files:
        flash("Belum ada file diagram yang dibuat. Silakan upload data terlebih dahulu.", "warning")
        return redirect(url_for("upload_file"))

    zip_buffer.seek(0)
    response = make_response(zip_buffer.getvalue())
    response.headers['Content-Type'] = 'application/zip'
    response.headers['Content-Disposition'] = 'attachment; filename=Diagram_Hubungan_ANP.zip'
    return response

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

@app.route("/finance/simulation", methods=["POST"])
def finance_simulation():
    if "user_id" not in session: return redirect(url_for("login"))

    try:
        biaya_sewa = float(request.form.get("biaya_sewa", 0))
    except:
        biaya_sewa = 0

    hasil_list = []
    detail_porsi = []
    form_data = {"biaya_sewa": biaya_sewa, "menu": []}

    for i in range(1, 6):
        nama = request.form.get(f"nama_{i}")
        if nama:
            try:
                harga = float(request.form.get(f"harga_{i}", 0))
                modal = float(request.form.get(f"modal_{i}", 0))
                form_data["menu"].append({"nama": nama, "harga": harga, "modal": modal})

                untung = harga - modal
                if untung <= 0:
                    rekomendasi = "Rugi/Nihil"
                else:
                    porsi = math.ceil(biaya_sewa / untung)
                    rekomendasi = porsi
                    if porsi > 0:
                        detail_porsi.append(f"{porsi:,} porsi {nama}")

                hasil_list.append({
                    "nama": nama,
                    "harga": harga,
                    "modal": modal,
                    "untung": untung,         
                    "rekomendasi": rekomendasi 
                })
            except ValueError:
                continue

    if detail_porsi and biaya_sewa > 0:
        summary = f"Untuk menutup biaya operasional Rp {biaya_sewa:,.0f}, Anda harus menjual (salah satu opsi): " + " ATAU ".join(detail_porsi) + "."
    elif biaya_sewa == 0:
        summary = "Biaya operasional 0, Anda sudah untung sejak penjualan pertama."
    else:
        summary = "Silakan input data biaya dan menu yang valid."

    sales_data = Sale.query.filter_by(user_id=session["user_id"]).all()
    total_profit = sum(s.profit for s in sales_data)

    return render_template(
        "user/finance.html",
        name=session["user_name"],
        sales=sales_data,
        total_profit=total_profit,
        active_tab="simulasi",
        hasil_simulasi=hasil_list,
        summary=summary,
        form_data=form_data
    )
    
@app.route("/finance/clear", methods=["GET"])
def finance_clear():
    if "user_id" not in session:
        return redirect(url_for("login"))

    if "form_data" in session:
        session.pop("form_data")

    return render_template(
        "user/finance.html",
        name=session["user_name"],
        sales=Sale.query.filter_by(user_id=session["user_id"]).all(),
        total_profit=sum(s.profit for s in Sale.query.filter_by(user_id=session["user_id"]).all()),
        active_tab="simulasi",
        hasil_simulasi=None,
        summary=None,
        form_data=None
    )

@app.route("/admin/download")
@admin_required
def admin_download_file():
    print("\n=== DEBUG admin_download_file SESSION ===")
    try:
        for k in ["user_id","user_name","user_role"]:
            print(f" session[{k}] =", session.get(k))
    except Exception as e:
        print(" session debug error:", e)

    raw_path = request.args.get("path")
    raw_file = request.args.get("file")

    if not raw_path and raw_file:
        raw_path = os.path.join(app.config["UPLOAD_FOLDER"], os.path.basename(raw_file))

    if not raw_path:
        flash("Path file tidak ditemukan.", "danger")
        return redirect(url_for("admin_logs"))

    path = unquote(raw_path)
    abs_path = os.path.abspath(os.path.normpath(path))
    upload_dir = os.path.abspath(os.path.normpath(app.config["UPLOAD_FOLDER"]))

    print(" raw_path:", raw_path)
    print(" decoded path:", path)
    print(" abs_path:", abs_path)
    print(" upload_dir:", upload_dir)

    try:
        common = os.path.commonpath([abs_path, upload_dir])
    except Exception:
        common = None

    if not common or os.path.normcase(common) != os.path.normcase(upload_dir):
        print("DEBUG: access denied - path outside upload_dir")
        flash("Akses file ditolak.", "danger")
        return redirect(url_for("admin_logs"))

    if not os.path.exists(abs_path):
        print("DEBUG: file not exists:", abs_path)
        flash("File tidak ditemukan di server.", "danger")
        return redirect(url_for("admin_logs"))

    try:
        return send_file(abs_path, as_attachment=True)
    except Exception as e:
        print("ERROR sending file:", e)
        flash(f"Gagal mengirim file: {e}", "danger")
        return redirect(url_for("admin_logs"))

if __name__ == "__main__":
    with app.app_context():
        db.create_all()
    app.run(
        debug=True, 
        host='0.0.0.0', 
        port=5000, 
        ssl_context=('spk-anp.com+2.pem', 'spk-anp.com+2-key.pem')
    )