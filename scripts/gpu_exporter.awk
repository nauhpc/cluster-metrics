#!/usr/bin/awk -f

# Author: Joseph
# Date: 2022-03-15

# Description: uses nvidia-smi to export metrics to graphite
#              inspired by https://github.com/zhebrak/nvidia_smi_exporter

function usage() {
    print "Usage:", ENVIRON["_"], "-- [OPTIONS]"
    print "        hostname    hostname of the target, if set, it will send graphite data to port 2003"
    print "                    using the plaintext protocol, otherwise print to stdout"
    print "        -s suffix, --suffix=suffix"
    print "                    the suffix to be appended to the hostname for all metric names"
    print "        -h, --help  display this help message"
}

BEGIN {
    # parse args
    for(i=1;i<ARGC;i++) {
        if(ARGV[i] == "-h" || ARGV[i] == "--help") {
            usage()
            exit
        }
        else if(! SUFFIX && ARGV[i] == "-s") {
            SUFFIX=ARGV[++i]
        }
        else if(! SUFFIX && ARGV[i] ~ /--suffix=[^=]+/) {
            split(ARGV[i], suf_arr, "=")
            SUFFIX=suf_arr[2]
        }
        else if(! TARGET) {
            TARGET=ARGV[i]
        }
        else {
            usage()
            exit
        }
    }

    # set constants
    metric_names[3] = "temp"
    metric_names[4] = "gpu_util"
    metric_names[5] = "mem_util"
    metric_names[6] = "mem_total"
    metric_names[7] = "mem_free"
    metric_names[8] = "mem_used"
    NVIDIA_CMD = "nvidia-smi --query-gpu=name,index,temperature.gpu,utilization.gpu,utilization.memory,memory.total,memory.free,memory.used --format=csv,noheader,nounits"
    buffer = ""

    # get current unix epoch
    epoch = systime()

    # get own hostname
    "hostname" | getline
    hostname = $0 SUFFIX
    close("hostname")

    # parse nvidia-smi output
    FS=", "
    while((NVIDIA_CMD | getline) > 0) {
        if(NF != 8) {
            exit 1
        }
        split($1, arr, " ")
        match(arr[2], /[A-Za-z]+[0-9]+/)
        gpu_name = tolower(substr(arr[2], RSTART, RLENGTH))
        gpu_index = $2
        for(i=3;i<=NF;i++) {
            metric_name = metric_names[i]
            buffer = buffer hostname ".gpu." gpu_name "." gpu_index "." metric_name " "
            buffer = buffer $i " "
            buffer = buffer epoch "\n"
        }
    }
    close(NVIDIA_CMD)

    # result
    if(TARGET) {
        NC_CMD = "nc -N " TARGET " " 2003
        printf buffer | NC_CMD
    }
    else {
        printf buffer
    }
}
