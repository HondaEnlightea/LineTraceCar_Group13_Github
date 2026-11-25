#!/usr/bin/python3
# coding: UTF-8
"""
特性試験 CSV のオフライン可視化スクリプト

dev13/pi/measure_motor_characteristics.py および
dev13/pi/measure_sensor_characteristics.py が出力した CSV を読み込み、
左右差や感度特性のグラフを PNG として保存します。

実行例（EicDesignLab フォルダから）:
    python dev13/pc/plot_characteristics.py dev13/data/logs/motor_char_20261125_120000_summary.csv
    python dev13/pc/plot_characteristics.py dev13/data/logs/sensor_char_20261125_120500_summary.csv
    python dev13/pc/plot_characteristics.py dev13/data/logs/sensor_sweep_20261125_121000.csv --calibration dev13/data/logs/sensor_char_20261125_120500_calibration.json
"""

import argparse
import csv
import json
from pathlib import Path

import matplotlib

matplotlib.use('Agg')
from matplotlib import font_manager
import matplotlib.pyplot as plt


JAPANESE_FONT_CANDIDATES = (
    'Meiryo',
    'Yu Gothic',
    'MS Gothic',
    'Noto Sans CJK JP',
    'Noto Sans JP',
    'IPAGothic',
    'TakaoGothic',
)


_FONT_CONFIGURED = False


def configure_japanese_font():
    """環境で利用可能な日本語フォントを選択する（無ければ既定フォントを維持）。"""
    global _FONT_CONFIGURED
    if _FONT_CONFIGURED:
        return
    available = {font.name for font in font_manager.fontManager.ttflist}
    for name in JAPANESE_FONT_CANDIDATES:
        if name in available:
            plt.rcParams['font.family'] = name
            break
    plt.rcParams['axes.unicode_minus'] = False
    _FONT_CONFIGURED = True


def detect_csv_kind(csv_path):
    """CSV の見出し行からファイル種別を自動判定する。"""
    with Path(csv_path).open('r', newline='', encoding='utf-8-sig') as handle:
        reader = csv.reader(handle)
        header = next(reader, None)
    if not header:
        raise ValueError('CSV に見出し行がありません: {}'.format(csv_path))

    cols = set(header)
    if {'motor_command', 'rpm_left_count', 'rpm_right_count'}.issubset(cols):
        return 'motor'
    if {'channel', 'white_mean', 'black_mean', 'abs_contrast'}.issubset(cols):
        return 'sensor-summary'
    if {'time_s', 's0', 's1', 's2', 's3'}.issubset(cols) and 'condition' not in cols:
        return 'sensor-sweep'
    raise ValueError(
        'CSV の種別を判別できません（見出し: {}）'.format(', '.join(header))
    )


def _read_dict_csv(csv_path):
    """UTF-8 BOM 対応で CSV 全行を辞書リストとして読み込む。"""
    with Path(csv_path).open('r', newline='', encoding='utf-8-sig') as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
    if not rows:
        raise ValueError('CSV にデータ行がありません: {}'.format(csv_path))
    return rows


def plot_motor_summary(summary_csv, output_path=None, raw_csv=None):
    """モータ・エンコーダ特性サマリ（および任意で生データ）を描画して保存する。"""
    configure_japanese_font()
    summary_csv = Path(summary_csv)
    rows = _read_dict_csv(summary_csv)

    if raw_csv is None and summary_csv.name.endswith('_summary.csv'):
        candidate = summary_csv.with_name(
            summary_csv.name[:-len('_summary.csv')] + '_raw.csv'
        )
        if candidate.is_file():
            raw_csv = candidate

    raw_rows = _read_dict_csv(raw_csv) if raw_csv is not None else None

    if output_path is None:
        output_path = summary_csv.with_name(summary_csv.stem + '_plot.png')
    else:
        output_path = Path(output_path)

    pwms = [float(r['motor_command']) for r in rows]
    rpm_l = [float(r['rpm_left_count']) for r in rows]
    rpm_r = [float(r['rpm_right_count']) for r in rows]
    std_l = [float(r['rpm_left_std']) for r in rows]
    std_r = [float(r['rpm_right_std']) for r in rows]
    diff_rl = [float(r['rpm_diff_r_minus_l']) for r in rows]
    ratios = [
        float(r['rpm_ratio_r_over_l']) if r['rpm_ratio_r_over_l'] != '' else float('nan')
        for r in rows
    ]

    x_idx = list(range(len(pwms)))
    x_labels = ['{:+.2f}'.format(p) for p in pwms]

    n_panels = 3 if raw_rows is not None else 2
    fig, axes = plt.subplots(n_panels, 1, figsize=(10, 4 * n_panels))
    if n_panels == 2:
        ax_rpm, ax_diff = axes
        ax_raw = None
    else:
        ax_rpm, ax_diff, ax_raw = axes

    # 1. PWM vs 定常 RPM
    ax_rpm.errorbar(
        x_idx, rpm_l, yerr=std_l, marker='o', capsize=4, color='tab:orange', label='左車輪 (count RPM)'
    )
    ax_rpm.errorbar(
        x_idx, rpm_r, yerr=std_r, marker='s', capsize=4, color='tab:blue', label='右車輪 (count RPM)'
    )
    ax_rpm.set_xticks(x_idx)
    ax_rpm.set_xticklabels(x_labels)
    ax_rpm.set_ylabel('回転数 [rpm]')
    ax_rpm.set_title('PWM 指令値に対する車輪回転数特性 ({})'.format(summary_csv.name))
    ax_rpm.grid(True)
    ax_rpm.legend(loc='best')

    # 2. 左右差と比
    ax_diff.axhline(0.0, color='gray', linestyle='--', linewidth=1)
    line_diff = ax_diff.plot(
        x_idx, diff_rl, marker='o', color='tab:red', label='回転数差 (右 - 左) [rpm]'
    )
    ax_diff.set_xticks(x_idx)
    ax_diff.set_xticklabels(x_labels)
    ax_diff.set_xlabel('ステップ順 PWM 指令値')
    ax_diff.set_ylabel('回転数差 (R - L) [rpm]')
    ax_diff.grid(True)

    ax_ratio = ax_diff.twinx()
    ax_ratio.axhline(1.0, color='tab:green', linestyle=':', linewidth=1)
    line_ratio = ax_ratio.plot(
        x_idx, ratios, marker='^', linestyle='--', color='tab:green', label='回転数比 (右 / 左)'
    )
    ax_ratio.set_ylabel('回転数比 (R / L)')

    combined_lines = line_diff + line_ratio
    combined_labels = [ln.get_label() for ln in combined_lines]
    ax_diff.legend(combined_lines, combined_labels, loc='best')

    # 3. 生データ時系列
    if ax_raw is not None and raw_rows is not None:
        t_s = [float(r['time_s']) for r in raw_rows]
        raw_l = [float(r['rpm_left']) for r in raw_rows]
        raw_r = [float(r['rpm_right']) for r in raw_rows]
        raw_cmd = [float(r['motor_command']) for r in raw_rows]

        l1 = ax_raw.plot(t_s, raw_l, color='tab:orange', label='左車輪 [rpm]')
        l2 = ax_raw.plot(t_s, raw_r, color='tab:blue', label='右車輪 [rpm]')
        ax_raw.set_xlabel('経過時間 [s]')
        ax_raw.set_ylabel('瞬時回転数 [rpm]')
        ax_raw.grid(True)

        ax_cmd = ax_raw.twinx()
        l3 = ax_cmd.plot(
            t_s, raw_cmd, color='tab:gray', alpha=0.6, linestyle='--', label='PWM指令値'
        )
        ax_cmd.margins(y=0.1)
        ax_cmd.set_ylabel('PWM指令値')
        ax_raw.legend(l1 + l2 + l3, [ln.get_label() for ln in (l1 + l2 + l3)], loc='best')

    fig.tight_layout()
    fig.savefig(output_path, dpi=150)
    plt.close(fig)
    return output_path


def plot_sensor_summary(summary_csv, output_path=None):
    """フォトリフレクタの白黒感度・左右対称性サマリを描画して保存する。"""
    configure_japanese_font()
    summary_csv = Path(summary_csv)
    rows = _read_dict_csv(summary_csv)

    if output_path is None:
        output_path = summary_csv.with_name(summary_csv.stem + '_plot.png')
    else:
        output_path = Path(output_path)

    labels = [r['position_label'] for r in rows]
    w_means = [float(r['white_mean']) for r in rows]
    w_stds = [float(r['white_std']) for r in rows]
    b_means = [float(r['black_mean']) for r in rows]
    b_stds = [float(r['black_std']) for r in rows]
    contrasts = [float(r['abs_contrast']) for r in rows]

    x_idx = list(range(len(labels)))
    width = 0.35

    fig, (ax_levels, ax_contrast) = plt.subplots(2, 1, figsize=(9, 7))

    ax_levels.bar(
        [x - width / 2 for x in x_idx],
        w_means,
        width=width,
        yerr=w_stds,
        capsize=4,
        color='tab:cyan',
        edgecolor='black',
        label='白路面 (White)',
    )
    ax_levels.bar(
        [x + width / 2 for x in x_idx],
        b_means,
        width=width,
        yerr=b_stds,
        capsize=4,
        color='dimgray',
        edgecolor='black',
        label='黒ライン (Black)',
    )
    ax_levels.set_xticks(x_idx)
    ax_levels.set_xticklabels(labels)
    ax_levels.set_ylim(0.0, 1.05)
    ax_levels.set_ylabel('センサ出力値 [0-1]')
    ax_levels.set_title('フォトリフレクタ 白・黒感度特性 ({})'.format(summary_csv.name))
    ax_levels.grid(True, axis='y')
    ax_levels.legend()

    bars = ax_contrast.bar(
        x_idx,
        contrasts,
        color=['tab:orange', 'tab:green', 'tab:cyan', 'tab:blue'][: len(x_idx)],
        edgecolor='black',
    )
    top_y = max((max(contrasts) * 1.18) if contrasts else 0.2, 0.2)
    ax_contrast.set_ylim(0.0, top_y)
    ax_contrast.set_xticks(x_idx)
    ax_contrast.set_xticklabels(labels)
    ax_contrast.set_ylabel('コントラスト |White - Black|')
    ax_contrast.grid(True, axis='y')

    for bar, val in zip(bars, contrasts):
        ax_contrast.text(
            bar.get_x() + bar.get_width() / 2.0,
            val + top_y * 0.02,
            '{:.3f}'.format(val),
            ha='center',
            va='bottom',
            fontsize=9,
        )

    if len(contrasts) == 4:
        outer_diff = contrasts[3] - contrasts[0]
        inner_diff = contrasts[2] - contrasts[1]
        ax_contrast.set_title(
            'コントラスト左右差: 外側(ch3-ch0)={:+.3f}, 内側(ch2-ch1)={:+.3f}'.format(
                outer_diff, inner_diff
            )
        )

    fig.tight_layout()
    fig.savefig(output_path, dpi=150)
    plt.close(fig)
    return output_path


def plot_sensor_sweep(sweep_csv, output_path=None, calibration_json=None):
    """フォトリフレクタの連続掃引 CSV（および任意の正規化結果）を描画する。"""
    configure_japanese_font()
    sweep_csv = Path(sweep_csv)
    rows = _read_dict_csv(sweep_csv)

    if output_path is None:
        output_path = sweep_csv.with_name(sweep_csv.stem + '_plot.png')
    else:
        output_path = Path(output_path)

    times = [float(r['time_s']) for r in rows]
    series = [[float(r['s{}'.format(ch)]) for r in rows] for ch in range(4)]
    labels = ('ch0(左外)', 'ch1(左内)', 'ch2(右内)', 'ch3(右外)')
    colors = ('tab:orange', 'tab:green', 'tab:cyan', 'tab:blue')

    cal_data = None
    if calibration_json is not None:
        cal_data = json.loads(Path(calibration_json).read_text(encoding='utf-8'))['channels']

    n_panels = 2 if cal_data is not None else 1
    fig, axes = plt.subplots(n_panels, 1, sharex=True, figsize=(10, 4 * n_panels))
    if n_panels == 1:
        ax_raw = axes
        ax_norm = None
    else:
        ax_raw, ax_norm = axes

    for ch in range(4):
        ax_raw.plot(times, series[ch], label=labels[ch], color=colors[ch])
    ax_raw.set_ylabel('生出力値 [0-1]')
    ax_raw.set_title('フォトリフレクタ連続掃引応答 ({})'.format(sweep_csv.name))
    ax_raw.grid(True)
    ax_raw.legend(loc='best')

    if ax_norm is not None and cal_data is not None:
        for ch in range(4):
            b_m = float(cal_data[str(ch)]['black_mean'])
            w_m = float(cal_data[str(ch)]['white_mean'])
            denom = w_m - b_m
            norm = [
                ((v - b_m) / denom) if abs(denom) > 1e-6 else 0.0
                for v in series[ch]
            ]
            ax_norm.plot(times, norm, label=labels[ch], color=colors[ch])
        ax_norm.axhline(0.0, color='gray', linestyle=':', linewidth=1)
        ax_norm.axhline(1.0, color='gray', linestyle=':', linewidth=1)
        ax_norm.set_ylabel('正規化値 (0=黒, 1=白)')
        ax_norm.grid(True)
        ax_norm.legend(loc='best')
        ax_norm.set_xlabel('経過時間 [s]')
    else:
        ax_raw.set_xlabel('経過時間 [s]')

    fig.tight_layout()
    fig.savefig(output_path, dpi=150)
    plt.close(fig)
    return output_path


def build_parser():
    """CLI 引数パーサを作成する。"""
    parser = argparse.ArgumentParser(
        description='特性試験 CSV（モータ・センサ）のグラフ PNG を作成します'
    )
    parser.add_argument('csv_file', type=Path, help='入力 CSV ファイル')
    parser.add_argument(
        '--kind',
        choices=('auto', 'motor', 'sensor-summary', 'sensor-sweep'),
        default='auto',
        help='CSV の種別（既定: auto）',
    )
    parser.add_argument(
        '--raw',
        type=Path,
        default=None,
        help='モータサマリ描画時に重ねる生データ CSV（省略時は同名の *_raw.csv を自動探索）',
    )
    parser.add_argument(
        '--calibration',
        type=Path,
        default=None,
        help='sensor-sweep 描画時に正規化曲線を併記するためのキャリブレーション JSON',
    )
    parser.add_argument(
        '-o',
        '--output',
        type=Path,
        default=None,
        help='出力 PNG ファイルパス（省略時は CSV名_plot.png）',
    )
    return parser


def main(argv=None):
    """メイン関数。"""
    configure_japanese_font()
    parser = build_parser()
    args = parser.parse_args(argv)

    if not args.csv_file.is_file():
        parser.error('CSV ファイルが見つかりません: {}'.format(args.csv_file))

    try:
        kind = detect_csv_kind(args.csv_file) if args.kind == 'auto' else args.kind
        if kind == 'motor':
            out = plot_motor_summary(args.csv_file, output_path=args.output, raw_csv=args.raw)
        elif kind == 'sensor-summary':
            out = plot_sensor_summary(args.csv_file, output_path=args.output)
        else:
            out = plot_sensor_sweep(
                args.csv_file,
                output_path=args.output,
                calibration_json=args.calibration,
            )
    except (OSError, ValueError, KeyError) as exc:
        parser.error('グラフ作成に失敗しました: {}'.format(exc))

    print('{} にグラフを保存しました'.format(out))


if __name__ == '__main__':
    main()
