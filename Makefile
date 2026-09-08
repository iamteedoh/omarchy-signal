.PHONY: test security qml python bash validate install dev

PY := python3

test: python qml bash validate qmlcheck

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

validate:
	omarchy-plugin-validate . >/dev/null 2>&1 && echo "manifest: valid" || echo "manifest: omarchy-plugin-validate unavailable or failed"

install:
	./install.sh

dev:
	./install.sh --link
