from flask import Flask, redirect, url_for, session, render_template, request, flash, make_response
from flask_sqlalchemy import SQLAlchemy
from flask_dance.contrib.google import make_google_blueprint, google
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
import pandas as pd
import json
import os
import re
import io
from xhtml2pdf import pisa
from functools import wraps
from flask import abort
from datetime import datetime
from urllib.parse import unquote
from flask import send_file
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
    price = db.Column(db.Float, nullable=False)
    profit = db.Column(db.Float, nullable=False)

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
    aliases = db.Column(db.String(500))   # comma-separated aliases, e.g. "sewa,harga sewa,biaya_sewa"

    def __repr__(self):
        return f"<Criteria {self.id} {self.name}>"


def normalize_aliases(aliases_str):
    """Return list of cleaned alias tokens (lowercase, stripped)."""
    if not aliases_str:
        return []
    parts = [p.strip().lower() for p in aliases_str.split(",") if p.strip()]
    # also keep multi-word tokens
    return parts

def map_columns_using_criteria_db(df):
    """
    Coba mapping otomatis: untuk setiap Criteria (urut), pakai aliases+name untuk mencari kolom.
    Return dict mapping criteria_name -> matched_column_or_None
    """
    cols = list(df.columns)
    mapped = {}
    # ambil criteria sorted by id (as they likely correspond to C1..C5)
    criteria_list = Criteria.query.order_by(Criteria.id).all()
    for idx, c in enumerate(criteria_list):
        aliases = normalize_aliases(c.aliases)
        # selalu sertakan nama criteria sendiri (lowercase)
        if c.name:
            aliases.append(c.name.lower())
        found = None
        for col in cols:
            col_l = str(col).lower()
            # match jika salah satu alias ada di nama kolom (contain)
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

# === ROUTES UTAMA ===
@app.route("/")
def index(): return render_template("user/landing.html")

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name, email, pwd = request.form.get("name"), request.form.get("email"), request.form.get("password")
        if User.query.filter_by(email=email).first():
            flash("Email sudah terdaftar.", "warning"); return redirect(url_for("login"))
        db.session.add(User(name=name, email=email, password=generate_password_hash(pwd, method='pbkdf2:sha256'), picture=None))
        db.session.commit()
        flash("Berhasil daftar.", "success"); return redirect(url_for("login"))
    return render_template("user/register.html")

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email, pwd = request.form.get("email"), request.form.get("password")
        user = User.query.filter_by(email=email).first()

        if not user:
            flash("❌ Akun tidak ditemukan.", "danger")
            return redirect(request.url)

        # ✅ Hanya admin boleh login manual
        if user.role != "admin":
            flash("⚠️ Login manual hanya untuk admin. Silakan gunakan login Google.", "warning")
            return redirect(url_for("login"))

        if user.password and check_password_hash(user.password, pwd):
            # Simpan sesi admin
            session["user_id"] = user.id
            session["user_name"] = user.name
            session["user_picture"] = user.picture or f"https://ui-avatars.com/api/?name={user.name}"
            session["user_role"] = user.role

            return redirect(url_for("admin_home"))

        flash("❌ Password salah.", "danger")

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
        db.session.add(user); db.session.commit()
    session["user_id"] = user.id
    session["user_name"] = user.name
    session["user_picture"] = user.picture
    session["user_role"] = user.role or "user"
    return redirect(url_for("dashboard"))



def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if session.get("user_role") != "admin":
            abort(403)  # Forbidden
        return f(*args, **kwargs)
    return decorated_function


@app.route("/admin/home")
@admin_required
def admin_home():
    """Halaman utama admin menampilkan daftar user"""
    users = User.query.all()
    return render_template("admin/home.html", users=users, name=session.get("user_name"))

@app.route("/admin/logs")
@admin_required
def admin_logs():
    logs = SystemLog.query.order_by(SystemLog.timestamp.desc()).limit(200).all()
    return render_template("admin/logs.html", logs=logs)

# === ADMIN: Kriteria (update + delete handling) ===
@app.route("/admin/kriteria", methods=["GET", "POST"])
@admin_required
def admin_kriteria():
    # HANDLE TAMBAH (POST)
    if request.method == "POST":
        name = request.form.get("name")
        desc = request.form.get("description")
        weight = request.form.get("weight_default") or 0
        try:
            weight = float(weight)
        except ValueError:
            weight = 0.0

        if not name:
            flash("Nama kriteria wajib diisi!", "danger")
            return redirect(url_for("admin_kriteria"))

        new_kriteria = Criteria(name=name, description=desc, weight_default=weight)
        db.session.add(new_kriteria)
        db.session.commit()

        flash("Kriteria berhasil ditambahkan!", "success")
        return redirect(url_for("admin_kriteria"))

    # HANDLE DELETE via GET param ?delete=<id>
    del_id = request.args.get("delete")
    if del_id:
        try:
            c = Criteria.query.get(int(del_id))
            if c:
                db.session.delete(c)
                db.session.commit()
                flash("Kriteria berhasil dihapus.", "success")
            else:
                flash("Kriteria tidak ditemukan.", "warning")
        except Exception as e:
            flash(f"Gagal menghapus kriteria: {e}", "danger")
        return redirect(url_for("admin_kriteria"))

    # DEFAULT: tampilkan halaman kriteria
    criteria = Criteria.query.order_by(Criteria.id).all()
    return render_template("admin/kriteria.html", criteria=criteria)


# === ADMIN: Edit Kriteria (POST dari modal) ===
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
        # abaikan jika bukan angka
        pass

    db.session.commit()
    flash("Kriteria berhasil diperbarui.", "success")
    return redirect(url_for('admin_kriteria'))



@app.route("/logout")
def logout(): session.clear(); return redirect(url_for("index"))

@app.route("/dashboard")
def dashboard():
    if "user_id" not in session: return redirect(url_for("login"))
    return render_template("user/dashboard.html", name=session["user_name"], picture=session["user_picture"])

# --- SPK ROUTE ---
@app.route("/upload", methods=["GET", "POST"])
def upload_file():
    """Upload file Excel/CSV untuk analisis ANP & simpan hasil lengkap ke riwayat"""
    if "user_id" not in session:
        return redirect(url_for("login"))

    if request.method == "POST":
        file = request.files.get("file")

        # 🔒 Validasi file
        if not file or not allowed_file(file.filename):
            flash("⚠️ Harap unggah file dengan format CSV atau Excel.", "warning")

            # Log attempt invalid upload
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
            # 📘 Baca file (CSV/Excel)
            if filename.endswith(".csv"):
                df = pd.read_csv(filepath, sep=";", engine="python")
                if len(df.columns) == 1:
                    df = pd.read_csv(filepath, sep=",", engine="python")
            else:
                df = pd.read_excel(filepath)

            # 🎬 Deteksi kolom video/link (opsional)
            video_map = {}
            col_name = df.columns[0]
            col_video = next((c for c in df.columns if "video" in c.lower() or "link" in c.lower()), None)

            if col_video:
                for _, row in df.iterrows():
                    video_map[str(row[col_name])] = convert_youtube_embed(str(row[col_video]))

            # ⚙️ Jalankan analisis ANP
            result_data = run_anp_analysis(df)

            # 💡 Tambahkan hasil tabel ke JSON agar bisa ditampilkan ulang
            result_data["table_html"] = df.to_html(classes="table table-bordered", index=False)

            # 💡 Tambahkan video ke setiap alternatif
            for item in result_data.get("ranking", []):
                item["video_url"] = video_map.get(item.get("Alternatif"))

            # 💾 Simpan hasil terbaik & detail analisis
            try:
                best_result = max(result_data["ranking"], key=lambda x: x["Skor_Global"])

                new_history = AnalysisHistory(
                    user_id=session["user_id"],
                    filename=filename,
                    best_location=best_result["Alternatif"],
                    best_score=best_result["Skor_Global"],
                    detail_json=json.dumps(result_data)
                )

                db.session.add(new_history)
                db.session.commit()
                print(f"✅ Riwayat berhasil disimpan untuk file: {filename}")

                # — LOG: berhasil menjalankan analisis ANP
                write_log(
                    "INFO",
                    f"user:{session['user_id']}",
                    "Analisis ANP",
                    f"file={filename};path={filepath};history_id={new_history.id}"  # ⬅️ DITAMBAHKAN
                )

            except Exception as e:
                print(f"⚠️ Gagal menyimpan riwayat ke database: {e}")

                write_log(
                    "ERROR",
                    f"user:{session.get('user_id')}",
                    "ANP Save Error",
                    f"file={filename};error={e}"
                )

            # ✅ Tampilkan hasil analisis
            return render_template(
                "user/upload.html",
                uploaded=True,
                tables=[result_data["table_html"]],
                chart=result_data.get("chart"),
                results=result_data.get("ranking", []),
                info=result_data,
                name=session["user_name"]
            )

        except Exception as e:
            # LOG: error saat memproses file / analisis
            write_log(
                "ERROR",
                f"user:{session.get('user_id')}",
                "ANP Processing Error",
                f"file={filename};error={e}"
            )
            flash(f"❌ Terjadi kesalahan saat membaca atau memproses file: {e}", "danger")
            return redirect(request.url)

    # 🔹 GET request — tampilkan halaman kosong
    return render_template("user/upload.html", uploaded=False, name=session["user_name"])


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

# === CLEAR HISTORY ROUTE ===
@app.route("/history/clear")
def clear_history():
    """Menghapus seluruh riwayat analisis milik user saat ini"""
    if "user_id" not in session:
        return redirect(url_for("login"))

    # Hapus semua data history milik user yang login
    user_id = session["user_id"]
    AnalysisHistory.query.filter_by(user_id=user_id).delete()
    db.session.commit()

    flash("Semua riwayat analisis telah dihapus.", "success")
    return redirect(url_for("history"))

@app.route("/history/<int:id>")
def view_history_detail(id):
    if "user_id" not in session:
        return redirect(url_for("login"))

    history = AnalysisHistory.query.filter_by(id=id, user_id=session["user_id"]).first_or_404()

    import json
    result_data = {}
    tables = []
    chart = None
    results = []

    try:
        if history.detail_json:
            result_data = json.loads(history.detail_json)
            results = result_data.get("ranking", [])
            chart = result_data.get("chart")
            if "table_html" in result_data:
                tables = [result_data["table_html"]]

        # 💡 Tambahkan fallback aman
        if "weights" not in result_data:
            result_data["weights"] = {}
    except Exception as e:
        print(f"⚠️ Gagal memuat detail JSON: {e}")

    return render_template(
        "user/upload.html",
        uploaded=True,
        tables=tables,
        chart=chart,
        results=results,
        info=result_data,
        name=session["user_name"]
    )

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
    return render_template("user/finance.html", name=session["user_name"], sales=sales_data, total_profit=total_profit)

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

@app.route("/finance/simulation", methods=["POST"])
def finance_simulation():
    if "user_id" not in session:
        return redirect(url_for("login"))

    biaya_sewa = float(request.form.get("biaya_sewa", 0))
    hasil_list = []
    detail_porsi = []
    total_profit = 0
    total_items = 0


    form_data = {"biaya_sewa": biaya_sewa, "menu": []}

    for i in range(1, 6):
        nama = request.form.get(f"nama_{i}")
        harga = request.form.get(f"harga_{i}")
        modal = request.form.get(f"modal_{i}")

        if nama or harga or modal:
            form_data["menu"].append({
                "nama": nama or "",
                "harga": harga or "",
                "modal": modal or "",
            })

        if nama and harga and modal:
            harga = float(harga)
            modal = float(modal)
            untung = harga - modal
            if untung <= 0:
                rekomendasi = f"Harga terlalu rendah! Naikkan minimal ke Rp {modal + 1000:,.0f}"
                porsi_perlu = 0
            else:
                porsi_perlu = biaya_sewa / untung
                rekomendasi = f"Perlu jual ±{porsi_perlu:.0f} porsi untuk tutup sewa."

            hasil_list.append({
                "nama": nama,
                "harga": harga,
                "modal": modal,
                "untung": untung,
                "rekomendasi": rekomendasi,
                "porsi_perlu": porsi_perlu
            })
            if porsi_perlu > 0:
                detail_porsi.append(f"{int(porsi_perlu):,} porsi {nama}")
            total_profit += untung
            total_items += 1

 
    if detail_porsi:
        summary = f"Untuk menutup biaya sewa Rp {biaya_sewa:,.0f} harus menjual " + \
                  " dan ".join(detail_porsi) + "."
    else:
        summary = "Tidak ada data makanan valid untuk simulasi."

    return render_template(
        "user/finance.html",
        name=session["user_name"],
        sales=Sale.query.filter_by(user_id=session["user_id"]).all(),
        total_profit=sum(s.profit for s in Sale.query.filter_by(user_id=session["user_id"]).all()),
        active_tab="simulasi",
        hasil_simulasi=hasil_list,
        summary=summary,
        form_data=form_data,  
    )
    
@app.route("/finance/clear", methods=["GET"])
def finance_clear():
    """Reset form simulasi balik modal"""
    if "user_id" not in session:
        return redirect(url_for("login"))

    # Hapus data form dari session jika ada
    if "form_data" in session:
        session.pop("form_data")

    # Render ulang halaman simulasi dalam keadaan kosong
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

@app.route("/admin/download")
@admin_required
def admin_download_file():
    # DEBUG: tunjukkan session saat route dipanggil
    print("\n=== DEBUG admin_download_file SESSION ===")
    try:
        for k in ["user_id","user_name","user_role"]:
            print(f" session[{k}] =", session.get(k))
    except Exception as e:
        print(" session debug error:", e)

    raw_path = request.args.get("path")
    raw_file = request.args.get("file")

    # jika hanya file param diberikan, bangun path dari UPLOAD_FOLDER
    if not raw_path and raw_file:
        raw_path = os.path.join(app.config["UPLOAD_FOLDER"], os.path.basename(raw_file))

    if not raw_path:
        flash("Path file tidak ditemukan.", "danger")
        return redirect(url_for("admin_logs"))

    path = unquote(raw_path)
    abs_path = os.path.abspath(os.path.normpath(path))
    upload_dir = os.path.abspath(os.path.normpath(app.config["UPLOAD_FOLDER"]))

    # DEBUG info path
    print(" raw_path:", raw_path)
    print(" decoded path:", path)
    print(" abs_path:", abs_path)
    print(" upload_dir:", upload_dir)

    # keamanan: pastikan berada dalam upload_dir
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
    with app.app_context(): db.create_all()
    app.run(debug=True)