# coding: UTF-8
"""
特性試験スクリプト（モータ回転数・フォトリフレクタ感度・描画）のユニットテスト
"""

import csv
import json
import sys
import tempfile
import unittest
from pathlib import Path

from pi.characterization import (
    measure_motor_characteristics as motor_char,
    measure_sensor_characteristics as sensor_char,
)
from pc import plot_characteristics, plot_rotary_encoder


class FakeClock:
    """sleep 呼び出しに連動して決定的・高速に時刻を進める仮想クロック。"""

    def __init__(self, start=100.0):
        self.now = start

    def __call__(self):
        return self.now

    def advance(self, dt):
        self.now += dt


class FakePinDevice:
    """DigitalInputDevice の最小モック。"""

    def __init__(self, pin):
        self.pin = pin
        self.when_activated = None
        self.when_deactivated = None
        self.closed = False

    def trigger(self):
        if self.when_activated is not None:
            self.when_activated()

    def close(self):
        self.closed = True


class FakeRobot:
    """gpiozero.Robot のモック。"""

    def __init__(self):
        self.commands = []
        self.stopped = False
        self.closed = False
        self.current = (0.0, 0.0)

    @property
    def value(self):
        return self.current

    @value.setter
    def value(self, val):
        self.current = val
        self.commands.append(val)

    def stop(self):
        self.current = (0.0, 0.0)
        self.stopped = True

    def close(self):
        self.closed = True


class FakeEncoder:
    """ロボットの PWM 指令値に比例してエッジが進む仮想エンコーダ。"""

    def __init__(self, pin, rate_hz_per_pwm, clock, cpr=32):
        self.pin = pin
        self.rate_hz_per_pwm = rate_hz_per_pwm
        self.clock = clock
        self.cpr = cpr
        self.total_edges = 0
        self.rpm = 0.0
        self.closed = False
        self.reset_count = 0
        self.last_t = clock()
        self.robot = None

    def reset(self):
        self.total_edges = 0
        self.rpm = 0.0
        self.reset_count += 1
        self.last_t = self.clock()

    def snapshot(self):
        now = self.clock()
        dt = max(0.0, now - self.last_t)
        self.last_t = now
        cmd = abs(self.robot.current[0]) if self.robot is not None else 0.0
        if cmd > 0.0:
            edge_rate = cmd * self.rate_hz_per_pwm
            self.total_edges += int(round(edge_rate * dt))
            self.rpm = 60.0 * edge_rate / float(self.cpr)
        else:
            self.rpm = 0.0
        return self.total_edges, self.rpm

    def close(self):
        self.closed = True


class InterruptingEncoder(FakeEncoder):
    """2 回目の reset 時に KeyboardInterrupt を発生させるモック。"""

    def reset(self):
        super().reset()
        if self.reset_count >= 2:
            raise KeyboardInterrupt


class FakeADC:
    """MCP3004 のモック。"""

    def __init__(self, channel, provider):
        self.channel = channel
        self.provider = provider
        self.closed = False

    @property
    def value(self):
        return self.provider(self.channel)

    @property
    def raw_value(self):
        return int(round(self.value * 1023))

    def close(self):
        self.closed = True


class FakeLED:
    """gpiozero.LED の状態遷移記録用モック。"""

    def __init__(self, pin):
        self.pin = pin
        self.events = []
        self.closed = False

    def on(self):
        self.events.append('on')

    def off(self):
        self.events.append('off')

    def blink(self, **_kwargs):
        self.events.append('blink')

    def close(self):
        self.closed = True
        self.events.append('close')


class TestCharacterizationEncoder(unittest.TestCase):
    def test_edge_counting_glitch_filter_reset_and_timeout(self):
        clock = FakeClock(0.0)
        enc = motor_char.CharacterizationEncoder(
            17,
            cpr=32,
            edges_per_calc=4,
            timeout_s=0.5,
            min_edge_interval_s=0.001,
            clock=clock,
            device_cls=FakePinDevice,
        )
        try:
            # 最初のエッジで基準時刻が記録される
            enc._on_edge()
            edges, rpm = enc.snapshot()
            self.assertEqual(edges, 1)
            self.assertEqual(rpm, 0.0)

            # 0.0002 s 後のグリッチは無視されるべき
            clock.advance(0.0002)
            enc._on_edge()
            self.assertEqual(enc.snapshot()[0], 1)

            # 最初の有効エッジを t=0.01 s に合わせ、合計 dt = 0.04 s で 4 エッジ進める -> RPM = 60 * (4/0.04) / 32 = 187.5
            clock.advance(0.0098)
            enc._on_edge()
            for _ in range(3):
                clock.advance(0.01)
                enc._on_edge()

            edges, rpm = enc.snapshot()
            self.assertEqual(edges, 5)
            self.assertAlmostEqual(rpm, 187.5, places=3)

            # タイムアウト超過後は 0.0 rpm を返すがエッジ数は保持される
            clock.advance(0.6)
            edges_after_to, rpm_after_to = enc.snapshot()
            self.assertEqual(edges_after_to, 5)
            self.assertEqual(rpm_after_to, 0.0)

            # reset で全状態がクリアされる
            enc.reset()
            self.assertEqual(enc.snapshot(), (0, 0.0))
        finally:
            enc.close()
        self.assertTrue(enc.dev.closed)


class TestMotorCharacterization(unittest.TestCase):
    def test_up_down_and_reverse_sweep_produces_valid_csv_and_scales(self):
        clock = FakeClock(0.0)
        robot = FakeRobot()
        # 左: 160 Hz/PWM (=300 rpm at 1.0), 右: 192 Hz/PWM (=360 rpm at 1.0)
        enc_l = FakeEncoder(17, 160.0, clock)
        enc_r = FakeEncoder(22, 192.0, clock)
        enc_l.robot = robot
        enc_r.robot = robot

        def enc_factory(pin):
            return enc_l if pin == 17 else enc_r

        with tempfile.TemporaryDirectory() as tmpdir:
            res = motor_char.run_motor_test(
                pwms=[0.4, 0.8],
                up_down=True,
                include_reverse=True,
                settle_s=0.2,
                measure_s=0.5,
                rest_s=0.1,
                sample_interval_s=0.1,
                output_dir=tmpdir,
                yes=True,
                robot_factory=lambda: robot,
                encoder_factory=enc_factory,
                clock=clock,
                sleep_fn=clock.advance,
            )

            self.assertFalse(res['aborted'])
            self.assertTrue(robot.stopped)
            self.assertTrue(robot.closed)
            self.assertTrue(enc_l.closed)
            self.assertTrue(enc_r.closed)

            # [0.4, 0.8, 0.4, -0.4, -0.8, -0.4] の 6 ステップ
            self.assertEqual(len(res['summary_rows']), 6)
            cmds = [r['motor_command'] for r in res['summary_rows']]
            self.assertEqual(cmds, [0.4, 0.8, 0.4, -0.4, -0.8, -0.4])

            # サマリ CSV の数値検証（L=300*0.8=240 rpm, R=360*0.8=288 rpm -> 速い右側を落として scale=(1.0, 0.8333)）
            with res['summary_path'].open('r', encoding='utf-8') as f:
                summary_csv_rows = list(csv.DictReader(f))
            self.assertEqual(len(summary_csv_rows), 6)
            step2 = summary_csv_rows[1]
            self.assertAlmostEqual(float(step2['rpm_left_count']), 240.0, delta=15.0)
            self.assertAlmostEqual(float(step2['rpm_right_count']), 288.0, delta=15.0)
            self.assertAlmostEqual(float(step2['pwm_scale_left']), 1.0, places=3)
            self.assertLess(float(step2['pwm_scale_right']), 0.90)

            # raw CSV が既存の plot_rotary_encoder.read_measurements と互換であること
            times, raw_l, raw_r = plot_rotary_encoder.read_measurements(res['raw_path'])
            self.assertGreater(len(times), 10)
            self.assertEqual(len(raw_l), len(times))
            self.assertEqual(len(raw_r), len(times))

    def test_keyboard_interrupt_stops_and_closes_devices(self):
        clock = FakeClock(0.0)
        robot = FakeRobot()
        enc_l = InterruptingEncoder(17, 160.0, clock)
        enc_r = FakeEncoder(22, 160.0, clock)
        enc_l.robot = robot
        enc_r.robot = robot

        with tempfile.TemporaryDirectory() as tmpdir:
            res = motor_char.run_motor_test(
                pwms=[0.4, 0.8],
                settle_s=0.1,
                measure_s=0.3,
                rest_s=0.1,
                sample_interval_s=0.1,
                output_dir=tmpdir,
                yes=True,
                robot_factory=lambda: robot,
                encoder_factory=lambda pin: enc_l if pin == 17 else enc_r,
                clock=clock,
                sleep_fn=clock.advance,
            )

        self.assertTrue(res['aborted'])
        self.assertEqual(len(res['summary_rows']), 1)
        self.assertTrue(robot.stopped)
        self.assertTrue(robot.closed)
        self.assertTrue(enc_l.closed)
        self.assertTrue(enc_r.closed)

    def test_headless_start_delay_led_and_auto_poweroff(self):
        clock = FakeClock(0.0)
        robot = FakeRobot()
        enc_l = FakeEncoder(17, 160.0, clock)
        enc_r = FakeEncoder(22, 160.0, clock)
        enc_l.robot = robot
        enc_r.robot = robot

        leds = []
        poweroff_calls = []

        def led_factory(pin):
            led = FakeLED(pin)
            leds.append(led)
            return led

        with tempfile.TemporaryDirectory() as tmpdir:
            res = motor_char.run_motor_test(
                pwms=[0.4],
                settle_s=0.1,
                measure_s=0.2,
                rest_s=0.1,
                sample_interval_s=0.1,
                output_dir=tmpdir,
                yes=True,
                start_delay_s=0.5,
                led_pin=23,
                auto_poweroff=True,
                robot_factory=lambda: robot,
                encoder_factory=lambda pin: enc_l if pin == 17 else enc_r,
                led_factory=led_factory,
                poweroff_fn=lambda: poweroff_calls.append('poweroff'),
                clock=clock,
                sleep_fn=clock.advance,
            )

        self.assertFalse(res['aborted'])
        self.assertEqual(poweroff_calls, ['poweroff'])
        self.assertEqual(len(leds), 1)
        self.assertTrue(leds[0].closed)
        self.assertEqual(leds[0].events, ['blink', 'on', 'off', 'close'])
        self.assertGreaterEqual(clock(), 0.9)

    def test_auto_poweroff_skipped_when_aborted(self):
        clock = FakeClock(0.0)
        robot = FakeRobot()
        enc_l = InterruptingEncoder(17, 160.0, clock)
        enc_r = FakeEncoder(22, 160.0, clock)
        enc_l.robot = robot
        enc_r.robot = robot
        poweroff_calls = []

        with tempfile.TemporaryDirectory() as tmpdir:
            res = motor_char.run_motor_test(
                pwms=[0.4, 0.8],
                settle_s=0.1,
                measure_s=0.2,
                rest_s=0.1,
                sample_interval_s=0.1,
                output_dir=tmpdir,
                yes=True,
                auto_poweroff=True,
                robot_factory=lambda: robot,
                encoder_factory=lambda pin: enc_l if pin == 17 else enc_r,
                poweroff_fn=lambda: poweroff_calls.append('poweroff'),
                clock=clock,
                sleep_fn=clock.advance,
            )

        self.assertTrue(res['aborted'])
        self.assertEqual(poweroff_calls, [])


class TestSensorCharacterization(unittest.TestCase):
    def test_static_each_mode_symmetry_and_warnings(self):
        clock = FakeClock(0.0)
        robot = FakeRobot()
        # 対話プロンプトの進行に応じて白/黒/外乱/ノイズ状態を模擬
        phase_values = [
            # 0: white
            [0.85, 0.84, 0.86, 0.85],
            # 1..4: black_ch0..ch3（ch3 が黒で下がり切らない非対称・低コントラスト設定）
            [0.15, 0.83, 0.85, 0.84],
            [0.84, 0.16, 0.85, 0.84],
            [0.85, 0.83, 0.18, 0.84],
            [0.84, 0.84, 0.85, 0.68],
            # 5: ambient
            [0.05, 0.05, 0.05, 0.05],
        ]
        state = {'idx': -1, 'noise_tick': 0}

        def prompt_handler(_msg):
            state['idx'] += 1
            return ''

        def value_provider(ch):
            idx = state['idx']
            if idx < len(phase_values):
                return phase_values[idx][ch]
            # motor noise step: motor_on の間に少しゆらぎを加える
            state['noise_tick'] += 1
            base = 0.85
            if robot.current[0] != 0.0:
                return base + (0.02 if state['noise_tick'] % 2 == 0 else -0.02)
            return base

        created_adcs = []

        def adc_factory(ch):
            dev = FakeADC(ch, value_provider)
            created_adcs.append(dev)
            return dev

        with tempfile.TemporaryDirectory() as tmpdir:
            res = sensor_char.run_static_test(
                samples=10,
                interval_s=0.05,
                black_mode='each',
                include_ambient=True,
                check_motor_noise=True,
                noise_pwm=0.5,
                output_dir=tmpdir,
                yes=False,
                adc_factory=adc_factory,
                robot_factory=lambda: robot,
                input_fn=prompt_handler,
                clock=clock,
                sleep_fn=clock.advance,
            )

            self.assertFalse(res['aborted'])
            self.assertTrue(all(d.closed for d in created_adcs))
            self.assertTrue(robot.stopped)
            self.assertTrue(robot.closed)
            self.assertEqual(len(res['channel_stats']), 4)

            # ch3 はコントラスト 0.17、ch0 は 0.70 なので外側非対称警告が出るはず
            joined_warnings = '\n'.join(res['warnings'])
            self.assertIn('左右非対称', joined_warnings)
            self.assertIsNotNone(res['motor_noise'])
            self.assertEqual(len(res['motor_noise']['channels']), 4)

            # calibration.json が有効に書き出されているか確認
            cal = json.loads(res['calibration_path'].read_text(encoding='utf-8'))
            self.assertAlmostEqual(cal['channels']['0']['white_mean'], 0.85, places=3)
            self.assertAlmostEqual(cal['channels']['0']['black_mean'], 0.15, places=3)
            self.assertAlmostEqual(cal['channels']['0']['ambient_mean'], 0.05, places=3)

    def test_static_all_mode_and_stuck_channel_warning(self):
        clock = FakeClock(0.0)
        # ch2 が 0.01 に張り付いた状態を模擬
        mode_map = {'step': -1}

        def value_provider(ch):
            if mode_map['step'] == 0:
                return [0.90, 0.88, 0.01, 0.89][ch]
            return [0.10, 0.12, 0.01, 0.11][ch]

        def next_prompt(_msg):
            mode_map['step'] += 1
            return ''

        with tempfile.TemporaryDirectory() as tmpdir:
            res = sensor_char.run_static_test(
                samples=6,
                interval_s=0.02,
                black_mode='all',
                output_dir=tmpdir,
                yes=False,
                adc_factory=lambda ch: FakeADC(ch, value_provider),
                input_fn=next_prompt,
                clock=clock,
                sleep_fn=clock.advance,
            )

        self.assertTrue(any('飽和/固着' in w for w in res['warnings']))

    def test_sweep_mode_writes_expected_samples(self):
        clock = FakeClock(0.0)
        created_adcs = []

        def adc_factory(ch):
            dev = FakeADC(ch, lambda c: 0.2 * (c + 1))
            created_adcs.append(dev)
            return dev

        with tempfile.TemporaryDirectory() as tmpdir:
            res = sensor_char.run_sweep_test(
                duration_s=0.25,
                interval_s=0.05,
                output_dir=tmpdir,
                adc_factory=adc_factory,
                clock=clock,
                sleep_fn=clock.advance,
            )

            self.assertFalse(res['aborted'])
            self.assertEqual(res['sample_count'], 5)
            self.assertTrue(all(d.closed for d in created_adcs))
            with res['csv_path'].open('r', encoding='utf-8') as f:
                rows = list(csv.DictReader(f))
            self.assertEqual(len(rows), 5)
            self.assertAlmostEqual(float(rows[0]['s0']), 0.2, places=3)

    def test_headless_static_step_wait_led_and_auto_poweroff(self):
        clock = FakeClock(0.0)
        leds = []
        poweroff_calls = []

        def fail_if_prompted(_msg):
            raise AssertionError('yes=True なのに input_fn が呼ばれました')

        def led_factory(pin):
            led = FakeLED(pin)
            leds.append(led)
            return led

        with tempfile.TemporaryDirectory() as tmpdir:
            res = sensor_char.run_static_test(
                samples=3,
                interval_s=0.05,
                black_mode='each',
                output_dir=tmpdir,
                yes=True,
                start_delay_s=0.5,
                step_wait_s=0.5,
                led_pin=23,
                auto_poweroff=True,
                adc_factory=lambda ch: FakeADC(ch, lambda c: 0.8 if clock() < 0.7 else 0.2),
                led_factory=led_factory,
                poweroff_fn=lambda: poweroff_calls.append('poweroff'),
                input_fn=fail_if_prompted,
                clock=clock,
                sleep_fn=clock.advance,
            )

        self.assertFalse(res['aborted'])
        self.assertEqual(poweroff_calls, ['poweroff'])
        self.assertEqual(len(leds), 1)
        self.assertTrue(leds[0].closed)
        # 1回(white前) + 4回(black_ch0..3前) の blink -> on が交互に入る
        self.assertEqual(leds[0].events.count('blink'), 5)
        self.assertEqual(leds[0].events.count('on'), 5)
        self.assertEqual(leds[0].events[-2:], ['off', 'close'])

    def test_sweep_headless_start_delay_and_auto_poweroff(self):
        clock = FakeClock(0.0)
        leds = []
        poweroff_calls = []

        def led_factory(pin):
            led = FakeLED(pin)
            leds.append(led)
            return led

        with tempfile.TemporaryDirectory() as tmpdir:
            res = sensor_char.run_sweep_test(
                duration_s=0.2,
                interval_s=0.05,
                output_dir=tmpdir,
                start_delay_s=0.5,
                led_pin=23,
                auto_poweroff=True,
                adc_factory=lambda ch: FakeADC(ch, lambda c: 0.4),
                led_factory=led_factory,
                poweroff_fn=lambda: poweroff_calls.append('poweroff'),
                clock=clock,
                sleep_fn=clock.advance,
            )

        self.assertFalse(res['aborted'])
        self.assertEqual(res['sample_count'], 4)
        self.assertEqual(poweroff_calls, ['poweroff'])
        self.assertEqual(leds[0].events, ['blink', 'on', 'off', 'close'])


class TestPlotCharacteristics(unittest.TestCase):
    def test_plots_generated_for_motor_and_sensor_outputs(self):
        clock = FakeClock(0.0)
        robot = FakeRobot()
        enc_l = FakeEncoder(17, 160.0, clock)
        enc_r = FakeEncoder(22, 180.0, clock)
        enc_l.robot = robot
        enc_r.robot = robot

        with tempfile.TemporaryDirectory() as tmpdir:
            motor_res = motor_char.run_motor_test(
                pwms=[0.3, 0.6],
                settle_s=0.1,
                measure_s=0.3,
                rest_s=0.1,
                sample_interval_s=0.1,
                output_dir=tmpdir,
                yes=True,
                robot_factory=lambda: robot,
                encoder_factory=lambda pin: enc_l if pin == 17 else enc_r,
                clock=clock,
                sleep_fn=clock.advance,
            )

            motor_png = plot_characteristics.plot_motor_summary(motor_res['summary_path'])
            self.assertTrue(motor_png.is_file())
            self.assertGreater(motor_png.stat().st_size, 0)

            sensor_res = sensor_char.run_static_test(
                samples=5,
                interval_s=0.02,
                black_mode='all',
                output_dir=tmpdir,
                yes=True,
                adc_factory=lambda ch: FakeADC(ch, lambda c: 0.8 if clock() < 0.6 else 0.1),
                clock=clock,
                sleep_fn=clock.advance,
            )
            sensor_png = plot_characteristics.plot_sensor_summary(sensor_res['summary_path'])
            self.assertTrue(sensor_png.is_file())
            self.assertGreater(sensor_png.stat().st_size, 0)

            sweep_res = sensor_char.run_sweep_test(
                duration_s=0.2,
                interval_s=0.05,
                calibration_path=sensor_res['calibration_path'],
                output_dir=tmpdir,
                adc_factory=lambda ch: FakeADC(ch, lambda c: 0.5),
                clock=clock,
                sleep_fn=clock.advance,
            )
            sweep_png = plot_characteristics.plot_sensor_sweep(
                sweep_res['csv_path'],
                calibration_json=sensor_res['calibration_path'],
            )
            self.assertTrue(sweep_png.is_file())
            self.assertGreater(sweep_png.stat().st_size, 0)


if __name__ == '__main__':
    unittest.main()
