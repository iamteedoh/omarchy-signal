# SPDX-License-Identifier: GPL-3.0-or-later
.PHONY: test security qml python bash shellcheck validate install dev legend-wrap screenshots tui-screenshots

PY := python3

test: python qml bash shellcheck validate qmlcheck

qmlcheck:
	./scripts/qml-check.sh

python:
	cd tests/python && $(PY) -m unittest -q

security:
	cd tests/python && $(PY) -m unittest -q test_sanitize test_envelope test_protocol_rpc \
	  test_bridge_integration.BridgeIntegrationTests.test_raw_protocol_abuse \
	  test_bridge_integration.BridgeIntegrationTests.test_send_rejects_bad_input \
	  test_bridge_integration.BridgeIntegrationTests.test_socket_permissions
	node --test tests/qml/*.test.js

qml:
	node --test tests/qml/*.test.js

bash:
	bash tests/bash/run.sh
	bash tests/bash/install-e2e.sh

shellcheck:
	shellcheck -S warning install.sh uninstall.sh scripts/*.sh tests/bash/*.sh tests/qml/*.sh

validate:
	omarchy-plugin-validate . >/dev/null 2>&1 && echo "manifest: valid" || echo "manifest: omarchy-plugin-validate unavailable or failed"

install:
	./install.sh

dev:
	./install.sh --link

# Renders ConversationView offscreen at several widths to prove the bottom
# legend wraps. Needs Quickshell and the Omarchy shell, so it is not part of `make test`.
legend-wrap:
	./tests/qml/legend-wrap.sh

# Re-renders docs/media and preview.png offscreen against the demo bridge:
# fictional contacts, a throwaway HOME, nothing captured from the screen.
screenshots:
	./scripts/demo-shots.sh

# Re-takes the terminal client pictures (docs/media/tui-*.png): Ghostty in a
# private nested Hyprland against the demo bridge. Needs a Hyprland session;
# the nested compositor's window shows on the desktop for a few seconds.
tui-screenshots:
	./scripts/demo-tui-shots.sh
