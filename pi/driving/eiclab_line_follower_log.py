# coding: UTF-8
"""
Asynchronous CSV and execution logging for real line-follower runs.

メインプログラムからセンサー値と左右のモーター指令値を受け取り、
CSVへ非同期で記録します。走行中の状態やエラーは、同じ保存先のログにも記録します。
"""

import csv
import logging
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from queue import Queue
from threading import Thread
from time import monotonic


PLOT_SCRIPT_NAME = 'plot_eiclab_line_follower_log.py'


def plot_run_log(csv_path):
    """終了した走行CSVを、同じフォルダーの描画スクリプトでPNGにする。"""
    project_dir = Path(__file__).resolve().parent
    subprocess.run(
        [sys.executable, PLOT_SCRIPT_NAME, str(Path(csv_path).resolve())],
        cwd=project_dir,
        check=True,
    )
    csv_path = Path(csv_path)
    return csv_path.with_name(csv_path.stem + '_plot.png')


class RunLogger:
    """走行データを別スレッドでCSVに書き込むロガー。"""

    # CSVの列名。描画スクリプトがこの列名を使ってデータを読み込む。
    HEADER = [
        'time_s',
        'sensor_0',
        'sensor_1',
        'sensor_2',
        'sensor_3',
        'motor_left',
        'motor_right',
    ]

    def __init__(self, log_dir=None):
        self._log_dir = Path(log_dir) if log_dir is not None else Path(__file__).resolve().parent / 'run_logs'
        self._log_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime('%Y%m%d_%H%M%S')

        self.csv_path, self.log_path, self._csv_file, log_file = self._create_files(stamp)
        self._log_file = log_file
        self.log = logging.getLogger('eiclab.line_follower.{}'.format(self.csv_path.stem))
        self.log.setLevel(logging.INFO)
        self.log.propagate = False
        formatter = logging.Formatter('%(asctime)s %(levelname)s %(message)s')
        self._file_handler = logging.StreamHandler(log_file)
        self._file_handler.setFormatter(formatter)
        self._console_handler = logging.StreamHandler()
        self._console_handler.setFormatter(formatter)
        self.log.addHandler(self._file_handler)
        self.log.addHandler(self._console_handler)

        self._writer = csv.writer(self._csv_file)
        self._writer.writerow(self.HEADER)
        self._csv_file.flush()
        self._queue = Queue()
        self._thread = None
        self._start_time = None
        self._error = None
        self._csv_closed = False
        self._closed = False
        self.log.info('Run initialized; CSV: %s', self.csv_path)

    def _create_files(self, stamp):
        suffix = 0
        while True:
            stem = 'run_{}'.format(stamp)
            if suffix:
                stem = '{}_{:02d}'.format(stem, suffix)
            csv_path = self._log_dir / '{}.csv'.format(stem)
            log_path = self._log_dir / '{}.log'.format(stem)
            csv_file = None
            log_file = None
            try:
                csv_file = csv_path.open('x', newline='', encoding='utf-8')
                log_file = log_path.open('x', encoding='utf-8')
                return csv_path, log_path, csv_file, log_file
            except FileExistsError:
                if csv_file is not None:
                    csv_file.close()
                    csv_path.unlink()
                if log_file is not None:
                    log_file.close()
                    log_path.unlink()
                suffix += 1

    @property
    def error(self):
        return self._error

    def start(self):
        """記録スレッドを開始し、走行時間の計測を始める。"""
        if self._thread is not None or self._closed:
            raise RuntimeError('RunLogger can only be started once')
        self._start_time = monotonic()
        self._thread = Thread(target=self._write_samples, name='line-follower-csv', daemon=True)
        self._thread.start()
        self.log.info('Sensor initialization complete; recording started')

    def record(self, sensors, motor_left, motor_right):
        """1回分のセンサー値とモーター指令値を記録キューに追加する。"""
        if self._error is not None:
            raise RuntimeError('CSV logging failed') from self._error
        if self._thread is None or self._closed:
            raise RuntimeError('RunLogger is not recording')
        if self._start_time is None:
            raise RuntimeError('RunLogger has no recording start time')
        if len(sensors) != 4:
            raise ValueError('Exactly four sensor values are required')
        row = [
            '{:.6f}'.format(monotonic() - self._start_time),
            *sensors,
            motor_left,
            motor_right,
        ]
        self._queue.put_nowait(row)

    def _write_samples(self):
        """キュー内のデータを順にCSVへ書き込む。"""
        try:
            while True:
                row = self._queue.get()
                if row is None:
                    break
                self._writer.writerow(row)
                self._csv_file.flush()
        except Exception as exc:
            self._error = exc
            self.log.exception('CSV logging failed')

    def stop_recording(self):
        if not self._csv_closed:
            if self._thread is not None and self._thread.is_alive():
                self._queue.put(None)
                self._thread.join()
            self._csv_file.close()
            self._csv_closed = True
        if self._error is not None:
            raise RuntimeError('CSV logging failed') from self._error

    def close(self):
        if not self._closed:
            try:
                self.stop_recording()
            finally:
                self.log.info('Run logging stopped')
                self._file_handler.flush()
                self.log.removeHandler(self._file_handler)
                self.log.removeHandler(self._console_handler)
                self._file_handler.close()
                self._console_handler.close()
                self._log_file.close()
                self._closed = True
        if self._error is not None:
            raise RuntimeError('CSV logging failed') from self._error
