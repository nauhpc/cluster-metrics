#!/usr/bin/env python3

# Made SRCC's script compatible with python3!
# Original Script:
# https://raw.githubusercontent.com/stanford-rc/hpc-dashboards/master/sched/slurm/slurm.py

from io import BytesIO
from io import StringIO
import subprocess

import re, sys, argparse

from datetime import datetime
from time import mktime, time

from ClusterShell.Task import task_self, TimeoutError
from ClusterShell.Event import EventHandler

import socket

## -- Helpers -----------------------------------------------------------------

# adapted from top answer here:
# https://stackoverflow.com/questions/1908878/netcat-implementation-in-python
def send_data(hostname, port, data):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.connect((hostname, port))
    s.sendall(data.encode())
    s.shutdown(socket.SHUT_WR)
    print("sent " + str(data.count('\n')) + f" lines of data to {hostname} {port}")
    s.close()

def now():
    """
    Return the exact same timestamp, required for metrics consistency
    """
    return _now

def strdate_to_ts(s):
    """
    Helper function to convert human readable sdiag date into timestamp
    """
    return int(mktime(
        datetime.strptime(s.strip(), "%a %b %d %H:%M:%S %Y").timetuple()))

def carbon_prefix(s):
    """
    Helper function to add a prefix to carbon metric names if defined
    """
    out='.'.join(filter(None, ([CARBON_PREFIX, str(s)])))
    return re.sub(r'\.{2,}', '.', out)


## -- EventHandler classes (process command output) ---------------------------

class SQueueHdlr(EventHandler):
    """ClusterShell event handler for squeue command execution."""

    def __init__(self):
        """event handler initializer: declare dicts"""
        # (group, user, partition, gres, state) => jobs (int)
        self.jobs = {}
        # (group, user, partition, gres, state) => cpus (int)
        self.cpus = {}
        # (group, user, partition, gres, state, reason) => jobs (int)
        self.jobs_r = {}
        # (group, user, partition, gres, state, reason) => cpus (int)
        self.cpus_r = {}
        # (group, user, partition, gres, state) => gpus (int)
        self.gpus = {}

    def ev_read(self, worker):
        """read line from squeue command"""
        try:
            # workaround for Slurm 18
            worker.current_msg = worker.current_msg.decode()
            worker.current_msg = re.sub(' +', ' ', worker.current_msg)
            #
            group, user, partition, gres, state, cpus, nodes, reason = \
                worker.current_msg.split(' ', 7)
            # workaround for Slurm 18
            if gres == 'N/A':
                gres = 'null'
        except ValueError:
            print(f"LINE PARSED: {worker.current_msg}", file=sys.stderr)
            # print >>sys.stderr, "LINE PARSED: %s" % worker.current_msg
            raise
        # without reason
        key = (group, user, partition, gres, state)
        try:
            self.jobs[key] += 1
            self.cpus[key] += int(cpus)
        except KeyError:
            self.jobs[key] = 1
            self.cpus[key] = int(cpus)

        # with reason
        reason = reason.split(',')[0]
        if reason.startswith('('):
            reason = reason.strip('(').strip(')').replace(' ', '_')
            key = (group, user, partition, gres, state, reason)
            try:
                self.jobs_r[key] += 1
                self.cpus_r[key] += int(cpus)
            except KeyError:
                self.jobs_r[key] = 1
                self.cpus_r[key] = int(cpus)

        # GPUs
        try:
            key = (group, user, partition, gres, state)
            # gres returns the number of gpus per nodes
            gres = gres.split(':')
            if gres[0] == 'gpu':  # gres type
                gpus = int(gres[-1]) * int(nodes)
                try:
                    self.gpus[key] += gpus
                except KeyError:
                    self.gpus[key] = gpus
        except IndexError:  # ignore error if we can't parse
            pass

    def ev_close(self, worker):
        """squeue command done: print results extracted from dicts"""
        # without reason
        for (group, user, partition, gres, state), jobs in self.jobs.items():
            out="squeue.%s.%s.%s.%s.%s.jobs %d %d" % (
                group, user, partition, re.sub('[()]','', gres), state, jobs,
                now())
            print(carbon_prefix(out))
        for (group, user, partition, gres, state), cpus in self.cpus.items():
            out="squeue.%s.%s.%s.%s.%s.cpus %d %d" % (
                group, user, partition, re.sub('[()]','', gres), state, cpus,
                now())
            print(carbon_prefix(out))
        # with reason
        for (group, user, partition, gres, state, reason), jobs in self.jobs_r.items():
            out="squeue.%s.%s.%s.%s.%s.reasons.%s.jobs %d %d" % (
                group, user, partition, re.sub('[()]','', gres), state, reason, jobs,
                now())
            print(carbon_prefix(out))
        for (group, user, partition, gres, state, reason), cpus in self.cpus_r.items():
            out="squeue.%s.%s.%s.%s.%s.reasons.%s.cpus %d %d" % (
                group, user, partition, re.sub('[()]','', gres), state, reason, cpus,
                now())
            print(carbon_prefix(out))
        # GPUs
        for (group, user, partition, gres, state), gpus in self.gpus.items():
            out="squeue.%s.%s.%s.%s.%s.gpus %d %d" % (
                group, user, partition, re.sub('[()]','', gres), state, gpus, now())
            print(carbon_prefix(out))



class SDiagHdlr(EventHandler):
    """ClusterShell event handler for sdiag command execution."""

    def __init__(self):
        """initializer: compile regexp used to parse sdiag output"""
        self.section = "sdiag"
        self.root_patterns = {
            # Server thread count: 5
            # Agent queue size:    0
            # Jobs submitted: 2915
            # Jobs started:   1707
            # Jobs completed: 1653
            # Jobs canceled:  20
            # Jobs failed:    0
            # Data since      Sun Jun 21 17:00:00 2015
            'thread_count':     re.compile(r"Server thread count:\s*(?P<thread_count>\d*)"),
            'agent_queue_size': re.compile(r"Agent queue size:\s*(?P<agent_queue_size>\d*)"),
            'jobs.submitted':   re.compile(r"Jobs submitted:\s*(?P<submitted>\d*)"),
            'jobs.started':     re.compile(r"Jobs started:\s*(?P<started>\d*)"),
            'jobs.completed':   re.compile(r"Jobs completed:\s*(?P<completed>\d*)"),
            'jobs.canceled':    re.compile(r"Jobs canceled:\s*(?P<canceled>\d*)"),
            'jobs.failed':      re.compile(r"Jobs failed:\s*(?P<failed>\d*)"),
            'data_since':      (re.compile(r"Data since\s*(?P<data_since>\w+\s+\w+\s+\d+\s+\d+[:]\d+[:]\d+\s+\d+)\s*.*"), strdate_to_ts),
        }
        self.sched_main_patterns = {
            # Last cycle:   11079
            # Max cycle:    123872
            # Total cycles: 2724
            # Mean cycle:   17276
            # Mean depth cycle:  314
            # Cycles per minute: 13
            # Last queue length: 2176
            'last_cycle_usec': re.compile(r"\s*Last cycle:\s*(?P<last_cycle_usec>\d*)"),
            'max_cycle_usec': re.compile(r"\s*Max cycle:\s*(?P<max_cycle_usec>\d*)"),
            'total_cycles': re.compile(r"\s*Total cycles:\s*(?P<total_cycles>\d*)"),
            'mean_cycle_usec': re.compile(r"\s*Mean cycle:\s*(?P<mean_cycle_usec>\d*)"),
            'mean_depth_cycle': re.compile(r"\s*Mean depth cycle:\s*(?P<mean_depth_cycle>\d*)"),
            'cycles_per_minute': re.compile(r"\s*Cycles per minute:\s*(?P<cycles_per_minute>\d*)"),
            'last_queue_length': re.compile(r"\s*Last queue length:\s*(?P<last_queue_length>\d*)"),
        }
        self.sched_backfill_patterns = {
            # Total backfilled jobs (since last slurm start): 3310
            # Total backfilled jobs (since last stats cycle start): 102
            # Total cycles: 289
            # Last cycle when: Sun Jun 21 19:42:02 2015
            # Last cycle: 2513675311
            # Mean cycle: 7318652
            # Last depth cycle: 2534
            # Last depth cycle (try sched): 60
            # Depth Mean: 3024
            # Depth Mean (try depth): 62
            # Last queue length: 2713
            # Queue length mean
            'total_bf_jobs': re.compile(r"\s*Total backfilled jobs \(since last slurm start\):\s*(?P<total_bf_jobs>\d*)"),
            'total_bf_jobs_since_reset': re.compile(r"\s*Total backfilled jobs \(since last stats cycle start\):\s*(?P<total_bf_jobs_since_reset>\d*)"),
            'total_cycles': re.compile(r"\s*Total cycles:\s*(?P<total_cycles>\d*)"),
            'last_cycle_time': (re.compile(r"Last cycle when:\s*(?P<last_cycle_time>\w+\s+\w+\s+\d+\s+\d+[:]\d+[:]\d+\s+\d+)\s*.*"), strdate_to_ts),
            'last_cycle_usec': re.compile(r"\s*Last cycle:\s*(?P<last_cycle_usec>\d*)"),
            'mean_cycle_usec': re.compile(r"\s*Mean cycle:\s*(?P<mean_cycle_usec>\d*)"),
            'last_depth_cycle': re.compile(r"\s*Last depth cycle:\s*(?P<last_depth_cycle>\d*)"),
            'last_depth_cycle_try': re.compile(r"\s*Last depth cycle \(try sched\):\s*(?P<last_depth_cycle_try>\d*)"),
            'depth_mean': re.compile(r"\s*Depth Mean:\s*(?P<depth_mean>\d*)"),
            'depth_mean_try': re.compile(r"\s*Depth Mean \(try depth\):\s*(?P<depth_mean_try>\d*)"),
            'last_queue_length': re.compile(r"\s*Last queue length:\s*(?P<last_queue_length>\d*)"),
            'queue_length_mean': re.compile(r"\s*Queue length mean:\s*(?P<queue_length_mean>\d*)"),
        }
        #TODO
        #self.sched_rpc_bytype_patterns = {
            # REQUEST_PARTITION_INFO    ( 2009) count:4559   ave_time:17809  total_time:81192898
        #}

    def ev_read(self, worker):
        """read line from sdiag command"""
        msg = worker.current_msg.decode()
        # Look at sdiag section change
        if msg.startswith("Main schedule statistics"):
            self.section = "sdiag.scheduler.main"
        elif msg.startswith("Backfilling stats"):
            self.section = "sdiag.scheduler.backfill"

        # Handle section specific content
        if self.section == "sdiag":
            for key, pat in self.root_patterns.items():
                if type(pat) is tuple: # include post-convert function?
                    pat, fun = pat
                else:
                    fun = lambda x: x
                match = pat.match(msg)
                if match:
                    out="%s.%s %s %d" % (self.section, key,
                                 fun(match.group(key.split('.')[-1])), now())
                    print(carbon_prefix(out))

        elif self.section == "sdiag.scheduler.main":
            for key, pat in self.sched_main_patterns.items():
                match = pat.match(msg)
                if match:
                    out="%s.%s %s %d" % (self.section, key,
                                         match.group(key), now())
                    print(carbon_prefix(out))

        elif self.section == "sdiag.scheduler.backfill":
            for key, pat in self.sched_backfill_patterns.items():
                if type(pat) is tuple: # include post-convert function?
                    pat, fun = pat
                else:
                    fun = lambda x: x
                match = pat.match(msg)
                if match:
                    out="%s.%s %s %d" % (self.section, key,
                                               fun(match.group(key)), now())
                    print(carbon_prefix(out))



class SInfoHdlr(EventHandler):
    """ClusterShell event handler for sinfo command execution."""

    def __init__(self):
        """initalizer: compile regexp pattern used to parse sinfo output"""
        self.pattern = re.compile(
            r"(?P<partition>.*)\s(?P<mem>\d*)\s(?P<cpu>\d*)\s"
            r"(?P<features>.*)\s(?P<gres>.*)\s"
            r"(?P<state>[^*$~#]*)[*$~#]?\s(?P<nodecnt>\d*)\s"
            r"(?P<allocated>\d*)/(?P<idle>\d*)/(?P<other>\d*)/(?P<total>\d*)")
        self.transtable = "".maketrans('.', '_')
        self.partitions = set()
        self.nodes = {}
        self.nodes_total = {}
        self.cpus = {}
        self.cpus_total = {}

    def ev_read(self, worker):
        """read line from sinfo command"""
        # owners 64000 16 CPU_IVY,E5-2650v2,2.60GHz,GPU_KPL,TITAN_BLACK,titanblack gpu:gtx:4 mixed 2 8/24/0/32
        msg = worker.current_msg.decode()
        match = self.pattern.match(msg)
        if match:
            # get partition name (cleaned) and add to a set for partition_count
            partition = match.group("partition").translate(str.maketrans('', '', '*'))
            # partition = match.group("partition").translate(None, '*')
            self.partitions.add(partition)
            features = match.group("features").translate(str.maketrans('.', '_', '*'))
            # features = match.group("features").translate(self.transtable, '*')
            gres = match.group("gres")
            # build path
            base_path = "sinfo.%s.%s.%s.%s.%s" % ( partition,
                match.group("mem"), match.group("cpu"), features,
                re.sub('[()]','', gres) )
            base_path=carbon_prefix(base_path)

            # build dicts to handle any duplicates and also total...

            # nodes
            state = match.group("state")
            nodecnt = int(match.group("nodecnt"))

            if base_path not in self.nodes:
                self.nodes[base_path] = {'allocated': 0, 'completing': 0,
                                         'down': 0, 'drained': 0,
                                         'draining': 0, 'idle': 0,
                                         'maint':0, 'mixed': 0, 'unknown': 0 }
                self.nodes[base_path][state] = 0 # in case of another state
                self.nodes_total[base_path] = 0

            self.nodes_total[base_path] += nodecnt

            try:
                self.nodes[base_path][state] += nodecnt
            except KeyError:
                self.nodes[base_path][state] = nodecnt

            # CPUs
            if base_path not in self.cpus:
                self.cpus[base_path] = { 'allocated': 0,
                                         'idle': 0,
                                         'other': 0 }
                self.cpus_total[base_path] = 0

            for cpustate in ('allocated', 'idle', 'other'):
                self.cpus[base_path][cpustate] += int(match.group(cpustate))

            self.cpus_total[base_path] += int(match.group('total'))

    def ev_close(self, worker):
        """sinfo command finished"""
        # Print partition count
        base_path = carbon_prefix("sinfo.partition_count")
        print("%s %d %d" % (base_path, len(self.partitions), now()))
        # Print all details
        for base_path, stated in self.nodes.items():
            for state, nodecnt in stated.items():
                print("%s.nodes.%s %d %d" % (base_path, state, nodecnt, now()))
        for base_path, totalcnt in self.nodes_total.items():
            print("%s.nodes_total %d %d" % (base_path, totalcnt, now()))
        for base_path, stated in self.cpus.items():
            for state, cpucnt in stated.items():
                print("%s.cpus.%s %d %d" % (base_path, state, cpucnt, now()))
        for base_path, totalcnt in self.cpus_total.items():
            print("%s.cpus_total %d %d" % (base_path, totalcnt, now()))

class SInfoExtrasHdlr(EventHandler):
    """ClusterShell event handler for sinfo command execution."""

    def __init__(self):
        """initalizer: compile regexp pattern used to parse sinfo output"""
        self.metrics = dict()

    def ev_read(self, worker):
        """read line from sinfo command"""
        # owners 64000 16 CPU_IVY,E5-2650v2,2.60GHz,GPU_KPL,TITAN_BLACK,titanblack gpu:gtx:4 mixed 2 8/24/0/32
        msg = worker.current_msg.decode()
        msg = msg.split()

        if len(msg) != 8:
            return

        nodes = int(msg[0])
        partition = msg[1] if msg[1][-1] != '*' else msg[1][0:-1]
        features = msg[2]
        gres_used = msg[3]
        gres = msg[4]
        alloc_mem = int(msg[5])
        mem = int(msg[6])
        cpus = int(msg[7])

        gpu_index = gres_used.find("gpu:tesla:")
        if gpu_index != -1:
            start = gpu_index+10
            end = gres_used.find("(", start)
            gpus_allocated = int(gres_used[start:end])
        else:
            gpus_allocated = 0

        gpu_index = gres.find("gpu:tesla:")
        if gpu_index != -1:
            start = gpu_index+10
            end = gres.find("(", start)
            gpus_total = int(gres[start:end])
        else:
            gpus_total = 0

        # print(f'{[nodes, partition, features, gres, gres_used, alloc_mem, mem]}')
        # print(f'    gpus_allocated: {gpus_allocated}, gpus_total: {gpus_total}, alloc_mem: {alloc_mem}, mem: {mem}')

        # filter out '(', ')', '.'
        partition = partition.replace('.', '_').replace('(', '').replace(')', '')
        features = features.replace('.', '_').replace('(', '').replace(')', '')
        gres = gres.replace('.', '_').replace('(', '').replace(')', '')        
        identifier = (partition, str(mem), str(cpus), features, gres)

        alloc_mem *= nodes
        mem *= nodes

        if identifier in self.metrics:
            # add
            self.metrics[identifier]['gpus.allocated'] += gpus_allocated
            self.metrics[identifier]['gpus.total'] += gpus_total
            self.metrics[identifier]['mem.allocated'] += alloc_mem
            self.metrics[identifier]['mem.total'] += mem
        else:
            self.metrics[identifier] = {'gpus.allocated': gpus_allocated,
                                        'gpus.total': gpus_total,
                                        'mem.allocated': alloc_mem,
                                        'mem.total': mem}

    def ev_close(self, worker):
        """sinfo command finished"""
        # Print all details
        for identifier in self.metrics:
            for field in self.metrics[identifier]:
                base_path = "sinfo." + (".".join(identifier))
                base_path = carbon_prefix(base_path)
                value = self.metrics[identifier][field]
                print(f"{base_path}.{field} {value} {int(now())}")

class SShareHdlr(EventHandler):
    """ClusterShell event handler for sshare command execution."""

    def __init__(self):
        """initalizer: compile regexp pattern used to parse sshare output"""
        self.assoc_tree_path = []

    def ev_read(self, worker):
        """parse sshare line"""
        # number of fields will be different if Level FS is configured
        fields = worker.current_msg.decode().split('|')
        if len(fields) == 11:
            account, user, raw_shares, norm_shares, raw_usage, norm_usage, \
                eff_usage, fairshare, level_fs, grpcpumins, \
                cpurunmins = fields
        elif len(fields) == 10:
            level_fs = None
            account, user, raw_shares, norm_shares, raw_usage, norm_usage, \
                eff_usage, fairshare, grpcpumins, cpurunmins = fields
        else:
            print(f"LINE PARSED: {worker.current_msg}", file=sys.stderr)
            # print >>sys.stderr, "LINE PARSED: %s" % worker.current_msg
            raise Exception("parsing failed")

        level = account.count(' ')
        if len(user) == 0:
            if level >= len(self.assoc_tree_path):
                assert level == len(self.assoc_tree_path)
                self.assoc_tree_path.append(account.strip())
            else:
                self.assoc_tree_path[level] = account.strip()
                self.assoc_tree_path = self.assoc_tree_path[:level + 1]
            if level_fs:
                if level_fs is 'inf':
                    level_fs = sys.maxint
                level_fs = float(level_fs)
                out="sshare.level_fs.%s %f %d" % \
                    ('.'.join(self.assoc_tree_path), level_fs, now())
                print(carbon_prefix(out))
        else:
            # User
            if level_fs:
                if level_fs is 'inf':
                    level_fs = sys.maxint
                level_fs = float(level_fs)
                out="sshare.level_fs.%s.%s %f %d" % \
                    ('.'.join(self.assoc_tree_path), user, level_fs, now())
                print(carbon_prefix(out))
            assert fairshare
            out="sshare.fairshare.%s.%s %f %d" % \
                ('.'.join(self.assoc_tree_path), user, float(fairshare), now())
            print(carbon_prefix(out))

def get_gpu_metrics():
    cmd = "nvidia-smi "
    cmd += "--query-gpu=name,index,temperature.gpu,utilization.gpu,utilization.memory,memory.total,memory.free,memory.used "
    cmd += "--format=csv,noheader,nounits"
    return_code, out = subprocess.getstatusoutput(cmd)
    if return_code != 0:
        return out

    result = ""
    col_names = ["name", "index", "temperature_gpu", "utilization_gpu", "utilization_memory", "memory_total", "memory_free", "memory_used"]
    for line in out.splitlines():
        cols = line.split(', ')
        data = {col_names[i]:cols[i] for i in range(len(col_names))}
        for i in range(2, len(cols)):
            result += f"{col_names[i]}{{gpu=\"{data['name']}[{data['index']}]\"}} {data[col_names[i]]}\n"
    return result

if __name__ == '__main__':
    global _now
    global CARBON_PREFIX

    parser = argparse.ArgumentParser(
        description='Export Slurm metrics to Graphite')
    parser.add_argument('-p',
                        '--prefix',
                        type=str,
                        help='metrics prefix (i.e. slurm.)')
    parser.add_argument('--no-squeue',
                        action='store_true',
                        default=False,
                        help='do not grab squeue metrics')
    parser.add_argument('--no-sinfo',
                        action='store_true',
                        default=False,
                        help='do not grab sinfo metrics')
    parser.add_argument('--no-sdiag',
                        action='store_true',
                        default=False,
                        help='do not grab sdiag metrics')
    parser.add_argument('--no-sshare',
                        action='store_true',
                        default=False,
                        help='do not grab sshare metrics')
    parser.add_argument('-s',
                        '--send',
                        action='store_true',
                        default=False,
                        help='send data to graphite\'s over the plaintext protocol')
    parser.add_argument('-P',
                        '--port',
                        type=int,
                        default=2003,
                        help=('port to send data to (default: 2003), '
                              'must also enable the -s/--send flag'))
    parser.add_argument('-t',
                        '--target',
                        type=str,
                        default='localhost',
                        help=('the hostname or ip to send the graphite data '
                              'to (default: localhost), must also enable the '
                              '-s/--send flag'))
    args = parser.parse_args()


    if args.prefix:
        CARBON_PREFIX=args.prefix
    else:
        CARBON_PREFIX=None

    _now = time()

    # Get clustershell task object
    task = task_self()
    task.set_default('stdout_msgtree', False)

    if args.send:
        old_stdout = sys.stdout
        result = StringIO()
        sys.stdout = result

    # Schedule slurm commands with related handler
    if not args.no_squeue:
        task.shell("squeue -rh -o '%g %u %P %16b %T %C %D %R'",
                   handler=SQueueHdlr(),
                   stderr=True)
    if not args.no_sdiag:
        task.shell("sdiag", handler=SDiagHdlr(), stderr=True)
    if not args.no_sinfo:
        task.shell("sinfo -h -e -o '%R %m %c %f %G %T %D %C'",
                   handler=SInfoHdlr(),
                   stderr=True)
        # we need the GresUsed and AllocMem fields which are *not* available
        # using the -o sinfo option have to use the less flexible -O option
        # with another parser
        task.shell(('sinfo -e -h -o \'%R\' '
                    '-O \'Nodes:500,Partition:500,Features:500,GresUsed:500,Gres:500,AllocMem:500,Memory:500,CPUS:500\''),
                   handler=SInfoExtrasHdlr(),
                   stderr=True)
    if not args.no_sshare:
        task.shell("sshare -ahlP", handler=SShareHdlr(), stderr=True)
    # Launch command execution (in parallel)
    task.resume(timeout=30)

    if args.send:
        sys.stdout = old_stdout
        send_data(args.target, args.port, result.getvalue())
