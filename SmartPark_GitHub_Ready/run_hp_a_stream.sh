#!/data/data/com.termux/files/usr/bin/bash
set -e
cd -- "$(dirname -- "$0")"
export SMARTPARK_PROJECT="${SMARTPARK_PROJECT:-smartpark_demo_2026}"
python -m smartpark.check_install
exec python -m smartpark.edge_a --mode termux --acc-name "LSM6DSVTR Accelerometer" --gyro-name "LSM6DSVTR Gyroscope" --sensor-read-mode stream --sensor-delay-ms 100
