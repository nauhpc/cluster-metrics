#!/usr/bin/awk -f

# Author: Joseph
# Date: 2022-04-22

# Description: reports metrics the describe the delay for a job to start

function usage() {
    print "Usage:", ENVIRON["_"], "-- [OPTIONS]"
    print "        hostname    hostname of the target, if set, it will send graphite data to port 2003"
    print "                    using the plaintext protocol, otherwise print to stdout"
    print "        -h, --help  display this help message"
}

BEGIN {
    
    NOW = systime()
    BUFFER = ""
    
    for(i=1;i<ARGC;i++) {
        if(ARGV[i] == "-h" || ARGV[i] == "--help") {
            usage()
            exit
        }
        else if(! TARGET) {
            TARGET=ARGV[i]
        }
        else {
            usage()
            exit
        }
    }

    for(i=1;i<=64;i*=2) {
        
        cmd = "srun -c " i " --test-only 3>&1 1>&2 2>&3"
        cmd | getline; close(cmd)
        est_start_str = $7
        
        cmd = "date -d \"" est_start_str "\" +'%s'"
        cmd | getline; close(cmd)
        est_start_epoch = $0
        
        delay = est_start_epoch - NOW
        BUFFER = BUFFER "slurm.srun.estimated_delay." i " " delay " " NOW "\n"
    }
    
    valid_reasons["Priority"] = 1
    valid_reasons["ReqNodeNotAvail"] = 1
    valid_reasons["Resources"] = 1
    valid_reasons["WaitingForScheduling"] = 1

    cmd = "squeue -h -r --state=PENDING -O 'SubmitTime:20,Reason:100'"
    i = 0
    while((cmd | getline) > 0) {
        submit_time = $1
        if(substr($2, length($2), 1) == ",") {
            reason = substr($2, 1, length($2) - 1)
        }
        else {
            reason = $2
        }
        if(reason in valid_reasons) {
            submit_times[i] = submit_time
        }
    }
    close(cmd)

    sum = 0
    for(i in submit_times) {
        cmd = "date -d \"" submit_times[i] "\" +'%s'"
        cmd | getline; close(cmd)
        submit_epochs[i] = int($0)
        sum += int($0)
    }
    
    len = asort(submit_epochs)
    median = (len > 0) ? NOW - submit_epochs[int(len / 2) + 1] : 0
    BUFFER = BUFFER "slurm.squeue.pending_time.median " median " " NOW "\n"

    average = (len > 0) ? NOW - (sum / len) : 0
    BUFFER = BUFFER "slurm.squeue.pending_time.average " average " " NOW "\n"

    if(TARGET) {
        cmd = "nc " TARGET " " 2003
        printf BUFFER | cmd
    }
    else {
        printf BUFFER
    }
}
