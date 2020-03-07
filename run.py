import contextlib
from distutils.dir_util import copy_tree
import multiprocessing
import os
import re
import shutil
import subprocess
import tempfile
import typing

import matplotlib.pyplot as plt

from SPRT_pentanomial import SPRT

TUNABLE_YOSYS_BASE_BRANCH = "master"
TUNABLE_YOSYS_NEW_BRANCH = "master"
TUNABLE_NEXTPNR_BASE_BRANCH = "master"
TUNABLE_NEXTPNR_NEW_BRANCH = "master"
TUNABLE_YOSYS_BASE_OPTS = ""
TUNABLE_YOSYS_NEW_OPTS = "-abc9"
TUNABLE_NEXTPNR_BASE_OPTS = "--placer heap --router router1"
TUNABLE_NEXTPNR_NEW_OPTS = "--placer heap --router router1"
TUNABLE_SOURCE_PATH = "/verilog/benchmarks_large/picosoc/"
TUNABLE_SOURCE_NAME = "picorv32_large.v"
TUNABLE_TEMP_DIR = "/mnt/d/"
NPROC = 4
ALPHA = 0.0001
BETA = 0.0001
ELO0 = 0
ELO1 = 50

# https://stackoverflow.com/a/24176022
@contextlib.contextmanager
def change_directory(newdir: str):
    prevdir = os.getcwd()
    os.chdir(os.path.expanduser(newdir))
    try:
        yield
    finally:
        os.chdir(prevdir)

def build_yosys(branch: str, dir: str):
    with change_directory(dir):
        subprocess.run(["git", "clone", "--recursive", "--depth", "1", "--branch", branch, "https://github.com/YosysHQ/yosys"], check=True)
        with change_directory(dir + "/yosys"):
            with open("Makefile.conf", "w") as f:
                # We don't use TCL or readline; ignore them
                f.write("ENABLE_TCL := 0\n")
                f.write("ENABLE_READLINE := 0\n")
            subprocess.run(["make", "-j" + str(NPROC)], check=True)
            subprocess.run(["make", "install", "PREFIX=" + dir], check=True)
        shutil.rmtree(dir + "/yosys", ignore_errors=True)

def build_icestorm(dir: str):
    with change_directory(dir):
        subprocess.run(["git", "clone", "--recursive", "--depth", "1", "--branch", "master", "https://github.com/cliffordwolf/icestorm"], check=True)
        with change_directory(dir + "/icestorm"):
            subprocess.run(["make", "-j" + str(NPROC), "ICEPROG=0"], check=True)
            subprocess.run(["make", "install", "PREFIX=" + dir], check=True)
        shutil.rmtree(dir + "/icestorm", ignore_errors=True)

def build_nextpnr(branch: str, dir: str):
    with change_directory(dir):
        subprocess.run(["git", "clone", "--depth", "1", "--branch", branch, "https://github.com/YosysHQ/nextpnr"], check=True)
        with change_directory(dir + "/nextpnr"):
            subprocess.run(["cmake", "-DARCH=ice40", "-DICEBOX_ROOT=" + dir + "/../../share/icebox", "-DBUILD_GUI=OFF", "-DBUILD_PYTHON=OFF", "-DCMAKE_INSTALL_PREFIX=" + dir, "."], check=True)
            subprocess.run(["make", "-j" + str(NPROC)], check=True)
            subprocess.run(["make", "install"])
        shutil.rmtree(dir + "/nextpnr", ignore_errors=True)

def fetch_yosys_bench(dir: str):
    with change_directory(dir):
        subprocess.run(["git", "clone", "--recursive", "--depth", "1", "--branch", "master", "https://github.com/YosysHQ/yosys-bench"], check=True)

def build_netlist(dir: str, source_name: str, netlist_name: str, yosys_path: str, yosys_opts: str):
    with change_directory(dir):
        subprocess.run(["python3", "generate.py"], check=True)
        subprocess.run([yosys_path + "/bin/yosys", "-p", "synth_ice40 -json " + netlist_name + " " + yosys_opts, source_name], check=True)

def place_and_route_netlist(dir: str, nextpnr_path: str, netlist_name: str, seed: int):
    with change_directory(dir):
        cmd = subprocess.run([
                nextpnr_path + "/bin/nextpnr-ice40",
                "--hx8k",
                "--package", "ct256",
                "--json", netlist_name,
                "--seed", str(seed)
            ], text=True, capture_output=True)
        fmax = 0.0
        for line in cmd.stderr.splitlines():
            match = re.match(r".*: (\d+.\d+) MHz.*", line)
            if match:
                fmax = float(match[1])
        return fmax

def pnr_base_netlist(arg):
    tempdir, seed = arg
    return place_and_route_netlist(
        tempdir + "/yosys-bench/" + TUNABLE_SOURCE_PATH,
        tempdir + "/base/nextpnr/",
        "base.json",
        seed
    )

def pnr_new_netlist(arg):
    tempdir, seed = arg
    return place_and_route_netlist(
        tempdir + "/yosys-bench/" + TUNABLE_SOURCE_PATH,
        tempdir + "/new/nextpnr/",
        "new.json",
        seed
    )

if __name__ == "__main__":
    base_results = []
    new_results = []
    LA = 0
    LB = 0
    LLR = []

    with tempfile.TemporaryDirectory(prefix=TUNABLE_TEMP_DIR) as tempdir:
        for dir in (tempdir + "/base", tempdir + "/new", tempdir + "/base/yosys", tempdir + "/new/yosys", tempdir + "/base/nextpnr", tempdir + "/new/nextpnr"):
            os.mkdir(dir)

        # Base Yosys build
        build_yosys(TUNABLE_YOSYS_BASE_BRANCH, tempdir + "/base/yosys")

        # New Yosys build (skipped if identical source branch to base)
        if TUNABLE_YOSYS_NEW_BRANCH != TUNABLE_YOSYS_BASE_BRANCH:
            build_yosys(TUNABLE_YOSYS_BASE_BRANCH, tempdir + "/new/yosys")
        else:
            copy_tree(tempdir + "/base/yosys", tempdir + "/new/yosys")

        # Icestorm
        build_icestorm(tempdir)

        # Base nextpnr build
        build_nextpnr(TUNABLE_NEXTPNR_BASE_BRANCH, tempdir + "/base/nextpnr")

        # New nextpnr build (skipped if identical source branch to base)
        if TUNABLE_NEXTPNR_NEW_BRANCH != TUNABLE_NEXTPNR_BASE_BRANCH:
            build_nextpnr(TUNABLE_NEXTPNR_BASE_BRANCH, tempdir + "/new/nextpnr")
        else:
            copy_tree(tempdir + "/base/nextpnr", tempdir + "/new/nextpnr")

        # Yosys-bench
        fetch_yosys_bench(tempdir)

        # Base netlist build
        build_netlist(tempdir + "/yosys-bench/" + TUNABLE_SOURCE_PATH, TUNABLE_SOURCE_NAME, "base.json", tempdir + "/base/yosys", TUNABLE_YOSYS_BASE_OPTS)

        # New netlist build (skipped if identical source branch and identical options)
        if TUNABLE_YOSYS_NEW_BRANCH != TUNABLE_YOSYS_BASE_BRANCH or TUNABLE_YOSYS_NEW_OPTS != TUNABLE_YOSYS_BASE_OPTS:
            build_netlist(tempdir + "/yosys-bench/" + TUNABLE_SOURCE_PATH, TUNABLE_SOURCE_NAME, "new.json", tempdir + "/new/yosys", TUNABLE_YOSYS_NEW_OPTS)
        else:
            shutil.copy2(tempdir + "/yosys-bench/" + TUNABLE_SOURCE_PATH + "base.json", tempdir + "/yosys-bench/" + TUNABLE_SOURCE_PATH + "new.json")

        p = multiprocessing.Pool(NPROC)

        s = SPRT(alpha=ALPHA, beta=BETA, elo0=ELO0, elo1=ELO1, mode="trinomial")

        LA, LB = s.LA, s.LB

        N = 0

        while s.status() == '':
            br = p.map(
                pnr_base_netlist,
                [(tempdir, x + N) for x in range(NPROC)]
            )
            nr = p.map(
                pnr_new_netlist,
                [(tempdir, x + N) for x in range(NPROC)]
            )

            base_results = base_results + br
            new_results = new_results + nr

            N += NPROC

            for i in range(NPROC):
                if nr[i] > br[i]:
                    s.record(2)
                elif nr[i] == br[i]:
                    s.record(1)
                else:
                    s.record(0)

                print("{},{},{},{}".format(s.LLR(), s.LA, s.LB, "W" if nr[i] > br[i] else "D" if nr[i] == br[i] else "L"))

                LLR.append(s.LLR())

                if s.status() != '':
                    break

        p.close()
        p.join()

        print("{} was accepted".format(s.status()))

    fig, axes = plt.subplots(2, 1)
    top, bot = axes.flatten()

    # Top: histogram of Fmax results, with two lines representing average Fmax.
    top.hist([base_results, new_results], density=True, stacked=True, label=[
        ' '.join(["Base: Yosys",TUNABLE_YOSYS_BASE_BRANCH,TUNABLE_YOSYS_BASE_OPTS,"; nextpnr",TUNABLE_NEXTPNR_BASE_BRANCH,TUNABLE_NEXTPNR_NEW_OPTS]),
        ' '.join(["New: Yosys",TUNABLE_YOSYS_NEW_BRANCH,TUNABLE_YOSYS_NEW_OPTS,"; nextpnr",TUNABLE_NEXTPNR_NEW_BRANCH,TUNABLE_NEXTPNR_NEW_OPTS])
    ], color=["blue", "yellow"])
    top.vlines([sum(base_results) / len(base_results), sum(new_results) / len(new_results)], 0, 1, color=["blue", "yellow"])
    top.legend()

    # Bottom: SPRT over time.
    bot.hlines([LA, LB], 0, len(base_results), label=["Fail threshold", "Pass threshold"], color=["red", "green"])
    bot.plot(LLR, "b-", label="Log-likelihood ratio")

    plt.savefig("output.png", bbox_inches="tight")
