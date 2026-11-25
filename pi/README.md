# Group 13 Raspberry Pi 実機ディレクトリ案内 (`~/EicDesignLab/dev13`)

この Raspberry Pi (`car13main`) には、実機上で直接実行する軽量な計測プログラムのみを配置しています。
**作図プログラム・過去の計測データ・HTML/PDF マニュアル・走行プログラムの本体は GitHub リポジトリおよび開発用 PC 側で管理しています。**

## GitHub リポジトリ

- ブラウザ閲覧用 URL: https://github.com/HondaEnlightea/LineTraceCar_Group13_Github
- Git Clone 用 URL: `https://github.com/HondaEnlightea/LineTraceCar_Group13_Github.git`

## 実機 (`car13main`) と GitHub / PC の役割分担

| 項目 | 実機 (`car13main`) での場所 | GitHub リポジトリでの場所 | 用途・備考 |
|---|---|---|---|
| 特性試験プログラム（実機実行用） | `~/EicDesignLab/dev13/pi/` | `pi/characterization/` | `measure_motor_characteristics.py`, `measure_sensor_characteristics.py`（標準ライブラリ + `gpiozero` のみ） |
| 走行・ロギングプログラム | （必要時に転送して実行） | `pi/driving/` | `eiclab_line_follower_advanced.py`, `eiclab_line_follower_log.py`, `rotary_encoder.py` |
| 作図・資料生成スクリプト（PC用） | 配置しない | `pc/` | `plot_characteristics.py`, `plot_eiclab_line_follower_log.py`, `plot_rotary_encoder.py`, `render_manual.py` |
| 計測データ・グラフ・サンプル | `~/EicDesignLab/dev13/logs/`（一時保存のみ） | `data/` | `data/logs/`, `data/rotary_20261028/`, `data/samples/` |
| マニュアル・手順書（MD / HTML） | 配置しない | `docs/` | `CHARACTERIZATION_GUIDE.md`, `REMOTE_ACCESS_MANUAL.md` / `.html` |
| 実機回収参考コード（未修正） | 配置しない | `reference/` | `eiclab_line_follower_pid.py` |

## 実機での特性試験実行コマンド

```bash
cd ~/EicDesignLab

# 1. モータ（タイヤ回転数）特性試験（例: 上昇・下降 / 無負荷）
python3 dev13/pi/measure_motor_characteristics.py --up-down --condition noload

# 2. フォトリフレクタ（白/黒ライン）感度特性試験
python3 dev13/pi/measure_sensor_characteristics.py --include-ambient
```

## 計測後のデータ回収・電源断（PC 側から実行）

計測ログ（`~/EicDesignLab/dev13/logs/`）は SD カードに残したままにせず、PC の GitHub リポジトリ配下 `data/logs/` へ `scp` で回収してから、安全にシャットダウンしてください。

```powershell
# PC (PowerShell) から計測ログを回収する例
scp -r car13main:~/EicDesignLab/dev13/logs/* .\data\logs\

# 実機の安全な電源オフ（電池を抜く前に必ず実行）
ssh car13main "sudo poweroff"
```
