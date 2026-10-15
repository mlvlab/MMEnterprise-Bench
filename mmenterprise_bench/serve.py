"""Launch a local reader with vLLM (``mmeb serve``)."""
import argparse
import importlib.metadata
import json
import os
from pathlib import Path
import re
import shlex
import sys


def build_command(alias, gpus, port, host="127.0.0.1", tensor_parallel_size=None):
    models = json.loads((Path(__file__).resolve().parent / "configs/models.json").read_text(encoding="utf-8"))
    if alias not in models:
        raise ValueError("Unknown model alias: " + alias)
    spec = models[alias]
    if spec["provider"] != "local":
        raise ValueError("Select a local reader model")
    devices = gpus.split(",")
    size = tensor_parallel_size or spec["tensor_parallel_size"]
    if len(devices) != size or len(set(devices)) != size or not all(re.fullmatch(r"\d+", x) for x in devices):
        raise ValueError("The GPU count must equal tensor parallel size")
    if not 1 <= port <= 65535:
        raise ValueError("Invalid port")
    qwen = alias.startswith("qwen")
    command = [sys.executable, "-m", "vllm.entrypoints.openai.api_server",
               "--model", spec["model_id"], "--revision", spec["checkpoint_revision"],
               "--served-model-name", spec["model_id"], "--host", host, "--port", str(port),
               "--dtype", "bfloat16", "--tensor-parallel-size", str(size),
               "--max-model-len", str(spec["context_length"]),
               "--limit-mm-per-prompt", json.dumps({"image": spec["max_images"]}),
               "--gpu-memory-utilization", "0.80" if qwen else "0.90",
               "--max-num-seqs", "4" if qwen else "1",
               "--max-num-batched-tokens", "8192" if qwen else "2048",
               "--enable-chunked-prefill", "--no-enable-prefix-caching", "--mm-processor-cache-gb", "0",
               "--no-async-scheduling", "--enforce-eager", "--trust-remote-code", "--skip-mm-profiling"]
    if qwen:
        command += ["--default-chat-template-kwargs", '{"enable_thinking":false}']
    return command


def add_arguments(parser):
    parser.add_argument("--model", required=True, help="Local reader alias from configs/models.json (qwen27b, qwen9b, qwen4b, qwen2b, internvl8b, kimivl16b)")
    parser.add_argument("--gpus", required=True, help="Device IDs, for example 0 or 0,1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--tensor-parallel-size", type=int)
    parser.add_argument("--print-command", action="store_true", help="Print the vLLM launch command instead of running it")
    return parser


def run(args):
    command = build_command(args.model, args.gpus, args.port, args.host, args.tensor_parallel_size)
    if args.print_command:
        print("CUDA_VISIBLE_DEVICES=" + shlex.quote(args.gpus) + " " + shlex.join(command))
        return 0
    try:
        version = importlib.metadata.version("vllm")
    except importlib.metadata.PackageNotFoundError:
        version = None
    if version != "0.24.0":
        raise SystemExit("Install vllm==0.24.0 in the serving environment (pip install 'mmenterprise-bench[local]')")
    environment = os.environ.copy()
    environment.update(CUDA_VISIBLE_DEVICES=args.gpus, VLLM_USE_FLASHINFER_SAMPLER="0", OMP_NUM_THREADS="4")
    os.execvpe(command[0], command, environment)


def main(argv=None):
    parser = add_arguments(argparse.ArgumentParser(description=__doc__))
    return run(parser.parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
