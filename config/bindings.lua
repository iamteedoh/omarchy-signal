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
