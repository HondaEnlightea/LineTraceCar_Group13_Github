# グループ13 車体特性試験 運用指南書（Raspberry Pi Zero 2 W 搭載機向け）

このドキュメントは、車体へ **Raspberry Pi Zero 2 W**（以下 PiZeroW）を搭載し、**マウス・キーボード・ディスプレイを接続できない状態**で以下の 2 つの特性試験を安全に実施し、Windows PC でグラフ化するまでの実践手順をまとめたものです。

1. **タイヤ・モータ回転数測定と左右差確認**（[../pi/measure_motor_characteristics.py](../pi/measure_motor_characteristics.py)）
2. **フォトリフレクタ感度・左右差測定**（[../pi/measure_sensor_characteristics.py](../pi/measure_sensor_characteristics.py)）
3. **PC 側でのグラフ描画**（[../pc/plot_characteristics.py](../pc/plot_characteristics.py) / [../pc/plot_rotary_encoder.py](../pc/plot_rotary_encoder.py)）

---

## 1. ディスプレイ・キーボードなしでプログラムを注入・実行する仕組み

講義資料（`2026年度電子情報通信設計製図スライド01.pdf` p.12, p.21 および `スライド04.pdf` p.4, p.9〜12）に、**毎回ディスプレイを繋ぐ必要のない運用方法**が明記されています。

- **基本方針**: プログラムの編集は PC または開発用 Raspberry Pi 4/5 で行い、PiZeroW へは Wi-Fi 経由の **SSH + Git / rsync / scp** で転送・実行します。
- **初回のみ必要な準備**（既に済んでいる場合は不要）:
  - microSD セットアップ時または 1 回目の接続時に、Wi-Fi（実習室/実験室の SSID）・ホスト名（`car13main.local`）・SSH 有効化を設定しておく。
  - ホスト名・ユーザー名が分かれば、PC の PowerShell から `ssh group13@car13main.local`（鍵設定済みなら `ssh car13main`）でキーボード・モニタなしに PiZeroW を操作できます。

### プログラム注入・CSV 回収の 2 通りの手順

#### 方法 A: Wi-Fi + SSH で操作する（推奨・標準）
1. **PC → PiZeroW へのプログラム転送**（実機用 `dev13/pi/*.py` のみを転送し、HTML/PDF/過去データは送らない）
   - 共有リポジトリを使う場合（`スライド04.pdf` p.12 方法 1）:
     ```bash
     # PC / Pi4 側でコミット・push したあと、SSH 先の PiZeroW で実行
     cd ~/EicDesignLab   # または ~/lf-group13
     git pull
     ```
   - コミット前に直接 `dev13/pi/` だけを送る場合（`スライド04.pdf` p.12 方法 2 / Windows PowerShell から `scp`）:
     ```powershell
     # Windows PowerShell（EicDesignLab フォルダから）
     ssh car13main "mkdir -p ~/EicDesignLab/dev13/pi"
     scp dev13\pi\*.py car13main:~/EicDesignLab/dev13/pi/
     ```
2. **SSH 先の PiZeroW で試験スクリプトを実行**（後述のセクション 4・5 参照）。
3. **PiZeroW → Windows PC へ CSV/JSON を回収**:
   ```powershell
   # Windows PowerShell（EicDesignLab フォルダから）
   mkdir dev13\data\logs -Force
   scp -r car13main:~/EicDesignLab/dev13/pi/logs/* .\dev13\data\logs\
   ```

#### 方法 B: 完全にヘッドレス（Wi-Fi 接続も使わず、自動起動・自動終了で動かす）
実験室で Wi-Fi に入れない場合や、走らせる直前に PC を開けない場合は、あらかじめ PiZeroW 側へスクリプトを入れた上で `--yes --start-delay --step-wait --led-pin --auto-poweroff` オプションを使い、**systemd サービス**等から起動して全自動で計測・保存・シャットダウンまで完結させます。

---

## 2. 【重要】電源の切り方と安全ルール（SD カード破損・データ消失防止）

> **結論**: **「ラズパイの電池をいきなり抜く／切る」終了方法は避けてください。**

### なぜラズパイ側の電池を直接切ってはいけないのか
1. **microSD カードの破損リスク**: 講義資料（`スライド01.pdf` p.6 / `システム開発環境補足.pdf` p.24）でも **「状態確認 LED（緑色 LED）の点滅中に電源を抜かない」「シャットダウン後 LED が消灯してから電源を抜いてください」** と明記されています。通電中に電源を絶つと Linux ファイルシステムが壊れ、次回起動不能になる恐れがあります。
2. **CSV 書き込みのキャッシュ消失**: OS のページキャッシュに残ったデータが電源断で失われることがあります。

### 本プログラム側で組み込んだ対策と「ソフトだけでは防げない限界」
- **ソフト側で実装した保護**:
  - 各ステップ完了時・一定サンプル毎・終了処理（`finally`）時に `flush()` + `os.fsync()` を実行し、CSV/JSON をストレージへ強制同期しています。
  - 全試験は無限ループではなく**決められた時間・ステップ数で自動停止・自動終了**します。
  - `--auto-poweroff` オプションを付けると、デバイス停止・`os.fsync()`・ファイルクローズの直後に `sudo poweroff` を自動実行します。
- **ソフト側で防げない限界**:
  - Linux が起動している最中に PiZeroW 用電源（単 3×3 本 → DC-DC コンバータ TPS63000）を遮断した場合の SD カード破損リスクはソフトウェアからは防げません。

### 正しい電源の切り方（使い分けルール）

| 電源系統 | 用途 | 切り方のルール |
|---|---|---|
| **モータ用電源**（単 3×2 本 → DRV8835 VM） | DC モータ駆動 | **いつでも切って OK（緊急停止スイッチとして安全）**。SD カードに影響しません。 |
| **マイコン用電源**（単 3×3 本 → TPS63000 5V） | PiZeroW 駆動 | **必ず下記のいずれかで OS をシャットダウンし、緑色 LED が完全消灯してから切る。** |

1. **SSH で操作している場合**: 試験終了後に `sudo poweroff` を実行し、PiZeroW の緑色 LED が消灯してから電池を切る。
2. **シャットダウンボタンを使っている場合**（`スライド03.pdf` p.18〜19 / `eiclab_shutdown_button.py`）: `BCM3` にタクトボタン＋`shutdownbutton.service` を導入済みなら、**2 秒長押し** → 緑色 LED が点滅後消灯 → 電池を切る。（再度押すと起動も可能）
3. **完全ヘッドレス自動試験の場合**: `--auto-poweroff` を付けて実行し、ステータス LED が消え、さらに PiZeroW 本体の緑色 LED が点滅を終えて完全消灯してから電池を切る。

---

## 3. 人間側がやるべき事前チェックリスト

試験を始める前に、毎回以下を確認してください。

- [ ] **SPI の有効化**（フォトリフレクタ試験で必須）:
  `pinctrl get`（または `raspi-config`）で `BCM8, 9, 10, 11` が `a0`（SPI0）になっていることを確認する。
- [ ] **電池残量**: モータ用（単 3×2 本）およびマイコン用（単 3×3 本）の電圧が極端に落ちていないか確認する（電池が弱っていると高 PWM で回転数が頭打ち・左右差拡大が起きます）。
- [ ] **車輪の安全確保（空転試験時）**: 車体が走り出さないよう、タイヤが浮く台の上に車体を載せる。
- [ ] **配線ピンの確認**:
  - 左モータ: `AIN1=BCM6`, `AIN2=BCM5` / 右モータ: `BIN1=BCM26`, `BIN2=BCM27`
  - エンコーダ: 左 `BCM17`, 右 `BCM22`（`CPR=32`）
  - フォトリフレクタ: 左から順に `MCP3004` の `ch0(左外)`, `ch1(左内)`, `ch2(右内)`, `ch3(右外)`
- [ ] **路面材の準備（センサ試験時）**: 白いコース用紙と、実コースと同じ黒ライン（または広い黒領域）。

---

## 4. 試験 1: タイヤ回転数・左右差測定

### 実行コマンド（PiZeroW 側、`EicDesignLab` フォルダから）

- **SSH で対話実行する場合（標準）**:
  ```bash
  # 無負荷（車輪を浮かせて）で上昇・下降スイープを実施
  python3 dev13/pi/measure_motor_characteristics.py --up-down --condition noload
  ```
  - 実行後、「車輪が浮いていること…」の確認メッセージが出たら `Enter` を押します。
  - `PWM = 0.15, 0.20, 0.30, 0.40, 0.60, 0.80, 1.00, 0.80, ..., 0.15` の各ステップを自動で回し、**全ステップ完了後に自動でモータを停止して終了**します。
  - 逆転も見たい場合は `--include-reverse` を追加します（単相エンコーダのため回転数は絶対値として記録されます）。

- **ヘッドレス（`Enter` 待ちなし・LED 表示・自動シャットダウン）で実行する場合**:
  ```bash
  python3 dev13/pi/measure_motor_characteristics.py --yes --start-delay 5 --up-down --led-pin 23 --auto-poweroff
  ```
  - 最初の 5 秒間は LED（例: BCM23）が点滅し、その間に台の上にセットして手を離せます。
  - 計測中は LED が点灯し、全ステップ終了・CSV 同期後に LED が消灯して自動 `poweroff` します。

### 生成されるファイル（Pi 側 `dev13/pi/logs/` → 回収先 `dev13/data/logs/`）
- `motor_char_YYYYMMDD_HHMMSS_raw.csv`（0.1 秒周期の時系列生データ）
- `motor_char_YYYYMMDD_HHMMSS_summary.csv`（各 PWM の定常区間エッジ数ベース回転数・左右差・比・補正係数）

### 確認ポイントと PC 側でのグラフ化
- 画面のサマリ表で以下を確認します:
  1. **不感帯**: `0.15` や `0.20` で回転数が `0` または極端に小さいか。
  2. **左右差（`R-L[rpm]`, `R/L`）**: 低 PWM と高 PWM で左右差が変化していないか。
  3. **補正係数（`Scale(L, R)`）**: 速い側のタイヤに掛ける `<= 1.0` の係数目安。
- Windows PC へ CSV をコピー後、以下で PNG グラフを作成します:
  ```powershell
  python dev13\pc\plot_characteristics.py dev13\data\logs\motor_char_YYYYMMDD_HHMMSS_summary.csv
  ```
  → `motor_char_YYYYMMDD_HHMMSS_summary_plot.png` が生成されます。

---

## 5. 試験 2: フォトリフレクタ感度・左右差測定

### 5-A. 白黒感度・左右対称性・ノイズ測定（`static` モード）

- **SSH 接続で対話実行する場合（推奨）**:
  ```bash
  python3 dev13/pi/measure_sensor_characteristics.py --include-ambient
  ```
  1. **ステップ 1（白路面）**: 4 つすべてのフォトリフレクタを白い紙の上に置き、`Enter` を押す。
  2. **ステップ 2-1〜2-4（各チャネルの黒ライン）**: 画面の指示に従い、`ch0(左外)` → `ch1(左内)` → `ch2(右内)` → `ch3(右外)` の順に**対象センサが黒ラインの真上に来るよう位置をずらし**、`Enter` を押す（約 2.5 秒間計測）。
  3. **ステップ 3（外乱光、任意）**: 車体を持ち上げて `Enter` を押す。
  - **モータ駆動ノイズも確認したい場合**は `--check-motor-noise` を付けます（ステップ 4 で車輪を浮かせて無駆動時と駆動時のセンサ値ゆらぎを比較します）。

- **キーボードなしで完全ヘッドレス実行する場合（`--yes` + `--step-wait` + `--led-pin`）**:
  ```bash
  python3 dev13/pi/measure_sensor_characteristics.py --yes --start-delay 5 --step-wait 5 --led-pin 23 --auto-poweroff
  ```
  - **LED が点滅している 5 秒間**に車体を次の位置（白 → ch0 黒 → ch1 黒 → ch2 黒 → ch3 黒）へ動かします。
  - **LED が連続点灯している間（約 2.5 秒）**は計測中なので車体を動かさず静止させます。
  - すべて終わると自動保存・LED 消灯・`poweroff` します。

### 5-B. ライン横切り連続応答測定（`sweep` モード）
1. まず `static` モードで `sensor_char_*_calibration.json` を作成しておく（省略も可）。
2. 以下を実行し、15 秒間に車体を左右へゆっくり滑らせて 4 つのセンサすべてで黒ラインを複数回横切らせます:
   ```bash
   python3 dev13/pi/measure_sensor_characteristics.py --mode sweep --duration 15 --calibration dev13/pi/logs/sensor_char_YYYYMMDD_HHMMSS_calibration.json
   ```

### 生成されるファイル（Pi 側 `dev13/pi/logs/` → 回収先 `dev13/data/logs/`）
- `static` モード:
  - `sensor_char_YYYYMMDD_HHMMSS_raw.csv`
  - `sensor_char_YYYYMMDD_HHMMSS_summary.csv`
  - `sensor_char_YYYYMMDD_HHMMSS_calibration.json`（正規化用 `white_mean` / `black_mean`・左右差・自動警告）
- `sweep` モード:
  - `sensor_sweep_YYYYMMDD_HHMMSS.csv`

### PC 側でのグラフ化
```powershell
# 白黒感度と左右差グラフ
python dev13\pc\plot_characteristics.py dev13\data\logs\sensor_char_YYYYMMDD_HHMMSS_summary.csv

# 連続横切り応答グラフ（calibration JSON を渡すと正規化曲線も出力）
python dev13\pc\plot_characteristics.py dev13\data\logs\sensor_sweep_YYYYMMDD_HHMMSS.csv --calibration dev13\data\logs\sensor_char_YYYYMMDD_HHMMSS_calibration.json
```

---

## 6. よくあるトラブルと対処

| 症状 | 考えられる原因 | 確認・対処 |
|---|---|---|
| `ssh group13@car13main.local` に繋がらない | Wi-Fi 未接続、ホスト名違い、電源不足 | 開発用 Pi4 や有線モニタで一度 `hostname` と Wi-Fi 設定を確認する。 |
| `MCP3004` の値が常に `0` またはエラー | SPI が無効、配線ミス | `pinctrl get` で BCM8〜11 が `a0` か確認。無効なら `sudo raspi-config` で SPI を有効化。 |
| 回転数が常に `0.00` | モータ電池切れ、DRV8835 配線、エンコーダピン違い | モータ用単 3 電池の電圧と `ENC_LEFT_PIN=17`, `ENC_RIGHT_PIN=22` の配線を確認。 |
| `ch0` と `ch3` の感度差が大きい | センサの取り付け高さ・傾き・はんだ不良 | 路面との距離が左右で揃っているか目視確認し、センサ基板の角度を揃えて再測定。 |
| モータは回るが途中で PiZeroW が再起動する | マイコン用電池の電圧低下、電源ノイズ | マイコン用電池を充電済みのものに交換し、電源平滑コンデンサの配線を確認。 |
