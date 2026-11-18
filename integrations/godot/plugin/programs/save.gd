## godot_save: the open scene to its file, or under PARAMS.path (as the open scene's file, or a copy with copy true).
## Says what was written, read from the disk.

func _target(path: String) -> String:
	if path.begins_with("res://"):
		return path
	if path.is_absolute_path():
		var local := ProjectSettings.localize_path(path)
		return local
	var base := scene.scene_file_path.get_base_dir() if scene.scene_file_path != "" else "res://"
	return base.path_join(path).simplify_path()


func run():
	if scene == null:
		assert(false, "No scene is open in the editor.")
		return
	var path := str(PARAMS.get("path", ""))
	var copy: bool = PARAMS.get("copy", false) == true
	var target := scene.scene_file_path if path == "" else _target(path)
	if target == "":
		assert(false, "This scene was never saved: give path (a .tscn file in the project).")
		return
	if not target.get_extension() in ["tscn", "scn"]:
		assert(false, "%s is not a .tscn or .scn file." % target)
		return
	if target != scene.scene_file_path and FileAccess.file_exists(target) and PARAMS.get("overwrite", false) != true:
		assert(false, "%s exists: give overwrite true to replace it." % target)
		return
	if copy:
		var packed := PackedScene.new()
		var packing := packed.pack(scene)
		var saved := ResourceSaver.save(packed, target) if packing == OK else packing
		if saved != OK:
			assert(false, "%s was not written: %s" % [target, error_string(saved)])
			return
	elif target == scene.scene_file_path:
		var saved := EditorInterface.save_scene()
		if saved != OK:
			assert(false, "%s was not written: %s" % [target, error_string(saved)])
			return
	else:
		EditorInterface.save_scene_as(target, true)
	if not FileAccess.file_exists(target):
		assert(false, "%s is not on disk after saving." % target)
		return
	var file := FileAccess.open(target, FileAccess.READ)
	result.path = target
	result.file = ProjectSettings.globalize_path(target)
	result.bytes = file.get_length() if file != null else 0
	result.modified = Time.get_datetime_string_from_unix_time(FileAccess.get_modified_time(target), true)
	result.copy = copy
	result.open_file = scene.scene_file_path
	result.unsaved = scene.scene_file_path == "" or scene.scene_file_path in EditorInterface.get_unsaved_scenes()
