# coding: UTF-8
"""
ライントレーサのCSVからセンサーの値とモーター指令値のグラフを作成します。
メインプログラムから呼び出して自動作成するほか、保存済みCSVを指定して単独実行できます。

実行例:
    python plot_eiclab_line_follower_log.py run_logs\\run_20261104_120000.csv
    python plot_eiclab_line_follower_log.py run_logs\\run_20261104_120000.csv --output sensors.png

画像データはPNGで保存され、CSV名_plot.pngとして保存します。
センサー値は上段、モーター指令値は下段に描画します。
"""

import argparse
import csv
import math
from pathlib import Path

import matplotlib.pyplot as plt

REQUIRED_COLUMNS = ('time_s', 'sensor_0', 'sensor_1', 'sensor_2', 'sensor_3', 'motor_left', 'motor_right')
SENSOR_COLUMNS = ('sensor_0', 'sensor_1', 'sensor_2', 'sensor_3')

plt.rcParams['axes.unicode_minus'] = False


def read_measurements(csv_path):
    """走行CSVの列を検証し、時間・センサー値・モーター指令値を読み込む。"""
    times = []
    sensors = {name: [] for name in SENSOR_COLUMNS}
    motor_left = []
    motor_right = []

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
                raise ValueError('{}行目に数値でない値があります'.format(line_number)) from None
            if not all(math.isfinite(value) for value in values):
                raise ValueError('{}行目に有限でない数値があります'.format(line_number))

            time_s, *sensor_values, left, right = values
            times.append(time_s)
            for name, value in zip(SENSOR_COLUMNS, sensor_values):
                sensors[name].append(value)
            motor_left.append(left)
            motor_right.append(right)

    if not times:
        raise ValueError('CSVに走行データがありません')
    return times, sensors, motor_left, motor_right


def main():
    parser = argparse.ArgumentParser(
        description='ライントレーサの走行CSVからセンサー値とモーター指令値を描画します'
    )
    parser.add_argument('csv_file', type=Path, help='走行CSVファイル')
    parser.add_argument(
        '-o',
        '--output',
        type=Path,
        help='出力PNGファイル（省略時はCSVと同じ場所に保存）',
    )
    args = parser.parse_args()

    csv_path = args.csv_file
    if not csv_path.is_file():
        parser.error('CSVファイルが見つかりません: {}'.format(csv_path))

    try:
        times, sensors, motor_left, motor_right = read_measurements(csv_path)
    except (OSError, ValueError) as error:
        parser.error('CSVを読み込めません: {}'.format(error))

    output_path = args.output
    if output_path is None:
        output_path = csv_path.with_name(csv_path.stem + '_plot.png')

    # 上段に4つのセンサー値、下段に左右のモーター指令値を時間軸で描画する。
    figure, (sensor_axis, motor_axis) = plt.subplots(
        2, 1, sharex=True, figsize=(10, 7)
    )
    for index, name in enumerate(SENSOR_COLUMNS, start=1):
        sensor_axis.plot(times, sensors[name], label='センサー{}'.format(index))
    sensor_axis.set_ylabel('センサー値')
    sensor_axis.grid(True)
    sensor_axis.legend(title=' {}'.format(csv_path.name))

    motor_axis.plot(times, motor_right, color='tab:blue', label='右モーター')
    motor_axis.plot(times, motor_left, color='tab:orange', label='左モーター')
    motor_axis.set_xlabel('時間 [s]')
    motor_axis.set_ylabel('モーター指令値')
    motor_axis.grid(True)
    motor_axis.legend()
    figure.tight_layout()
    figure.savefig(output_path, dpi=150)
    plt.close(figure)
    print('{} にグラフを保存しました'.format(output_path))


if __name__ == '__main__':
    main()
