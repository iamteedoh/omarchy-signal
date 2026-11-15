-- SPDX-License-Identifier: GPL-3.0-or-later
-- Author: Tito Valentin
-- Name of Program: omarchy-signal keybindings handler tests
-- Date Created: 2026-11-15
-- Description: Loads config/bindings.lua against stub hl and o tables and drives its Hyprland event handlers.
--
-- Hyprland runs these handlers for every window on the desktop, and a Lua
-- error in one of them shows as a banner over the whole screen. Run with any
-- Lua 5.1+ or LuaJIT from the repo root:  lua tests/lua/bindings_test.lua

local path = arg and arg[1] or "config/bindings.lua"

local function load_bindings()
  local handlers, dispatched = {}, {}
  local function action(kind)
    return function(opts) return { kind = kind, opts = opts } end
  end
  local hl = {
    unbind = function() end,
    on = function(event, fn) handlers[event] = fn end,
    dispatch = function(a) dispatched[#dispatched + 1] = a end,
    dsp = {
      exec_cmd = action("exec_cmd"),
      window = { close = action("close"), float = action("float"), center = action("center") },
    },
  }
  local env = setmetatable({ hl = hl, o = { bind = function() end } }, { __index = _G })
  local chunk, err
  if setfenv then -- Lua 5.1 / LuaJIT
    chunk, err = loadfile(path)
    if chunk then setfenv(chunk, env) end
  else
    chunk, err = loadfile(path, "t", env)
  end
  assert(chunk, err)
  chunk()
  return handlers, dispatched
end

local failed, passed = 0, 0
local function test(name, fn)
  local ok, err = pcall(fn)
  if ok then
    passed = passed + 1
    print("  ok   " .. name)
  else
    failed = failed + 1
    print("  FAIL " .. name .. ": " .. tostring(err))
  end
end

local function chat(address, floating)
  return { class = "org.quickshell", title = "Signal · Trinity", address = address, floating = floating }
end
local console = { name = "special:qconsole" }
local workspace = { name = "2" }

test("window.destroy ignores a window with no address", function()
  local h = load_bindings()
  h["window.destroy"]({})
  h["window.destroy"]({ class = "xdg-desktop-portal-gtk", title = "Open File" })
end)

test("window.destroy ignores a nil window", function()
  local h = load_bindings()
  h["window.destroy"](nil)
end)

test("window.move_to_workspace ignores a chat window with no address", function()
  local h, d = load_bindings()
  h["window.move_to_workspace"](chat(nil, true), console)
  h["window.move_to_workspace"](chat(nil, false), workspace)
  assert(#d == 0, "dispatched " .. #d .. " actions for a window with no address")
end)

test("other windows are left alone", function()
  local h, d = load_bindings()
  h["window.move_to_workspace"]({ class = "firefox", title = "Signal · not us", address = "0x1", floating = true }, console)
  h["window.destroy"]({ class = "firefox", address = "0x1" })
  assert(#d == 0, "dispatched " .. #d .. " actions for another app's window")
end)

test("the chat window tiles on the console and floats again off it", function()
  local h, d = load_bindings()
  h["window.move_to_workspace"](chat("0xabc", true), console)
  assert(#d == 1 and d[1].kind == "float", "expected one float action on the console")
  assert(d[1].opts.action == "disable" and d[1].opts.window == "address:0xabc", "expected float disable for address:0xabc")
  h["window.move_to_workspace"](chat("0xabc", false), workspace)
  assert(#d == 3, "expected float enable and center off the console, got " .. #d .. " actions")
  assert(d[2].kind == "float" and d[2].opts.action == "enable" and d[2].opts.window == "address:0xabc", "expected float enable")
  assert(d[3].kind == "center" and d[3].opts.window == "address:0xabc", "expected center")
end)

test("a chat window closed on the console is forgotten", function()
  local h, d = load_bindings()
  h["window.move_to_workspace"](chat("0xabc", true), console)
  h["window.destroy"](chat("0xabc", false))
  -- A new window that reuses the address and was never docked stays tiled.
  h["window.move_to_workspace"](chat("0xabc", false), workspace)
  assert(#d == 1, "expected no refloat after the docked window closed, got " .. #d .. " actions")
end)

print(string.format("bindings.lua handlers: %d passed, %d failed", passed, failed))
os.exit(failed == 0 and 0 or 1)
