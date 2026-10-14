# wsl-terminal-pdf

Windows Terminal / WSL2 / Neovim 用の PDF プレビュー連携キット（試作）。

Windows Terminal の同じタブを左右に分け、左に Neovim、右に SIXEL 対応 PDF ビューアを置くための設定一式です。Neovim の `:TermPdfOpen` で右ペインを開き、`:TermPdfForward` でカーソル位置から PDF にジャンプします。

PDF の描画には [yebo-liu/tdf](https://github.com/yebo-liu/tdf) を使います。このキットは新しい PDF 描画エンジンを実装するものではありません。小さな Lua 設定、標準ライブラリだけを使う Python 起動スクリプト、tdf の接続先ソケット指定と描画サイズの修正を提供します。tdf 本体のソースや実行ファイルはこのリポジトリに含めません。

## できることと現在の制限

| 操作 | 実装 |
| --- | --- |
| ソースと PDF を同一タブに左右表示 | Windows Terminal の独立ペイン |
| Neovim から PDF の対応箇所へ | 順方向 SyncTeX、Unix socket |
| PDF からソースへ | PDF ビューアで `s`。ページ全体の中央に対応するソースへ戻る |
| コンパイル済み PDF の更新反映 | tdf のファイル監視・再描画 |
| 拡大・移動 | `z`、`o` / `O`、`H` / `J` / `K` / `L` |
| 表示準備の負荷を抑える設定 | 横に 1 ページ、周辺 2 ページのプリレンダリング、更新待機 150 ms |

**逆方向検索はクリック位置に対応しません。拡大や移動をしても、ページ全体の中央が検索対象です。** 原稿の狙った数式をクリックして戻る使い方が必要なら、ビューア側の追加実装が必要です。また、逆方向検索は左側の Neovim のバッファを切り替えますが、Windows Terminal のフォーカスを左ペインへ移しません。

Neovim の `:vsplit` / `:terminal` 内への PDF 埋め込みは実装していません。端末の画像描画と Neovim の再描画を混ぜずに使える、Windows Terminal ペイン方式を初版に選んでいます。

メモリ使用量と操作遅延は実機で未測定です。SIXEL では拡大・移動の際に画像を再エンコードするため、ページの解像度や端末サイズが大きいと遅くなる可能性があります。プリレンダリング数の制限は、厳密なメモリ上限を保証するものではありません。

## 前提

- Windows Terminal **1.22 以降**。SIXEL の搭載は [Microsoft の公式リリース記事](https://devblogs.microsoft.com/commandline/windows-terminal-preview-1-23-release/) で確認できます。
- WSL2 の Ubuntu 系 Linux。同じディストリビューション内で Neovim と tdf を動かします。
- Linux 側の Neovim **0.10 以降**。`vim.system`、Lua 設定を使います。
- TeX 環境と `.synctex.gz` または `.synctex`。コンパイルに `-synctex=1` を付けます。
- Windows 実行ファイルを WSL から呼び出せること。`powershell.exe` による Windows Terminal 実行エイリアスの呼び出し、または実行可能な `wt.exe` が必要です。
- tdf のビルドに [Rust / rustup](https://rustup.rs/)。固定した upstream は nightly を要求します。ビルド時だけ必要です。

PDF ペインは tmux の外に直接開きます。左の Neovim が tmux 内にあっても PDF の画像は tmux を通しません。Neovim のソケットを同じ WSL のプロセスから参照できる必要があります。

接続には Neovim の Unix socket を使います。既存の server が TCP の場合は、Lua モジュールが追加の Unix socket を作成します。

## インストール

WSL 側でリポジトリを取得し、`wsl-terminal-pdf` ディレクトリで実行します。

```bash
sudo apt update
sudo apt install build-essential git clang pkg-config libfontconfig1-dev libsynctex-dev pipx
pipx install neovim-remote
git clone https://github.com/daikihashimoto95/wsl-terminal-pdf.git
cd wsl-terminal-pdf
```

`neovim-remote` は逆方向検索に使う `nvr` を提供します。起動スクリプトは新しい WSL ペインの PATH に `~/.local/bin` を加えるので、シェルの設定読み込みに依存しません。

Rust / rustup を導入した上で、ビューアと連携部分をインストールします。

```bash
rustup toolchain install nightly
bash scripts/build-viewer.sh
bash scripts/install-integration.sh
```

ビルドスクリプトは upstream commit `91709652c18ed027dcb671200e8bf2dc7211dc2a` に固定し、`patches/tdf-socket-env.patch` と `patches/tdf-image-fit.patch` を適用して `--features synctex` 付きでビルドします。

以下が作られます。

- `~/.local/bin/term-pdf` — 起動・順方向検索用スクリプト。
- `~/.local/share/terminal-pdf/viewer/bin/tdf` — SyncTeX 有効のビューア。
- `~/.local/share/terminal-pdf/neovim/lua/terminal_pdf/init.lua` — Neovim モジュール。

既存の tdf、Neovim 設定、TeX コンパイラ設定は上書きしません。Rust の依存ライブラリやビルド出力は初回に取得・作成されるため、初回ビルドは数分以上かかる場合があります。

## Lazy.nvim に追加

`neovim/lazy-example.lua` のエントリーを、既存の Lazy の plugin specification に加えます。

```lua
{
  dir = vim.fn.expand("~/.local/share/terminal-pdf/neovim"),
  name = "terminal-pdf-local",
  ft = "tex",
  config = function()
    require("terminal_pdf").setup({
      window = "0",
      size = 0.45,
    })
  end,
}
```

`window = "0"` は最も最近アクティブだった Windows Terminal ウィンドウを対象にします。複数ウィンドウを開いている場合は、開く直前に原稿側のウィンドウをアクティブにしてください。必要なら Windows Terminal に付けた window name を指定できます。

Lazy を経由しない場合は、インストール先を `runtimepath` に加えて `require("terminal_pdf").setup()` を呼んでください。

## 通常の使い方

1. `main.tex` を `-synctex=1` 付きでコンパイルします。例えば LuaLaTeX なら `latexmk -lualatex -synctex=1 main.tex`。継続コンパイルには既存の VimTeX 設定などを使えます。
2. Windows Terminal の WSL で `nvim main.tex` を開きます。
3. `:TermPdfDoctor` で必要なコマンドの検出結果を確認します。
4. `:TermPdfOpen` で右側の PDF ペインを開きます。
5. ソース側で `:TermPdfForward`、または `<localleader>pv` を押します。
6. PDF 側で `s` を押すと、ページ中央に対応するソースへ戻ります。

| Neovim | 操作 |
| --- | --- |
| `:TermPdfOpen` / `<localleader>po` | PDF ペインを起動。起動済みなら順方向検索 |
| `:TermPdfOpen /absolute/path/main.pdf` | 出力 PDF を明示し、このバッファの後続検索でも使用 |
| `:TermPdfForward` / `<localleader>pv` | カーソル行・列から順方向検索 |
| `:TermPdfDoctor` | 起動に必要なプログラムの検出 |

`<localleader>` は現在の Neovim の設定に従います。このキットはその値を変更しません。自分のキー割り当てを使う場合は `setup({ keymaps = false })` にして、`require("terminal_pdf").open()` / `.forward()` を割り当てます。

| PDF ビューア | 操作 |
| --- | --- |
| `h` / `l` | 前後のページ |
| `z` | fit / fill モード切り替え |
| `o` / `O` | fill モードで拡大 / 縮小 |
| `H` / `J` / `K` / `L` | 拡大後の移動 |
| `/` | PDF 内検索 |
| `s` | ページ中央から逆方向検索 |
| `q` | ビューアを終了 |

## 複数ファイルの原稿・出力ディレクトリ

PDF の選択順は、コマンドの明示指定、`b:term_pdf_path`、VimTeX の `b:vimtex.out()`、`setup` の `resolve_pdf`、root TeX と同名の PDF です。root は先頭 50 行にある `% !TeX root = ../main.tex` から辿れます。

VimTeX を使っている場合は、原稿の root とビルド出力をその設定から取得します。使っていない場合は、子ファイルに root directive を付けるか、PDF を明示してください。

```lua
vim.b.term_pdf_path = "/home/you/notes/build/main.pdf"
vim.b.term_pdf_cwd = "/home/you/notes"
```

`term_pdf_cwd` は **TeX コンパイラを起動したディレクトリ**です。SyncTeX が返す相対ソースパスと逆方向検索の整合性に必要です。VimTeX 使用時は project root、それ以外は root TeX のディレクトリを初期値にしています。異なる CWD からビルドしている場合は明示指定してください。

空白・日本語・コロン・セミコロンを含む PDF / TeX パスは JSON request と argv を使って受け渡します。改行・NUL を含むファイル名は tdf の IPC 形式に合わないため非対応です。起動スクリプトの設置パスにはセミコロンや改行を使わないでください。

## 画像が表示されない場合

Windows Terminal のバージョンを確認し、同じ WSL ペインで SIXEL を直接試します。

```bash
sudo apt install libsixel-bin
img2sixel /path/to/a-small-image.png
```

これが表示されなければ、ビューアの設定より先に端末の SIXEL 表示を確認する必要があります。正確な表示と解像度の取得は実機に依存します。Neovim の `:terminal` 内でこのチェックを行わないでください。

`wt.exe` の実行エイリアスは WSL からの直接起動が機能しないことがあります。このキットは必要に応じて短時間の PowerShell プロセス経由でエイリアスを呼びます。PowerShell も呼べない場合は、実行可能な Windows Terminal 本体のパスを `setup({ wt_path = "/mnt/c/.../wt.exe" })` で指定してください。

## 検証範囲

`Checks` workflow では Python テスト 15 件、shell 構文、Lua 構文が通過しています。実際の Unix socket を使う 2 件も GitHub の Ubuntu runner で実行しています。

追加した `Linux integration` workflow は Ubuntu 24.04 で実際の patched tdf をビルドし、Neovim 0.11.5・LuaLaTeX・nvr と接続します。ビューアの実ビルド・CLI 起動・共有ライブラリ解決は確認済みです。全プロセスを使う往復検索の結果は確認中です。

この workflow の検証対象は次のとおりです。

- 子ファイル・root directive・別の PDF 出力ディレクトリ・空白と日本語を含むパスを使う実際の TeX コンパイル。
- 実際の Neovim に Lua モジュールを読み込み、`:TermPdfForward` から tdf の Unix socket を経由して PDF の 2 ページ目へ移動。
- 実際の tdf を疑似端末で動かし、SIXEL の問い合わせ応答と描画データの出力を確認。
- tdf へ `s` を送り、実際の nvr と Neovim RPC で、`synctex edit` と同じソース・行へ戻ることを確認。
- `q` でビューアと起動スクリプトが正常終了することを確認。

疑似端末は画像のデータを受け取りますが、ラスタ画像を表示・復号しません。Windows Terminal の表示品質、WSL と Windows の相互運用、ペイン分割、サイズ変更、自動再読込、操作遅延・メモリ使用量は、この検証には含めていません。

起動処理のテストは標準ライブラリだけで実行できます。

```bash
python3 -m unittest discover -s tests -p 'test_*.py' -v
bash -n scripts/build-viewer.sh
bash -n scripts/install-integration.sh
```

実プロセスを使う Linux / WSL の追加検証には、LuaLaTeX・synctex・Neovim 0.10 以降・ビルド済みビューアが必要です。テスト用の Python 環境を作って実行します。

```bash
python3 -m venv .venv
.venv/bin/pip install neovim-remote
PATH="$PWD/.venv/bin:$PATH" .venv/bin/python tests/smoke_linux.py \
  --viewer "$HOME/.local/share/terminal-pdf/viewer/bin/tdf"
```

これは Windows Terminal ペインの起動テストではありません。詳細な検証結果は [Linux integration](https://github.com/daikihashimoto95/wsl-terminal-pdf/actions/workflows/linux-integration.yml) の実行ログで確認できます。

実機では同じ原稿について、起動 → 順方向検索 → ページ中央から逆方向検索 → 再コンパイルによる更新 → ウィンドウサイズ変更を確認してください。クリック逆方向検索や実測済みの軽量性を提供する完成版ではなく、上記制限付きの連携試作です。

## ライセンスと出典

このキットの Python / Lua / shell コードは `LICENSE` に従います。tdf とその変更 patch は upstream の AGPL-3.0 に従います。ビルド後の tdf を再配布する場合は、適用 patch と対応するソースも扱う必要があります。詳しくは `patches/README.md` を参照してください。

- [tdf fork](https://github.com/yebo-liu/tdf) — PDF rendering、SIXEL zoom、SyncTeX、hot reload。
- [Windows Terminal command line](https://learn.microsoft.com/en-us/windows/terminal/command-line-arguments) — split-pane、window target。
- [Neovim remote support](https://neovim.io/doc/user/remote/) — Neovim server socket。
- [neovim-remote](https://github.com/mhinz/neovim-remote) — 逆方向検索で使用する `nvr`。

README に記載した upstream の機能は、2026-10-14 に参照したソースを基準にしています。
