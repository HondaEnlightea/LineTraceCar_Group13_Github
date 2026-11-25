#!/usr/bin/python3
# coding: UTF-8
"""
ライントレーサー（スレッド版クラス実装）

「電子情報通信設計製図」新潟大学工学部工学科電子情報通信プログラム

元の走行制御に、走行データのCSV記録と終了後のグラフ作成を追加しました。
記録処理は eiclab_line_follower_log.py、グラフ作成処理は
plot_eiclab_line_follower_log.py に分けています。

参考サイト
        https://gpiozero.readthedocs.io/en/stable/index.html
        https://gpiozero.readthedocs.io/en/stable/recipes_advanced.html#bluedot-robot

実行例:
    python3 eiclab_line_follower_advanced.py
"""
import numpy as np
import gpiozero
from gpiozero import MCP3004, Robot
import signal
import sys
from subprocess import CalledProcessError
from threading import Event

from eiclab_line_follower_log import RunLogger, plot_run_log

class LineFollower:

        def __init__(self,photorefs,run_logger=None,motors=None,stop_event=None):
                self.prs = photorefs
                self.run_logger = run_logger
                self.motors = motors
                self.stop_event = stop_event
                self.error = None
                
        def prs2mtrs(self):
                """ フォトリフレクタの値をモーター制御の強度値に変換 """
                # フォトリフレクタの値を読み出しとベクトル化
                vec_x = np.array([ self.prs[idx].value \
                                 for idx in range(len(self.prs)) ])
                
                # モーター制御の強度値を計算（ここを工夫）
                mat_A = np.array([[0.4, 0.3, 0.2, 0.1],\
                                  [0.1, 0.2, 0.3, 0.4]])
                vec_y = np.dot(mat_A,vec_x)

                # 出力範囲を[-1,1]に直して出力
                left, right = vec_y[0], vec_y[1]
                left, right = clamped(left),clamped(right)
                # 追加機能: センサー値とモーター指令値をロガーへ渡し、CSVに記録する。
                if self.run_logger is not None:
                        self.run_logger.record(vec_x.tolist(),left,right)
                return (left,right)
                  
        def line_follow(self):
                while True:
                        try:
                                motor_values = self.prs2mtrs()
                        except Exception as exc:
                                self.error = exc
                                try:
                                        if self.motors is not None:
                                                self.motors.stop()
                                finally:
                                        if self.stop_event is not None:
                                                self.stop_event.set()
                                raise
                        yield motor_values

def clamped(v):
        return max(-1,min(1,v))

def main():
        """ メイン関数 """
        # モータードライバ接続ピン
        PIN_AIN1 = 6
        PIN_AIN2 = 5
        PIN_BIN1 = 26
        PIN_BIN2 = 27

        # A/D変換チャネル数
        NUM_CH = 4

        # 追加機能: CSVと実行ログの保存先を用意する。
        run_logger = RunLogger()
        motors = None
        photorefs = []
        recording_started = False
        stop_event = Event()
        previous_sigterm = signal.getsignal(signal.SIGTERM)
        signal.signal(signal.SIGTERM, lambda _signum, _frame: stop_event.set())
        lf = None
        try:
                run_logger.log.info('Program started')
                motors = Robot(left=(PIN_AIN1,PIN_AIN2), \
                               right=(PIN_BIN1,PIN_BIN2)) #, \
                               # pwm=True)
                motors.stop()

                # フォトリフレクタ（複数）設定（A/D変換）
                for idx in range(NUM_CH):
                        photorefs.append(MCP3004(channel=idx))
                initial_values = [ pr.value for pr in photorefs ]
                run_logger.log.info('Sensors initialized: %s', initial_values)

                lf = LineFollower(photorefs,run_logger,motors,stop_event)
                # センサー・モーターの準備後に記録を開始し、走行データの時刻計測を始める。
                run_logger.start()
                motors.source = lf.line_follow()
                recording_started = True
                run_logger.log.info('Driving started')

                while not stop_event.wait(0.25):
                        if run_logger.error is not None:
                                raise RuntimeError('CSV logging failed') from run_logger.error
                if lf.error is not None:
                        raise RuntimeError('Sensor or control loop failed') from lf.error
                if run_logger.error is not None:
                        raise RuntimeError('CSV logging failed') from run_logger.error
        except KeyboardInterrupt:
                run_logger.log.info('KeyboardInterrupt received')
        except Exception:
                run_logger.log.exception('Program failed')
                raise
        finally:
                stop_event.set()
                try:
                        if motors is not None:
                                motors.stop()
                                run_logger.log.info('Motors stopped')
                finally:
                        try:
                                for pr in photorefs:
                                        pr.close()
                        finally:
                                try:
                                        if motors is not None:
                                                motors.close()
                                finally:
                                        try:
                                                try:
                                                        # 追加機能: CSVを確定してから、そのCSVを使って走行グラフを作る。
                                                        run_logger.stop_recording()
                                                        if recording_started:
                                                                try:
                                                                        graph_path = plot_run_log(run_logger.csv_path)
                                                                except (OSError, CalledProcessError):
                                                                        run_logger.log.exception('Automatic graph generation failed')
                                                                        print('グラフの自動作成に失敗しました。実行ログを確認してください。', file=sys.stderr)
                                                                else:
                                                                        run_logger.log.info('Graph saved: %s', graph_path)
                                                                        print('グラフを保存しました: {}'.format(graph_path))
                                                finally:
                                                        run_logger.close()
                                        finally:
                                                signal.signal(signal.SIGTERM, previous_sigterm)

if __name__ == '__main__':
        main()
