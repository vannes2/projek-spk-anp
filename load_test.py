from locust import HttpUser, task, between
import os

class UserSimulation(HttpUser):
    wait_time = between(2, 5)

    def on_start(self):
        """Inisialisasi file untuk diupload"""
        self.upload_folder = os.path.join(os.getcwd(), "uploads")
        files = [f for f in os.listdir(self.upload_folder) if f.endswith((".csv", ".xlsx", ".xls"))]
        self.test_file = os.path.join(self.upload_folder, files[0]) if files else None

        if not self.test_file:
            print("⚠️ Tidak ada file di folder uploads. Tambahkan satu file CSV/Excel dulu.")
        else:
            print(f"✅ File uji yang digunakan: {os.path.basename(self.test_file)}")

        # Simulasi session user yang sudah login
        self.client.cookies.set("session", "dummy_user_session_value")

    @task(2)
    def upload_file_for_analysis(self):
        """Simulasi user upload file (analisis ANP)"""
        if not self.test_file:
            return
        with open(self.test_file, "rb") as f:
            response = self.client.post("/upload", files={"file": f})
            if response.status_code != 200:
                print(f"⚠️ Gagal upload file: {response.status_code}")

    @task(1)
    def view_dashboard(self):
        """Simulasi buka dashboard"""
        self.client.get("/dashboard")

    @task(1)
    def view_history(self):
        """Simulasi buka halaman riwayat"""
        self.client.get("/history")
