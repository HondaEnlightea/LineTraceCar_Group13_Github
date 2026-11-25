# LineTraceCar Group 13

新潟大学工学部「電子情報通信設計製図」グループ 13 のライントレースカー開発リポジトリです。
実機（Raspberry Pi Zero 2 W）へ転送する軽量プログラムと、PC 側で行う作図・資料生成・計測データを役割ごとに分離して管理しています。

- GitHub: https://github.com/HondaEnlightea/LineTraceCar_Group13_Github
- Clone URL: `https://github.com/HondaEnlightea/LineTraceCar_Group13_Github.git`

## ディレクトリ構成

| パス | 対象環境 | 内容 |
|---|---|---|
| `pi/characterization/` | **PiZeroW** | タイヤ回転数・センサ感度の特性試験プログラム（標準ライブラリ + `gpiozero` のみ） |
| `pi/driving/` | **PiZeroW** | 実機走行・ロギングプログラム（`eiclab_line_follower_advanced.py`, `rotary_encoder.py` 等） |
| `pc/` | **PC** | 特性グラフ・走行ログ描画スクリプト、マニュアル HTML/PDF 生成ツール |
| `docs/` | **人間向け** | 特性試験運用指南書、遠隔操作マニュアル（MD / HTML） |
| `data/` | **PC / 保管** | 計測生データ・回転数データ・グラフ出力・サンプルデータ（※PiZeroW へは送らない） |
| `reference/` | **参考** | 実機から回収した未修正の参考コード（構文・配線要修正） |
| `tests/` | **PC** | Mock によるユニットテスト |

## よく使うコマンド

### 1. PiZeroW（`car13main`）へ実機用プログラムのみ転送
```powershell
ssh car13main "mkdir -p ~/EicDesignLab/dev13/pi"
scp pi\characterization\*.py car13main:~/EicDesignLab/dev13/pi/
```

### 2. PiZeroW から計測 CSV を回収
```powershell
scp -r car13main:~/EicDesignLab/dev13/pi/logs/* .\data\logs\
```

### 3. PC 側で特性グラフを描画
```powershell
python pc\plot_characteristics.py data\logs\motor_char_YYYYMMDD_HHMMSS_summary.csv
python pc\plot_characteristics.py data\logs\sensor_char_YYYYMMDD_HHMMSS_summary.csv
python pc\plot_characteristics.py data\logs\sensor_sweep_YYYYMMDD_HHMMSS.csv --calibration data\logs\sensor_char_YYYYMMDD_HHMMSS_calibration.json
```

### 4. PC 側でユニットテストを実行
```powershell
python -m unittest discover -s tests -v
```
