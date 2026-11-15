-- SPDX-License-Identifier: GPL-3.0-or-later
-- omarchy-signal keybindings. install.sh and `omarchy-signal sync` write
-- everything below the first blank line into ~/.config/hypr/bindings.lua,
-- between their own markers. Edits made there are replaced on the next
-- install or update, so change this file (or pass --no-bind) instead.

-- SUPER+SHIFT+G is Omarchy's Signal key; point it at the terminal client.
hl.unbind("SUPER + SHIFT + G")
o.bind("SUPER + SHIFT + G", "Signal", "omarchy-signal open")
-- New conversation from anywhere: the bar widget's searchable contact list.
o.bind("SUPER + CTRL + G", "Signal: new conversation", "omarchy-shell iamteedoh.signal.bar toggle")
-- Detached conversation windows float themselves (720x640, centred) when they
-- open; SUPER+ALT+S moves one to the scratchpad, SUPER+S brings it back.
-- The popup conversation and the linking QR code are layer surfaces, not
-- windows, so SUPER+W closes them itself while one is on screen, and closes
-- the active window as usual otherwise.
local signal_layers = { ["omarchy-signal-reply"] = { open = 0, ipc = "close" }, ["omarchy-signal-qr"] = { open = 0, ipc = "hideQr" } }
hl.on("layer.opened", function(layer)
  local l = signal_layers[layer.namespace]
  if l then l.open = l.open + 1 end
end)
hl.on("layer.closed", function(layer)
  local l = signal_layers[layer.namespace]
  if l and l.open > 0 then l.open = l.open - 1 end
end)
hl.unbind("SUPER + W")
o.bind("SUPER + W", "Close window", function()
  for _, l in pairs(signal_layers) do
    if l.open > 0 then
      hl.dispatch(hl.dsp.exec_cmd("omarchy-shell iamteedoh.signal " .. l.ipc))
      return
    end
  end
  hl.dispatch(hl.dsp.window.close())
end)
-- The chat window floats, and the Quake console (SUPER+grave) only lays out
-- tiled windows: a floating one sent there with SUPER+SHIFT+grave keeps the
-- place and size it had on the workspace and hangs over the console instead
-- of sitting in it. So the chat window is tiled while it is on the console,
-- and floats again when it is moved back off.
local signal_docked = {}
local function signal_chat(window)
  return window and window.class == "org.quickshell" and (window.title or ""):find("Signal · ", 1, true) == 1
end
hl.on("window.move_to_workspace", function(window, ws)
  if not ws or not signal_chat(window) then return end
  local on_console = ws.name == "special:qconsole"
  if on_console and window.floating then
    signal_docked[window.address] = true
    hl.dispatch(hl.dsp.window.float({ action = "disable", window = "address:" .. window.address }))
  elseif not on_console and signal_docked[window.address] then
    signal_docked[window.address] = nil
    if not window.floating then
      hl.dispatch(hl.dsp.window.float({ action = "enable", window = "address:" .. window.address }))
      hl.dispatch(hl.dsp.window.center({ window = "address:" .. window.address }))
    end
  end
end)
hl.on("window.destroy", function(window)
  if window then signal_docked[window.address] = nil end
end)
