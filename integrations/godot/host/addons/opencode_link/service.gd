@tool
extends RefCounted
## The OpenCode services of this computer, and the offer of this editor to them (a GDScript port of the kit's
## opencode_service.py and of the other hosts' advertisers; Godot runs no Python).
##
## Discovery reads the registrations under $XDG_STATE_HOME/opencode (default ~/.local/state/opencode): service.json
## and service-<name>.json. A registration counts when its URL is loopback and /api/info answers its pid and version.
## Requests never use a proxy and never follow a redirect. advertise and withdraw go to the global configuration
## Location ($XDG_CONFIG_HOME/opencode, default ~/.config/opencode), then to this project's folder when the plugin is
## not loaded there (a plugin declared in the project's own opencode.json). Every call runs off the main thread;
## nothing here logs the password or the token.

const RPC_ID := "neuralnomads.godot"
const PROTOCOL := 1
const TIMEOUT_MS := 3000
# One renewal may wait this long for a busy service: an offer lasts 15 s, renewed every 5 s.
const RENEW_TIMEOUT_MS := 8000
const RENEW_MS := 5000
# Renewals go straight to the services that took the offer; every this many renewals (or after a failure) the
# services are discovered again, which finds a service started since.
const DISCOVER_EVERY := 12
const OUTCOMES := {"advertise": ["accepted", "renewed", "refused"], "withdraw": ["withdrawn", "none"]}

var _snapshot: Callable  # () -> {port, token, offers: [[script_handle, label, offer_key]], project}
var _thread := Thread.new()
var _stop := false
var _mutex := Mutex.new()
var _results: Array = []
var _count := 0
var _targets := {}  # service file: [service, directory]
var _offered := {}  # offer_key: token


func _init(snapshot: Callable) -> void:
	_snapshot = snapshot


static func _home() -> String:
	# The daemon resolves its home like Node's os.homedir(): USERPROFILE on Windows, HOME elsewhere.
	var names := ["USERPROFILE", "HOME"] if OS.get_name() == "Windows" else ["HOME"]
	for name in names:
		if OS.get_environment(name) != "":
			return OS.get_environment(name)
	return ""


static func _join(base: String, parts: Array) -> String:
	var separator := "\\" if OS.get_name() == "Windows" else "/"
	var path := base
	for part in parts:
		path = path.trim_suffix(separator) + separator + part
	return path


static func state_dir() -> String:
	var base := OS.get_environment("XDG_STATE_HOME")
	return _join(base if base != "" else _join(_home(), [".local", "state"]), ["opencode"])


static func config_dir() -> String:
	var base := OS.get_environment("XDG_CONFIG_HOME")
	return _join(base if base != "" else _join(_home(), [".config"]), ["opencode"])


## The global configuration directory, then the project's folder.
static func location_candidates(project: String) -> Array:
	var candidates := [config_dir()]
	if project != "" and project.to_lower().replace("\\", "/") != candidates[0].to_lower().replace("\\", "/"):
		candidates.append(project)
	return candidates


static func _parse_url(url: String) -> Dictionary:
	var found := RegEx.create_from_string("^http://(127\\.0\\.0\\.1|\\[::1\\])(?::(\\d{1,5}))?(/[^?#]*)?$").search(url)
	if found == null:
		return {}
	var host := found.get_string(1)
	return {
		"host": "::1" if host == "[::1]" else host,
		"port": int(found.get_string(2)) if found.get_string(2) != "" else 80,
		"path": found.get_string(3).trim_suffix("/"),
	}


static func _json(text: String) -> Variant:
	var parser := JSON.new()
	return parser.data if text != "" and parser.parse(text) == OK else null


## [status, body] of one request; status -1 when the service is unreachable or does not speak HTTP.
static func request(url: String, path: String, password: Variant, body: Variant, timeout_ms: int) -> Array:
	var target := _parse_url(url)
	if target.is_empty():
		return [-1, null]
	var deadline := Time.get_ticks_msec() + timeout_ms
	var client := HTTPClient.new()
	if client.connect_to_host(target.host, target.port) != OK:
		return [-1, null]
	while client.get_status() in [HTTPClient.STATUS_RESOLVING, HTTPClient.STATUS_CONNECTING]:
		client.poll()
		if Time.get_ticks_msec() > deadline:
			client.close()
			return [-1, null]
		OS.delay_msec(2)
	if client.get_status() != HTTPClient.STATUS_CONNECTED:
		client.close()
		return [-1, null]
	var headers := PackedStringArray(["content-type: application/json", "accept: application/json"])
	if password is String:
		headers.append("authorization: Basic " + Marshalls.utf8_to_base64("opencode:" + password))
	var method := HTTPClient.METHOD_GET if body == null else HTTPClient.METHOD_POST
	if client.request(method, target.path + path, headers, "" if body == null else JSON.stringify(body)) != OK:
		client.close()
		return [-1, null]
	while client.get_status() == HTTPClient.STATUS_REQUESTING:
		client.poll()
		if Time.get_ticks_msec() > deadline:
			client.close()
			return [-1, null]
		OS.delay_msec(2)
	if not client.has_response():
		client.close()
		return [-1, null]
	var code := client.get_response_code()
	var bytes := PackedByteArray()
	while client.get_status() == HTTPClient.STATUS_BODY:
		client.poll()
		var chunk := client.read_response_body_chunk()
		if chunk.is_empty():
			OS.delay_msec(1)
		else:
			bytes.append_array(chunk)
		if Time.get_ticks_msec() > deadline or bytes.size() > 4 * 1024 * 1024:
			client.close()
			return [-1, null]
	client.close()
	return [code, _json(bytes.get_string_from_utf8())]


## Healthy registered services: [{file, url, version, password}].
static func discover(timeout_ms := TIMEOUT_MS) -> Array:
	var directory := state_dir()
	if not DirAccess.dir_exists_absolute(directory):
		return []
	var names := DirAccess.get_files_at(directory)
	var pattern := RegEx.create_from_string("^service(-[A-Za-z0-9._-]{1,64})?\\.json$")
	var found := []
	var sorted := Array(names)
	sorted.sort()
	for name in sorted:
		if pattern.search(name) == null:
			continue
		var info: Variant = _json(FileAccess.get_file_as_string(directory.path_join(name)))
		if not (info is Dictionary) or not (info.get("url") is String) or not (info.get("pid") is float):
			continue
		if _parse_url(info.url.rstrip("/")).is_empty() or not (info.get("version") is String):
			continue
		var password: Variant = info.password if info.get("password") is String else null
		var answer := request(info.url, "/api/info", password, null, timeout_ms)
		var body: Variant = answer[1]
		if answer[0] != 200 or not (body is Dictionary) or not (body.get("pid") is float):
			continue
		if int(body.pid) != int(info.pid) or body.get("version") != info.version:
			continue
		found.append({"file": name, "url": info.url, "version": info.version, "password": password})
	return found


static func call_rpc(service: Dictionary, method: String, payload: Dictionary, directory: String, timeout_ms: int) -> Array:
	var path := "/api/rpc/%s/%s?%s=%s" % [
		RPC_ID.uri_encode(), method.uri_encode(), "location[directory]".uri_encode(), directory.uri_encode()
	]
	return request(service.url, path, service.password, {"input": payload}, timeout_ms)


static func unavailable(status: int, body: Variant) -> bool:
	return status == 404 or (body is Dictionary and body.get("type") in ["rpc.unavailable", "rpc.method_not_found"])


static func outcome(method: String, status: int, body: Variant) -> String:
	var output: Variant = body.get("output") if body is Dictionary else null
	var value: Variant = output.get("outcome") if output is Dictionary else null
	return value if status == 200 and value in OUTCOMES[method] else "error"


## A cold Location registers its plugins asynchronously: only the payload-free status read is repeated, never an
## advertisement or a withdrawal.
static func ready(service: Dictionary, directory: String, deadline: int) -> Array:
	while true:
		var remaining := deadline - Time.get_ticks_msec()
		if remaining <= 0:
			return [400, {"_tag": "RpcError", "type": "rpc.unavailable"}]
		var answer := call_rpc(service, "status", {}, directory, remaining)
		var body: Variant = answer[1]
		var loading: bool = (answer[0] in [400, 404] and body is Dictionary and body.get("_tag") == "RpcError"
			and body.get("type") == "rpc.unavailable")
		if not loading:
			return answer
		OS.delay_msec(50)
	return []


## Withdraws one offer from every service, at the first Location that has the plugin.
static func withdraw_all(offer_key: String, token: String, project: String) -> void:
	for service in discover():
		for directory in location_candidates(project):
			var answer := ready(service, directory, Time.get_ticks_msec() + TIMEOUT_MS)
			if unavailable(answer[0], answer[1]):
				continue
			if answer[0] == 200:
				call_rpc(service, "withdraw", {"offer_key": offer_key, "token": token}, directory, TIMEOUT_MS)
			break


func start() -> void:
	_thread.start(_loop)


func results() -> Array:
	_mutex.lock()
	var copy := _results.duplicate(true)
	_mutex.unlock()
	return copy


func advertise_once() -> Variant:
	var now: Dictionary = _snapshot.call()
	if now.get("token") == null:
		return null
	var offers := []
	for item in now.offers:
		offers.append({
			"host": "127.0.0.1", "port": now.port, "token": now.token, "script_handle": item[0], "label": item[1],
			"protocol": PROTOCOL, "offer_key": item[2],
		})
	var keys := offers.map(func(offer): return offer.offer_key)
	for key in _offered.keys():
		if not key in keys:
			withdraw_all(key, _offered[key], now.project)
			_offered.erase(key)
	var results := _renew(offers, now.project) if offers else []
	for offer in offers:
		_offered[offer.offer_key] = offer.token
	_mutex.lock()
	_results = results
	_mutex.unlock()
	return results


static func _advertise_result(service: String, label: String, answer: Array) -> Dictionary:
	var said := outcome("advertise", answer[0], answer[1])
	var result := {"service": service, "label": label, "outcome": said}
	if said in ["accepted", "renewed"]:
		var runtime: Variant = answer[1].output.get("panel_runtime")
		if runtime is String and runtime.is_absolute_path():
			result.panel_runtime = runtime
	return result


func _renew(offers: Array, project: String) -> Array:
	_count += 1
	var results := []
	if not _targets.is_empty() and _count % DISCOVER_EVERY != 0:
		var failed := false
		for name in _targets:
			var target: Array = _targets[name]
			for offer in offers:
				var answer := call_rpc(target[0], "advertise", offer, target[1], RENEW_TIMEOUT_MS)
				var result := _advertise_result(name, offer.label, answer)
				results.append(result)
				failed = failed or not result.outcome in ["accepted", "renewed"]
		if not failed:
			return results
	var found := {}
	results = []
	for service in discover(RENEW_TIMEOUT_MS):
		var reached := false
		for directory in location_candidates(project):
			var deadline := Time.get_ticks_msec() + RENEW_TIMEOUT_MS
			var answer := ready(service, directory, deadline)
			if unavailable(answer[0], answer[1]):
				continue
			reached = true
			var taken := false
			for offer in offers:
				var sent := [500, null]
				if answer[0] == 200:
					sent = call_rpc(service, "advertise", offer, directory, maxi(500, deadline - Time.get_ticks_msec()))
				var result := _advertise_result(service.file, offer.label, sent)
				results.append(result)
				taken = taken or result.outcome in ["accepted", "renewed"]
			if taken:
				found[service.file] = [service, directory]
			break
		if not reached:
			results.append({"service": service.file, "outcome": "unavailable"})
	_targets = found
	return results


func _loop() -> void:
	while not _stop:
		var results: Variant = advertise_once()
		var taken: bool = results is Array and not results.is_empty()
		if taken:
			for result in results:
				taken = taken and result.get("outcome") in ["accepted", "renewed"]
		# A renewal that failed is tried again soon: an offer lasts 15 s from the last one taken.
		var wait := RENEW_MS if taken else 1000
		var until := Time.get_ticks_msec() + wait
		while not _stop and Time.get_ticks_msec() < until:
			OS.delay_msec(50)


## Stops renewing; withdraws every offer with the token it was announced with.
func stop(project: String) -> void:
	_stop = true
	if _thread.is_started():
		_thread.wait_to_finish()
	for key in _offered.keys():
		withdraw_all(key, _offered[key], project)
	_offered = {}
