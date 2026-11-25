#!/usr/bin/python3
# coding: UTF-8
"""
車体の特性試験用プログラム（モータ・タイヤ回転数測定と左右差確認）

Raspberry Pi Zero 2 W での動作を想定し、標準ライブラリと gpiozero のみで動作します。
ラズパイ側では測定中の NumPy / Matplotlib 読み込みを行わず、軽量に CSV へ記録するまでを担当します。
グラフ描画はこのパソコン（Windows PC）側で dev13/pc/plot_characteristics.py
（または dev13/pc/plot_rotary_encoder.py）を実行して行ってください。

実行例（EicDesignLab フォルダから）:
    python3 dev13/pi/measure_motor_characteristics.py
    python3 dev13/pi/measure_motor_characteristics.py --up-down --condition noload
    python3 dev13/pi/measure_motor_characteristics.py --pwms 0.2,0.4,0.6,0.8,1.0 --measure 4.0
"""

import argparse
import csv
import os
import signal
import statistics
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from threading import Event, Lock

from gpiozero import DigitalInputDevice, LED, Robot


DEFAULT_PWMS = (0.15, 0.20, 0.30, 0.40, 0.60, 0.80, 1.00)

RAW_HEADER = [
    'time_s',
    'step_index',
    'phase',
    'motor_command',
    'rpm_left',
    'rpm_right',
    'edges_left',
    'edges_right',
    'condition',
]

SUMMARY_HEADER = [
    'step_index',
    'motor_command',
    'condition',
    'settle_s',
    'measure_s',
    'edges_left',
    'edges_right',
    'rpm_left_count',
    'rpm_right_count',
    'rpm_left_mean',
    'rpm_left_std',
    'rpm_right_mean',
    'rpm_right_std',
    'rpm_diff_r_minus_l',
    'rpm_ratio_r_over_l',
    'pwm_scale_left',
    'pwm_scale_right',
]


class CharacterizationEncoder:
    """
    特性試験用の軽量フォトインタラプタ式ロータリーエンコーダ。

    既存の rotary_encoder.py と異なり、以下の点に対応している:
    - スレッド安全な排他制御（threading.Lock）
    - グリッチ除去（min_edge_interval_s 未満の極短パルスを無視）
    - EMA による遅延を排除した瞬時 RPM + 区間総エッジ数の記録
    - PWM ステップ間の reset() による状態クリア
    """

    def __init__(
        self,
        pin,
        cpr=32,
        edges_per_calc=4,
        timeout_s=0.5,
        min_edge_interval_s=0.0005,
        clock=time.monotonic,
        device_cls=DigitalInputDevice,
    ):
        self.pin = pin
        self.cpr = float(cpr)
        self.edges_per_calc = int(edges_per_calc)
        self.timeout_s = float(timeout_s)
        self.min_edge_interval_s = float(min_edge_interval_s)
        self._clock = clock
        self._lock = Lock()

        self._total_edges = 0
        self._rpm = 0.0
        self._t0 = None
        self._k = 0
        self._last_edge = None

        self.dev = device_cls(pin)
        self.dev.when_activated = self._on_edge
        self.dev.when_deactivated = self._on_edge

    def _on_edge(self):
        t = self._clock()
        with self._lock:
            if (
                self._last_edge is not None
                and self.min_edge_interval_s > 0.0
                and (t - self._last_edge) < self.min_edge_interval_s
            ):
                return

            self._last_edge = t
            self._total_edges += 1

            if self._t0 is None:
                self._t0 = t
                self._k = 0
                return

            self._k += 1
            if self._k >= self.edges_per_calc:
                dt = t - self._t0
                if dt > 0.0 and self.cpr > 0.0:
                    self._rpm = 60.0 * (self.edges_per_calc / dt) / self.cpr
                self._t0 = t
                self._k = 0

    def reset(self):
        """ステップ切り替え時にカウントと計算状態をリセットする。"""
        with self._lock:
            self._total_edges = 0
            self._rpm = 0.0
            self._t0 = None
            self._k = 0
            self._last_edge = None

    def snapshot(self):
        """現在の (累計エッジ数, 瞬時RPM) を原子的に返す。"""
        now = self._clock()
        with self._lock:
            if (
                self._last_edge is not None
                and (now - self._last_edge) > self.timeout_s
            ):
                rpm = 0.0
            else:
                rpm = self._rpm
            return self._total_edges, rpm

    def read_rpm(self):
        """現在の瞬時 RPM を返す。"""
        return self.snapshot()[1]

    def close(self):
        """終了時にコールバックを解除しデバイスを閉じる。"""
        try:
            self.dev.when_activated = None
            self.dev.when_deactivated = None
        finally:
            self.dev.close()


def parse_pwm_list(raw_text):
    """カンマ区切り文字列から正の PWM 指令値リストを解析・検証する。"""
    items = [part.strip() for part in raw_text.split(',') if part.strip()]
    if not items:
        raise ValueError('PWM リストが空です')
    values = []
    for item in items:
        val = float(item)
        if not (0.0 < val <= 1.0):
            raise ValueError(
                'PWM 指令値は 0 < pwm <= 1.0 の範囲で指定してください: {}'.format(item)
            )
        values.append(val)
    return values


def build_pwm_sequence(base_pwms, up_down=False, include_reverse=False):
    """試験用の PWM シーケンスを構築する。"""
    fwd = list(base_pwms)
    if up_down and len(fwd) > 1:
        fwd.extend(reversed(fwd[:-1]))
    seq = list(fwd)
    if include_reverse:
        seq.extend(-val for val in fwd)
    return seq


def _create_unique_files(output_dir, prefix):
    """既存ファイルを上書きしないユニークな CSV ファイルペアを作成する。"""
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    suffix = 0
    while True:
        stem = '{}_{}'.format(prefix, stamp)
        if suffix:
            stem = '{}_{:02d}'.format(stem, suffix)
        raw_path = output_dir / '{}_raw.csv'.format(stem)
        summary_path = output_dir / '{}_summary.csv'.format(stem)
        raw_file = None
        summary_file = None
        try:
            raw_file = raw_path.open('x', newline='', encoding='utf-8')
            summary_file = summary_path.open('x', newline='', encoding='utf-8')
            return raw_path, summary_path, raw_file, summary_file
        except FileExistsError:
            if raw_file is not None:
                raw_file.close()
                raw_path.unlink(missing_ok=True)
            if summary_file is not None:
                summary_file.close()
                summary_path.unlink(missing_ok=True)
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


def _compute_stats(values):
    """平均と標準偏差を計算する（要素不足時は 0.0）。"""
    if not values:
        return 0.0, 0.0
    mean_val = statistics.fmean(values)
    std_val = statistics.stdev(values) if len(values) >= 2 else 0.0
    return mean_val, std_val


def _compute_scale_factors(rpm_left, rpm_right):
    """速い側の車輪を減衰させて左右を揃えるスケール係数を返す。"""
    if rpm_left <= 0.0 or rpm_right <= 0.0:
        return 1.0, 1.0
    if rpm_left >= rpm_right:
        return rpm_right / rpm_left, 1.0
    return 1.0, rpm_left / rpm_right


def _run_sampling_phase(
    *,
    duration_s,
    sample_interval_s,
    step_index,
    phase_name,
    motor_cmd,
    condition,
    t_test_start,
    enc_left,
    enc_right,
    raw_writer,
    raw_file,
    stop_event,
    clock,
    sleep_fn,
):
    """指定時間だけドリフトなしの周期でエンコーダ値を記録する。"""
    if duration_s <= 0.0:
        return [], 0

    phase_start = clock()
    end_time = phase_start + duration_s
    next_tick = phase_start
    rpm_left_samples = []
    rpm_right_samples = []
    sample_count = 0

    while not stop_event.is_set():
        now = clock()
        edges_l, rpm_l = enc_left.snapshot()
        edges_r, rpm_r = enc_right.snapshot()
        elapsed_s = now - t_test_start

        raw_writer.writerow([
            '{:.3f}'.format(elapsed_s),
            step_index,
            phase_name,
            '{:.2f}'.format(motor_cmd),
            '{:.2f}'.format(rpm_l),
            '{:.2f}'.format(rpm_r),
            edges_l,
            edges_r,
            condition,
        ])
        sample_count += 1
        if sample_count % 10 == 0:
            raw_file.flush()

        if phase_name == 'steady':
            rpm_left_samples.append(rpm_l)
            rpm_right_samples.append(rpm_r)

        next_tick += sample_interval_s
        now_after = clock()
        remaining_total = end_time - now_after
        if remaining_total <= 1e-9:
            break
        if next_tick >= end_time - 1e-9:
            sleep_fn(remaining_total)
            break
        wait_s = next_tick - now_after
        if wait_s > 0.0:
            sleep_fn(wait_s)

    return list(zip(rpm_left_samples, rpm_right_samples)), sample_count


def _format_summary_table(summary_rows):
    """コンソール表示用のサマリ表と診断警告を作成する。"""
    lines = []
    header = (
        'Step | PWM   | L_cnt[rpm] | R_cnt[rpm] | R-L[rpm] | R/L   | Scale(L, R)'
    )
    lines.append(header)
    lines.append('-' * len(header))

    deadband_cmds = []
    max_asym_entry = None

    for row in summary_rows:
        rpm_l = row['rpm_left_count']
        rpm_r = row['rpm_right_count']
        diff = row['rpm_diff_r_minus_l']
        ratio = row['rpm_ratio_r_over_l']
        ratio_str = '{:.3f}'.format(ratio) if ratio is not None else '  N/A'
        lines.append(
            '{:4d} | {:+5.2f} | {:10.2f} | {:10.2f} | {:+8.2f} | {} | ({:.3f}, {:.3f})'.format(
                row['step_index'],
                row['motor_command'],
                rpm_l,
                rpm_r,
                diff,
                ratio_str,
                row['pwm_scale_left'],
                row['pwm_scale_right'],
            )
        )

        if rpm_l < 1.0 or rpm_r < 1.0:
            deadband_cmds.append(row['motor_command'])
        else:
            rel_asym = abs(diff) / max(rpm_l, rpm_r)
            if max_asym_entry is None or rel_asym > max_asym_entry[0]:
                max_asym_entry = (rel_asym, row['motor_command'], diff)

    warnings = []
    if deadband_cmds:
        cmds_txt = ', '.join('{:+.2f}'.format(c) for c in deadband_cmds)
        warnings.append(
            '不感帯/停止検出（回転数 < 1.0 rpm）: PWM = {}'.format(cmds_txt)
        )
    if max_asym_entry is not None and max_asym_entry[0] > 0.05:
        warnings.append(
            '左右回転数差が大きい条件があります: PWM={:+.2f} で相対差 {:.1f}% (R-L={:+.2f} rpm)'.format(
                max_asym_entry[1], max_asym_entry[0] * 100.0, max_asym_entry[2]
            )
        )
    return '\n'.join(lines), warnings


def run_motor_test(
    *,
    pwms=DEFAULT_PWMS,
    up_down=False,
    include_reverse=False,
    settle_s=1.5,
    measure_s=3.0,
    rest_s=1.5,
    sample_interval_s=0.1,
    cpr=32,
    edges_per_calc=4,
    timeout_s=0.5,
    glitch_ms=0.5,
    ain1=6,
    ain2=5,
    bin1=26,
    bin2=27,
    enc_left_pin=17,
    enc_right_pin=22,
    condition='noload',
    output_dir=None,
    yes=False,
    start_delay_s=0.0,
    led_pin=None,
    auto_poweroff=False,
    robot_factory=None,
    encoder_factory=None,
    led_factory=None,
    poweroff_fn=None,
    input_fn=input,
    clock=time.monotonic,
    sleep_fn=time.sleep,
):
    """モータ・ロータリーエンコーダ特性試験を実行して結果を返す。"""
    if output_dir is None:
        output_dir = Path(__file__).resolve().parent / 'logs'
    else:
        output_dir = Path(output_dir)

    if robot_factory is None:
        robot_factory = lambda: Robot(left=(ain1, ain2), right=(bin1, bin2))
    if encoder_factory is None:
        glitch_s = max(0.0, glitch_ms / 1000.0)
        encoder_factory = lambda pin: CharacterizationEncoder(
            pin,
            cpr=cpr,
            edges_per_calc=edges_per_calc,
            timeout_s=timeout_s,
            min_edge_interval_s=glitch_s,
            clock=clock,
        )

    pwm_sequence = build_pwm_sequence(
        pwms, up_down=up_down, include_reverse=include_reverse
    )

    if not yes:
        print('=== モータ・回転数特性試験 ===')
        print('条件: {}, ステップ数: {}, PWM列: {}'.format(
            condition, len(pwm_sequence), [round(p, 2) for p in pwm_sequence]
        ))
        print('注意: 車輪が浮いていること（空転時）または安全な走行スペースを確認してください。')
        input_fn('準備ができたら Enter キーを押してください（中止は Ctrl+C）: ')

    raw_path, summary_path, raw_file, summary_file = _create_unique_files(
        output_dir, 'motor_char'
    )
    raw_writer = csv.writer(raw_file)
    summary_writer = csv.writer(summary_file)
    raw_writer.writerow(RAW_HEADER)
    summary_writer.writerow(SUMMARY_HEADER)
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

    motors = None
    enc_left = None
    enc_right = None
    status_led = _StatusLED(led_pin, led_factory=led_factory)
    summary_rows = []
    aborted = False

    try:
        motors = robot_factory()
        motors.stop()
        enc_left = encoder_factory(enc_left_pin)
        enc_right = encoder_factory(enc_right_pin)

        _wait_with_countdown(
            start_delay_s, '開始前待機', status_led, stop_event, clock, sleep_fn
        )
        status_led.on()

        t_test_start = clock()

        for step_index, cmd in enumerate(pwm_sequence, start=1):
            if stop_event.is_set():
                aborted = True
                break

            print(
                '[Step {}/{}] PWM = {:+.2f} を計測中...'.format(
                    step_index, len(pwm_sequence), cmd
                )
            )
            enc_left.reset()
            enc_right.reset()
            motors.value = (cmd, cmd)

            _run_sampling_phase(
                duration_s=settle_s,
                sample_interval_s=sample_interval_s,
                step_index=step_index,
                phase_name='settle',
                motor_cmd=cmd,
                condition=condition,
                t_test_start=t_test_start,
                enc_left=enc_left,
                enc_right=enc_right,
                raw_writer=raw_writer,
                raw_file=raw_file,
                stop_event=stop_event,
                clock=clock,
                sleep_fn=sleep_fn,
            )
            if stop_event.is_set():
                aborted = True
                break

            t_steady_start = clock()
            edges_l_0, _ = enc_left.snapshot()
            edges_r_0, _ = enc_right.snapshot()

            steady_pairs, _ = _run_sampling_phase(
                duration_s=measure_s,
                sample_interval_s=sample_interval_s,
                step_index=step_index,
                phase_name='steady',
                motor_cmd=cmd,
                condition=condition,
                t_test_start=t_test_start,
                enc_left=enc_left,
                enc_right=enc_right,
                raw_writer=raw_writer,
                raw_file=raw_file,
                stop_event=stop_event,
                clock=clock,
                sleep_fn=sleep_fn,
            )

            t_steady_end = clock()
            edges_l_1, _ = enc_left.snapshot()
            edges_r_1, _ = enc_right.snapshot()

            dt_steady = max(t_steady_end - t_steady_start, 1e-9)
            d_edges_l = max(0, edges_l_1 - edges_l_0)
            d_edges_r = max(0, edges_r_1 - edges_r_0)

            rpm_l_count = 60.0 * (d_edges_l / float(cpr)) / dt_steady
            rpm_r_count = 60.0 * (d_edges_r / float(cpr)) / dt_steady

            l_samples = [pair[0] for pair in steady_pairs]
            r_samples = [pair[1] for pair in steady_pairs]
            rpm_l_mean, rpm_l_std = _compute_stats(l_samples)
            rpm_r_mean, rpm_r_std = _compute_stats(r_samples)

            rpm_diff = rpm_r_count - rpm_l_count
            rpm_ratio = (rpm_r_count / rpm_l_count) if rpm_l_count > 0.0 else None
            scale_l, scale_r = _compute_scale_factors(rpm_l_count, rpm_r_count)

            row_dict = {
                'step_index': step_index,
                'motor_command': cmd,
                'condition': condition,
                'settle_s': settle_s,
                'measure_s': dt_steady,
                'edges_left': d_edges_l,
                'edges_right': d_edges_r,
                'rpm_left_count': rpm_l_count,
                'rpm_right_count': rpm_r_count,
                'rpm_left_mean': rpm_l_mean,
                'rpm_left_std': rpm_l_std,
                'rpm_right_mean': rpm_r_mean,
                'rpm_right_std': rpm_r_std,
                'rpm_diff_r_minus_l': rpm_diff,
                'rpm_ratio_r_over_l': rpm_ratio,
                'pwm_scale_left': scale_l,
                'pwm_scale_right': scale_r,
            }
            summary_rows.append(row_dict)

            summary_writer.writerow([
                step_index,
                '{:.2f}'.format(cmd),
                condition,
                '{:.3f}'.format(settle_s),
                '{:.3f}'.format(dt_steady),
                d_edges_l,
                d_edges_r,
                '{:.2f}'.format(rpm_l_count),
                '{:.2f}'.format(rpm_r_count),
                '{:.2f}'.format(rpm_l_mean),
                '{:.2f}'.format(rpm_l_std),
                '{:.2f}'.format(rpm_r_mean),
                '{:.2f}'.format(rpm_r_std),
                '{:+.2f}'.format(rpm_diff),
                '{:.4f}'.format(rpm_ratio) if rpm_ratio is not None else '',
                '{:.4f}'.format(scale_l),
                '{:.4f}'.format(scale_r),
            ])
            _flush_and_sync(raw_file)
            _flush_and_sync(summary_file)

            motors.stop()
            if stop_event.is_set():
                aborted = True
                break

            _run_sampling_phase(
                duration_s=rest_s,
                sample_interval_s=sample_interval_s,
                step_index=step_index,
                phase_name='rest',
                motor_cmd=0.0,
                condition=condition,
                t_test_start=t_test_start,
                enc_left=enc_left,
                enc_right=enc_right,
                raw_writer=raw_writer,
                raw_file=raw_file,
                stop_event=stop_event,
                clock=clock,
                sleep_fn=sleep_fn,
            )
            _flush_and_sync(raw_file)

    except KeyboardInterrupt:
        aborted = True
        print('\nCtrl+C を検出しました。モータを停止して保存します。')
    finally:
        stop_event.set()
        try:
            if motors is not None:
                motors.stop()
        finally:
            try:
                if enc_left is not None:
                    enc_left.close()
            finally:
                try:
                    if enc_right is not None:
                        enc_right.close()
                finally:
                    try:
                        if motors is not None:
                            motors.close()
                    finally:
                        try:
                            status_led.close()
                        finally:
                            try:
                                _flush_and_sync(raw_file)
                                raw_file.close()
                            finally:
                                _flush_and_sync(summary_file)
                                summary_file.close()
                                if sigterm_registered:
                                    signal.signal(signal.SIGTERM, previous_sigterm)

    if summary_rows:
        table_text, warnings = _format_summary_table(summary_rows)
        print('\n=== 計測サマリ ===')
        print(table_text)
        for warning in warnings:
            print('[注意] {}'.format(warning))
    else:
        print('\n[注意] 完了した計測ステップがありません。')

    print('\n保存ファイル:')
    print('  生データ CSV : {}'.format(raw_path))
    print('  サマリ CSV   : {}'.format(summary_path))

    if auto_poweroff and not aborted:
        print('自動シャットダウンを実行します（緑色 LED が完全消灯してから電源を切ってください）。')
        if poweroff_fn is not None:
            poweroff_fn()
        else:
            subprocess.run(['sudo', 'poweroff'], check=False)

    return {
        'raw_path': raw_path,
        'summary_path': summary_path,
        'summary_rows': summary_rows,
        'aborted': aborted,
    }


def build_parser():
    """CLI 引数パーサを作成する。"""
    parser = argparse.ArgumentParser(
        description='左右モータとロータリーエンコーダの回転数・左右差を計測します'
    )
    parser.add_argument(
        '--pwms',
        default=','.join('{:.2f}'.format(v) for v in DEFAULT_PWMS),
        help='計測する正の PWM 指令値（カンマ区切り、既定: 0.15,0.20,0.30,0.40,0.60,0.80,1.00）',
    )
    parser.add_argument(
        '--up-down',
        action='store_true',
        help='上昇スイープ後に下降スイープも計測し、電池電圧降下やヒステリシスを確認する',
    )
    parser.add_argument(
        '--include-reverse',
        action='store_true',
        help='負の PWM（逆転）も追加で計測する（注: 単相エンコーダのため回転数は絶対値）',
    )
    parser.add_argument(
        '--settle',
        type=float,
        default=1.5,
        help='各ステップで定常状態になるまでの待機時間 [s]（既定: 1.5）',
    )
    parser.add_argument(
        '--measure',
        type=float,
        default=3.0,
        help='各ステップの定常計測時間 [s]（既定: 3.0）',
    )
    parser.add_argument(
        '--rest',
        type=float,
        default=1.5,
        help='ステップ間の停止時間 [s]（既定: 1.5）',
    )
    parser.add_argument(
        '--sample-interval',
        type=float,
        default=0.1,
        help='生データ CSV の記録周期 [s]（既定: 0.1）',
    )
    parser.add_argument(
        '--cpr',
        type=int,
        default=32,
        help='タイヤ1回転あたりのエッジ数（16スリット両エッジ=32、既定: 32）',
    )
    parser.add_argument(
        '--edges-per-calc',
        type=int,
        default=4,
        help='瞬時 RPM 計算に使うエッジ数（既定: 4）',
    )
    parser.add_argument(
        '--timeout',
        type=float,
        default=0.5,
        help='エッジが途絶えた際に 0 rpm とみなす時間 [s]（既定: 0.5）',
    )
    parser.add_argument(
        '--glitch-ms',
        type=float,
        default=0.5,
        help='チャタリング/ノイズとみなして無視する最小エッジ間隔 [ms]（既定: 0.5）',
    )
    parser.add_argument('--ain1', type=int, default=6, help='左モータ AIN1 (BCM)')
    parser.add_argument('--ain2', type=int, default=5, help='左モータ AIN2 (BCM)')
    parser.add_argument('--bin1', type=int, default=26, help='右モータ BIN1 (BCM)')
    parser.add_argument('--bin2', type=int, default=27, help='右モータ BIN2 (BCM)')
    parser.add_argument('--enc-left', type=int, default=17, help='左エンコーダピン (BCM)')
    parser.add_argument('--enc-right', type=int, default=22, help='右エンコーダピン (BCM)')
    parser.add_argument(
        '--condition',
        default='noload',
        help='計測条件ラベル（例: noload, floor など、既定: noload）',
    )
    parser.add_argument(
        '--output-dir',
        type=Path,
        default=Path(__file__).resolve().parent / 'logs',
        help='CSV 保存先フォルダ（既定: dev13/logs）',
    )
    parser.add_argument(
        '--yes',
        action='store_true',
        help='開始前の Enter 待ち確認をスキップする',
    )
    parser.add_argument(
        '--start-delay',
        type=float,
        default=0.0,
        help='開始前に待機する秒数 [s]（--yes と併用してヘッドレス実行する用、既定: 0.0）',
    )
    parser.add_argument(
        '--led-pin',
        type=int,
        default=None,
        help='状態通知用 LED の BCM ピン番号（例: 23。省略時は使用しない）',
    )
    parser.add_argument(
        '--auto-poweroff',
        action='store_true',
        help='CSV 保存と停止完了後に自動で sudo poweroff を実行する',
    )
    return parser


def main(argv=None):
    """メイン関数。"""
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        pwms = parse_pwm_list(args.pwms)
    except ValueError as exc:
        parser.error(str(exc))

    if args.settle < 0.0 or args.rest < 0.0 or args.measure <= 0.0 or args.start_delay < 0.0:
        parser.error('--settle, --rest, --start-delay は 0 以上、--measure は正の数を指定してください')
    if args.sample_interval <= 0.0 or args.cpr <= 0 or args.edges_per_calc <= 0:
        parser.error('--sample-interval, --cpr, --edges-per-calc は正の数を指定してください')

    run_motor_test(
        pwms=pwms,
        up_down=args.up_down,
        include_reverse=args.include_reverse,
        settle_s=args.settle,
        measure_s=args.measure,
        rest_s=args.rest,
        sample_interval_s=args.sample_interval,
        cpr=args.cpr,
        edges_per_calc=args.edges_per_calc,
        timeout_s=args.timeout,
        glitch_ms=args.glitch_ms,
        ain1=args.ain1,
        ain2=args.ain2,
        bin1=args.bin1,
        bin2=args.bin2,
        enc_left_pin=args.enc_left,
        enc_right_pin=args.enc_right,
        condition=args.condition,
        output_dir=args.output_dir,
        yes=args.yes,
        start_delay_s=args.start_delay,
        led_pin=args.led_pin,
        auto_poweroff=args.auto_poweroff,
    )


if __name__ == '__main__':
    main()
