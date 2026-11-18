@tool
extends EditorPlugin
## OpenCode Link for Godot: offers this editor's project to OpenCode from the editor's start, without any panel, and
## adds the optional OpenCode dock. OPENCODE_GODOT_OFFER=0 in the editor's environment keeps it from offering.

const Link := preload("link.gd")
const Dock := preload("dock.gd")

var link: RefCounted
var dock: Control


func _enter_tree() -> void:
	link = Link.new(self)
	var error: Error = link.start(OS.get_environment("OPENCODE_GODOT_OFFER") != "0")
	if error != OK:
		push_warning("OpenCode Link did not start: %s" % error_string(error))
		link = null
		return
	get_undo_redo().version_changed.connect(link.bump)
	scene_changed.connect(_scene_changed)
	scene_saved.connect(_scene_changed)
	scene_closed.connect(_scene_changed)
	dock = Dock.new()
	dock.name = "OpenCode"
	dock.link = link
	add_control_to_dock(DOCK_SLOT_RIGHT_UL, dock)


func _exit_tree() -> void:
	if dock != null:
		remove_control_from_docks(dock)
		dock.queue_free()
		dock = null
	if link != null:
		link.stop()
		link = null


func _scene_changed(_value: Variant = null) -> void:
	if link != null:
		link.refresh_label()
		link.bump()


func _process(_delta: float) -> void:
	if link != null:
		link.pump()
