"""Convert a PA sweep CSV to MDIF without import-time file access.

Usage: ``python -m format_convert.csv_to_MDIF input.csv output.mdf``
"""

from __future__ import annotations

import argparse
from pathlib import Path


REQUIRED_COLUMNS = (
    "input_power_dut",
    "output_power_dut",
    "gain",
    "dc_power",
    "efficiency",
    "frequency_ghz",
)


def convert_csv_to_mdif(input_path: Path, output_path: Path) -> Path:
    """Convert *input_path* and return the written *output_path*."""
    import pandas as pd

    frame = pd.read_csv(input_path, sep=",", encoding="utf-8", engine="python")
    missing = [column for column in REQUIRED_COLUMNS if column not in frame.columns]
    if missing:
        raise ValueError("CSV 缺少列: " + ", ".join(missing))

    frequencies = sorted(frame["frequency_ghz"].unique())
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as target:
        target.write("! PA measurement data converted from CSV\n\n")
        for frequency in frequencies:
            subset = frame[frame["frequency_ghz"] == frequency].sort_values("input_power_dut")
            target.write(f"VAR frequency_ghz(real) = {frequency}\nBEGIN AMP_DATA\n")
            target.write("% input_power_dut(real) output_power_dut(real) gain(real) dc_power(real) efficiency(real)\n")
            for _, row in subset.iterrows():
                target.write(
                    f"{row['input_power_dut']:.3f} {row['output_power_dut']:.3f} "
                    f"{row['gain']:.2f} {row['dc_power']:.8f} {row['efficiency']:.9f}\n"
                )
            target.write("END\n\n")
    return output_path


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="将 PA 扫描 CSV 转换为 MDIF")
    parser.add_argument("input", type=Path, help="输入 CSV")
    parser.add_argument("output", type=Path, help="输出 MDIF")
    args = parser.parse_args(argv)
    convert_csv_to_mdif(args.input, args.output)
    print(f"转换完成: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
