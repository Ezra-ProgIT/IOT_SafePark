"""Physical sensor timing probe, without MQTT. Run from the SmartPark root."""
import argparse
import json
import statistics
import time
from .sensor import TermuxSensor, SensorError


def main():
    parser=argparse.ArgumentParser(description='Profiling sensor fisik SmartPark (tanpa MQTT)')
    parser.add_argument('--acc-name',required=True)
    parser.add_argument('--gyro-name',required=True)
    parser.add_argument('--read-mode',choices=('sequential','combined','stream'),default='sequential')
    parser.add_argument('--sensor-delay-ms',type=int,default=100)
    parser.add_argument('--samples',type=int,default=10)
    args=parser.parse_args()
    if not 2<=args.samples<=100:parser.error('--samples harus 2–100')
    source=TermuxSensor(args.acc_name,args.gyro_name,args.read_mode,stream_delay_ms=args.sensor_delay_ms)
    durations=[];gaps=[];previous=None;errors=0
    print('PROFIL FISIK: timestamp saat baca selesai; bukan timestamp event Android. Gyroscope diasumsikan rad/s.')
    try:
        for i in range(args.samples):
            try:
                sample=source.read();durations.append(sample['read_seconds'])
                if previous is not None:gaps.append(sample['t']-previous)
                previous=sample['t']
                print(json.dumps({'sample':i+1,**sample},allow_nan=False),flush=True)
            except SensorError as exc:
                errors+=1;previous=None;print('SENSOR_ERROR:',exc,flush=True);time.sleep(1)
    finally:
        source.close()
    summary={'mode':args.read_mode,'success':len(durations),'errors':errors,
             'median_read_seconds':statistics.median(durations) if durations else None,
             'max_read_seconds':max(durations) if durations else None,
             'max_observed_gap_seconds':max(gaps) if gaps else None}
    print('SUMMARY '+json.dumps(summary),flush=True)
    if errors:raise SystemExit(1)


if __name__=='__main__':main()
