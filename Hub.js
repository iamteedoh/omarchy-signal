// SPDX-License-Identifier: GPL-3.0-or-later
// The one thing the plugin's components share in memory: a reference to the
// running Service.qml. The bar widget and the service are loaded as separate
// entry points, and without this the widget could only reach the service by
// starting a process that calls back into the same shell over IPC.
//
// A stateless-library pragma makes this file one instance per QML engine. If a
// hot reload ever hands a component a fresh instance, `service` is simply null
// there and the caller falls back to IPC, so nothing depends on it being set.
.pragma library

var service = null

function register(item) { service = item }

function unregister(item) { if (service === item) service = null }
