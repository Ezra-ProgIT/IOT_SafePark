"""Subprocess fixture only. These generated vectors are NOT physical sensor readings."""
import argparse
import json
import time


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--mode', default='normal')
    parser.add_argument('--startup', type=float, default=.05)
    args = parser.parse_args()
    time.sleep(args.startup)
    if args.mode == 'eof':
        raise SystemExit(0)
    if args.mode == 'malformed':
        print('{"bad": }', flush=True)
        time.sleep(30)
    i = 0
    while True:
        i += 1
        doc = {'Acc': {'values': [i, 0, 9.81]}, 'Gyro': {'values': [1, 0, 0]}}
        if args.mode == 'missing' or (args.mode == 'warmup' and i <= 3):
            del doc['Gyro']
        if args.mode == 'nan':
            doc['Gyro']['values'][0] = float('nan')
        print(json.dumps(doc, indent=2), flush=True)
        if args.mode == 'stall':
            time.sleep(30)
        time.sleep(.03)
