#!/usr/bin/env bash
set -euo pipefail
kit_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
install -d -- "$HOME/.local/bin" "$HOME/.local/share/terminal-pdf/neovim/lua/terminal_pdf"
install -m 0755 -- "$kit_dir/bin/term-pdf" "$HOME/.local/bin/term-pdf"
install -m 0644 -- "$kit_dir/neovim/lua/terminal_pdf/init.lua" "$HOME/.local/share/terminal-pdf/neovim/lua/terminal_pdf/init.lua"
printf '%s\n' 'Installed launcher and Lua module. Add neovim/lazy-example.lua to your Lazy specification.'
