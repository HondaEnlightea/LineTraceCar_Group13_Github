# coding: UTF-8
"""
ロータリーエンコーダのCSVから左右の車輪の回転数グラフを作成します。

実行例（EicDesignLabフォルダーから）:
    python dev13\\plot_rotary_encoder.py rotary_encoder_20261028_143000.csv
    python dev13\\plot_rotary_encoder.py rotary_encoder_20261028_143000.csv --output rpm.png

PNGは既定でCSVと同じフォルダーに、CSV名_plot.pngとして保存します。
"""

import argparse
import csv
import math
from pathlib import Path

import matplotlib.pyplot as plt


REQUIRED_COLUMNS = ('time_s', 'rpm_left', 'rpm_right')

# Japanese labels are rendered using the Windows Japanese font installed on the target PC.
plt.rcParams['font.family'] = 'Meiryo'
plt.rcParams['axes.unicode_minus'] = False


def read_measurements(csv_path):
    """計測CSVから時間と左右のRPMを読み込む。"""
    times = []
    rpm_left = []
    rpm_right = []

    with csv_path.open('r', newline='', encoding='utf-8-sig') as csv_file:
        reader = csv.DictReader(csv_file)
        if reader.fieldnames is None:
            raise ValueError('CSVに見出し行がありません')

        missing = [name for name in REQUIRED_COLUMNS if name not in reader.fieldnames]
        if missing:
            raise ValueError(
                'CSVに必要な列がありません: {}（必要な列: {}）'.format(
                    ', '.join(missing), ', '.join(REQUIRED_COLUMNS)
                )
            )

        for line_number, row in enumerate(reader, start=2):
            try:
                values = [float(row[name]) for name in REQUIRED_COLUMNS]
            except (TypeError, ValueError):
                raise ValueError(
                    '{}行目の時間または回転数が数値ではありません'.format(line_number)
                ) from None
            if not all(math.isfinite(value) for value in values):
                raise ValueError(
                    '{}行目に有限でない数値があります'.format(line_number)
                )

            time_s, left_rpm, right_rpm = values
            times.append(time_s)
            rpm_left.append(left_rpm)
            rpm_right.append(right_rpm)

    if not times:
        raise ValueError('CSVに計測データがありません')
    return times, rpm_left, rpm_right


def main():
    parser = argparse.ArgumentParser(
        description='ロータリーエンコーダのCSVから左右のRPMグラフをPNGで作成します'
    )
    parser.add_argument('csv_file', type=Path, help='計測CSVファイル')
    parser.add_argument(
        '-o',
        '--output',
        type=Path,
        help='出力PNGファイル（省略時はCSVと同じフォルダーに保存）',
    )
    args = parser.parse_args()

    if not args.csv_file.is_file():
        parser.error('CSVファイルが見つかりません: {}'.format(args.csv_file))

    try:
        times, rpm_left, rpm_right = read_measurements(args.csv_file)
    except (OSError, ValueError) as error:
        parser.error('CSVを読み込めません: {}'.format(error))

    output_path = args.output
    if output_path is None:
        output_path = args.csv_file.with_name(args.csv_file.stem + '_plot.png')

    figure, axis = plt.subplots(figsize=(10, 5))
    axis.plot(times, rpm_right, color='tab:blue', label='右車輪')
    axis.plot(times, rpm_left, color='tab:orange', label='左車輪')
    axis.set_xlabel('時間 [s]')
    axis.set_ylabel('車輪の回転数 [rpm]')
    axis.grid(True)
    axis.legend(title=' {}'.format(args.csv_file.name))
    figure.tight_layout()
    figure.savefig(output_path, dpi=150)
    plt.close(figure)
    print('{} にグラフを保存しました'.format(output_path))


if __name__ == '__main__':
    main()
