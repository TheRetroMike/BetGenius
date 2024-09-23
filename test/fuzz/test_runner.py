#!/usr/bin/env python3
# Copyright (c) 2019-present The Bitcoin Core developers
# Distributed under the MIT software license, see the accompanying
# file COPYING or http://www.opensource.org/licenses/mit-license.php.
"""Run fuzz test targets.
"""

from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import argparse
import configparser
import logging
import os
import random
import subprocess
import sys


def get_fuzz_env(*, target, source_dir):
    symbolizer = os.environ.get('LLVM_SYMBOLIZER_PATH', "/usr/bin/llvm-symbolizer")
    return {
        'FUZZ': target,
        'UBSAN_OPTIONS':
        f'suppressions={source_dir}/test/sanitizer_suppressions/ubsan:print_stacktrace=1:halt_on_error=1:report_error_type=1',
        'UBSAN_SYMBOLIZER_PATH':symbolizer,
        "ASAN_OPTIONS": "detect_stack_use_after_return=1:check_initialization_order=1:strict_init_order=1",
        'ASAN_SYMBOLIZER_PATH':symbolizer,
        'MSAN_SYMBOLIZER_PATH':symbolizer,
    }


def main():
    parser = argparse.ArgumentParser(
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
        description='''Run the fuzz targets with all inputs from the corpus_dir once.''',
    )
    parser.add_argument(
        "-l",
        "--loglevel",
        dest="loglevel",
        default="INFO",
        help="log events at this level and higher to the console. Can be set to DEBUG, INFO, WARNING, ERROR or CRITICAL. Passing --loglevel DEBUG will output all logs to console.",
    )
    parser.add_argument(
        '--valgrind',
        action='store_true',
        help='If true, run fuzzing binaries under the valgrind memory error detector',
    )
    parser.add_argument(
        "--empty_min_time",
        type=int,
        help="If set, run at least this long, if the existing fuzz inputs directory is empty.",
    )
    parser.add_argument(
        '-x',
        '--exclude',
        help="A comma-separated list of targets to exclude",
    )
    parser.add_argument(
        '--par',
        '-j',
        type=int,
        default=4,
        help='How many targets to merge or execute in parallel.',
    )
    parser.add_argument(
        'corpus_dir',
        help='The corpus to run on (must contain subfolders for each fuzz target).',
    )
    parser.add_argument(
        'target',
        nargs='*',
        help='The target(s) to run. Default is to run all targets.',
    )
    parser.add_argument(
        '--m_dir',
        action="append",
        help="Merge inputs from these directories into the corpus_dir.",
    )
    parser.add_argument(
        '-g',
        '--generate',
        action='store_true',
        help='Create new corpus (or extend the existing ones) by running'
             ' the given targets for a finite number of times. Outputs them to'
             ' the passed corpus_dir.'
    )

    args = parser.parse_args()
    args.corpus_dir = Path(args.corpus_dir)

    # Set up logging
    logging.basicConfig(
        format='%(message)s',
        level=int(args.loglevel) if args.loglevel.isdigit() else args.loglevel.upper(),
    )

    # Read config generated by configure.
    config = configparser.ConfigParser()
    configfile = os.path.abspath(os.path.dirname(__file__)) + "/../config.ini"
    config.read_file(open(configfile, encoding="utf8"))

    if not config["components"].getboolean("ENABLE_FUZZ_BINARY"):
        logging.error("Must have fuzz executable built")
        sys.exit(1)

    # Build list of tests
    test_list_all = parse_test_list(fuzz_bin=os.path.join(config["environment"]["BUILDDIR"], 'src', 'test', 'fuzz', 'fuzz'))

    if not test_list_all:
        logging.error("No fuzz targets found")
        sys.exit(1)

    logging.debug("{} fuzz target(s) found: {}".format(len(test_list_all), " ".join(sorted(test_list_all))))

    args.target = args.target or test_list_all  # By default run all
    test_list_error = list(set(args.target).difference(set(test_list_all)))
    if test_list_error:
        logging.error("Unknown fuzz targets selected: {}".format(test_list_error))
    test_list_selection = list(set(test_list_all).intersection(set(args.target)))
    if not test_list_selection:
        logging.error("No fuzz targets selected")
    if args.exclude:
        for excluded_target in args.exclude.split(","):
            if excluded_target not in test_list_selection:
                logging.error("Target \"{}\" not found in current target list.".format(excluded_target))
                continue
            test_list_selection.remove(excluded_target)
    test_list_selection.sort()

    logging.info("{} of {} detected fuzz target(s) selected: {}".format(len(test_list_selection), len(test_list_all), " ".join(test_list_selection)))

    if not args.generate:
        test_list_missing_corpus = []
        for t in test_list_selection:
            corpus_path = os.path.join(args.corpus_dir, t)
            if not os.path.exists(corpus_path) or len(os.listdir(corpus_path)) == 0:
                test_list_missing_corpus.append(t)
        test_list_missing_corpus.sort()
        if test_list_missing_corpus:
            logging.info(
                "Fuzzing harnesses lacking a corpus: {}".format(
                    " ".join(test_list_missing_corpus)
                )
            )
            logging.info("Please consider adding a fuzz corpus at https://github.com/betgenius-core/qa-assets")

    try:
        help_output = subprocess.run(
            args=[
                os.path.join(config["environment"]["BUILDDIR"], 'src', 'test', 'fuzz', 'fuzz'),
                '-help=1',
            ],
            env=get_fuzz_env(target=test_list_selection[0], source_dir=config['environment']['SRCDIR']),
            timeout=20,
            check=False,
            stderr=subprocess.PIPE,
            text=True,
        ).stderr
        using_libfuzzer = "libFuzzer" in help_output
        if (args.generate or args.m_dir) and not using_libfuzzer:
            logging.error("Must be built with libFuzzer")
            sys.exit(1)
    except subprocess.TimeoutExpired:
        logging.error("subprocess timed out: Currently only libFuzzer is supported")
        sys.exit(1)

    with ThreadPoolExecutor(max_workers=args.par) as fuzz_pool:
        if args.generate:
            return generate_corpus(
                fuzz_pool=fuzz_pool,
                src_dir=config['environment']['SRCDIR'],
                build_dir=config["environment"]["BUILDDIR"],
                corpus_dir=args.corpus_dir,
                targets=test_list_selection,
            )

        if args.m_dir:
            merge_inputs(
                fuzz_pool=fuzz_pool,
                corpus=args.corpus_dir,
                test_list=test_list_selection,
                src_dir=config['environment']['SRCDIR'],
                build_dir=config["environment"]["BUILDDIR"],
                merge_dirs=[Path(m_dir) for m_dir in args.m_dir],
            )
            return

        run_once(
            fuzz_pool=fuzz_pool,
            corpus=args.corpus_dir,
            test_list=test_list_selection,
            src_dir=config['environment']['SRCDIR'],
            build_dir=config["environment"]["BUILDDIR"],
            using_libfuzzer=using_libfuzzer,
            use_valgrind=args.valgrind,
            empty_min_time=args.empty_min_time,
        )


def transform_process_message_target(targets, src_dir):
    """Add a target per process message, and also keep ("process_message", {}) to allow for
    cross-pollination, or unlimited search"""

    p2p_msg_target = "process_message"
    if (p2p_msg_target, {}) in targets:
        lines = subprocess.run(
            ["git", "grep", "--function-context", "g_all_net_message_types{", src_dir / "src" / "protocol.cpp"],
            check=True,
            stdout=subprocess.PIPE,
            text=True,
        ).stdout.splitlines()
        lines = [l.split("::", 1)[1].split(",")[0].lower() for l in lines if l.startswith("src/protocol.cpp-    NetMsgType::")]
        assert len(lines)
        targets += [(p2p_msg_target, {"LIMIT_TO_MESSAGE_TYPE": m}) for m in lines]
    return targets


def transform_rpc_target(targets, src_dir):
    """Add a target per RPC command, and also keep ("rpc", {}) to allow for cross-pollination,
    or unlimited search"""

    rpc_target = "rpc"
    if (rpc_target, {}) in targets:
        lines = subprocess.run(
            ["git", "grep", "--function-context", "RPC_COMMANDS_SAFE_FOR_FUZZING{", src_dir / "src" / "test" / "fuzz" / "rpc.cpp"],
            check=True,
            stdout=subprocess.PIPE,
            text=True,
        ).stdout.splitlines()
        lines = [l.split("\"", 1)[1].split("\"")[0] for l in lines if l.startswith("src/test/fuzz/rpc.cpp-    \"")]
        assert len(lines)
        targets += [(rpc_target, {"LIMIT_TO_RPC_COMMAND": r}) for r in lines]
    return targets


def generate_corpus(*, fuzz_pool, src_dir, build_dir, corpus_dir, targets):
    """Generates new corpus.

    Run {targets} without input, and outputs the generated corpus to
    {corpus_dir}.
    """
    logging.info("Generating corpus to {}".format(corpus_dir))
    targets = [(t, {}) for t in targets]  # expand to add dictionary for target-specific env variables
    targets = transform_process_message_target(targets, Path(src_dir))
    targets = transform_rpc_target(targets, Path(src_dir))

    def job(command, t, t_env):
        logging.debug(f"Running '{command}'")
        logging.debug("Command '{}' output:\n'{}'\n".format(
            command,
            subprocess.run(
                command,
                env={
                    **t_env,
                    **get_fuzz_env(target=t, source_dir=src_dir),
                },
                check=True,
                stderr=subprocess.PIPE,
                text=True,
            ).stderr,
        ))

    futures = []
    for target, t_env in targets:
        target_corpus_dir = corpus_dir / target
        os.makedirs(target_corpus_dir, exist_ok=True)
        use_value_profile = int(random.random() < .3)
        command = [
            os.path.join(build_dir, 'src', 'test', 'fuzz', 'fuzz'),
            "-rss_limit_mb=8000",
            "-max_total_time=6000",
            "-reload=0",
            f"-use_value_profile={use_value_profile}",
            target_corpus_dir,
        ]
        futures.append(fuzz_pool.submit(job, command, target, t_env))

    for future in as_completed(futures):
        future.result()


def merge_inputs(*, fuzz_pool, corpus, test_list, src_dir, build_dir, merge_dirs):
    logging.info(f"Merge the inputs from the passed dir into the corpus_dir. Passed dirs {merge_dirs}")
    jobs = []
    for t in test_list:
        args = [
            os.path.join(build_dir, 'src', 'test', 'fuzz', 'fuzz'),
            '-rss_limit_mb=8000',
            '-set_cover_merge=1',
            # set_cover_merge is used instead of -merge=1 to reduce the overall
            # size of the qa-assets git repository a bit, but more importantly,
            # to cut the runtime to iterate over all fuzz inputs [0].
            # [0] https://github.com/betgenius-core/qa-assets/issues/130#issuecomment-1761760866
            '-shuffle=0',
            '-prefer_small=1',
            '-use_value_profile=0',
            # use_value_profile is enabled by oss-fuzz [0], but disabled for
            # now to avoid bloating the qa-assets git repository [1].
            # [0] https://github.com/google/oss-fuzz/issues/1406#issuecomment-387790487
            # [1] https://github.com/betgenius-core/qa-assets/issues/130#issuecomment-1749075891
            os.path.join(corpus, t),
        ] + [str(m_dir / t) for m_dir in merge_dirs]
        os.makedirs(os.path.join(corpus, t), exist_ok=True)
        for m_dir in merge_dirs:
            (m_dir / t).mkdir(exist_ok=True)

        def job(t, args):
            output = 'Run {} with args {}\n'.format(t, " ".join(args))
            output += subprocess.run(
                args,
                env=get_fuzz_env(target=t, source_dir=src_dir),
                check=True,
                stderr=subprocess.PIPE,
                text=True,
            ).stderr
            logging.debug(output)

        jobs.append(fuzz_pool.submit(job, t, args))

    for future in as_completed(jobs):
        future.result()


def run_once(*, fuzz_pool, corpus, test_list, src_dir, build_dir, using_libfuzzer, use_valgrind, empty_min_time):
    jobs = []
    for t in test_list:
        corpus_path = corpus / t
        os.makedirs(corpus_path, exist_ok=True)
        args = [
            os.path.join(build_dir, 'src', 'test', 'fuzz', 'fuzz'),
        ]
        empty_dir = not any(corpus_path.iterdir())
        if using_libfuzzer:
            if empty_min_time and empty_dir:
                args += [f"-max_total_time={empty_min_time}"]
            else:
                args += [
                    "-runs=1",
                    corpus_path,
                ]
        else:
            args += [corpus_path]
        if use_valgrind:
            args = ['valgrind', '--quiet', '--error-exitcode=1'] + args

        def job(t, args):
            output = 'Run {} with args {}'.format(t, args)
            result = subprocess.run(
                args,
                env=get_fuzz_env(target=t, source_dir=src_dir),
                stderr=subprocess.PIPE,
                text=True,
            )
            output += result.stderr
            return output, result, t

        jobs.append(fuzz_pool.submit(job, t, args))

    stats = []
    for future in as_completed(jobs):
        output, result, target = future.result()
        logging.debug(output)
        if using_libfuzzer:
            done_stat = [l for l in output.splitlines() if "DONE" in l]
            assert len(done_stat) == 1
            stats.append((target, done_stat[0]))
        try:
            result.check_returncode()
        except subprocess.CalledProcessError as e:
            if e.stdout:
                logging.info(e.stdout)
            if e.stderr:
                logging.info(e.stderr)
            logging.info(f"Target {result.args} failed with exit code {e.returncode}")
            sys.exit(1)

    if using_libfuzzer:
        print("Summary:")
        max_len = max(len(t[0]) for t in stats)
        for t, s in sorted(stats):
            t = t.ljust(max_len + 1)
            print(f"{t}{s}")


def parse_test_list(*, fuzz_bin):
    test_list_all = subprocess.run(
        fuzz_bin,
        env={
            'PRINT_ALL_FUZZ_TARGETS_AND_ABORT': ''
        },
        stdout=subprocess.PIPE,
        text=True,
        check=True,
    ).stdout.splitlines()
    return test_list_all


if __name__ == '__main__':
    main()
