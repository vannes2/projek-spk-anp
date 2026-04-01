import os
import numpy as np
import pandas as pd
import json
import re
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

# ======================================================
#  KONFIGURASI & FOLDER
# ======================================================
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
CHART_DIR = os.path.join(BASE_DIR, "static", "charts")
os.makedirs(CHART_DIR, exist_ok=True)

# ======================================================
# 1. HELPER: KONVERSI & PARSING (SMART REGEX)
# ======================================================
def extract_number_and_convert(value_str):
    """Mengubah string '1.5 Juta', '10 ribu', 'Rp 50.000' menjadi float murni."""
    v = str(value_str).lower().replace(",", "").replace(".", "")
    
    # Ambil angka
    numbers = [float(s) for s in re.findall(r'\d+', v)]
    if not numbers: return 0
    base_val = sum(numbers) / len(numbers)
    
    # FAKTOR PENGALI
    multiplier = 1
    if "juta" in v: multiplier = 1000000
    elif "ribu" in v or "rb" in v: multiplier = 1000
    elif "ekor" in v: multiplier = 4        
    elif "biji" in v or "tusuk" in v: multiplier = 0.2  
    
    return base_val * multiplier

def normalize_numeric_column(series, is_cost=False):
    """
    Dynamic Min-Max Scaling.
    Mengubah angka berapapun (ribuan s/d miliaran) menjadi skor 1-5.
    """
    # 1. Konversi ke angka murni
    clean_data = series.apply(extract_number_and_convert)
    
    
    min_val = clean_data.min()
    max_val = clean_data.max()
    
    # Jika data seragam (min == max), kasih skor tengah
    if min_val == max_val:
        return pd.Series([3.0] * len(series))
    
    # 2. Rumus Normalisasi 1-5
    scores = 1 + ((clean_data - min_val) * 4 / (max_val - min_val))
    
    # 3. Jika COST (Biaya), balik nilainya (Makin mahal makin kecil skornya)
    if is_cost:
        scores = 6 - scores
        
    return scores

def translate_categorical_value(column_name, value):
    """
    Validasi KETAT untuk kolom teks (C3, C4, C5).
    Jika input tidak dikenal -> ERROR (Raise ValueError).
    """
    v_orig = str(value).strip()
    v = v_orig.lower()
    col = column_name.lower()
    score = None 

    # --- C3: BAHAN ---
    if "c3" in col or "bahan" in col:
        if "sangat" in v: score = 5
        elif "cukup" in v: score = 4
        elif "agak sulit" in v: score = 2
        elif "sulit" in v: score = 1
        else: 
            # STRICT MODE: Tidak ada else score=5
            raise ValueError(f"Isi kolom C3 '{v_orig}' tidak valid. Gunakan: Sangat Mudah, Cukup, Sulit.")

    # --- C4: FASILITAS ---
    elif "c4" in col or "fasil" in col:
        if "tidak ada" in v or v == "-" or v == "": item_count = 0
        else: item_count = v.count(",") + 1
        
        if "wifi" in v: score = 5
        elif item_count >= 4: score = 5
        elif item_count == 3: score = 4
        elif item_count == 2: score = 3
        elif item_count == 1: score = 2
        else: score = 1 # C4 aman default 1 (basis jumlah koma)

    # --- C5: PERSAINGAN ---
    elif "c5" in col or "saing" in col:
        if "belum ada" in v: score = 5
        elif "tidak" in v: score = 4
        elif "cukup" in v: score = 3
        elif "sangat" in v: score = 2
        elif "ketat" in v: score = 1
        else: 
            # STRICT MODE: Tidak ada else score=2
            raise ValueError(f"Isi kolom C5 '{v_orig}' tidak valid. Gunakan: Ketat, Biasa, Tidak Ada.")
    
    # Cek jika user input angka manual 1-5
    if score is None:
        try:
            val_float = float(value)
            if 1 <= val_float <= 5: score = val_float
            else: raise ValueError
        except:
            raise ValueError(f"Data '{v_orig}' pada kolom {column_name} tidak dikenali.")

    return score

# ======================================================
# 2. ANP ENGINE (CORE)
# ======================================================
def get_saaty_scale(diff_score):
    mapping = {0: 1, 1: 3, 2: 5, 3: 7, 4: 9}
    return mapping.get(int(round(abs(diff_score))), 9)

def get_ri_value(n):
    ri_dict = {1: 0.0, 2: 0.0, 3: 0.58, 4: 0.90, 5: 1.12, 6: 1.24, 7: 1.32, 8: 1.41}
    return ri_dict.get(n, 1.45)

def calculate_priority_vector(matrix):
    n = matrix.shape[0]
    col_sum = np.sum(matrix, axis=0)
    # Hindari pembagian nol
    col_sum[col_sum == 0] = 1 
    
    norm_matrix = matrix / col_sum
    weights = np.mean(norm_matrix, axis=1)
    
    lam_max = np.dot(col_sum, weights)
    if n > 1:
        CI = (lam_max - n) / (n - 1)
        RI = get_ri_value(n)
        CR = CI / RI if RI != 0 else 0
    else:
        CI, CR = 0, 0
    return weights, CI, CR

def analyze_alternatives_pairwise(scores):
    n = len(scores)
    matrix = np.ones((n, n), dtype=float)
    for i in range(n):
        for j in range(n):
            if i == j: continue
            diff = scores[i] - scores[j]
            s_val = get_saaty_scale(diff)
            matrix[i, j] = s_val if diff > 0 else (1 / s_val if diff < 0 else 1)
    return calculate_priority_vector(matrix)

# ======================================================
# 3. SUPERMATRIX (DENGAN CONFIG ADMIN)
# ======================================================
def get_criteria_limit_matrix_weights():
    # 1. Default Hardcoded (Backup) - SAMA dengan manual
    main_matrix = np.array([
        [1,   1/7, 3,   1/5, 1/3], 
        [7,   1,   9,   3,   5  ], 
        [1/3, 1/9, 1,   1/7, 1/5], 
        [5,   1/3, 7,   1,   3  ], 
        [3,   1/5, 5,   1/3, 1  ]  
    ])

    # 2. Cek Config Admin
    config_path = os.path.join(BASE_DIR, "anp_config.json")
    if os.path.exists(config_path):
        try:
            with open(config_path, "r") as f:
                config = json.load(f)
            
            keys = ["C1", "C2", "C3", "C4", "C5"]
            new_matrix = np.eye(5)
            
            for i in range(5):
                for j in range(5):
                    if i == j: continue
                    key_pair = f"{keys[i]}_{keys[j]}"
                    if key_pair in config:
                        val = float(config[key_pair])
                        new_matrix[i, j] = val
                    else:
                        key_rev = f"{keys[j]}_{keys[i]}"
                        if key_rev in config:
                            val = float(config[key_rev])
                            new_matrix[i, j] = 1 / val if val != 0 else 1
            main_matrix = new_matrix
            print(">>> ANP: Memakai Bobot Admin.")
        except Exception as e:
            print(f"Error load config: {e}")

    # 3. Hitung Bobot Kriteria Utama (dari manual: C1=0.068, C2=0.503, C3=0.035, C4=0.260, C5=0.134)
    w_main, ci_main, cr_main = calculate_priority_vector(main_matrix)
    
    # DEBUG: Tampilkan bobot
    print(f">>> Bobot Kriteria Utama: {w_main}")
    print(f">>> CI: {ci_main}, CR: {cr_main}")
    
    # 4. Inner Dependence untuk C2 (dari manual halaman terakhir)
    # C2 dipengaruhi oleh C3, C4, C5 dengan matriks:
    # [[1, 1/5, 1/3], [5, 1, 3], [3, 1/3, 1]]
    dep_c2_matrix = np.array([
        [1,   1/5, 1/3],  # C3 vs (C3, C4, C5)
        [5,   1,   3  ],  # C4 vs (C3, C4, C5)
        [3,   1/3, 1  ]   # C5 vs (C3, C4, C5)
    ])
    
    w_dep_c2, ci_dep, cr_dep = calculate_priority_vector(dep_c2_matrix)
    print(f">>> Inner Dependence (C3,C4,C5 -> C2): {w_dep_c2}")
    print(f">>> CI Dep: {ci_dep}, CR Dep: {cr_dep}")
    
    # 5. Bangun Supermatrix yang BENAR (5x5)
    # Urutan: C1, C2, C3, C4, C5
    supermatrix = np.zeros((5, 5))
    
    # Kolom 0: C1 (tidak ada ketergantungan)
    supermatrix[:, 0] = w_main  # Semua kriteria dipengaruhi oleh bobot utama
    
    # Kolom 1: C2 (dipengaruhi C3, C4, C5)
    supermatrix[0, 1] = 0.0  # C1 tidak pengaruhi C2
    supermatrix[1, 1] = 0.0  # C2 tidak pengaruhi diri sendiri di sini
    supermatrix[2, 1] = w_dep_c2[0]  # C3 -> C2
    supermatrix[3, 1] = w_dep_c2[1]  # C4 -> C2
    supermatrix[4, 1] = w_dep_c2[2]  # C5 -> C2
    
    # Kolom 2: C3 (tidak ada ketergantungan lain)
    supermatrix[:, 2] = w_main
    
    # Kolom 3: C4 (tidak ada ketergantungan lain)
    supermatrix[:, 3] = w_main
    
    # Kolom 4: C5 (tidak ada ketergantungan lain)
    supermatrix[:, 4] = w_main
    
    print(">>> Supermatrix awal:")
    print(supermatrix)
    
    # 6. NORMALISASI Supermatrix (setiap kolom harus berjumlah 1)
    for j in range(5):
        col_sum = np.sum(supermatrix[:, j])
        if col_sum > 0:
            supermatrix[:, j] = supermatrix[:, j] / col_sum
    
    print(">>> Supermatrix ternormalisasi:")
    print(supermatrix)
    
    # 7. Hitung Limit Matrix dengan metode iteratif (lebih stabil)
    # Limit matrix = supermatrix^∞ (konvergen ke nilai stabil)
    limit_matrix = supermatrix.copy()
    for i in range(50):  # Iterasi 50x sudah cukup untuk konvergensi
        limit_matrix = np.dot(limit_matrix, supermatrix)
    
    # Alternatif: bisa juga pakai eigenvalue
    # eigenvalues, eigenvectors = np.linalg.eig(supermatrix.T)
    # idx = np.argmax(np.abs(eigenvalues))
    # limit_weights = np.abs(eigenvectors[:, idx])
    # limit_weights = limit_weights / np.sum(limit_weights)
    
    print(">>> Limit Matrix kolom 0:")
    print(limit_matrix[:, 0])
    
    # 8. Ambil bobot akhir dari kolom pertama Limit Matrix
    final_weights_array = limit_matrix[:, 0]
    
    # Normalisasi final (pastikan total = 1)
    final_weights_array = final_weights_array / np.sum(final_weights_array)
    
    print(f">>> Bobot ANP Final: {final_weights_array}")
    print(f">>> Total: {np.sum(final_weights_array)}")
    
    # 9. Format output
    final_anp_weights = {
        "C1": float(final_weights_array[0]),
        "C2": float(final_weights_array[1]),
        "C3": float(final_weights_array[2]),
        "C4": float(final_weights_array[3]),
        "C5": float(final_weights_array[4])
    }
    
    # Untuk testing, bandingkan dengan manual
    manual_weights = {
        "C1": 0.068, "C2": 0.503, "C3": 0.035, "C4": 0.260, "C5": 0.134
    }
    
    print(">>> Perbandingan dengan manual:")
    for key in final_anp_weights:
        diff = abs(final_anp_weights[key] - manual_weights[key])
        print(f"  {key}: ANP={final_anp_weights[key]:.3f}, Manual={manual_weights[key]:.3f}, Diff={diff:.3f}")
    
    consistency_report = {
        "CR_Criteria_Matrix": float(cr_main),
        "CI_Criteria_Matrix": float(ci_main),
        "CR_Inner_Dependence": float(cr_dep),
        "Status": "Valid" if cr_main < 0.1 and cr_dep < 0.1 else "Konsistensi Rendah",
        "Source": "ANP Calculation"
    }
    
    return final_anp_weights, consistency_report

# ======================================================
# 5. ANP SIMPLE (TAMBAHKAN DI SINI 🔥)
# ======================================================

def get_anp_weights_simple():
    matrix = np.array([
        [1,   1/7, 3,   1/5, 1/3], 
        [7,   1,   9,   3,   5  ], 
        [1/3, 1/9, 1,   1/7, 1/5], 
        [5,   1/3, 7,   1,   3  ], 
        [3,   1/5, 5,   1/3, 1  ]  
    ])

    weights, ci, cr = calculate_priority_vector(matrix)

    return {
    "C1": float(weights[0]),
    "C2": float(weights[1]),
    "C3": float(weights[2]),
    "C4": float(weights[3]),
    "C5": float(weights[4])
    }, {
        "CR_Criteria_Matrix": cr,  # 🔥 SAMAKAN KEY
        "CI_Criteria_Matrix": ci,
        "note": "ANP Pairwise (tanpa limit matrix)"
    }

# ======================================================
# 6. TOPSIS ENGINE (FINAL)
# ======================================================
def calculate_topsis(df_scores, weights_dict):
    """
    df_scores : DataFrame (C1–C5 sudah dalam skala 1–5)
    weights_dict : hasil ANP {"C1":..., "C2":...}
    """

    # 1. Konversi ke numpy
    criteria = ["C1", "C2", "C3", "C4", "C5"]
    X = df_scores[criteria].values.astype(float)

    # 2. Bobot dari ANP
    W = np.array([weights_dict[c] for c in criteria])

    # 3. Normalisasi matriks keputusan
    norm = np.sqrt((X ** 2).sum(axis=0))
    norm[norm == 0] = 1  # hindari pembagian nol
    R = X / norm

    # 4. Matriks ternormalisasi berbobot
    V = R * W

    # 5. Tentukan solusi ideal (+) dan (-)
    # C1 = cost, lainnya benefit
    ideal_pos = np.zeros(len(criteria))
    ideal_neg = np.zeros(len(criteria))

    for i, c in enumerate(criteria):
        if c in ["C1", "C5"]: # COST
            ideal_pos[i] = np.min(V[:, i])  # terbaik (biaya kecil)
            ideal_neg[i] = np.max(V[:, i])  # terburuk
        else:  # BENEFIT
            ideal_pos[i] = np.max(V[:, i])
            ideal_neg[i] = np.min(V[:, i])

    # 6. Hitung jarak ke solusi ideal
    D_pos = np.sqrt(((V - ideal_pos) ** 2).sum(axis=1))
    D_neg = np.sqrt(((V - ideal_neg) ** 2).sum(axis=1))

    # 7. Hitung skor preferensi
    scores = D_neg / (D_pos + D_neg + 1e-9)  # hindari division by zero

    return scores

# ======================================================
# 7. FUNGSI UTAMA (MAIN)
# ======================================================
def run_anp_analysis(df):
    print(">>> Memulai ANP Processor (Final Version)...")
    df = df.dropna(how="all")

    # 1. Cek Kolom
    criteria_map = {
        "C1": [c for c in df.columns if "C1" in c.upper() or "SEWA" in c.upper()],
        "C2": [c for c in df.columns if "C2" in c.upper() or "JUAL" in c.upper()],
        "C3": [c for c in df.columns if "C3" in c.upper() or "BAHAN" in c.upper()],
        "C4": [c for c in df.columns if "C4" in c.upper() or "FASIL" in c.upper()],
        "C5": [c for c in df.columns if "C5" in c.upper() or "SAING" in c.upper()]
    }
    for k, v in criteria_map.items():
        if not v: return {"error": f"Kolom {k} tidak ditemukan."}

    # 2. Konversi Data (Pre-processing)
    df_scores = pd.DataFrame()
    try:
        # =========================
        # MODE 1: DATA SUDAH ANGKA (PROPOSAL)
        # =========================
        df_scores = df[[criteria_map["C1"][0],
                        criteria_map["C2"][0],
                        criteria_map["C3"][0],
                        criteria_map["C4"][0],
                        criteria_map["C5"][0]]].astype(float)

        df_scores.columns = ["C1", "C2", "C3", "C4", "C5"]

    except:
        # =========================
        # MODE 2: DATA TEKS (APLIKASI)
        # =========================
        df_scores["C1"] = normalize_numeric_column(df[criteria_map["C1"][0]], is_cost=True)
        df_scores["C2"] = normalize_numeric_column(df[criteria_map["C2"][0]], is_cost=False)

        for cat in ["C3", "C4", "C5"]:
            col_name = criteria_map[cat][0]
            df_scores[cat] = df[col_name].apply(lambda x: translate_categorical_value(col_name, x))

    # 3. Hitung Pairwise Alternatif
    local_priorities = {}
    detailed_consistency = {}
    for col in ["C1", "C2", "C3", "C4", "C5"]:
        w, ci, cr = analyze_alternatives_pairwise(df_scores[col].values)
        local_priorities[col] = w
        detailed_consistency[f"CR_{col}"] = cr

    # 4. Ambil Bobot Global
    global_weights, criteria_report = get_anp_weights_simple()

    # 5. Sintesis
    alts = df[df.columns[0]].tolist()
    final_scores = calculate_topsis(df_scores, global_weights)

    # 6. Result
    result_df = pd.DataFrame({"Alternatif": alts, "Skor_Global": np.round(final_scores, 4)})
    result_df = result_df.sort_values(by="Skor_Global", ascending=False)
    
    plt.figure(figsize=(10, 6))
    plt.barh(result_df["Alternatif"], result_df["Skor_Global"], color="#2E86C1")
    plt.gca().invert_yaxis()
    plt.tight_layout()
    plt.savefig(os.path.join(CHART_DIR, "hasil_anp.png"))
    plt.close()

    top3 = result_df.head(3).reset_index(drop=True)
    full_report = {**criteria_report, **detailed_consistency}

    return {
        "ranking": result_df.to_dict(orient="records"),
        "chart": "static/charts/hasil_anp.png",
        "weights": global_weights,
        "consistency_report": full_report,
        "consistency_ratio": criteria_report["CR_Criteria_Matrix"],
        "summary": f"Rekomendasi: {top3.iloc[0,0]} ({top3.iloc[0,1]})"
    }