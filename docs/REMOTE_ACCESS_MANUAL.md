# グループ13 車載 Raspberry Pi Zero 2 W 遠隔操作マニュアル

対象機器: 車体搭載 Raspberry Pi Zero 2 W（ホスト名: `car13main.local`）  
対象読者: グループ 13 開発メンバー全員

---

## 1. 対象機器と基本ルール

| 項目 | 設定値・ルール |
|---|---|
| ホスト名 | `car13main.local`（接続環境により `car13main` の場合あり） |
| ユーザー名 | `group13` |
| パスワード | `<班内で別途共有>`（※資料・Git・公開チャットに書かないこと） |
| 作業フォルダ（例） | `~/EicDesignLab`（または `~/lf-group13`） |

> **最重要ルール（秘密鍵と公開鍵の扱い）**  
> 1. **秘密鍵（`id_ed25519_car13`）は絶対に他の人へ渡さないでください。** メンバー各自が自分の PC で自分専用の鍵ペアを作成します。  
> 2. 公開鍵（`.pub`）を `car13main` に登録するときは、**必ず追記（`>>`）** を使います。上書き（`>`）すると他メンバーの鍵がすべて消えてしまいます。

---

## 2. 接続前の準備

1. **同じ Wi-Fi（SSID）に接続する**
   - 自分の PC と車体の PiZeroW の両方を、**授業で指定された実習室・実験室用の Wi-Fi（SSID）** に接続してください。
   - 大学全学 Wi-Fi（eduroam 等）は端末間通信が遮断されていることが多く、SSH が届きません。
2. **起動待ちと疎通確認**
   - マイコン用電源（単 3×3 本側）を入れてから、基板の緑色 LED の激しい点滅が落ち着くまで **30〜60 秒** 待ちます。
   - PC の端末（PowerShell / Terminal）で以下を実行し、応答があるか確認します。
     ```powershell
     ping car13main.local
     ```
   - `car13main.local` が見つからない場合は `ping car13main` も試してください。

---

## 3. 基本の SSH 接続と正しい終わり方

### 3-1. 初回・通常の SSH 接続
```powershell
ssh group13@car13main.local
```
- **初回のみ** `Are you sure you want to continue connecting (yes/no/[fingerprint])?` と聞かれます。**`yes`** と入力して `Enter` を押します（`y` のみは不可）。
- 続いて `group13@car13main.local's password:` と表示されたら、**班内で共有しているパスワード**を入力して `Enter` を押します。  
  ※ セキュリティ仕様上、**入力中は画面に `*` も文字も一切表示されません**。そのまま打ち切って `Enter` を押してください。
- プロンプトが `group13@car13main:~ $` に変わればログイン完了です。

### 3-2. 正しい終わり方（2 種類の区別）

| 目的 | 実行コマンド | 注意事項 |
|---|---|---|
| **SSH 接続だけ切る**（PiZeroW は動かしたまま） | `exit`（または `Ctrl+D`） | PC 側へ戻ります。**この時点ではまだ電池を抜かないでください。** |
| **電源を切る前の安全シャットダウン** | `sudo poweroff` | 実行前に `who` で他の人がログイン中でないか確認。**PiZeroW の緑色 LED が完全消灯してから**電池を抜く。 |
| 応答不能時の強制切断 | `Enter` → `~` → `.` | Wi-Fi 切断などで画面が固まったとき用。 |

> **電源切断の鉄則**  
> マイコン用電源（単 3×3 本）をシャットダウン前に直接切ると、microSD カード破損・CSV 消失の原因になります。  
> 一方、**モータ用電源（単 3×2 本）** はマイコンと別系統のため、車体が暴走しそうな際の緊急停止としていつでも切って構いません。

---

## 4. パスワードスキップ（SSH 公開鍵認証）の設定手順

**各自の PC で最初に 1 回だけ設定**します。設定後はパスワード入力なしでログインおよび `scp` 転送ができるようになります。

### A. Windows（PowerShell）の場合

> **Windows での注意**  
> Windows 標準の PowerShell には Linux の `ssh-copy-id` コマンドがありません。また、PowerShell 5.1 で `Out-File` / `Add-Content` の既定文字コードや UTF-8 BOM が入ると `~/.ssh/config` が読み込めなくなります。必ず以下の手順通り実行してください。

#### ステップ 1：自分専用の SSH 鍵を作成（既存鍵を保護するため別名にする）
```powershell
mkdir "$env:USERPROFILE\.ssh" -Force | Out-Null
ssh-keygen -t ed25519 -f "$env:USERPROFILE\.ssh\id_ed25519_car13" -C "car13main-member"
```
- `Enter passphrase (empty for no passphrase):` → **何も入力せず `Enter`**
- `Enter same passphrase again:` → **そのまま `Enter`**

#### ステップ 2：公開鍵（`.pub`）を `car13main` に追記登録（ここで 1 回だけパスワード入力）
```powershell
Get-Content "$env:USERPROFILE\.ssh\id_ed25519_car13.pub" | ssh group13@car13main.local "mkdir -p ~/.ssh && chmod 700 ~/.ssh && { echo ''; tr -d '\r'; } >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys"
```
- `>>` により既存の他メンバーの公開鍵を残したまま末尾に追記されます。
- `tr -d '\r'` により Windows 由来の改行コード混入を防ぎます。

#### ステップ 3：`~/.ssh/config` に接続先エイリアスを登録
```powershell
Add-Content -Path "$env:USERPROFILE\.ssh\config" -Encoding ascii -Value @"

Host car13main
    HostName car13main.local
    User group13
    IdentityFile ~/.ssh/id_ed25519_car13
    IdentitiesOnly yes
"@
```
- ※ もし `car13main.local` ではなく `car13main` で繋がる環境の場合は `HostName car13main` に書き換えてください。

#### ステップ 4：パスワードなしでログインできるか確認
```powershell
ssh -o BatchMode=yes car13main "hostname && whoami"
```
パスワードを一切聞かれずに `car13main` と `group13` が表示されれば成功です。以後は `ssh car13main` だけで接続できます。

---

### B. macOS / Linux / Raspberry Pi 4 の場合

1. **専用鍵を作成**（パスフレーズは空で `Enter` × 2）:
   ```bash
   mkdir -p ~/.ssh && chmod 700 ~/.ssh
   ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519_car13 -C "car13main-member"
   ```
2. **公開鍵を `car13main` に登録**（ここで 1 回だけパスワード入力）:
   ```bash
   ssh-copy-id -i ~/.ssh/id_ed25519_car13.pub group13@car13main.local
   ```
3. **`~/.ssh/config` に追記して権限を設定**:
   ```bash
   cat << 'EOF' >> ~/.ssh/config

   Host car13main
       HostName car13main.local
       User group13
       IdentityFile ~/.ssh/id_ed25519_car13
       IdentitiesOnly yes
   EOF
   chmod 600 ~/.ssh/config
   ```
4. **動作確認**:
   ```bash
   ssh -o BatchMode=yes car13main "hostname && whoami"
   ```

---

## 5. ファイル転送（`scp`）の基本

公開鍵認証の設定後は、パスワードなしで PC と PiZeroW 間のファイル送受信ができます（**Windows PC 側の PowerShell** から実行してください）。

- **PC → PiZeroW へ計測プログラムのみを送る**（HTML/PDF や過去データは送らない）:
  ```powershell
  ssh car13main "mkdir -p ~/EicDesignLab/dev13/pi"
  scp dev13\pi\*.py car13main:~/EicDesignLab/dev13/pi/
  ```
- **PiZeroW → PC へ計測 CSV を回収する**（必ず `sudo poweroff` の前に実行）:
  ```powershell
  mkdir dev13\data\logs -Force
  scp -r car13main:~/EicDesignLab/dev13/pi/logs/* .\dev13\data\logs\
  ```

---

## 6. 複数人で開発・試験するときのチーム運用ルール

1. **実機ハードウェアの同時実行禁止**: モータや A/D コンバータ（GPIO/SPI）を 2 人が同時に動かすと `GPIO busy` エラーや車体の予期せぬ暴走が起きます。実機を動かす前に必ず声をかけてください。
2. **シャットダウン前の `who` 確認**: `sudo poweroff` や `sudo reboot` を打つ前に `who` を実行し、他メンバーが作業中でないか確認してください。
3. **鍵の適切な管理**: `~/.ssh/id_ed25519_car13`（拡張子なし＝秘密鍵）を Git リポジトリ・Teams・共有 USB メモリ等に絶対にアップロードしないでください。
4. **教員リポジトリ（`msiplab/EicDesignLab`）の保護**: `origin`（教員リポジトリ）への `git push` やプルリクエストは禁止です。班内共有はグループの共有リポジトリ（`lf-group13` 等）または `dev13/` 内で管理してください。

---

## 7. トラブルシューティング

| 症状 | 主な原因 | 対処法 |
|---|---|---|
| `Could not resolve hostname car13main.local` | 別 Wi-Fi（eduroam 等）に接続、Pi 未起動 | PC と Pi を同じ実験室 SSID に接続し、Pi 起動後 1 分待ってから `ping car13main.local`（または `car13main`）を確認。 |
| `ssh-copy-id` が見つからない（Windows） | Windows PowerShell には無いコマンド | セクション 4-A ステップ 2 の `Get-Content ... \| ssh ...` を使用する。 |
| `Bad configuration option` / 文字化けエラー | `~/.ssh/config` に UTF-8 BOM が入った | Notepad 等で UTF-8（BOM なし）で保存し直すか、`Add-Content -Encoding ascii` を使う。 |
| 公開鍵登録後もパスワードを聞かれる | `~/.ssh` や `authorized_keys` の権限、または鍵の不一致 | `car13main` 側で `chmod 700 ~/.ssh && chmod 600 ~/.ssh/authorized_keys` を実行し、`ssh -vvv car13main` で読み込まれている鍵を確認。 |
| `GPIO busy` / デバイス使用中エラー | 他のプロセス・他メンバーが GPIO を占有中 | 誰かが別ターミナルやサービスでスクリプトを実行していないか `ps aux \| grep python` で確認して停止する。 |

---

## 8. このマニュアルの更新・HTML / PDF 出力手順

本資料の原本は Markdown（`dev13/docs/REMOTE_ACCESS_MANUAL.md`）で管理しています。内容を編集した後は、Windows PC 上の `EicDesignLab` フォルダから以下を実行すると、配布用の **スタンドアロン HTML** および **PDF** を生成できます。

```powershell
# 1. Markdown → HTML のみ生成（外部ライブラリ不要・標準ライブラリのみで動作）
python dev13\pc\render_manual.py dev13\docs\REMOTE_ACCESS_MANUAL.md

# 2. Markdown → HTML と PDF を一括生成（Edge または Chrome を使用）
python dev13\pc\render_manual.py dev13\docs\REMOTE_ACCESS_MANUAL.md --pdf
```

- 生成されるファイル:
  - `dev13/docs/REMOTE_ACCESS_MANUAL.html`（ブラウザでそのまま閲覧可能）
  - `dev13/docs/REMOTE_ACCESS_MANUAL.pdf`（印刷・Teams 配布向け）
- もし `--pdf` が環境都合で使えない場合は、生成された `REMOTE_ACCESS_MANUAL.html` を Edge / Chrome で開き、**`Ctrl + P` → 送信先「PDF として保存」** を選んで保存できます。
