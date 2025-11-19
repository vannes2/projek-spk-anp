# ============================================
# ANP Processor - Versi Lengkap & Terpadu (Final)
# ============================================

import os
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

# --------------------------------------------
# Bagian 1: Konfigurasi dasar & fungsi bantu
# --------------------------------------------

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
CHART_DIR = os.path.join(BASE_DIR, "static", "charts")
os.makedirs(CHART_DIR, exist_ok=True)


def normalize_matrix(matrix):
    """Normalisasi matriks pairwise comparison."""
    column_sum = np.sum(matrix, axis=0)
    return matrix / column_sum


def get_eigenvector(matrix):
    """Hitung eigenvector (bobot kriteria)."""
    norm = normalize_matrix(matrix)
    return np.mean(norm, axis=1)


def consistency_ratio(matrix, eigenvector):
    """Menghitung Consistency Index (CI) dan Consistency Ratio (CR)."""
    n = matrix.shape[0]
    lambda_max = np.mean(np.sum(matrix * eigenvector, axis=1) / eigenvector)
    CI = (lambda_max - n) / (n - 1)
    RI = 1.12  # untuk n=5
    CR = CI / RI
    return round(CI, 4), round(CR, 4)


# --------------------------------------------
# Bagian 2: Matriks Saaty Default
# --------------------------------------------

def build_default_matrix():
    """Membangun matriks perbandingan kriteria (C1–C5)."""
    kriteria = ["C1", "C2", "C3", "C4", "C5"]
    n = len(kriteria)
    M = np.ones((n, n))

    pairwise = {
        ("C1", "C2"): 7,
        ("C1", "C3"): 3,
        ("C1", "C4"): 5,
        ("C1", "C5"): 3,
        ("C2", "C3"): 9,
        ("C2", "C4"): 3,
        ("C2", "C5"): 5,
        ("C3", "C4"): 7,
        ("C3", "C5"): 5,
        ("C4", "C5"): 3
    }

    for (a, b), val in pairwise.items():
        i, j = kriteria.index(a), kriteria.index(b)
        M[i][j] = val
        M[j][i] = 1 / val

    return M, kriteria


# --------------------------------------------
# Bagian 3: Konversi teks ke angka
# --------------------------------------------

def translate_value(column_name, value):
    v = str(value).lower().strip()

    # C1: Sewa (Cost)
    if "sewa" in column_name.lower():
        if "7" in v or "tujuh" in v:
            return 1
        elif "4" in v or "empat" in v or "mahal" in v:
            return 2
        elif "2" in v or "dua" in v or "sedang" in v:
            return 3
        elif "1" in v or "satu" in v or "murah" in v:
            return 4
        elif "500" in v or "lima ratus" in v or "sangat murah" in v:
            return 5

    # C2: Penjualan (Benefit)
    if "porsi" in v or "ekor" in v:
        if "5" in v and "10" in v:
            return 1
        elif "15" in v or "30" in v:
            return 2
        elif "50" in v:
            return 3
        elif "100" in v:
            return 4
        else:
            return 5

    # C3: Bahan Baku (Benefit)
    if "sulit" in v:
        if "agak" in v:
            return 2
        else:
            return 1
    elif "mudah" in v:
        if "cukup" in v:
            return 4
        elif "sangat" in v:
            return 5
        else:
            return 3

    # C4: Fasilitas (Benefit)
    if any(x in v for x in ["barcode", "kulkas", "kursi", "tempat duduk"]):
        items = v.split(",")
        return min(5, len(items))

    # C5: Persaingan (Cost)
    if "ketat" in v:
        return 1
    elif "sangat mempengaruhi" in v:
        return 2
    elif "cukup" in v:
        return 3
    elif "tidak" in v:
        return 4
    elif "belum" in v:
        return 5

    try:
        return float(value)
    except:
        return 0


# --------------------------------------------
# Bagian 4: Fungsi utama ANP (versi DataFrame)
# --------------------------------------------

def run_anp_analysis(df):
    """
    Fungsi utama untuk menghitung ANP.
    Input: DataFrame (bukan path file).
    Output: dictionary hasil analisis.
    """

    print("\n=== DEBUG ANP INPUT ===")
    print("Tipe data diterima:", type(df))
    print("========================\n")
    
    print("Kolom yang terbaca:", df.columns.tolist())
    print("Jumlah kolom:", len(df.columns))
    print("Contoh data baris pertama:")
    print(df.head(1).to_string())
    print("=============================================\n")

    # ✅ Pastikan df adalah DataFrame valid
    if not isinstance(df, pd.DataFrame):
        raise TypeError(f"Tipe data tidak valid untuk run_anp_analysis: {type(df)}")

    # Hapus baris kosong
    df = df.dropna(how="all")

    # Ambil daftar kolom
    cols = df.columns.tolist()
    if len(cols) < 6:
        raise ValueError("File harus memiliki minimal 6 kolom: Nama Usaha + 5 Kriteria.")

    # Ambil 5 kolom terakhir sebagai kriteria
    criteria_cols = cols[-5:]
    alt_col = cols[0]  # kolom pertama dianggap nama usaha / alternatif

    # Konversi teks → angka
    df_numeric = df.copy()
    for col in criteria_cols:
        df_numeric[col] = df_numeric[col].apply(lambda x: translate_value(col, x))

    # Hitung bobot kriteria (default Saaty)
    M, kriteria = build_default_matrix()
    weights = get_eigenvector(M)
    CI, CR = consistency_ratio(M, weights)

    # Normalisasi kolom
    norm_df = df_numeric.copy()
    for col in criteria_cols:
        total = norm_df[col].sum()
        norm_df[col] = norm_df[col] / total if total > 0 else norm_df[col]

    # Hitung skor ANP total
    df_numeric["Skor_Akhir"] = np.dot(norm_df[criteria_cols], weights)
    df_sorted = df_numeric.sort_values(by="Skor_Akhir", ascending=False)

    # Buat grafik hasil
    plt.figure(figsize=(8, 5))
    plt.barh(df_sorted[alt_col], df_sorted["Skor_Akhir"], color="#5D6D7E")
    plt.xlabel("Nilai Prioritas ANP (0–1)")
    plt.ylabel("Alternatif Lokasi Usaha")
    plt.title("Hasil Analisis ANP (Default Saaty)")
    plt.gca().invert_yaxis()
    plt.tight_layout()

    chart_path = os.path.join(CHART_DIR, "hasil_anp.png")
    plt.savefig(chart_path)
    plt.close()

    # Kesimpulan otomatis
    top3 = df_sorted.head(3).reset_index(drop=True)
    summary = f"Lokasi terbaik adalah {top3.iloc[0,0]} dengan skor {top3.iloc[0,-1]:.3f}. "
    if len(top3) > 1:
        summary += f"Peringkat kedua {top3.iloc[1,0]} ({top3.iloc[1,-1]:.3f}), "
    if len(top3) > 2:
        summary += f"dan ketiga {top3.iloc[2,0]} ({top3.iloc[2,-1]:.3f})."
    summary += f" CI={CI}, CR={CR}."

    # Hasil akhir dikembalikan
    return {
        "ranking": df_sorted.to_dict(orient="records"),
        "chart": "static/charts/hasil_anp.png",
        "weights": dict(zip(kriteria, weights)),
        "CI": CI,
        "CR": CR,
        "summary": summary
    }

# --------------------------------------------
# Bagian 5: Tes mandiri (opsional)
# --------------------------------------------
if __name__ == "__main__":
    M, kriteria = build_default_matrix()
    weights = get_eigenvector(M)
    CI, CR = consistency_ratio(M, weights)

    print("=== Bobot Kriteria (default) ===")
    for k, w in zip(kriteria, weights):
        print(f"{k}: {w:.4f}")
    print(f"CI = {CI}, CR = {CR}")
