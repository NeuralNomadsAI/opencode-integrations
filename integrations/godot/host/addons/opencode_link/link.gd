@tool
extends RefCounted
## OpenCode Link for Godot: the editor's listener and its requests.
##
## Listens on 127.0.0.1 (a random port, a token kept in memory) and offers this editor's project to the OpenCode
## services every 5 seconds (service.gd), labelled "Godot: <project> — <scene>". Each request is one line of JSON on
## its own connection and thread. hello and status answer on that thread; every other request is queued for the
## editor's main loop (pump(), called by the EditorPlugin each frame), and a request the main loop has not taken
## within 10 s is answered busy, with what runs there and since when.

const Service := preload("service.gd")
const Runner := preload("runner.gd")

const PROTOCOL := 1
const MAX_REQUEST_BYTES := 1024 * 1024
const QUEUE_WAIT_MS := 10000
const RUN_LIMIT_MS := 3600 * 1000
const METHODS := ["hello", "status", "context", "execute_code", "release", "cancel", "resume"]
const MESSAGE_MAX := 2000

var plugin: EditorPlugin
var version := "0.0.0"
var script_handle := ""
var offer_key := ""
var token := ""
var port := 0
var revision := 0
var quarantine: Variant = null  # {cause, release, ...} while a failed change could not be proven undone
var runner: RefCounted

var _server := TCPServer.new()
var _serving := Thread.new()
var _handlers: Array = []
var _stopping := false
var _advertiser: RefCounted = null
var _project := ""
var _hello := {}
var _label := ""

var _mutex := Mutex.new()
var _jobs: Array = []
var _running: Variant = null  # [label, since]
var _cancelled := {}  # session_id: true, after a panel's Stop


func _init(owner: EditorPlugin) -> void:
	plugin = owner
	var config := ConfigFile.new()
	if config.load(get_script().resource_path.get_base_dir().path_join("plugin.cfg")) == OK:
		version = str(config.get_value("plugin", "version", version))
	var crypto := Crypto.new()
	token = crypto.generate_random_bytes(32).hex_encode()
	script_handle = "godot:%d:%s" % [OS.get_process_id(), crypto.generate_random_bytes(6).hex_encode()]
	offer_key = "godot-%d-%s" % [OS.get_process_id(), crypto.generate_random_bytes(8).hex_encode()]
	_project = native(ProjectSettings.globalize_path("res://"))
	runner = Runner.new(self)
	var capabilities := {
		"methods": METHODS.duplicate(), "operations": [], "undo": "grouped_best_effort", "background": false,
	}
	_hello = {
		"protocol": PROTOCOL,
		"host_version": version,
		"app_version": Engine.get_version_info().string,
		"capabilities": capabilities,
		"capability_hash": JSON.stringify(capabilities, "", true).sha256_text(),
		# The folder of this process's crash report (runner.gd's Capture writes it when Godot's crash handler runs).
		"process": {"pid": OS.get_process_id(), "temp_dir": native(OS.get_user_data_dir())},
	}
	refresh_label()


## A path as this computer writes it (Godot's own paths use forward slashes).
static func native(path: String) -> String:
	var clean := path.trim_suffix("/") if path.length() > 3 else path
	return clean.replace("/", "\\") if OS.get_name() == "Windows" else clean


func project_dir() -> String:
	return _project


func project_name() -> String:
	var name := str(ProjectSettings.get_setting("application/config/name", ""))
	return name if name.strip_edges() != "" else _project.get_file()


static func _clean(text: String) -> String:
	var out := ""
	for character in text:
		var code := character.unicode_at(0)
		if code >= 32 and code != 127:
			out += "-" if character in ["/", "\\"] else character
	return out.strip_edges()


func scene_name() -> String:
	var root := EditorInterface.get_edited_scene_root()
	if root == null:
		return "no scene"
	return root.scene_file_path.get_file() if root.scene_file_path != "" else "%s (unsaved)" % root.name


## The offer's label follows the edited scene (read on the main thread).
func refresh_label() -> void:
	var label := ("Godot: %s — %s" % [_clean(project_name()), _clean(scene_name())]).left(120)
	_mutex.lock()
	_label = label
	_mutex.unlock()


func bump() -> void:
	_mutex.lock()
	revision += 1
	_mutex.unlock()


func label() -> String:
	_mutex.lock()
	var value := _label
	_mutex.unlock()
	return value


func start(advertise: bool) -> Error:
	var error := _server.listen(0, "127.0.0.1")
	if error != OK:
		return error
	port = _server.get_local_port()
	_serving.start(_serve)
	if advertise:
		_advertiser = Service.new(_snapshot)
		_advertiser.start()
	return OK


func stop() -> void:
	_stopping = true
	if _advertiser != null:
		_advertiser.stop(_project)
		_advertiser = null
	if _serving.is_started():
		_serving.wait_to_finish()
	_server.stop()
	for thread in _handlers:
		thread.wait_to_finish()
	_handlers.clear()
	_mutex.lock()
	for job in _jobs:
		job.result = error("busy", "The Godot editor is closing.")
		job.state = "done"
		job.done.post()
	_jobs.clear()
	_mutex.unlock()
	token = Crypto.new().generate_random_bytes(32).hex_encode()
	runner.close()


func info() -> Dictionary:
	return {"host": "127.0.0.1", "port": port, "token": token, "protocol": PROTOCOL}


func advertising() -> bool:
	return _advertiser != null


func advertiser_results() -> Array:
	return _advertiser.results() if _advertiser != null else []


func _snapshot() -> Dictionary:
	return {"port": port, "token": null if _stopping else token, "offers": [[script_handle, label(), offer_key]], "project": _project}


static func error(code: String, message: String, data := {}) -> Dictionary:
	return {"__host_error": true, "code": code, "message": message.left(MESSAGE_MAX), "data": data}


static func is_error(value: Variant) -> bool:
	return value is Dictionary and value.get("__host_error") == true


# --- the main loop's queue ---

func running() -> Dictionary:
	_mutex.lock()
	var now: Variant = _running
	var queued := _jobs.size()
	_mutex.unlock()
	if now == null:
		return {"busy": false, "queued": queued}
	return {"busy": true, "running": now[0], "seconds": snappedf((Time.get_ticks_msec() - now[1]) / 1000.0, 0.1), "queued": queued}


## Called by the EditorPlugin every frame, on the main thread. Never one request inside another.
func pump() -> void:
	while true:
		_mutex.lock()
		if _running != null or _jobs.is_empty():
			_mutex.unlock()
			return
		var job: Dictionary = _jobs.pop_front()
		job.state = "running"
		_running = [job.label, Time.get_ticks_msec()]
		_mutex.unlock()
		var result: Variant = job.work.call()
		if result == null:
			result = error("internal", "OpenCode Link failed on this request; the editor's Output names the error.")
		_mutex.lock()
		job.result = result
		job.state = "done"
		_running = null
		_mutex.unlock()
		job.done.post()


func _on_main(label: String, work: Callable, session_id: String) -> Variant:
	var job := {"label": label, "work": work, "state": "queued", "result": null, "done": Semaphore.new(), "session": session_id}
	_mutex.lock()
	if _cancelled.has(session_id):
		_mutex.unlock()
		return error("busy", "The conversation was stopped before Godot ran this request.", {"reason": "cancelled"})
	_jobs.append(job)
	_mutex.unlock()
	var asked := Time.get_ticks_msec()
	while true:
		_mutex.lock()
		var state: String = job.state
		if state == "queued" and (Time.get_ticks_msec() - asked > QUEUE_WAIT_MS or _stopping):
			_jobs.erase(job)
			_mutex.unlock()
			var now := running()
			var doing: String = (
				"it still runs %s (%s s so far): nothing was done for this request; ask again once it has finished" % [now.running, now.seconds]
				if now.busy else "a modal dialog, a long import or a script outside OpenCode holds it"
			)
			return error("busy", "Godot's main thread did not take the request within %d s: %s" % [QUEUE_WAIT_MS / 1000, doing],
				{"reason": "ui_timeout", "running": now.get("running"), "seconds": now.get("seconds")})
		_mutex.unlock()
		if state != "queued":
			break
		OS.delay_msec(5)
	while not job.done.try_wait():
		if Time.get_ticks_msec() - asked > RUN_LIMIT_MS:
			return error("busy", "Godot did not finish the request within %d s" % (RUN_LIMIT_MS / 1000))
		OS.delay_msec(5)
	return job.result


# --- requests ---

func quick_hello(handle: Variant) -> Variant:
	if handle is String and handle != "" and handle != script_handle:
		return error("stale_handle", "Unknown or expired script_handle")
	var hello := _hello.duplicate(true)
	var label := label()
	_mutex.lock()
	hello.revision = revision
	_mutex.unlock()
	hello.script_handle = script_handle
	hello.document = label.trim_prefix("Godot: ")
	return hello


func dispatch(method: String, params: Dictionary) -> Variant:
	if method == "status":
		return running()
	if method == "hello":
		return quick_hello(params.get("script_handle"))
	if not method in METHODS:
		return error("method_not_found", "Unknown Godot host method: " + method)
	if str(params.get("script_handle", "")) != script_handle:
		return error("stale_handle", "Unknown or expired script_handle")
	var session := str(params.get("session_id", ""))
	if method in ["cancel", "resume"]:
		if session == "":
			return error("invalid_request", "session_id is required")
		_mutex.lock()
		if method == "cancel":
			_cancelled[session] = true
			for job in _jobs.duplicate():
				if job.session == session:
					_jobs.erase(job)
					job.result = error("busy", "The conversation was stopped before Godot ran this request.", {"reason": "cancelled"})
					job.state = "done"
					job.done.post()
		else:
			_cancelled.erase(session)
		_mutex.unlock()
		return {"cancelled": method == "cancel"}
	var work: Callable
	if method == "context":
		work = func(): return context(params)
	elif method == "execute_code":
		work = func(): return runner.execute(params)
	else:
		work = func(): return runner.release(params)
	return _on_main(method, work, session)


func scene_root() -> Node:
	return EditorInterface.get_edited_scene_root()


## A node path as the agent reads it: relative to the scene root ("." for the root itself).
static func path_of(root: Node, node: Node) -> String:
	return "." if node == root else str(root.get_path_to(node))


func context(params: Dictionary) -> Dictionary:
	var root := scene_root()
	var unsaved := Array(EditorInterface.get_unsaved_scenes())
	var out := {
		"script_handle": script_handle,
		"revision": revision,
		"godot": Engine.get_version_info().string,
		"executable": OS.get_executable_path(),
		"project": {
			"name": project_name(), "path": _project,
			"main_scene": str(ProjectSettings.get_setting("application/run/main_scene", "")),
		},
		"open_scenes": Array(EditorInterface.get_open_scenes()),
		"scene": null,
	}
	if quarantine != null:
		out.quarantined = true
		out.quarantine = quarantine
	if root == null:
		return out
	out.scene = {
		"path": root.scene_file_path, "root": str(root.name), "class": root.get_class(),
		"unsaved": root.scene_file_path == "" or root.scene_file_path in unsaved,
	}
	var selection := []
	for node in EditorInterface.get_selection().get_selected_nodes():
		if node == root or root.is_ancestor_of(node):
			selection.append(path_of(root, node))
	out.selection = selection
	var start: Node = root
	if params.get("node") is String and params.node != "" and params.node != ".":
		start = root.get_node_or_null(NodePath(params.node))
		if start == null:
			return error("invalid_args", "No node %s in the open scene." % params.node, {"children": _children(root)})
	var limit := clampi(int(params.get("limit", 40)), 0, 500)
	var depth := clampi(int(params.get("depth", 3)), 0, 32)
	var pattern := str(params.get("match", ""))
	var properties: Array = params.get("properties", []) if params.get("properties") is Array else []
	var nodes := []
	var total := 0
	var queue := [[start, 0]]
	while not queue.is_empty():
		var item: Array = queue.pop_front()
		var node: Node = item[0]
		if node != root and node.owner != root:
			continue
		total += 1
		var picked := pattern == "" or node.name.matchn(pattern) or node.get_class().matchn(pattern)
		if picked and nodes.size() < limit:
			nodes.append(_card(root, node, properties))
		if item[1] < depth:
			for child in node.get_children():
				queue.append([child, item[1] + 1])
	out.nodes = nodes
	out.nodes_listed = nodes.size()
	out.nodes_seen = total
	return out


static func _children(node: Node) -> Array:
	return node.get_children().map(func(child): return str(child.name)).slice(0, 50)


func _card(root: Node, node: Node, properties: Array) -> Dictionary:
	var card := {"path": path_of(root, node), "class": node.get_class(), "children": node.get_child_count()}
	var script: Variant = node.get_script()
	if script is Script:
		card.script = script.resource_path
	if node != root and node.scene_file_path != "":
		card.instance = node.scene_file_path
	if properties:
		var values := {}
		for name in properties:
			if name in node:
				values[name] = Runner.json_value(node.get(name))
		card.values = values
	return card


# --- the listener ---

func _serve() -> void:
	while not _stopping:
		if _server.is_connection_available():
			var peer := _server.take_connection()
			var thread := Thread.new()
			_handlers.append(thread)
			thread.start(_handle.bind(peer))
		else:
			OS.delay_msec(10)
		for thread in _handlers.duplicate():
			if not thread.is_alive():
				thread.wait_to_finish()
				_handlers.erase(thread)


static func _same(a: String, b: String) -> bool:
	if a.length() != b.length():
		return false
	var difference := 0
	for i in a.length():
		difference |= a.unicode_at(i) ^ b.unicode_at(i)
	return difference == 0


func _handle(peer: StreamPeerTCP) -> void:
	var buffer := PackedByteArray()
	var until := Time.get_ticks_msec() + 130000
	var response := {}
	var request_id: Variant = null
	while true:
		peer.poll()
		if _stopping or peer.get_status() != StreamPeerTCP.STATUS_CONNECTED or Time.get_ticks_msec() > until:
			break
		var available := peer.get_available_bytes()
		if available > 0:
			buffer.append_array(peer.get_partial_data(mini(available, MAX_REQUEST_BYTES + 1 - buffer.size()))[1])
			if buffer.find(10) >= 0 or buffer.size() > MAX_REQUEST_BYTES:
				break
		else:
			OS.delay_msec(2)
	var newline := buffer.find(10)
	var result: Variant
	if newline < 0 or newline >= MAX_REQUEST_BYTES:
		result = error("invalid_request", "Godot host request exceeded the size limit" if buffer.size() > MAX_REQUEST_BYTES else "Incomplete request")
	else:
		var parser := JSON.new()
		if parser.parse(buffer.slice(0, newline).get_string_from_utf8()) != OK or not (parser.data is Dictionary):
			result = error("invalid_request", "Malformed request")
		else:
			var request: Dictionary = parser.data
			request_id = request.get("id")
			if not _same(str(request.get("token", "")), token):
				result = error("unauthorized", "Invalid Godot host token")
			else:
				var params: Variant = request.get("params")
				result = dispatch(str(request.get("method", "")), params if params is Dictionary else {})
	if is_error(result):
		var refusal := {"code": result.code, "message": result.message}
		if not result.data.is_empty():
			refusal.data = Runner.json_value(result.data)
		response = {"id": request_id, "ok": false, "error": refusal}
	else:
		response = {"id": request_id, "ok": true, "result": Runner.json_value(result)}
	peer.put_data((JSON.stringify(response) + "\n").to_utf8_buffer())
	peer.disconnect_from_host()
