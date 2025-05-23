# noqa: F811, E402
import argparse
import json
import os
import time
from os import path

# from line_profiler import profile

from utils.io import check_path_exists, check_path_not_exists, problems_from_densities, write_result, Formatter
from utils.run import get_integrators, get_wmi_id, compute_wmi, run_fn_with_timeout, WMIResult

# add parent directory to import path
import sys
module_path = os.path.abspath(os.path.join('.'))
if module_path not in sys.path:
    sys.path.append(module_path)

from wmipa import WMI
from wmipa.integration.cache_integrator import CacheIntegrator
from wmipa.integration.volesti_integrator import VolestiIntegrator


def get_input_files(input_dir):
    files = []
    for root, dirs, filenames in os.walk(input_dir):
        for filename in filenames:
            if filename.endswith(".json"):
                files.append(path.join(root, filename))
    return files


def get_output_filename(output_dir, output_filename, wmi_id, run_id, output_suffix):
    return path.join(output_dir, f"{output_filename}_{wmi_id}_{run_id}{output_suffix}.json")


def initialize_output_files(args, output_prefix, run_id, output_suffix):
    """Initializes output files for each mode and integrator.

    Args:
        args (Namespace): command line arguments
        output_prefix (str): prefix of the output filename
        run_id (int): Run ID
        output_suffix (str): suffix of the output filename

    Returns:s
        A dictionary mapping each wmi_id (identifying a pair <mode, integrator>) to the corresponding output file

    """

    output_files = {}
    args.total_degree = 1
    args.variable_map = {"x":0}
    for integrator in get_integrators(args):
        wmi_id = get_wmi_id(args.mode, integrator)
        output_filename = get_output_filename(args.output, output_prefix, wmi_id, run_id, output_suffix)
        check_path_not_exists(output_filename)
        with open(output_filename, "w") as output_file:
            integrator_json = integrator.to_json() if integrator is not None else None
            skeleton = {
                "wmi_id": wmi_id,
                "mode": args.mode,
                "integrator": integrator_json,
                "results": []
            }
            json.dump(skeleton, output_file)
        output_files[wmi_id] = output_filename
    return output_files


def run_wmi_with_timeout(args, domain, support, weight):
    return run_fn_with_timeout(compute_wmi, args.timeout, args, domain, support, weight)


def run_wmi_without_timeout(args, domain, support, weight):
    return compute_wmi(args, domain, support, weight)


def parse_args():
    modes = WMI.MODES + ["XADD", "XSDD", "FXSDD", "Rejection"]

    parser = argparse.ArgumentParser(
        description="Compute WMI on models",
        formatter_class=Formatter,
    )
    parser.add_argument("input", help="Folder with .json files")
    parser.add_argument("-o", "--output", default=os.getcwd(),
                        help="Output folder where to save the results")
    parser.add_argument("-f", "--filename", help="Suffix for the result file", default="")
    parser.add_argument("--timeout", type=int, default=3600, help="Max time (in seconds)")
    parser.add_argument("-m", "--mode", choices=modes, required=True, help="Mode to use")
    parser.add_argument("--n-threads", default=CacheIntegrator.DEF_N_THREADS, type=int,
                        help="Number of threads to use for WMIPA")
    parser.add_argument("-c", "--cache", type=int, choices=[-1, 0, 1, 2, 3], default=-1,
                        help="Cache level for WMIPA methods")
    parser.add_argument("-t", "--stub", action="store_true",
                        help="Set this flag if you only want to count the number of integrals to be computed")
    parser.add_argument("--unweighted", action="store_true",
                        help="Set this flag if you want to compute the (unweighted) model integration, "
                             "i.e., to use 1 as weight")

    integration_parsers = parser.add_subparsers(title="integrator", description="Integrator to use for WMIPA methods",
                                                dest="integrator")
    latte_parser = integration_parsers.add_parser("latte", formatter_class=Formatter)
    symbolic_parser = integration_parsers.add_parser("symbolic", formatter_class=Formatter)
    volesti_parser = integration_parsers.add_parser("volesti", formatter_class=Formatter)
    volesti_parser.add_argument("-e", "--error", default=0.1, type=float,
                                help="Relative error for the volume computation [in (0, 1)]")
    volesti_parser.add_argument("--algorithm", choices=VolestiIntegrator.ALGORITHMS,
                                default=VolestiIntegrator.DEF_ALGORITHM,
                                help=f"Volume computation method: {', '.join(VolestiIntegrator.ALGORITHMS)}")
    volesti_parser.add_argument("--walk_type", choices=VolestiIntegrator.RANDOM_WALKS,
                                default=VolestiIntegrator.DEF_RANDOM_WALK,
                                help="Type of random walk: {', '.join(VolestiIntegrator.RANDOM_WALKS)}")
    volesti_parser.add_argument("-N", type=int, default=VolestiIntegrator.DEF_N, help="Number of samples")
    volesti_parser.add_argument("--walk_length", type=int, default=VolestiIntegrator.DEF_WALK_LENGTH,
                                help="Length of random walk (0 for default value)")
    volesti_parser.add_argument("--seed", type=int, default=666,
                                help="Random seed for (the first instance of) VolEsti integrator")
    volesti_parser.add_argument("--n-seeds", type=int, default=1,
                                help="Number of seeds to use. A list of VolEsti integrator will be used "
                                     "with seeds [seed, seed + 1, ..., seed + n_seeds - 1]")
    torch_parser = integration_parsers.add_parser("torch", formatter_class=Formatter)
    torch_parser.add_argument("--monomials_use_float64", action="store_true", help="Use float64 instead of float32 for monomials")
    torch_parser.add_argument("--sum_seperately", action="store_true", help="Sum the integrals of the simplices separately")
    torch_parser.add_argument("--with_sorting", action="store_true", help="Sort the simplices before integration")
    print()
    parser.epilog = (
        f"""See more options for each integrator with:
\t{latte_parser.format_usage()}
\t{symbolic_parser.format_usage()}
\t{volesti_parser.format_usage()}
\t{torch_parser.format_usage()}
""")

    return parser.parse_args()


# @profile
def main():
    
    args = parse_args()
    if args.integrator == "torch":
        import torch.multiprocessing as mp
        mp.set_start_method('forkserver')

    output_suffix = args.filename
    check_path_exists(args.input)
    check_path_exists(args.output)

    output_prefix = "_".join(args.input.rstrip("/").split("/"))
    print("Output prefix:", output_prefix)
    run_id = int(time.time())

    files = get_input_files(args.input)

    output_files = initialize_output_files(args, output_prefix, run_id, output_suffix)

    print(f"Started computing. RunID: {run_id}, args:\n{args}")
    print("Output files:\n\t{}".format("\n\t".join(output_files.values())))
    time_start = time.time()

    i = -1
    for i, (filename, query_n, domain, support, weight) in enumerate(problems_from_densities(files)):
        try:
            time_init = time.time()
            if args.integrator == "torch":
                results = run_wmi_without_timeout(args, domain, support, weight)
            else:
                results = run_wmi_with_timeout(args, domain, support, weight)
            time_total = time.time() - time_init
        except TimeoutError:
            results = [WMIResult(wmi_id=wmi_id, value=None, n_integrations=None, parallel_integration_time=0,
                                 sequential_integration_time=0) for wmi_id in output_files.keys()]
            time_total = args.timeout

        except Exception as err:

            # raise err

            print(f"Exception while solving '{filename}' of type {type(err)}:")
            print(err.args)
            print(err)
            print()

            results = [WMIResult(wmi_id=wmi_id, value=None, n_integrations=None, parallel_integration_time=0,
                                 sequential_integration_time=0) for wmi_id in output_files.keys()]
            time_total = args.timeout            
            
        enumeration_time = time_total - sum(result.parallel_integration_time for result in results)

        for result in results:
            effective_sequential_time = enumeration_time + result.sequential_integration_time
            effective_parallel_time = enumeration_time + result.parallel_integration_time

            result_json = {
                "filename": filename,
                "query": query_n,
                "value": result.value,
                "n_integrations": result.n_integrations,
                "sequential_integration_time": result.sequential_integration_time,
                "parallel_integration_time": result.parallel_integration_time,
                "sequential_time": effective_sequential_time,
                "parallel_time": effective_parallel_time,
            }

            output_file = output_files[result.wmi_id]
            write_result(output_file, result_json)

    print()
    print("Computed {} WMI".format(i + 1))

    seconds = time.time() - time_start
    print("Done! {:.3f}s".format(seconds))


if __name__ == "__main__":
    main()
