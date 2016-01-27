"""
Script to automate verification of the msprime simulator against
Hudson's ms.
"""
from __future__ import print_function
from __future__ import division

import os
import math
import random
import tempfile
import subprocess

import numpy as np
import pandas as pd
import statsmodels.api as sm
import allel
import matplotlib
# Force matplotlib to not use any Xwindows backend.
matplotlib.use('Agg')
from matplotlib import pyplot


import msprime

def harmonic_number(n):
    """
    Returns the nth Harmonic number.
    """
    return sum(1 / k for k in range(1, n + 1))

def get_scaled_recombination_rate(Ne, m, r):
    """
    Returns rho = 4 * Ne * (m - 1) * r, the scaled recombination rate.
    """
    return 4 * Ne * (m - 1) * r

class Simulator(object):
    """
    Superclass of coalescent simulator objects.
    """
    def __init__(
            self, n, m, Ne, r, models=[], mutation_rate=None,
            sample_configuration=None, migration_rate=None,
            migration_matrix=None):
        self.sample_size = n
        self.recombination_rate = r
        self.num_loci = m
        self.effective_population_size = Ne
        self.population_models = models
        self.mutation_rate = mutation_rate
        self.migration_rate = migration_rate
        self.migration_matrix = migration_matrix
        self.sample_configuration = sample_configuration
        self.num_populations = len(sample_configuration)

class MsSimulator(Simulator):
    """
    Class representing Hudson's ms simulator. Takes care of running the
    simulations and collating results.
    """
    def get_executable(self):
        return ["./data/ms/ms"]

    def generate_trees(self):
        return True

    def get_command_line(self, replicates):
        executable = self.get_executable()
        rho = get_scaled_recombination_rate(self.effective_population_size,
                self.num_loci, self.recombination_rate)
        args = executable + [str(self.sample_size), str(replicates)]
        if self.generate_trees():
            args += ["-T"]
        if self.num_loci > 1:
            args += ["-r", str(rho), str(self.num_loci)]
        if self.mutation_rate is not None:
            args += ["-t", str(self.mutation_rate)]
        for model in self.population_models:
            if isinstance(model, msprime.ConstantPopulationModel):
                v = ["-eN", str(model.start_time), str(model.size)]
            elif isinstance(model, msprime.ExponentialPopulationModel):
                v = ["-eG", str(model.start_time), str(model.alpha)]
            else:
                raise ValueError("unknown population model")
            args.extend(v)
        if self.sample_configuration is not None:
            v = ["-I", str(len(self.sample_configuration))] + [
                    str(c) for c in self.sample_configuration]
            if self.migration_matrix is None and self.migration_rate is None:
                raise ValueError("Migration rate must be specified")
            if self.migration_rate is not None:
                v.append(str(self.migration_rate))
            args.extend(v)
            if self.migration_matrix is not None:
                flattened = [str(v) for row in self.migration_matrix for v in row]
                args.extend(["-ma"] + flattened)
        return args


class MsCoalescentStatisticsSimulator(MsSimulator):
    """
    A modified version of ms in which we output statistics about the
    coalescent algorithm.
    """
    def get_executable(self):
        return ["./data/ms/ms_summary_stats"]

    def run(self, replicates):
        with tempfile.TemporaryFile() as f:
            args = self.get_command_line(replicates)
            print(" ".join(args))
            subprocess.call(args, stdout=f)
            f.seek(0)
            df = pd.read_table(f)
        return df

class MutationStatisticsSimulator(object):
    """
    A mixin to run the simulation and pass the results through Hudson's
    sample_stats program.
    """
    def generate_trees(self):
        return False

    def run(self, replicates):
        args = self.get_command_line(replicates)
        print(" ".join(args))
        p1 = subprocess.Popen(args, stdout=subprocess.PIPE)
        p2 = subprocess.Popen(["./data/ms/sample_stats"], stdin=p1.stdout,
                stdout=subprocess.PIPE)
        p1.stdout.close()
        output = p2.communicate()[0]
        with tempfile.TemporaryFile() as f:
            f.write(output)
            f.seek(0)
            df = pd.read_table(f)
        return df


class MsMutationStatisticsSimulator(MutationStatisticsSimulator, MsSimulator):
    """
    Runs ms with a given set of parameters, and returns the mutation
    statistics.
    """

class MsprimeMutationStatisticsSimulator(MutationStatisticsSimulator, MsSimulator):
    """
    Runs msprime with a given set of parameters, and returns the mutation
    statistics.
    """
    def get_executable(self):
        return ["python", "mspms_dev.py"]

class MsprimeCoalescentStatisticsSimulator(Simulator):
    """
    Class to simlify running the msprime simulator and getting summary
    stats over many replicates.
    """
    def run(self, replicates):
        num_trees = [0 for j in range(replicates)]
        time = [0 for j in range(replicates)]
        ca_events = [0 for j in range(replicates)]
        re_events = [0 for j in range(replicates)]
        mig_events = [None for j in range(replicates)]
        for j in range(replicates):
            sim = msprime.TreeSimulator(self.sample_size)
            sim.set_scaled_recombination_rate(4
                * self.effective_population_size * self.recombination_rate)
            sim.set_num_loci(self.num_loci)
            sim.set_max_memory("10G")
            for m in self.population_models:
                sim.add_population_model(m)
            N = len(self.sample_configuration)
            sim.set_sample_configuration(self.sample_configuration)
            if self.migration_rate is not None:
                matrix = [[(self.migration_rate / (N - 1)) * int(j != k)
                        for j in range(N)] for k in range(N)]
            else:
                matrix = self.migration_matrix
            sim.set_migration_matrix(matrix)
            tree_sequence = sim.run()
            num_trees[j] = sim.get_num_breakpoints() + 1
            time[j] = sim.get_time()
            ca_events[j] = sim.get_num_common_ancestor_events()
            re_events[j] = sim.get_num_recombination_events()
            mig_events[j] = [r for row in sim.get_num_migration_events() for r in row]
        d = {
            "t":time, "num_trees":num_trees,
            "ca_events":ca_events, "re_events":re_events}

        for j in range(self.num_populations**2):
            events = [0 for j in range(replicates)]
            for k in range(replicates):
                events[k] = mig_events[k][j]
            d["mig_events_{}".format(j)] = events
        df = pd.DataFrame(d)
        return df

def run_verify_mutations(n, m, Ne, r, models, num_replicates, mutation_rate,
        output_prefix):
    """
    Runs ms and msprime for the specified parameters, and filters the results
    through Hudson's sample_stats program to get distributions of the
    haplotype statistics.
    """
    ms = MsMutationStatisticsSimulator(n, m, r, Ne, models, mutation_rate)
    df_ms = ms.run(num_replicates)
    msp = MsprimeMutationStatisticsSimulator(n, m, r, Ne, models, mutation_rate)
    df_msp = msp.run(num_replicates)
    for stat in ["pi", "ss", "D", "thetaH", "H"]:
        v1 = df_ms[stat]
        v2 = df_msp[stat]
        # pyplot.hist(v1, 20, alpha=0.5, label="ms")
        # pyplot.hist(v2, 20, alpha=0.5, label="msp")
        # pyplot.legend(loc="upper left")
        sm.graphics.qqplot(v1)
        sm.qqplot_2samples(v1, v2, line="45")
        f = "{0}_{1}.png".format(output_prefix, stat)
        pyplot.savefig(f, dpi=72)
        pyplot.clf()

def run_verify_ms_command(n, num_replicates, output_prefix):
    """
    Runs ms and msprime for the specified parameters, and filters the results
    through Hudson's sample_stats program to get distributions of the
    haplotype statistics.
    """
    ms = MsMutationStatisticsSimulator(n, m, r, Ne, models, mutation_rate)
    df_ms = ms.run(num_replicates)
    msp = MsprimeMutationStatisticsSimulator(n, m, r, Ne, models, mutation_rate)
    df_msp = msp.run(num_replicates)
    for stat in ["pi", "ss", "D", "thetaH", "H"]:
        v1 = df_ms[stat]
        v2 = df_msp[stat]
        # pyplot.hist(v1, 20, alpha=0.5, label="ms")
        # pyplot.hist(v2, 20, alpha=0.5, label="msp")
        # pyplot.legend(loc="upper left")
        sm.graphics.qqplot(v1)
        sm.qqplot_2samples(v1, v2, line="45")
        f = "{0}_{1}.png".format(output_prefix, stat)
        pyplot.savefig(f, dpi=72)
        pyplot.clf()


def run_verify_coalescent(
        n, m, Ne, r, models, num_replicates, output_prefix,
        sample_configuration=None, migration_rate=None,
        migration_matrix=None):
    """
    Runs ms and msprime on the specified parameters and outputs qqplots
    of the coalescent simulation summary statistics with the specified
    prefix.
    """
    ms = MsCoalescentStatisticsSimulator(n, m, r, Ne, models,
            sample_configuration=sample_configuration,
            migration_rate=migration_rate,
            migration_matrix=migration_matrix)
    df_ms = ms.run(num_replicates)
    msp = MsprimeCoalescentStatisticsSimulator(n, m, r, Ne, models,
            sample_configuration=sample_configuration,
            migration_rate=migration_rate, migration_matrix=migration_matrix)
    df_msp = msp.run(num_replicates)
    stats = ["t", "num_trees", "re_events", "ca_events"]
    for j in range(len(sample_configuration)**2):
        stats.append("mig_events_{}".format(j))
    for stat in stats:
        v1 = df_ms[stat]
        v2 = df_msp[stat]
        # pyplot.hist(v1, 20, alpha=0.5, label="ms")
        # pyplot.hist(v2, 20, alpha=0.5, label="msp")
        # pyplot.legend(loc="upper left")
        sm.graphics.qqplot(v1)
        sm.qqplot_2samples(v1, v2, line="45")
        f = "{0}_{1}.png".format(output_prefix, stat)
        pyplot.savefig(f, dpi=72)
        pyplot.clf()
        # pyplot.hist(v1, 20, alpha=0.5, label="ms")
        # pyplot.legend(loc="upper left")
        # f = "{0}_{1}_1.png".format(output_prefix, stat)
        # pyplot.savefig(f, dpi=72)
        # pyplot.clf()
        # pyplot.hist(v2, 20, alpha=0.5, label="msp")
        # pyplot.legend(loc="upper left")
        # f = "{0}_{1}_2.png".format(output_prefix, stat)
        # pyplot.savefig(f, dpi=72)
        # pyplot.clf()


def verify_random(k):

    random.seed(k)
    for j in range(k):
        n = random.randint(2, 100)
        m = random.randint(1, 10000)
        Ne = random.uniform(100, 1e4)
        r = random.uniform(1e-9, 1e-6)
        theta = random.uniform(1, 100)
        num_replicates = 1000
        output_prefix = "tmp__NOBACKUP__/random_{0}".format(j)
        models = []
        t = 0
        for j in range(random.randint(0, 10)):
            t += random.uniform(0, 0.3)
            p = random.uniform(0.1, 2.0)
            if random.random() < 0.5:
                mod = msprime.ConstantPopulationModel(t, p)
            else:
                mod = msprime.ExponentialPopulationModel(t, p)
            models.append(mod)
            print(mod.get_ll_model())
        print("running for", n, m, Ne, r, 4 * Ne * r)
        run_verify_coalescent(n, m, Ne, r, models, num_replicates, output_prefix)
        run_verify_mutations(n, m, Ne, r, models, num_replicates, theta, output_prefix)

def verify_exponential_models():
    random.seed(4)
    n = 15
    m = 4550
    Ne = 7730.75967602
    r = 7.05807713707e-07
    num_replicates = 10000
    output_prefix = "tmp__NOBACKUP__/expo_models"
    models = []
    t = 0.0
    for j in range(3):
        t += 0.1
        p = 100 * t
        mod = msprime.ExponentialPopulationModel(t, p)
        models.append(mod)
        print(mod.get_ll_model())
    # params = [(0.05, 0.1), (0.1, 0.2), (0.11, 1000), (0.15, 0.0001)]
    # models = [msprime.ConstantPopulationModel(t, p) for t, p in params]
    print("running for", n, m, Ne, r, 4 * Ne * r)
    run_verify_coalescent(n, m, Ne, r, models, num_replicates, output_prefix)

def verify_scrm_example():
    # -eN 0.3 0.5 -eG .3 7.0
    num_replicates = 10000
    models = [
            msprime.ConstantPopulationModel(0.3, 0.5),
            msprime.ExponentialPopulationModel(0.3, 7.0)]
    output_prefix = "tmp__NOBACKUP__/scrm"
    run_verify_coalescent(5, 1, 1, 0, models, num_replicates, output_prefix)

def verify_zero_growth_example():
    num_replicates = 10000
    models = [
            msprime.ExponentialPopulationModel(0.0, 6.93),
            msprime.ExponentialPopulationModel(0.2, 0.0),
            msprime.ConstantPopulationModel(0.3, 0.5)]
    output_prefix = "tmp__NOBACKUP__/zero"
    run_verify_coalescent(5, 1, 1, 0, models, num_replicates, output_prefix)

def verify_simple():
    # default to humanish recombination rates and population sizes.
    n = 400
    m = 100000
    Ne = 1e4
    r = 1e-8
    num_replicates = 1000
    models = [
            msprime.ConstantPopulationModel(0.1, 2.0),
            msprime.ConstantPopulationModel(0.4, 0.5),
            msprime.ExponentialPopulationModel(0.5, 1.0)]
    output_prefix = "tmp__NOBACKUP__/simple_coalescent"
    run_verify_coalescent(n, m, Ne, r, models, num_replicates, output_prefix)
    output_prefix = "tmp__NOBACKUP__/simple_mutations"
    run_verify_mutations(n, m, Ne, r, models, num_replicates, 10, output_prefix)

def verify_recombination_events():
    """
    Simple check to see if the expected number of recombination events
    is correct for large simulations.
    """
    n = 10000
    Ne = 10**4
    r = 1e-8
    num_replicates = 10
    for k in range(1, 21):
        m = k * 10**7
        msp = MsprimeSimulator(n, m, r, Ne, [])
        df = msp.run(num_replicates)
        R = get_scaled_recombination_rate(Ne, m, r)
        expected = R * harmonic_number(n - 1)
        print(m, df["num_trees"].mean(), expected, sep="\t")

def verify_mutations():

    n = 9
    m = 7165
    Ne = 3717
    r = 5.05e-07
    theta = 100
    num_replicates = 1000
    output_prefix = "tmp__NOBACKUP__/mutations"
    models = []
    run_verify_mutations(n, m, Ne, r, models, num_replicates, theta, output_prefix)
    run_verify_coalescent(n, m, Ne, r, models, num_replicates, output_prefix)

def sample_stats(executable, sample_size, num_replicates, options):
    args = executable + [str(sample_size), str(num_replicates)] + options.split()
    print(" ".join(args))
    p1 = subprocess.Popen(args, stdout=subprocess.PIPE)
    p2 = subprocess.Popen(["./data/ms/sample_stats"], stdin=p1.stdout,
            stdout=subprocess.PIPE)
    p1.stdout.close()
    output = p2.communicate()[0]
    with tempfile.TemporaryFile() as f:
        f.write(output)
        f.seek(0)
        df = pd.read_table(f)
    return df


def get_ms_haplotypes(
        prefix, sample_size, mutation_rate, sample_configuration,
        migration_rate, migration_options=""):
    cmd = prefix + [
        str(sample_size), "1", "-t", str(mutation_rate),
        "-I", str(len(sample_configuration))]
    cmd += [str(s) for s in sample_configuration]
    cmd += [str(migration_rate)] + migration_options.split()

    output = subprocess.check_output(cmd)
    haplotypes_started = 0
    haplotypes = [[] for _ in sample_configuration]
    pop_id = 0
    for line in output.splitlines():
        if line.startswith(b'//'):
            haplotypes_started = True
        if haplotypes_started and (
                line.startswith(b'0') or line.startswith(b'1')):
            if len(haplotypes[pop_id]) == sample_configuration[pop_id]:
                pop_id += 1
            haplotypes[pop_id].append(line)
            s = len(line)

    ret = []
    for pop in haplotypes:
        shape = (len(pop), s)
        h_array = np.zeros(shape, dtype='u1')
        for j, h in enumerate(pop):
            h_array[j] = np.fromstring(h, np.uint8) - ord('0')
        a_array = allel.HaplotypeArray(h_array.T)
        ret.append(a_array)
    return ret


def verify_migration():
    # -I 3 10 4 1 5.0
    samples = get_ms_haplotypes(["./data/ms/ms"], 15, 2.0, [10, 4, 1], 5.0)
    # get_ms_haplotypes(["python", "mspms_dev.py"], 15, 2.0, [1, 13, 1], 5.0)
    # TODO Finish this: we need to get data into this somehow and figure out
    # how to calculate various statistics. These can be used to compare the
    # output of ms and msprime.
    for pop in samples:
        print(pop)
        pi = allel.stats.diversity.mean_pairwise_difference(pop.count_alleles())
        print(pi)




def verify_migration_example():
    output_prefix = "tmp__NOBACKUP__/migration_"
    n = 15
    num_replicates = 10000
    # options = "-t 2.0 -I 4 10 4 1 0 5.0"
    options = "-t 2.0 -I 3 10 4 1 5.0 -m 1 2 100.0 -m 2 1 90.0"
    df_ms = sample_stats(["./data/ms/ms"], n, num_replicates, options)
    # df_msp = sample_stats(["./data/ms/ms"], n, num_replicates, options)
    df_msp = sample_stats(["python", "mspms_dev.py"], n, num_replicates, options)
    for stat in ["pi", "ss", "D", "thetaH", "H"]:
        v1 = df_ms[stat]
        v2 = df_msp[stat]
        # pyplot.hist(v1, 20, alpha=0.5, label="ms")
        # pyplot.hist(v2, 20, alpha=0.5, label="msp")
        # pyplot.legend(loc="upper left")
        sm.graphics.qqplot(v1)
        sm.qqplot_2samples(v1, v2, line="45")
        f = "{0}_{1}.png".format(output_prefix, stat)
        pyplot.savefig(f, dpi=72)
        pyplot.clf()

def verify_migration_new():
    n = 15
    # -I 3 10 4 1 5.0
    num_replicates = 10000
    models = []
    output_prefix = "tmp__NOBACKUP__/migration_coalescent"
    run_verify_coalescent(
        n, 1, 1, 0, [], num_replicates, output_prefix,
        sample_configuration=[10, 4, 1], migration_rate=5.0)

    #ms 15 1000 -t 10.0 -I 3 10 4 1 -ma x 1.0 2.0 3.0 x 4.0 5.0 6.0 x
    output_prefix = "tmp__NOBACKUP__/migration_matrix_coalescent"
    migration_matrix = [
        [0, 1, 2],
        [2, 0, 4],
        [5, 6, 0]]
    run_verify_coalescent(
        n, 1, 1, 0, [], num_replicates, output_prefix,
        sample_configuration=[10, 4, 1],
        migration_matrix=migration_matrix)

    n = 100
    num_replicates = 1000
    output_prefix = "tmp__NOBACKUP__/high_migration_coalescent"
    run_verify_coalescent(
        n, 1, 1, 0, [], num_replicates, output_prefix,
        sample_configuration=[10, 90, 0],
        migration_matrix=[[0.0, 100, 0], [0, 0, 150], [200, 0, 0]])



def main():
    # verify_recombination_events()
    # verify_random(10)
    # verify_exponential_models()
    # verify_simple()
    verify_migration_new()
    # verify_zero_growth_example()
    # verify_scrm_example()
    # verify_mutations()
    # verify_migration_example()
    # verify_migration()


def verify_human_demographics():
    """
    Model: 1e6 now, increasing from 2e4 400 generations ago
    (12kya), then 2e3 2000 generations ago (60kya) then 2e4 again fixed size
    beyond that.
    """
    n = 100
    m = 500000
    r = 1e-8
    num_replicates = 10000
    # Calculate the models
    N0 = 1e6
    N1 = 2e4
    N2 = 2e3
    N3 = 2e4
    g1 = 400
    g2 = 2000
    t1 = g1 / (4 * N0)
    t2 = g2 / (4 * N0)
    # Calculate the growth rates.
    alpha1 = -math.log(N1 / N0) / t1
    alpha2 = -math.log(N2 / N1) / (t2 - t1)
    # print(t1, t2)
    # print(alpha1, N0 * math.exp(- alpha1 * t1))
    # print(alpha2, N1 * math.exp(- alpha2 * (t2 - t1)))
    # num_replicates = 1
    models = [
            msprime.ExponentialPopulationModel(0, alpha1),
            msprime.ExponentialPopulationModel(t1, alpha2),
            msprime.ConstantPopulationModel(t2, N3 / N0),
            ]
    output_prefix = "tmp__NOBACKUP__/simple"
    run_verify_coalescent(n, m, N0, r, models, num_replicates, output_prefix)

if __name__ == "__main__":
    main()
    # verify_human_demographics()
