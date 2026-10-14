local path = assert(arg[1], "provide Lua source path")
local chunk, err = loadfile(path)
assert(chunk, err)
print("Lua syntax OK: " .. path)
