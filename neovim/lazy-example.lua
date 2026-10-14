-- Copy this entry into your existing lazy.nvim plugin specification.
return {
  dir = vim.fn.expand("~/.local/share/terminal-pdf/neovim"),
  name = "terminal-pdf-local",
  ft = "tex",
  config = function()
    require("terminal_pdf").setup({
      window = "0", -- Most recently active Windows Terminal window.
      size = 0.45,  -- Fraction occupied by the new PDF pane.
    })
  end,
}
