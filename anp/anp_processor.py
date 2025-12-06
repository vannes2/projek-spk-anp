import os
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import re

# ======================================================
#  KONFIGURASI & FOLDER
# ======================================================
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
CHART_DIR = os.path.join(BASE_DIR, "static", "charts")
os.makedirs(CHART_DIR, exist_ok=True)

# ======================================================
# 1. HELPER: KONVERSI & PARSING
# ======================================================
def extract_number_and_convert(value_str):
    v = str(value_str).lower().replace(",", "").replace(".", "")
    numbers = [float(s) for s in re.findall(r'\d+', v)]
    if not numbers: return 0
    base_val = sum(numbers) / len(numbers)
    
    multiplier = 1
    if "juta" in v: multiplier = 1000000
    elif "ribu" in v or "rb" in v: multiplier = 1000
    elif "ekor" in v: multiplier = 4        
    elif "biji" in v or "tusuk" in v: multiplier = 0.2  
    
    return base_val * multiplier

def translate_value(column_name, value):
    v_orig = str(value).strip()
    v = v_orig.lower()
    col = column_name.lower()
    
    num_val = extract_number_and_convert(v_orig) 
    score = 1 

    if "c1" in col or "sewa" in col:
        if num_val == 0: score = 1
        elif num_val <= 900000: score = 5
        elif num_val <= 1500000: score = 4
        elif num_val <= 2500000: score = 3
        elif num_val <= 4500000: score = 2
        else: score = 1

    elif "c2" in col or "jual" in col:
        if num_val >= 100: score = 5
        elif num_val >= 60: score = 4
        elif num_val >= 30: score = 3
        elif num_val >= 15: score = 2
        else: score = 1

    elif "c3" in col or "bahan" in col:
        if "sangat" in v: score = 5
        elif "cukup" in v: score = 4
        elif "agak sulit" in v: score = 2
        elif "sulit" in v: score = 1
        else: score = 5

    elif "c4" in col or "fasil" in col:
        if "tidak ada" in v or v == "-" or v == "": item_count = 0
        else: item_count = v.count(",") + 1
        
        if "wifi" in v: score = 5
        elif item_count >= 4: score = 5
        elif item_count == 3: score = 4
        elif item_count == 2: score = 3
        elif item_count == 1: score = 2
        else: score = 1

    elif "c5" in col or "saing" in col:
        if "belum ada" in v: score = 5
        elif "tidak" in v: score = 4
        elif "cukup" in v: score = 3
        elif "sangat" in v: score = 2
        elif "ketat" in v: score = 1
        else: score = 2

    try:
        if 1 <= float(value) <= 5:
            if float(value) <= 5: score = float(value)
    except:
        pass

    return score

# ======================================================
# 2. ANP ENGINE (CORE LOGIC: MATRIKS & EIGENVECTOR)
# ======================================================

def get_saaty_scale(diff_score):
    mapping = {0: 1, 1: 3, 2: 5, 3: 7, 4: 9}
    return mapping.get(int(abs(diff_score)), 9)

def get_ri_value(n):
    ri_dict = {
        1: 0.0, 2: 0.0, 3: 0.58, 4: 0.90, 5: 1.12,
        6: 1.24, 7: 1.32, 8: 1.41, 9: 1.45, 10: 1.49,
        11: 1.51, 12: 1.48, 13: 1.56, 14: 1.57, 15: 1.59
    }
    return ri_dict.get(n, 1.59)

def calculate_priority_vector(matrix):
    """
    Menghitung Bobot (Eigenvector), CI, dan CR dari matriks.
    """
    n = matrix.shape[0]
    col_sum = np.sum(matrix, axis=0)
    
    # Normalisasi
    norm_matrix = matrix / col_sum
    weights = np.mean(norm_matrix, axis=1)
    
    # Konsistensi
    lam_max = np.dot(col_sum, weights)
    
    if n > 1:
        CI = (lam_max - n) / (n - 1)
        RI = get_ri_value(n)
        CR = CI / RI if RI != 0 else 0
    else:
        CI = 0
        CR = 0
        
    return weights, CI, CR

def analyze_alternatives_pairwise(scores):
    """
    Membuat matriks perbandingan alternatif secara dinamis berdasarkan data.
    """
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
# [cite_start]3. SUPERMATRIX CALCULATION (FULL DYNAMIC) [cite: 142, 143, 154]
# ======================================================

def get_criteria_limit_matrix_weights():
    """
    Menghitung Bobot Akhir Kriteria menggunakan LIMIT SUPERMATRIX.
    Data diambil dari dokumen Bab 3 (Inner Dependence).
    """
    
    main_matrix = np.array([
        [1,   1/7, 3,   1/5, 1/3], # C1
        [7,   1,   9,   3,   5  ], # C2
        [1/3, 1/9, 1,   1/7, 1/5], # C3
        [5,   1/3, 7,   1,   3  ], # C4
        [3,   1/5, 5,   1/3, 1  ]  # C5
    ])
    w_main, ci_main, cr_main = calculate_priority_vector(main_matrix)

    dep_c2_matrix = np.array([
        [1,   1/5, 1/3], # C3
        [5,   1,   3  ], # C4
        [3,   1/3, 1  ]  # C5
    ])
    w_dep_c2, _, _ = calculate_priority_vector(dep_c2_matrix)
    
    
    supermatrix = np.zeros((5, 5))

    for i in range(5):
        supermatrix[i, 0] = w_main[i] # Col C1
        supermatrix[i, 2] = w_main[i] # Col C3
        supermatrix[i, 3] = w_main[i] # Col C4
        supermatrix[i, 4] = w_main[i] # Col C5
        
    supermatrix[0, 1] = 0.0 # C1 tidak mempengaruhi C2 di inner dep ini
    supermatrix[1, 1] = 0.0 # C2 (self)
    supermatrix[2, 1] = w_dep_c2[0] # C3
    supermatrix[3, 1] = w_dep_c2[1] # C4
    supermatrix[4, 1] = w_dep_c2[2] # C5
    
   
    
    limit_matrix = np.linalg.matrix_power(supermatrix, 100) # Pangkat 100 cukup untuk stabil
    
    # Ambil bobot akhir dari salah satu kolom (biasanya kolomnya jadi sama semua)
    final_weights_array = limit_matrix[:, 0]
    
    # Normalisasi akhir (untuk memastikan jumlah = 1)
    final_weights_array = final_weights_array / np.sum(final_weights_array)
    
    # Mapping ke Dictionary
    final_anp_weights = {
        "C1": final_weights_array[0],
        "C2": final_weights_array[1],
        "C3": final_weights_array[2],
        "C4": final_weights_array[3],
        "C5": final_weights_array[4]
    }
    
    # Kembalikan laporan konsistensi (agar Frontend tetap tampil keren)
    consistency_report = {
        "CR_Criteria_Matrix": cr_main,
        "CI_Criteria_Matrix": ci_main,
        "Status": "Valid" if cr_main < 0.1 else "Konsistensi Rendah"
    }
    
    return final_anp_weights, consistency_report

# ======================================================
# 4. FUNGSI UTAMA (MAIN PROCESS)
# ======================================================
def run_anp_analysis(df):
    print(">>> Memulai ANP Processor (Full Dynamic Limit Matrix)...")
    
    df = df.dropna(how="all")
    
    # 1. Deteksi Kolom
    criteria_map = {
        "C1": [c for c in df.columns if "C1" in c.upper() or "SEWA" in c.upper()],
        "C2": [c for c in df.columns if "C2" in c.upper() or "JUAL" in c.upper()],
        "C3": [c for c in df.columns if "C3" in c.upper() or "BAHAN" in c.upper()],
        "C4": [c for c in df.columns if "C4" in c.upper() or "FASIL" in c.upper()],
        "C5": [c for c in df.columns if "C5" in c.upper() or "SAING" in c.upper()]
    }
    
    criteria_cols = []
    for key, found in criteria_map.items():
        if found: criteria_cols.append(found[0])
    
    if len(criteria_cols) < 5:
        return {"error": "Kolom C1-C5 tidak lengkap. Cek header Excel."}

    # 2. Konversi Data
    df_num = df.copy()
    alt_col = df.columns[0]
    
    for col in criteria_cols:
        df_num[col] = df_num[col].apply(lambda x: float(translate_value(col, x)))

    # 3. Hitung Local Priorities (Alternatif)
    local_priorities = {}
    detailed_consistency = {} 
    mapping_key = {"C1":0, "C2":1, "C3":2, "C4":3, "C5":4}
    
    for i, c in enumerate(criteria_cols):
        key = list(mapping_key.keys())[i]
        weights, ci, cr = analyze_alternatives_pairwise(df_num[c].values)
        local_priorities[key] = weights
        detailed_consistency[f"CR_{key}"] = cr

    # 4. Ambil Bobot Kriteria (HITUNGAN MATRIKS DINAMIS)

    global_weights, criteria_report = get_criteria_limit_matrix_weights()
    
   
    # 5. Sintesis Akhir
    alts = df[alt_col].tolist()
    final_scores = np.zeros(len(alts))
    
    for code, weight_val in global_weights.items():
        if code in local_priorities:
            local_w = np.array(local_priorities[code])
            final_scores += local_w * weight_val

    # 6. Hasil & Visualisasi
    result_df = pd.DataFrame({
        "Alternatif": alts,
        "Skor_Global": np.round(final_scores, 4)
    }).sort_values(by="Skor_Global", ascending=False)

    plt.figure(figsize=(10, 6))
    bars = plt.barh(result_df["Alternatif"], result_df["Skor_Global"], color="#2E86C1")
    plt.xlabel("Skor Prioritas (ANP)")
    plt.title("Hasil Perangkingan Lokasi UMKM")
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
        "summary": f"Rekomendasi Utama: {top3.iloc[0,0]} dengan skor {top3.iloc[0,1]}."
    }