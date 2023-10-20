import torch
#################### code changes ####################  # noqa F401
import intel_extension_for_pytorch as ipex
######################################################  # noqa F401
import argparse
from transformers import (
    AutoConfig,
    AutoModelForCausalLM,
    AutoTokenizer,
)

# args
parser = argparse.ArgumentParser("Generation script (weight only quantization path)", add_help=False)
parser.add_argument(
    "--dtype",
    type=str,
    choices=["float32", "bfloat16"],
    default="float32",
    help="choose the weight dtype and whether to enable auto mixed precision or not",
)
parser.add_argument(
    "--max-new-tokens", default=32, type=int, help="output max new tokens"
)
parser.add_argument(
    "--prompt", default="What are we having for dinner?", type=str, help="input prompt"
)
parser.add_argument("--greedy", action="store_true")
parser.add_argument("--batch-size", default=1, type=int, help="batch size")
# Intel(R) Extension for PyTorch*
#################### code changes ####################  # noqa F401
parser.add_argument(
    "--lowp-mode",
    choices=["AUTO", "BF16", "FP32", "INT8", "FP16"],
    default="AUTO",
    type=str,
    help="low precision mode for weight only quantization. "
    "It indicates data type for computation for speedup at the cost "
    "of accuracy. Unrelated to activation or weight data type."
    "It is not supported yet to use lowp_mode=INT8 for INT8 weight, "
    "falling back to lowp_mode=BF16 implicitly in this case."
    "If set to AUTO, lowp_mode is determined by weight data type: "
    "lowp_mode=BF16 is used for INT8 weight "
    "and lowp_mode=INT8 used for INT4 weight",
)
parser.add_argument(
    "--weight-dtype",
    choices=["INT8", "INT4"],
    default="INT8",
    type=str,
    help="weight data type for weight only quantization. Unrelated to activation"
    " data type or lowp-mode. If `--low-precision-checkpoint` is given, weight"
    " data type is always INT4 and this argument is not needed.",
)
parser.add_argument(
    "--low-precision-checkpoint",
    default="",
    type=str,
    help="Low precision checkpoint file generated by calibration, such as GPTQ. It contains"
    " modified weights, scales, zero points, etc. For better accuracy of weight only"
    " quantization with INT4 weight.",
)
######################################################  # noqa F401
args = parser.parse_args()
print(args)

# dtype
amp_enabled = True if args.dtype != "float32" else False
amp_dtype = getattr(torch, args.dtype)

# load model
model_id = "facebook/opt-125m"
config = AutoConfig.from_pretrained(
    model_id, torchscript=True, trust_remote_code=True
)
model = AutoModelForCausalLM.from_pretrained(
    model_id,
    torch_dtype=amp_dtype,
    config=config,
    low_cpu_mem_usage=True,
    trust_remote_code=True,
)
tokenizer = AutoTokenizer.from_pretrained(
    model_id,
    trust_remote_code=True
)
model = model.eval()
model = model.to(memory_format=torch.channels_last)

# Intel(R) Extension for PyTorch*
#################### code changes ####################  # noqa F401
weight_dtype = torch.quint4x2 if args.weight_dtype == "INT4" else torch.qint8

if args.lowp_mode == "INT8":
    lowp_mode = ipex.quantization.WoqLowpMode.INT8
elif args.lowp_mode == "FP32":
    lowp_mode = ipex.quantization.WoqLowpMode.NONE
elif args.lowp_mode == "FP16":
    lowp_mode = ipex.quantization.WoqLowpMode.FP16
elif args.lowp_mode == "BF16":
    lowp_mode = ipex.quantization.WoqLowpMode.BF16
else:  # AUTO
    if args.low_precision_checkpoint != "" or weight_dtype == torch.quint4x2:
        lowp_mode = ipex.quantization.WoqLowpMode.INT8
    else:
        lowp_mode = ipex.quantization.WoqLowpMode.BF16

qconfig = ipex.quantization.get_weight_only_quant_qconfig_mapping(
    weight_dtype=weight_dtype, lowp_mode=lowp_mode
)
if args.low_precision_checkpoint != "":
    low_precision_checkpoint = torch.load(args.low_precision_checkpoint)
else:
    low_precision_checkpoint = None
model = ipex.optimize_transformers(
    model.eval(),
    dtype=amp_dtype,
    quantization_config=qconfig,
    low_precision_checkpoint=low_precision_checkpoint,
    deployment_mode=True,
    inplace=True,
)

######################################################  # noqa F401

# generate args
num_beams = 1 if args.greedy else 4
generate_kwargs = dict(do_sample=False, temperature=0.9, num_beams=num_beams)

# input prompt
prompt = args.prompt
input_size = tokenizer(prompt, return_tensors="pt").input_ids.size(dim=1)
print("---- Prompt size:", input_size)
prompt = [prompt] * args.batch_size

# inference
with torch.no_grad(), torch.inference_mode(), torch.cpu.amp.autocast(enabled=amp_enabled):
    input_ids = tokenizer(prompt, return_tensors="pt").input_ids
    gen_ids = model.generate(
        input_ids,
        max_new_tokens=args.max_new_tokens,
        **generate_kwargs
    )
    gen_text = tokenizer.batch_decode(gen_ids, skip_special_tokens=True)
    input_tokens_lengths = [x.shape[0] for x in input_ids]
    output_tokens_lengths = [x.shape[0] for x in gen_ids]
    total_new_tokens = [
        o - i for i, o in zip(input_tokens_lengths, output_tokens_lengths)
    ]
    print(gen_text, total_new_tokens, flush=True)
