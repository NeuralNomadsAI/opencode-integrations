@tool
extends RefCounted
## execute_code and release: GDScript run in the editor on the open scene, as one undo action.
##
## Godot records no undo for a direct change (node.position = ...): the run's changes are read by comparing the scene
## before and after it (the stored properties of every node the scene owns and of its embedded resources, each
## node's parent, position, name and owner). A run that changed the scene becomes one EditorUndoRedoManager action,
## "OpenCode: code", whose undo puts every property, node and position back. A run that fails (a parse or script
## error) is reverted the same way at once and the scene compared with what it was: restored, or quarantined (changes
## refused, reads and read_only runs allowed) until the agent read it and releases it with that read's revision. A
## freed node cannot come back: its deletion is said, and a failed run that freed one is quarantined.
## The run's print() output and the errors Godot logs during it are captured by a Logger (Godot 4.5).

const Hints := preload("hints.gd")

const UNDO_STEP := "OpenCode: code"
const OUTPUT_MAX := 64 * 1024
const LIST_MAX := 40
const HEADER := "@tool\nextends RefCounted\nvar PARAMS = {}\nvar result = {}\nvar scene: Node\nvar undo_redo: EditorUndoRedoManager\n"
const QUARANTINED := ("A failed change could not be proven undone: changes are refused until you read the scene (godot_context) "
	+ "and release the quarantine with that read's revision (godot_context release_quarantine).")


## The run's output and errors, from Godot's loggers; only the main thread's, only while a run captures. Godot's
## editor writes no log of its own (unless started with --log-file, whose path it does not tell): when its crash
## handler writes "Program crashed", the crash and its backtrace go to crash_file, the crash report hello names.
class Capture extends Logger:
	var active := false
	var main := 0
	var output := ""
	var errors: Array = []
	var crash_file := ""
	var _crash: FileAccess = null
	# Terminal colours of the editor's own messages.
	var _ansi := RegEx.create_from_string(r"\x1b\[[0-9;]*[A-Za-z]")

	func _log_message(message: String, error: bool) -> void:
		if error and (_crash != null or message.contains("Program crashed")):
			if _crash == null:
				# The editor saves through a temporary file renamed on close, which a crash never reaches: the file is
				# created first, then written in place.
				var created := FileAccess.open(crash_file, FileAccess.WRITE)
				if created != null:
					created.close()
				_crash = FileAccess.open(crash_file, FileAccess.READ_WRITE)
			if _crash != null:
				_crash.store_string(message)
				_crash.flush()
			return
		if active and OS.get_thread_caller_id() == main and output.length() < OUTPUT_MAX * 2:
			output += _ansi.sub(message, "", true)

	func _log_error(function: String, file: String, line: int, code: String, rationale: String, _notify: bool, type: int, backtraces: Array) -> void:
		if not active or OS.get_thread_caller_id() != main or errors.size() >= 200:
			return
		var frames := []
		for backtrace in backtraces:
			for i in backtrace.get_frame_count():
				frames.append({"function": backtrace.get_frame_function(i), "file": backtrace.get_frame_file(i), "line": backtrace.get_frame_line(i)})
		errors.append({"function": function, "file": file, "line": line, "code": code, "rationale": rationale, "type": type, "frames": frames})


var link: RefCounted
var capture := Capture.new()
# The control characters JSON may not carry raw (Godot's JSON leaves them as they are).
static var CONTROL := RegEx.create_from_string(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


func _init(owner: RefCounted) -> void:
	link = owner
	capture.main = OS.get_thread_caller_id()
	DirAccess.make_dir_recursive_absolute(OS.get_user_data_dir())
	capture.crash_file = OS.get_user_data_dir().path_join("opencode-crash-%d.log" % OS.get_process_id())
	OS.add_logger(capture)


func close() -> void:
	OS.remove_logger(capture)


# --- JSON values ---

static func json_value(value: Variant, depth := 0) -> Variant:
	if depth > 32:
		return str(value)
	match typeof(value):
		TYPE_NIL, TYPE_BOOL, TYPE_INT:
			return value
		TYPE_STRING:
			return CONTROL.sub(value, "", true)
		TYPE_FLOAT:
			if is_nan(value):
				return "NaN"
			if is_inf(value):
				return "Infinity" if value > 0 else "-Infinity"
			return value
		TYPE_STRING_NAME, TYPE_NODE_PATH:
			return str(value)
		TYPE_VECTOR2, TYPE_VECTOR2I, TYPE_VECTOR3, TYPE_VECTOR3I, TYPE_VECTOR4, TYPE_VECTOR4I, TYPE_QUATERNION:
			var items := []
			for i in {TYPE_VECTOR2: 2, TYPE_VECTOR2I: 2, TYPE_VECTOR3: 3, TYPE_VECTOR3I: 3}.get(typeof(value), 4):
				items.append(json_value(value[i], depth + 1))
			return items
		TYPE_COLOR:
			return [value.r, value.g, value.b, value.a]
		TYPE_RECT2, TYPE_RECT2I, TYPE_AABB:
			return {"position": json_value(value.position, depth + 1), "size": json_value(value.size, depth + 1)}
		TYPE_BASIS:
			return [json_value(value.x, depth + 1), json_value(value.y, depth + 1), json_value(value.z, depth + 1)]
		TYPE_TRANSFORM2D:
			return {"x": json_value(value.x), "y": json_value(value.y), "origin": json_value(value.origin)}
		TYPE_TRANSFORM3D:
			return {"basis": json_value(value.basis, depth + 1), "origin": json_value(value.origin, depth + 1)}
		TYPE_PLANE:
			return {"normal": json_value(value.normal), "d": value.d}
		TYPE_OBJECT:
			if value == null or not is_instance_valid(value):
				return null
			if value is Node:
				var root := EditorInterface.get_edited_scene_root() if Engine.is_editor_hint() else null
				if root != null and (value == root or root.is_ancestor_of(value)):
					return {"node": "." if value == root else str(root.get_path_to(value)), "class": value.get_class()}
				return {"node": str(value.get_path()) if value.is_inside_tree() else str(value.name), "class": value.get_class()}
			if value is Resource:
				return {"resource": value.resource_path, "class": value.get_class()} if value.resource_path != "" else {"resource": null, "class": value.get_class()}
			return {"object": value.get_class()}
		TYPE_DICTIONARY:
			var out := {}
			for key in value:
				out[str(key)] = json_value(value[key], depth + 1)
			return out
		TYPE_ARRAY, TYPE_PACKED_BYTE_ARRAY, TYPE_PACKED_INT32_ARRAY, TYPE_PACKED_INT64_ARRAY, TYPE_PACKED_FLOAT32_ARRAY, \
				TYPE_PACKED_FLOAT64_ARRAY, TYPE_PACKED_STRING_ARRAY, TYPE_PACKED_VECTOR2_ARRAY, TYPE_PACKED_VECTOR3_ARRAY, \
				TYPE_PACKED_COLOR_ARRAY, TYPE_PACKED_VECTOR4_ARRAY:
			var items := []
			for i in mini(value.size(), 10000):
				items.append(json_value(value[i], depth + 1))
			return items
	return str(value)


# --- the scene before and after a run ---

static func _local(resource: Resource) -> bool:
	return resource.resource_path == "" or resource.resource_path.contains("::")


static func _copy(value: Variant) -> Variant:
	return value.duplicate(true) if value is Array or value is Dictionary else value


static func _same(a: Variant, b: Variant) -> bool:
	return typeof(a) == typeof(b) and (a == b or (a is float and is_nan(a) and is_nan(b)))


## The stored properties of an object; its embedded resources go to `resources`, each once.
static func _props(object: Object, resources: Dictionary) -> Dictionary:
	var props := {}
	for property in object.get_property_list():
		if not property.usage & PROPERTY_USAGE_STORAGE:
			continue
		var value: Variant = object.get(property.name)
		props[property.name] = _copy(value)
		if value is Resource and _local(value) and not resources.has(value.get_instance_id()):
			resources[value.get_instance_id()] = {"object": value, "props": {}}
			resources[value.get_instance_id()].props = _props(value, resources)
	return props


## The scene's own nodes: the root and every descendant it owns, or that has no owner (added without one); the
## inside of an instanced scene belongs to that scene.
static func _members(root: Node) -> Array:
	var out := [root]
	var stack := [root]
	while not stack.is_empty():
		var node: Node = stack.pop_back()
		for child in node.get_children():
			if child.owner == root or child.owner == null:
				out.append(child)
				stack.append(child)
			elif root.is_ancestor_of(child.owner) or child.owner == root:
				stack.append(child)
	return out


static func snapshot(root: Node) -> Dictionary:
	var nodes := {}
	var resources := {}
	for node in _members(root):
		nodes[node.get_instance_id()] = {
			"node": node, "path": "." if node == root else str(root.get_path_to(node)),
			"parent": node.get_parent() if node != root else null, "index": node.get_index(), "name": str(node.name),
			"owner": node.owner, "props": _props(node, resources),
		}
	return {"nodes": nodes, "resources": resources}


static func _in_scene(root: Node, node: Variant) -> bool:
	return is_instance_valid(node) and (node == root or root.is_ancestor_of(node))


## What the run changed, as the undo action's operations ([object, kind, name, args]) and in words.
static func diff(root: Node, before: Dictionary) -> Dictionary:
	var after := snapshot(root)
	var do_ops := []
	var undo_ops := []
	var refs := {"do": [], "undo": []}
	var said := {"properties": 0, "added": [], "removed": [], "moved": [], "renamed": [], "freed": []}
	var properties := func(object: Object, old: Dictionary, new: Dictionary):
		for name in new:
			if old.has(name) and not _same(old[name], new[name]):
				do_ops.append([object, "property", name, new[name]])
				undo_ops.append([object, "property", name, old[name]])
				said.properties += 1
	for id in before.nodes:
		var was: Dictionary = before.nodes[id]
		var node: Variant = was.node
		if not is_instance_valid(node) or node.is_queued_for_deletion():
			said.freed.append(was.path)
			continue
		if not _in_scene(root, node):
			# Removed from the scene: put back by the undo; only its topmost node moves.
			var parent_id: int = was.parent.get_instance_id() if is_instance_valid(was.parent) else 0
			var topmost: bool = parent_id == 0 or not before.nodes.has(parent_id) or _in_scene(root, before.nodes[parent_id].node)
			if topmost and is_instance_valid(was.parent):
				do_ops.append([was.parent, "method", "remove_child", [node]])
				undo_ops.append([was.parent, "method", "add_child", [node, true]])
				undo_ops.append([was.parent, "method", "move_child", [node, was.index]])
				refs.undo.append(node)
				said.removed.append(was.path)
			if was.owner == root:
				undo_ops.append([node, "property", "owner", root])
			properties.call(node, was.props, _props(node, {}))
			continue
		var now: Dictionary = after.nodes.get(id, {})
		if now.is_empty():
			continue
		if now.parent != was.parent:
			do_ops.append([node, "method", "reparent", [now.parent, false]])
			do_ops.append([now.parent, "method", "move_child", [node, now.index]])
			undo_ops.append([node, "method", "reparent", [was.parent, false]])
			undo_ops.append([was.parent, "method", "move_child", [node, was.index]])
			said.moved.append(was.path)
		elif now.index != was.index:
			do_ops.append([now.parent, "method", "move_child", [node, now.index]])
			undo_ops.append([was.parent, "method", "move_child", [node, was.index]])
			said.moved.append(was.path)
		if now.name != was.name:
			do_ops.append([node, "property", "name", now.name])
			undo_ops.append([node, "property", "name", was.name])
			said.renamed.append("%s -> %s" % [was.path, now.name])
		if now.owner != was.owner or now.parent != was.parent:
			do_ops.append([node, "property", "owner", now.owner])
			undo_ops.append([node, "property", "owner", was.owner])
		properties.call(node, was.props, now.props)
	for id in before.resources:
		var was: Dictionary = before.resources[id]
		if is_instance_valid(was.object):
			properties.call(was.object, was.props, after.resources[id].props if after.resources.has(id) else _props(was.object, {}))
	# Added nodes: the topmost ones go back in by the redo, before the moves and properties that may name them.
	var adds := []
	var owners := []
	for id in after.nodes:
		if before.nodes.has(id):
			continue
		var now: Dictionary = after.nodes[id]
		if now.owner == root:
			owners.append([now.node, "property", "owner", root])
		if after.nodes.has(now.parent.get_instance_id()) and not before.nodes.has(now.parent.get_instance_id()):
			continue
		adds.append([now.parent, "method", "add_child", [now.node, true]])
		adds.append([now.parent, "method", "move_child", [now.node, now.index]])
		undo_ops.append([now.parent, "method", "remove_child", [now.node]])
		refs.do.append(now.node)
		said.added.append(now.path)
	do_ops = adds + do_ops + owners
	var unowned := []
	for id in after.nodes:
		if after.nodes[id].owner == null and after.nodes[id].node != root:
			unowned.append(after.nodes[id].path)
	return {"do": do_ops, "undo": undo_ops, "refs": refs, "said": said, "unowned": unowned, "added_nodes": refs.do}


static func changed(changes: Dictionary) -> bool:
	var said: Dictionary = changes.said
	return said.properties > 0 or not (said.added.is_empty() and said.removed.is_empty() and said.moved.is_empty()
		and said.renamed.is_empty() and said.freed.is_empty())


static func _apply(op: Array) -> void:
	if not is_instance_valid(op[0]):
		return
	if op[1] == "property":
		op[0].set(op[2], op[3])
	else:
		op[0].callv(op[2], op[3])


## Puts the scene back as it was before the run (a failed run): the undo operations, at once; nodes the run added are
## freed. Then the scene is compared with the snapshot taken before.
static func revert(root: Node, before: Dictionary, changes: Dictionary) -> bool:
	for op in changes.undo:
		_apply(op)
	for node in changes.added_nodes:
		if is_instance_valid(node) and not node.is_inside_tree():
			node.queue_free()
	if not changes.said.freed.is_empty():
		return false
	var now := snapshot(root)
	if now.nodes.keys().size() != before.nodes.keys().size():
		return false
	for id in before.nodes:
		var was: Dictionary = before.nodes[id]
		var is_now: Dictionary = now.nodes.get(id, {})
		if is_now.is_empty() or is_now.parent != was.parent or is_now.index != was.index or is_now.name != was.name or is_now.owner != was.owner:
			return false
		for name in was.props:
			if not _same(was.props[name], is_now.props.get(name)):
				return false
	for id in before.resources:
		var was: Dictionary = before.resources[id]
		var props: Dictionary = _props(was.object, {}) if is_instance_valid(was.object) else {}
		for name in was.props:
			if not _same(was.props[name], props.get(name)):
				return false
	return true


func _commit(root: Node, changes: Dictionary) -> void:
	var undo: EditorUndoRedoManager = EditorInterface.get_editor_undo_redo()
	undo.create_action(UNDO_STEP, UndoRedo.MERGE_DISABLE, root)
	for op in changes.do:
		if op[1] == "property":
			undo.add_do_property(op[0], op[2], op[3])
		else:
			undo.callv("add_do_method", [op[0], op[2]] + op[3])
	for op in changes.undo:
		if op[1] == "property":
			undo.add_undo_property(op[0], op[2], op[3])
		else:
			undo.callv("add_undo_method", [op[0], op[2]] + op[3])
	for node in changes.refs.do:
		undo.add_do_reference(node)
	for node in changes.refs.undo:
		undo.add_undo_reference(node)
	undo.commit_action(false)


# --- the run ---

static func _indent(code: String) -> String:
	var unit := "\t"
	for line in code.split("\n"):
		if line.begins_with(" "):
			unit = " "
			break
		if line.begins_with("\t"):
			break
	var out := PackedStringArray()
	for line in code.split("\n"):
		out.append(unit + line)
	return "\n".join(out) + "\n" + unit + "pass\n"


## The script to compile: a body of statements becomes func run(); a script that has its own func run() (and helpers)
## is kept as it is.
static func source_of(code: String) -> Dictionary:
	var script_mode := RegEx.create_from_string("(?m)^func run\\(").search(code) != null
	if script_mode:
		return {"source": HEADER + code + "\n", "offset": HEADER.count("\n")}
	return {"source": HEADER + "func run():\n" + _indent(code), "offset": HEADER.count("\n") + 1}


func _failure(error: Dictionary, offset: int, file: String) -> Dictionary:
	var line := int(error.line) - offset if error.file == file else -1
	for frame in error.frames:
		if frame.file == file:
			line = int(frame.line) - offset
			break
	var out := {"message": (error.code + (" " + error.rationale if error.rationale != "" else "")).left(2000)}
	if line > 0:
		out.line = line
	var frames := []
	for frame in error.frames:
		frames.append("%s (%s:%d)" % [frame.function, "<code>" if frame.file == file else frame.file, int(frame.line) - offset if frame.file == file else int(frame.line)])
	if frames:
		out.traceback = "\n".join(frames.slice(0, 20))
	return out


func execute(params: Dictionary) -> Dictionary:
	var code: Variant = params.get("code")
	if not (code is String) or code.strip_edges() == "":
		return link.error("invalid_request", "code is required")
	var read_only: bool = params.get("read_only") == true
	if link.quarantine != null and not read_only:
		return link.error("quarantined", QUARANTINED, {"reason": "script_quarantined", "quarantine": link.quarantine})
	var root: Node = link.scene_root()
	var built := source_of(code)
	var script := GDScript.new()
	script.source_code = built.source
	var file := "gdscript://%d.gd" % script.get_instance_id()
	var revision_before: int = link.revision
	capture.output = ""
	capture.errors = []
	capture.active = true
	var compiled := script.reload()
	capture.active = false
	var base := {"script_handle": link.script_handle, "revision_before": revision_before}
	if compiled != OK:
		var parse: Dictionary = capture.errors[0] if capture.errors else {"code": "The code does not compile (error %d)." % compiled, "file": file, "line": 0, "rationale": "", "frames": []}
		var failed := _failure(parse, built.offset, file)
		var hint: String = Hints.explain(failed.message, root, capture.errors)
		return base.merged({"status": "failed", "result": {}, "stdout": "", "changed": false, "revision_after": link.revision}).merged(failed).merged({"hint": hint} if hint != "" else {})
	var before: Variant = snapshot(root) if root != null and not read_only else null
	var instance: Object = script.new()
	instance.set("PARAMS", params.get("params", {}) if params.get("params") is Dictionary else {})
	instance.set("scene", root)
	instance.set("undo_redo", EditorInterface.get_editor_undo_redo())
	capture.output = ""
	capture.errors = []
	capture.active = true
	var returned: Variant = instance.call("run")
	capture.active = false
	var output := capture.output
	var logged: Array = capture.errors
	var script_error: Variant = null
	for entry in logged:
		if entry.type == Logger.ERROR_TYPE_SCRIPT:
			script_error = entry
			break
	if script_error == null and returned is Object and is_instance_valid(returned) and returned.get_class() == "GDScriptFunctionState":
		script_error = {"code": "await is not supported: the run ended at its first await, and the rest runs later outside OpenCode.", "file": "", "line": 0, "rationale": "", "frames": []}
	var out := base.merged({
		"status": "failed" if script_error != null else "executed",
		"result": json_value(instance.get("result")),
		"stdout": output.left(OUTPUT_MAX),
		"truncated": output.length() > OUTPUT_MAX,
	})
	var notes := []
	for entry in logged:
		if entry != script_error and entry.type != Logger.ERROR_TYPE_SCRIPT and notes.size() < 20:
			notes.append(("%s: %s" % ["WARNING" if entry.type == Logger.ERROR_TYPE_WARNING else "ERROR", entry.code + (" " + entry.rationale if entry.rationale != "" else "")]).left(500))
	if notes:
		out.errors = notes
	if before != null and is_instance_valid(root):
		var changes := diff(root, before)
		var did := changed(changes)
		out.changed = did
		if did:
			out.changes = _words(changes.said)
		if script_error != null:
			if did:
				var restored := revert(root, before, changes)
				out.restored = restored
				if not restored:
					link.quarantine = {
						"cause": ("failed code: " + script_error.code).left(200),
						"release": "read the scene (godot_context), check what may have changed, then release the quarantine with that read's revision",
					}
					if not changes.said.freed.is_empty():
						link.quarantine.freed = changes.said.freed.slice(0, LIST_MAX)
					out.quarantine = link.quarantine
					link.bump()
		elif did:
			_commit(root, changes)
			out.undo_step = UNDO_STEP
			if not changes.said.freed.is_empty():
				out.not_undoable = "Ctrl+Z cannot bring back a freed node (free, queue_free): %s. Remove a node with remove_child to keep it undoable." % ", ".join(changes.said.freed.slice(0, LIST_MAX))
			if not changes.unowned.is_empty():
				out.unowned = "Added without an owner, so not saved with the scene: %s. Set node.owner = scene on each." % ", ".join(changes.unowned.slice(0, LIST_MAX))
	else:
		out.changed = false
	if script_error != null:
		var failed := _failure(script_error, built.offset, file)
		out.merge(failed)
		var hint: String = Hints.explain(failed.message, root, logged)
		if hint != "":
			out.hint = hint
	out.revision_after = link.revision
	return out


static func _words(said: Dictionary) -> Dictionary:
	var out := {}
	if said.properties:
		out.properties = said.properties
	for key in ["added", "removed", "moved", "renamed", "freed"]:
		if not said[key].is_empty():
			out[key] = said[key].slice(0, LIST_MAX)
	return out


func release(params: Dictionary) -> Dictionary:
	if link.quarantine == null:
		return {"released": false, "revision": link.revision}
	var revision: Variant = params.get("revision")
	if not (revision is float or revision is int) or int(revision) != link.revision:
		return link.error("stale_revision", "Read the scene again (godot_context) and release with its revision.",
			{"reason": "revision_conflict", "current_revision": link.revision})
	link.quarantine = null
	return {"released": true, "revision": link.revision}
