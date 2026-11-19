@tool
extends VBoxContainer
## The OpenCode dock: this project's conversation inside the editor. A plain client of the OpenCode service through a
## Private sidecar: Godot offers its project (link.gd), and this dock attaches the current folder's conversation.

var link: RefCounted

var _pid := -1
var _stdio: FileAccess
var _stderr: FileAccess
var _buffer := PackedByteArray()
var _channel := ""
var _target := {}
var _pending: Array[Dictionary] = []
var _selected := ""
var _draft := false
var _retry_at := 0
var _launch_at := 0
var _problem := ""
var _next := 0
var _active := false
var _connected := false
var _busy := false
var _streaming := false
var _permission := ""
var _models: Array = []
var _model := ""

var _session_label := Label.new()
var _status_label := Label.new()
var _agent_label := Label.new()
var _model_button := OptionButton.new()
var _conversations := OptionButton.new()
var _transcript := RichTextLabel.new()
var _permission_row := HBoxContainer.new()
var _permission_label := Label.new()
var _prompt := TextEdit.new()
var _send_button := Button.new()
var _stop_button := Button.new()


func _ready() -> void:
	_channel = "dock-" + Crypto.new().generate_random_bytes(8).hex_encode()
	var header := HBoxContainer.new()
	var brand := Label.new()
	brand.text = "OpenCode"
	header.add_child(brand)
	_session_label.text = ""
	_session_label.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_session_label.clip_text = true
	header.add_child(_session_label)
	_status_label.text = "CONNECTING"
	header.add_child(_status_label)
	add_child(header)

	var row := HBoxContainer.new()
	_agent_label.text = ""
	row.add_child(_agent_label)
	_model_button.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_model_button.disabled = true
	_model_button.clip_text = true
	_model_button.item_selected.connect(_select_model)
	row.add_child(_model_button)
	_conversations.clip_text = true
	_conversations.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_conversations.add_item("New conversation")
	_conversations.set_item_metadata(0, "")
	_conversations.get_popup().about_to_popup.connect(func(): _send("list", {}))
	_conversations.item_selected.connect(func(index: int):
		var id: String = _conversations.get_item_metadata(index)
		if id == "":
			_send("new_session", _target)
		else:
			_send("open", {"session_id": id})
	)
	row.add_child(_conversations)
	add_child(row)

	_transcript.size_flags_vertical = Control.SIZE_EXPAND_FILL
	_transcript.bbcode_enabled = true
	_transcript.scroll_following = true
	_transcript.selection_enabled = true
	_transcript.custom_minimum_size = Vector2(0, 200)
	add_child(_transcript)

	_permission_row.visible = false
	_permission_label.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_permission_label.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	_permission_row.add_child(_permission_label)
	var reject := Button.new()
	reject.text = "Reject"
	reject.pressed.connect(func(): _reply_permission("reject"))
	_permission_row.add_child(reject)
	var allow := Button.new()
	allow.text = "Allow once"
	allow.pressed.connect(func(): _reply_permission("once"))
	_permission_row.add_child(allow)
	add_child(_permission_row)

	_prompt.placeholder_text = "Ask OpenCode about this project... Ctrl+Enter to send"
	_prompt.custom_minimum_size = Vector2(0, 80)
	_prompt.wrap_mode = TextEdit.LINE_WRAPPING_BOUNDARY
	_prompt.gui_input.connect(_prompt_input)
	add_child(_prompt)
	var buttons := HBoxContainer.new()
	buttons.alignment = BoxContainer.ALIGNMENT_END
	_stop_button.text = "Stop"
	_stop_button.disabled = true
	_stop_button.pressed.connect(stop)
	buttons.add_child(_stop_button)
	_send_button.text = "Send"
	_send_button.pressed.connect(func(): send_text(_prompt.text))
	buttons.add_child(_send_button)
	add_child(buttons)


func _exit_tree() -> void:
	if _stdio != null:
		# The sidecar ends once its input closes, after releasing its conversations.
		_stdio.close()
		_stdio = null


# --- what a user (or a script driving the dock) does ---

func send_text(text: String) -> void:
	text = text.strip_edges()
	if _busy or text == "":
		return
	_prompt.text = ""
	_send("prompt", {"text": text})


func stop() -> void:
	_send("interrupt", {})


func transcript() -> String:
	return _transcript.get_parsed_text()


func state() -> Dictionary:
	return {"active": _active, "connected": _connected, "busy": _busy, "status": _status_label.text, "model": _model}


# --- the sidecar ---

static func _node() -> String:
	var configured := OS.get_environment("NEURALNOMADS_GODOT_NODE")
	if configured != "" and FileAccess.file_exists(configured):
		return configured
	var windows := OS.get_name() == "Windows"
	for directory in OS.get_environment("PATH").split(";" if windows else ":", false):
		var candidate := directory.path_join("node.exe" if windows else "node")
		if FileAccess.file_exists(candidate):
			return candidate
	return ""


func _start() -> void:
	var services: Array = link.advertiser_results()
	var runtime := ""
	var ready := false
	var waiting := Time.get_ticks_msec() < _launch_at
	for result in services:
		if not result.get("outcome") in ["accepted", "renewed"]:
			continue
		ready = true
		var advertised: String = result.get("panel_runtime", "")
		if advertised.is_absolute_path() and FileAccess.file_exists(advertised):
			runtime = advertised
			break
	var bun := runtime != ""
	if not bun and (ready or not waiting):
		runtime = _node()
	var sidecar := ProjectSettings.globalize_path(get_script().resource_path.get_base_dir().path_join("sidecar.cjs"))
	if not FileAccess.file_exists(sidecar):
		_blocked("The OpenCode dock's sidecar is missing: install OpenCode Link again (godot_install_host).")
		return
	if runtime == "":
		if not waiting:
			_blocked("No OpenCode runtime, NEURALNOMADS_GODOT_NODE executable, or Node on PATH.")
		return
	var inherited := OS.get_environment("BUN_BE_BUN")
	var had_inherited := OS.has_environment("BUN_BE_BUN")
	if bun:
		OS.set_environment("BUN_BE_BUN", "1")
	else:
		OS.unset_environment("BUN_BE_BUN")
	var started := OS.execute_with_pipe(runtime, [sidecar], false)
	if had_inherited:
		OS.set_environment("BUN_BE_BUN", inherited)
	else:
		OS.unset_environment("BUN_BE_BUN")
	if started.is_empty():
		_blocked("The OpenCode sidecar runtime did not start.")
		return
	_pid = started.pid
	_stdio = started.stdio
	_stderr = started.stderr
	_buffer = PackedByteArray()
	_problem = ""
	var params := _target.duplicate()
	params.session_id = _selected
	params.draft = _draft
	_send("connect", params)
	for item in _pending:
		_send(item.method, item.params)
	_pending.clear()


func _blocked(message: String) -> void:
	if message != _problem:
		_problem = message
		_event("error", {"message": message})


func _send(method: String, params: Dictionary) -> void:
	if _stdio == null or not OS.is_process_running(_pid):
		if method == "interrupt":
			_pending.clear()
		elif method == "prompt":
			_pending.append({"method": method, "params": params})
		return
	_next += 1
	var line := JSON.stringify({"id": str(_next), "channel": _channel, "method": method, "params": params}) + "\n"
	_stdio.store_buffer(line.to_utf8_buffer())


func _process(_delta: float) -> void:
	if not _active:
		if not is_visible_in_tree():
			return
		_active = true
		_launch_at = Time.get_ticks_msec() + 5000
	_sync()
	if _stdio == null:
		if Time.get_ticks_msec() >= _retry_at:
			_retry_at = Time.get_ticks_msec() + 1000
			_start()
		return
	var available := _stdio.get_length()
	if available > 0:
		_buffer.append_array(_stdio.get_buffer(available))
		var newline := _buffer.find(10)
		while newline >= 0:
			var line := _buffer.slice(0, newline).get_string_from_utf8()
			_buffer = _buffer.slice(newline + 1)
			newline = _buffer.find(10)
			var parser := JSON.new()
			if line.strip_edges() == "" or parser.parse(line) != OK or not (parser.data is Dictionary):
				continue
			var message: Dictionary = parser.data
			if message.get("channel") == _channel and message.get("event") is String:
				_event(message.event, message.get("data", {}) if message.get("data") is Dictionary else {})
			elif message.get("error") is Dictionary and not message.has("id"):
				_event("error", message.error)
	if _stderr != null and _stderr.get_length() > 0:
		_stderr.get_buffer(_stderr.get_length())
	if not OS.is_process_running(_pid):
		_stdio = null
		_stderr = null
		_pid = -1
		_event("status", {"state": "connecting"})


func _sync() -> void:
	if link == null or not _active:
		return
	var project: String = link.project_dir()
	var scene := EditorInterface.get_edited_scene_root()
	var directory := project
	if scene != null and scene.scene_file_path.begins_with("res://"):
		directory = link.native(ProjectSettings.globalize_path(scene.scene_file_path).get_base_dir())
	var params: Dictionary = link.info()
	params.merge({
		"script_handle": link.script_handle,
		"offer_label": link.label(),
		"directory": directory,
	})
	if params != _target:
		if directory != _target.get("directory"):
			_selected = ""
			_draft = false
			_pending.clear()
		_target = params
		_send("connect", params)


func _select_model(index: int) -> void:
	var key := _model_button.get_item_metadata(index) as String
	if _busy or not _connected or key == "" or key == _model:
		return
	_send("switch_model", {"model": key})


func _prompt_input(event: InputEvent) -> void:
	if event is InputEventKey and event.pressed and event.keycode == KEY_ENTER and event.is_command_or_control_pressed():
		send_text(_prompt.text)
		_prompt.accept_event()


func _reply_permission(reply: String) -> void:
	if _permission == "":
		return
	_send("permission_reply", {"request_id": _permission, "reply": reply})
	_permission = ""
	_permission_row.visible = false


func _set_busy(busy: bool) -> void:
	_busy = busy
	_stop_button.disabled = not busy and _status_label.text != "CONNECTING"
	_send_button.disabled = busy
	_prompt.editable = not busy
	_model_button.disabled = not _connected or busy or _models.is_empty()


func _set_model(model: String) -> void:
	if model == "":
		return
	_model = model
	for i in _model_button.item_count:
		if _model_button.get_item_metadata(i) == model:
			_model_button.select(i)
			return
	_model_button.add_item(model.get_slice("/", 1) if model.contains("/") else model)
	_model_button.set_item_metadata(_model_button.item_count - 1, model)
	_model_button.select(_model_button.item_count - 1)


func _event(name: String, data: Dictionary) -> void:
	if not _active and name != "error":
		return  # a reply to a connection this dock has since closed
	match name:
		"session":
			_connected = true
			_selected = str(data.id) if data.get("id") else ""
			_draft = data.get("draft", false)
			_session_label.text = "Session " + _selected.right(8) if _selected != "" else "New conversation"
			_session_label.tooltip_text = "Workspace: " + str(data.get("workspace", ""))
			_agent_label.text = str(data.get("agent", "")) if data.get("agent") else "default agent"
			_set_model(str(data.get("model", "")))
		"conversations":
			_conversations.clear()
			_conversations.add_item("New conversation")
			_conversations.set_item_metadata(0, "")
			var index := 0
			for item in data.get("entries", []):
				var title: String = item.get("title", "")
				_conversations.add_item(title if title != "" else "Untitled")
				_conversations.set_item_metadata(_conversations.item_count - 1, item.id)
				if item.id == _selected:
					index = _conversations.item_count - 1
			_conversations.select(index)
		"notice":
			_append("system", data.message)
		"user_message":
			_append("user", data.text)
		"models":
			_models = data.get("models", [])
			_model_button.clear()
			for model in _models:
				var key := "%s/%s" % [model.get("provider_id", ""), model.get("id", "")]
				_model_button.add_item(str(model.get("name", key)))
				_model_button.set_item_metadata(_model_button.item_count - 1, key)
			_set_model(str(data.get("selected", "")))
			_model_button.disabled = _models.is_empty() or _busy
		"model":
			_set_model(str(data.get("selected", "")))
		"agent":
			_agent_label.text = str(data.get("agent", ""))
		"history":
			_transcript.clear()
			for message in data.get("messages", []):
				_append(str(message.get("role", "system")), str(message.get("text", "")))
		"assistant_start":
			_start_assistant()
		"assistant_delta":
			if not _streaming:
				_start_assistant()
			_transcript.append_text(_escape(str(data.get("text", ""))))
		"assistant_end":
			_streaming = false
			_transcript.append_text("\n")
		"tool":
			_append("tool", "%s / %s" % [data.get("name", data.get("id", "tool")), data.get("state", "")])
		"permission":
			_permission = str(data.get("request_id", ""))
			var resources: Array = data.get("resources", [])
			_permission_label.text = "Permission: %s%s" % [data.get("action", ""), (" / " + ", ".join(resources)) if resources else ""]
			_permission_row.visible = true
		"status":
			var value := str(data.get("state", "unknown"))
			_status_label.text = value.to_upper()
			if value == "connecting":
				_connected = false
				_permission = ""
				_permission_row.hide()
			_set_busy(value in ["busy", "retry"])
		"error":
			_append("error", str(data.get("message", "Unknown OpenCode error")))
			_status_label.text = "ERROR"
			_streaming = false
			_set_busy(false)


static func _escape(text: String) -> String:
	return text.replace("[", "[lb]")


const COLORS := {"user": "#e7e9eb", "assistant": "#b9ddd7", "tool": "#d5a657", "error": "#ef7777", "system": "#8e969c"}
const LABELS := {"user": "YOU", "assistant": "OPENCODE", "tool": "TOOL", "error": "ERROR", "system": "SYSTEM"}


func _append(role: String, text: String) -> void:
	_streaming = false
	var color: String = COLORS.get(role, COLORS.system)
	if _transcript.get_parsed_text() != "":
		_transcript.append_text("\n")
	_transcript.append_text("[color=%s][b]%s[/b][/color]\n[color=%s]%s[/color]\n" % [COLORS.tool if role == "tool" else color, LABELS.get(role, role.to_upper()), color, _escape(text)])


func _start_assistant() -> void:
	if _streaming:
		return
	_streaming = true
	if _transcript.get_parsed_text() != "":
		_transcript.append_text("\n")
	_transcript.append_text("[color=%s][b]OPENCODE[/b][/color]\n" % COLORS.assistant)
