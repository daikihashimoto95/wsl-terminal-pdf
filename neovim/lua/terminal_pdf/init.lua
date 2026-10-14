-- Windows Terminal / WSL side-by-side PDF integration. Neovim >= 0.10.
local M = {}
local uv = vim.uv or vim.loop
local config
local unix_server

local function absolute(path, base)
  path = vim.fn.expand(path)
  if path:sub(1, 1) ~= "/" then
    path = (base or vim.fn.getcwd()) .. "/" .. path
  end
  path = vim.fs.normalize(path)
  return uv.fs_realpath(path) or path
end

local function root_tex(source)
  local seen = {}
  for _ = 1, 8 do
    if seen[source] then error("Cycle in % !TeX root directives") end
    seen[source] = true
    local lines
    if source == vim.api.nvim_buf_get_name(0) then
      lines = vim.api.nvim_buf_get_lines(0, 0, math.min(50, vim.api.nvim_buf_line_count(0)), false)
    else
      local ok, result = pcall(vim.fn.readfile, source, "", 50)
      if not ok then error("Cannot read TeX root: " .. source) end
      lines = result
    end
    local next_root
    for _, line in ipairs(lines) do
      local value = line:match("^%s*%%%s*!%s*[Tt][Ee][Xx]%s+[Rr][Oo][Oo][Tt]%s*=%s*(.-)%s*$")
      if value and value ~= "" then
        next_root = absolute(value, vim.fs.dirname(source))
        break
      end
    end
    if not next_root then return source end
    source = next_root
  end
  error("Too many nested % !TeX root directives")
end

local function context(explicit_pdf)
  local source = vim.api.nvim_buf_get_name(0)
  if source == "" or not source:lower():match("%.tex$") then
    error("Run this command from a named .tex buffer")
  end
  source = absolute(source)
  local root = root_tex(source)
  local cwd = vim.fs.dirname(root)
  local pdf = explicit_pdf or vim.b.term_pdf_path
  if vim.b.vimtex then
    if vim.b.vimtex.root and vim.b.vimtex.root ~= "" then
      cwd = absolute(vim.b.vimtex.root)
    end
    if not pdf then
      local ok, out = pcall(vim.fn.eval, "b:vimtex.out()")
      if ok and type(out) == "string" and out ~= "" then pdf = out end
    end
  end
  if not pdf and config.resolve_pdf then pdf = config.resolve_pdf(0, source, root) end
  if vim.b.term_pdf_cwd then cwd = absolute(vim.b.term_pdf_cwd) end
  pdf = absolute(pdf or (root:gsub("%.[Tt][Ee][Xx]$", ".pdf")), cwd)
  local st = uv.fs_stat(pdf)
  if not st or st.type ~= "file" then
    error("PDF does not exist; compile first or specify :TermPdfOpen /path/to/main.pdf: " .. pdf)
  end
  local server = vim.v.servername
  if server:sub(1, 1) ~= "/" then
    unix_server = unix_server or vim.fn.serverstart()
    server = unix_server
  end
  local uid = uv.getuid and uv.getuid() or 0
  local digest = vim.fn.sha256(server .. "\n" .. pdf):sub(1, 32)
  return {
    pdf = pdf,
    tex = source,
    cwd = cwd,
    server = server,
    socket = "/tmp/tpdf-" .. uid .. "-" .. digest .. ".sock",
    line = tostring(vim.fn.line(".")),
    column = tostring(vim.fn.col(".")),
  }
end

local function run(argv)
  if vim.fn.executable(argv[1]) ~= 1 then
    error("Launcher is not executable: " .. argv[1] .. " (run scripts/install-integration.sh)")
  end
  vim.system(argv, { text = true }, function(result)
    vim.schedule(function()
      if result.code ~= 0 then
        local detail = result.stderr and result.stderr ~= "" and result.stderr or result.stdout
        vim.notify(detail and detail ~= "" and detail or "term-pdf failed", vim.log.levels.ERROR)
      elseif result.stdout and result.stdout ~= "" then
        vim.notify(vim.trim(result.stdout), vim.log.levels.INFO)
      end
    end)
  end)
end

local function invoke(action, explicit_pdf)
  local ok, err = pcall(function()
    local c = context(explicit_pdf)
    if explicit_pdf then vim.b.term_pdf_path = c.pdf end
    local argv = {
      config.launcher, action,
      "--pdf", c.pdf, "--socket", c.socket,
      "--tex", c.tex, "--line", c.line, "--column", c.column,
    }
    if action == "open" then
      vim.list_extend(argv, {
        "--server", c.server, "--cwd", c.cwd,
        "--viewer", config.viewer,
        "--window", tostring(config.window), "--size", tostring(config.size),
      })
      if config.wt_path then vim.list_extend(argv, { "--wt-path", config.wt_path }) end
    end
    run(argv)
  end)
  if not ok then vim.notify(tostring(err), vim.log.levels.ERROR) end
end

function M.open(pdf) invoke("open", pdf ~= "" and pdf or nil) end
function M.forward() invoke("forward") end

function M.setup(opts)
  config = vim.tbl_extend("force", {
    launcher = vim.fn.expand("~/.local/bin/term-pdf"),
    viewer = vim.fn.expand("~/.local/share/terminal-pdf/viewer/bin/tdf"),
    window = "0",
    size = 0.45,
    keymaps = true,
  }, opts or {})
  config.launcher = absolute(config.launcher)
  config.viewer = absolute(config.viewer)
  vim.api.nvim_create_user_command("TermPdfOpen", function(cmd) M.open(cmd.args) end,
    { nargs = "?", complete = "file", force = true })
  vim.api.nvim_create_user_command("TermPdfForward", M.forward, { force = true })
  vim.api.nvim_create_user_command("TermPdfDoctor", function()
    local argv = { config.launcher, "doctor", "--viewer", config.viewer }
    if config.wt_path then vim.list_extend(argv, { "--wt-path", config.wt_path }) end
    run(argv)
  end, { force = true })
  local group = vim.api.nvim_create_augroup("TerminalPdf", { clear = true })
  if config.keymaps then
    local function maps(buf)
      vim.keymap.set("n", "<localleader>po", function() M.open() end,
        { buffer = buf, desc = "Open PDF in Windows Terminal pane" })
      vim.keymap.set("n", "<localleader>pv", M.forward,
        { buffer = buf, desc = "PDF SyncTeX forward search" })
    end
    vim.api.nvim_create_autocmd("FileType", { group = group, pattern = "tex", callback = function(ev) maps(ev.buf) end })
    if vim.bo.filetype == "tex" then maps(vim.api.nvim_get_current_buf()) end
  end
end

return M
