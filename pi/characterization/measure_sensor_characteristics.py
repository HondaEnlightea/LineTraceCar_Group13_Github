#!/usr/bin/python3
# coding: UTF-8
"""
車体の特性試験用プログラム（フォトリフレクタ感度・左右差測定）

4ch のフォトリフレクタ（MCP3004 ch0〜ch3）の白・黒路面での感度、ノイズ、
および左右の個体差（対称性）を計測します。
Raspberry Pi Zero 2 W での動作を想定し、標準ライブラリと gpiozero のみで動作します。
ラズパイ側では軽量に CSV/JSON へ記録するまでを担当し、グラフ描画はこのパソコン側で
dev13/pc/plot_characteristics.py を実行して行ってください。

実行例（EicDesignLab フォルダから）:
    python3 dev13/pi/measure_sensor_characteristics.py
    python3 dev13/pi/measure_sensor_characteristics.py --black-mode all --include-ambient
    python3 dev13/pi/measure_sensor_characteristics.py --check-motor-noise
    python3 dev13/pi/measure_sensor_characteristics.py --mode sweep --duration 15
"""

import argparse
import csv
import json
import os
import signal
import statistics
import subprocess
import time
from datetime import datetime
from pathlib import Path
from threading import Event

from gpiozero import LED, MCP3004, Robot


NUM_CHANNELS = 4
CHANNEL_LABELS = (
    'ch0(左外)',
    'ch1(左内)',
    'ch2(右内)',
    'ch3(右外)',
)

STATIC_RAW_HEADER = [
    'time_s',
    'condition',
    'target_channel',
    's0',
    's1',
    's2',
    's3',
    'raw0',
    'raw1',
    'raw2',
    'raw3',
]

STATIC_SUMMARY_HEADER = [
    'channel',
    'position_label',
    'white_mean',
    'white_std',
    'white_min',
    'white_max',
    'black_mean',
    'black_std',
    'black_min',
    'black_max',
    'contrast_w_minus_b',
    'abs_contrast',
    'midpoint',
    'snr',
]

SWEEP_HEADER = [
    'time_s',
    's0',
    's1',
    's2',
    's3',
    'raw0',
    'raw1',
    'raw2',
    'raw3',
]


def _read_all_channels(sensors):
    """4ch の正規化値 [0, 1] と 10bit 生値 [0..1023] を取得する。"""
    values = []
    raws = []
    for pr in sensors:
        val = float(pr.value)
        raw_attr = getattr(pr, 'raw_value', None)
        if raw_attr is not None:
            raw = int(raw_attr)
        else:
            raw = int(round(val * 1023))
        values.append(val)
        raws.append(raw)
    return values, raws


def _create_unique_static_files(output_dir, prefix='sensor_char'):
    """上書きしないユニークな static モード用ファイル群を作成する。"""
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    suffix = 0
    while True:
        stem = '{}_{}'.format(prefix, stamp)
        if suffix:
            stem = '{}_{:02d}'.format(stem, suffix)
        raw_path = output_dir / '{}_raw.csv'.format(stem)
        summary_path = output_dir / '{}_summary.csv'.format(stem)
        cal_path = output_dir / '{}_calibration.json'.format(stem)
        raw_file = None
        summary_file = None
        cal_file = None
        try:
            raw_file = raw_path.open('x', newline='', encoding='utf-8')
            summary_file = summary_path.open('x', newline='', encoding='utf-8')
            cal_file = cal_path.open('x', encoding='utf-8')
            return (
                raw_path,
                summary_path,
                cal_path,
                raw_file,
                summary_file,
                cal_file,
            )
        except FileExistsError:
            for handle, path in (
                (raw_file, raw_path),
                (summary_file, summary_path),
                (cal_file, cal_path),
            ):
                if handle is not None:
                    handle.close()
                    path.unlink(missing_ok=True)
            suffix += 1


def _create_unique_sweep_file(output_dir, prefix='sensor_sweep'):
    """上書きしないユニークな sweep モード用 CSV ファイルを作成する。"""
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    suffix = 0
    while True:
        stem = '{}_{}'.format(prefix, stamp)
        if suffix:
            stem = '{}_{:02d}'.format(stem, suffix)
        csv_path = output_dir / '{}.csv'.format(stem)
        try:
            csv_file = csv_path.open('x', newline='', encoding='utf-8')
            return csv_path, csv_file
        except FileExistsError:
            suffix += 1


def _flush_and_sync(file_obj):
    """バッファを OS へフラッシュし、可能であればストレージへ同期する。"""
    if file_obj is None or getattr(file_obj, 'closed', False):
        return
    file_obj.flush()
    try:
        os.fsync(file_obj.fileno())
    except (OSError, ValueError, AttributeError):
        pass


class _StatusLED:
    """ヘッドレス実行時の状態通知用 LED ラッパー（ピン未指定時は何もしない）。"""

    def __init__(self, pin, led_factory=None):
        if pin is None:
            self._dev = None
        elif led_factory is not None:
            self._dev = led_factory(pin)
        else:
            self._dev = LED(pin)

    def on(self):
        if self._dev is not None:
            self._dev.on()

    def off(self):
        if self._dev is not None:
            self._dev.off()

    def blink(self):
        if self._dev is not None:
            if hasattr(self._dev, 'blink'):
                self._dev.blink(on_time=0.2, off_time=0.2, background=True)
            else:
                self._dev.on()

    def close(self):
        if self._dev is not None:
            try:
                self._dev.off()
            finally:
                self._dev.close()


def _wait_with_countdown(seconds, label, status_led, stop_event, clock, sleep_fn):
    """待機中にステータス LED を点滅させ、指定秒数待機する。"""
    if seconds <= 0.0 or stop_event.is_set():
        return
    print('{} ({:.1f} 秒待機)...'.format(label, seconds))
    status_led.blink()
    end_time = clock() + seconds
    while not stop_event.is_set():
        remaining = end_time - clock()
        if remaining <= 1e-9:
            break
        sleep_fn(min(0.25, remaining))


def _calc_series_stats(values):
    """サンプル列から平均・標準偏差・最小・最大を計算する。"""
    if not values:
        return 0.0, 0.0, 0.0, 0.0
    mean_v = statistics.fmean(values)
    std_v = statistics.stdev(values) if len(values) >= 2 else 0.0
    return mean_v, std_v, min(values), max(values)


def _sample_condition(
    *,
    sensors,
    condition,
    target_channel,
    samples,
    interval_s,
    t_start,
    raw_writer,
    raw_file,
    stop_event,
    clock,
    sleep_fn,
):
    """指定条件で所定サンプル数だけ 4ch を計測し、チャネルごとの値リストを返す。"""
    per_ch = [[] for _ in range(NUM_CHANNELS)]
    next_tick = clock()
    for idx in range(samples):
        if stop_event.is_set():
            break
        now = clock()
        vals, raws = _read_all_channels(sensors)
        for ch_idx, v in enumerate(vals):
            per_ch[ch_idx].append(v)

        raw_writer.writerow(
            [
                '{:.3f}'.format(now - t_start),
                condition,
                str(target_channel),
            ]
            + ['{:.4f}'.format(v) for v in vals]
            + raws
        )
        if (idx + 1) % 10 == 0:
            raw_file.flush()

        if idx + 1 < samples:
            next_tick += interval_s
            wait_s = next_tick - clock()
            if wait_s > 0.0:
                sleep_fn(wait_s)

    _flush_and_sync(raw_file)
    return per_ch


def _compute_symmetry_and_warnings(channel_stats):
    """左右対称性指標と異常診断メッセージを計算する。"""
    s0, s1, s2, s3 = channel_stats

    def _pair_metrics(name, left_st, right_st):
        c_left = left_st['abs_contrast']
        c_right = right_st['abs_contrast']
        rel_diff = (
            abs(c_right - c_left) / max(c_left, c_right)
            if max(c_left, c_right) > 0.0
            else 0.0
        )
        return {
            'pair': name,
            'left_channel': left_st['channel'],
            'right_channel': right_st['channel'],
            'white_diff_r_minus_l': right_st['white_mean'] - left_st['white_mean'],
            'black_diff_r_minus_l': right_st['black_mean'] - left_st['black_mean'],
            'contrast_diff_r_minus_l': c_right - c_left,
            'contrast_ratio_r_over_l': (c_right / c_left) if c_left > 0.0 else None,
            'relative_contrast_diff': rel_diff,
        }

    outer_pair = _pair_metrics('outer(ch0-ch3)', s0, s3)
    inner_pair = _pair_metrics('inner(ch1-ch2)', s1, s2)
    left_mean_contrast = (s0['abs_contrast'] + s1['abs_contrast']) / 2.0
    right_mean_contrast = (s2['abs_contrast'] + s3['abs_contrast']) / 2.0

    symmetry = {
        'outer_pair': outer_pair,
        'inner_pair': inner_pair,
        'left_pair_mean_contrast': left_mean_contrast,
        'right_pair_mean_contrast': right_mean_contrast,
        'left_right_mean_contrast_diff': right_mean_contrast - left_mean_contrast,
    }

    warnings = []
    for st in channel_stats:
        ch_name = st['position_label']
        w_m = st['white_mean']
        b_m = st['black_mean']
        abs_c = st['abs_contrast']

        if (w_m < 0.02 and b_m < 0.02) or (w_m > 0.98 and b_m > 0.98):
            warnings.append(
                '{}: 出力が飽和/固着している疑いがあります (white={:.3f}, black={:.3f})'.format(
                    ch_name, w_m, b_m
                )
            )
        elif abs_c < 0.05:
            warnings.append(
                '{}: 白黒の差が極端に小さく断線・未接続・配置不良の恐れがあります (|W-B|={:.3f})'.format(
                    ch_name, abs_c
                )
            )
        elif abs_c < 0.15:
            warnings.append(
                '{}: コントラストが低めです (|W-B|={:.3f})。高さ・向き・抵抗を確認してください'.format(
                    ch_name, abs_c
                )
            )

        if st['contrast_w_minus_b'] < -0.05:
            warnings.append(
                '{}: 黒の方が白より高い値です (W={:.3f}, B={:.3f})。極性または配線を確認してください'.format(
                    ch_name, w_m, b_m
                )
            )

        if st['white_std'] > 0.03 or st['black_std'] > 0.03:
            warnings.append(
                '{}: ばらつきが大きいです (std_W={:.4f}, std_B={:.4f})'.format(
                    ch_name, st['white_std'], st['black_std']
                )
            )

    for pair in (outer_pair, inner_pair):
        if pair['relative_contrast_diff'] > 0.15:
            warnings.append(
                '左右非対称 ({}): コントラスト相対差が {:.1f}% あります (L={:.3f}, R={:.3f})'.format(
                    pair['pair'],
                    pair['relative_contrast_diff'] * 100.0,
                    channel_stats[pair['left_channel']]['abs_contrast'],
                    channel_stats[pair['right_channel']]['abs_contrast'],
                )
            )

    return symmetry, warnings


def _format_static_report(channel_stats, symmetry, warnings, motor_noise=None):
    """コンソール出力用の静的試験レポートを整形する。"""
    lines = []
    header = (
        'CH | Pos      | White(mean±std) | Black(mean±std) | W-B    | Mid   | SNR'
    )
    lines.append(header)
    lines.append('-' * len(header))
    for st in channel_stats:
        lines.append(
            '{:2d} | {:8s} | {:.3f} ± {:.4f} | {:.3f} ± {:.4f} | {:+6.3f} | {:.3f} | {:6.1f}'.format(
                st['channel'],
                st['position_label'],
                st['white_mean'],
                st['white_std'],
                st['black_mean'],
                st['black_std'],
                st['contrast_w_minus_b'],
                st['midpoint'],
                st['snr'],
            )
        )

    lines.append('')
    lines.append('--- 左右差（対称性）確認 ---')
    for pair in (symmetry['outer_pair'], symmetry['inner_pair']):
        ratio = pair['contrast_ratio_r_over_l']
        ratio_txt = '{:.3f}'.format(ratio) if ratio is not None else 'N/A'
        lines.append(
            '  {}: ΔW={:+.3f}, ΔB={:+.3f}, Δ|W-B|={:+.3f}, Ratio(R/L)={}, 相対差={:.1f}%'.format(
                pair['pair'],
                pair['white_diff_r_minus_l'],
                pair['black_diff_r_minus_l'],
                pair['contrast_diff_r_minus_l'],
                ratio_txt,
                pair['relative_contrast_diff'] * 100.0,
            )
        )

    if motor_noise is not None:
        lines.append('')
        lines.append('--- モータ駆動時の電源/ADC ノイズ影響 ---')
        for item in motor_noise['channels']:
            lines.append(
                '  {}: Δmean={:+.4f}, std_off={:.4f} -> std_on={:.4f}'.format(
                    item['position_label'],
                    item['mean_shift'],
                    item['std_off'],
                    item['std_on'],
                )
            )

    if warnings:
        lines.append('')
        for warning in warnings:
            lines.append('[警告] {}'.format(warning))
    else:
        lines.append('')
        lines.append('[判定] 全チャネルの感度および左右対称性は良好な範囲です。')

    return '\n'.join(lines)


def run_static_test(
    *,
    samples=50,
    interval_s=0.05,
    black_mode='each',
    include_ambient=False,
    check_motor_noise=False,
    noise_pwm=0.5,
    ain1=6,
    ain2=5,
    bin1=26,
    bin2=27,
    output_dir=None,
    yes=False,
    start_delay_s=0.0,
    step_wait_s=0.0,
    led_pin=None,
    auto_poweroff=False,
    adc_factory=None,
    robot_factory=None,
    led_factory=None,
    poweroff_fn=None,
    input_fn=input,
    clock=time.monotonic,
    sleep_fn=time.sleep,
):
    """白・黒（および任意で外乱光・モータノイズ）の静的感度特性を計測する。"""
    if output_dir is None:
        output_dir = Path(__file__).resolve().parent / 'logs'
    else:
        output_dir = Path(output_dir)

    if adc_factory is None:
        adc_factory = lambda ch: MCP3004(channel=ch)
    if robot_factory is None:
        robot_factory = lambda: Robot(left=(ain1, ain2), right=(bin1, bin2))

    (
        raw_path,
        summary_path,
        cal_path,
        raw_file,
        summary_file,
        cal_file,
    ) = _create_unique_static_files(output_dir)

    raw_writer = csv.writer(raw_file)
    summary_writer = csv.writer(summary_file)
    raw_writer.writerow(STATIC_RAW_HEADER)
    summary_writer.writerow(STATIC_SUMMARY_HEADER)
    raw_file.flush()
    summary_file.flush()

    stop_event = Event()
    previous_sigterm = None
    sigterm_registered = False
    try:
        previous_sigterm = signal.getsignal(signal.SIGTERM)
        signal.signal(signal.SIGTERM, lambda _signum, _frame: stop_event.set())
        sigterm_registered = True
    except (ValueError, AttributeError):
        pass

    sensors = []
    motors = None
    status_led = _StatusLED(led_pin, led_factory=led_factory)
    channel_stats = []
    symmetry = {}
    warnings = []
    motor_noise_result = None
    aborted = False

    try:
        sensors = [adc_factory(ch) for ch in range(NUM_CHANNELS)]
        t_start = clock()

        # 1. 白路面計測
        if not yes:
            print('=== ステップ 1: 白路面の計測 ===')
            input_fn('4つすべてのフォトリフレクタを白い路面の上に置き、Enter を押してください: ')
        if start_delay_s > 0.0:
            _wait_with_countdown(
                start_delay_s, '開始前待機（白路面）', status_led, stop_event, clock, sleep_fn
            )
        status_led.on()
        white_per_ch = _sample_condition(
            sensors=sensors,
            condition='white',
            target_channel='all',
            samples=samples,
            interval_s=interval_s,
            t_start=t_start,
            raw_writer=raw_writer,
            raw_file=raw_file,
            stop_event=stop_event,
            clock=clock,
            sleep_fn=sleep_fn,
        )

        # 2. 黒ライン計測
        black_per_ch = [[] for _ in range(NUM_CHANNELS)]
        if not stop_event.is_set():
            if black_mode == 'each':
                for ch_idx in range(NUM_CHANNELS):
                    if stop_event.is_set():
                        break
                    if not yes:
                        print(
                            '=== ステップ 2-{}: {} の黒ライン計測 ==='.format(
                                ch_idx + 1, CHANNEL_LABELS[ch_idx]
                            )
                        )
                        input_fn(
                            '{} が黒いラインの真上に来るように置き、Enter を押してください: '.format(
                                CHANNEL_LABELS[ch_idx]
                            )
                        )
                    elif step_wait_s > 0.0:
                        _wait_with_countdown(
                            step_wait_s,
                            '{} を黒ライン中央へ移動してください'.format(CHANNEL_LABELS[ch_idx]),
                            status_led,
                            stop_event,
                            clock,
                            sleep_fn,
                        )
                    status_led.on()
                    measured = _sample_condition(
                        sensors=sensors,
                        condition='black_ch{}'.format(ch_idx),
                        target_channel=ch_idx,
                        samples=samples,
                        interval_s=interval_s,
                        t_start=t_start,
                        raw_writer=raw_writer,
                        raw_file=raw_file,
                        stop_event=stop_event,
                        clock=clock,
                        sleep_fn=sleep_fn,
                    )
                    black_per_ch[ch_idx] = measured[ch_idx]
            else:
                if not yes:
                    print('=== ステップ 2: 黒路面（全チャネル同時）の計測 ===')
                    input_fn('4つすべてのフォトリフレクタを広い黒領域の上に置き、Enter を押してください: ')
                elif step_wait_s > 0.0:
                    _wait_with_countdown(
                        step_wait_s,
                        '4ch すべてを黒領域へ移動してください',
                        status_led,
                        stop_event,
                        clock,
                        sleep_fn,
                    )
                status_led.on()
                black_per_ch = _sample_condition(
                    sensors=sensors,
                    condition='black_all',
                    target_channel='all',
                    samples=samples,
                    interval_s=interval_s,
                    t_start=t_start,
                    raw_writer=raw_writer,
                    raw_file=raw_file,
                    stop_event=stop_event,
                    clock=clock,
                    sleep_fn=sleep_fn,
                )

        # 3. 任意の持ち上げ（外乱光）計測
        ambient_per_ch = None
        if include_ambient and not stop_event.is_set():
            if not yes:
                print('=== ステップ 3: 外乱光（車体持ち上げ）の計測 ===')
                input_fn('車体を路面から持ち上げた状態にして Enter を押してください: ')
            elif step_wait_s > 0.0:
                _wait_with_countdown(
                    step_wait_s,
                    '車体を持ち上げてください（外乱光計測）',
                    status_led,
                    stop_event,
                    clock,
                    sleep_fn,
                )
            status_led.on()
            ambient_per_ch = _sample_condition(
                sensors=sensors,
                condition='ambient',
                target_channel='all',
                samples=samples,
                interval_s=interval_s,
                t_start=t_start,
                raw_writer=raw_writer,
                raw_file=raw_file,
                stop_event=stop_event,
                clock=clock,
                sleep_fn=sleep_fn,
            )

        # 4. 任意のモータノイズ影響計測
        if check_motor_noise and not stop_event.is_set():
            if not yes:
                print('=== ステップ 4: モータ駆動による電源・ADC ノイズ確認 ===')
                input_fn('車輪を浮かせた状態（安全確保）で Enter を押してください: ')
            elif step_wait_s > 0.0:
                _wait_with_countdown(
                    step_wait_s,
                    '車輪を浮かせてください（モータノイズ計測）',
                    status_led,
                    stop_event,
                    clock,
                    sleep_fn,
                )
            status_led.on()
            off_vals = _sample_condition(
                sensors=sensors,
                condition='motor_off',
                target_channel='all',
                samples=samples,
                interval_s=interval_s,
                t_start=t_start,
                raw_writer=raw_writer,
                raw_file=raw_file,
                stop_event=stop_event,
                clock=clock,
                sleep_fn=sleep_fn,
            )
            motors = robot_factory()
            motors.value = (noise_pwm, noise_pwm)
            sleep_fn(0.3)
            on_vals = _sample_condition(
                sensors=sensors,
                condition='motor_on',
                target_channel='all',
                samples=samples,
                interval_s=interval_s,
                t_start=t_start,
                raw_writer=raw_writer,
                raw_file=raw_file,
                stop_event=stop_event,
                clock=clock,
                sleep_fn=sleep_fn,
            )
            motors.stop()

            noise_channels = []
            for ch_idx in range(NUM_CHANNELS):
                m_off, s_off, _, _ = _calc_series_stats(off_vals[ch_idx])
                m_on, s_on, _, _ = _calc_series_stats(on_vals[ch_idx])
                noise_channels.append({
                    'channel': ch_idx,
                    'position_label': CHANNEL_LABELS[ch_idx],
                    'mean_off': m_off,
                    'std_off': s_off,
                    'mean_on': m_on,
                    'std_on': s_on,
                    'mean_shift': m_on - m_off,
                })
            motor_noise_result = {
                'noise_pwm': noise_pwm,
                'channels': noise_channels,
            }

        if stop_event.is_set():
            aborted = True

        # 集計・サマリ出力
        for ch_idx in range(NUM_CHANNELS):
            w_mean, w_std, w_min, w_max = _calc_series_stats(white_per_ch[ch_idx])
            b_mean, b_std, b_min, b_max = _calc_series_stats(black_per_ch[ch_idx])
            contrast = w_mean - b_mean
            abs_contrast = abs(contrast)
            midpoint = (w_mean + b_mean) / 2.0
            snr = abs_contrast / max(w_std, b_std, 1e-6)

            st = {
                'channel': ch_idx,
                'position_label': CHANNEL_LABELS[ch_idx],
                'white_mean': w_mean,
                'white_std': w_std,
                'white_min': w_min,
                'white_max': w_max,
                'black_mean': b_mean,
                'black_std': b_std,
                'black_min': b_min,
                'black_max': b_max,
                'contrast_w_minus_b': contrast,
                'abs_contrast': abs_contrast,
                'midpoint': midpoint,
                'snr': snr,
            }
            if ambient_per_ch is not None:
                amb_m, amb_s, _, _ = _calc_series_stats(ambient_per_ch[ch_idx])
                st['ambient_mean'] = amb_m
                st['ambient_std'] = amb_s
            channel_stats.append(st)

            summary_writer.writerow([
                ch_idx,
                CHANNEL_LABELS[ch_idx],
                '{:.4f}'.format(w_mean),
                '{:.4f}'.format(w_std),
                '{:.4f}'.format(w_min),
                '{:.4f}'.format(w_max),
                '{:.4f}'.format(b_mean),
                '{:.4f}'.format(b_std),
                '{:.4f}'.format(b_min),
                '{:.4f}'.format(b_max),
                '{:+.4f}'.format(contrast),
                '{:.4f}'.format(abs_contrast),
                '{:.4f}'.format(midpoint),
                '{:.2f}'.format(snr),
            ])
        _flush_and_sync(summary_file)

        symmetry, warnings = _compute_symmetry_and_warnings(channel_stats)

        cal_payload = {
            'metadata': {
                'created_at': datetime.now().isoformat(timespec='seconds'),
                'black_mode': black_mode,
                'samples_per_condition': samples,
                'interval_s': interval_s,
                'aborted': aborted,
                'normalization_formula': '(value - black_mean) / (white_mean - black_mean)',
            },
            'channels': {
                str(st['channel']): st for st in channel_stats
            },
            'symmetry': symmetry,
            'warnings': warnings,
            'motor_noise': motor_noise_result,
        }
        json.dump(cal_payload, cal_file, ensure_ascii=False, indent=2)
        _flush_and_sync(cal_file)

    except KeyboardInterrupt:
        aborted = True
        print('\nCtrl+C を検出しました。センサーを閉じて保存します。')
    finally:
        stop_event.set()
        try:
            if motors is not None:
                try:
                    motors.stop()
                finally:
                    motors.close()
        finally:
            try:
                for pr in sensors:
                    pr.close()
            finally:
                try:
                    status_led.close()
                finally:
                    try:
                        _flush_and_sync(raw_file)
                        raw_file.close()
                    finally:
                        try:
                            _flush_and_sync(summary_file)
                            summary_file.close()
                        finally:
                            _flush_and_sync(cal_file)
                            cal_file.close()
                            if sigterm_registered:
                                signal.signal(signal.SIGTERM, previous_sigterm)

    if channel_stats:
        print('\n=== フォトリフレクタ感度計測サマリ ===')
        print(_format_static_report(channel_stats, symmetry, warnings, motor_noise_result))

    print('\n保存ファイル:')
    print('  生データ CSV       : {}'.format(raw_path))
    print('  サマリ CSV         : {}'.format(summary_path))
    print('  キャリブレーション : {}'.format(cal_path))

    if auto_poweroff and not aborted:
        print('自動シャットダウンを実行します（緑色 LED が完全消灯してから電源を切ってください）。')
        if poweroff_fn is not None:
            poweroff_fn()
        else:
            subprocess.run(['sudo', 'poweroff'], check=False)

    return {
        'raw_path': raw_path,
        'summary_path': summary_path,
        'calibration_path': cal_path,
        'channel_stats': channel_stats,
        'symmetry': symmetry,
        'warnings': warnings,
        'motor_noise': motor_noise_result,
        'aborted': aborted,
    }


def _load_calibration_map(cal_path):
    """キャリブレーション JSON から各チャネルの (black_mean, white_mean) を読み込む。"""
    data = json.loads(Path(cal_path).read_text(encoding='utf-8'))
    channels = data['channels']
    result = []
    for ch in range(NUM_CHANNELS):
        entry = channels[str(ch)]
        result.append((float(entry['black_mean']), float(entry['white_mean'])))
    return result


def run_sweep_test(
    *,
    duration_s=15.0,
    interval_s=0.02,
    calibration_path=None,
    output_dir=None,
    start_delay_s=0.0,
    led_pin=None,
    auto_poweroff=False,
    adc_factory=None,
    led_factory=None,
    poweroff_fn=None,
    clock=time.monotonic,
    sleep_fn=time.sleep,
):
    """ラインを横切らせた際の連続応答を CSV に記録する。"""
    if output_dir is None:
        output_dir = Path(__file__).resolve().parent / 'logs'
    else:
        output_dir = Path(output_dir)

    if adc_factory is None:
        adc_factory = lambda ch: MCP3004(channel=ch)

    cal_map = _load_calibration_map(calibration_path) if calibration_path else None
    csv_path, csv_file = _create_unique_sweep_file(output_dir)
    writer = csv.writer(csv_file)
    writer.writerow(SWEEP_HEADER)
    csv_file.flush()

    stop_event = Event()
    previous_sigterm = None
    sigterm_registered = False
    try:
        previous_sigterm = signal.getsignal(signal.SIGTERM)
        signal.signal(signal.SIGTERM, lambda _signum, _frame: stop_event.set())
        sigterm_registered = True
    except (ValueError, AttributeError):
        pass

    sensors = []
    status_led = _StatusLED(led_pin, led_factory=led_factory)
    sample_count = 0
    aborted = False

    try:
        sensors = [adc_factory(ch) for ch in range(NUM_CHANNELS)]
        _wait_with_countdown(
            start_delay_s, 'Sweep 開始前待機', status_led, stop_event, clock, sleep_fn
        )
        status_led.on()

        t_start = clock()
        end_time = (t_start + duration_s) if duration_s > 0.0 else None
        next_tick = t_start
        last_print_t = t_start - 1.0

        print('=== フォトリフレクタ連続掃引（Sweep）計測 ===')
        print('車体を左右にゆっくり動かして、4つのセンサーで黒ラインを横切らせてください。')

        while not stop_event.is_set():
            now = clock()
            elapsed = now - t_start
            if end_time is not None and (end_time - now) <= 1e-9:
                break

            vals, raws = _read_all_channels(sensors)
            writer.writerow(
                ['{:.3f}'.format(elapsed)]
                + ['{:.4f}'.format(v) for v in vals]
                + raws
            )
            sample_count += 1
            if sample_count % 50 == 0:
                _flush_and_sync(csv_file)
            elif sample_count % 25 == 0:
                csv_file.flush()

            # Raspberry Pi Zero 2 W の標準出力負荷を抑えるため約 5 Hz で表示する
            if (now - last_print_t) >= 0.2:
                last_print_t = now
                if cal_map is not None:
                    norm_vals = []
                    for v, (b_m, w_m) in zip(vals, cal_map):
                        denom = w_m - b_m
                        norm_v = (v - b_m) / denom if abs(denom) > 1e-6 else 0.0
                        norm_vals.append(norm_v)
                    print(
                        't={:5.2f}s raw=[{}] norm=[{}]'.format(
                            elapsed,
                            ', '.join('{:.3f}'.format(v) for v in vals),
                            ', '.join('{:+.2f}'.format(n) for n in norm_vals),
                        )
                    )
                else:
                    print(
                        't={:5.2f}s [{}]'.format(
                            elapsed,
                            ', '.join('ch{}:{:.3f}'.format(i, v) for i, v in enumerate(vals)),
                        )
                    )

            next_tick += interval_s
            now_after = clock()
            if end_time is not None:
                remaining = end_time - now_after
                if remaining <= 1e-9:
                    break
                if next_tick >= end_time - 1e-9:
                    sleep_fn(remaining)
                    break
            wait_s = next_tick - now_after
            if wait_s > 0.0:
                sleep_fn(wait_s)

    except KeyboardInterrupt:
        aborted = True
        print('\nCtrl+C により Sweep 計測を終了します。')
    finally:
        stop_event.set()
        try:
            for pr in sensors:
                pr.close()
        finally:
            try:
                status_led.close()
            finally:
                _flush_and_sync(csv_file)
                csv_file.close()
                if sigterm_registered:
                    signal.signal(signal.SIGTERM, previous_sigterm)

    print('Sweep CSV を保存しました ({} サンプル): {}'.format(sample_count, csv_path))

    if auto_poweroff and not aborted:
        print('自動シャットダウンを実行します（緑色 LED が完全消灯してから電源を切ってください）。')
        if poweroff_fn is not None:
            poweroff_fn()
        else:
            subprocess.run(['sudo', 'poweroff'], check=False)

    return {
        'csv_path': csv_path,
        'sample_count': sample_count,
        'aborted': aborted,
    }


def build_parser():
    """CLI 引数パーサを作成する。"""
    parser = argparse.ArgumentParser(
        description='4ch フォトリフレクタの白黒感度・ノイズ・左右差を計測します'
    )
    parser.add_argument(
        '--mode',
        choices=('static', 'sweep'),
        default='static',
        help='計測モード（static: 白黒定点計測、sweep: ライン横切り連続記録、既定: static）',
    )
    parser.add_argument(
        '--samples',
        type=int,
        default=50,
        help='static モードでの各条件サンプル数（既定: 50）',
    )
    parser.add_argument(
        '--interval',
        type=float,
        default=None,
        help='サンプリング周期 [s]（既定: static=0.05, sweep=0.02）',
    )
    parser.add_argument(
        '--black-mode',
        choices=('each', 'all'),
        default='each',
        help='黒計測の方法（each: 各チャネルを順にライン上に置く、all: 4ch同時、既定: each）',
    )
    parser.add_argument(
        '--include-ambient',
        action='store_true',
        help='車体持ち上げ（外乱光）状態も追加計測する',
    )
    parser.add_argument(
        '--check-motor-noise',
        action='store_true',
        help='モータ停止時と駆動時のセンサ値・ばらつきを比較してノイズ影響を確認する',
    )
    parser.add_argument(
        '--noise-pwm',
        type=float,
        default=0.5,
        help='--check-motor-noise で与える PWM 指令値（既定: 0.5）',
    )
    parser.add_argument(
        '--duration',
        type=float,
        default=15.0,
        help='sweep モードの計測時間 [s]（0 以下で Ctrl+C まで継続、既定: 15.0）',
    )
    parser.add_argument(
        '--calibration',
        type=Path,
        default=None,
        help='sweep モードで正規化値を同時表示するためのキャリブレーション JSON',
    )
    parser.add_argument('--ain1', type=int, default=6, help='左モータ AIN1 (BCM)')
    parser.add_argument('--ain2', type=int, default=5, help='左モータ AIN2 (BCM)')
    parser.add_argument('--bin1', type=int, default=26, help='右モータ BIN1 (BCM)')
    parser.add_argument('--bin2', type=int, default=27, help='右モータ BIN2 (BCM)')
    parser.add_argument(
        '--output-dir',
        type=Path,
        default=Path(__file__).resolve().parent / 'logs',
        help='CSV/JSON 保存先フォルダ（既定: dev13/logs）',
    )
    parser.add_argument(
        '--yes',
        action='store_true',
        help='Enter キー待ち確認をスキップする',
    )
    parser.add_argument(
        '--start-delay',
        type=float,
        default=0.0,
        help='最初の計測前に待機する秒数 [s]（既定: 0.0）',
    )
    parser.add_argument(
        '--step-wait',
        type=float,
        default=0.0,
        help='--yes 実行時に次の位置へ車体を移すためのステップ間待機秒数 [s]（既定: 0.0）',
    )
    parser.add_argument(
        '--led-pin',
        type=int,
        default=None,
        help='状態通知用 LED の BCM ピン番号（例: 23。点滅=移動待機、点灯=計測中）',
    )
    parser.add_argument(
        '--auto-poweroff',
        action='store_true',
        help='CSV/JSON 保存完了後に自動で sudo poweroff を実行する',
    )
    return parser


def main(argv=None):
    """メイン関数。"""
    parser = build_parser()
    args = parser.parse_args(argv)

    interval_s = args.interval
    if interval_s is None:
        interval_s = 0.05 if args.mode == 'static' else 0.02
    if interval_s <= 0.0:
        parser.error('--interval は正の数を指定してください')
    if args.start_delay < 0.0 or args.step_wait < 0.0:
        parser.error('--start-delay と --step-wait は 0 以上を指定してください')

    if args.mode == 'static':
        if args.samples <= 0:
            parser.error('--samples は正の整数を指定してください')
        if not (-1.0 <= args.noise_pwm <= 1.0):
            parser.error('--noise-pwm は -1.0 ～ 1.0 の範囲で指定してください')
        run_static_test(
            samples=args.samples,
            interval_s=interval_s,
            black_mode=args.black_mode,
            include_ambient=args.include_ambient,
            check_motor_noise=args.check_motor_noise,
            noise_pwm=args.noise_pwm,
            ain1=args.ain1,
            ain2=args.ain2,
            bin1=args.bin1,
            bin2=args.bin2,
            output_dir=args.output_dir,
            yes=args.yes,
            start_delay_s=args.start_delay,
            step_wait_s=args.step_wait,
            led_pin=args.led_pin,
            auto_poweroff=args.auto_poweroff,
        )
    else:
        run_sweep_test(
            duration_s=args.duration,
            interval_s=interval_s,
            calibration_path=args.calibration,
            output_dir=args.output_dir,
            start_delay_s=args.start_delay,
            led_pin=args.led_pin,
            auto_poweroff=args.auto_poweroff,
        )


if __name__ == '__main__':
    main()
