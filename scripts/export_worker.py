#!/usr/bin/env python3

import sys
import os
import subprocess
import re
from bs4 import BeautifulSoup
from socket import gethostname
import argparse
import pickle
from pathlib import Path

METRIC_MAP = { "boottime": None,
               "cpu_num": "cpu.count",
               "disk_total": None,
               "mem_free": "memory.memory.free",
               "proc_run": "processes.ps_state.running",
               "bytes_in": "interface.total.if_octets.rx",
               "cpu_speed": None,
               "load_fifteen": "load.load.longterm",
               "mem_shared": None,
               "proc_total": "aggregation.processes-sum.ps_state",
               "bytes_out": "interface.total.if_octets.tx",
               "cpu_system": "aggregation.cpu-average.percent.system",
               "load_five": "load.load.midterm",
               "mem_total": None,
               "swap_free": "swap.swap.free",
               "cpu_aidle": None,
               "cpu_user": "aggregation.cpu-average.percent.user",
               "load_one": "load.load.shortterm",
               "part_max_used": None,
               "swap_total": "aggregation.swap-sum.swap",
               "cpu_idle": "aggregation.cpu-average.percent.idle",
               "cpu_wio": "aggregation.cpu-average.percent.wait",
               "mem_buffers": "memory.memory.buffered",
               "pkts_in": None,
               "cpu_nice": "aggregation.cpu-average.percent.nice",
               "disk_free": "aggregation.df-sum.df_complex.free",
               "mem_cached": "memory.memory.cached",
               "pkts_out": None }

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('-p',
                        '--pickle',
                        action='store_true',
                        default=True,
                        help=("Export data into python's pickle format "
                              "(serialized binary data), will use the "
                              ".pkl extension, this should allow for much "
                              "faster importing into graphite"))
    parser.add_argument('-t',
                        '--plaintext',
                        action='store_true',
                        default=False,
                        help="Export data into a plaintext format")
    parser.add_argument('file',
                        type=argparse.FileType('r'),
                        help="RRD file")
    args = parser.parse_args()

    rrdpath = args.file.name
    host = re.search(r'cn[0-9]+\.nauhpc', rrdpath)[0].replace('.nauhpc', '_nauhpc')
    ganglia_metric = os.path.basename(rrdpath).split('.')[0]

    if (ganglia_metric not in METRIC_MAP) or \
       (METRIC_MAP[ganglia_metric] is None):
        sys.stderr.write((f"WARNING: {ganglia_metric} has no equivalent in "
                          "graphite, nothing to do\n"))
        sys.exit(0)
        
    metric = METRIC_MAP[ganglia_metric]

    cmd = f"rrdtool dump {rrdpath}"
    result = subprocess.run(cmd.split(), capture_output=True)
    if result.returncode == 0:
        soup = BeautifulSoup(result.stdout, 'lxml-xml')
        # we use the range [1:-1] because we should have a '<database>'
        # and a '</database>' line
        raw_data = []
        for database in soup.find_all('database'):
            raw_data += str(database).split('\n')[1:-1]

        timestamp_pattern = re.compile(r'(?<=/ )[0-9]+(?= -->)')
        value_pattern  = re.compile(r'(?<=<v>).*(?=</v>)')

        # data will hold tuples, needed so we can sort based on timestamp
        data = []
        for raw_data_point in raw_data:
            # must parse the date and value

            # if the pattern search fails, you should get a nonetype error
            value = float(value_pattern.search(raw_data_point).group())
            timestamp = timestamp_pattern.search(raw_data_point).group()
            data.append((value, timestamp))

        data.sort(key=lambda x: x[1])

        # ensure that our dirs exist
        Path(f"./export/{host}").mkdir(parents=True, exist_ok=True)

        if args.plaintext:
            fp = open(f"./export/{host}/{metric}.txt", "w")
        metric_tuples = []
        for data_point in data:
            value = data_point[0]
            timestamp = data_point[1]
            metric_tuples.append((f"{host}.{metric}", (int(timestamp), value)))
            if args.plaintext:
                fp.write(f"{host}.{metric} {value} {timestamp}\n")

        if args.plaintext:
            fp.close()
            print(f"wrote to: {fp.name}")

        if args.pickle:
            fp = open(f"./export/{host}/{metric}.pkl", "wb")
            pickle.dump(metric_tuples, fp)
            fp.close()
            print(f"wrote to: {fp.name}")
            
    else:
        sys.stderr.write(f"ERROR: Unable to retrieve XML for {rrdpath}")
        sys.exit(1)
