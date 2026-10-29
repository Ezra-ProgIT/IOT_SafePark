# Verifikasi Android — belum dijalankan pada perangkat fisik

Tanggal persiapan: 29 Oktober 2026 (Asia/Jakarta). Jalankan dari folder SmartPark yang telah diperbarui **pada kedua HP dan laptop**. Simpan salinan proyek lama sebelum mengganti berkas. Jangan menyalin folder SmartPark ke dalam folder SmartPark lagi; isi folder akhirnya harus langsung memuat `smartpark/`, `dashboard/`, dan `requirements.txt`.

## 1. Persiapan

- Matikan instance SmartPark lama agar tidak ada dua sensor/receiver di Project ID yang sama.
- Gunakan Project ID unik, sama pada tiga perangkat. Aktifkan tanggal/waktu otomatis di Android dan Windows karena validasi memakai waktu pesan.
- Pastikan Termux dan Termux:API tersedia dan berasal dari sumber yang kompatibel. Jalankan `termux-sensor -l` pada HP A untuk mencocokkan nama sensor. Nama yang sebelumnya berhasil pada Samsung: `LSM6DSVTR Accelerometer` dan `LSM6DSVTR Gyroscope`.
- Letakkan HP A dengan aman pada prototipe kendaraan diam. Hindari percobaan pada kendaraan yang sedang berjalan.

HP A:

```sh
cd ~/downloads/SmartPark
python -m pip install -r requirements.txt
export SMARTPARK_PROJECT=smartpark_kelompok_unik_2026
termux-sensor -l
```

HP B: masuk ke lokasi proyek yang benar pada HP tersebut (jangan asumsikan sama dengan HP A), lalu:

```sh
python -m pip install -r requirements.txt
export SMARTPARK_PROJECT=smartpark_kelompok_unik_2026
termux-vibrate -d 700
```

Catat apakah getaran manual benar-benar terasa. Exit sukses saja tidak cukup untuk membuktikan getaran fisik.

## 2. Profiling HP A sebelum menilai responsivitas

Jangan menjalankan probe bersamaan dengan program sensor HP A, karena Termux memakai service sensor bersama.

```sh
cd ~/downloads/SmartPark
python -m smartpark.profile_sensor \
  --acc-name "LSM6DSVTR Accelerometer" \
  --gyro-name "LSM6DSVTR Gyroscope" \
  --read-mode sequential --samples 10 | tee profile-sequential.txt

python -m smartpark.profile_sensor \
  --acc-name "LSM6DSVTR Accelerometer" \
  --gyro-name "LSM6DSVTR Gyroscope" \
  --read-mode combined --samples 10 | tee profile-combined.txt
```

Bandingkan `median_read_seconds`, `max_read_seconds`, `max_observed_gap_seconds`, error, serta kelengkapan dua vektor. `combined` mengurangi jumlah proses dari dua menjadi satu per sampel, tetapi pengurangan latensi aktual belum diukur di HP pengguna. Gyroscope Android standar berunit rad/s, tetapi JSON Termux tidak melampirkan unit/timestamp event; pastikan nama yang dipilih benar dan catat keterbatasannya.

Jika command macet, periksa Termux:API dan sensor names, lalu gunakan `termux-sensor -c` ketika tidak ada proses akuisisi lain. Jangan menaikkan batas gap hanya untuk menghilangkan SENSOR_ERROR. Sampel berjeda besar tidak cukup untuk menyimpulkan gerakan terjadi terus-menerus selama jeda itu.

## 3. Jalankan sistem

HP A — jalur lama tetap default:

```sh
cd ~/downloads/SmartPark
python -m smartpark.edge_a --mode termux \
  --acc-name "LSM6DSVTR Accelerometer" \
  --gyro-name "LSM6DSVTR Gyroscope"
```

Jika hasil profiling gabungan lengkap dan lebih responsif, hentikan proses tersebut kemudian uji:

```sh
python -m smartpark.edge_a --mode termux \
  --acc-name "LSM6DSVTR Accelerometer" \
  --gyro-name "LSM6DSVTR Gyroscope" \
  --sensor-read-mode combined
```

HP B — dari folder proyek yang benar:

```sh
python -m smartpark.edge_b --alarm termux
```

Laptop — dari folder SmartPark, setelah dependensi terpasang:

```powershell
.\.venv\Scripts\python.exe -m http.server 8000 --bind 127.0.0.1 -d dashboard
```

Buka `http://localhost:8000`, masukkan Project ID yang sama, lalu Hubungkan MQTT. Tunggu `MQTT CONNECTED` pada kedua HP. Gunakan urutan pengujian berikut.

## 4. Skenario yang wajib dibuktikan

| Langkah | Tindakan | Bukti yang dicatat |
|---|---|---|
| PING | Klik PING dashboard | `Diakui kedua HP`, request ID cocok, ARM keduanya sama; tanpa mengubah monitoring |
| ARM | Klik ARM dashboard | ACK HP A dan HP B, kedua panel ARM; telemetri mulai mengalir |
| Data fisik | Diamkan HP A, lalu gerakkan perlahan | `simulated=false`, DATA FISIK (klaim HP A), angka RMS/rotasi dan grafik berubah sesuai pengukuran |
| Alarm otomatis | Lakukan gerakan berulang minimal dua window yang melewati ambang | Klasifikasi HP B, event `alert=true`, alarm_result, getaran fisik yang diamati langsung pada HP B |
| DISARM dashboard | Klik DISARM saat sebelumnya ARM | ACK kedua HP, keduanya DISARM, tidak ada event baru setelah semua pekerjaan lama selesai |
| Menu HP B | Pilih 1 ARM lalu 2 DISARM | HP A berubah lewat kontrol; HP B mengikuti ACK HP A; dashboard sesuai |
| Stop HP A | Hentikan proses HP A | Last Will jika koneksi hilang mendadak; tanpa Will: aktivitas Stale >15 dtk, Offline >45 dtk; klasifikasi tidak tetap NORMAL |
| Stop HP B | Hentikan proses HP B | HP B menjadi Stale/Offline secara mandiri; data HP A dapat tetap tampil |
| Wi-Fi/internet hilang | Putus jaringan satu perangkat sementara | Status sumber data tidak disamakan dengan koneksi broker dashboard; tidak ada alarm dari paket stale |
| Reconnect | Sambungkan jaringan dan jalankan ulang bila perlu | Subscribe pulih; HP B memakai PING/ACK untuk rekonsiliasi; periksa ARM aktual sebelum melanjutkan |
| Sensor error | Uji nama sensor salah pada proses terpisah | SENSOR_ERROR dengan penyebab; tidak muncul data simulasi sebagai pengganti |
| Ekspor | Klik Ekspor CSV setelah event nyata | Waktu ISO, klasifikasi, nilai, sumber, mode/hasil alarm; cocokkan dengan terminal HP B |

Urutan startup/reconnect perlu dicoba dua arah: HP B lebih dahulu, lalu HP A; berikutnya HP A lebih dahulu, lalu HP B. Saat sebuah perintah timeout, jangan menganggap keadaan belum berubah: kirim PING dan periksa ACK terbaru. Jika dua kontrol dikirim hampir bersamaan, ulangi PING setelah sistem tenang untuk memastikan hasil akhir.

## 5. Eksperimen dan kalibrasi transparan

Jangan ubah ambang sambil mengumpulkan satu seri data. Catat model HP, posisi pemasangan, mode baca, Project ID, ambang, interval, dan waktu tiap eksperimen. Ulangi kondisi diam, getaran singkat, perubahan orientasi, serta gerakan berkelanjutan sedikitnya 5 kali. Simpan log mentah profil dan log A/B; CSV dashboard hanya menyimpan event alarm, bukan seluruh telemetri atau ground truth.

| Percobaan | Kondisi fisik yang dilakukan | Rentang RMS | Rotasi/window + durasi | Keputusan HP B | Getar diamati? | False alarm / miss? |
|---|---|---|---|---|---|---|
| Diisi kelompok | | | | | | |

Pilih ambang berdasarkan sebaran keadaan diam dan skenario gerak, lalu validasi pada seri percobaan lain. Laporkan jumlah false alarm serta gerakan yang terlewat. Jangan menyebut hasil uji lokal simulasi sebagai bukti sensor atau getaran fisik. Definition of Done belum terpenuhi sampai alur fisik tersebut didokumentasikan.
